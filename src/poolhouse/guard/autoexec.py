"""Writes that put code where something runs it later: git hooks, build and test files, shell startup files, agent settings.

`path_reason` says why a path runs by itself, from `guard.runs_later.why` and the records it reads (the language registry snapshot, the
harness and ecosystem records); no list of names is kept here. `write_findings` reads the files a Write, Edit, MultiEdit,
NotebookEdit or apply_patch call would change and returns one finding per file.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from poolhouse import files, home, secretpaths
from poolhouse.guard import fetchexec, runs_later
from poolhouse.guard.harm import Finding, outside, resolve
from poolhouse.guard.verbs import words_of

__all__ = ["path_reason", "write_findings", "written"]

U, D = "unsure", "destructive"
EDITS = frozenset({"edit", "write", "append", "create", "update", "patch", "replace", "insert", "save", "put"})
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
PATH_KEYS = ("file_path", "path", "notebook_path", "filepath", "filename", "file")
TEXT_KEYS = ("content", "contents", "text", "file_text", "new_string", "new_text", "newText", "new_source", "data", "body")
BEGIN = "*** Begin Patch"
EXCERPT = 2
MAX_FILE = 1 << 20
SHOWN = 90


def _parts(path: str, roots: Sequence[str]) -> tuple[str, ...]:
    full = resolve(path, roots)
    for root in roots:
        base = Path(os.path.normpath(home.expand(root)))
        if full == base or base in full.parents:
            return tuple(p.lower() for p in full.relative_to(base).parts)
    user = home.user_home()
    parts = full.relative_to(user).parts if user in full.parents else full.parts
    return tuple(p.lower() for p in parts)


def _real(path: str, roots: Sequence[str]) -> tuple[str, list[str]]:
    full = resolve(path, roots)
    try:
        return os.path.realpath(full), [os.path.realpath(home.expand(r)) for r in roots]
    except (OSError, ValueError):
        return str(full), list(roots)


def path_reason(path: str, roots: Sequence[str] = ()) -> str:
    """Why a write to ``path`` runs by itself later (relative to the project root, or the home folder
    outside it, a link being followed), or empty when it does not."""
    return _reason(path, roots) or _reason(*_real(path, roots))


def _reason(path: str, roots: Sequence[str]) -> str:
    parts = _parts(path, roots)
    return runs_later.why("/".join(parts)) if parts else ""


def _patch(text: str) -> Iterator[tuple[str, str]]:
    """The ``(path, added lines)`` of each file an ``apply_patch`` body adds or updates."""
    path, lines = "", []
    for line in text.splitlines():
        found = re.match(r"\*\*\* (?:Add|Update) File: (.+)$", line) or re.match(r"\*\*\* Move to: (.+)$", line)
        if found or line.startswith("*** "):
            if path:
                yield path, "\n".join(lines)
            path, lines = (found.group(1) if found else ""), []
        elif path and line.startswith("+"):
            lines.append(line[1:])
    if path:
        yield path, "\n".join(lines)


def _edits(args: Mapping[str, Any]) -> Iterator[str]:
    edits = args.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, Mapping):
                yield from (edit[k] for k in ("new_string", "new_text", "newText") if isinstance(edit.get(k), str))


def written(name: str, args: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The ``(path, text)`` pairs a write tool call would put on disk; empty for any other call."""
    pairs = [pair for v in args.values() if isinstance(v, str) and BEGIN in v for pair in _patch(v)]
    if name not in WRITE_TOOLS and not set(words_of(name)) & EDITS:
        return pairs
    text = "\n".join([*(str(args[k]) for k in TEXT_KEYS if isinstance(args.get(k), str)), *_edits(args)])
    return [*pairs, *((str(args[k]), text) for k in PATH_KEYS if isinstance(args.get(k), str) and args[k])]


def _excerpt(text: str) -> str:
    lines = [fetchexec.ascii_line(x, SHOWN) for x in text.splitlines() if x.strip()][:EXCERPT]
    return "; ".join(lines)


def escapes(path: str, roots: Sequence[str]) -> list[Finding]:
    """Findings for a write to ``path``: a credential file outside the project is forbidden, and a link that leads out of it is a question."""
    hit = secretpaths.path_hit(path, roots[0] if roots else "")
    if hit and secretpaths.beyond(hit.path, roots):
        return [Finding(D, f"writes a credential file ({hit.rule}) outside the project, which an agent never does", forbidden=True)]
    if roots and not outside(path, roots) and secretpaths.beyond(path, roots):
        return [Finding(D, f"writes through a link to {fetchexec.ascii_line(os.path.realpath(resolve(path, roots)), SHOWN)}, outside the project")]
    return []


