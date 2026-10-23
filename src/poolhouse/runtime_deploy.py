"""Build a runtime from one commit, verify it, select it, recover from a broken selection and roll back."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from poolhouse import (
    node_build,
    person,
    runtime,
    runtime_bridges,
    runtime_git,
    runtime_identity,
    runtime_launchers,
    runtime_store,
    runtime_unmerged,
)
from poolhouse.board import session
from poolhouse.board.client import Conflict, NodeError
from poolhouse.files import writing
from poolhouse.fleet import runtime_wheel
from poolhouse.fleet.environment import Environment
from poolhouse.lock import Busy, only_one
from poolhouse.runtime_source import (
    FLOOR,
    DeployError as DeployError,
    admit,
    floor_of,
    parse_epoch,
    resolve_commit,
)

BUILD_TIMEOUT = 1800.0
CLAIM_WAIT_S = 60.0
CLAIM_TTL_S = 3600.0
CLAIM_POLL_S = 1.0
SMOKE_TIMEOUT = 60.0
HOOKS = ("claude-session-start", "claude-session-end", "claude-subagent-start", "claude-subagent-stop")
FAULTS = ("Traceback", "ModuleNotFoundError", "No module named", "ImportError")
FAILURES = (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError)


RECOVERABLE = (*FAILURES, DeployError)
BACKOFF_S = 600.0


@dataclass(frozen=True, slots=True)
class Plan:
    """What to deploy: the source checkout, the commit built from it, the launcher directory (or None) and the epoch floor."""

    checkout: Path
    commit: str
    launchers: Path | None
    floor: int = 0
    timeout: float = BUILD_TIMEOUT
    wait_s: float = CLAIM_WAIT_S
    agent: str = ""
    allow_unmerged: bool | runtime_unmerged.Approval = False
    automatic_from: tuple[str, ...] | None = None
    boundary_wait_s: float = 0.0


class Superseded(DeployError):
    """An automatic refresh no longer owns the selection it observed."""


def automatic_selection() -> tuple[str, ...]:
    """Snapshot the fully verified runtime an automatic refresh may advance."""
    chosen = runtime.available()
    return (str(chosen.prefix), chosen.commit, chosen.version, chosen.identity, str(runtime_store.epoch_of(chosen.prefix))) if chosen else ()


def _automatic(plan: Plan) -> None:
    if plan.automatic_from is None:
        return
    current = automatic_selection()
    if (not current or current != plan.automatic_from
            or resolve_commit(plan.checkout) != plan.commit
            or runtime_git.git(plan.checkout, "merge-base", current[1], plan.commit, check=False) != current[1]):
        raise Superseded("automatic refresh was superseded; the selected runtime was preserved")


@dataclass(frozen=True, slots=True)
class Outcome:
    """What an ensure, recover or rollback did."""

    action: str
    commit: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.action != "failed"


Builder = Callable[[Plan, Path, Path], runtime.Runtime]


def source_epoch(source: Path) -> int:
    """The runtime epoch a built source tree declares."""
    path = source / FLOOR
    return parse_epoch(path.read_text(encoding="utf-8")) if path.is_file() else 0


def acting(plan: Plan) -> session.Session | None:
    """The session of the workspace agent running this command, on the board of the plan's checkout; None when there
    is no agent or the node does not know its session."""
    name = plan.agent or os.environ.get(session.AGENT_ENV, "")
    if not name:
        return None
    try:
        held = session.connect(agent=name, cwd=plan.checkout)
        return held if held.whoami().name == name else None
    except (NodeError, OSError, ValueError):
        return None


def _needs_session(who: session.Session | None) -> None:
    if who is None and person.marked():
        raise DeployError("runtime deployment needs an authenticated workspace agent for its install claims")


@contextmanager
def owning(paths: list[Path], *, wait_s: float, who: session.Session | None) -> Iterator[frozenset[Path]]:
    """Hold an `install` claim as ``who`` on each path of this device, waiting for a live other owner up to ``wait_s`` for each.

    With no session there is no claim; the deploy lock alone serialises builds.
    """
    _needs_session(who)
    if who is None:
        yield frozenset()
        return
    wanted = list(dict.fromkeys(paths))
    taken: list[Path] = []
    try:
        deadline = time.monotonic() + wait_s
        while wanted:
            try:
                rows = who.reserve([("install", str(path)) for path in wanted],
                                   ttl_s=int(CLAIM_TTL_S), pid=os.getpid())
                taken = [Path(row.key) for row in rows if row.changed and row.kind == "install"
                         and row.owner == who.name and row.key in {str(path) for path in wanted}]
                if (len(rows) != len(wanted) or {row.key for row in rows} != {str(path) for path in wanted}
                        or any(row.kind != "install" or row.owner != who.name for row in rows)):
                    raise DeployError("runtime deployment received an invalid install reservation")
                break
            except Conflict as held:
                if time.monotonic() >= deadline:
                    raise DeployError(f"runtime install reservation is claimed by {str(held).rpartition(' is held by ')[2]}") from held
                time.sleep(CLAIM_POLL_S)
        yield frozenset(wanted)
    finally:
        for path in taken:
            with suppress(NodeError, OSError):
                who.release("install", str(path))


def host_python() -> Path:
    """The Python that builds runtime environments."""
    found = Environment(runtime.directory() / "bootstrap").host_python()
    if found is None:
        raise OSError("install Python 3.13 to prepare an isolated runtime")
    return runtime_identity.unbound(found)


def build(plan: Plan, stage: Path, prefix: Path) -> runtime.Runtime:
    """Build the plan's commit into a new immutable runtime tree at `prefix`."""
    creator = runtime_store.creator_record(plan.agent or os.environ.get(session.AGENT_ENV, ""), "ensure")
    target = runtime_wheel.Target(prefix, creator, host_python())
    built = runtime_wheel.build(plan.checkout, plan.commit, stage, timeout=plan.timeout, target=target)
    try:
        runtime_identity.adopt(built)
        node_build.add_to(built, stage / "source", plan.commit, timeout=plan.timeout)
    except RECOVERABLE:
        runtime_store.discard(built.prefix)
        raise
    return built


