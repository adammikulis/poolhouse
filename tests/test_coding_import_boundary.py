"""Untrusted repositories cannot replace trusted coding hook or bridge imports."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import coding_policy, harnesshook, harnessing, isolation, pi, runtime

pytest_plugins = ["tests.sandbox_kit"]


@pytest.fixture
def hostile(tmp_path, monkeypatch):
    project = tmp_path / "quoted & user's project"
    project.mkdir()
    marker = tmp_path / "untrusted-import-ran"
    body = f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\nraise RuntimeError('untrusted package loaded')\n"
    (project / "poolhouse").mkdir()
    (project / "poolhouse/__init__.py").write_text(body)
    (project / "sitecustomize.py").write_text(body)
    monkeypatch.setenv("PYTHONPATH", str(project))
    monkeypatch.setenv("PYTHONHOME", str(project / "fake-python-home"))
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_HOME", str(tmp_path / "workspace-state"))
    monkeypatch.chdir(project)
    return project, marker


CLIENT = Path(isolation.client.__file__) if hasattr(isolation, "client") else Path(sys.modules["poolhouse.isolation.client"].__file__)


def hook_against_a_real_bridge(project: Path, client: Path, tool_input: dict):
    """The hook command a confined harness runs, asking a real session bridge in this process; its answer and the process's exit status."""
    rail = harnesshook.Rail(coding_policy.Policy("read-only"), "isolated-worker", (str(project),), ())
    bridge = isolation.SessionBridge(isolation.Handlers(lambda event, payload: harnesshook.pre(payload, rail), lambda *_: {}),
                                     isolation.Terms(False, 4096), root="/tmp")
    path = bridge.start()
    try:
        context = isolation.Context(project, project, str(project), os.path.realpath(sys.executable), str(client), path, os.getpid(), "", "", 0)
        command = shlex.split(context.hook("pre"))
        done = subprocess.run(command, input=json.dumps({"tool_name": "Read", "tool_input": tool_input, "cwd": str(project)}), text=True,
                              capture_output=True, timeout=30, check=False)
    finally:
        bridge.stop()
    return command, done


@pytest.mark.parametrize("invalid_python_home", [False, True])
def test_actual_generated_hook_ignores_repository_package_and_python_environment(hostile, monkeypatch, invalid_python_home):
    project, marker = hostile
    if not invalid_python_home:
        monkeypatch.delenv("PYTHONHOME")
    _, done = hook_against_a_real_bridge(project, CLIENT, {"path": "safe.txt"})
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not marker.exists(), "the hostile package in the project was never imported: the client runs isolated and imports nothing of Poolhouse"


def test_newline_and_quoted_arguments_cannot_change_the_hook_command(hostile, tmp_path):
    project, marker = hostile
    odd = project / "line\nbreak ' $(touch injected)"
    odd.mkdir()
    client = odd / "client.py"
    client.write_text(CLIENT.read_text())
    command, done = hook_against_a_real_bridge(project, client, {"path": "safe.txt"})
    assert command[command.index("-I") + 1] == str(client), "the whole odd path is one argument"
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not (project / "injected").exists() and not marker.exists()


def test_actual_notification_caller_uses_isolated_module_argv(hostile, monkeypatch):
    project, marker = hostile
    captured = []
    execute = harnesshook._reader_run
    def observe(command, **options):
        result = execute(command, **options)
        captured.append((command, result))
        return result
    monkeypatch.setattr(harnesshook, "_reader_run", observe)
    harnesshook.nudge("isolated-worker", harnesshook.Rail(coding_policy.Policy("read-only"), "isolated-worker", [str(project)]))
    command, result = captured[0]
    assert command[1:3] == ["-I", "-c"]
    assert result.returncode == 1
    assert "workspace" in result.stderr.lower() or "board" in result.stderr.lower()
    assert "Fatal Python" not in result.stderr and "No module named" not in result.stderr
    assert not marker.exists()


def test_frozen_module_command_requires_verified_runtime_before_launch(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime, "available", lambda: None)
    with pytest.raises(OSError, match="verified standalone runtime"):
        runtime.module_argv("poolhouse.harnesshook", Path("/unselected"), "pre")


def test_frozen_command_pins_selected_interpreter_and_ignores_python_environment(hostile, monkeypatch):
    project, marker = hostile
    selected = runtime.Runtime(Path(sys.prefix), "1" * 40, "0", runtime.identity())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime, "available", lambda: selected)
    command = runtime.module_argv("poolhouse.harnesshook", project, "pre", "--root", str(project))
    assert command[:4] == [str(selected.python), "-I", "-m", "poolhouse.harnesshook"]
    done = subprocess.run(command, input=json.dumps({"tool_name": "Read", "tool_input": {"path": "safe.txt"},
                                                   "cwd": str(project)}),
                          text=True, capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not marker.exists()


@pytest.mark.parametrize("module", ["os", "poolhouse", "poolhouse.runtime", "../poolhouse.harnesshook"])
def test_tool_or_environment_input_cannot_choose_an_executable_module(module):
    with pytest.raises(ValueError, match="Unsupported isolated"):
        runtime.module_argv(module, Path("/unselected"))


@pytest.mark.parametrize("root", [Path(runtime.__file__).resolve().parent.parent, Path(sys.prefix)])
def test_repository_cannot_contain_or_be_part_of_trusted_application_runtime(root):
    with pytest.raises(ValueError, match="must not overlap"):
        runtime.module_argv("poolhouse.harnesshook", root, "pre")


def test_symlink_and_missing_child_runtime_overlap_are_refused(tmp_path):
    package = Path(runtime.__file__).resolve().parent.parent
    redirect = tmp_path / "runtime-alias"
    redirect.symlink_to(package, target_is_directory=True)
    for target in (redirect, package / "missing-tool-root"):
        with pytest.raises(ValueError, match="must not overlap"):
            runtime.module_argv("poolhouse.harnesshook", target, "pre")


def test_runtime_overlap_is_refused_before_native_model_admission(tmp_path, monkeypatch):
    binary = tmp_path / "fixture-pi"
    binary.write_text("unused")
    monkeypatch.setattr(harnessing, "binary_for", lambda _: str(binary))
    monkeypatch.setattr(harnessing, "serving", lambda *_args, **_options: pytest.fail("model admitted"))
    root = Path(runtime.__file__).resolve().parent.parent
    assert pi.launch(["--project", str(root)], say=lambda _line: None) == 2


def test_actual_task_admission_helper_ignores_repository_imports_and_python_environment(hostile):
    project, marker = hostile
    counter = project.parent / "parent-owned-tool-counter.json"
    counter.write_text(json.dumps({"version": 1, "calls": 0, "limit": 1}))
    command = runtime.module_argv("poolhouse.workspace.task_caps", project, str(counter))
    first = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    second = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert first.returncode == 0 and second.returncode == 2
    assert json.loads(counter.read_text())["calls"] == 1
    assert "limit reached" in second.stderr
    assert not marker.exists()
