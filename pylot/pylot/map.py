"""Reading and writing DDNet / Teeworlds ``.map`` datafiles.

A DDNet map is a "datafile": a small header, a table of item types and
item offsets, the items themselves and finally a number of zlib compressed
blobs (tile layers, images, sounds, ...).

Only the parts that matter for a physics bot are exposed here:

* the physics layers (game / front / tele / speedup / switch / tune),
* the tunings embedded in a map (``TILE_TUNE`` zones),
* the start and finish positions.

The module is dependency free (pure standard library).
"""

from __future__ import annotations

import struct
import sys
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# constants (mirrors src/game/mapitems.h)
# ---------------------------------------------------------------------------

MAPITEMTYPE_VERSION = 0
MAPITEMTYPE_INFO = 1
MAPITEMTYPE_IMAGE = 2
MAPITEMTYPE_ENVELOPE = 3
MAPITEMTYPE_GROUP = 4
MAPITEMTYPE_LAYER = 5
MAPITEMTYPE_ENVPOINTS = 6
MAPITEMTYPE_SOUND = 7
ITEMTYPE_EX = 0xFFFF

LAYERTYPE_TILES = 2
LAYERTYPE_QUADS = 3

TILESLAYERFLAG_GAME = 1 << 0
TILESLAYERFLAG_TELE = 1 << 1
TILESLAYERFLAG_SPEEDUP = 1 << 2
TILESLAYERFLAG_FRONT = 1 << 3
TILESLAYERFLAG_SWITCH = 1 << 4
TILESLAYERFLAG_TUNE = 1 << 5

CANTMOVE_LEFT = 1 << 0
CANTMOVE_RIGHT = 1 << 1
CANTMOVE_UP = 1 << 2
CANTMOVE_DOWN = 1 << 3

TILE_AIR = 0
TILE_SOLID = 1
TILE_DEATH = 2
TILE_NOHOOK = 3
TILE_NOLASER = 4
TILE_THROUGH_CUT = 5
TILE_THROUGH = 6
TILE_JUMP = 7
TILE_FREEZE = 9
TILE_TELEINEVIL = 10
TILE_UNFREEZE = 11
TILE_DFREEZE = 12
TILE_DUNFREEZE = 13
TILE_TELEINWEAPON = 14
TILE_TELEINHOOK = 15
TILE_WALLJUMP = 16
TILE_EHOOK_ENABLE = 17
TILE_EHOOK_DISABLE = 18
TILE_HIT_ENABLE = 19
TILE_HIT_DISABLE = 20
TILE_SOLO_ENABLE = 21
TILE_SOLO_DISABLE = 22
TILE_SWITCHTIMEDOPEN = 22
TILE_SWITCHTIMEDCLOSE = 23
TILE_SWITCHOPEN = 24
TILE_SWITCHCLOSE = 25
TILE_TELEIN = 26
TILE_TELEOUT = 27
TILE_SPEED_BOOST_OLD = 28
TILE_SPEED_BOOST = 29
TILE_TELECHECK = 29
TILE_TELECHECKOUT = 30
TILE_TELECHECKIN = 31
TILE_REFILL_JUMPS = 32
TILE_START = 33
TILE_FINISH = 34
TILE_STOP = 60
TILE_STOPS = 61
TILE_STOPA = 62
TILE_TELECHECKINEVIL = 63
TILE_CP = 64
TILE_CP_F = 65
TILE_THROUGH_ALL = 66
TILE_THROUGH_DIR = 67
TILE_TUNE = 68
TILE_OLDLASER = 71
TILE_NPC = 72
TILE_EHOOK = 73
TILE_NOHIT = 74
TILE_NPH = 75
TILE_UNLOCK_TEAM = 76
TILE_ADD_TIME = 79
TILE_NPC_DISABLE = 88
TILE_UNLIMITED_JUMPS_DISABLE = 89
TILE_JETPACK_DISABLE = 90
TILE_NPH_DISABLE = 91
TILE_SUBTRACT_TIME = 95
TILE_TELE_GUN_ENABLE = 96
TILE_TELE_GUN_DISABLE = 97
TILE_ALLOW_TELE_GUN = 98
TILE_ALLOW_BLUE_TELE_GUN = 99
TILE_NPC_ENABLE = 104
TILE_UNLIMITED_JUMPS_ENABLE = 105
TILE_JETPACK_ENABLE = 106
TILE_NPH_ENABLE = 107
TILE_TELE_GRENADE_ENABLE = 112
TILE_TELE_GRENADE_DISABLE = 113
TILE_TELE_LASER_ENABLE = 128
TILE_TELE_LASER_DISABLE = 129
TILE_LFREEZE = 144
TILE_LUNFREEZE = 145
TILE_ENTITIES_OFF_1 = 190
TILE_ENTITIES_OFF_2 = 191

