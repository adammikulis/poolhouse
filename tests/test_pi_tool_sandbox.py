"""Actual Pi built-in tools are confined independently of their orchestrator."""
from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest

from poolhouse import pi_tools
from poolhouse.sandbox.backend import Availability, SandboxUnavailable
from poolhouse.sandbox.seatbelt import Seatbelt

pytest_plugins = ["tests.sandbox_kit"]


class Files:
    def __init__(self, path):
        self.path = path
        path.mkdir()

    def write(self, name, text):
        target = self.path / name
        target.write_text(text)
        return target


@pytest.fixture
def installed(tmp_path, seatbelt):
    binary = shutil.which("pi")
    if not binary:
        pytest.skip("Installed Pi is required for its stock tool implementation")
    project = tmp_path / "selected-repo"
    project.mkdir()
    files = Files(tmp_path / "control")
    configured = pi_tools.prepared(files, binary, project, "plan-and-go")
    return json.loads(configured.read_text())


def invoke(configuration, name, **inputs):
    return pi_tools.execute(configuration, {"tool": name, "input": inputs})


def text(answer):
    return "\n".join(row.get("text", "") for row in answer["content"])


def test_stock_read_write_edit_and_shell_stay_in_selected_repo(installed):
    root = Path(installed["root"])
    assert not invoke(installed, "write", path="inside.txt", content="first").get("isError")
    assert root.joinpath("inside.txt").read_text() == "first"
    assert not invoke(installed, "edit", path="inside.txt", edits=[{"oldText": "first", "newText": "second"}]).get("isError")
    assert "second" in text(invoke(installed, "read", path="inside.txt"))
    assert "second" in text(invoke(installed, "bash", command="cat inside.txt"))


def test_stock_tools_cannot_read_or_modify_another_repo_or_parent_control(installed):
    root = Path(installed["root"])
    other = root.parent / "other-repo"
    other.mkdir()
    secret = other / "private.txt"
    secret.write_text("isolated-private-marker")
    for tool, inputs in [("read", {"path": str(secret)}),
                         ("bash", {"command": f"cat {secret}"}),
                         ("read", {"path": str(root.parent / "control" / "tool-worker.json")}),
                         ("write", {"path": str(secret), "content": "changed"}),
                         ("edit", {"path": str(secret), "edits": [{"oldText": "isolated", "newText": "changed"}]})]:
        answer = invoke(installed, tool, **inputs)
        assert answer.get("isError"), (tool, answer)
        assert "isolated-private-marker" not in text(answer)
    assert secret.read_text() == "isolated-private-marker"


def test_symlink_escape_is_denied_by_os_for_stock_direct_tools_and_shell(installed):
    root = Path(installed["root"])
    outside = root.parent / "not-selected.txt"
    outside.write_text("outside-marker")
    (root / "linked.txt").symlink_to(outside)
    for tool, inputs in [("read", {"path": "linked.txt"}),
                         ("write", {"path": "linked.txt", "content": "changed"}),
                         ("bash", {"command": "cat linked.txt; printf changed > linked.txt"})]:
        answer = invoke(installed, tool, **inputs)
        assert answer.get("isError"), answer
        assert "outside-marker" not in text(answer)
    assert outside.read_text() == "outside-marker"


def test_read_only_worker_denies_repo_writes_but_can_read(installed):
    installed["write"] = False
    root = Path(installed["root"])
    (root / "readme").write_text("allowed-read")
    assert "allowed-read" in text(invoke(installed, "read", path="readme"))
    for tool, inputs in [("write", {"path": "created", "content": "changed"}),
                         ("bash", {"command": "printf changed > created"})]:
        assert invoke(installed, tool, **inputs).get("isError")
    assert not (root / "created").exists()


def test_worker_environment_and_network_exclude_parent_authority(installed, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_TEST_PARENT_CREDENTIAL", "secret-parent-marker")
    answer = invoke(installed, "bash", command='printf "parent=%s" "$POOLHOUSE_TEST_PARENT_CREDENTIAL"')
    assert "parent=" in text(answer) and "secret-parent-marker" not in text(answer)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(.1)
        port = listener.getsockname()[1]
        answer = invoke(installed, "bash", command=f"/usr/bin/curl -sS --max-time 1 http://127.0.0.1:{port}/")
        assert answer.get("isError")
        with pytest.raises(TimeoutError):
            listener.accept()


def test_no_os_sandbox_never_runs_tool_or_falls_back(installed, monkeypatch):
    monkeypatch.setattr(Seatbelt, "available", lambda self: Availability(False, "test unavailable"))
    target = Path(installed["root"]) / "never-created"
    with pytest.raises(SandboxUnavailable, match="test unavailable"):
        invoke(installed, "write", path=str(target), content="changed")
    assert not target.exists()


@pytest.mark.parametrize("frame", [
    {"tool": "evaluate", "input": {}}, {"tool": "read", "input": {}, "module": "/tmp/injected"},
    {"tool": "read", "input": "not-object"},
])
def test_tool_worker_rejects_caller_runtime_or_dispatch_authority(frame):
    with pytest.raises(ValueError, match="Invalid coding tool request"):
        pi_tools.execute({}, frame)