def _clean_environment(home: Path, bin_dir: Path) -> dict[str, str]:
    kept = {key: value for key, value in runtime.environment().items()
            if not key.startswith(("POOLHOUSE_", "POOLHOUSE_")) and key not in {"CLAUDE_ENV_FILE", "CLAUDE_CONFIG_DIR"}}
    return {**kept, "HOME": str(home), "POOLHOUSE_HOME": str(home / "state"), 
            "POOLHOUSE_NONINTERACTIVE": "1", "POOLHOUSE_NOTIFY": "off", "POOLHOUSE_RUNTIME_ENSURE": "off",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}


def smoke(chosen: runtime.Runtime, source: Path, stage: Path) -> None:
    """Import the runtime under -I, run its CLI help and the Claude hooks against a temporary state root."""
    runtime.verify(chosen)
    home, bin_dir = stage / "smoke-home", stage / "smoke-bin"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    runtime_launchers.install(bin_dir, chosen, follow_selection=False)
    env = _clean_environment(home, bin_dir)
    done = subprocess.run([str(bin_dir / "poolhouse-workspace"), "--help"], capture_output=True, text=True,
                          timeout=SMOKE_TIMEOUT, env=env, cwd=home)
    if done.returncode or not done.stdout.strip():
        raise DeployError(f"poolhouse-workspace --help exited {done.returncode}: {done.stderr.strip()[-300:]}")
    event = json.dumps({"session_id": "runtime-smoke", "cwd": str(home), "model": "runtime-smoke",
                        "agent_id": "smoke", "agent_type": "smoke", "description": "runtime smoke"})
    for name in HOOKS:
        script = source / "scripts" / "hooks" / name
        if not script.is_file():
            continue
        done = subprocess.run([str(chosen.python), str(script)], input=event, capture_output=True, text=True,
                              timeout=SMOKE_TIMEOUT, env={**env, "CLAUDE_PROJECT_DIR": str(source)}, cwd=home)
        if done.returncode or any(fault in done.stderr for fault in FAULTS):
            raise DeployError(f"hook {name} exited {done.returncode}: {done.stderr.strip()[-300:]}")


def _note(update: dict) -> None:
    with suppress(OSError):
        runtime_store.write_state(update)


def _record(plan: Plan) -> None:
    _note({"checkout": str(plan.checkout), "launchers": str(plan.launchers) if plan.launchers else None})


