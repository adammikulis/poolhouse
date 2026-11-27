"""The writes a delegate's run was refused, decided from what the launcher sees: what its hook answered each call and the harness's own tool
events, never the model's words.

Refused by Poolhouse's tool rules (`RULES`): a call that writes (an editing tool, or a shell line that asks to change files) which the hook
denied, unless a later call that went through writes the same path. Refused by the sandbox (`SANDBOX`): an editing tool that ended in an error on
a path the box lets nothing write, and a shell line that asks to change such a path whose output carries the system's refusal on a line that names
it. A command or an edit that merely failed is neither. What a program writes without the line saying so is not seen here.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from poolhouse.guard.shellscan import segments
from poolhouse.isolation.publication import NAME
from poolhouse.worktreerules import CHANGES_THE_TREE

__all__ = ["RULES", "SANDBOX", "Refused", "Writes", "said", "targets", "writes"]

SANDBOX, RULES = "sandbox", "rules"
BY = {SANDBOX: "the sandbox", RULES: "Poolhouse's tool rules"}
EDITING = frozenset({"edit", "write"})
"""The tools, in lower case, that always change files."""
SHELL = frozenset({"bash", "shell"})
WRITERS = frozenset({"chmod", "cp", "dd", "install", "ln", "mkdir", "mv", "patch", "rm", "rmdir", "rsync", "tee", "touch", "truncate", "unzip"})
"""The programs a shell line names when it changes files."""
IN_PLACE = frozenset({"perl", "ruby", "sed"})
"""The programs that change files only when told to edit in place."""
LAST = frozenset({"cp", "install", "ln", "rsync"})
"""The writers that change only the last path they are given."""
REPOSITORY = CHANGES_THE_TREE - {"apply", "clean"}
"""The git commands that write the repository itself, not only the files of the tree."""
REFUSAL = re.compile(r"operation not permitted|permission denied|read-only file system|\b(?:EPERM|EACCES|EROFS)\b", re.IGNORECASE)
"""What the system says of a write it refuses."""
UNNAMED = "(a shell line)"
"""The path of a refused shell line that names none."""
LISTED = 5
"""The refused paths a notice names before it only counts the rest."""


def _under(folder: str, path: str) -> str:
    """``path`` read from ``folder`` (empty: as it is written); an absolute path stays."""
    return str(Path(folder) / path)


def _in_place(words: list[str]) -> bool:
    return any(word.startswith(("-i", "-pi", "--in-place")) for word in words)


def _writes(program: str, words: list[str]) -> bool:
    if program == "git":
        return any(word in CHANGES_THE_TREE for word in words[:3])
    return program in WRITERS or (program in IN_PLACE and _in_place(words))


def writes(command: str) -> bool:
    """Whether a shell line asks to change files, read from its text and never run: a redirect to anything but ``/dev/null`` or a descriptor, a
    program that changes files (`WRITERS`), an edit in place, or a git command that changes the tree. What a program or a script changes without
    the line saying so is not seen here: what it leaves in the project is counted by the publication."""
    for part in segments(command)[0]:
        if any(_redirected(operator, target) for operator, target in part.redirects):
            return True
        if part.argv and _writes(Path(part.argv[0]).name, list(part.argv[1:])):
            return True
    return False


def _redirected(operator: str, target: str) -> bool:
    return ">" in operator and target != "/dev/null" and not target.lstrip("&").isdigit()


def _changed(program: str, words: list[str]) -> list[str]:
    """The paths ``program`` with ``words`` asks to change."""
    plain = [word for word in words if word and not word.startswith("-")]
    if program == "git":
        return [".git"] if any(word in REPOSITORY for word in words[:3]) else []
    if program in LAST:
        return plain[-1:] if len(plain) > 1 else []
    if program == "dd":
        return [word[3:] for word in words if word.startswith("of=")]
    if program == "unzip":
        return [after for before, after in pairwise(words) if before == "-d"]
    if program == "chmod" or (program in IN_PLACE and _in_place(words)):
        return plain[1:]   # the first plain word is the mode, or the script
    return plain if program in WRITERS and program != "patch" else []


def targets(command: str) -> list[str]:
    """The paths a shell line asks to change, as the line writes them, behind the folder a plain ``cd`` before them named."""
    found: list[str] = []
    folder = ""
    for part in segments(command)[0]:
        words = list(part.argv)
        if len(words) == 2 and words[0] == "cd":
            folder = _under(folder, words[1])
            continue
        named = [target for operator, target in part.redirects if _redirected(operator, target)]
        if words:
            named += _changed(Path(words[0]).name, words[1:])
        found += [_under(folder, each) for each in named if each]
    return list(dict.fromkeys(found))


@dataclass(frozen=True, slots=True)
class Refused:
    """One write a run was refused: the path (from the folder the run works in, when inside it), the tool and who refused (`SANDBOX`, `RULES`)."""

    path: str
    tool: str
    by: str


def _named(arguments: Mapping[str, Any]) -> str:
    return next((str(arguments[key]) for key in ("file_path", "path", "filePath") if isinstance(arguments.get(key), str)), "")


def _says_refused(output: str, path: str) -> bool:
    """Whether a line of ``output`` carries the system's refusal and names ``path``, by any part of it."""
    names = [part for part in Path(path).parts if len(part) > 1 and part not in ("..", os.sep)] or [path]
    return any(REFUSAL.search(line) and any(name in line for name in names) for line in output.splitlines())


