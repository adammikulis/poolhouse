"""Telling a change under the real node directory that the live node made for another agent's session from one this test run caused.

The node writes ``boards/<id>/names.json``, ``tokens.json`` and ``leases.json`` for every session that talks to it, and each entry
names the processes the session was registered from (its lineage). A change is excused when the node that holds ``node.lock`` is a
live process outside this run and every entry that changed belongs to a session this run did not start. An entry whose lineage has
a process of the run is the run's own write and is refused; one that cannot be placed is left to the caller's rule. Keys,
certificates, the enrolment secret, grants and person files are never excused.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

NODE = "node"
MINE, FOREIGN, UNKNOWN, OURS = "mine", "foreign", "unknown", "ours"
LARGEST = 8_000_000
MOST_BOARDS = 256
TOLERANCE_MS = 1500
GUARDED_NAMES = frozenset({"device.key", "device.key.wrapped", "device.key.enc", "device.custody.json", "device.cert", "enroll_secret.json", "persons.json", "persons_removed.json",
                           "persons_nonces.json", "land_trust.json"})
GUARDED_PARTS = frozenset({"person", "persons", "grants", "keystore"})
GUARDED_SUFFIXES = (".key", ".cert", ".pem")
OFFSET_MS = int(psutil.boot_time() * 1000) if sys.platform.startswith("linux") else 0
"""What turns a process start of the node's own clock (milliseconds since boot on Linux, since the epoch elsewhere) into epoch milliseconds."""
Judgement = tuple[set[str], dict[str, str]]


def anchor(pid: int) -> dict[str, int]:
    """``{pid, start}`` of a live process as the node records it in a lineage."""
    return {"pid": pid, "start": int(psutil.Process(pid).create_time() * 1000) - OFFSET_MS}


def _runner(argv: list[str]) -> bool:
    """Whether ``argv`` is pytest or the runner that starts it, by the program it runs and not by words in its command line."""
    named = Path(argv[0]).name if argv else ""
    return argv[1:3] == ["-m", "pytest"] or named in ("pytest", "py.test") or (
        len(argv) > 1 and Path(argv[1]).as_posix().endswith("scripts/test"))


def run_tree() -> set[int]:
    """This process, its ancestors that are pytest or the runner that starts it, and everything below the topmost of them."""
    me = psutil.Process()
    top = me
    for parent in me.parents():
        try:
            if not _runner(parent.cmdline()):
                break
        except psutil.Error:
            break
        top = parent
    try:
        return {top.pid, me.pid, *(p.pid for p in top.children(recursive=True))}
    except psutil.Error:
        return {me.pid}


def _members(tree: Callable[[], set[int]]) -> dict[int, int]:
    """Each live process of the run by id, with its start in epoch milliseconds."""
    found = {}
    for pid in tree():
        try:
            found[pid] = int(psutil.Process(pid).create_time() * 1000)
        except psutil.Error:
            continue
    return found


def _json(path: Path) -> object:
    """What ``path`` holds as JSON, or None when it is absent, too large or not JSON."""
    try:
        with path.open("rb") as handle:
            text = handle.read(LARGEST + 1)
        return json.loads(text) if len(text) <= LARGEST else None
    except (OSError, ValueError):
        return None


def _dict(value: object) -> dict | None:
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class Tables:
    """The session tables of a node directory: names by board, tokens, and leases by id; None where a file is unreadable."""

    names: dict[str, dict | None]
    tokens: dict | None
    leases: dict | None


def tables(node: Path) -> Tables:
    """Read the session tables under ``node``."""
    names = {path.parent.name: _dict(_json(path)) for path in sorted((node / "boards").glob("*/names.json"))[:MOST_BOARDS]}
    held = _dict(_json(node / "leases.json"))
    rows = held.get("leases") if held else None
    leases = {row["id"]: row for row in rows if isinstance(row, dict) and "id" in row} if isinstance(rows, list) else None
    return Tables(names, _dict(_json(node / "tokens.json")), leases)


def guarded(rel: Path) -> bool:
    """Whether ``rel`` (below the node directory) holds a key, certificate, secret, grant or person's record."""
    return (rel.name in GUARDED_NAMES or rel.name.endswith(GUARDED_SUFFIXES)
            or bool(GUARDED_PARTS.intersection(rel.parts[:-1])))


def _worst(found: list[tuple[str, str]]) -> tuple[str, str]:
    """The class that decides a file (mine, then unknown, then foreign) and the names that put it there."""
    for kind in (MINE, UNKNOWN):
        who = [name for klass, name in found if klass == kind]
        if who:
            return kind, ", ".join(sorted(who))
    return FOREIGN, ""