def switch(chosen: runtime.Runtime, plan: Plan, *, claimed: frozenset[Path] = frozenset()) -> None:
    """Prepare owned commands and atomically publish the selected runtime."""
    with runtime_git.crossing(plan.checkout, note="runtime cutover"):
        _automatic(plan)
        admit(plan.checkout, chosen.commit, allow_unmerged=plan.allow_unmerged if chosen.commit == plan.commit else False)
        floor = max(plan.floor, floor_of(plan.checkout, resolve_commit(plan.checkout)))
        if runtime_store.epoch_of(chosen.prefix) < floor:
            raise DeployError(f"runtime epoch is below the source floor {floor}")
        expected = runtime_bridges.planned(plan.checkout, chosen.commit, plan.launchers)
        if expected:
            runtime.verify(chosen)
            if runtime_launchers.entrypoints(chosen) != runtime_bridges.declared(plan.checkout, chosen.commit):
                raise DeployError("runtime command metadata does not match the reviewed source")
        original = _snapshot(plan.launchers)
        bridges = runtime_bridges.snapshot(expected, claimed)
        published = False
        try:
            runtime_identity.adopt(chosen)
            if plan.launchers is not None:
                if plan.launchers == runtime_launchers.directory():
                    runtime.plain(plan.launchers)
                    plan.launchers.mkdir(mode=0o700, parents=True, exist_ok=True)
                    runtime._owned(plan.launchers)
                runtime_launchers.install(plan.launchers, chosen, follow_selection=plan.launchers == runtime_launchers.directory())
            runtime_bridges.install(expected, bridges)
            _automatic(plan)
            runtime.publish(chosen)
            published = True
            _record(plan)
        finally:
            if not published:
                try:
                    _restore(plan.launchers, original)
                finally:
                    runtime_bridges.restore(bridges, expected)


def _switch_paths(plan: Plan, commit: str = "") -> list[Path]:
    bridges = runtime_bridges.planned(plan.checkout, commit or plan.commit, plan.launchers)
    return [*([plan.launchers] if plan.launchers is not None else []), runtime.directory() / "selected.json",
            *runtime_bridges.changes(bridges)]


def _snapshot(directory: Path | None) -> dict[Path, tuple[bytes, int]]:
    """The bytes and mode of every console launcher in a directory."""
    if directory is None:
        return {}
    return {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for path in directory.glob("poolhouse*") if path.is_file() and not path.is_symlink()}


def _restore(directory: Path | None, original: dict[Path, tuple[bytes, int]]) -> None:
    """Put the snapshotted launchers back and remove console launchers that were not there."""
    if directory is None:
        return
    with suppress(OSError):
        for path in directory.glob("poolhouse*"):
            if path not in original and path.is_file() and not path.is_symlink():
                path.unlink()
        for path, (data, mode) in original.items():
            with writing(path) as temporary:
                temporary.write_bytes(data)
                temporary.chmod(mode)


def build_and_switch(plan: Plan, builder: Builder = build, who: session.Session | None = None) -> runtime.Runtime:
    """Build, smoke and select the plan's commit; the install claims are taken for the cutover only, so a build that fails,
    or a cutover that finds the boundary held, writes no row. A failure before the selection keeps the previous runtime."""
    who = who or acting(plan)
    _needs_session(who)
    prefix = runtime.directory() / plan.commit / uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="poolhouse-runtime-") as temporary:
        stage = Path(temporary).resolve()
        built, published, deferred = builder(plan, stage, prefix), False, False
        try:
            runtime_store.mark_created(built.prefix, who.name if who else plan.agent or os.environ.get(session.AGENT_ENV, ""), "ensure")
            if built.commit != plan.commit:
                raise DeployError(f"built {built.commit}, expected {plan.commit}")
            smoke(built, stage / "source", stage)
            epoch = source_epoch(stage / "source")
            if epoch < plan.floor:
                raise DeployError(f"built epoch {epoch} is below the floor {plan.floor}")
            runtime_store.mark_verified(built, epoch, node_build.mark(built.prefix))
            try:
                runtime_git.until_free(plan.checkout, 0.0)
                with owning(_switch_paths(plan), wait_s=plan.wait_s, who=who) as claimed:
                    switch(built, plan, claimed=claimed)
            except Busy:
                deferred = True
                raise
            published = True
        finally:
            if not published and not deferred:
                runtime_store.discard(built.prefix)
        return built


def acceptable(plan: Plan, chosen: runtime.Runtime) -> bool:
    """Whether a runtime is not rejected and carries at least the plan's epoch floor."""
    return not runtime_store.rejected(chosen.prefix) and runtime_store.epoch_of(chosen.prefix) >= plan.floor


def healthy(plan: Plan) -> runtime.Runtime | None:
    """The selected runtime when it verifies and meets the floor, else None."""
    chosen = runtime.available()
    return chosen if chosen is not None and acceptable(plan, chosen) else None


