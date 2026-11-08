"""High level bot: plan a route through a map and record the result.

The planner works in three steps, repeated until the tee reaches the finish:

1. **Plan** - A* on the jump/hook graph built by :mod:`pylot.nav`.
2. **Execute** - replay the macros on the *real* player state, recording every
   tick. Because the graph is built from the same physics there is normally no
   surprise, but if the tee ends up in an unexpected tile we simply re-plan
   from where it is (this also recovers from teleporters).
3. **Verify** - the recording is a plain list of inputs; re-simulating it from
   the start must reproduce the recorded positions exactly. That is a strong
   self check of the whole physics port.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .map import Map
from .nav import (
    Macro,
    NavGraph,
    Plan,
    goal_tiles,
    is_standable,
    tile_center,
    tile_of,
)
from .physics import Collision, Input, Player, SERVER_TICK_SPEED, Tuning


@dataclass
class Recording:
    """Inputs plus the resulting path; enough to reproduce the run."""

    inputs: List[Input] = field(default_factory=list)
    positions: List[Tuple[float, float]] = field(default_factory=list)
    velocities: List[Tuple[float, float]] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    #: player position *before* the first recorded tick
    initial_pos: Tuple[float, float] = (0.0, 0.0)

    def __len__(self) -> int:
        return len(self.inputs)

    @property
    def time(self) -> float:
        return len(self.inputs) / SERVER_TICK_SPEED

    def add(self, input_: Input, player: Player) -> None:
        self.inputs.append(input_)
        self.positions.append((player.pos[0], player.pos[1]))
        self.velocities.append((player.vel[0], player.vel[1]))


def make_player(map_: Map, pos: Optional[Sequence[float]] = None, tuning: Optional[Tuning] = None) -> Player:
    collision = Collision(map_)
    if pos is not None:
        start = (float(pos[0]), float(pos[1]))
    else:
        starts = map_.start_positions()
        if starts:
            start = starts[0]
        else:
            spawns = map_.spawn_positions()
            if not spawns:
                raise ValueError("map has neither start nor spawn tiles")
            start = spawns[0]
    return Player(collision, pos=start, tuning=tuning)


def find_goal_tiles(player: Player) -> List[Tuple[int, int]]:
    goals = goal_tiles(player)
    if goals:
        return goals
    # fall back to the tile of the finish itself (some maps have the finish
    # floating, the tee then simply has to touch it)
    map_ = player.world.map
    return [(x, y) for x, y in map_.find_tiles(34)]


def execute_plan(player: Player, plan: Plan, recording: Recording, verbose: bool = False) -> bool:
    """Run every macro of ``plan`` on ``player``, recording all ticks."""
    for index, macro in enumerate(plan.macros):
        for input_ in macro.inputs():
            player.tick(input_)
            recording.add(input_, player)
            if player.dead:
                if verbose:
                    print(f"  died during macro {macro.name} at {tile_of(player.pos)}")
                return False
            if player.finished:
                recording.events.append(f"finished after {len(recording)} ticks")
                return True
        # a small settle between macros keeps the state close to the graph
        idle = Input()
        for _ in range(3):
            player.tick(idle)
            recording.add(idle, player)
            if player.dead:
                return False
            if player.finished:
                recording.events.append(f"finished after {len(recording)} ticks")
                return True
    return False


def try_finish_sweep(player: Player, recording: Recording, graph: NavGraph) -> bool:
    """Try to actually cross the finish line from the current position.

    Standing *next to* the finish is enough for the graph but not for the game,
    so this brute forces a few short macros and keeps the first that touches
    the finish tile.
    """
    for macro in graph.catalogue:
        if macro.hook_ticks:
            continue
        probe = player.clone()
        for input_ in macro.inputs():
            probe.tick(input_)
            if probe.dead:
                break
            if probe.finished:
                for real_input in macro.inputs():
                    player.tick(real_input)
                    recording.add(real_input, player)
                    if player.dead or player.finished:
                        return player.finished
                return False
    return False


#: which greedy edges were already taken, per graph (avoids oscillations)
_GREEDY_USED: dict = {}
#: how often each tile was visited, so the greedy walk does not loop forever
_GREEDY_VISITS: dict = {}


def _greedy_progress(player: Player, goals, graph: "NavGraph", verbose: bool = False) -> bool:
    used = _GREEDY_USED.setdefault(id(graph), set())
    before = tile_of(player.pos)
    for attempt in range(8):
        if not greedy_step(player, goals, graph):
            return attempt > 0 and tile_of(player.pos) != before
        if player.dead or player.finished:
            return True
    if verbose:
        print("  greedy exploration moved the tee to", tile_of(player.pos))
    return tile_of(player.pos) != before


def greedy_step(player: Player, goals, graph: "NavGraph") -> bool:
    """Fallback when A* finds nothing: take the macro that gets us closest.

    This keeps the bot moving on large maps where the tile graph is too big to
    search exhaustively; it is not optimal, but it is exactly how a human
    explores an unknown map.
    """
    current = tile_of(player.pos)

    def score(tile: Tuple[int, int]) -> float:
        return min((abs(tile[0] - g[0]) + 0.35 * abs(tile[1] - g[1])) for g in goals)

    edges = graph.edges(current)
    if not edges:
        return False
    visits = _GREEDY_VISITS.setdefault(id(graph), {})
    visits[current] = visits.get(current, 0) + 1
    # ignore edges we already used from this tile: a greedy walk otherwise
    # oscillates between two tiles forever
    used = _GREEDY_USED.setdefault(id(graph), set())
    fresh = [edge for edge in edges if (current, edge.target, edge.macro.name) not in used]
    candidates = fresh or edges
    best = None
    best_score = score(current) - 0.01
    for edge in candidates:
        value = score(edge.target) + 2.5 * visits.get(edge.target, 0)
        if value < best_score:
            best_score = value
            best = edge
    if best is None:
        # local minimum: jiggle with the macro that covers the most ground
        best = max(candidates, key=lambda e: (abs(e.target[0] - current[0]), -e.cost))
        if best.target == current:
            return False
    used.add((current, best.target, best.macro.name))
    for input_ in best.macro.inputs():
        player.tick(input_)
        if player.dead or player.finished:
            return True
    return True


def run_bot(
    map_path: str,
    max_restarts: int = 6,
    max_plan_expansions: int = 3000,
    verbose: bool = True,
    max_ticks: int = 60 * 50 * 5,
    best_effort: bool = False,
    leg_time_budget: float = 60.0,
    leg_max_tiles: int = 600,
) -> Tuple[Optional[Recording], Map, Player]:
    """Plan and execute a full run. Returns ``(recording, map, player)``."""
    map_ = Map(map_path)
    player = make_player(map_)
    recording = Recording(initial_pos=(player.pos[0], player.pos[1]))
    graph: Optional[NavGraph] = None
    goals = find_goal_tiles(player)

    if verbose:
        print(f"map   : {map_} ({map_.width}x{map_.height} tiles)")
        print(f"start : {player.pos} -> tile {tile_of(player.pos)}")
        print(f"goal  : {len(goals)} tile(s), e.g. {goals[:3]}")

    restarts = 0
    replans = 0
    greedy_moves = 0
    started = time.time()
    while len(recording) < max_ticks:
        current = tile_of(player.pos)
        if player.finished:
            recording.events.append(f"finished after {len(recording)} ticks")
            break
        if tuple(current) in set(goals) and graph is not None:
            if try_finish_sweep(player, recording, graph):
                continue
            if player.dead:
                return None, map_, player
        if graph is None:
            graph = NavGraph(player, verbose=False, settle_ticks=8 if best_effort else 30)
        search_goals = [g for g in goals if tuple(g) != current] or goals
        plan = graph.plan(
            current,
            search_goals,
            max_expansions=max_plan_expansions,
            max_tiles=leg_max_tiles if best_effort else 1500,
            time_budget=leg_time_budget if best_effort else None,
            weight=1.6 if best_effort else 1.0,
            allow_partial=best_effort,
        )
        if plan is None or (best_effort and plan.tiles[-1] == current):
            if best_effort and _greedy_progress(player, goals, graph, verbose):
                if player.finished:
                    recording.events.append(f"finished after {len(recording)} ticks")
                    break
                greedy_moves += 1
                if greedy_moves > 4000:
                    return None, map_, player
                continue
            if verbose:
                print(f"no route from {current}: the bot is stuck in a dead end")
            return None, map_, player
        if verbose:
            print(
                f"plan  : {len(plan.macros)} macros from {current} "
                f"({plan.cost / SERVER_TICK_SPEED:.2f}s simulated, "
                f"{graph.expansions} expansions)"
            )
        reached = execute_plan(player, plan, recording, verbose=verbose)
        if reached:
            break
        if player.dead:
            if verbose:
                print(f"died at {tile_of(player.pos)}, restarting from the start")
            restarts += 1
            if restarts > max_restarts:
                return None, map_, player
            tick_count = len(recording)
            player = make_player(map_)
            recording = Recording(initial_pos=(player.pos[0], player.pos[1]))
            recording.events.append(f"restart {restarts} after {tick_count} ticks")
            graph = None
            continue
        replans += 1
        if replans > 400:
            if verbose:
                print("too many re-plans, giving up")
            return None, map_, player
        # we ended somewhere unexpected: continue from there
        goals = find_goal_tiles(player)
        if verbose:
            print(f"re-plan at {tile_of(player.pos)} ({replans})")

    if verbose:
        print(
            f"done  : {len(recording)} ticks ({recording.time:.2f}s), "
            f"{time.time() - started:.1f}s wall clock, "
            f"finished={player.finished}, dead={player.dead}"
        )
    return recording, map_, player


def verify_recording(map_: Map, start: Sequence[float], recording: Recording) -> bool:
    """Re-simulate the recorded inputs and compare positions."""
    player = make_player(map_, pos=start)
    for index, (input_, pos) in enumerate(zip(recording.inputs, recording.positions)):
        player.tick(input_)
        if abs(player.pos[0] - pos[0]) > 1e-6 or abs(player.pos[1] - pos[1]) > 1e-6:
            print(
                f"desync at tick {index}: simulated {player.pos} != recorded {pos}"
            )
            return False
    return True
