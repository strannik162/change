# pylot — a DDNet bot that completes maps

`pylot` is a **map completing bot for DDNet (DDRaceNetwork)** written in pure
Python 3, with no third party dependencies. It reads a `.map` file, simulates
the *real* DDNet tee physics offline, searches for a route with A\* over a
jump/hook graph, and then plays the route back tick by tick until the tee
touches the finish line.

```
$ python -m pylot.cli run maps/pit.map
map   : <Map maps/pit.map 60x24> (60x24 tiles)
start : [112.0, 560.0] -> tile (3, 17)
goal  : 9 tile(s), e.g. [(50, 17), (51, 17), (49, 17)]
plan  : 4 macros from (3, 17) (4.28s simulated, 312 expansions)
done  : 184 ticks (3.68s), 0.5s wall clock, finished=True, dead=False
start (114, 561) -> end (1592, 561), 1478 px travelled, 184 ticks, finished=True
verification of the recorded inputs: ok
```

## How it works

| step | module | what happens |
| --- | --- | --- |
| 1 | `pylot.map` | The datafile is parsed: game/front/tele/speedup/switch/tune layers, tunings of the map, start and finish tiles. |
| 2 | `pylot.physics` | A port of `CCharacterCore::Tick`, `CCharacterCore::Move`, `CCollision::MoveBox` and the DDRace tile handling. One tick = 1/50 s, exactly like the server. |
| 3 | `pylot.nav` | A catalogue of *macros* (walk, jump, double jump, hook in 12 directions, chained hooks for climbing walls). Expanding a tile runs every macro through the physics and records where the tee ends up standing. |
| 4 | `pylot.planner` | A\* over the tiles, then the macro sequence is executed on the live state while every tick is recorded, and finally the recording is re-simulated from the start to prove it is exact. |
| 5 | `pylot.formats` | Optional `.gho` ghost export, so the run can be watched inside the DDNet client. |

Because steps 3 and 4 use the *same* simulator, a plan is not a guess: the bot
knows before it starts that every jump and hook will work. The verification
step at the end re-runs the recorded inputs against a fresh tee and compares
positions tick by tick.

### The physics port

The port follows the C++ code closely, including the details that make DDNet
what it is:

* `SaturatedAdd`/`Friction` based ground and air control,
* `VelocityRamp` (so high speed actually slows you down),
* `MoveBox` with its sub-stepping, elasticities and corner case handling,
* the three jump bits (`m_Jumped`) and the DDRace jump rules (`setjumps 0/1`,
  endless jumps, refill jumps, wall jumps),
* hook states (`IDLE`, `FLYING`, `GRABBED`, `RETRACTED`), `hook_drag_accel`,
  the `0.3` vertical dampening and the `46 px` minimum drag distance,
* speedup tiles, stoppers (`TILE_STOP/STOPS/STOPA`), tune zones,
  teleporters, checkpoints and death tiles.

The unit tests pin the most important invariants (`pylot/tests`): walking
speed, jump height, landing position, stoppers, death tiles, snapshot/restore
equality and the ghost container format.

## Install / run

No dependencies, Python 3.9+:

```sh
cd pylot
python -m pylot.cli maps                 # generate the example maps
python -m pylot.cli run maps/pit.map     # complete a map
python -m pylot.cli selftest             # complete all example maps
python -m unittest discover -s tests -t .   # test suite
```

To run a real DDNet map from the game (for example one of the maps that ship
with the source tree):

```sh
python -m pylot.cli run ../data/maps/Gold\ Mine.map --expansions 20000
python -m pylot.cli info ../data/maps/Tutorial.map
```

### Big maps: best-effort mode

A\* over tiles does not scale to a 200x150 map with thousands of landing spots
in one go, so the bot also has a best-effort mode: it plans a leg with
weighted A\* under a time budget, executes it, re-plans, and falls back to a
greedy exploration step if a leg cannot be planned at all.

```sh
python -m pylot.cli run ../data/maps/coverage.map --best-effort --leg-time 8
```

`coverage.map` (shipped with DDNet, 50x50 tiles, several hook-only sections) is
completed and verified this way:

```
$ python -m pylot.cli run ../data/maps/coverage.map --best-effort --leg-time 8 --max-seconds 400
plan  : 3 macros from (47, 2) (2.12s simulated, 4788 expansions)
done  : 57 ticks (1.14s), 10.0s wall clock, finished=True, dead=False
verification of the recorded inputs: ok
```

On the large official maps (for example `Gold Mine.map`, 211x137 tiles) the
bot currently gets part way and then reports that it is stuck: the tile graph
and the macro catalogue are simply not rich enough for every trick those maps
need. Adding macros (hammer hits, grenade boosts, telegun) is the intended way
to extend it - the physics underneath already supports weapons and tunings.

### Recording a ghost you can watch in the game

```sh
python -m pylot.cli run maps/pit.map --ghost ~/.teeworlds/ghosts/pit_pylot.gho
```

The file is a regular DDNet ghost (`TWGHOST`, version 6, Huffman + variable
int encoded chunks). Drop it into your DDNet `ghosts` folder next to the map
and stand on the map's start line: the client then renders the bot's tee as a
ghost racing you. The encoder was verified against the C++
`CHuffman`/`CVariableInt` implementation (see `python -m pylot.cli selftest`).

## Example maps

`python -m pylot.cli maps` writes five small maps that exercise every move:

| map | what it needs |
| --- | --- |
| `walk.map` | plain walking |
| `pit.map` | jumping over a deadly pit |
| `stairs.map` | two consecutive ledges |
| `hook.map` | a wall that can only be passed with the hook |
| `tower.map` | alternating jumps and hooks |

All five are solved and verified by the test suite.

## Command line

```
python -m pylot.cli run <map> [--ghost FILE] [--replay FILE] [--frames N]
                             [--expansions N] [--max-seconds N] [--quiet]
python -m pylot.cli info <map>       # layers, start/finish, tunings
python -m pylot.cli draw <map> [--x --y --width --height]   # ascii view
python -m pylot.cli maps [--dir D]   # (re)create the example maps
python -m pylot.cli selftest         # run the bot on all example maps
```

`--frames N` prints the run as ASCII, which is the quickest way to see what
the bot actually did:

```
....~~.~~..........
...~.....~.........
..~~.....~.........
..~.......~........
..~~~~~~~~~~~~~~~~~~~~@EEE.......
#########################
```

## Library use

```python
from pylot import Map, Collision, Player, Input, run_bot

map_ = Map("maps/pit.map")
player = Player(Collision(map_), pos=map_.start_positions()[0])
for _ in range(50):
    player.tick(Input(direction=1))       # 1 second of running right
print(player.pos, player.vel, player.is_grounded())

recording, map_, player = run_bot("maps/pit.map")   # full run
print(len(recording), recording.time, player.finished)
```

## Limitations

* The bot is a *race* bot: it ignores weapons except for the hook, so maps
  that require shooting a switch, throwing a grenade, catching a bullet or
  cooperating with another tee are out of scope. The physics core already
  knows about tunings and weapons, so adding macros for them is the natural
  next step.
* The planner is a tile graph. Maps whose solution needs a very specific
  non-obvious move (or a very long chain of them) may exceed the expansion
  budget; the budget is a command line option.
* No networking: the bot does not log into a server, it computes the inputs
  offline. Sending them to a server would mean speaking the DDNet netprotocol
  (the input format is already identical, `pylot.physics.Input` maps one to
  one to `CNetObj_PlayerInput`).

## Where this lives in the repository

`pylot/` is additive and self contained; nothing in `src/` was modified, so
the C++ game builds exactly like upstream DDNet.
