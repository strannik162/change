"""Rendering helpers: a tiny ASCII view of a map or of a recorded run.

The renderer is what makes the bot *watchable* without the game: every tick of
a run can be drawn as text, which is also how the unit tests check that the
tee really moved from the start to the finish.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Sequence, Tuple

from .map import (
    TILE_CP,
    TILE_CP_F,
    TILE_DEATH,
    TILE_FINISH,
    TILE_FREEZE,
    TILE_JUMP,
    TILE_LFREEZE,
    TILE_NOHOOK,
    TILE_SOLID,
    TILE_START,
    TILE_WALLJUMP,
    Map,
)

SYMBOLS = {
    TILE_SOLID: "#",
    TILE_DEATH: "!",
    TILE_NOHOOK: "N",
    TILE_FREEZE: "F",
    TILE_LFREEZE: "f",
    TILE_START: "S",
    TILE_FINISH: "E",
    TILE_JUMP: "J",
    TILE_WALLJUMP: "W",
    TILE_CP: "C",
    TILE_CP_F: "c",
}


def render_map(
    map_: Map,
    x0: int = 0,
    y0: int = 0,
    width: Optional[int] = None,
    height: Optional[int] = None,
    pos: Optional[Sequence[float]] = None,
    path: Optional[Iterable[Sequence[float]]] = None,
) -> str:
    """Draw a rectangle of the map, optionally with the tee and its path."""
    width = width or map_.width
    height = height or map_.height
    x1 = min(map_.width, x0 + width)
    y1 = min(map_.height, y0 + height)
    grid: List[List[str]] = []
    for y in range(y0, y1):
        row = []
        for x in range(x0, x1):
            index = map_.game_layer.index_at(x, y) if map_.game_layer else 0
            row.append(SYMBOLS.get(index, "." if index == 0 else "?"))
        grid.append(row)

    def plot(px: float, py: float, char: str) -> None:
        tx = int(px) // 32 - x0
        ty = int(py) // 32 - y0
        if 0 <= ty < len(grid) and 0 <= tx < len(grid[ty]):
            grid[ty][tx] = char

    if path is not None:
        for point in path:
            plot(point[0], point[1], "~")
    if pos is not None:
        plot(pos[0], pos[1], "@")
    return "\n".join("".join(row) for row in grid)


def render_frames(
    map_: Map,
    positions: Sequence[Sequence[float]],
    every: int = 5,
    width: int = 80,
    height: int = 30,
) -> str:
    """A flip-book of the run, handy for the docs and for debugging."""
    if not positions:
        return ""
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    x0 = max(0, int(min(xs)) // 32 - 2)
    y0 = max(0, int(min(ys)) // 32 - 2)
    frames = []
    for index in range(0, len(positions), every):
        frames.append(
            f"tick {index:5d}\n"
            + render_map(map_, x0, y0, width, height, pos=positions[index], path=positions[: index + 1])
        )
    frames.append(f"tick {len(positions) - 1:5d} (end)")
    frames.append(render_map(map_, x0, y0, width, height, pos=positions[-1], path=positions))
    return "\n\n".join(frames)


def write_replay(path: str, map_: Map, positions: Sequence[Sequence[float]], every: int = 2) -> None:
    """Write an ASCII animation that can be replayed in a terminal."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_frames(map_, positions, every=every))


def summarise(map_: Map, positions: Sequence[Sequence[float]], finished: bool) -> str:
    if not positions:
        return "no movement recorded"
    start = positions[0]
    end = positions[-1]
    distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    return (
        f"start ({start[0]:.0f}, {start[1]:.0f}) -> end ({end[0]:.0f}, {end[1]:.0f}), "
        f"{distance:.0f} px travelled, {len(positions)} ticks, finished={finished}"
    )
