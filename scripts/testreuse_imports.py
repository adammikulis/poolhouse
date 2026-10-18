"""Import facts of exact source bytes, kept in one file that every checkout of a repository shares."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path

KEEP_SECONDS = 14 * 86400


def version(*sources: Path) -> str:
    """A digest of the code that produces the facts, so an edit to it drops every stored fact."""
    return hashlib.sha256(b"".join(path.read_bytes() for path in sources)).hexdigest()[:16]


class ImportFacts:
    """Names and dynamic-import flags by the digest of (analysis version, module context, source bytes)."""

    def __init__(self, path: Path, analysis: str) -> None:
        self.path, self.analysis = path, analysis
        self.rows: dict[str, list] = {}
        self.used: set[str] = set()
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("analysis") == analysis and isinstance(loaded.get("rows"), dict):
                self.rows = loaded["rows"]

    def key(self, source: bytes, module: str, is_package: bool) -> str:
        """The digest that names these exact bytes in this module context."""
        head = f"{self.analysis}\0{module}\0{int(is_package)}\0".encode()
        return hashlib.sha256(head + source).hexdigest()

    def get(self, key: str) -> tuple[frozenset[str], bool] | None:
        """The stored facts under ``key``, or None."""
        row = self.rows.get(key)
        if not (isinstance(row, list) and len(row) == 3 and isinstance(row[0], list)):
            return None
        self.used.add(key)
        return frozenset(str(name) for name in row[0]), bool(row[1])

    def put(self, key: str, names: frozenset[str], dynamic: bool) -> None:
        """Remember the facts under ``key``."""
        self.rows[key] = [sorted(names), dynamic, time.time()]
        self.used.add(key)

    def save(self) -> None:
        """Write the facts used in this run and those used within the keep window, merged with the file's."""
        if not self.used:
            return
        now = time.time()
        rows = {}
        with contextlib.suppress(OSError, ValueError):
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("analysis") == self.analysis and isinstance(current.get("rows"), dict):
                rows.update(current["rows"])
        for key in self.used:
            if key in self.rows:
                rows[key] = [self.rows[key][0], self.rows[key][1], now]
        rows = {k: v for k, v in rows.items() if isinstance(v, list) and len(v) == 3 and now - v[2] < KEEP_SECONDS}
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"analysis": self.analysis, "rows": rows}), encoding="utf-8")
        temporary.replace(self.path)
