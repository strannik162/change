"""``pylot`` - a DDNet map completing bot written in pure Python.

Usage::

    python -m pylot.cli run maps/pit.map                 # complete a map
    python -m pylot.cli run map.map --ghost out.gho      # also record a ghost
    python -m pylot.cli info map.map                     # map overview
    python -m pylot.cli draw map.map --x 40 --y 160      # ascii view
    python -m pylot.cli maps                             # (re)create example maps
    python -m pylot.cli selftest                         # physics sanity checks
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
from typing import List, Optional

from . import examples
from .formats import GhostFrame, write_ghost
from .map import Map
from .nav import NavGraph, goal_tiles, is_standable, tile_of
from .physics import Collision, Input, Player, SERVER_TICK_SPEED
from .planner import Recording, run_bot, verify_recording
from .render import render_frames, render_map, summarise


def _player_for(map_: Map) -> Player:
    collision = Collision(map_)
    starts = map_.start_positions() or map_.spawn_positions()
    if not starts:
        raise SystemExit("map has no start and no spawn tile")
    return Player(collision, pos=starts[0])


def cmd_run(args: argparse.Namespace) -> int:
    started = time.time()
    recording, map_, player = run_bot(
        args.map,
        max_plan_expansions=args.expansions,
        max_ticks=int(args.max_seconds * SERVER_TICK_SPEED),
        verbose=not args.quiet,
        best_effort=args.best_effort,
        leg_time_budget=args.leg_time,
    )
    if recording is None:
        print("the bot could not find a route", file=sys.stderr)
        return 1

    ok = verify_recording(map_, recording.initial_pos, recording)
    print(summarise(map_, recording.positions, player.finished))
    print(f"verification of the recorded inputs: {'ok' if ok else 'FAILED'}")

    if args.frames:
        print()
        print(render_frames(map_, recording.positions, every=args.frames))

    if args.ghost:
        frames = [
            GhostFrame(
                tick=index,
                x=int(round(pos[0])),
                y=int(round(pos[1])),
                vel_x=int(round(vel[0] * 256)),
                vel_y=int(round(vel[1] * 256)),
                hook_state=0,
                direction=0,
            )
            for index, (pos, vel) in enumerate(zip(recording.positions, recording.velocities))
        ]
        write_ghost(
            args.ghost,
            os.path.splitext(os.path.basename(args.map))[0],
            map_.blob,
            args.name,
            frames,
            time_ms=int(recording.time * 1000),
        )
        print(f"ghost written to {args.ghost}")

    if args.replay:
        with open(args.replay, "w", encoding="utf-8") as handle:
            handle.write(render_frames(map_, recording.positions, every=2))
        print(f"ascii replay written to {args.replay}")

    print(f"wall clock: {time.time() - started:.1f}s")
    return 0 if (ok and player.finished) else 1


def cmd_info(args: argparse.Namespace) -> int:
    map_ = Map(args.map)
    print(f"map       : {map_} ({map_.width} x {map_.height} tiles)")
    if map_.map_info:
        for key, value in map_.map_info.items():
            if value:
                print(f"{key:10s}: {value}")
    starts = map_.start_positions()
    finishes = map_.finish_positions()
    print(f"start     : {len(starts)} tile(s) {starts[:3]}")
    print(f"finish    : {len(finishes)} tile(s) {finishes[:3]}")
    player = _player_for(map_) if starts or map_.spawn_positions() else None
    if player:
        goals = goal_tiles(player)
        print(f"standable : {len(goals)} finish-adjacent tile(s)")
    print(f"tunings   : {len(map_.tuning_zones)} custom zone(s)")
    return 0


def cmd_draw(args: argparse.Namespace) -> int:
    map_ = Map(args.map)
    print(render_map(map_, args.x, args.y, args.width, args.height))
    return 0


def cmd_maps(args: argparse.Namespace) -> int:
    target = args.dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps")
    for path in examples.write_all(target):
        print(path)
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """A couple of physics invariants; also run by the test suite."""
    target = args.dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps")
    examples.write_all(target)
    failures = 0
    for path in sorted(glob.glob(os.path.join(target, "*.map"))):
        name = os.path.splitext(os.path.basename(path))[0]
        recording, map_, player = run_bot(path, verbose=False, max_plan_expansions=args.expansions)
        if recording is None:
            print(f"[FAIL] {name:8s} no route")
            failures += 1
            continue
        ok = verify_recording(map_, recording.initial_pos, recording) and player.finished
        print(
            f"[{'ok' if ok else 'FAIL'}] {name:8s} {len(recording):4d} ticks "
            f"({recording.time:5.2f}s) finished={player.finished}"
        )
        failures += 0 if ok else 1
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pylot", description="DDNet map completing bot")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="complete a map")
    run.add_argument("map")
    run.add_argument("--ghost", help="write a .gho ghost of the run for the DDNet client")
    run.add_argument("--replay", help="write an ascii replay of the run")
    run.add_argument("--frames", type=int, default=0, help="print every n-th frame as ascii")
    run.add_argument("--name", default="pylot", help="name stored in the ghost file")
    run.add_argument("--expansions", type=int, default=8000, help="A* expansion budget")
    run.add_argument("--max-seconds", type=float, default=600.0, help="give up after this much game time")
    run.add_argument("--quiet", action="store_true")
    run.add_argument(
        "--best-effort",
        action="store_true",
        help="on big maps, make progress with weighted A* legs instead of failing",
    )
    run.add_argument(
        "--leg-time",
        type=float,
        default=60.0,
        help="time budget per planning leg in --best-effort mode (seconds)",
    )
    run.set_defaults(func=cmd_run)

    info = sub.add_parser("info", help="show map information")
    info.add_argument("map")
    info.set_defaults(func=cmd_info)

    draw = sub.add_parser("draw", help="ascii view of a map")
    draw.add_argument("map")
    draw.add_argument("--x", type=int, default=0)
    draw.add_argument("--y", type=int, default=0)
    draw.add_argument("--width", type=int, default=80)
    draw.add_argument("--height", type=int, default=30)
    draw.set_defaults(func=cmd_draw)

    maps = sub.add_parser("maps", help="create the example maps")
    maps.add_argument("--dir")
    maps.set_defaults(func=cmd_maps)

    selftest = sub.add_parser("selftest", help="run the bot on all example maps")
    selftest.add_argument("--dir")
    selftest.add_argument("--expansions", type=int, default=8000)
    selftest.set_defaults(func=cmd_selftest)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
