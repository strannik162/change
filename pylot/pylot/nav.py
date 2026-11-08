"""Navigation: getting a tee from A to B by *simulating* the real physics.

There are two layers:

``Macro``
    a short, parameterised sequence of inputs (walk right for 25 ticks, jump and
    hold right, throw the hook up-left for 40 ticks, ...).

``NavGraph``
    a lazily expanded A* over *tiles*. Expanding a tile means: put the tee in
    the middle of that tile, try every macro in the catalogue with the real
    DDNet physics and record where the tee ends up when it is standing still
    again. The result is a jump/hook graph that is physically correct by
    construction instead of being hand-tuned per map.

Because the edges are produced by the same code that will later execute them,
the plan can be replayed and verified tick by tick - that verification is what
:mod:`pylot.planner` does, and a mismatch simply triggers a re-plan.
"""

from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .physics import (
    HOOK_FLYING,
    HOOK_GRABBED,
    Input,
    PHYSICAL_SIZE,
    Player,
    SERVER_TICK_SPEED,
)
from .map import TILE, TILE_DEATH, TILE_FINISH

#: a macro never runs longer than this many ticks
MAX_MACRO_TICKS = 55

Vec = Tuple[float, float]
TilePos = Tuple[int, int]


# ---------------------------------------------------------------------------
# macros
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Macro:
    """A short input script, e.g. ``walk_right`` or ``hook_upper_left``."""

    name: str
    ticks: int
    direction: int = 0
    jump_ticks: int = 0
    hook_ticks: int = 0
    target: Tuple[int, int] = (0, -1)
    jump2_start: int = -1
    hook2_start: int = -1
    chain: int = 1
    chain_gap: int = 4

    def inputs(self) -> List[Input]:
        if self.chain > 1:
            return self._chained_inputs()
        out: List[Input] = []
        for tick in range(self.ticks):
            jump = 1 if tick < self.jump_ticks else 0
            if self.jump2_start >= 0 and tick == self.jump2_start:
                jump = 1
            if self.hook_ticks and tick < self.hook_ticks:
                hook = 1
            elif self.hook2_start >= 0 and tick == self.hook2_start:
                hook = 1
            else:
                hook = 0
            out.append(
                Input(
                    direction=self.direction,
                    jump=jump,
                    hook=hook,
                    target_x=self.target[0],
                    target_y=self.target[1],
                )
            )
        return out

    def _chained_inputs(self) -> List[Input]:
        """Several hook pulses in a row: this is how a wall is climbed."""
        out: List[Input] = []
        for pulse in range(self.chain):
            for tick in range(self.hook_ticks + self.chain_gap):
                hook = 1 if tick < self.hook_ticks else 0
                out.append(
                    Input(
                        direction=self.direction,
                        jump=0,
                        hook=hook,
                        target_x=self.target[0],
                        target_y=self.target[1],
                    )
                )
        return out


def _hook_targets() -> List[Tuple[str, Tuple[int, int]]]:
    """12 hook directions (every 30 degrees), ordered clockwise from "up"."""
    targets: List[Tuple[str, Tuple[int, int]]] = []
    for angle_deg in range(0, 360, 30):
        rad = math.radians(angle_deg - 90.0)  # 0 deg == straight up
        x = int(round(math.cos(rad) * 100))
        y = int(round(math.sin(rad) * 100))
        if x == 0 and y == 0:
            x, y = 0, -1
        targets.append((f"hook_{angle_deg:03d}", (x, y)))
    return targets


def build_macro_catalogue() -> List[Macro]:
    """All macros the planner may use. Sorted deterministically."""
    macros: List[Macro] = []
    # pure walking, with the tee starting from standstill
    for direction, label in ((1, "right"), (-1, "left")):
        for ticks in (12, 25, 45):
            macros.append(Macro(f"walk_{label}_{ticks}", ticks, direction=direction))
    # falling (walk off a ledge / drop straight down)
    macros.append(Macro("fall", 40))
    macros.append(Macro("fall_right", 40, direction=1))
    macros.append(Macro("fall_left", 40, direction=-1))
    # jumping, with and without horizontal speed
    for direction, label in ((0, "still"), (1, "right"), (-1, "left")):
        for jump_ticks in (3, 8, 16):
            macros.append(
                Macro(f"jump_{label}_{jump_ticks}", 45, direction=direction, jump_ticks=jump_ticks)
            )
    # double jumps: release after the ground jump, press again in the air
    for direction, label in ((1, "right"), (-1, "left"), (0, "still")):
        for delay in (6, 10, 14, 18):
            macros.append(
                Macro(
                    f"double_jump_{label}_{delay}",
                    delay + 40,
                    direction=direction,
                    jump_ticks=3,
                    jump2_start=delay,
                )
            )
    # hooking: fire in a direction, then drag for a while. The horizontal
    # input follows the hook direction, which is what a player does naturally
    # (it keeps the hook attached for longer).
    for name, target in _hook_targets():
        lean = 0 if target[0] == 0 else (1 if target[0] > 0 else -1)
        for hold in (16, 34):
            macros.append(
                Macro(f"{name}_{hold}", hold + 6, direction=0, hook_ticks=hold, target=target)
            )
            macros.append(
                Macro(
                    f"{name}_{hold}_lean",
                    hold + 6,
                    direction=lean,
                    hook_ticks=hold,
                    target=target,
                )
            )
        # climbing a wall means hooking again and again in the same direction
        for chain, hold in ((2, 22), (3, 22), (4, 22)):
            macros.append(
                Macro(
                    f"{name}_chain{chain}",
                    chain * (hold + 4) + 4,
                    direction=lean,
                    hook_ticks=hold,
                    target=target,
                    chain=chain,
                    chain_gap=4,
                )
            )
    return macros


