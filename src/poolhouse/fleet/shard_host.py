"""Whether this device takes test shards, and the Python 3.13 with pytest that runs them.

A device takes shards only in a pool in dev mode, only while nobody turned `remote-tests` off here,
and only with a Python 3.13 that imports pytest. What it says is `capability`.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from poolhouse import features
from poolhouse.home import user_home

from .wsl_startup import guest

__all__ = ["FEATURE", "PYTHON_ENV", "capability", "refusal", "resolve"]

FEATURE = "remote-tests"
PYTHON_ENV = "POOLHOUSE_SHARD_PYTHON"
WANTED = "3.13"
PROBE = "import sys, pytest; print('%d.%d' % sys.version_info[:2])"
PROBE_S = 10.0
FRESH_S = 60.0
FIX = ("run scripts/setup-wsl-dev in this device's poolhouse checkout, or point "
       f"{PYTHON_ENV} at a Python {WANTED} that has pytest")
_known: dict[str, tuple[float, str]] = {}


def probe(python: str) -> str:
    """The `major.minor` of ``python`` when it runs and imports pytest, else the empty string."""
    seen = _known.get(python)
    if seen and time.monotonic() - seen[0] < FRESH_S:
        return seen[1]
    try:
        done = subprocess.run([python, "-c", PROBE], capture_output=True, text=True, timeout=PROBE_S, check=False)
        version = done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        version = ""
    _known[python] = (time.monotonic(), version)
    return version


def candidates() -> list[str]:
    """The interpreters that may run shards, in the order they are tried; only the named one when ``PYTHON_ENV`` names one."""
    if named := os.environ.get(PYTHON_ENV):
        return [named]
    venvs = [Path(v) for v in (os.environ.get("POOLHOUSE_DEV_VENV"), os.environ.get("POOLHOUSE_WSL_VENV")) if v]
    venvs.append(user_home() / ".venvs" / "poolhouse-dev")
    inside = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    found = [str(v / inside) for v in venvs] + [sys.executable, shutil.which("python3.13") or "", shutil.which("python3") or ""]
    return list(dict.fromkeys(p for p in found if p and Path(p).is_file()))


def resolve() -> tuple[str, str]:
    """The interpreter that runs shards here and ``''``, or ``''`` and the sentence that says what to do."""
    tried = []
    for python in candidates():
        version = probe(python)
        if version == WANTED:
            return python, ""
        tried.append(f"{python} {'is ' + version if version else 'does not run pytest'}")
    return "", f"Python {WANTED} with pytest is missing on this device ({'; '.join(tried) or 'nothing found'}): {FIX}"


def refusal(mode: str, root: str = "") -> str:
    """Why this device takes no shards (``''`` when it takes them); a sentence a person can act on."""
    if mode != "dev":
        return "this device is in a pool that is not in dev mode, so it takes tests only from devices a person named"
    if features.turned_off(FEATURE, root):
        return f"tests from other devices were turned off here (`poolhouse features disable {FEATURE}`); `poolhouse features enable {FEATURE}` turns them on"
    return resolve()[1]


def platform_of(python: str = WANTED) -> dict[str, Any]:
    """This device in the words a result carries: `sys.platform`, the machine, the cpu count, and whether it is WSL."""
    return {"system": sys.platform, "machine": platform.machine(), "cpus": os.cpu_count() or 1,
            "wsl": guest(), "python": python}


def capability(mode: str, root: str, name: str, status: dict[str, Any]) -> dict[str, Any]:
    """What `GET /shards/v1/caps` answers: whether shards are taken (and why not), the platform and the free slots."""
    reason = refusal(mode, root)
    slots, free = int(status.get("slots", 1)), int(status.get("free", 0))
    return {"accepts": not reason, "reason": reason, "platform": platform_of(), "python": WANTED, "name": name, "mode": mode,
            "free": free, "most_active": slots}