TILEFLAG_XFLIP = 1 << 0
TILEFLAG_YFLIP = 1 << 1
TILEFLAG_OPAQUE = 1 << 2
TILEFLAG_ROTATE = 1 << 3

ROTATION_0 = 0
ROTATION_90 = TILEFLAG_ROTATE
ROTATION_180 = TILEFLAG_XFLIP | TILEFLAG_YFLIP
ROTATION_270 = TILEFLAG_XFLIP | TILEFLAG_YFLIP | TILEFLAG_ROTATE

ENTITY_OFFSET = 255 - 16 * 4

ENTITY_NULL = 0
ENTITY_SPAWN = 1
ENTITY_SPAWN_RED = 2
ENTITY_SPAWN_BLUE = 3
ENTITY_FLAGSTAND_RED = 4
ENTITY_FLAGSTAND_BLUE = 5
ENTITY_ARMOR_1 = 6
ENTITY_HEALTH_1 = 7
ENTITY_WEAPON_SHOTGUN = 8
ENTITY_WEAPON_GRENADE = 9
ENTITY_POWERUP_NINJA = 10
ENTITY_WEAPON_LASER = 11

#: vertical distance between the centre of two tiles, in game units
TILE = 32.0


# ---------------------------------------------------------------------------
# datafile primitives
# ---------------------------------------------------------------------------


#: ``sizeof(CDatafileHeader)`` of the C++ implementation
DATAFILE_HEADER_SIZE = 36


class MapError(Exception):
    """Raised when a map file cannot be parsed."""


def _read_pascal_string(data: bytes, offset: int) -> Tuple[str, int]:
    if offset >= len(data):
        return "", len(data)
    length = data[offset]
    if offset + 1 + length > len(data):
        return "", len(data)
    raw = data[offset + 1 : offset + 1 + length]
    return raw.decode("utf-8", "replace"), offset + 1 + length


@dataclass
class DatafileItem:
    type: int
    uid: int
    data: bytes

    @property
    def base_type(self) -> int:
        return self.type