class Writes:
    """The writes of one run, as the hook's calls (`asked`) and the harness's tool events (`ran`) report them. ``project`` is the person's folder;
    `within` names the folder the run works in and, in a sandbox, what the box lets nothing write."""

    def __init__(self, project: str) -> None:
        self.project, self.folder = project, project
        self.box: Callable[[str], bool] | None = None
        self._refused: dict[str, Refused] = {}
        self._calls: dict[str, tuple[str, str]] = {}
        """The tool and the shell line of each call the hook was asked about, as the model gave it (the plugin rewrites a shell line after)."""
        self._through: dict[str, list[Refused]] = {}
        """The writing calls the hook let through that have not failed, each with the refusals of the same paths it takes back."""
        self._denied: set[str] = set()
        self._done = False
        self._lock = threading.Lock()

    def within(self, folder: str, box: Callable[[str], bool] | None = None) -> None:
        self.folder, self.box = folder, box

    @property
    def wrote(self) -> bool:
        """Whether any writing call went through."""
        with self._lock:
            return self._done or bool(self._through)

    def _full(self, path: str) -> str:
        return os.path.normpath(_under(self.folder, path))

    def _shown(self, path: str) -> str:
        full = self._full(path)
        for root in (self.folder, self.project):
            for here in (os.path.normpath(root), os.path.realpath(root)):
                if full.startswith(here + os.sep):
                    return Path(full).relative_to(here).as_posix()
        return full

    def _paths(self, tool: str, arguments: Mapping[str, Any], command: str) -> list[str]:
        """The paths a call of ``tool`` writes, as it names them; empty for a call that writes nothing."""
        if tool in EDITING:
            return [_named(arguments)] if _named(arguments) else []
        if tool not in SHELL or not writes(command):
            return []
        base = str(arguments.get("workdir") or "")
        return [_under(base, each) for each in targets(command)] or [UNNAMED]

    def asked(self, call: str, name: str, arguments: Mapping[str, Any], denied: bool) -> None:
        """Take in one call the hook answered."""
        tool, command = name.lower(), str(arguments.get("command") or "")
        paths = [each if each == UNNAMED else self._shown(each) for each in self._paths(tool, arguments, command)]
        with self._lock:
            self._calls[call] = (tool, command)
            if denied:
                self._denied.add(call)
                for path in paths:
                    self._refused[path] = Refused(path, tool, RULES)
            elif paths:
                self._through[call] = [self._refused.pop(path) for path in paths
                                       if path in self._refused and self._refused[path].by == RULES]

    def ran(self, part: Mapping[str, Any]) -> None:
        """Take in one ``tool_use`` event of the harness: the part of a call that has ended, completed or in an error."""
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        arguments = state.get("input") if isinstance(state.get("input"), dict) else {}
        call = str(part.get("id") or part.get("callID") or "")
        failed = state.get("status") == "error"
        with self._lock:
            if call in self._denied:
                return
            tool, command = self._calls.get(call) or (str(part.get("tool") or "").lower(), str(arguments.get("command") or ""))
            if failed:
                for held in self._through.pop(call, []):
                    self._refused.setdefault(held.path, held)
            elif tool in EDITING:
                self._done = True
        if self.box is None:
            return
        output = f"{state.get('output') or ''}\n{state.get('error') or ''}"
        base = str(arguments.get("workdir") or "")
        if tool in EDITING:
            found = [_named(arguments)] if failed and _named(arguments) else []
        elif tool in SHELL:
            # a word the shell would expand, or one under the box's own home, is no path this can judge
            found = [_under(base, each) for each in targets(command)
                     if "$" not in each and "`" not in each and not each.startswith("~") and _says_refused(output, each)]
        else:
            return
        for path in found:
            try:
                refused = self.box(self._full(path))
            except (OSError, ValueError):
                continue
            if refused:
                shown = self._shown(path)
                with self._lock:
                    self._refused[shown] = Refused(shown, tool, SANDBOX)

    def refused(self, applied: Collection[str] = ()) -> tuple[Refused, ...]:
        """The writes refused, without those whose path was published all the same (``applied``)."""
        with self._lock:
            return tuple(each for each in self._refused.values() if each.path not in applied)


def _token(path: str) -> str:
    """``path`` as a notice names it: quoted when it is a plain short name, else a digest, so that no text the model chose reads as a sentence."""
    if NAME.fullmatch(path):
        return f'"{path}"'
    return f"<a path, digest {hashlib.sha256(path.encode(errors='surrogateescape')).hexdigest()[:8]}..>"


def said(refused: Collection[Refused]) -> str:
    """The refused writes in the launcher's words: how many each refuser refused and the first of their paths; empty for none."""
    parts = []
    for by, who in BY.items():
        paths = [each.path for each in refused if each.by == by]
        if not paths:
            continue
        more = f" and {len(paths) - LISTED} more" if len(paths) > LISTED else ""
        count = "1 write" if len(paths) == 1 else f"{len(paths)} writes"
        parts.append(f"{count} refused by {who}: {', '.join(map(_token, paths[:LISTED]))}{more}")
    return "; ".join(parts)
