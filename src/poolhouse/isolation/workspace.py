"""The workspace of one launch (docs/design-isolated-launcher.md, 4.2 and 4.4): the stage the agent sees, a publication after each turn, and the end.

`Session.begin` stages the person's project (or takes the stage an earlier launch of the same conversation kept), links the person's environment
folders into it, and opens the directory of this session's refused patches. `publish_turn` captures the stage, decides every change with
`isolation.publication`, applies them with `coding_apply` (which keeps first, under the state root and out of the session's reach, the
version of a file git does not hold that an edit displaces) and refuses at once the few `publication` does not apply: a patch kept for the session to read, and one report to
the launching session in the parent's words. The base advances to what was published, so each turn compares against the last one.
`finish` ends every process of the session first (`sandbox.process_tree.end`), and publishes only when none is left. Nothing here asks a question,
holds a change for an answer or waits for a person.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from poolhouse import activity, coding_apply, files, private_path, secretpaths
from poolhouse.activity import source_snapshot
from poolhouse.activity.source_snapshot import Capture, CaptureEntry
from poolhouse.coding_workspace import Stage, Workspace, between
from poolhouse.coding_workspace_state import CONTROL_PREFIXES
from poolhouse.isolation import environments, history, places, publication, toolchain
from poolhouse.isolation.notices import HEAD, Directory, Kept, Notice, Patch, Reports, Result, words
from poolhouse.isolation.publication import Refusal, fold
from poolhouse.sandbox import process_tree

__all__ = ["PATCHES_AT", "Launch", "Published", "Session", "Turn", "binds", "tally"]

PATCHES_AT = "/poolhouse/refused"
"""Where the Linux and WSL backends bind the session's patch directory inside the namespace, read-only."""
KEPT_VERSION = 1
"""The shape of the record a kept stage leaves for a re-launch of its conversation."""
Log = Callable[..., object]
SURVIVOR = "A process of the session could not be ended, so the stage may still be written and nothing more of it was published; the stage is kept."
DEFERRED = "The stage was written while it was read, so nothing was published this turn; it is read again at the next turn."
UNREAD = "The run failed and the stage could not be read, so nothing more was published and no patch of it was kept."


@dataclass(frozen=True, slots=True)
class Launch:
    """What one launch tells the workspace: the person's project, the state root, the session's name (the parent's own), the conversation whose kept
    stage a re-launch reuses, where reports go, the activity log and, for a session that commits in its copy, the name it commits under."""

    project: str
    state: str
    session: str
    notice: Notice
    conversation: str = ""
    log: Log = activity.record
    committer: str = ""


@dataclass(frozen=True, slots=True)
class Turn:
    """What one publication did: the paths applied, the refusals, the paths the person changed meanwhile, the patch, the words and what became of the
    report (``posted``, ``folded``, ``capped`` or ``line``); ``deferred`` says why nothing was published; ``restored`` gives, for each applied path whose
    previous version was kept first, the name that copy has in the session's restore directory."""

    applied: tuple[str, ...] = ()
    refused: tuple[Refusal, ...] = ()
    conflicts: tuple[str, ...] = ()
    patch: Patch | None = None
    text: str = ""
    report: str = ""
    deferred: str = ""
    restored: tuple[tuple[str, str], ...] = ()
    withheld: tuple[str, ...] = ()
    """The paths a failed session had changed and nothing published; ``patch`` keeps them."""
    runs_later: tuple[tuple[str, str], ...] = ()
    """The applied paths of the kind that runs later, each with why, for information: nothing of it was held back."""


@dataclass(frozen=True, slots=True)
class Published:
    """What the publications of one session came to, as data: each path where its last publication left it, the paths withheld from a failed
    session, why the last publication published nothing (empty when it ran) and every patch that was kept."""

    applied: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    withheld: tuple[str, ...] = ()
    deferred: str = ""
    patches: tuple[str, ...] = ()
    runs_later: tuple[str, ...] = ()
    """The applied paths of the kind that runs later."""