class Datafile:
    """Minimal reader for the Teeworlds datafile format (versions 3 and 4)."""

    def __init__(self, blob: bytes):
        self.blob = blob
        if len(blob) < DATAFILE_HEADER_SIZE:
            raise MapError("file is too small to be a map")
        magic = blob[0:4]
        if magic not in (b"DATA", b"ATAD"):
            raise MapError(f"wrong header magic {magic!r}")
        self.version = struct.unpack_from("<i", blob, 4)[0]
        if self.version not in (3, 4):
            raise MapError(f"unsupported map version {self.version}")
        (
            _file_size,
            _swap_len,
            num_item_types,
            num_items,
            num_raw_data,
            item_size,
            data_size,
        ) = struct.unpack_from("<7i", blob, 8)
        if num_item_types < 0 or num_items < 0 or num_raw_data < 0:
            raise MapError("corrupt header")
        self.num_item_types = num_item_types
        self.num_items = num_items
        self.num_raw_data = num_raw_data
        self.item_size = item_size
        self.data_size = data_size

        offset = DATAFILE_HEADER_SIZE
        self.item_types: List[Tuple[int, int, int]] = []
        for _ in range(num_item_types):
            # CDatafileItemType is {m_Type, m_Start, m_Num}
            self.item_types.append(struct.unpack_from("<3i", blob, offset))
            offset += 12
        self.item_offsets = list(struct.unpack_from(f"<{num_items}i", blob, offset)) if num_items else []
        offset += 4 * num_items
        self.data_offsets = list(struct.unpack_from(f"<{num_raw_data}i", blob, offset)) if num_raw_data else []
        offset += 4 * num_raw_data
        self.data_sizes: Optional[List[int]] = None
        if self.version == 4 and num_raw_data:
            self.data_sizes = list(struct.unpack_from(f"<{num_raw_data}i", blob, offset))
            offset += 4 * num_raw_data
        self.item_start = offset
        self.data_start = offset + item_size
        self._data_cache: Dict[int, bytes] = {}

        #: flattened list of items, index == position in the file
        self.items: List[DatafileItem] = []
        for index in range(num_items):
            item_offset = self.item_offsets[index]
            type_and_id, size = struct.unpack_from("<Ii", blob, self.item_start + item_offset)
            item_type = (type_and_id >> 16) & 0xFFFF
            uid = type_and_id & 0xFFFF
            payload = blob[
                self.item_start + item_offset + 8 : self.item_start + item_offset + 8 + size
            ]
            self.items.append(DatafileItem(item_type, uid, payload))

    # -- raw data ---------------------------------------------------------

    def data(self, index: int) -> bytes:
        if index < 0 or index >= self.num_raw_data:
            return b""
        if index in self._data_cache:
            return self._data_cache[index]
        start = self.data_offsets[index]
        if index + 1 < self.num_raw_data:
            end = self.data_offsets[index + 1]
        else:
            end = self.data_size
        chunk = self.blob[self.data_start + start : self.data_start + end]
        if self.version == 4:
            try:
                chunk = zlib.decompress(chunk)
            except zlib.error as exc:  # pragma: no cover - corrupt map
                raise MapError(f"failed to decompress data block {index}: {exc}") from exc
        self._data_cache[index] = chunk
        return chunk

    # -- items ------------------------------------------------------------

    def items_of_type(self, item_type: int) -> Iterator[DatafileItem]:
        for typ, start, num in self.item_types:
            if typ == item_type:
                for index in range(start, start + num):
                    yield self.items[index]

    def get_type(self, item_type: int) -> Tuple[int, int]:
        for typ, start, num in self.item_types:
            if typ == item_type:
                return start, num
        return 0, 0


# ---------------------------------------------------------------------------
# layers
# ---------------------------------------------------------------------------

TILE_LAYER_HEADER = struct.Struct("<3i i i i i 4i i i i i")
#: m_Layer.m_Version, m_Layer.m_Type, m_Layer.m_Flags, m_Version, m_Width,
#: m_Height, m_Flags, m_Color (4 ints), m_ColorEnv, m_ColorEnvOffset,
#: m_Image, m_Data


@dataclass
class Tile:
    index: int = 0
    flags: int = 0
    skip: int = 0
    reserved: int = 0


@dataclass
class TilesLayer:
    """A physical layer of the map (game / front / tele / ...)."""

    kind: str
    width: int
    height: int
    tiles: List[Tile] = field(default_factory=list)
    data_index: int = -1
    name: str = ""

    def at(self, x: int, y: int) -> Tile:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return Tile()
        return self.tiles[y * self.width + x]

    def index_at(self, x: int, y: int) -> int:
        return self.at(x, y).index


@dataclass
class TeleTile:
    number: int = 0
    type: int = 0


