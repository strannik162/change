"""pylot - a DDNet (DDRaceNetwork) map completing bot in pure Python.

The package contains:

``map``      reading/writing of ``.map`` datafiles (physics layers, tunings)
``physics``  a port of the DDNet tee physics, so moves can be simulated offline
``nav``      macro moves and the jump/hook graph the planner searches
``planner``  A* planning + execution + verification of a full run
``formats``  ``.gho`` ghost files, so a run can be watched in the DDNet client
``render``   ascii rendering of maps and runs
"""

__version__ = "1.0.0"

from .map import Map, MapError  # noqa: F401
from .physics import Collision, Input, Player  # noqa: F401
from .planner import Recording, run_bot, verify_recording  # noqa: F401

__all__ = [
    "Map",
    "MapError",
    "Collision",
    "Input",
    "Player",
    "Recording",
    "run_bot",
    "verify_recording",
    "__version__",
]
