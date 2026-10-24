"""Git and harness notification dispatch never builds or selects an installed runtime."""

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "scripts/hooks"


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    specification = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_merge_hook_does_not_dispatch_a_runtime_builder(tmp_path):
    copied = tmp_path / "post-merge"
    shutil.copyfile(HOOKS / "post-merge", copied)
    marker = tmp_path / "unexpected-runtime-builder"
    (tmp_path / "runtime-refresh").write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected')\n")
    done = subprocess.run(["/bin/sh", str(copied), "0"], cwd=tmp_path,
                          env={**os.environ, "PYTHON": sys.executable}, capture_output=True, text=True, check=False)
    assert done.returncode == 0
    assert not marker.exists(), "a merge launched a runtime build helper"


def test_legacy_runtime_refresh_entrypoint_does_not_dispatch_updates(tmp_path):
    copied = tmp_path / "runtime-refresh"
    shutil.copyfile(HOOKS / "runtime-refresh", copied)
    marker = tmp_path / "unexpected-runtime-update"
    (tmp_path / "workspace_hook.py").write_text(
        "from pathlib import Path\ndef refresh_runtime(*args, **kwargs):\n"
        f"    Path({str(marker)!r}).write_text('unexpected')\n")
    done = subprocess.run([sys.executable, str(copied), "post-merge"], cwd=tmp_path,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0
    assert not marker.exists(), "a legacy notification entrypoint requested an installed update"


@pytest.mark.parametrize("stage", ["SessionStart", "post-merge", "primary-sync"])
def test_legacy_refresh_helper_never_checks_or_builds_the_runtime(monkeypatch, stage):
    module = load(HOOKS / "workspace_hook.py", "runtime_refresh_helper_fixture")
    monkeypatch.delenv("POOLHOUSE_RUNTIME_ENSURE", raising=False)
    if hasattr(module, "lan"):
        monkeypatch.setattr(module.lan, "on_the_persons_machine", lambda: True)

    def forbidden(*args, **kwargs):
        pytest.fail("a notification tried to build or install a runtime")

    monkeypatch.setattr(module.subprocess, "run", forbidden)
    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    assert module.refresh_runtime(stage) is None


def test_session_start_notification_does_not_request_runtime_refresh(monkeypatch):
    monkeypatch.syspath_prepend(str(HOOKS))
    helper = load(HOOKS / "workspace_hook.py", "runtime_refresh_session_fixture")
    monkeypatch.setitem(sys.modules, "workspace_hook", helper)
    environment = {"POOLHOUSE_WORKSPACE_AGENT": "synthetic-dispatch-only"}
    # This exercises dispatch only; no session is registered and no identity or authority is granted.
    monkeypatch.setattr(helper, "event", lambda stage: {"session_id": "synthetic-event", "model": "label-only"})
    monkeypatch.setattr(helper, "person", lambda *args: 0)
    monkeypatch.setattr(helper, "session_environment", lambda *args: environment)
    monkeypatch.setattr(helper, "name_session", lambda *args: "synthetic-dispatch-only")
    monkeypatch.setattr(helper, "persist_session", lambda *args: None)
    monkeypatch.setattr(helper, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(helper, "hook_notice", lambda *args: "")

    def forbidden(*args, **kwargs):
        pytest.fail("SessionStart requested a runtime build")

    monkeypatch.setattr(helper, "refresh_runtime", forbidden)
    module = load(HOOKS / "claude-session-start", "runtime_refresh_session_start_fixture")
    assert module.main() == 0
