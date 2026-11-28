"""What the daemon's phone API calls in the tools layer, handed to it by the command that starts it (`poolhouse.cli.daemon`): the
workspace board's agents and the local delegates. Each is the same operation the commands run."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from poolhouse import errors, home
from poolhouse.board import client as node_client, person, session as sessions

__all__ = ["WAIT_S", "board_agents", "start_delegate", "stop_delegate"]

WAIT_S = 180
"""Seconds a delegate command may take to answer: starting one admits it and may wait on the node."""
IDENTITY = (sessions.AGENT_ENV, sessions.TOKEN_ENV, sessions.SUBAGENT_ENV)
"""Variables that name an agent session; a command the person asks for from the phone runs as none of them."""


def board_agents(projects: Sequence[str]) -> list[dict[str, Any]]:
    """The agents the boards of ``projects`` list, each once; none from a board whose node does not answer."""
    found: dict[str, dict[str, Any]] = {}
    for project in projects:
        try:
            listed = person.session(Path(project)).agents(True)
        except (errors.Error, node_client.NodeError, OSError, ValueError):
            continue
        for agent in listed:
            if not agent.person:
                found[agent.name] = {"name": agent.name, "harness": agent.harness, "model": agent.model, "parent": agent.parent,
                                     "retired": agent.retired, "seen": agent.seen_ms / 1000}
    return list(found.values())


def _delegate(arguments: Sequence[str], cwd: str) -> str:
    """Run ``poolhouse-delegate`` with ``arguments`` in ``cwd`` and return what it said; ValueError with it when it failed."""
    environment = {name: value for name, value in os.environ.items() if name not in IDENTITY}
    try:
        done = subprocess.run([sys.executable, "-m", "poolhouse.delegate", *arguments], cwd=cwd, env=environment, capture_output=True,
                              text=True, timeout=WAIT_S, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"The delegate command did not answer within {WAIT_S} seconds.") from exc
    said = " ".join((done.stdout + " " + done.stderr).split())[-2000:]
    if done.returncode:
        raise ValueError(said or f"The delegate command ended with status {done.returncode}.")
    return said


def start_delegate(project: str, task: str, edit: bool = False, model: str = "") -> str:
    """Start a local delegate on ``project``; what the command said, which names it."""
    return _delegate(["start", f"--task={task}", f"--project={project}", *(["--edit"] if edit else []),
                      *([f"--model={model}"] if model else [])], project)


def stop_delegate(name: str) -> str:
    return _delegate(["stop", "--", name], str(home.user_home()))
