"""Which files are plain source and which run later: the one classifier, by structure, deny by default.

A change is plain source when four tests hold (docs/design-isolated-launcher.md, 4.4.1): its shape (a regular text file with one link, no
executable bit, no shebang, no program format), its position (no path component starts with a dot), its kind (the language registry
snapshot types its extension as a programming language, markup or prose, and no record assigns its name to a role) and its wiring (no
non-plain file of the result tree names it). A fact the caller cannot read is a failed test. `classify` is shared by publication, by the
in-place capture and by the hooks; the hooks read `Verdict.runs`, the part of the answer that path facts alone give.
"""

from __future__ import annotations

import codecs
import posixpath
import re
import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass
from functools import cache

from poolhouse.contracts.loader import ContractError, load
from poolhouse.guard import ecosystems, harnesses

__all__ = ["CLASSES", "PLAIN", "Verdict", "classify", "harness_names", "registry", "tree", "wired"]

PLAIN = "plain"
CLASSES = {
    "hidden": "a hidden configuration file",
    "program": "a program",
    "data": "data or configuration: the tool that reads it decides what it means",
    "instruction": "an instruction file for a coding tool",
    "named": "named by a file that runs it",
    "governing": "a file that governs what runs or who reviews",
}
"""The one sentence the parent writes for each class of refusal."""
GOVERNING = frozenset({"hidden", "instruction", "named", "governing"})
HIDDEN_FOLDER = "is in a hidden folder, which tools run or read without being asked"
HIDDEN_FILE = "is a hidden file no language registry entry types, so whatever reads it decides what it means"
AGENT = "changes what the coding agent may run or is told to do"
FILE = "language-kinds.json"
MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"MZ", b"PK\x03\x04",
         b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b", b"\0asm", b"BZh", b"\xfd7zXZ\0", b"7z\xbc\xaf\x27\x1c", b"Rar!")
PATH_RUN = re.compile(r"[A-Za-z0-9._/@+-]+")
READ_LIMIT = 1 << 20


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the classifier concluded: ``klass`` is `PLAIN` or a key of `CLASSES`; ``runs`` says why path facts alone show it runs later."""

    klass: str
    runs: str = ""

    @property
    def plain(self) -> bool:
        return self.klass == PLAIN

    @property
    def governing(self) -> bool:
        """Never applied, whatever rule and whoever asks: it runs by itself later or decides what runs or who reviews."""
        return self.klass in GOVERNING

    @property
    def sentence(self) -> str:
        return "" if self.plain else CLASSES[self.klass]


@cache
def registry() -> dict:
    """The language registry snapshot (`contracts/language-kinds.json`); `ContractError` when it is missing or lacks a table."""
    snapshot = load(FILE)
    if not all(isinstance(snapshot.get(key), dict) for key in ("registry", "extensions", "names")) or not snapshot.get("plain_kinds"):
        raise ContractError(f"{FILE}: the snapshot lacks its registry record or one of its tables")
    return snapshot


@cache
def harness_names() -> frozenset[str]:
    """The instruction and settings names of every harness Poolhouse launches (`guard.harnesses`), as `classify` compares them."""
    return frozenset(fold(name) for name in harnesses.names())


def fold(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def _runs(parts: tuple[str, ...], name: str, extension: str) -> str:
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


def _structure(data: bytes, mode: int, links: int) -> str:
    """The class a failed shape test (4.4.1, test 1) gives, or empty when the file is a text file with nothing executable about it."""
    bytecode = data[2:4] == b"\r\n" and not data[:2].isascii()
    if mode & 0o7111 or data.startswith(b"#!") or data.startswith(MAGIC) or bytecode or data[257:262] == b"ustar":
        return "program"
    if links != 1 or b"\0" in data:
        return "data"
    try:
        codecs.utf_8_decode(data, "strict", len(data) < READ_LIMIT)
    except UnicodeDecodeError:
        return "data"
    return ""


def _kind(name: str, extension: str) -> str:
    """The class test 3 gives: empty when the registry types the extension as plain and no record or registry name claims the file."""
    tables = registry()
    typed = extension in ecosystems.source_extensions() or tables["extensions"].get(extension) in tables["plain_kinds"]
    return "" if typed and name not in tables["names"] else "data"


def classify(path: str, data: bytes | None = None, mode: int | None = None, links: int | None = None) -> Verdict:
    """The verdict for the file at ``path`` (relative, with ``/``) with the facts the caller has: its bytes, its permission and type bits
    and its link count. A fact that is not given fails the test it belongs to, so a file whose shape cannot be read is not plain source."""
    parts = tuple(fold(part) for part in path.split("/") if part not in ("", "."))
    name = parts[-1] if parts else ""
    extension = posixpath.splitext(name)[1]
    runs = _runs(parts, name, extension)
    if any(part in harness_names() for part in parts):
        return Verdict("instruction", runs)
    if any(part.startswith(".") for part in parts) or name.startswith("."):
        return Verdict("hidden", runs)
    if runs and runs != HIDDEN_FOLDER:
        return Verdict("governing", runs)
    if data is None or mode is None or links is None:
        return Verdict("data", runs)
    return Verdict(_structure(data, mode, links) or _kind(name, extension) or PLAIN, runs)


def wired(paths: Collection[str], non_plain: Collection[str], read: Callable[[str], bytes | None]) -> dict[str, str]:
    """Test 4: the files of the result tree that a non-plain text file names, directly or through the files it names in turn.

    ``paths`` is every file of the result tree, ``non_plain`` the ones that fail tests 1 to 3 and ``read`` gives the bytes of a file (or
    ``None``). A path is a maximal run of path characters that resolves, from the project root or from the naming file's directory, to a
    file of the tree. Each file reached maps to the file that named it; the files reached are non-plain too."""
    held, reached, queue = set(paths), {}, list(non_plain)
    seen, basenames = set(queue), {posixpath.basename(path) for path in held}
    while queue:
        current = queue.pop()
        text = (read(current) or b"")[:READ_LIMIT].decode("utf-8", errors="replace")
        for token in set(PATH_RUN.findall(text)):
            if token.rpartition("/")[2] not in basenames:
                continue
            for base in ("", posixpath.dirname(current)):
                found = posixpath.normpath(posixpath.join(base, token))
                if found in held and found not in seen:
                    seen.add(found)
                    reached[found] = current
                    queue.append(found)
    return reached


def tree(paths: Collection[str], facts: Callable[[str], tuple[bytes, int, int] | None]) -> dict[str, Verdict]:
    """The verdict of every file of a result tree: tests 1 to 3 by `classify` with the facts ``facts`` gives for a path (its first
    `READ_LIMIT` bytes, its permission and type bits and its link count; ``None`` when they cannot be read), then test 4 over the files
    a non-plain text file names, which become non-plain whatever they hold."""
    known: dict[str, tuple[bytes, int, int] | None] = {}

    def got(path: str) -> tuple[bytes, int, int] | None:
        if path not in known:
            known[path] = facts(path)
        return known[path]

    verdicts = {path: classify(path, *(got(path) or (None, None, None))) for path in paths}
    named = wired(paths, [path for path, verdict in verdicts.items() if not verdict.plain], lambda path: (got(path) or (b"",))[0])
    return {**verdicts, **{path: Verdict("named", ecosystems.BUILD) for path in named if verdicts[path].plain}}