def fallback(plan: Plan) -> runtime.Runtime | None:
    """The newest verified runtime that still verifies and meets the floor."""
    held = runtime_store.read_state().get("held")
    held = held.get("commit") if isinstance(held, dict) else None
    ranked = sorted(runtime_store.candidates(), key=lambda candidate: candidate.commit == held)
    for candidate in ranked:
        if runtime_store.epoch_of(candidate.prefix) < plan.floor:
            continue
        try:
            return runtime.verify(candidate)
        except FAILURES:
            continue
    return None


def settled(plan: Plan) -> str:
    """"current" or "held" when the selection file names an intact tree that needs no build, else empty; runs nothing."""
    row = runtime_store.selection()
    prefix = Path(str(row.get("prefix", "")))
    if not runtime_store.intact(prefix) or runtime_store.epoch_of(prefix) < plan.floor:
        return ""
    if row.get("commit") == plan.commit:
        return "current"
    held = runtime_store.read_state().get("held")
    return "held" if isinstance(held, dict) and held.get("commit") == plan.commit else ""


def current(plan: Plan) -> bool:
    """Whether the selection file already names this commit's intact tree."""
    return settled(plan) == "current"


def _reject_below_floor(plan: Plan, keep: Path) -> None:
    for candidate in runtime_store.candidates():
        if candidate.prefix != keep and runtime_store.epoch_of(candidate.prefix) < plan.floor:
            runtime_store.reject(candidate.prefix, "below the source checkout's runtime floor")


def prepare_root() -> Path:
    root = runtime.directory()
    if not root.exists():
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.mkdir(mode=0o700)
        runtime.protect(root)
    return root


def ensure(plan: Plan, *, force: bool = False, force_build: bool = False, builder: Builder = build,
           after_wait: Callable[[], None] | None = None) -> Outcome:
    """Make the plan's commit the selected runtime, recovering a broken selection first; ``after_wait`` runs once the boundary wait is over."""
    state = "" if force else settled(plan)
    if state:
        held = runtime_store.read_state().get("held") or {}
        return Outcome(state, plan.commit, "; ".join(filter(None, [str(held.get("reason", "")) if state == "held" else "", *_selection_identity(plan)])))
    root = prepare_root()
    try:
        if plan.boundary_wait_s > 0:
            runtime_git.until_free(plan.checkout, plan.boundary_wait_s)
            if after_wait is not None:
                after_wait()
        with only_one(root / "deploy.lock", wait=False, note="ensure"):
            _automatic(plan)
            return _ensured(plan, force, force_build, builder)
    except Superseded as exc:
        return Outcome("superseded", plan.commit, str(exc))
    except Busy as exc:
        return Outcome("busy", plan.commit, str(exc))


def _recovered(plan: Plan, who: session.Session | None) -> tuple[runtime.Runtime | None, list[str]]:
    chosen = healthy(plan)
    if chosen is not None:
        return chosen, []
    chosen = fallback(plan)
    if chosen is None:
        return None, []
    try:
        with owning(_switch_paths(plan, chosen.commit), wait_s=plan.wait_s, who=who) as claimed:
            switch(chosen, plan, claimed=claimed)
    except RECOVERABLE as exc:
        return chosen, [f"could not select {chosen.commit[:7]}: {exc}"]
    return chosen, [f"selected {chosen.commit[:7]} in place of a broken selection"]


def binder(plan: Plan) -> str:
    """The one command that binds a runtime to the Poolhouse executable from the installed software: the launcher the plan installs."""
    return f"{plan.launchers / 'poolhouse' if plan.launchers is not None else 'poolhouse'} runtime ensure"


def _identity(chosen: runtime.Runtime, plan: Plan) -> list[str]:
    """What it took to make the selected runtime run as Poolhouse, or why it cannot (here, or because this process is not the
    installed software and says so); a failure never takes a working runtime down."""
    try:
        done = runtime_identity.adopt(chosen)
    except runtime_identity.IdentityError as exc:
        _note({"identity_failure": {"commit": chosen.commit, "at": time.time(), "detail": str(exc)[-1000:]}})
        return [f"not running as Poolhouse: {exc}"]
    _note({"identity_failure": None})
    note = done or runtime_identity.unbound_note(chosen, binder(plan))
    return [note] if note else []


