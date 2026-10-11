"""Shared setup for tests that run real subprocesses against a workspace home under tmp_path."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SRC = str(Path(__file__).resolve().parents[1] / "src")
STRIPPED = ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "POOLHOUSE_WORKSPACE_TOKEN",
            "POOLHOUSE_WORKSPACE_DENYLIST")


def clean_env(monkeypatch, tmp_path: Path) -> Path:
    """Point the workspace at ``tmp_path`` and remove the variables that mark an agent."""
    for name in STRIPPED:
        monkeypatch.delenv(name, raising=False)
    base = tmp_path / "ws"
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_HOME", str(base))
    monkeypatch.setenv("DEV_TEST_SLOTS_DIR", str(tmp_path / "slots"))
    return base


def run_python(code: str, base: Path, token: str = "", *args: str,
               timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter against the same workspace."""
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
    env.update({"POOLHOUSE_WORKSPACE_HOME": str(base), "PYTHONPATH": SRC})
    if token:
        env["POOLHOUSE_WORKSPACE_TOKEN"] = token
    return subprocess.run([sys.executable, "-c", code, *args], env=env, capture_output=True,
                          text=True, timeout=timeout, check=False)


def cli(base: Path, token: str, *argv: str, env_extra: dict[str, str] | None = None,
        timeout: float = 60, **run: Any) -> subprocess.CompletedProcess[str]:
    """Run ``poolhouse workspace`` as a subprocess."""
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
    env.update({"POOLHOUSE_WORKSPACE_HOME": str(base), "PYTHONPATH": SRC, **(env_extra or {})})
    if token:
        env["POOLHOUSE_WORKSPACE_TOKEN"] = token
    return subprocess.run([sys.executable, "-m", "poolhouse.workspace.cli", *argv], env=env,
                          capture_output=True, text=True, timeout=timeout, check=False, **run)
