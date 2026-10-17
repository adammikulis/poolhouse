"""Importing this module puts the checkout's ``src`` first on the path, so a script run on its own finds ``poolhouse``."""
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if sys.path[:1] != [SRC]:
    sys.path.insert(0, SRC)
