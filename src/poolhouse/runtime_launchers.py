"""Owned console entrypoints for selected immutable runtimes."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from poolhouse import runtime, windows_private
from poolhouse.files import writing


def owned(path: Path) -> None:
    """Verify an existing launcher belongs to the current account."""
    runtime.plain(path)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o022))
            or (os.name == "nt" and windows_private.launcher_problem(path))):
        raise OSError("runtime entrypoint must be an owned regular file")


def directory() -> Path:
    """Return the canonical Poolhouse command directory."""
    return runtime.directory().parent.parent / "bin"


def validate_entries(entries: object) -> dict[str, str]:
    """Check console script names and import targets."""
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 256:
        raise ValueError("invalid runtime console entry points")
    for name, value in entries.items():
        if (not isinstance(name, str) or not re.fullmatch(r"poolhouse(?:-[a-z0-9-]+)?", name)
                or not isinstance(value, str)
                or not re.fullmatch(r"poolhouse(?:\.[a-z_][a-z0-9_]*)+:[a-z_][a-z0-9_]*", value)):
            raise ValueError("runtime entrypoint must belong to poolhouse")
    return entries


def entrypoints(chosen: runtime.Runtime) -> dict[str, str]:
    """Read the verified distribution's console script targets."""
    script = ("import json;from importlib.metadata import distribution;"
              "print(json.dumps({p.name:p.value for p in distribution('poolhouse').entry_points "
              "if p.group=='console_scripts'}))")
    done = subprocess.run([str(chosen.python), "-I", "-c", script], capture_output=True,
                          text=True, timeout=30, env=runtime.environment())
    if done.returncode or len(done.stdout) > 65536:
        raise OSError("the selected runtime does not list its console entry points")
    entries = json.loads(done.stdout)
    return validate_entries(entries)


def install(directory: Path, chosen: runtime.Runtime | None = None, *, follow_selection: bool = False) -> list[Path]:
    """Replace the selected distribution's owned console entrypoints atomically."""
    chosen = runtime.verify(chosen) if chosen is not None else runtime.selected()
    if chosen is None:
        raise OSError("select an immutable runtime before installing its launchers")
    runtime.confine_tests(directory)
    runtime.plain(directory)
    if (not directory.is_dir() or (os.name != "nt" and
            (directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o022))):
        raise OSError("launcher directory must belong to the current account")
    entries = entrypoints(chosen)
    targets = []
    for name, value in entries.items():
        target = directory / (name + ".exe" if os.name == "nt" else name)
        if os.name == "nt":
            owned(chosen.python.parent / target.name)
        if target.exists() or target.is_symlink():
            owned(target)
        targets.append((target, value.split(":")))
    for target, (module, function) in targets:
        if os.name == "nt":
            with writing(target) as temporary:
                shutil.copyfile(chosen.python.parent / target.name, temporary)
                runtime.protect(temporary)
        else:
            runtime.write_launcher(target, module, function, chosen, follow_selection=follow_selection)
    return [target for target, _ in targets]
