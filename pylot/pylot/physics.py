"""A dependency-free port of the DDNet tee physics.

The goal of this module is bit-for-bit compatibility with the DDNet server
(`src/game/gamecore.cpp`, `src/game/collision.cpp` and the tile handling in
`src/game/server/entities/character.cpp`), which is what makes it possible to
run a bot entirely offline: we can try millions of input sequences inside the
real physics and only send the one that works.

The public interface is :class:`GameWorld` (collision + tunings) and
:class:`Player` (one tee). ``Player.tick(input)`` advances the world by one
server tick (50 per second).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import map as mapmod
from .map import (
    CANTMOVE_DOWN,
    CANTMOVE_LEFT,
    CANTMOVE_RIGHT,
    CANTMOVE_UP,
    ROTATION_0,
    ROTATION_90,
    ROTATION_180,
    ROTATION_270,
    TILE_CP,
    TILE_CP_F,
    TILE_DEATH,
    TILE_DFREEZE,
    TILE_DUNFREEZE,
    TILE_FINISH,
    TILE_FREEZE,
    TILE_JUMP,
    TILE_LFREEZE,
    TILE_LUNFREEZE,
    TILE_NOHOOK,
    TILE_NOLASER,
    TILE_REFILL_JUMPS,
    TILE_SOLID,
    TILE_START,
    TILE_STOP,
    TILE_STOPA,
    TILE_STOPS,
    TILE_SWITCHCLOSE,
    TILE_SWITCHOPEN,
    TILE_SWITCHTIMEDCLOSE,
    TILE_SWITCHTIMEDOPEN,
    TILE_TELEIN,
    TILE_TELEINHOOK,
    TILE_TELEINWEAPON,
    TILE_TELECHECKIN,
    TILE_TELECHECKINEVIL,
    TILE_TELECHECKOUT,
    TILE_TELEINEVIL,
    TILE_TELEOUT,
    TILE_THROUGH,
    TILE_THROUGH_ALL,
    TILE_THROUGH_CUT,
    TILE_THROUGH_DIR,
    TILE_TUNE,
    TILE_UNFREEZE,
    TILE_WALLJUMP,
    TILEFLAG_ROTATE,
    TILEFLAG_XFLIP,
    TILEFLAG_YFLIP,
    Map,
)

SERVER_TICK_SPEED = 50

#: hook states, mirroring ``src/game/gamecore.h``
HOOK_RETRACTED = -1
HOOK_IDLE = 0
HOOK_RETRACT_START = 1
HOOK_RETRACT_END = 3
HOOK_FLYING = 4
HOOK_GRABBED = 5

PHYSICAL_SIZE = 28.0


# ---------------------------------------------------------------------------
# vector helpers (small and fast on purpose; tuples instead of classes)
# ---------------------------------------------------------------------------


def _normalize(x: float, y: float) -> Tuple[float, float]:
    length = math.hypot(x, y)
    if length == 0.0:
        return 0.0, 0.0
    return x / length, y / length


def _round_to_int(value: float) -> int:
    """``round_to_int`` from ``src/base/math.h`` (round half away from zero)."""
    return int(value + 0.5) if value > 0 else int(value - 0.5)


def _round_truncate(value: float) -> int:
    if not math.isfinite(value):
        return -(2 ** 31)
    return int(value)


def _saturated_add(minimum: float, maximum: float, current: float, modifier: float) -> float:
    if modifier < 0:
        if current < minimum:
            return current
        current += modifier
        if current < minimum:
            current = minimum
        return current
    if current > maximum:
        return current
    current += modifier
    if current > maximum:
        current = maximum
    return current


def velocity_ramp(value: float, start: float, ramp_range: float, curvature: float) -> float:
    """``VelocityRamp`` from ``src/game/gamecore.cpp``."""
    if value < start:
        return 1.0
    return 1.0 / (curvature ** ((value - start) / ramp_range))


def _get_move_restrictions_raw(direction: int, tile: int, flags: int) -> int:
    flags &= TILEFLAG_XFLIP | TILEFLAG_YFLIP | TILEFLAG_ROTATE
    if tile == TILE_STOP:
        rotation_map = {
            ROTATION_0: CANTMOVE_DOWN,
            ROTATION_90: CANTMOVE_LEFT,
            ROTATION_180: CANTMOVE_UP,
            ROTATION_270: CANTMOVE_RIGHT,
            TILEFLAG_YFLIP ^ ROTATION_0: CANTMOVE_UP,
            TILEFLAG_YFLIP ^ ROTATION_90: CANTMOVE_RIGHT,
            TILEFLAG_YFLIP ^ ROTATION_180: CANTMOVE_DOWN,
            TILEFLAG_YFLIP ^ ROTATION_270: CANTMOVE_LEFT,
        }
        return rotation_map.get(flags, 0)
    if tile == TILE_STOPS:
        if flags in (ROTATION_0, ROTATION_180, TILEFLAG_YFLIP ^ ROTATION_0, TILEFLAG_YFLIP ^ ROTATION_180):
            return CANTMOVE_DOWN | CANTMOVE_UP
        if flags in (ROTATION_90, ROTATION_270, TILEFLAG_YFLIP ^ ROTATION_90, TILEFLAG_YFLIP ^ ROTATION_270):
            return CANTMOVE_LEFT | CANTMOVE_RIGHT
        return 0
    if tile == TILE_STOPA:
        return CANTMOVE_LEFT | CANTMOVE_RIGHT | CANTMOVE_UP | CANTMOVE_DOWN
    return 0


_MR_MASKS = (0, CANTMOVE_RIGHT, CANTMOVE_DOWN, CANTMOVE_LEFT, CANTMOVE_UP)


def _get_move_restrictions(direction: int, tile: int, flags: int) -> int:
    result = _get_move_restrictions_raw(direction, tile, flags)
    if direction == 0 and tile == TILE_STOP:
        return result
    return result & _MR_MASKS[direction]


def clamp_vel(restriction: int, vx: float, vy: float) -> Tuple[float, float]:
    """``ClampVel`` from ``src/game/collision.cpp``."""
    if vx > 0 and restriction & CANTMOVE_RIGHT:
        vx = 0.0
    if vx < 0 and restriction & CANTMOVE_LEFT:
        vx = 0.0
    if vy > 0 and restriction & CANTMOVE_DOWN:
        vy = 0.0
    if vy < 0 and restriction & CANTMOVE_UP:
        vy = 0.0
    return vx, vy


# ---------------------------------------------------------------------------
# collision
# ---------------------------------------------------------------------------


class Collision:
    """Port of ``CCollision`` operating on a :class:`pylot.map.Map`."""

    __slots__ = (
        "map",
        "width",
        "height",
        "_tiles",
        "_front",
        "_tele",
        "_speedup",
        "_switch",
        "_tune",
        "_highest_switch_number",
        "_num_tiles",
    )

    def __init__(self, map_: Map):
        self.map = map_
        self.width = map_.width
        self.height = map_.height
        game = map_.game_layer
        assert game is not None
        self._tiles = game.tiles
        self._front = map_.front_layer.tiles if map_.front_layer else None
        self._tele = map_.tele_tiles
        self._speedup = map_.speedup_tiles
        self._switch = map_.switch_tiles
        self._tune = map_.tune_tiles
        self._highest_switch_number = max((t.number for t in self._switch), default=0)
        self._num_tiles = len(self._tiles)

    # -- tiles ------------------------------------------------------------

    def get_tile(self, x: float, y: float) -> int:
        """``CCollision::GetTile``; note that it takes *pixel* coordinates."""
        nx = min(max(int(x) // 32, 0), self.width - 1)
        ny = min(max(int(y) // 32, 0), self.height - 1)
        index = self._tiles[ny * self.width + nx].index
        if TILE_SOLID <= index <= TILE_NOLASER:
            return index
        return 0

    def is_solid(self, x: float, y: float) -> bool:
        index = self.get_tile(x, y)
        return index == TILE_SOLID or index == TILE_NOHOOK

    def index_xy(self, nx: int, ny: int) -> int:
        """Game layer tile index in tile coordinates."""
        if nx < 0 or ny < 0 or nx >= self.width or ny >= self.height:
            return 0
        return self._tiles[ny * self.width + nx].index

    def check_point(self, x: float, y: float) -> bool:
        # inlined _round_to_int + GetTile; this is the hottest function of the
        # whole bot, so the bounds clamp is done with a plain index check
        ix = int(x + 0.5) if x > 0 else int(x - 0.5)
        iy = int(y + 0.5) if y > 0 else int(y - 0.5)
        index = (iy >> 5) * self.width + (ix >> 5)
        if index < 0 or index >= self._num_tiles:
            return False
        kind = self._tiles[index].index
        return kind == 1 or kind == 3

    def get_collision_at(self, x: float, y: float) -> int:
        return self.get_tile(_round_to_int(x), _round_to_int(y))

    def get_front_tile(self, x: float, y: float) -> int:
        """``CCollision::GetFrontTile``; also takes pixel coordinates."""
        if self._front is None:
            return 0
        nx = min(max(int(x) // 32, 0), self.width - 1)
        ny = min(max(int(y) // 32, 0), self.height - 1)
        index = self._front[ny * self.width + nx].index
        if index == TILE_DEATH or index == TILE_NOLASER:
            return index
        return 0

    def get_front_tile_index(self, index: int) -> int:
        if self._front is None or index < 0 or index >= len(self._front):
            return 0
        return self._front[index].index

    def get_tile_index(self, index: int) -> int:
        if index < 0 or index >= len(self._tiles):
            return 0
        return self._tiles[index].index

    def get_tile_flags(self, index: int) -> int:
        if index < 0 or index >= len(self._tiles):
            return 0
        return self._tiles[index].flags

    def get_front_tile_flags(self, index: int) -> int:
        if self._front is None or index < 0 or index >= len(self._front):
            return 0
        return self._front[index].flags

    def get_pure_map_index_xy(self, x: float, y: float) -> int:
        ix = int(x + 0.5) if x > 0 else int(x - 0.5)
        iy = int(y + 0.5) if y > 0 else int(y - 0.5)
        nx = ix >> 5
        ny = iy >> 5
        if nx < 0:
            nx = 0
        elif nx >= self.width:
            nx = self.width - 1
        if ny < 0:
            ny = 0
        elif ny >= self.height:
            ny = self.height - 1
        return ny * self.width + nx

    def get_pure_map_index(self, pos: Sequence[float]) -> int:
        return self.get_pure_map_index_xy(pos[0], pos[1])

    def get_map_index(self, pos: Sequence[float]) -> int:
        index = self.get_pure_map_index(pos)
        if self.tile_exists(index):
            return index
        return -1

    def tile_index_from_coord(self, coord: float, size: int) -> int:
        if not coord > 0.0:
            return 0
        return min(int(coord / 32.0), size - 1)

    # -- physics helpers --------------------------------------------------

    def test_box(self, px: float, py: float, size: float = PHYSICAL_SIZE) -> bool:
        half = size * 0.5
        return (
            self.check_point(px - half, py - half)
            or self.check_point(px + half, py - half)
            or self.check_point(px - half, py + half)
            or self.check_point(px + half, py + half)
        )

    def is_on_ground(self, px: float, py: float, size: float = PHYSICAL_SIZE) -> bool:
        half = size / 2.0
        if self.check_point(px + half, py + half + 5.0):
            return True
        if self.check_point(px - half, py + half + 5.0):
            return True
        return False

    def move_box(
        self,
        pos: Tuple[float, float],
        vel: Tuple[float, float],
        size: float,
        elasticity_x: float,
        elasticity_y: float,
        grounded: Optional[List[bool]] = None,
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Port of ``CCollision::MoveBox``."""
        px, py = pos
        vx, vy = vel
        distance = math.hypot(vx, vy)
        steps = _round_truncate(distance)

        if distance > 0.00001:
            fraction = 1.0 / (steps + 1)
            elastic_x = min(max(elasticity_x, -1.0), 1.0)
            elastic_y = min(max(elasticity_y, -1.0), 1.0)
            for _ in range(steps + 1):
                if vx == 0.0 and vy == 0.0:
                    break
                new_x = px + vx * fraction
                new_y = py + vy * fraction
                if new_x == px and new_y == py:
                    break
                if self.test_box(new_x, new_y, size):
                    hits = 0
                    if self.test_box(px, new_y, size):
                        if grounded is not None and elastic_y > 0 and vy > 0:
                            grounded[0] = True
                        new_y = py
                        vy *= -elastic_y
                        hits += 1
                    if self.test_box(new_x, py, size):
                        new_x = px
                        vx *= -elastic_x
                        hits += 1
                    if hits == 0:
                        if grounded is not None and elastic_y > 0 and vy > 0:
                            grounded[0] = True
                        new_y = py
                        vy *= -elastic_y
                        new_x = px
                        vx *= -elastic_x
                px, py = new_x, new_y
        return (px, py), (vx, vy)

    def get_move_restrictions(self, px: float, py: float, distance: float = 18.0, switch_active=None) -> int:
        """Port of ``CCollision::GetMoveRestrictions``."""
        restrictions = 0
        tiles = self._tiles
        front = self._front
        for direction in range(5):
            if direction == 0:
                index = self.get_pure_map_index_xy(px, py)
            elif direction == 1:
                index = self.get_pure_map_index_xy(px + distance, py)
            elif direction == 2:
                index = self.get_pure_map_index_xy(px, py + distance)
            elif direction == 3:
                index = self.get_pure_map_index_xy(px - distance, py)
            else:
                index = self.get_pure_map_index_xy(px, py - distance)
            tile = tiles[index]
            if tile.index == 60 or tile.index == 61 or tile.index == 62:
                restrictions |= _get_move_restrictions(direction, tile.index, tile.flags)
            if front is not None:
                ftile = front[index]
                if ftile.index == 60 or ftile.index == 61 or ftile.index == 62:
                    restrictions |= _get_move_restrictions(direction, ftile.index, ftile.flags)
        return restrictions

    def get_door_tile(self, index: int):
        """Doors are switch driven; we only model switch tiles that block."""
        if index < 0 or index >= len(self._switch):
            return None
        tile = self._switch[index]
        if tile.number == 0:
            return None
        return (0, 0, tile.number)

    # -- tees / hook / weapons -------------------------------------------

    def is_through(self, x: int, y: int, offset_x: int, offset_y: int, p0, p1) -> bool:
        index = self.get_pure_map_index_xy(x, y)
        if self._front is not None:
            front = self._front[index]
            if front.index in (TILE_THROUGH_ALL, TILE_THROUGH_CUT):
                return True
            if front.index == TILE_THROUGH_DIR and (
                (front.flags == ROTATION_0 and p0[1] > p1[1])
                or (front.flags == ROTATION_90 and p0[0] < p1[0])
                or (front.flags == ROTATION_180 and p0[1] < p1[1])
                or (front.flags == ROTATION_270 and p0[0] > p1[0])
            ):
                return True
        offset_index = self.get_pure_map_index_xy(x + offset_x, y + offset_y)
        if self._tiles[offset_index].index == TILE_THROUGH:
            return True
        if self._front is not None and self._front[offset_index].index == TILE_THROUGH:
            return True
        return False

    def is_hook_blocker(self, x: int, y: int, p0, p1) -> bool:
        index = self.get_pure_map_index_xy(x, y)
        if self._tiles[index].index == TILE_THROUGH_ALL:
            return True
        if self._front is not None and self._front[index].index == TILE_THROUGH_ALL:
            return True
        if self._tiles[index].index == TILE_THROUGH_DIR:
            flags = self._tiles[index].flags
            if (
                (flags == ROTATION_0 and p0[1] < p1[1])
                or (flags == ROTATION_90 and p0[0] > p1[0])
                or (flags == ROTATION_180 and p0[1] > p1[1])
                or (flags == ROTATION_270 and p0[0] < p1[0])
            ):
                return True
        return False

    def intersect_line_tele_hook(self, p0, p1, tele_nr: List[int]):
        """Returns (hit tile, new pos, teleporter number) like the C++ version."""
        dx = p0[0] - p1[0]
        dy = p0[1] - p1[1]
        if abs(dx) > abs(dy):
            offset_x, offset_y = (-32, 0) if dx < 0 else (32, 0)
        else:
            offset_x, offset_y = (0, -32) if dy < 0 else (0, 32)

        distance = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        end = int(distance) + 1
        last = p0
        new_pos = p1
        hit = 0
        tele_nr[0] = 0
        if end <= 0:
            return 0, p1, 0
        for i in range(end + 1):
            a = i / float(end)
            px = p0[0] + (p1[0] - p0[0]) * a
            py = p0[1] + (p1[1] - p0[1]) * a
            index = self.get_pure_map_index_xy(px, py)
            tele_nr[0] = self.is_teleport_hook(index)
            if tele_nr[0]:
                return TILE_TELEINHOOK, (px, py), tele_nr[0]
            ix = _round_to_int(px)
            iy = _round_to_int(py)
            step_hit = 0
            if self.check_point(ix, iy):
                if not self.is_through(ix, iy, offset_x, offset_y, p0, p1):
                    step_hit = self.get_collision_at(ix, iy)
            elif self.is_hook_blocker(ix, iy, p0, p1):
                step_hit = TILE_NOHOOK
            if step_hit:
                hit = step_hit
                new_pos = (px, py)
                tele_nr[0] = 0
                break
            last = (px, py)
        return hit, new_pos, tele_nr[0]

    # -- DDRace tile queries ---------------------------------------------

    def is_teleport(self, index: int) -> int:
        if index < 0 or index >= len(self._tele):
            return 0
        tile = self._tele[index]
        return tile.number if tile.type == TILE_TELEIN else 0

    def is_evil_teleport(self, index: int) -> int:
        if index < 0 or index >= len(self._tele):
            return 0
        tile = self._tele[index]
        return tile.number if tile.type == TILE_TELEINEVIL else 0

    def is_teleport_hook(self, index: int) -> int:
        if index < 0 or index >= len(self._tele):
            return 0
        tile = self._tele[index]
        return tile.number if tile.type == TILE_TELEINHOOK else 0

    def is_check_teleport(self, index: int) -> bool:
        if index < 0 or index >= len(self._tele):
            return False
        return self._tele[index].type == TILE_TELECHECKIN

    def is_check_evil_teleport(self, index: int) -> bool:
        if index < 0 or index >= len(self._tele):
            return False
        return self._tele[index].type == TILE_TELECHECKINEVIL

    def is_tele_checkpoint(self, index: int) -> int:
        if index < 0 or index >= len(self._tele):
            return 0
        tile = self._tele[index]
        return tile.number if tile.type == 29 else 0

    def is_speedup(self, index: int) -> bool:
        return 0 <= index < len(self._speedup) and self._speedup[index].force > 0

    def get_speedup(self, index: int):
        tile = self._speedup[index]
        angle = tile.angle * (math.pi / 180.0)
        return (math.cos(angle), math.sin(angle)), tile.force, tile.max_speed, tile.type

    def get_switch_type(self, index: int) -> int:
        if index < 0 or index >= len(self._switch):
            return 0
        return self._switch[index].type

    def get_switch_number(self, index: int) -> int:
        if index < 0 or index >= len(self._switch):
            return 0
        tile = self._switch[index]
        if tile.type > 0 and 0 < tile.number <= self._highest_switch_number:
            return tile.number
        return 0

    def get_switch_delay(self, index: int) -> int:
        if index < 0 or index >= len(self._switch):
            return 0
        tile = self._switch[index]
        return tile.delay if tile.type > 0 else 0

    def is_tune(self, index: int) -> int:
        if index < 0 or index >= len(self._tune):
            return 0
        tile = self._tune[index]
        return tile.number if tile.type else 0

    def is_wall_jump(self, index: int) -> bool:
        return 0 <= index < len(self._tiles) and self._tiles[index].index == TILE_WALLJUMP

    def tile_exists(self, index: int) -> bool:
        if index < 0 or index >= len(self._tiles):
            return False
        if TILE_FREEZE <= index and False:  # pragma: no cover - keeps structure explicit
            return True
        tile = self._tiles[index]
        if 9 <= tile.index <= 129 or 144 <= tile.index <= 145:
            # TILE_FREEZE .. TILE_TELE_LASER_DISABLE and TILE_LFREEZE .. TILE_LUNFREEZE
            return True
        if self._front is not None:
            front = self._front[index]
            if 9 <= front.index <= 129 or 144 <= front.index <= 145:
                return True
        if index < len(self._tele):
            tele_type = self._tele[index].type
            if tele_type in (26, 10, 63, 29, 31):
                return True
        if index < len(self._speedup) and self._speedup[index].force > 0:
            return True
        if index < len(self._switch) and self._switch[index].type:
            return True
        if index < len(self._tune) and self._tune[index].type:
            return True
        return self.tile_exists_next(index)

    def tile_exists_next(self, index: int) -> bool:
        total = self.width * self.height
        left = index - 1 if index - 1 > 0 else index
        right = index + 1 if index + 1 < total else index
        below = index + self.width if index + self.width < total else index
        above = index - self.width if index - self.width > 0 else index
        tiles = self._tiles
        if (tiles[right].index == TILE_STOP and tiles[right].flags == ROTATION_270) or (
            tiles[left].index == TILE_STOP and tiles[left].flags == ROTATION_90
        ):
            return True
        if (tiles[below].index == TILE_STOP and tiles[below].flags == ROTATION_0) or (
            tiles[above].index == TILE_STOP and tiles[above].flags == ROTATION_180
        ):
            return True
        if TILE_STOPA in (tiles[right].index, tiles[left].index):
            return True
        if TILE_STOPS in (tiles[right].index, tiles[left].index):
            return True
        if TILE_STOPA in (tiles[below].index, tiles[above].index):
            return True
        if TILE_STOPS in (tiles[below].index, tiles[above].index):
            return True
        return False

    def get_map_indices(self, prev_pos, pos) -> List[int]:
        """``CCollision::GetMapIndices``; returns the tile indices touched."""
        indices: List[int] = []
        distance = math.hypot(pos[0] - prev_pos[0], pos[1] - prev_pos[1])
        end = int(distance + 1)
        if distance == 0:
            nx = min(max(int(pos[0]) // 32, 0), self.width - 1)
            ny = min(max(int(pos[1]) // 32, 0), self.height - 1)
            index = ny * self.width + nx
            if self.tile_exists(index):
                indices.append(index)
            return indices
        last = 0
        for i in range(end):
            a = i / distance
            px = prev_pos[0] + (pos[0] - prev_pos[0]) * a
            py = prev_pos[1] + (pos[1] - prev_pos[1]) * a
            nx = min(max(int(px) // 32, 0), self.width - 1)
            ny = min(max(int(py) // 32, 0), self.height - 1)
            index = ny * self.width + nx
            if self.tile_exists(index) and last != index:
                indices.append(index)
                last = index
        return indices


# ---------------------------------------------------------------------------
# tunings
# ---------------------------------------------------------------------------


@dataclass
class Tuning:
    """The subset of tunings that influences tee movement."""

    ground_control_speed: float = 10.0
    ground_control_accel: float = 2.0
    ground_friction: float = 0.5
    ground_jump_impulse: float = 13.2
    air_jump_impulse: float = 12.0
    air_control_speed: float = 5.0
    air_control_accel: float = 1.5
    air_friction: float = 0.95
    hook_length: float = 380.0
    hook_fire_speed: float = 80.0
    hook_drag_accel: float = 3.0
    hook_drag_speed: float = 15.0
    gravity: float = 0.5
    velramp_start: float = 550.0
    velramp_range: float = 2000.0
    velramp_curvature: float = 1.4
    player_collision: float = 1.0
    player_hooking: float = 1.0
    hook_duration: float = 1.25
    ground_elasticity_x: float = 0.0
    ground_elasticity_y: float = 0.0

    @classmethod
    def from_dict(cls, values: Dict[str, float]) -> "Tuning":
        tuning = cls()
        for key in cls.__dataclass_fields__:  # type: ignore[attr-defined]
            if key in values:
                setattr(tuning, key, float(values[key]))
        return tuning


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Input:
    """One tick of player input (matches ``CNetObj_PlayerInput``)."""

    direction: int = 0
    jump: int = 0
    hook: int = 0
    target_x: int = 0
    target_y: int = -1
    fire: int = 0
    weapon: int = 0

    def key(self) -> Tuple[int, int, int, int, int]:
        return (self.direction, self.jump, self.hook, self.target_x, self.target_y)


DIRECTION_LEFT = -1
DIRECTION_NONE = 0
DIRECTION_RIGHT = 1


# ---------------------------------------------------------------------------
# the tee
# ---------------------------------------------------------------------------


class Player:
    """A single tee simulated against a :class:`Collision` world."""

    def __init__(self, world: Collision, pos=(0.0, 0.0), tuning: Optional[Tuning] = None):
        self.world = world
        self.tuning = tuning or Tuning()
        self.pos = [float(pos[0]), float(pos[1])]
        self.vel = [0.0, 0.0]
        self.hook_pos = [self.pos[0], self.pos[1]]
        self.hook_dir = [0.0, -1.0]
        self.hook_state = HOOK_IDLE
        self.hook_tick = 0
        self.hook_tele_base = [0.0, 0.0]
        self.new_hook = False
        self.hooked_player = -1
        self.input = Input()
        self.direction = 0
        self.angle = 0
        self.jumped = 0
        self.jumped_total = 0
        self.jumps = 2
        self.move_restrictions = 0
        self.triggered_events = 0
        self.reset = False
        self.left_wall = False
        self.colliding = 0

        # DDNet character state
        self.solo = False
        self.deep_frozen = False
        self.live_frozen = False
        self.endless_hook = False
        self.endless_jump = False
        self.super = False
        self.jetpack = False
        self.freeze_time = 0
        self.last_refill_jumps = False
        self.tele_checkpoint = 0
        self.time = 0.0
        self.started = False
        self.finished = False
        self.dead = False
        self.death_pos = (0.0, 0.0)
        self.tune_zone = 0
        self.ticks = 0

        self._tuning_cache: Dict[int, Tuning] = {0: self.tuning}
        # teleporters (number -> list of target positions)
        self._tele_outs: Dict[int, List[Tuple[float, float]]] = {}
        self._tele_check_outs: Dict[int, List[Tuple[float, float]]] = {}
        self._collect_teleports()

    def _tuning_for_zone(self, zone: int) -> Tuning:
        cached = self._tuning_cache.get(zone)
        if cached is None:
            cached = Tuning.from_dict(self.world.map.tuning(zone))
            self._tuning_cache[zone] = cached
        return cached

    def _collect_teleports(self) -> None:
        world = self.world
        for index, tile in enumerate(world._tele):
            if tile.number <= 0 or not tile.type:
                continue
            x = (index % world.width) * 32.0 + 16.0
            y = (index // world.width) * 32.0 + 16.0
            if tile.type == TILE_TELEOUT:
                self._tele_outs.setdefault(tile.number, []).append((x, y))
            elif tile.type == TILE_TELECHECKOUT:
                self._tele_check_outs.setdefault(tile.number, []).append((x, y))

    # -- helpers ----------------------------------------------------------

    #: attributes that make up the complete simulatable state
    _STATE_FIELDS = (
        "pos", "vel", "hook_pos", "hook_dir", "hook_state", "hook_tick",
        "hook_tele_base", "new_hook", "hooked_player", "input", "direction",
        "angle", "jumped", "jumped_total", "jumps", "move_restrictions",
        "triggered_events", "reset", "left_wall", "colliding", "solo",
        "deep_frozen", "live_frozen", "endless_hook", "endless_jump", "super",
        "jetpack", "freeze_time", "last_refill_jumps", "tele_checkpoint",
        "time", "started", "finished", "dead", "death_pos", "tune_zone",
        "ticks", "tuning",
    )

    def snapshot(self) -> tuple:
        """A cheap, complete copy of the mutable state (used by the search)."""
        state = []
        for name in self._STATE_FIELDS:
            value = getattr(self, name)
            if isinstance(value, list):
                value = tuple(value)
            state.append(value)
        return tuple(state)

    def restore(self, state: tuple) -> None:
        for name, value in zip(self._STATE_FIELDS, state):
            if isinstance(value, tuple):
                value = list(value)
            setattr(self, name, value)

    def clone(self) -> "Player":
        other = Player.__new__(Player)
        other.__dict__.update(self.__dict__)
        other.pos = list(self.pos)
        other.vel = list(self.vel)
        other.hook_pos = list(self.hook_pos)
        other.hook_dir = list(self.hook_dir)
        other.hook_tele_base = list(self.hook_tele_base)
        other.input = self.input
        return other

    def get_proximity_radius(self) -> float:
        return PHYSICAL_SIZE

    def is_grounded(self) -> bool:
        """``CCharacter::IsGrounded``."""
        if self.world.is_on_ground(self.pos[0], self.pos[1], PHYSICAL_SIZE):
            return True
        below = self.world.get_move_restrictions(self.pos[0], self.pos[1] + PHYSICAL_SIZE / 2 + 4, 0.0)
        return bool(below & CANTMOVE_DOWN)

    def freeze(self, seconds: int = 3) -> bool:
        if seconds <= 0 or self.super:
            return False
        if self.freeze_time == 0:
            self.freeze_time = seconds * SERVER_TICK_SPEED
            return True
        return False

    def unfreeze(self) -> bool:
        if self.freeze_time > 0:
            self.freeze_time = 0
            return True
        return False

    # -- the tick ---------------------------------------------------------

    def tick(self, input: Input) -> None:
        """Advance the simulation by one tick."""
        if self.dead or self.finished:
            return
        self.ticks += 1
        self.time = self.ticks / SERVER_TICK_SPEED

        # --- CCharacter::DDRaceTick -------------------------------------
        direction = input.direction
        jump = input.jump
        hook = input.hook
        if self.live_frozen and not self.super:
            direction = 0
            jump = 0
        if self.freeze_time > 0:
            self.freeze_time -= 1
            direction = 0
            jump = 0
            hook = 0
            if self.freeze_time <= 0:
                self.freeze_time = 0
        self.input = Input(direction, jump, hook, input.target_x, input.target_y, input.fire, input.weapon)

        # tuning zone (HandleTuneLayer happens before the core tick)
        zone = self.world.is_tune(self.world.get_pure_map_index_xy(self.pos[0], self.pos[1]))
        if zone != self.tune_zone:
            self.tune_zone = zone
            self.tuning = self._tuning_for_zone(zone)

        # --- CCharacterCore::Tick ---------------------------------------
        self.move_restrictions = self.world.get_move_restrictions(self.pos[0], self.pos[1])
        self.triggered_events = 0
        grounded = self.world.is_on_ground(self.pos[0], self.pos[1], PHYSICAL_SIZE)
        target_x, target_y = _normalize(self.input.target_x, self.input.target_y)

        self.vel[1] += self.tuning.gravity

        max_speed = self.tuning.ground_control_speed if grounded else self.tuning.air_control_speed
        accel = self.tuning.ground_control_accel if grounded else self.tuning.air_control_accel
        friction = self.tuning.ground_friction if grounded else self.tuning.air_friction

        if True:
            self.direction = self.input.direction
            if target_y or target_x:
                angle = math.atan2(self.input.target_y, self.input.target_x)
                if angle < -(math.pi / 2.0):
                    self.angle = int((angle + 2.0 * math.pi) * 256.0)
                else:
                    self.angle = int(angle * 256.0)

            # jump
            if self.input.jump:
                if not (self.jumped & 1):
                    if grounded and (not (self.jumped & 2) or self.jumps != 0):
                        self.triggered_events |= 1  # COREEVENT_GROUND_JUMP
                        self.vel[1] = -self.tuning.ground_jump_impulse
                        if self.jumps > 1:
                            self.jumped |= 1
                        else:
                            self.jumped |= 3
                        self.jumped_total = 0
                    elif not (self.jumped & 2):
                        self.triggered_events |= 2  # COREEVENT_AIR_JUMP
                        self.vel[1] = -self.tuning.air_jump_impulse
                        self.jumped |= 3
                        self.jumped_total += 1
            else:
                self.jumped &= ~1

            # hook
            if self.input.hook:
                if self.hook_state == HOOK_IDLE:
                    self.hook_state = HOOK_FLYING
                    self.hook_pos[0] = self.pos[0] + target_x * PHYSICAL_SIZE * 1.5
                    self.hook_pos[1] = self.pos[1] + target_y * PHYSICAL_SIZE * 1.5
                    self.hook_dir = [target_x, target_y]
                    self.hooked_player = -1
                    self.hook_tick = int(SERVER_TICK_SPEED * (1.25 - self.tuning.hook_duration))
                    self.triggered_events |= 8
            else:
                self.hooked_player = -1
                self.hook_state = HOOK_IDLE
                self.hook_pos = [self.pos[0], self.pos[1]]

        if grounded:
            self.jumped &= ~2
            self.jumped_total = 0

        if self.direction < 0:
            self.vel[0] = _saturated_add(-max_speed, max_speed, self.vel[0], -accel)
        if self.direction > 0:
            self.vel[0] = _saturated_add(-max_speed, max_speed, self.vel[0], accel)
        if self.direction == 0:
            self.vel[0] *= friction

        self._tick_hook(target_x, target_y)

        # DDRace jump rules (overridden by tiles, applied in DDRacePostCoreTick)
        if self.jumps == -1:
            self.jumped |= 2
        elif self.jumps == 0:
            self.jumped |= 2
        elif self.jumps == 1 and self.jumped > 0:
            self.jumped |= 2
        elif self.jumped_total < self.jumps - 1 and self.jumped > 1:
            self.jumped = 1
        if (self.super or self.endless_jump) and self.jumped > 1:
            self.jumped = 1

        # tiles below us (DDRacePostCoreTick -> HandleSkippableTiles)
        prev_pos = (self.pos[0], self.pos[1])
        index = self.world.get_map_index(self.pos)
        self._handle_skippable_tiles(index)
        if self.dead:
            return
        indices = self.world.get_map_indices(prev_pos, self.pos)
        if indices:
            for tile_index in indices:
                self._handle_tiles(tile_index)
                if self.dead:
                    return
        else:
            self._handle_tiles(index)
            if self.dead:
                return

        # --- CCharacterCore::Move ---------------------------------------
        self._move()

        # after the move: start / finish detection (CGameControllerDDNet)
        self._check_start_finish()

    # -- hook -------------------------------------------------------------

    def _tick_hook(self, target_x: float, target_y: float) -> None:
        vel = self.vel
        tuning = self.tuning
        if self.hook_state == HOOK_IDLE:
            self.hooked_player = -1
            self.hook_pos = [self.pos[0], self.pos[1]]
        elif HOOK_RETRACT_START <= self.hook_state < HOOK_RETRACT_END:
            self.hook_state += 1
        elif self.hook_state == HOOK_RETRACT_END:
            self.hook_state = HOOK_RETRACTED
        elif self.hook_state == HOOK_FLYING:
            base = self.hook_tele_base if self.new_hook else self.pos
            new_x = self.hook_pos[0] + self.hook_dir[0] * tuning.hook_fire_speed
            new_y = self.hook_pos[1] + self.hook_dir[1] * tuning.hook_fire_speed
            if math.hypot(new_x - base[0], new_y - base[1]) > tuning.hook_length:
                self.hook_state = HOOK_RETRACT_START
                length = math.hypot(new_x - base[0], new_y - base[1])
                new_x = base[0] + (new_x - base[0]) / length * tuning.hook_length
                new_y = base[1] + (new_y - base[1]) / length * tuning.hook_length
                self.reset = True

            going_to_hit_ground = False
            going_to_retract = False
            going_through_tele = False
            tele_nr = [0]
            hit, hook_pos, _ = self.world.intersect_line_tele_hook(
                (self.hook_pos[0], self.hook_pos[1]), (new_x, new_y), tele_nr
            )
            new_x, new_y = hook_pos
            if hit:
                if hit == TILE_NOHOOK:
                    going_to_retract = True
                elif hit == TILE_TELEINHOOK:
                    going_through_tele = True
                else:
                    going_to_hit_ground = True
                self.reset = True

            if self.hook_state == HOOK_FLYING:
                if going_to_hit_ground:
                    self.hook_state = HOOK_GRABBED
                elif going_to_retract:
                    self.hook_state = HOOK_RETRACT_START
                if going_through_tele and tele_nr[0] and self._tele_outs.get(tele_nr[0]):
                    self.triggered_events = 0
                    self.hooked_player = -1
                    self.new_hook = True
                    out = self._tele_outs[tele_nr[0]][0]
                    self.hook_pos = [
                        out[0] + target_x * PHYSICAL_SIZE * 1.5,
                        out[1] + target_y * PHYSICAL_SIZE * 1.5,
                    ]
                    self.hook_dir = [target_x, target_y]
                    self.hook_tele_base = list(self.hook_pos)
                else:
                    self.hook_pos = [new_x, new_y]

        if self.hook_state == HOOK_GRABBED:
            if self.hooked_player == -1 and math.hypot(
                self.hook_pos[0] - self.pos[0], self.hook_pos[1] - self.pos[1]
            ) > 46.0:
                dir_x, dir_y = _normalize(self.hook_pos[0] - self.pos[0], self.hook_pos[1] - self.pos[1])
                hook_vel_x = dir_x * tuning.hook_drag_accel
                hook_vel_y = dir_y * tuning.hook_drag_accel
                if hook_vel_y > 0:
                    hook_vel_y *= 0.3
                if (hook_vel_x < 0 and self.direction < 0) or (hook_vel_x > 0 and self.direction > 0):
                    hook_vel_x *= 0.95
                else:
                    hook_vel_x *= 0.75
                new_vel = [vel[0] + hook_vel_x, vel[1] + hook_vel_y]
                new_len = math.hypot(new_vel[0], new_vel[1])
                if new_len < tuning.hook_drag_speed or new_len < math.hypot(vel[0], vel[1]):
                    vel[0], vel[1] = new_vel
            self.hook_tick += 1
            if self.hooked_player != -1 and self.hook_tick > SERVER_TICK_SPEED + SERVER_TICK_SPEED // 5:
                self.hooked_player = -1
                self.hook_state = HOOK_RETRACTED
                self.hook_pos = [self.pos[0], self.pos[1]]

    # -- movement ---------------------------------------------------------

    def _move(self) -> None:
        ramp = velocity_ramp(
            math.hypot(self.vel[0], self.vel[1]) * 50.0,
            self.tuning.velramp_start,
            self.tuning.velramp_range,
            self.tuning.velramp_curvature,
        )
        self.vel[0] *= ramp
        old_vel_x = self.vel[0]
        grounded = [False]
        pos, vel = self.world.move_box(
            (self.pos[0], self.pos[1]),
            (self.vel[0], self.vel[1]),
            PHYSICAL_SIZE,
            self.tuning.ground_elasticity_x,
            self.tuning.ground_elasticity_y,
            grounded,
        )
        self.pos = [pos[0], pos[1]]
        self.vel = [vel[0], vel[1]]
        if grounded[0]:
            self.jumped &= ~2
            self.jumped_total = 0
        self.colliding = 0
        if -0.001 < self.vel[0] < 0.001:
            if old_vel_x > 0:
                self.colliding = 1
            elif old_vel_x < 0:
                self.colliding = 2
        else:
            self.left_wall = True
        self.vel[0] *= 1.0 / ramp

    # -- tiles ------------------------------------------------------------

    def _game_layer_clipped(self) -> bool:
        world = self.world
        nx = int(self.pos[0]) // 32
        ny = int(self.pos[1]) // 32
        if nx < 0 or ny < 0 or nx >= world.width or ny >= world.height:
            return True
        return False

    def _check_death_tile(self) -> bool:
        world = self.world
        r = PHYSICAL_SIZE / 3.0
        px, py = self.pos
        for dx, dy in ((r, -r), (r, r), (-r, -r), (-r, r)):
            if world.get_collision_at(px + dx, py + dy) == TILE_DEATH:
                return True
            if world.get_front_tile(_round_to_int(px + dx), _round_to_int(py + dy)) == TILE_DEATH:
                return True
        return False

    def _handle_skippable_tiles(self, index: int) -> None:
        if self._check_death_tile() and not self.super:
            self.dead = True
            self.death_pos = (self.pos[0], self.pos[1])
            return
        if self._game_layer_clipped():
            self.dead = True
            self.death_pos = (self.pos[0], self.pos[1])
            return
        if index < 0:
            return
        world = self.world
        if world.is_speedup(index):
            direction, force, max_speed, typ = world.get_speedup(index)
            temp_vel = list(self.vel)
            if typ == mapmod.TILE_SPEED_BOOST_OLD:
                if force == 255 and max_speed:
                    self.vel = [direction[0] * (max_speed / 5.0), direction[1] * (max_speed / 5.0)]
                else:
                    if 0 < max_speed < 5:
                        max_speed = 5
                    if max_speed > 0:
                        # simplified: the old ramp is rarely used, approximate it
                        temp_vel[0] += direction[0] * force
                        temp_vel[1] += direction[1] * force
                    else:
                        temp_vel[0] += direction[0] * force
                        temp_vel[1] += direction[1] * force
                    self.vel = list(clamp_vel(self.move_restrictions, temp_vel[0], temp_vel[1]))
            else:
                max_speed_scale = 5.0
                if max_speed == 0:
                    ramp_range = self.tuning.velramp_range / (
                        50.0 * math.log(max(self.tuning.velramp_curvature, 1.01))
                    )
                    max_speed = max(ramp_range, self.tuning.velramp_start / 50.0) * max_speed_scale
                current = direction[0] * self.vel[0] + direction[1] * self.vel[1]
                temp_max = max_speed / max_speed_scale
                if current + force > temp_max:
                    temp_vel[0] += direction[0] * (temp_max - current)
                    temp_vel[1] += direction[1] * (temp_max - current)
                else:
                    temp_vel[0] += direction[0] * force
                    temp_vel[1] += direction[1] * force
                self.vel = list(clamp_vel(self.move_restrictions, temp_vel[0], temp_vel[1]))

    def _handle_tiles(self, index: int) -> None:
        world = self.world
        tile_index = world.get_tile_index(index)
        front_index = world.get_front_tile_index(index)
        self.move_restrictions = world.get_move_restrictions(self.pos[0], self.pos[1], 18.0)
        if index < 0:
            self.last_refill_jumps = False
            return

        # freeze
        if (tile_index == TILE_FREEZE or front_index == TILE_FREEZE) and not self.super and not self.deep_frozen:
            self.freeze()
        elif (tile_index == TILE_UNFREEZE or front_index == TILE_UNFREEZE) and not self.deep_frozen:
            self.unfreeze()

        if (tile_index == TILE_DFREEZE or front_index == TILE_DFREEZE) and not self.super and not self.deep_frozen:
            self.deep_frozen = True
        elif (tile_index == TILE_DUNFREEZE or front_index == TILE_DUNFREEZE) and not self.super and self.deep_frozen:
            self.deep_frozen = False

        if (tile_index == TILE_LFREEZE or front_index == TILE_LFREEZE) and not self.super:
            self.live_frozen = True
        elif (tile_index == TILE_LUNFREEZE or front_index == TILE_LUNFREEZE) and not self.super:
            self.live_frozen = False

        # wall jump
        if tile_index == TILE_WALLJUMP or front_index == TILE_WALLJUMP:
            if self.vel[1] > 0 and self.colliding and self.left_wall:
                self.left_wall = False
                self.jumped_total = self.jumps - 2 if self.jumps >= 2 else 0
                self.jumped = 1

        # refill jumps
        if (tile_index == TILE_REFILL_JUMPS or front_index == TILE_REFILL_JUMPS) and not self.last_refill_jumps:
            self.jumped_total = 0
            self.jumped = 0
            self.last_refill_jumps = True
        if tile_index != TILE_REFILL_JUMPS and front_index != TILE_REFILL_JUMPS:
            self.last_refill_jumps = False

        # stopper
        if self.vel[1] > 0 and (self.move_restrictions & CANTMOVE_DOWN):
            self.jumped = 0
            self.jumped_total = 0
        self.vel = list(clamp_vel(self.move_restrictions, self.vel[0], self.vel[1]))

        # switches
        switch_type = world.get_switch_type(index)
        switch_delay = world.get_switch_delay(index)
        if switch_type == TILE_JUMP:
            new_jumps = switch_delay
            if new_jumps == 255:
                new_jumps = -1
            self.jumps = new_jumps

        # teleporters
        tele = world.is_teleport(index)
        if tele and self._tele_outs.get(tele):
            if self.super:
                return
            self.pos = list(self._tele_outs[tele][0])
            self.reset = True
            self.hook_state = HOOK_RETRACTED
            self.hook_pos = list(self.pos)
            return
        evil_tele = world.is_evil_teleport(index)
        if evil_tele and self._tele_outs.get(evil_tele):
            if self.super:
                return
            self.pos = list(self._tele_outs[evil_tele][0])
            self.vel = [0.0, 0.0]
            self.reset = True
            self.hook_state = HOOK_RETRACTED
            self.hook_pos = list(self.pos)
            return
        if world.is_check_evil_teleport(index):
            if self.super:
                return
            for check in range(self.tele_checkpoint - 1, -1, -1):
                if self._tele_check_outs.get(check):
                    self.pos = list(self._tele_check_outs[check][0])
                    self.vel = [0.0, 0.0]
                    self.reset = True
                    self.hook_state = HOOK_RETRACTED
                    self.hook_pos = list(self.pos)
                    return
            self.dead = True
            self.death_pos = (self.pos[0], self.pos[1])
            return
        if world.is_check_teleport(index):
            if self.super:
                return
            for check in range(self.tele_checkpoint - 1, -1, -1):
                if self._tele_check_outs.get(check):
                    self.pos = list(self._tele_check_outs[check][0])
                    self.reset = True
                    self.hook_state = HOOK_RETRACTED
                    self.hook_pos = list(self.pos)
                    return

        tele_checkpoint = world.is_tele_checkpoint(index)
        if tele_checkpoint:
            self.tele_checkpoint = tele_checkpoint

    def _check_start_finish(self) -> None:
        world = self.world
        r = PHYSICAL_SIZE / 3.0
        px, py = self.pos
        offsets = ((r, -r), (r, r), (-r, -r), (-r, r))
        tiles = [world.get_tile_index(world.get_pure_map_index(self.pos))]
        f_tiles = [world.get_front_tile_index(world.get_pure_map_index(self.pos))]
        for dx, dy in offsets:
            index = world.get_pure_map_index_xy(px + dx, py + dy)
            tiles.append(world.get_tile_index(index))
            f_tiles.append(world.get_front_tile_index(index))
        if TILE_START in tiles or TILE_START in f_tiles:
            if not self.started:
                self.started = True
                self.ticks = 0
        if (TILE_FINISH in tiles or TILE_FINISH in f_tiles) and self.started:
            self.finished = True

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<Player pos=({self.pos[0]:.1f}, {self.pos[1]:.1f}) "
            f"vel=({self.vel[0]:.2f}, {self.vel[1]:.2f}) ticks={self.ticks}>"
        )
