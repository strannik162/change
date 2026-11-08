"""Unit tests for pylot.

Run with ``python -m unittest discover -s pylot/tests -t pylot`` from the
repository root, or ``python -m pytest pylot/tests`` if pytest is available.
The tests are intentionally dependency free (stdlib ``unittest`` only).
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pylot import examples  # noqa: E402
from pylot.formats import (  # noqa: E402
    GhostFrame,
    huffman_compress,
    huffman_decompress,
    pack_int,
    unpack_int,
    write_ghost,
)
from pylot.map import (  # noqa: E402
    Map,
    MapWriter,
    TILE_FINISH,
    TILE_SOLID,
    TILE_START,
    TILE_DEATH,
    CANTMOVE_DOWN,
)
from pylot.nav import build_macro_catalogue, is_standable, tile_of  # noqa: E402
from pylot.physics import (  # noqa: E402
    Collision,
    Input,
    Player,
    PHYSICAL_SIZE,
    SERVER_TICK_SPEED,
)

MAPS_DIR = os.path.join(ROOT, "maps")


def flat_map(path: str, width: int = 50) -> None:
    writer = MapWriter(width, 24)
    for x in range(width):
        for y in range(18, 24):
            writer.set_game(x, y, TILE_SOLID)
    writer.set_game(2, 17, TILE_START)
    writer.set_game(width - 4, 17, TILE_FINISH)
    with open(path, "wb") as handle:
        handle.write(writer.to_bytes())


class MapFormatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "flat.map")
        flat_map(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_roundtrip(self) -> None:
        map_ = Map(self.path)
        self.assertEqual((map_.width, map_.height), (50, 24))
        self.assertEqual(map_.game_layer.index_at(2, 17), TILE_START)
        self.assertEqual(map_.game_layer.index_at(46, 17), TILE_FINISH)
        self.assertTrue(map_.solid(5, 18))
        self.assertFalse(map_.solid(5, 17))
        self.assertEqual(len(map_.start_positions()), 1)
        self.assertEqual(len(map_.finish_positions()), 1)

    def test_tile_layer_header_size(self) -> None:
        map_ = Map(self.path)
        layer_items = list(map_.datafile.items_of_type(5))
        self.assertTrue(layer_items)
        for item in layer_items:
            self.assertEqual(len(item.data), 92)

    def test_reads_shipped_ddnet_maps(self) -> None:
        repo_map = os.path.join(os.path.dirname(ROOT), "data", "maps", "Tutorial.map")
        if not os.path.exists(repo_map):
            self.skipTest("DDNet data directory not present")
        map_ = Map(repo_map)
        self.assertGreater(map_.width, 100)
        self.assertTrue(map_.start_positions())
        self.assertTrue(map_.finish_positions())


class PhysicsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "flat.map")
        flat_map(cls.path)
        cls.map = Map(cls.path)
        cls.collision = Collision(cls.map)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def make_player(self) -> Player:
        return Player(self.collision, pos=(6 * 32 + 16.0, 17 * 32 + 16.0))

    def drop(self, player: Player, ticks: int = 40) -> None:
        idle = Input()
        for _ in range(ticks):
            player.tick(idle)

    def test_lands_on_floor(self) -> None:
        player = self.make_player()
        self.drop(player)
        # the tee box is 28px, resting on top of row 18 (y = 576); the last
        # move step before the collision leaves the centre at 561..562
        self.assertGreaterEqual(player.pos[1], 576.0 - 15.0)
        self.assertLessEqual(player.pos[1], 576.0 - 14.0)
        self.assertTrue(player.is_grounded())

    def test_walk_speed_matches_ddnet(self) -> None:
        player = self.make_player()
        self.drop(player)
        for _ in range(40):
            player.tick(Input(direction=1))
        # ground_control_speed is reached after a few ticks
        self.assertAlmostEqual(player.vel[0], 10.0, places=3)

    def test_stopper_blocks_movement(self) -> None:
        writer = MapWriter(50, 24)
        for x in range(50):
            for y in range(18, 24):
                writer.set_game(x, y, TILE_SOLID)
        # one-way stopper at the bottom of the pit facing up
        writer.set_game(10, 17, 60, flags=0)  # TILE_STOP rotation 0 blocks down
        path = os.path.join(self.tmp.name, "stopper.map")
        with open(path, "wb") as handle:
            handle.write(writer.to_bytes())
        map_ = Map(path)
        collision = Collision(map_)
        restrictions = collision.get_move_restrictions(10 * 32 + 16.0, 17 * 32 + 16.0 - 20)
        self.assertTrue(restrictions & CANTMOVE_DOWN)
        del collision

    def test_jump_height(self) -> None:
        player = self.make_player()
        self.drop(player)
        start = player.pos[1]
        lowest = start
        for tick in range(45):
            player.tick(Input(jump=1 if tick < 3 else 0))
            lowest = min(lowest, player.pos[1])
        # a full ground jump is a bit less than 5 tiles high
        self.assertGreater(start - lowest, 4.5 * 32)
        self.assertLess(start - lowest, 6.0 * 32)

    def test_death_tile_kills(self) -> None:
        writer = MapWriter(30, 24)
        for x in range(30):
            for y in range(18, 24):
                writer.set_game(x, y, TILE_DEATH)
        path = os.path.join(self.tmp.name, "dead.map")
        with open(path, "wb") as handle:
            handle.write(writer.to_bytes())
        player = Player(Collision(Map(path)), pos=(16 * 32 + 16.0, 17 * 32 + 16.0))
        for _ in range(60):
            player.tick(Input())
        self.assertTrue(player.dead)

    def test_snapshot_restore(self) -> None:
        player = self.make_player()
        self.drop(player)
        for _ in range(5):
            player.tick(Input(direction=1))
        state = player.snapshot()
        for _ in range(10):
            player.tick(Input(direction=1, jump=1))
        player.restore(state)
        expected = player.clone()
        player.tick(Input(direction=1))
        expected.tick(Input(direction=1))
        self.assertEqual(player.pos, expected.pos)
        self.assertEqual(player.vel, expected.vel)


class FormatTest(unittest.TestCase):
    def test_huffman_roundtrip(self) -> None:
        for payload in (b"", b"a", b"hello world", bytes(range(256)) * 3):
            compressed = huffman_compress(payload)
            self.assertEqual(huffman_decompress(compressed, len(payload)), payload)

    def test_int_packing(self) -> None:
        values = [0, 1, -1, 63, 64, -64, 100000, -100000, 2 ** 30]
        blob = b"".join(pack_int(value) for value in values)
        offset = 0
        decoded = []
        for _ in values:
            value, offset = unpack_int(blob, offset)
            decoded.append(value)
        self.assertEqual(decoded, values)

    def test_ghost_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "run.gho")
            frames = [GhostFrame(tick=i, x=100 + i, y=200) for i in range(5)]
            write_ghost(path, "flat", b"mapdata", "pylot", frames, time_ms=1000)
            with open(path, "rb") as handle:
                blob = handle.read()
            self.assertEqual(blob[:8], b"TWGHOST\x00")
            self.assertEqual(blob[8], 6)
            self.assertEqual(blob[9:73].rstrip(b"\x00"), b"pylot")
            ticks = struct.unpack(">i", blob[9 + 64 + 64 + 4 : 9 + 64 + 64 + 8])[0]
            self.assertEqual(ticks, 5)


class BotTest(unittest.TestCase):
    def test_macro_catalogue_is_reproducible(self) -> None:
        first = build_macro_catalogue()
        second = build_macro_catalogue()
        self.assertEqual([m.name for m in first], [m.name for m in second])
        self.assertGreater(len(first), 20)

    def test_examples_are_solved(self) -> None:
        from pylot.planner import run_bot, verify_recording

        with tempfile.TemporaryDirectory() as tmp:
            paths = examples.write_all(tmp)
            self.assertEqual(len(paths), len(examples.BUILDERS))
            for path in paths:
                recording, map_, player = run_bot(path, verbose=False, max_plan_expansions=8000)
                name = os.path.basename(path)
                self.assertIsNotNone(recording, f"no route found for {name}")
                assert recording is not None
                self.assertTrue(player.finished, f"{name} did not reach the finish")
                self.assertTrue(
                    verify_recording(map_, recording.initial_pos, recording),
                    f"{name} recording does not replay",
                )

    def test_standability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "flat.map")
            flat_map(path)
            map_ = Map(path)
            player = Player(Collision(map_), pos=(6 * 32 + 16.0, 17 * 32 + 16.0))
            self.assertTrue(is_standable(player, 6, 17))
            self.assertFalse(is_standable(player, 6, 2))
            self.assertEqual(tile_of(player.pos), (6, 17))


if __name__ == "__main__":
    unittest.main()
