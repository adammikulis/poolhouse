"""The writes a delegate's run was refused, decided from what the launcher sees: what its hook answered each call and the harness's own tool
events, never the model's words.

What a call writes is read by the guard's own parser (`guard.unboxed.written`). Refused by Poolhouse's tool rules (`RULES`): a call that writes
which the hook denied, unless an editing tool later completed on the same path. Refused by the sandbox (`SANDBOX`): a call that writes a path the
box lets nothing write and that says it could not: the system's refusal on a line that names the path, or, of an editing tool, the harness's own
word that the write itself failed (`UNWRITTEN`). A command or an edit that merely failed (a search with no match, text an edit does not find) is
neither. What a program writes without the line saying so is not seen here.

Every one is recorded and said. `lost` names those that say something of the work was left out, which the sandbox's refusal of git's own folder
does not once changes were applied: that folder is read-only there by design, and the launcher publishes and commits the files itself.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import safetext
from poolhouse.guard import unboxed
from poolhouse.isolation.publication import NAME

__all__ = ["RULES", "SANDBOX", "Refused", "Writes", "lost", "said", "writes"]

SANDBOX, RULES = "sandbox", "rules"
BY = {SANDBOX: "the sandbox", RULES: "Poolhouse's tool rules"}
EDITING = frozenset({"edit", "write"})
"""The tools, in lower case, that always change files."""
SHELL = frozenset({"bash", "shell"})
REFUSAL = re.compile(r"operation not permitted|permission denied|read-only file system|\b(?:EPERM|EACCES|EROFS)\b", re.IGNORECASE)
"""What the system says of a write it refuses."""
UNWRITTEN = ("Unable to write {}", "Unable to edit {}")
"""OpenCode 2.4.2's whole first line for an editing tool whose write of a path failed, whatever the system said; its other errors (an `oldString`
it does not find, a file that is not there) have words of their own."""
LISTED = 5
"""The refused paths a notice names before it only counts the rest."""


def _written(command: str, ground: unboxed.Ground, workdir: str = "") -> list[str]:
    """What the guard reads the shell line ``command`` to write; empty for a line it cannot take."""
    try:
        return unboxed.written("Bash", {"command": command, "workdir": workdir}, ground)
    except (OSError, ValueError, RecursionError):
        return []


def writes(command: str) -> bool:
    """Whether a shell line asks to change files, as the guard reads its text, never running it. What a program or a script changes without the
    line saying so is not seen here: what it leaves in the project is counted by the publication."""
    return bool(_written(command, unboxed.Ground("/", "/", "")))


@dataclass(frozen=True, slots=True)
class Refused:
    """One write a run was refused: the path (from the folder the run works in, when inside it), the tool and who refused (`SANDBOX`, `RULES`)."""

    path: str
    tool: str
    by: str

    @property
    def of_git(self) -> bool:
        """Whether the sandbox refused it in the git folder of the folder the run works in."""
        return self.by == SANDBOX and Path(self.path).parts[:1] == (".git",)


def lost(refused: Collection[Refused], applied: Collection[str]) -> tuple[Refused, ...]:
    """The refused writes that count against a run that had ``applied`` published: every one, except those of git's own folder when something was."""
    return tuple(each for each in refused if not (applied and each.of_git))


def _named(arguments: Mapping[str, Any]) -> str:
    return next((str(arguments[key]) for key in ("file_path", "path", "filePath") if isinstance(arguments.get(key), str)), "")


def _says_refused(output: str, path: str) -> bool:
    """Whether a line of ``output`` carries the system's refusal and names ``path``: by any part of one inside the folder, by the last of any
    other."""
    parts = [part for part in Path(path).parts if part not in (os.sep, "..", ".")]
    names = [re.compile(rf"(?<![\w.-]){re.escape(part)}(?![\w-])") for part in (parts[-1:] if Path(path).is_absolute() else parts)]
    return any(REFUSAL.search(line) and any(name.search(line) for name in names) for line in output.splitlines())