def tally(turns: Sequence[Turn]) -> Published:
    """The turns of one session, in order, as one `Published`: a later turn's word on a path replaces an earlier one's."""
    where: dict[str, str] = {}
    for turn in turns:
        for state, paths in (("applied", turn.applied), ("refused", tuple(each.path for each in turn.refused)),
                             ("conflicts", turn.conflicts), ("withheld", turn.withheld)):
            where.update(dict.fromkeys(paths, state))
    held = {state: tuple(sorted(path for path, found in where.items() if found == state)) for state in ("applied", "refused", "conflicts", "withheld")}
    later = {path for turn in turns for path, _ in turn.runs_later}
    return Published(**held, deferred=turns[-1].deferred if turns else "", patches=tuple(turn.patch.path for turn in turns if turn.patch),
                     runs_later=tuple(path for path in held["applied"] if path in later))


def _digest(value: str | bytes) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode(errors="surrogateescape")).hexdigest()[:32]


def binds(project: str | os.PathLike[str], stage: str, patches: str) -> tuple[tuple[str, str, bool], ...]:
    """The binds the Linux and WSL backends make inside the namespace: the stage at the person's own project path, so the absolute paths inside a
    virtual environment resolve with no link, and the session's patch directory, read-only, at a fixed path (source, target, writable)."""
    return ((stage, os.path.realpath(project), True), (patches, PATCHES_AT, False))


def environment_links(project: Path, stage: Path, unread: list[tuple[str, str]] | None = None) -> tuple[tuple[str, toolchain.Resolved], ...]:
    """Link each environment folder of the launch (`environments.folders`: the project's own, or a worktree's main checkout's) into the stage,
    a link to the person's own directory, which the profile reads and never writes. A folder that is a link, or that resolves outside the checkout
    that holds it, gets nothing, and a name the stage holds as anything but that link is left as it is. A folder in which the credential walk finds
    a credential file, or that is too large for it to check, is neither linked nor read, and its name and why are added to ``unread``. The walk
    does not enter `site-packages` or a nested environment (`secretpaths.SKIP_DIRS`), as for every grant."""
    made = []
    for name, root in environments.folders(Path(os.path.realpath(project))):
        found = toolchain.resolve(root / name)
        if found is None:
            continue
        inside = Path(found.path).is_relative_to(root)
        hit = secretpaths.tree_hit(Path(found.path), suffixes=False) if inside else None
        if hit is not None and unread is not None:
            unread.append((name, f"holds a credential file or is too large to check: {hit.rule}"))
        if not inside or hit is not None or not _linked(stage / name, found.path):
            found.close()
            continue
        made.append((name, found))
    return tuple(made)


def _is_link_to(path: Path, target: str) -> bool:
    return path.is_symlink() and str(path.readlink()) == target


def _linked(link: Path, target: str) -> bool:
    """Make ``link`` a link to ``target`` when nothing has the name; whether the stage holds that link now (a kept stage already does)."""
    if not os.path.lexists(link):
        link.symlink_to(target)
    return _is_link_to(link, target)


def _tracked(root: Path) -> frozenset[str]:
    """The paths git holds, from the index and never from the repository's configuration; nothing at all when it cannot be read, so every
    overwrite of a file git may not hold is refused."""
    try:
        return frozenset(os.fsdecode(name) for name in source_snapshot.git_output(["ls-files", "-z"], cwd=root).split(b"\0") if name)
    except (OSError, RuntimeError, TimeoutError):
        return frozenset()