@dataclass
class SpeedupTile:
    force: int = 0
    max_speed: int = 0
    type: int = 0
    angle: int = 0


@dataclass
class SwitchTile:
    number: int = 0
    type: int = 0
    flags: int = 0
    delay: int = 0


@dataclass
class TuneTile:
    number: int = 0
    type: int = 0


@dataclass
class Group:
    offset_x: int = 0
    offset_y: int = 0
    start_layer: int = 0
    num_layers: int = 0
    version: int = 0


# ---------------------------------------------------------------------------
# tunings (mirrors src/game/tuning.h)
# ---------------------------------------------------------------------------

#: (attribute name, script name, default value)
TUNINGS: List[Tuple[str, str, float]] = [
    ("ground_control_speed", "ground_control_speed", 10.0),
    ("ground_control_accel", "ground_control_accel", 2.0),
    ("ground_friction", "ground_friction", 0.5),
    ("ground_jump_impulse", "ground_jump_impulse", 13.2),
    ("air_jump_impulse", "air_jump_impulse", 12.0),
    ("air_control_speed", "air_control_speed", 5.0),
    ("air_control_accel", "air_control_accel", 1.5),
    ("air_friction", "air_friction", 0.95),
    ("hook_length", "hook_length", 380.0),
    ("hook_fire_speed", "hook_fire_speed", 80.0),
    ("hook_drag_accel", "hook_drag_accel", 3.0),
    ("hook_drag_speed", "hook_drag_speed", 15.0),
    ("gravity", "gravity", 0.5),
    ("velramp_start", "velramp_start", 550.0),
    ("velramp_range", "velramp_range", 2000.0),
    ("velramp_curvature", "velramp_curvature", 1.4),
    ("gun_curvature", "gun_curvature", 0.0),
    ("gun_speed", "gun_speed", 1400.0),
    ("gun_lifetime", "gun_lifetime", 2.0),
    ("shotgun_curvature", "shotgun_curvature", 0.0),
    ("shotgun_speed", "shotgun_speed", 500.0),
    ("shotgun_speeddiff", "shotgun_speeddiff", 0.0),
    ("shotgun_lifetime", "shotgun_lifetime", 0.20),
    ("grenade_curvature", "grenade_curvature", 7.0),
    ("grenade_speed", "grenade_speed", 1000.0),
    ("grenade_lifetime", "grenade_lifetime", 2.0),
    ("laser_reach", "laser_reach", 800.0),
    ("laser_bounce_delay", "laser_bounce_delay", 150),
    ("laser_bounce_num", "laser_bounce_num", 1000),
    ("laser_bounce_cost", "laser_bounce_cost", 0),
    ("laser_damage", "laser_damage", 5),
    ("player_collision", "player_collision", 1),
    ("player_hooking", "player_hooking", 1),
    ("jetpack_strength", "jetpack_strength", 400.0),
    ("shotgun_strength", "shotgun_strength", 10.0),
    ("explosion_strength", "explosion_strength", 6.0),
    ("hammer_strength", "hammer_strength", 1.0),
    ("hook_duration", "hook_duration", 1.25),
    ("hammer_fire_delay", "hammer_fire_delay", 125),
    ("gun_fire_delay", "gun_fire_delay", 125),
    ("shotgun_fire_delay", "shotgun_fire_delay", 500),
    ("grenade_fire_delay", "grenade_fire_delay", 500),
    ("laser_fire_delay", "laser_fire_delay", 800),
    ("ninja_fire_delay", "ninja_fire_delay", 800),
    ("hammer_hit_fire_delay", "hammer_hit_fire_delay", 320),
    ("ground_elasticity_x", "ground_elasticity_x", 0.0),
    ("ground_elasticity_y", "ground_elasticity_y", 0.0),
]

TUNING_BY_SCRIPT_NAME = {script: name for name, script, _ in TUNINGS}
DEFAULT_TUNING = {name: value for name, _, value in TUNINGS}
TUNING_ORDER = [name for name, _, _ in TUNINGS]

