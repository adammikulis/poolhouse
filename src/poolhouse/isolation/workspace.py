"""The workspace of one launch (docs/design-isolated-launcher.md, 4.2 and 4.4): the stage the agent sees, a publication after each turn, and the end.

`Session.begin` stages the person's project (or takes the stage an earlier launch of the same conversation kept), links the person's environment
folders into it, and opens the directory of this session's refused patches. `publish_turn` captures the stage, decides every change with
`isolation.publication`, applies the plain ones with `coding_apply` and refuses the rest at once: a patch kept for the session to read, and one
report to the launching session in the parent's words. The base advances to what was published, so each turn compares against the last one.
`finish` ends every process of the session first (`sandbox.process_tree.end`), and publishes only when none is left. Nothing here asks a question,
holds a change for an answer or waits for a person.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from poolhouse import activity, coding_apply, files, private_path
from poolhouse.activity import source_snapshot
from poolhouse.activity.source_snapshot import Capture, CaptureEntry
from poolhouse.coding_workspace import Stage, Workspace, between
from poolhouse.coding_workspace_state import CONTROL_PREFIXES
from poolhouse.isolation import places, publication, toolchain
from poolhouse.isolation.notices import HEAD, Kept, Notice, Patch, Reports, words
from poolhouse.isolation.publication import Refusal, fold
from poolhouse.isolation.publish import PublishRule
from poolhouse.sandbox import process_tree

__all__ = ["ENVIRONMENT_LINKS", "PATCHES_AT", "Launch", "Session", "Turn", "binds"]

ENVIRONMENT_LINKS = (".venv", "venv", "node_modules")
"""The ignored folders a project needs that a stage does not hold: each is linked into the stage when the person's own folder of that name is a real
directory inside the project."""
PATCHES_AT = "/poolhouse/refused"
"""Where the Linux and WSL backends bind the session's patch directory inside the namespace, read-only."""
KEPT_VERSION = 1
"""The shape of the record a kept stage leaves for a re-launch of its conversation."""
Log = Callable[..., object]
SURVIVOR = "A process of the session could not be ended, so the stage may still be written and nothing more of it was published; the stage is kept."
DEFERRED = "The stage was written while it was read, so nothing was published this turn; it is read again at the next turn."


@dataclass(frozen=True, slots=True)
class Launch:
    """What one launch tells the workspace: the person's project, the state root, the session's name (the parent's own), the conversation whose kept
    stage a re-launch reuses, the `publish` rules decided for this launch, where reports go and the activity log."""

    project: str
    state: str
    session: str
    notice: Notice
    conversation: str = ""
    rules: tuple[PublishRule, ...] = ()
    log: Log = activity.record


@dataclass(frozen=True, slots=True)
class Turn:
    """What one publication did: the paths applied, the refusals, the paths the person saved meanwhile, the patch, the words and what became of the
    report (``posted``, ``folded``, ``capped`` or ``line``); ``deferred`` says why nothing was published."""

    applied: tuple[str, ...] = ()
    refused: tuple[Refusal, ...] = ()
    conflicts: tuple[str, ...] = ()
    patch: Patch | None = None
    text: str = ""
    report: str = ""
    deferred: str = ""


def _digest(value: str | bytes) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode(errors="surrogateescape")).hexdigest()[:32]


def binds(project: str | os.PathLike[str], stage: str, patches: str) -> tuple[tuple[str, str, bool], ...]:
    """The binds the Linux and WSL backends make inside the namespace: the stage at the person's own project path, so the absolute paths inside a
    virtual environment resolve with no link, and the session's patch directory, read-only, at a fixed path (source, target, writable)."""
    return ((stage, os.path.realpath(project), True), (patches, PATCHES_AT, False))


def environment_links(project: Path, stage: Path) -> tuple[tuple[str, toolchain.Resolved], ...]:
    """Link each environment folder of the project into the stage (a link to the person's own directory, which the profile reads and never writes).
    A folder that is a link, or that resolves outside the project, gets nothing, and a name the stage already holds is left as it is."""
    root, made = Path(os.path.realpath(project)), []
    for name in ENVIRONMENT_LINKS:
        found = toolchain.resolve(root / name) if (root / name).is_dir() and not (root / name).is_symlink() else None
        if found is None or not Path(found.path).is_relative_to(root) or os.path.lexists(stage / name):
            if found is not None:
                found.close()
            continue
        (stage / name).symlink_to(found.path)
        made.append((name, found))
    return tuple(made)


def _tracked(root: Path) -> frozenset[str]:
    """The paths git holds, from the index and never from the repository's configuration; nothing at all when it cannot be read, so every
    overwrite of a file git may not hold is refused."""
    try:
        return frozenset(os.fsdecode(name) for name in source_snapshot.git_output(["ls-files", "-z"], cwd=root).split(b"\0") if name)
    except (OSError, RuntimeError, TimeoutError):
        return frozenset()


