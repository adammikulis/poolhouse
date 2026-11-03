"""Which commit a runtime deploy may build, who may redirect it, and the audit record every deploy command leaves."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from poolhouse import authority, home, node_launch, person, runtime_git, runtime_store
from poolhouse.activity import writer
from poolhouse.board import session as board_session
from poolhouse.board.client import Client, NodeError
from poolhouse.runtime_deploy import DeployError, floor_of

KIND = "runtime.deploy"
GATE = "runtime.deploy"
DETAIL_LIMIT = 150


def is_person() -> bool:
    """Whether stdin and stdout are terminals and no agent marker is set."""
    return not person.marked() and person.is_terminal(sys.stdin) and person.is_terminal(sys.stdout)


def common_dir(path: Path) -> Path:
    """The git directory shared by every worktree of the repository at `path`, read from files without running git."""
    link = path / ".git"
    if link.is_dir():
        return link.resolve()
    try:
        text = link.read_text(encoding="utf-8").strip() if link.is_file() else ""
    except (OSError, ValueError):
        text = ""
    if not text.startswith("gitdir:"):
        raise DeployError(f"{path} is not a git checkout")
    gitdir = Path(text.split(":", 1)[1].strip())
    gitdir = gitdir if gitdir.is_absolute() else path / gitdir
    pointer = gitdir / "commondir"
    if pointer.is_file():
        target = Path(pointer.read_text(encoding="utf-8").strip())
        gitdir = target if target.is_absolute() else gitdir / target
    return gitdir.resolve()


def main_worktree(path: Path) -> Path:
    """The primary checkout of the repository that holds `path`."""
    shared = common_dir(path)
    if shared.name != ".git":
        raise DeployError(f"{path} belongs to a repository with no primary checkout")
    return shared.parent


def recorded_primary() -> Path | None:
    """The primary checkout of the source recorded by an earlier deploy, or None."""
    named = runtime_store.read_state().get("checkout")
    if not isinstance(named, str) or not named:
        return None
    try:
        return main_worktree(home.expand(named).resolve())
    except DeployError:
        return None


def primary_for(supplied: Path, *, deploying: bool = True, delegated: bool = False) -> Path:
    """The primary checkout to deploy from: the recorded one when `supplied` is a worktree of it.

    A person may name another repository. A lead agent that passed the delegated `runtime.deploy` gate (``delegated``)
    may record the first one on a machine with none; any other process that deploys must use the one already recorded.
    """
    recorded = recorded_primary()
    if recorded is not None and common_dir(supplied) == common_dir(recorded):
        return recorded
    if is_person() or not deploying or (recorded is None and delegated):
        return main_worktree(supplied)
    if recorded is None:
        raise DeployError("no source repository is recorded: a person, or a lead agent while `runtime.deploy` is delegated, "
                          "runs `poolhouse runtime ensure --checkout PATH`")
    raise DeployError(f"{supplied} is not a worktree of the recorded repository {recorded}; another source repository requires person authentication")


def _git(primary: Path, *words: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(primary), *words], capture_output=True, text=True, timeout=60, env=runtime_git.environment())


def tip(primary: Path) -> str:
    """The commit the primary checkout's development branch points at."""
    if _git(primary, "symbolic-ref", "-q", "HEAD").returncode:
        raise DeployError(f"{primary} is on a detached HEAD; check out the development branch")
    done = _git(primary, "rev-parse", "HEAD")
    if done.returncode:
        raise DeployError(f"git rev-parse HEAD failed in {primary}: {done.stderr.strip()[:200]}")
    return done.stdout.strip()


def floor(primary: Path) -> int:
    """The runtime epoch floor declared at the development branch tip."""
    return floor_of(primary, tip(primary))


def ignore_redirects() -> list[str]:
    """Remove agent redirects; return only those that differ from the account's state root."""
    if not person.marked():
        return []
    names = [home.ROOT_ENV, home.CACHE_ENV, *home.OVERRIDES.values()]
    warnings = []
    for name in names:
        value = os.environ.pop(name, None)
        if not value:
            continue
        if name == home.ROOT_ENV:
            try:
                if home.expand(value).resolve() == home.account_roots()[0].resolve():
                    continue
            except (OSError, RuntimeError, ValueError):
                pass
        warnings.append(name)
    return warnings


def _own_session(cwd: Path | None) -> None:
    """A lead agent whose token is gone, or that the node no longer knows, is registered again from the native session id its harness set
    (`board.session.revive`) before its authority is read, so a deploy never asks a person for a session the harness still runs. A session
    that cannot be (a subagent, one retired for cause) is left to `authority.require`, which says so."""
    try:
        board_session.connect(cwd=cwd, client=Client(node_launch.default_state(), autostart=False)).whoami()
    except (NodeError, OSError, ValueError):
        return


def authorize(command: str, *, cwd: Path | None = None) -> str:
    """Who may run a deploy command here: "person" for a process no agent started, "delegated" for a lead agent
    while the owner has delegated the `runtime.deploy` gate; HumanRequired for an agent while it is the person's.

    A launcher or login unit carries no agent marker, so it is not gated; the `authority.use` row this writes for a
    delegated pass names the agent.
    """
    if not (person.marked() or os.environ.get(authority.SUBAGENT_ENV)):
        return authority.PERSON
    _own_session(recorded_primary() or cwd)
    return authority.require(GATE, f"runtime {command}", scope=authority.Scope(cwd=recorded_primary() or cwd))


IDENTITY =(*person.AGENT_MARKERS, board_session.AGENT_ENV, board_session.TOKEN_ENV, board_session.SUBAGENT_ENV, "POOLHOUSE_BOARD",
            "POOLHOUSE_SESSION_HARNESS", "CLAUDE_ENV_FILE")
"""The variables that name an agent to a process: a build started as the system carries none of them."""


def refresh() -> None:
    """Admit a background refresh. It needs no identity and no delegation, whether or not the node has a session for the caller (a lead
    whose registration ended on a node that will not reopen it has none): it builds a commit the recorded checkout's development branch has
    published (`runtime_source.admit`) and only moves the selection forward from the one it saw (`runtime_deploy` automatic snapshot), in
    a process that carries no agent (`IDENTITY`), so it is audited as `system:runtime`. A subagent starts none, and
    `runtime_cli.plan_from` refuses an authenticated helper."""
    if os.environ.get(authority.SUBAGENT_ENV):
        raise DeployError("a subagent starts no runtime build")


def audit(command: str, commit: str, result: str, *, agent: str = "", detail: str = "", **passed: str) -> bool:
    """Append a sealed activity record for one deploy command; True when it was written."""
    context = bool(person.marked() or agent or os.environ.get(board_session.AGENT_ENV) or os.environ.get("POOLHOUSE_WORKSPACE_TOKEN") or
                   os.environ.get(authority.SUBAGENT_ENV))
    who = ""
    if context:
        try:
            source = Path(passed["source"]) if passed.get("source") else recorded_primary()
            who = authority.current_agent(cwd=source).name
        except (OSError, ValueError):
            pass
    actor = f"agent:{who}" if who else "agent:unavailable" if context else "person" if is_person() else "system:runtime"
    return writer.record(KIND, actor=actor, subject=commit, outcome=result,
                         meta={"command": command, "agent": who, "person": not context and is_person(), "authority": passed.get("via", ""),
                               "detail": detail[:DETAIL_LIMIT]})