# ---------------------------------------------------------------------------
# standability
# ---------------------------------------------------------------------------


def is_standable(player: Player, tx: int, ty: int) -> bool:
    """Can the tee rest with its centre in tile (tx, ty)?"""
    world = player.world
    if tx < 0 or ty < 0 or tx >= world.width or ty >= world.height:
        return False
    cx = tx * TILE + 16.0
    cy = ty * TILE + 16.0
    if world.test_box(cx, cy):
        return False
    # needs something to stand on: ground check like CCharacter::IsGrounded
    if world.is_on_ground(cx, cy, PHYSICAL_SIZE):
        return True
    return bool(world.get_move_restrictions(cx, cy + PHYSICAL_SIZE / 2 + 4, 0.0))


def tile_of(pos: Sequence[float]) -> TilePos:
    return (int(pos[0]) // 32, int(pos[1]) // 32)


def tile_center(tile: TilePos) -> Vec:
    return (tile[0] * TILE + 16.0, tile[1] * TILE + 16.0)


# ---------------------------------------------------------------------------
# macro execution
# ---------------------------------------------------------------------------


@dataclass
class MacroResult:
    tile: TilePos
    ticks: int
    inputs: List[Input]
    frames: List[Tuple[float, float]]


def run_macro(
    player: Player, macro: Macro, settle: bool = True, settle_ticks: int = 30
) -> Optional[MacroResult]:
    """Simulate ``macro``; return where the tee comes to rest.

    Returns ``None`` when the tee dies, finishes, or does not end up standing
    on solid ground within the macro time plus a short settle period.
    """
    inputs = macro.inputs()
    frames: List[Tuple[float, float]] = []
    for input_ in inputs:
        player.tick(input_)
        frames.append((player.pos[0], player.pos[1]))
        if player.dead or player.finished:
            return None
    if settle:
        # let the tee land / come to a stop, as an edge must be reproducible
        idle = Input()
        for _ in range(settle_ticks):
            player.tick(idle)
            frames.append((player.pos[0], player.pos[1]))
            if player.dead or player.finished:
                return None
            if player.is_grounded() and abs(player.vel[0]) < 0.05 and abs(player.vel[1]) < 0.05:
                break
    if player.dead or player.finished:
        return None
    tile = tile_of(player.pos)
    return MacroResult(tile, len(frames), inputs, frames)


# ---------------------------------------------------------------------------
# the graph / A*
# ---------------------------------------------------------------------------


@dataclass
class Edge:
    macro: Macro
    target: TilePos
    cost: float
    result: MacroResult


@dataclass
class Plan:
    tiles: List[TilePos]
    macros: List[Macro]
    cost: float

    def __len__(self) -> int:
        return len(self.macros)


class NavGraph:
    """Lazily expanded jump/hook graph plus an A* search on top of it."""

    def __init__(
        self,
        start_player: Player,
        catalogue: Optional[List[Macro]] = None,
        verbose: bool = False,
        settle_ticks: int = 30,
    ):
        self.template = start_player
        self.settle_ticks = settle_ticks
        self.catalogue = catalogue or build_macro_catalogue()
        self.verbose = verbose
        self._cache: Dict[TilePos, List[Edge]] = {}
        self.expansions = 0
        self.simulated_ticks = 0

    # -- edge generation --------------------------------------------------

    def edges(self, tile: TilePos) -> List[Edge]:
        cached = self._cache.get(tile)
        if cached is not None:
            return cached
        edges: List[Edge] = []
        seen: Dict[TilePos, float] = {}
        for macro in self.catalogue:
            self.expansions += 1
            player = self.template.clone()
            player.pos = list(tile_center(tile))
            player.vel = [0.0, 0.0]
            player.dead = False
            player.finished = False
            result = run_macro(player, macro, settle_ticks=self.settle_ticks)
            self.simulated_ticks += macro.ticks + self.settle_ticks
            if result is None:
                continue
            if result.tile == tile and macro.ticks > 20:
                continue  # not useful, it is a no-op edge
            cost = float(result.ticks)
            if result.tile in seen and seen[result.tile] <= cost:
                continue
            seen[result.tile] = cost
            edges.append(Edge(macro, result.tile, cost, result))
        self._cache[tile] = edges
        if self.verbose:
            print(f"  expanded {tile}: {len(edges)} edges")
        return edges

    # -- search -----------------------------------------------------------

    def plan(
        self,
        start: TilePos,
        goals: Iterable[TilePos],
        max_expansions: int = 4000,
        max_tiles: int = 1500,
        time_budget: Optional[float] = None,
        weight: float = 1.0,
        allow_partial: bool = False,
    ) -> Optional[Plan]:
        """A* over tiles. ``weight > 1`` turns it into weighted A* (faster,
        slightly suboptimal routes); ``time_budget`` is in seconds."""
        goal_set = set(goals)
        if not goal_set:
            return None
        if start in goal_set:
            return Plan([start], [], 0.0)

        deadline = None if time_budget is None else time.time() + time_budget

        def heuristic(tile: TilePos) -> float:
            # cheap octile distance to the closest goal tile
            best = 1e9
            for gx, gy in goal_set:
                dx = abs(tile[0] - gx)
                dy = abs(tile[1] - gy)
                # DDNet maps are usually solved by going *up* (chains) and to
                # the right, so vertical progress is cheaper than horizontal
                value = dx * 1.0 + dy * 0.35
                if value < best:
                    best = value
            return best * 7.0 * weight

        open_heap: List[Tuple[float, float, TilePos]] = [(heuristic(start), 0.0, start)]
        came_from: Dict[TilePos, Tuple[TilePos, Macro]] = {}
        best_cost: Dict[TilePos, float] = {start: 0.0}
        expanded = 0
        closed: set = set()
        best_tile = start
        best_score = heuristic(start)
        while open_heap and expanded < max_expansions and len(closed) < max_tiles:
            if deadline is not None and expanded % 16 == 0 and time.time() > deadline:
                break
            _, cost, tile = heapq.heappop(open_heap)
            if cost > best_cost.get(tile, float("inf")):
                continue
            if tile in goal_set:
                return self._reconstruct(start, tile, came_from, cost)
            if tile in closed:
                continue
            closed.add(tile)
            expanded += 1
            for edge in self.edges(tile):
                new_cost = cost + edge.cost
                if new_cost >= best_cost.get(edge.target, float("inf")):
                    continue
                best_cost[edge.target] = new_cost
                came_from[edge.target] = (tile, edge.macro)
                heapq.heappush(open_heap, (new_cost + heuristic(edge.target), new_cost, edge.target))
                score = heuristic(edge.target)
                if score < best_score:
                    best_score = score
                    best_tile = edge.target
        if allow_partial and best_tile != start and best_tile in came_from:
            # no full route within budget: walk as close to the goal as we got
            return self._reconstruct(start, best_tile, came_from, best_cost.get(best_tile, 0.0))
        return None

    @staticmethod
    def _reconstruct(start: TilePos, end: TilePos, came_from: Dict[TilePos, Tuple[TilePos, Macro]], cost: float) -> Plan:
        tiles = [end]
        macros: List[Macro] = []
        current = end
        while current != start:
            previous, macro = came_from[current]
            macros.append(macro)
            tiles.append(previous)
            current = previous
        tiles.reverse()
        macros.reverse()
        return Plan(tiles, macros, cost)


# ---------------------------------------------------------------------------
# helpers used by the CLI
# ---------------------------------------------------------------------------


def standable_tiles(player: Player, limit: int = 0) -> List[TilePos]:
    world = player.world
    tiles: List[TilePos] = []
    for ty in range(world.height):
        for tx in range(world.width):
            if is_standable(player, tx, ty):
                tiles.append((tx, ty))
                if limit and len(tiles) >= limit:
                    return tiles
    return tiles


def goal_tiles(player: Player) -> List[TilePos]:
    """Tiles adjacent to the finish line that a tee can stand on."""
    world = player.world
    goals: List[TilePos] = []
    for ty in range(world.height):
        for tx in range(world.width):
            index = world.index_xy(tx, ty)
            if index != TILE_FINISH:
                continue
            for dy in (0, 1, -1):
                for dx in (0, 1, -1):
                    candidate = (tx + dx, ty + dy)
                    if is_standable(player, *candidate):
                        goals.append(candidate)
    return goals


def start_tile(player: Player) -> TilePos:
    return tile_of(player.pos)
