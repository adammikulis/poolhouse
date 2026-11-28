"""The RBMK-1000 control room: a toy plant, the decision loop, the web server and the 3D room, in one app.

Run from a Poolhouse checkout it uses that checkout's ``src``; copied elsewhere it uses the installed Poolhouse.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if (_SRC / "poolhouse" / "home.py").is_file() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
