"""The files a harness session downloaded, kept so that a later command that runs one asks.

`targets` reads a shell line for the files it would download, clone or copy from a downloaded file; `note` and `read`
keep them per session under the state root; `uses` finds the word of a line that runs one of them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from poolhouse import home, secretpaths
from poolhouse.guard import execchain
from poolhouse.guard.shellscan import has_expansion, segments

__all__ = ["note", "read", "targets", "uses"]

MOST = 400
PER_CALL = 50
FOLDER = "harness-fetched"
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "ash"})
WRAPPERS = frozenset({"env", "sudo", "doas", "nohup", "time", "nice", "ionice", "timeout", "command", "exec", "stdbuf", "setsid", "xargs", "watch"})
COPIERS = frozenset({"cp", "mv", "install", "ln", "rsync"})
HARMLESS = execchain.READERS | execchain.NAME_ONLY | {"git", "cd", "pushd", "popd"}
MAX_DEPTH = 3


def _file(agent: str) -> Path:
    return home.state(FOLDER, re.sub(r"[^A-Za-z0-9_.-]", "_", agent)[:64] + ".log")


def read(agent: str) -> list[str]:
    """The paths the session ``agent`` downloaded, newest last; empty when there are none."""
    try:
        lines = _file(agent).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return [line for line in lines[-MOST:] if line]


def note(agent: str, paths: Iterable[str]) -> None:
    """Add ``paths`` to what the session ``agent`` downloaded."""
    fresh = [p for p in dict.fromkeys(paths) if p and "\n" not in p][:PER_CALL]
    if not fresh:
        return
    target = _file(agent)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, ("\n".join(fresh) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _inner(argv: list[str]) -> list[str]:
    """``argv`` without the wrapper commands, their options and the variable assignments in front of the command."""
    at = 0
    while at < len(argv) and (Path(argv[at]).name.lower() in WRAPPERS or re.match(r"[A-Za-z_]\w*=", argv[at]) or (at and argv[at].startswith("-"))
                              or (at and re.fullmatch(r"[0-9.]+[smhd]?", argv[at]))):
        at += 1
    return argv[at:]


def _absolute(word: str, base: str) -> str:
    path = home.expand(secretpaths.expanded(word))
    return os.path.normpath(path if path.is_absolute() else Path(base) / path)


def _taken(fetched: Sequence[str], word: str, base: str) -> bool:
    if not word or word.startswith("-") or has_expansion(word):
        return False
    full = _absolute(word, base)
    return any(full == f or full.startswith(f.rstrip("/") + "/") or f.startswith(full.rstrip("/") + "/") for f in fetched)


def _bodies(argv: list[str]) -> list[str]:
    """The text a shell is told to run with ``-c``."""
    if argv and Path(argv[0]).name.lower() in SHELLS:
        return [argv[i + 1] for i, a in enumerate(argv[:-1]) if re.fullmatch(r"-[a-z]*c", a)]
    return []


def _walk(line: str, depth: int = 0):
    parsed, _ = segments(line)
    for seg in parsed:
        argv = _inner(list(seg.argv))
        if argv:
            yield seg, argv
            if depth < MAX_DEPTH:
                for body in _bodies(argv):
                    yield from _walk(body, depth + 1)


def targets(line: str, roots: Sequence[str], cwd: str = "", fetched: Sequence[str] = ()) -> list[str]:
    """The absolute paths ``line`` would download, clone or copy from a downloaded file."""
    base = cwd or (roots[0] if roots else str(Path.cwd()))
    found: list[str] = []
    previous: list[str] = []
    for seg, argv in _walk(line):
        name, args = Path(argv[0]).name.lower(), argv[1:]
        if name in ("cd", "pushd") and args:
            base = _absolute(args[0], base)
        fetching = name in execchain.DOWNLOADERS or (seg.piped and previous[:1] and Path(previous[0]).name.lower() in execchain.DOWNLOADERS)
        found += [_absolute(d, base) for d in execchain.downloads(name, args)]
        if fetching:
            found += [_absolute(t, base) for _, t in seg.redirects if t and not t.startswith(("&", "/dev/"))]
        if name == "tee" and previous[:1] and Path(previous[0]).name.lower() in execchain.DOWNLOADERS:
            found += [_absolute(a, base) for a in args if not a.startswith("-")]
        if name == "git" and args[:1] == ["clone"]:
            words = [a for a in args[1:] if not a.startswith("-")]
            if words:
                found.append(_absolute(words[1] if len(words) > 1 else re.sub(r"\.git$", "", words[0].rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]), base))
        if name in COPIERS:
            words = [a for a in args if not a.startswith("-")]
            if len(words) > 1 and any(_taken([*fetched, *found], w, base) for w in words[:-1]):
                found.append(_absolute(words[-1], base))
        previous = argv
    return found


def uses(line: str, roots: Sequence[str], fetched: Sequence[str], cwd: str = "") -> str:
    """The first word of ``line`` that a command which runs files names and that the session downloaded, else empty."""
    if not fetched:
        return ""
    base = cwd or (roots[0] if roots else str(Path.cwd()))
    for seg, argv in _walk(line):
        name = Path(argv[0]).name.lower()
        if name in ("cd", "pushd") and len(argv) > 1:
            base = _absolute(argv[1], base)
        if (name in HARMLESS and "/" not in argv[0]) or name in COPIERS:
            continue
        for word in [*argv, *(t for op, t in seg.redirects if op.startswith("<"))]:
            for piece in (word, *(word.split("=", 1)[1:] if word.startswith("--") else ())):
                if _taken(fetched, piece, base):
                    return Path(piece).name or piece
    return ""