class NodeWatch:
    """The node directory of ``root`` as it was when this was made; ``judge`` says which later changes the run did not cause."""

    def __init__(self, root: Path, tree: Callable[[], set[int]] = run_tree) -> None:
        self.dir, self.tree = root / NODE, tree
        self.began = int(time.time() * 1000)
        self.members = _members(tree)
        self.before = tables(self.dir)

    def judge(self, changed: list[str]) -> Judgement:
        """``(excused, refused)`` for the changed paths under ``node/``: refused with the reason, excused when the
        live node wrote them for sessions outside the run. Paths it cannot place are in neither."""
        below = [n for n in changed if Path(n).parts[:1] == (NODE,)]
        if not below:
            return set(), {}
        self.members.update(_members(self.tree))
        after = tables(self.dir)
        daemon = self._daemon()
        excused: set[str] = set()
        refused: dict[str, str] = {}
        for name in below:
            rel = Path(name).relative_to(NODE)
            if guarded(rel):
                refused[name] = "a key, certificate, secret, grant or person file is never excused"
            elif daemon == OURS:
                refused[name] = "the node that holds this directory runs inside this test run"
            elif (found := self._placed(rel, after)) is None:
                continue
            elif found[0] == MINE:
                refused[name] = f"written for {found[1]}, registered from this test run"
            elif found[0] == FOREIGN and daemon == FOREIGN:
                excused.add(name)
        return excused, refused

    def _daemon(self) -> str:
        """``foreign`` for the live process that holds ``node.lock`` as ``node-run.json`` names it and that is no part of the run,
        ``ours`` when that process is part of the run, ``unknown`` when there is none."""
        run = _dict(_json(self.dir / "node-run.json")) or {}
        try:
            process = psutil.Process(run["pid"])
            born = process.create_time()
            held = {Path(f.path).resolve() for f in process.open_files()}
            live = process.is_running() and process.status() != psutil.STATUS_ZOMBIE
            same = abs(born - float(run["started_at"])) < 5
        except (psutil.Error, OSError, KeyError, TypeError, ValueError):
            return UNKNOWN
        if not (live and same and (self.dir / "node.lock").resolve() in held):
            return UNKNOWN
        return OURS if self._in_run(process.pid, int(born * 1000)) else FOREIGN

    def _in_run(self, pid: int, epoch_ms: int) -> bool:
        seen = self.members.get(pid)
        return seen is not None and abs(seen - epoch_ms) <= TOLERANCE_MS

    def _member(self, pid: object, start: object) -> bool:
        """Whether the process ``pid`` that started at ``start`` (the node's clock) is one of the run's; a lease with no start is judged by its id."""
        if not isinstance(pid, int):
            return False
        if start is None:
            return pid in self.members
        return isinstance(start, int) and self._in_run(pid, start + OFFSET_MS)

    def _placed(self, rel: Path, after: Tables) -> tuple[str, str] | None:
        """The class of a session table's change and the sessions behind it, or None for a file that is not one."""
        parts = rel.parts
        if parts == ("leases.json",):
            return self._leases(after)
        if parts == ("tokens.json",):
            return self._compare(self.before.tokens, after.tokens, lambda key, entry: str(entry.get("name") or key[:8]))
        if len(parts) == 3 and parts[0] == "boards" and parts[2] == "names.json":
            return self._compare(self.before.names.get(parts[1]) or {}, after.names.get(parts[1]), lambda key, _entry: key)
        return None

    def _compare(self, was: dict | None, now: dict | None, who: Callable[[str, dict], str]) -> tuple[str, str]:
        """Class every entry that differs between ``was`` and ``now``; an entry is new when it is absent from ``was`` or has another digest."""
        if was is None or now is None:
            return UNKNOWN, ""
        found = []
        for key in sorted(was.keys() | now.keys()):
            if was.get(key) == now.get(key):
                continue
            entry = _dict(now.get(key)) or _dict(was.get(key))
            if entry is None:
                found.append((UNKNOWN, key))
                continue
            old = _dict(was.get(key))
            found.append((self._entry(entry, old is None or old.get("digest") != entry.get("digest")), who(key, entry)))
        return _worst(found)

    def _entry(self, entry: dict, new: bool) -> str:
        """``mine`` when the session was registered from a process of the run; ``foreign`` when it was before the run or from a process
        that started before it; otherwise ``unknown``."""
        lineage = entry.get("lineage")
        anchors = [a for a in lineage if isinstance(a, dict)] if isinstance(lineage, list) else []
        if any(self._member(a.get("pid"), a.get("start")) for a in anchors):
            return MINE
        older = any(isinstance(a.get("start"), int) and a["start"] + OFFSET_MS < self.began for a in anchors)
        return FOREIGN if older or not new else UNKNOWN

    def _leases(self, after: Tables) -> tuple[str, str]:
        was, now = self.before.leases, after.leases
        if was is None or now is None:
            return UNKNOWN, ""
        found = [(self._lease(_dict(now.get(key)) or _dict(was.get(key)) or {}, after), str(key))
                 for key in sorted(was.keys() | now.keys()) if was.get(key) != now.get(key)]
        return _worst(found)

    def _lease(self, lease: dict, after: Tables) -> str:
        """Class a lease by the process that holds it and the session it was granted to."""
        if lease.get("remote") is True:
            return FOREIGN
        if isinstance(lease.get("pid"), int) and lease["pid"] > 0 and self._member(lease["pid"], lease.get("pid_start")):
            return MINE
        board, name = lease.get("board"), lease.get("name")
        before = self.before.names.get(board) or {}
        session = _dict((after.names.get(board) or {}).get(name)) or _dict(before.get(name))
        return UNKNOWN if session is None else self._entry(session, name not in before)
