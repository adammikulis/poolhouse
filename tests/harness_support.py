"""Fixtures for the harness-ancestry and push-guard tests: a repository one commit ahead of its remote's main, and the hook scripts run as real child processes in an environment that carries no agent marker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "scripts" / "hooks"
SESSION = "sess-harness-1"
ZERO = "0" * 40
ENV_DROPPED = ("CLAUDECODE", "POOLHOUSE_NONINTERACTIVE", "CLAUDE_CODE_SESSION_ATTENDED", "POOLHOUSE_AGENT",
               "POOLHOUSE_SESSION_ID", "POOLHOUSE_GUARD", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_CODE_SESSION_ID",
               "POOLHOUSE_WORKSPACE_AGENT", "CLAUDE_CONFIG_DIR")


def environment(state: Path, **more: str) -> dict[str, str]:
    """A child environment whose poolhouse state is ``state`` and that carries none of the markers an agent's harness sets."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_DROPPED}
    env.update(POOLHOUSE_HOME=str(state), **more)
    return env


def git(where: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(where), *args], check=True, text=True, capture_output=True,
                          env=env).stdout.strip()


def commit(where: Path, name: str) -> str:
    """Commit a new file called ``name`` and return the commit."""
    (where / name).write_text(name + "\n")
    git(where, "add", name)
    git(where, "commit", "-q", "-m", f"add {name}")
    return git(where, "rev-parse", "HEAD")


def repository(where: Path) -> Path:
    """A repository whose ``main`` is one commit ahead of ``origin/main``; the first commit is ``base``."""
    remote = where.with_name(where.name + "-remote.git")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(where)], check=True)
    commit(where, "base.txt")
    git(where, "remote", "add", "origin", str(remote))
    git(where, "push", "-q", "origin", "main")
    commit(where, "release.txt")
    return where


def tip(repo: Path, ref: str = "main") -> str:
    return git(repo, "rev-parse", ref)


def session_event(name: str = "UserPromptSubmit", session: str = SESSION, **more) -> dict:
    """The input Claude Code gives a session hook."""
    return {"hook_event_name": name, "session_id": session, "cwd": str(ROOT), **more}


def run_hook(script: str, event: dict, state: Path, **env: str) -> subprocess.CompletedProcess:
    """Run the hook script ``script`` of scripts/hooks on ``event`` as a child process of this one."""
    return subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(event), text=True, capture_output=True,
                          timeout=60, check=False, env=environment(state, **env))


def bind(state: Path, **more) -> subprocess.CompletedProcess:
    """The UserPromptSubmit hook records this process as the harness that runs a session."""
    return run_hook("claude-harness-bind", session_event(**more), state)


def under_a_harness(state: Path, **env: str) -> subprocess.CompletedProcess:
    """`scripts/hooks/harness-ancestry` run as a child of this process: exit 0 when a harness is above it."""
    return subprocess.run([sys.executable, str(HOOKS / "harness-ancestry")], text=True, capture_output=True, timeout=60,
                          check=False, env=environment(state, **env))


def recorded(state: Path) -> list[dict]:
    """The harness processes the hooks recorded under ``state``."""
    path = state / "person" / "harness-sessions.json"
    return json.loads(path.read_text())["processes"] if path.exists() else []
