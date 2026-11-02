"""Declared external shared-library paths of an installed executable."""
from __future__ import annotations

import platform
import re
from collections.abc import Callable, Sequence


def paths(executable: str | Sequence[str], inspect: Callable[[list[str]], tuple[int, str]]) -> list[str]:
    """Return non-system absolute install names reported by the platform loader inspector."""
    selected = [executable] if isinstance(executable, str) else list(executable)
    query = ["otool", "-L", *selected] if platform.system() == "Darwin" else ["ldd", *selected]
    _code, out = inspect(query)
    found = re.findall(r"(?:=>\s+|^\s+)(/\S+)", out, re.M)
    return [path for path in found if not path.startswith(("/usr/lib", "/System", "/lib"))]
