"""Untrusted repositories cannot replace trusted coding hook or bridge imports."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import coding_policy, harnesshook, harnessing, pi, pi_tools, runtime

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


@pytest.mark.parametrize("invalid_python_home", [False, True])
def test_actual_generated_hook_ignores_repository_package_and_python_environment(hostile, monkeypatch, invalid_python_home):
    project, marker = hostile
    if not invalid_python_home:
        monkeypatch.delenv("PYTHONHOME")
    command = harnessing.hook_command("pre", policy=coding_policy.Policy("read-only"),
                                     agent="isolated-worker", root=project, protect=[])
    done = subprocess.run(shlex.split(command), input=json.dumps({"tool_name": "Read", "tool_input": {"path": "safe.txt"},
                                                               "cwd": str(project)}),
                          text=True, capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not marker.exists()


def test_newline_and_quoted_arguments_cannot_change_the_hook_command(hostile):
    project, marker = hostile
    root = project / "line\nbreak ' $(touch injected)"
    root.mkdir()
    command = harnessing.hook_command("pre", policy=coding_policy.Policy("read-only"),
                                     agent="isolated-worker", root=root, protect=[])
    arguments = shlex.split(command)
    assert arguments[arguments.index("--root") + 1] == str(root)
    done = subprocess.run(arguments, input=json.dumps({"tool_name": "Read", "tool_input": {"path": "safe.txt"},
                                                       "cwd": str(root)}),
                          text=True, capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not (project / "injected").exists() and not marker.exists()


def test_actual_pi_extension_bridge_uses_trusted_imports(hostile, seatbelt):
    project, marker = hostile
    binary, node = shutil.which("pi"), shutil.which("node")
    if not binary or not node:
        pytest.skip("Installed Pi and Node are required")
    (project / "safe.txt").write_text("trusted tool read")
    files = harnessing.session_files(project)
    try:
        configuration = pi_tools.prepared(files, binary, project, coding_policy.Policy("read-only"))
        settings = {"permissions": "read-only", "executionMode": "execute", "agent": "isolated-worker",
                    "root": str(project), "protected": [], "toolConfig": str(configuration),
                    "toolModule": json.loads(configuration.read_text())["module"]}
        (project / "policy.mjs").write_text(pi.extension(settings))
        (project / "driver.mjs").write_text('import extension from "./policy.mjs";\n'
            'const tools={}; await extension({on:()=>{},registerTool:tool=>tools[tool.name]=tool});\n'
            'console.log(JSON.stringify(await tools.read.execute("read-1",{path:"safe.txt"})));\n')
        files.lock()
        done = subprocess.run([node, str(project / "driver.mjs")], text=True, capture_output=True,
                              timeout=30, check=False)
        assert done.returncode == 0, done.stderr
        assert "trusted tool read" in json.dumps(json.loads(done.stdout))
        assert not marker.exists()
    finally:
        files.release()


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