#: DDRace clients receive these tunings from the server, matching
#: ``CGameContext::OnInit`` in the DDNet server.
DDNET_SERVER_TUNING_OVERRIDES = {
    "gun_curvature": 0.0,
    "gun_speed": 1400.0,
    "shotgun_curvature": 0.0,
    "shotgun_speed": 500.0,
    "shotgun_speeddiff": 0.0,
}


def default_tuning() -> Dict[str, float]:
    tuning = dict(DEFAULT_TUNING)
    tuning.update(DDNET_SERVER_TUNING_OVERRIDES)
    return tuning


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------


class Map:
    """A parsed DDNet map with the physics layers ready to be queried."""

    def __init__(self, path: Optional[str] = None, blob: Optional[bytes] = None):
        if blob is None:
            if path is None:
                raise ValueError("either path or blob is required")
            with open(path, "rb") as handle:
                blob = handle.read()
        self.blob = blob
        self.path = path
        self.datafile = Datafile(blob)

        self.game_layer: Optional[TilesLayer] = None
        self.front_layer: Optional[TilesLayer] = None
        self.tele_layer: Optional[TilesLayer] = None
        self.speedup_layer: Optional[TilesLayer] = None
        self.switch_layer: Optional[TilesLayer] = None
        self.tune_layer: Optional[TilesLayer] = None

        self.tele_tiles: List[TeleTile] = []
        self.speedup_tiles: List[SpeedupTile] = []
        self.switch_tiles: List[SwitchTile] = []
        self.tune_tiles: List[TuneTile] = []

        self.groups: List[Group] = []
        self.map_info: Dict[str, str] = {}
        self._parse()

        if self.game_layer is None:
            raise MapError("map has no game layer")

        self.width = self.game_layer.width
        self.height = self.game_layer.height

        #: tuning zone -> {tuning name: value}; zone 0 is the global one
        self.tuning_zones: Dict[int, Dict[str, float]] = {}
        self._build_tuning_zones()

    # -- parsing ----------------------------------------------------------

    def _parse(self) -> None:
        df = self.datafile
        for item in df.items_of_type(MAPITEMTYPE_INFO):
            if len(item.data) >= 20:
                version, author, map_version, credits, license_ = struct.unpack_from(
                    "<5i", item.data, 0
                )

                def string_at(idx: int) -> str:
                    if idx <= 0:
                        return ""
                    try:
                        value, _ = _read_pascal_string(df.data(idx), 0)
                    except Exception:  # pragma: no cover - defensive
                        return ""
                    return value

                self.map_info = {
                    "author": string_at(author),
                    "version": string_at(map_version),
                    "credits": string_at(credits),
                    "license": string_at(license_),
                }
        for item in df.items_of_type(MAPITEMTYPE_GROUP):
            if len(item.data) < 28:
                continue
            version = struct.unpack_from("<i", item.data, 0)[0]
            offset_x, offset_y = struct.unpack_from("<2i", item.data, 4)
            start_layer, num_layers = struct.unpack_from("<2i", item.data, 20)
            self.groups.append(Group(offset_x, offset_y, start_layer, num_layers, version))

        for item in df.items_of_type(MAPITEMTYPE_LAYER):
            if len(item.data) < 12:
                continue
            _version, layer_type, _flags = struct.unpack_from("<3i", item.data, 0)
            if layer_type != LAYERTYPE_TILES:
                continue
            self._parse_tiles_layer(item)

    def _parse_tiles_layer(self, item: DatafileItem) -> None:
        data = item.data
        if len(data) < 60:
            return
        (
            _layer_version,
            _layer_type,
            _layer_flags,
            map_version,
            width,
            height,
            flags,
            _c1,
            _c2,
            _c3,
            _c4,
            _color_env,
            _color_env_offset,
            _image,
            data_index,
        ) = TILE_LAYER_HEADER.unpack_from(data, 0)

        name = ""
        if map_version >= 3 and len(data) >= 72:
            chars = struct.unpack_from("<12B", data, 60)
            name = bytes(chars).split(b"\x00")[0].decode("utf-8", "replace")

        df = self.datafile
        if flags & TILESLAYERFLAG_GAME:
            layer = TilesLayer("game", width, height, data_index=data_index, name=name or "Game")
            raw = df.data(data_index)
            count = min(width * height, len(raw) // 4)
            layer.tiles = [Tile(raw[i * 4], raw[i * 4 + 1], raw[i * 4 + 2], raw[i * 4 + 3]) for i in range(count)]
            self.game_layer = layer
        elif flags & TILESLAYERFLAG_FRONT:
            layer = TilesLayer("front", width, height, data_index=data_index, name=name or "Front")
            raw = df.data(data_index)
            count = min(width * height, len(raw) // 4)
            layer.tiles = [Tile(raw[i * 4], raw[i * 4 + 1], raw[i * 4 + 2], raw[i * 4 + 3]) for i in range(count)]
            self.front_layer = layer
        elif flags & TILESLAYERFLAG_TELE:
            raw = df.data(data_index)
            count = min(width * height, len(raw) // 2)
            self.tele_tiles = [TeleTile(raw[i * 2], raw[i * 2 + 1]) for i in range(count)]
        elif flags & TILESLAYERFLAG_SPEEDUP:
            raw = df.data(data_index)
            count = min(width * height, len(raw) // 6)
            self.speedup_tiles = []
            for i in range(count):
                force, max_speed, typ, _res, angle = struct.unpack_from("<4Bh", raw, i * 6)
                self.speedup_tiles.append(SpeedupTile(force, max_speed, typ, angle))
        elif flags & TILESLAYERFLAG_SWITCH:
            raw = df.data(data_index)
            count = min(width * height, len(raw) // 4)
            self.switch_tiles = [
                SwitchTile(raw[i * 4], raw[i * 4 + 1], raw[i * 4 + 2], raw[i * 4 + 3]) for i in range(count)
            ]
        elif flags & TILESLAYERFLAG_TUNE:
            raw = df.data(data_index)
            count = min(width * height, len(raw) // 2)
            self.tune_tiles = [TuneTile(raw[i * 2], raw[i * 2 + 1]) for i in range(count)]

    def _build_tuning_zones(self) -> None:
        zones: Dict[int, Dict[str, float]] = {}
        for idx, tile in enumerate(self.tune_tiles):
            if tile.type <= 0 or tile.number <= 0:
                continue
            if idx * 2 + 4 > 0:  # always true, keeps the linter honest
                pass
            tuning = zones.setdefault(tile.number, {})
            # the tune layer stores the tuning which is applied *inside* a zone;
            # the actual values live in the game layer tile of type TILE_TUNE
            # (the switch layer stores the number/type).
        # collect zone tunings from switch tiles of type TILE_TUNE
        for idx, tile in enumerate(self.switch_tiles):
            if tile.type != TILE_TUNE or tile.number <= 0:
                continue
            zone = tile.number
            tuning = zones.setdefault(zone, {})
            # m_Flags holds the index of the tuning parameter, m_Delay the value
            if tile.flags < len(TUNING_ORDER):
                name = TUNING_ORDER[tile.flags]
                value = tile.delay if tile.delay < 128 else tile.delay - 256
                tuning[name] = float(value)
        self.tuning_zones = zones

    # -- accessors --------------------------------------------------------

    def tuning(self, zone: int = 0) -> Dict[str, float]:
        tuning = default_tuning()
        tuning.update(self.tuning_zones.get(zone, {}))
        return tuning

    def tile_index(self, x: int, y: int) -> int:
        assert self.game_layer is not None
        return self.game_layer.index_at(x, y)

    def front_index(self, x: int, y: int) -> int:
        if self.front_layer is None:
            return 0
        return self.front_layer.index_at(x, y)

    def tele_at(self, index: int) -> TeleTile:
        if 0 <= index < len(self.tele_tiles):
            return self.tele_tiles[index]
        return TeleTile()

    def speedup_at(self, index: int) -> SpeedupTile:
        if 0 <= index < len(self.speedup_tiles):
            return self.speedup_tiles[index]
        return SpeedupTile()

    def switch_at(self, index: int) -> SwitchTile:
        if 0 <= index < len(self.switch_tiles):
            return self.switch_tiles[index]
        return SwitchTile()

    def tune_at(self, index: int) -> TuneTile:
        if 0 <= index < len(self.tune_tiles):
            return self.tune_tiles[index]
        return TuneTile()

    # -- game entities ----------------------------------------------------

    def find_tiles(self, *indices: int) -> List[Tuple[int, int]]:
        """Return tile coordinates where the game layer holds one of ``indices``."""
        found: List[Tuple[int, int]] = []
        wanted = set(indices)
        assert self.game_layer is not None
        for y in range(self.height):
            row = y * self.width
            for x in range(self.width):
                if self.game_layer.tiles[row + x].index in wanted:
                    found.append((x, y))
        return found

    def start_positions(self) -> List[Tuple[float, float]]:
        return [(x * TILE + 16.0, y * TILE + 16.0) for x, y in self.find_tiles(TILE_START)]

    def finish_positions(self) -> List[Tuple[float, float]]:
        return [(x * TILE + 16.0, y * TILE + 16.0) for x, y in self.find_tiles(TILE_FINISH)]

    def spawn_positions(self) -> List[Tuple[float, float]]:
        """Classic deathmatch spawns (``ENTITY_SPAWN``)."""
        positions = []
        for y in range(self.height):
            for x in range(self.width):
                index = self.game_layer.index_at(x, y)
                if ENTITY_OFFSET + ENTITY_SPAWN <= index <= ENTITY_OFFSET + ENTITY_SPAWN_BLUE:
                    positions.append((x * TILE + 16.0, y * TILE + 16.0))
        return positions

    def solid(self, x: int, y: int) -> bool:
        index = self.tile_index(x, y) if 0 <= x < self.width and 0 <= y < self.height else TILE_SOLID
        return index in (TILE_SOLID, TILE_NOHOOK)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Map {self.path or '<memory>'} {self.width}x{self.height}>"


# ---------------------------------------------------------------------------
# map writing (used by ``pylot generate`` for test maps)
# ---------------------------------------------------------------------------


@dataclass
class _OutItem:
    type: int
    uid: int
    data: bytes


class MapWriter:
    """Build a small map from python. Used for the generated demo maps."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.game = [Tile() for _ in range(width * height)]
        self.front = [Tile() for _ in range(width * height)]
        self._items: List[_OutItem] = [
            _OutItem(MAPITEMTYPE_VERSION, 0, struct.pack("<i", 1)),
            _OutItem(
                MAPITEMTYPE_INFO,
                0,
                struct.pack("<5i", 1, -1, -1, -1, -1),
            ),
        ]

    def set_game(self, x: int, y: int, index: int, flags: int = 0) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.game[y * self.width + x] = Tile(index, flags)

    def set_front(self, x: int, y: int, index: int, flags: int = 0) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.front[y * self.width + x] = Tile(index, flags)

    def to_bytes(self) -> bytes:
        # one group holding the two tile layers (front + game)
        layers: List[Tuple[bytes, bytes]] = []
        datas: List[bytes] = []

        front_bytes = bytearray()
        for tile in self.front:
            front_bytes += bytes((tile.index, tile.flags, 0, 0))
        game_bytes = bytearray()
        for tile in self.game:
            game_bytes += bytes((tile.index, tile.flags, 0, 0))

        datas.append(bytes(front_bytes))
        datas.append(bytes(game_bytes))

        num_layers = 2
        # CMapItemGroup: version, offset x/y, parallax x/y, start layer,
        # num layers, use clipping, clip x/y/width/height, name (12 bytes)
        group = struct.pack(
            "<11i",
            3, 0, 0, 100, 100, 0, num_layers, 0, 0, 0, 0
        ) + b"\x00" * 12
        self._items.append(_OutItem(MAPITEMTYPE_GROUP, 0, group))

        def tile_layer_item(flags: int, data_index: int, name: bytes) -> bytes:
            # CMapItemLayerTilemap is exactly 92 bytes:
            #   layer header (version, type, flags), m_Version, width, height,
            #   m_Flags, color, color env + offset, image, data, name,
            #   tele, speedup, front, switch, tune
            payload = bytearray()
            payload += struct.pack("<3i", 3, LAYERTYPE_TILES, 0)
            payload += struct.pack("<4i", 3, self.width, self.height, flags)
            payload += struct.pack("<6i", -1, -1, -1, -1, 0, 0)
            payload += struct.pack("<2i", -1, data_index)
            payload += name.ljust(12, b"\x00")[:12]
            payload += struct.pack("<5i", -1, -1, -1, -1, -1)
            assert len(payload) == 92, len(payload)
            return bytes(payload)

        for index, (flags, name) in enumerate(
            ((TILESLAYERFLAG_FRONT, b"Front"), (TILESLAYERFLAG_GAME, b"Game"))
        ):
            self._items.append(_OutItem(MAPITEMTYPE_LAYER, index, tile_layer_item(flags, index, name)))

        return _serialise_datafile(self._items, datas)


def _serialise_datafile(items: List[_OutItem], datas: List[bytes]) -> bytes:
    # group items by type; item type table must list types in use
    types: List[int] = sorted({item.type for item in items})
    type_index = {typ: idx for idx, typ in enumerate(types)}

    items_by_type: Dict[int, List[int]] = {typ: [] for typ in types}
    for idx, item in enumerate(items):
        items_by_type[item.type].append(idx)

    type_table = bytearray()
    item_order: List[int] = []
    for typ in types:
        first = len(item_order)
        item_order.extend(items_by_type[typ])
        type_table += struct.pack("<3i", typ, first, len(items_by_type[typ]))

    item_blob = bytearray()
    item_offsets: List[int] = []
    for idx in item_order:
        item = items[idx]
        item_offsets.append(len(item_blob))
        item_blob += struct.pack("<Ii", (item.type << 16) | item.uid, len(item.data))
        item_blob += item.data

    compressed: List[bytes] = []
    data_offsets: List[int] = []
    uncompressed_sizes: List[int] = []
    total = 0
    for blob in datas:
        data_offsets.append(total)
        uncompressed_sizes.append(len(blob))
        packed = zlib.compress(blob, 9)
        compressed.append(packed)
        total += len(packed)
    data_blob = b"".join(compressed)

    num_items = len(items)
    num_data = len(datas)
    offset_blob = struct.pack(f"<{num_items}i", *item_offsets)
    offset_blob += struct.pack(f"<{num_data}i", *data_offsets)
    offset_blob += struct.pack(f"<{num_data}i", *uncompressed_sizes)

    size_offset = 16  # magic + version + size + swaplen
    swap_len = size_offset + len(type_table) + len(offset_blob) + len(item_blob)
    file_size = swap_len + len(data_blob)

    header = b"DATA"
    header += struct.pack("<i", 4)
    header += struct.pack("<i", file_size)
    header += struct.pack("<i", swap_len)
    header += struct.pack(
        "<5i", len(types), num_items, num_data, len(item_blob), len(data_blob)
    )
    return header + bytes(type_table) + offset_blob + bytes(item_blob) + data_blob
