"""Generator for the small example maps that ship with pylot.

They are deliberately tiny (60x24 tiles) so the planner can solve them in a
couple of seconds while still exercising every move the bot knows: walking,
jumping over pits, hooking up walls, dropping down and finishing.

Run ``python -m pylot.cli maps`` to (re)create them in ``pylot/maps``.
"""

from __future__ import annotations

import os
from typing import List, Sequence, Tuple

from .map import MapWriter, TILE_DEATH, TILE_FINISH, TILE_NOHOOK, TILE_SOLID, TILE_START

WIDTH = 60
HEIGHT = 24
FLOOR = 18


def _box(w: MapWriter, x0: int, y0: int, x1: int, y1: int, tile: int = TILE_SOLID) -> None:
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            w.set_game(x, y, tile)


def _start(w: MapWriter, x: int, y: int = FLOOR - 1) -> None:
    w.set_game(x, y, TILE_START)


def _finish(w: MapWriter, x0: int, x1: int, y: int = FLOOR - 1) -> None:
    for x in range(x0, x1 + 1):
        w.set_game(x, y, TILE_FINISH)


def _ground(w: MapWriter, gaps: Sequence[Tuple[int, int]] = ()) -> None:
    for x in range(WIDTH):
        if any(a <= x <= b for a, b in gaps):
            continue
        for y in range(FLOOR, HEIGHT):
            w.set_game(x, y, TILE_SOLID)


def map_walk() -> MapWriter:
    """Straight line: just walk to the right and cross the finish."""
    w = MapWriter(WIDTH, HEIGHT)
    _ground(w)
    _start(w, 3)
    _finish(w, 50, 52)
    return w


def map_pit() -> MapWriter:
    """A 4 tile wide pit that has to be jumped over."""
    w = MapWriter(WIDTH, HEIGHT)
    _ground(w, gaps=[(20, 23)])
    _box(w, 0, FLOOR + 1, 19, HEIGHT - 1)
    _box(w, 24, FLOOR + 1, WIDTH - 1, HEIGHT - 1)
    # make the pit deadly so the bot cannot simply fall and climb out
    _box(w, 20, HEIGHT - 1, 23, HEIGHT - 1, TILE_DEATH)
    _start(w, 3)
    _finish(w, 50, 52)
    return w


def map_stairs() -> MapWriter:
    """Two small ledges; needs two jumps."""
    w = MapWriter(WIDTH, HEIGHT)
    _ground(w)
    _box(w, 16, FLOOR - 1, 22, FLOOR - 1)
    _box(w, 16, FLOOR - 2, 22, FLOOR - 2)
    _box(w, 30, FLOOR - 1, 34, FLOOR - 1)
    _box(w, 30, FLOOR - 2, 34, FLOOR - 2)
    _box(w, 30, FLOOR - 3, 34, FLOOR - 3)
    _start(w, 3)
    _finish(w, 50, 52, FLOOR - 4)
    _box(w, 47, FLOOR - 3, 55, FLOOR - 3)
    return w


def map_hook() -> MapWriter:
    """A tall wall that can only be passed with the hook."""
    w = MapWriter(WIDTH, HEIGHT)
    _ground(w)
    # solid block up to the ceiling in the middle, with a platform on top of it
    # that the tee has to reach with the hook
    _box(w, 26, 0, 30, FLOOR - 6)
    _box(w, 26, FLOOR - 6, 34, FLOOR - 6)
    _box(w, 30, FLOOR - 5, 50, FLOOR - 5)  # walkway behind the wall
    _start(w, 3)
    _finish(w, 44, 46, FLOOR - 6)
    return w


def map_tower() -> MapWriter:
    """Alternating ledges: jump, hook, jump, hook."""
    w = MapWriter(WIDTH, HEIGHT)
    _ground(w, gaps=[(30, 33)])
    _box(w, 0, FLOOR + 1, 29, HEIGHT - 1)
    _box(w, 34, FLOOR + 1, WIDTH - 1, HEIGHT - 1)
    for index, (x0, x1, y) in enumerate(
        ((10, 16, FLOOR - 3), (19, 25, FLOOR - 6), (10, 16, FLOOR - 9), (40, 46, FLOOR - 3))
    ):
        _box(w, x0, y, x1, y)
    _start(w, 2)
    _box(w, 47, FLOOR - 4, 55, FLOOR - 4)
    _finish(w, 48, 50, FLOOR - 5)
    return w


BUILDERS = {
    "walk": map_walk,
    "pit": map_pit,
    "stairs": map_stairs,
    "hook": map_hook,
    "tower": map_tower,
}


def write_all(target_dir: str) -> List[str]:
    os.makedirs(target_dir, exist_ok=True)
    written = []
    for name, builder in BUILDERS.items():
        path = os.path.join(target_dir, f"{name}.map")
        with open(path, "wb") as handle:
            handle.write(builder().to_bytes())
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover - manual use
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in write_all(os.path.join(here, "maps")):
        print(path)