class Session:
    """One launch's workspace. Build it with `Session.begin`; `stage` is the directory the agent works in and `patches` the one directory of
    refused patches it may read. `restores` is the directory of the versions a publication displaced and kept first: the session has no grant there."""

    def __init__(self, launch: Launch, workspace: Workspace, stage: Stage, base: Capture) -> None:
        self.launch, self.workspace, self.stage, self.base = launch, workspace, stage, base
        self.root = Path(os.path.realpath(launch.project))
        self.state = Path(launch.state)
        self.kept = Kept(self.state / "refused" / launch.session)
        self.restores = Directory(self.state / "restored" / launch.session)
        self.reports = Reports(launch.notice)
        self.tracked = _tracked(self.root)
        self.held = frozenset(os.fsdecode(each.path) for each in workspace.initial.entries)
        self.decided: dict[bytes, tuple[bytes | None, bytes | None]] = {}
        self.escaped: set[bytes] = set()
        self.unread: list[tuple[str, str]] = []
        self.links = environment_links(self.root, stage.physical, self.unread)
        self.closed = False
        self.start = ""
        """The commit the project was at when the session began."""

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
        if not resumed:
            history.give(root, stage.physical, workspace.excluded, launch.committer)   # what it could not give, the preflight finds in the box
        if launch.committer:
            workspace.pin(stage)
        session = cls(launch, workspace, stage, base)
        session.start = history.head(root)
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

    @property
    def sources(self) -> tuple[str, ...]:
        """Where Python of the box imports the project from (`environments.sources`)."""
        return environments.sources(self.stage.physical, {name for name, _ in self.links})

    def note(self) -> str:
        """The sentence the session's brief and result carry when the project asks for an environment the box has none of; empty otherwise."""
        return environments.note(self.root, {name for name, _ in self.links})

    def binds(self) -> tuple[tuple[str, str, bool], ...]:
        return binds(self.root, str(self.stage.physical), self.patches)

    def first_line(self) -> str:
        """The first line of a session: how many files the working copy does not carry, each with its name and why (W3), and the environment folders
        the session does not read; empty when there are neither."""
        left = self.workspace.left_out()
        names = frozenset(os.fsdecode(name) for name, _ in left)
        listed = ", ".join(f"{publication.token(Refusal(os.fsdecode(name), 'special'), index, names)} ({why})" for index, (name, why) in enumerate(left, 1))
        files_left = f"{len(left)} file{'' if len(left) == 1 else 's'} left out of the working copy: {listed}" if left else ""
        folders = ", ".join(f"{name} ({why})" for name, why in self.unread)
        return "; ".join(each for each in (files_left, f"not read by the session: {folders}" if folders else "") if each)

    def _news(self, captured: Capture, made: tuple[bytes, ...]) -> list[tuple[bytes, str]]:
        """The names this turn adds to what the capture leaves out (a control path, a credential, a special file, an environment link the session removed or
        replaced), each once for the session. A replaced link is found by looking at the stage: an ignore pattern for a folder hides what took its place."""
        controls = {fold(name) for name in CONTROL_PREFIXES}
        found = [(name, "control" if fold(os.fsdecode(name)).split("/")[0] in controls else "credential") for name in made]
        links = {os.fsencode(name): _is_link_to(self.stage.physical / name, held.path) for name, held in self.links}
        found += [(name, "environment") for name, kept in links.items() if not kept]
        found += [(name, "special") for name, why in captured.left_out if (name, why) not in self.base.left_out and name not in links]
        fresh = [(name, why) for name, why in found if name not in self.escaped]
        self.escaped.update(name for name, _ in fresh)
        if any(why == "control" for _, why in fresh) and self.launch.notice.events is not None:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                self.launch.notice.events("isolation.escape", {"severity": "warning", "policy": "isolation", "kind": "a control path appeared in the stage"})
        return fresh

    def publish_turn(self) -> Turn:
        """Publish what the stage holds now: apply the changes, refuse at once what is never applied, report once. Safe to call at every turn."""
        with self.workspace.lock:
            return self._turn()

    def _turn(self) -> Turn:
        try:
            captured, made = self.workspace.observe(self.stage, self.base)
        except (RuntimeError, TimeoutError):
            return Turn(deferred=DEFERRED, text=DEFERRED)
        changes = tuple(each for each in between(self.base, captured) if self.decided.get(each.path) != (each.after, each.after_mode))
        basis = publication.Basis(self.root, captured, self.tracked)
        decision = publication.decide(basis, changes, self._news(captured, made))
        places = coding_apply.Places(self.state / "applying" / f"{_digest(str(self.root))}.log", self.kept.directory / "kept", self.restores.descriptor)
        done = coding_apply.apply(self.root, decision.edits, places) if decision.edits else []
        refused = [*decision.refused, *(Refusal(each.path, "path") for each in done if each.state == "refused"),
                   *(Refusal(each.path, "restore") for each in done if each.state == "unkept")]
        conflicts = [each.path for each in done if each.state == "conflict"]
        restored = [(Refusal(each.path, "restored", os.stat(each.restored, dir_fd=self.restores.descriptor).st_size), each.restored) for each in done if each.restored]
        settled = {os.fsencode(each.path) for each in done if each.state in ("applied", "unchanged")}
        by_path = {each.path: each for each in changes}
        self.decided.update({os.fsencode(each.path): (by_path[os.fsencode(each.path)].after, by_path[os.fsencode(each.path)].after_mode)
                             for each in (*refused, *(Refusal(path, "conflict") for path in conflicts))
                             if os.fsencode(each.path) in by_path and each.klass != "restore"})
        self._advance(captured, settled)
        patch = self.kept.write(publication.patch_text(basis, refused)) if refused else None
        applied = tuple(each.path for each in done if each.state == "applied")
        named = self.held | {name for name, _ in self.links}   # an environment link's name is the launcher's own (`ENVIRONMENT_LINKS`)
        later = tuple((path, why) for path, why in decision.later if path in applied)
        result = Result(len(applied), refused, patch, conflicts, restored, str(self.restores.directory), later)
        text = words(result, named) if applied or refused or conflicts else ""
        pending = [*refused, *(Refusal(path, "conflict") for path in conflicts)]
        status = self.reports.send(pending, text) if pending else ""
        self.launch.log("isolation.publication", subject=self.launch.session, outcome="refused" if pending else "applied" if applied else "unchanged",
                        meta={"applied": [_digest(path) for path in applied], "refused": [_digest(each.path) for each in refused],
                              "conflicts": [_digest(path) for path in conflicts], "restored": [name for _, name in restored],
                              "patch": patch.digest if patch else "", "report": status})
        return Turn(applied, tuple(refused), tuple(conflicts), patch, text, status, restored=tuple((each.path, name) for each, name in restored),
                    runs_later=later)

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

    def discard(self, tree: process_tree.Tree | None, *, stalled: float, keep_patch: bool = False, ended_by: str = "") -> Turn:
        """End every process of the session and drop the stage without publishing it: what the session of a stopped or failed launch had made and
        not yet published is not applied, and nothing of it is kept for a re-launch. The patches already kept for the session stay, with their
        reports. With ``keep_patch`` (a failed launch) the unpublished changes are first written as one more patch, which the turn names.
        ``ended_by`` is the launcher's own reason for ending the session (a resource bound): it is in the turn's words and reported at once."""
        if tree is not None:
            process_tree.end(tree, stalled=stalled)
            process_tree.release(tree)
        turn = Turn()
        try:
            if keep_patch:
                with self.workspace.lock:
                    turn = self._withhold()
            if ended_by:
                text = f"{HEAD} {ended_by} {turn.text.removeprefix(HEAD).strip() or 'Nothing more was published.'}"
                turn = replace(turn, text=text, report=self.reports.send([Refusal(ended_by, "limit")], text))
        finally:
            self._settle(keep=False)
        return turn

    def _withhold(self) -> Turn:
        """The changes no publication has decided, kept as a patch and applied nowhere."""
        try:
            captured, made = self.workspace.observe(self.stage, self.base)
        except (RuntimeError, TimeoutError):
            return Turn(deferred=UNREAD, text=UNREAD)
        changes = tuple(each for each in between(self.base, captured) if self.decided.get(each.path) != (each.after, each.after_mode))
        basis = publication.Basis(self.root, captured, self.tracked)
        decision = publication.decide(basis, changes, self._news(captured, made))
        sizes = {os.fsdecode(each.path): each.size for each in captured.entries}
        kept = [*decision.refused, *(Refusal(each.path, "withheld", sizes.get(each.path, 0)) for each in decision.edits)]
        if not kept:
            return Turn()
        patch = self.kept.write(publication.patch_text(basis, kept))
        paths = tuple(each.path for each in kept)
        text = (f"{HEAD} The run failed, so nothing more was published. What it had changed ({len(paths)}) and had not published is kept as a patch, "
                f"digest {patch.digest[:16]}, at {patch.path}.")
        self.launch.log("isolation.publication", subject=self.launch.session, outcome="withheld",
                        meta={"withheld": [_digest(path) for path in paths], "patch": patch.digest})
        return Turn(patch=patch, text=text, withheld=paths)

    def _settle(self, *, keep: bool) -> None:
        """Close the session's directories. A stage with refused or unpublished work is kept, with its base, for a re-launch of the same
        conversation; any other stage is removed."""
        self.closed = True
        self.kept.close()
        self.restores.close()
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
        for each in (self.kept.directory, self.restores.directory):
            with contextlib.suppress(OSError):
                each.rmdir()