def _replacements(before: str, args: Mapping[str, Any]) -> str:
    top = {"old_string", "new_string", "oldText", "newText"} & args.keys()
    edits = args.get("edits") if "edits" in args else [args] if top else None
    if not isinstance(edits, list) or not edits or ("edits" in args and top):
        raise ValueError("the edit arguments do not describe exact replacements")
    camel = all(isinstance(e, Mapping) and set(e) == {"oldText", "newText"} for e in edits)
    snake = all(isinstance(e, Mapping) and {"old_string", "new_string"} <= e.keys()
                and not {"oldText", "newText"} & e.keys() for e in edits)
    if not camel and not snake:
        raise ValueError("the edit mixes or omits replacement fields")
    after, matches = before, []
    for edit in edits:
        old, new = (edit.get(k) for k in (("oldText", "newText") if camel else ("old_string", "new_string")))
        if not isinstance(old, str) or not old or not isinstance(new, str) or "\r" in old + new:
            raise ValueError("the edit is not an exact text replacement")
        many = edit.get("replace_all", False)
        if not isinstance(many, bool):
            raise ValueError("the edit has an invalid replacement count")
        content = before if camel else after
        count = content.count(old)
        if not count or (count != 1 and not many):
            raise ValueError("the edit text is missing or not unique in the current file")
        if camel:
            at = before.index(old)
            matches.append((at, at + len(old), new))
        else:
            after = after.replace(old, new, -1 if many else 1)
    matches.sort()
    if any(left[1] > right[0] for left, right in pairwise(matches)):
        raise ValueError("the edits overlap in the original file")
    for start, end, new in reversed(matches):
        after = after[:start] + new + after[end:]
    return after


def _merged(path: str, roots: Sequence[str], args: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return the current file and exact prospective postimage of an edit."""
    if not {"edits", "old_string", "new_string", "oldText", "newText"} & args.keys():
        return None
    if not roots or secretpaths.beyond(path, roots):
        raise ValueError("the edited file is outside the selected project")
    try:
        descriptor = os.open(resolve(path, roots), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode) or initial.st_size > MAX_FILE:
                raise ValueError("the edited file cannot be inspected within its size limit")
            raw = stream.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE or files.stat_signature(initial) != files.stat_signature(os.fstat(stream.fileno())):
                raise ValueError("the edited file changed while it was inspected")
        before = raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError("the current edited file cannot be inspected") from error
    if "\r" in before:
        raise ValueError("the edit requires newline normalization")
    return before, _replacements(before, args)


def _flags(text: str, path: str, roots: Sequence[str], args: Mapping[str, Any], strict: bool) -> list[fetchexec.Flag]:
    """What ``text`` flags, plus what the whole file flags once the edit is applied that it did not flag before."""
    flags = fetchexec.scan(text, strict=strict)
    merged = _merged(path, roots, args)
    if merged is not None:
        seen = {f.text for f in fetchexec.scan(merged[0], strict=strict)} | {f.text for f in flags}
        flags += [f for f in fetchexec.scan(merged[1], strict=strict) if f.text not in seen]
    return flags


def write_findings(name: str, args: Mapping[str, Any], roots: Sequence[str] = ()) -> list[Finding]:
    """One finding for each file a write call would change that runs by itself or whose new text
    downloads and runs code, sends a credential out or hides its meaning."""
    found: list[Finding] = []
    pairs = written(name, args)
    if name in {"Edit", "MultiEdit"}:
        if not pairs:
            found.append(Finding(U, "the edit has no file path"))
        if not {"edits", "old_string", "new_string", "oldText", "newText"} & args.keys():
            found.append(Finding(U, "the edit has no exact replacements"))
    for path, text in pairs:
        found += escapes(path, roots)
        why = path_reason(path, roots)
        try:
            flags = _flags(text, path, roots, args, bool(why))
        except ValueError as error:
            found.append(Finding(U, str(error)))
            flags = fetchexec.scan(text, strict=bool(why))
        shown = fetchexec.describe(flags)
        base = fetchexec.ascii_line(Path(path).name or path, SHOWN)
        if why:
            found.append(Finding(U, f"writes {base}, which {why}" + (f": {shown}" if shown else f" (starts: {_excerpt(text)})" if text.strip() else "")))
        elif flags:
            found.append(Finding(U, f"writes code to {base} that {shown}"))
    return found