def _selection_identity(plan: Plan) -> list[str]:
    """`_identity` for the selected runtime, which costs a start of the interpreter only while it does not run as Poolhouse yet."""
    row = runtime_store.selection()
    prefix = Path(str(row.get("prefix", "")))
    if not runtime_identity.applies():
        chosen = runtime.available()
        return [note] if chosen is not None and (note := runtime_identity.unbound_note(chosen, binder(plan))) else []
    if runtime_identity.bound(prefix, runtime.identity()):
        return []
    failure = runtime_store.read_state().get("identity_failure")
    if isinstance(failure, dict) and time.time() - float(failure.get("at", 0.0)) < BACKOFF_S:
        return [f"not running as Poolhouse: {failure.get('detail', '')} (tried again after {BACKOFF_S:.0f}s)"]
    chosen = runtime.available()
    return _identity(chosen, plan) if chosen is not None else []


def _failed_recently(plan: Plan) -> bool:
    failure = runtime_store.read_state().get("last_failure")
    return (isinstance(failure, dict) and failure.get("commit") == plan.commit
            and time.time() - float(failure.get("at", 0.0)) < BACKOFF_S)


def _ensured(plan: Plan, force: bool, force_build: bool, builder: Builder) -> Outcome:
    who = acting(plan)
    if force_build and who is None:
        raise DeployError("--force-build needs an authenticated agent: pass --agent or set POOLHOUSE_WORKSPACE_AGENT")
    chosen, steps = (healthy(plan), []) if plan.automatic_from is not None else _recovered(plan, who)
    held = runtime_store.read_state().get("held")
    if not force and chosen is not None and chosen.commit == plan.commit:
        runtime_store.collect({chosen.prefix})
        return Outcome("recovered" if steps else "current", plan.commit, "; ".join([*steps, *_identity(chosen, plan)]))
    if not force and chosen is not None and isinstance(held, dict) and held.get("commit") == plan.commit:
        return Outcome("recovered" if steps else "held", plan.commit, "; ".join([*steps, str(held.get("reason", ""))]))
    if not force_build and _failed_recently(plan):
        return Outcome("recovered" if steps else "backoff", plan.commit,
                       "; ".join([*steps, f"the last build of {plan.commit[:7]} failed; retrying after {BACKOFF_S:.0f}s or with --force-build"]))
    try:
        built = build_and_switch(plan, builder, who)
    except Superseded as exc:
        return Outcome("superseded", plan.commit, str(exc))
    except RECOVERABLE as exc:
        _note({"last_failure": {"commit": plan.commit, "at": time.time(), "detail": str(exc)[-1000:]}})
        return Outcome("recovered" if steps else "failed", plan.commit, "; ".join([*steps, f"build failed: {exc}"])[-1500:])
    _note({"last_failure": None, "held": None, "last_success": {"commit": plan.commit, "at": time.time()}})
    _reject_below_floor(plan, built.prefix)
    runtime_store.collect({built.prefix, *([chosen.prefix] if chosen else [])})
    detail = [*steps, f"selected {built.prefix}", *([] if plan.launchers else ["launchers not updated"]),
              *filter(None, [runtime_identity.unbound_note(built, binder(plan))])]
    return Outcome("switched", plan.commit, "; ".join(detail))


def rollback(plan: Plan, to: str = "") -> Outcome:
    """Select an earlier verified runtime and hold the commit that was selected."""
    root = prepare_root()
    try:
        with only_one(root / "deploy.lock", wait=False, note="rollback"):
            who = acting(plan)
            if who is None:
                raise DeployError("rollback needs an authenticated agent: pass --agent or set POOLHOUSE_WORKSPACE_AGENT")
            now = runtime_store.selection(root)
            target = next((c for c in runtime_store.candidates(root)
                           if str(c.prefix) != now.get("prefix") and (not to or c.commit.startswith(to))), None)
            if target is None:
                return Outcome("failed", detail="no earlier verified runtime to select")
            try:
                runtime.verify(target)
                with owning(_switch_paths(plan, target.commit), wait_s=plan.wait_s, who=who) as claimed:
                    switch(target, plan, claimed=claimed)
            except RECOVERABLE as exc:
                return Outcome("failed", target.commit, str(exc)[-500:])
            if now.get("commit"):
                _note({"held": {"commit": now["commit"], "reason": f"rolled back to {target.commit[:7]}"}})
            return Outcome("switched", target.commit, "; ".join(filter(None, [f"selected {target.prefix}", runtime_identity.unbound_note(target, binder(plan))])))
    except Busy as exc:
        return Outcome("busy", detail=str(exc))
