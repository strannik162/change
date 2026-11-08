"""Binary formats produced by the bot: ``.gho`` ghosts and ``.demo`` demos.

Both formats are implemented closely enough to the C++ versions
(``src/engine/client/ghost.cpp`` and ``src/engine/shared/demo.cpp``) that the
DDNet client can play them back, so a bot run can be watched in game instead
of only being printed to the terminal.

Implementing them turned out to be the cheapest way to *verify* a bot run:
a ghost stores exactly the tee positions we simulated, so if the ghost
desynchronises visually we know the physics port drifted.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# variable length integers (CVariableInt)
# ---------------------------------------------------------------------------


def pack_int(value: int) -> bytes:
    out = bytearray()
    out.append(0)
    if value < 0:
        out[0] |= 0x40
        value = ~value
    out[0] |= value & 0x3F
    value >>= 6
    while value:
        out[-1] |= 0x80
        out.append(value & 0x7F)
        value >>= 7
    return bytes(out)


def unpack_int(data: bytes, offset: int) -> Tuple[int, int]:
    sign = (data[offset] >> 6) & 1
    value = data[offset] & 0x3F
    masks = (0x7F, 0x7F, 0x7F, 0x0F)
    shifts = (6, 13, 20, 27)
    for i in range(4):
        if not data[offset] & 0x80:
            break
        offset += 1
        value |= (data[offset] & masks[i]) << shifts[i]
    if sign:
        value = ~value
    return value, offset + 1


def compress_ints(values: Sequence[int]) -> bytes:
    return b"".join(pack_int(int(v)) for v in values)


# ---------------------------------------------------------------------------
# Huffman (CHuffman) - needed for ghost chunks and demo packets
# ---------------------------------------------------------------------------

HUFFMAN_MAX_SYMBOLS = 257
HUFFMAN_FREQUENCIES = [
    1 << 30, 4545, 2657, 431, 1950, 919, 444, 482, 2244, 617, 838, 542, 715, 1814, 304, 240, 754, 212, 647, 186,
    283, 131, 146, 166, 543, 164, 167, 136, 179, 859, 363, 113, 157, 154, 204, 108, 137, 180, 202, 176,
    872, 404, 168, 134, 151, 111, 113, 109, 120, 126, 129, 100, 41, 20, 16, 22, 18, 18, 17, 19,
    16, 37, 13, 21, 362, 166, 99, 78, 95, 88, 81, 70, 83, 284, 91, 187, 77, 68, 52, 68,
    59, 66, 61, 638, 71, 157, 50, 46, 69, 43, 11, 24, 13, 19, 10, 12, 12, 20, 14, 9,
    20, 20, 10, 10, 15, 15, 12, 12, 7, 19, 15, 14, 13, 18, 35, 19, 17, 14, 8, 5,
    15, 17, 9, 15, 14, 18, 8, 10, 2173, 134, 157, 68, 188, 60, 170, 60, 194, 62, 175, 71,
    148, 67, 167, 78, 211, 67, 156, 69, 1674, 90, 174, 53, 147, 89, 181, 51, 174, 63, 163, 80,
    167, 94, 128, 122, 223, 153, 218, 77, 200, 110, 190, 73, 174, 69, 145, 66, 277, 143, 141, 60,
    136, 53, 180, 57, 142, 57, 158, 61, 166, 112, 152, 92, 26, 22, 21, 28, 20, 26, 30, 21,
    32, 27, 20, 17, 23, 21, 30, 22, 22, 21, 27, 25, 17, 27, 23, 18, 39, 26, 15, 21,
    12, 18, 18, 27, 20, 18, 15, 19, 11, 17, 33, 12, 18, 15, 19, 18, 16, 26, 17, 18,
    9, 10, 25, 22, 22, 17, 20, 16, 6, 16, 15, 20, 14, 18, 24, 335, 1,
]


class _Node:
    __slots__ = ("leaves", "bits", "num_bits", "symbol", "frequency")

    def __init__(self):
        self.leaves = [0xFFFF, 0xFFFF]
        self.bits = 0
        self.num_bits = 0
        self.symbol = 0
        self.frequency = 0


class Huffman:
    """Port of ``CHuffman`` (only compression is needed here)."""

    def __init__(self) -> None:
        self.nodes: List[_Node] = []
        self._build(HUFFMAN_FREQUENCIES)

    @staticmethod
    def _sort_key(entry):
        # mirrors ``CompareNodesByFrequencyDesc``: highest frequency first,
        # ties keep their insertion order (``std::stable_sort``)
        return -entry[1]

    def _build(self, frequencies: Sequence[int]) -> None:
        nodes: List[_Node] = []
        for symbol in range(len(frequencies)):
            node = _Node()
            node.symbol = symbol
            node.num_bits = 0xFFFFFFFF
            nodes.append(node)

        left = [[symbol, frequencies[symbol]] for symbol in range(len(frequencies))]
        num_left = len(left)
        while num_left > 1:
            left[:num_left] = sorted(left[:num_left], key=self._sort_key)
            first, second = left[num_left - 1], left[num_left - 2]
            parent_index = len(nodes)
            parent = _Node()
            parent.leaves = [first[0], second[0]]
            parent.symbol = parent_index
            nodes.append(parent)
            second[0] = parent_index
            second[1] = first[1] + second[1]
            num_left -= 1

        self.nodes = nodes
        self._set_bits(len(nodes) - 1, 0, 0)

    def _set_bits(self, index: int, bits: int, depth: int) -> None:
        node = self.nodes[index]
        if node.leaves[1] != 0xFFFF:
            self._set_bits(node.leaves[1], bits | (1 << depth), depth + 1)
        if node.leaves[0] != 0xFFFF:
            self._set_bits(node.leaves[0], bits, depth + 1)
        if node.num_bits:
            node.bits = bits
            node.num_bits = depth

    def compress(self, data: bytes) -> bytes:
        bits = 0
        bitcount = 0
        out = bytearray()
        for byte in list(data) + [256]:  # the EOF symbol terminates the stream
            node = self.nodes[byte]
            bits |= node.bits << bitcount
            bitcount += node.num_bits
            while bitcount >= 8:
                out.append(bits & 0xFF)
                bits >>= 8
                bitcount -= 8
        if bitcount:
            out.append(bits & 0xFF)
        return bytes(out)


def huffman_decompress(data: bytes, size: int) -> bytes:
    """Decode a Huffman stream; used by the tests to verify our encoder."""
    global _HUFFMAN
    if _HUFFMAN is None:
        _HUFFMAN = Huffman()
    huffman = _HUFFMAN
    nodes = huffman.nodes
    bits = 0
    bitcount = 0
    pos = 0
    out = bytearray()
    node = nodes[len(nodes) - 1]
    while True:
        while bitcount < 24 and pos < len(data):
            bits |= data[pos] << bitcount
            bitcount += 8
            pos += 1
        if bitcount == 0:
            raise ValueError("truncated huffman stream")
        leaf = node.leaves[bits & 1]
        bits >>= 1
        bitcount -= 1
        node = nodes[leaf]
        if node.num_bits:
            if node.symbol == 256:  # EOF
                return bytes(out)
            out.append(node.symbol & 0xFF)
            node = nodes[len(nodes) - 1]
            if len(out) >= size and size:
                return bytes(out)


_HUFFMAN: Optional[Huffman] = None


def huffman_compress(data: bytes) -> bytes:
    global _HUFFMAN
    if _HUFFMAN is None:
        _HUFFMAN = Huffman()
    return _HUFFMAN.compress(data)


# ---------------------------------------------------------------------------
# ghost files
# ---------------------------------------------------------------------------

GHOST_MAGIC = b"TWGHOST\x00"
GHOST_VERSION = 6
GHOSTDATA_TYPE_SKIN = 0
GHOSTDATA_TYPE_CHARACTER = 2
GHOSTDATA_TYPE_START_TICK = 3
NUM_ITEMS_PER_CHUNK = 50
MAX_ITEM_SIZE = 128


@dataclass
class GhostFrame:
    """One client tick of the tee path (``CGhostCharacter``)."""

    tick: int = 0
    x: int = 0
    y: int = 0
    vel_x: int = 0
    vel_y: int = 0
    angle: int = 0
    direction: int = 0
    weapon: int = 0
    hook_state: int = 0
    hook_x: int = 0
    hook_y: int = 0
    attack_tick: int = 0

    def to_bytes(self) -> bytes:
        return struct.pack(
            "<12i",
            self.x,
            self.y,
            self.vel_x,
            self.vel_y,
            self.angle,
            self.direction,
            self.weapon,
            self.hook_state,
            self.hook_x,
            self.hook_y,
            self.attack_tick,
            self.tick,
        )


def _str_to_ints(text: str, count: int) -> List[int]:
    raw = text.encode("utf-8")[: count * 4 - 1]
    raw = raw + b"\x00" * (count * 4 - len(raw))
    return list(struct.unpack(f"<{count}i", raw))


def _int_diff(previous: bytes, current: bytes) -> bytes:
    prev = struct.unpack(f"<{len(previous) // 4}I", previous)
    cur = struct.unpack(f"<{len(current) // 4}I", current)
    return struct.pack(f"<{len(cur)}I", *[(c - p) & 0xFFFFFFFF for c, p in zip(cur, prev)])


def write_ghost(
    path: str,
    map_name: str,
    map_blob: bytes,
    name: str,
    frames: Sequence[GhostFrame],
    start_tick: int = 0,
    time_ms: Optional[int] = None,
    skin: str = "default",
) -> None:
    """Write a ``.gho`` file that the DDNet client can replay."""
    if not frames:
        raise ValueError("a ghost needs at least one frame")
    if time_ms is None:
        time_ms = int(len(frames) / 50.0 * 1000)

    header = bytearray()
    header += GHOST_MAGIC
    header.append(GHOST_VERSION)
    header += name.encode("utf-8")[:63].ljust(64, b"\x00")
    header += map_name.encode("utf-8")[:63].ljust(64, b"\x00")
    header += b"\x00" * 4  # CRC, unused since version 6
    header += struct.pack(">i", len(frames))
    header += struct.pack(">i", time_ms)
    header += hashlib.sha256(map_blob).digest()

    body = bytearray()
    _write_ghost_chunk(body, GHOSTDATA_TYPE_START_TICK, [start_tick])
    _write_ghost_chunk(body, GHOSTDATA_TYPE_SKIN, _ghost_skin(skin))
    _write_ghost_chunk(body, GHOSTDATA_TYPE_CHARACTER, [frame.to_bytes() for frame in frames])

    with open(path, "wb") as handle:
        handle.write(bytes(header))
        handle.write(bytes(body))


def _ghost_skin(skin: str) -> List[bytes]:
    ints = _str_to_ints(skin, 6)
    return [struct.pack("<9i", *ints, 0, 0, 0)]


def _write_ghost_chunk(out: bytearray, data_type: int, items: Sequence) -> None:
    """Chunks store at most ``NUM_ITEMS_PER_CHUNK`` items, delta encoded."""
    def emit(chunk_items: List[bytes]) -> None:
        payload = bytearray()
        previous = None
        for item in chunk_items:
            if previous is not None:
                payload += _int_diff(previous, item)
            else:
                payload += item
            previous = item
        packed = compress_ints(list(struct.unpack(f"<{len(payload) // 4}i", payload)))
        compressed = huffman_compress(packed)
        header = bytes(
            (
                data_type & 0xFF,
                len(chunk_items) & 0xFF,
                (len(compressed) >> 8) & 0xFF,
                len(compressed) & 0xFF,
            )
        )
        out.extend(header)
        out.extend(compressed)

    if isinstance(items[0], int):
        items = [struct.pack("<i", item) for item in items]
    chunk: List[bytes] = []
    for item in items:
        chunk.append(item)
        if len(chunk) == NUM_ITEMS_PER_CHUNK:
            emit(chunk)
            chunk = []
    if chunk:
        emit(chunk)