class Writes:
    """The writes of one run, as the hook's calls (`asked`) and the harness's tool events (`ran`) report them. ``project`` is the person's folder;
    `within` names the folder the run works in, the home its shell has and, in a sandbox, what the box lets nothing write."""

    def __init__(self, project: str) -> None:
        self.project, self.folder, self.home = project, project, ""
        self.box: Callable[[str], bool] | None = None
        self._refused: dict[str, Refused] = {}
        self._calls: dict[str, tuple[str, str, list[str]]] = {}
        """Of each call the hook was asked about: the tool, the shell line as the model gave it (the plugin rewrites one after) and what it
        writes."""
        self._through: set[str] = set()
        """The writing calls the hook let through that have not failed."""
        self._denied: set[str] = set()
        self._done = False
        self._lock = threading.Lock()

    def within(self, folder: str, box: Callable[[str], bool] | None = None, home: str = "") -> None:
        self.folder, self.box, self.home = folder, box, home

    @property
    def wrote(self) -> bool:
        """Whether any writing call went through."""
        with self._lock:
            return self._done or bool(self._through)

    def _full(self, path: str) -> str:
        return os.path.normpath(str(Path(self.folder) / path))

    def _shown(self, path: str) -> str:
        full = self._full(path)
        for root in (self.folder, self.project):
            for here in (os.path.normpath(root), os.path.realpath(root)):
                if full == here:
                    return "."
                if full.startswith(here + os.sep):
                    return Path(full).relative_to(here).as_posix()
        return full

    def _paths(self, tool: str, arguments: Mapping[str, Any], command: str) -> list[str]:
        """What a call of ``tool`` writes; empty for a call that writes nothing."""
        if tool in EDITING:
            return [_named(arguments)] if _named(arguments) else []
        if tool not in SHELL:
            return []
        return _written(command, unboxed.Ground(self.folder, self.home, ""), str(arguments.get("workdir") or ""))

    def asked(self, call: str, name: str, arguments: Mapping[str, Any], denied: bool) -> None:
        """Take in one call the hook answered."""
        held = safetext.utf8(dict(arguments))
        tool, command = name.lower(), str(held.get("command") or "")
        paths = list(dict.fromkeys(self._shown(each) for each in self._paths(tool, held, command)))
        with self._lock:
            self._calls[call] = (tool, command, paths)
            if denied:
                self._denied.add(call)
                self._refused.update((path, Refused(path, tool, RULES)) for path in paths)
            elif paths:
                self._through.add(call)

    def ran(self, part: Mapping[str, Any]) -> None:
        """Take in one ``tool_use`` event of the harness: the part of a call that has ended, completed or in an error."""
        state = safetext.utf8(part.get("state")) if isinstance(part.get("state"), dict) else {}
        arguments = state.get("input") if isinstance(state.get("input"), dict) else {}
        call = str(part.get("id") or part.get("callID") or "")
        failed = state.get("status") == "error"
        with self._lock:
            if call in self._denied:
                return
            tool, command, paths = self._calls.get(call) or (str(part.get("tool") or "").lower(), str(arguments.get("command") or ""), [])
            if failed:
                self._through.discard(call)
            elif state.get("status") == "completed" and tool in EDITING:
                # only an edit that completed shows the path was written after all: how a shell line did is not told
                self._done = True
                for path in paths or [self._shown(each) for each in self._paths(tool, arguments, command)]:
                    if path in self._refused and self._refused[path].by == RULES:
                        del self._refused[path]
        if self.box is None:
            return
        output = f"{state.get('output') or ''}\n{state.get('error') or ''}"
        unwritten = tool in EDITING and str(state.get("error") or "").split("\n")[0].strip() in {each.format(_named(arguments)) for each in UNWRITTEN}
        if tool in EDITING:
            found = self._paths(tool, arguments, command) if failed else []
        else:   # a word the shell would expand is no path this can judge
            found = [each for each in self._paths(tool, arguments, command) if "$" not in each and "`" not in each]
        for path in found:
            shown = self._shown(path)
            if not (unwritten or _says_refused(output, shown)):
                continue
            try:
                refused = self.box(self._full(path))
            except (OSError, ValueError):
                continue
            if refused:
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
    return f"<a path, digest {hashlib.sha256(path.encode(errors='surrogatepass')).hexdigest()[:8]}..>"


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
