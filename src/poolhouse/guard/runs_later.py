"""Which files are of the kind that runs later, by their path and their shape. It informs and never refuses.

Publication names such files among the ones it applied (docs/design-isolated-launcher.md, 4.4), and the hooks (`guard.autoexec`) ask the
same of a write, with path facts alone.
"""

from __future__ import annotations

import posixpath
import unicodedata
from functools import cache

from poolhouse.contracts.loader import ContractError, load
from poolhouse.guard import ecosystems, harnesses

__all__ = ["HEAD_BYTES", "HIDDEN_FILE", "HIDDEN_FOLDER", "harness_names", "registry", "why"]

HIDDEN_FOLDER = "is in a hidden folder, which tools run or read without being asked"
HIDDEN_FILE = "is a hidden file no language registry entry names, so whatever reads it decides what it means"
AGENT = "changes what the coding agent may run or is told to do"
EXECUTABLE = "is a program"
FILE = "language-kinds.json"
MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"MZ",
         b"\0asm")
HEAD_BYTES = 4
"""How many leading bytes `why` reads to tell a program by its shape."""


@cache
def registry() -> dict:
    """The language registry snapshot (`contracts/language-kinds.json`); `ContractError` when it is missing or lacks a table."""
    snapshot = load(FILE)
    if not all(isinstance(snapshot.get(key), dict) for key in ("registry", "names")):
        raise ContractError(f"{FILE}: the snapshot lacks its registry record or its table of names")
    return snapshot


@cache
def harness_names() -> frozenset[str]:
    """The instruction and settings names of every harness Poolhouse launches (`guard.harnesses`), as `why` compares them."""
    return frozenset(fold(name) for name in harnesses.names())


def fold(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def _by_path(parts: tuple[str, ...], name: str, extension: str) -> str:
    """Why path facts alone show a write here runs later: a harness or ecosystem record, a registry program name or a hidden folder."""
    harness = harness_names()
    if any(part in harness for part in parts):
        return AGENT
    if role := ecosystems.names().get(name) or ecosystems.suffixes().get(extension):
        return role
    if role := next((role for pattern, role in ecosystems.patterns() if pattern.fullmatch(name)), ""):
        return role
    if role := next((ecosystems.folders()[part] for part in parts[:-1] if part in ecosystems.folders()), ""):
        return role
    if registry()["names"].get(name) == "programming":
        return ecosystems.BUILD
    if not extension and any(part in ecosystems.PROGRAM_FOLDERS for part in parts[:-1]):
        return ecosystems.PROGRAM
    if name.startswith(".") and name not in registry()["names"]:
        return HIDDEN_FILE
    return HIDDEN_FOLDER if any(part.startswith(".") for part in parts[:-1]) else ""


def why(path: str, data: bytes = b"", mode: int = 0) -> str:
    """Why the file at ``path`` (relative, with ``/``) runs later, or empty. ``data`` (its first `HEAD_BYTES` bytes are enough) and ``mode``
    (its permission bits) are given by a caller that holds the file: a program by its executable bit, its shebang or its format is one
    whatever it is called."""
    parts = tuple(fold(part) for part in path.split("/") if part not in ("", "."))
    name = parts[-1] if parts else ""
    if named := _by_path(parts, name, posixpath.splitext(name)[1]):
        return named
    bytecode = data[2:4] == b"\r\n" and not data[:2].isascii()
    return EXECUTABLE if mode & 0o111 or data.startswith(b"#!") or data.startswith(MAGIC) or bytecode else ""