class Session:
    """One launch's workspace. Build it with `Session.begin`; `stage` is the directory the agent works in and `patches` the one directory of
    refused patches it may read."""

    def __init__(self, launch: Launch, workspace: Workspace, stage: Stage, base: Capture) -> None:
        self.launch, self.workspace, self.stage, self.base = launch, workspace, stage, base
        self.root = Path(os.path.realpath(launch.project))
        self.state = Path(launch.state)
        self.kept = Kept(self.state / "refused" / launch.session)
        self.reports = Reports(launch.notice)
        self.tracked = _tracked(self.root)
        self.held = frozenset(os.fsdecode(each.path) for each in workspace.initial.entries)
        self.decided: dict[bytes, tuple[bytes | None, bytes | None]] = {}
        self.escaped: set[bytes] = set()
        self.links = environment_links(self.root, stage.physical)
        self.closed = False

    @classmethod
    def begin(cls, launch: Launch) -> Session:
        """Stage the project, or take the stage this conversation kept, and recover what a crash of an earlier launch left half applied."""
        root = Path(os.path.realpath(launch.project))
        applying = Path(launch.state) / "applying" / f"{_digest(str(root))}.log"
        coding_apply.recover(root, applying)
        applying.unlink(missing_ok=True)
        workspace = Workspace(root)
        resumed = cls._resume(launch, workspace)
        stage, base, reported = resumed or (workspace.begin(), workspace.initial, [])
        session = cls(launch, workspace, stage, base)
        session.reports.seen.update(reported)
        return session

    @staticmethod
    def _record(launch: Launch) -> Path:
        return Path(launch.state) / "kept" / f"{_digest(launch.conversation)}.json"

    @classmethod
    def _resume(cls, launch: Launch, workspace: Workspace) -> tuple[Stage, Capture, list[str]] | None:
        """The stage a re-launch of the same conversation finds kept (4.4, step 6), with the base it was working from and what was reported about
        it already (a refusal reported once is not reported again), or ``None``."""
        record = cls._record(launch)
        if not launch.conversation or not record.is_file():
            return None
        try:
            saved = files.read_json(record, {})
            if files.version_of(saved) != KEPT_VERSION:
                raise ValueError("the kept record is not of a shape this launcher reads")
            kept, namespace = Path(saved["stage"]), Path(saved["namespace"])
            entries = tuple(CaptureEntry(bytes.fromhex(path), mode.encode(), bytes.fromhex(sha), size) for path, mode, sha, size in saved["base"])
            if kept.parent != namespace or namespace == workspace.directory or not namespace.name.startswith(places.STAGE_PREFIX):
                raise ValueError("the kept stage is not in a namespace of this account")
            stage = workspace.adopt(kept)
        except (OSError, ValueError, KeyError, TypeError):
            record.unlink(missing_ok=True)
            return None
        if not private_path.problem(namespace):
            shutil.rmtree(namespace, ignore_errors=True)
        record.unlink(missing_ok=True)
        return stage, replace(workspace.initial, entries=entries), list(saved.get("reported", []))

    @property
    def patches(self) -> str:
        """The real path of the directory of this session's refused patches: the one directory of the state root its profile may read."""
        return str(self.kept.directory)

    @property
    def reads(self) -> tuple[str, ...]:
        """The exact directories the profile reads for the environment links (a read grant of each, and no more)."""
        return tuple(found.path for _, found in self.links)

    def binds(self) -> tuple[tuple[str, str, bool], ...]:
        return binds(self.root, str(self.stage.physical), self.patches)

    def first_line(self) -> str:
        """The first line of a session: how many files the working copy does not carry, each with its name and why (W3); empty when there are none."""
        left = self.workspace.left_out()
        names = frozenset(os.fsdecode(name) for name, _ in left)
        listed = ", ".join(f"{publication.token(Refusal(os.fsdecode(name), 'special'), index, names)} ({why})" for index, (name, why) in enumerate(left, 1))
        return f"{len(left)} file{'' if len(left) == 1 else 's'} left out of the working copy: {listed}" if left else ""

    def _news(self, captured: Capture, made: tuple[bytes, ...]) -> list[tuple[bytes, str]]:
        """The names this turn adds to what the capture leaves out (a control path, a credential, a special file), each once for the session."""
        controls = {fold(name) for name in CONTROL_PREFIXES}
        found = [(name, "control" if fold(os.fsdecode(name)).split("/")[0] in controls else "credential") for name in made]
        found += [(name, "special") for name, why in captured.left_out if (name, why) not in self.base.left_out]
        fresh = [(name, why) for name, why in found if name not in self.escaped]
        self.escaped.update(name for name, _ in fresh)
        if any(why == "control" for _, why in fresh) and self.launch.notice.events is not None:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                self.launch.notice.events("isolation.escape", {"severity": "warning", "policy": "isolation", "kind": "a control path appeared in the stage"})
        return fresh

    def publish_turn(self) -> Turn:
        """Publish what the stage holds now: apply the plain changes, refuse the rest at once, report once. Safe to call at every turn."""
        with self.workspace.lock:
            return self._turn()

    def _turn(self) -> Turn:
        try:
            captured, made = self.workspace.observe(self.stage, self.base)
        except (RuntimeError, TimeoutError):
            return Turn(deferred=DEFERRED, text=DEFERRED)
        changes = tuple(each for each in between(self.base, captured) if self.decided.get(each.path) != (each.after, each.after_mode))
        basis = publication.Basis(self.root, captured, self.tracked, self.launch.rules)
        decision = publication.decide(basis, changes, self._news(captured, made))
        done = coding_apply.apply(self.root, decision.edits, log=self.state / "applying" / f"{_digest(str(self.root))}.log",
                                  keep=self.kept.directory / "kept") if decision.edits else []
        refused = [*decision.refused, *(Refusal(each.path, "path") for each in done if each.state == "refused")]
        conflicts = [each.path for each in done if each.state == "conflict"]
        settled = {os.fsencode(each.path) for each in done if each.state in ("applied", "unchanged")}
        by_path = {each.path: each for each in changes}
        self.decided.update({os.fsencode(each.path): (by_path[os.fsencode(each.path)].after, by_path[os.fsencode(each.path)].after_mode)
                             for each in (*refused, *(Refusal(path, "conflict") for path in conflicts)) if os.fsencode(each.path) in by_path})
        self._advance(captured, settled)
        patch = self.kept.write(publication.patch_text(basis, refused)) if refused else None
        applied = tuple(each.path for each in done if each.state == "applied")
        text = words(len(applied), refused, self.held, patch, conflicts) if applied or refused or conflicts else ""
        pending = [*refused, *(Refusal(path, "conflict") for path in conflicts)]
        status = self.reports.send(pending, text) if pending else ""
        self.launch.log("isolation.publication", subject=self.launch.session, outcome="refused" if pending else "applied" if applied else "unchanged",
                        meta={"applied": [_digest(path) for path in applied], "refused": [_digest(each.path) for each in refused],
                              "conflicts": [_digest(path) for path in conflicts], "patch": patch.digest if patch else "", "report": status})
        return Turn(applied, tuple(refused), tuple(conflicts), patch, text, status)

    def _advance(self, captured: Capture, settled: set[bytes]) -> None:
        """The base moves to what was published: the settled paths take the stage's entry (or leave the base when the stage deleted them), and the
        paths refused or kept stay as they were, so they are compared again only when the stage's version of them changes."""
        held = {each.path: each for each in self.base.entries}
        now = {each.path: each for each in captured.entries}
        for path in settled:
            if path in now:
                held[path] = now[path]
            else:
                held.pop(path, None)
        self.base = replace(self.base, entries=tuple(held[path] for path in sorted(held)), left_out=captured.left_out, excluded=captured.excluded)

    def finish(self, tree: process_tree.Tree | None, *, stalled: float, ended: process_tree.Ended | None = None) -> Turn:
        """End every process of the session, then publish the last turn: the final capture is taken only after the tree is gone (4.2 item 3).

        ``tree`` is the follower's record of the confinement (macOS); ``ended`` is what a backend that ends the session by its own namespace or job
        reports (Linux, Windows), in place of the walk. A survivor, or a tree that was not complete, skips the publication and keeps the stage, and
        the launching session is told in the parent's words. ``stalled`` is the seconds without progress that end the barrier: the caller measures it."""
        result = ended or (process_tree.end(tree, stalled=stalled) if tree is not None else process_tree.Ended(unseen=True))
        if tree is not None:
            process_tree.release(tree)
        if result.clean:
            turn = self.publish_turn()
        else:
            text = f"{HEAD} {SURVIVOR}"
            turn = Turn(text=text, report=self.reports.send([Refusal("(processes)", "survivor")], text), deferred=SURVIVOR)
            self.launch.log("isolation.publication", subject=self.launch.session, outcome="skipped",
                            meta={"survivors": len(result.survivors), "unseen": result.unseen})
        self._settle(keep=bool(self.decided) or bool(turn.deferred))
        return turn

    def discard(self, tree: process_tree.Tree | None, *, stalled: float) -> Turn:
        """End every process of the session and drop the stage without publishing it: what the session of a stopped launch had made and not yet
        published is not applied, and nothing of it is kept for a re-launch. The patches already kept for the session stay, with their reports."""
        if tree is not None:
            process_tree.end(tree, stalled=stalled)
            process_tree.release(tree)
        self._settle(keep=False)
        return Turn()

    def _settle(self, *, keep: bool) -> None:
        """Close the session's directories. A stage with refused or unpublished work is kept, with its base, for a re-launch of the same
        conversation; any other stage is removed."""
        self.closed = True
        self.kept.close()
        for _, found in self.links:
            found.close()
        namespace = self.workspace.directory
        if keep and self.launch.conversation:
            record = self._record(self.launch)
            record.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            rows = [[each.path.hex(), each.mode.decode(), each.sha256.hex(), each.size] for each in self.base.entries]
            files.write_json(record, files.versioned({"stage": str(self.stage.physical), "namespace": str(namespace), "base": rows,
                                                      "reported": sorted(self.reports.seen)}, KEPT_VERSION))
            record.chmod(0o600)
        else:
            shutil.rmtree(namespace, ignore_errors=True)
        with contextlib.suppress(OSError):
            self.kept.directory.rmdir()
