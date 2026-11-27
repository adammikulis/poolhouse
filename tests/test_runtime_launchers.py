"""Owned runtime launcher boundaries; no tool's settings file is rewritten."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from poolhouse import home, runtime, runtime_cli, runtime_launchers, runtime_store


@pytest.fixture
def chosen(tmp_path, monkeypatch):
    value = runtime.Runtime(tmp_path / "prefix", "a" * 40, "0.2.2", runtime.identity())
    monkeypatch.setattr(runtime, "selected", lambda: value)
    monkeypatch.setattr(runtime, "verify", lambda row: row)
    return value


def test_regular_owned_console_gateway_executes_selected_python_with_literal_arguments(tmp_path, chosen):
    target = tmp_path / "poolhouse-workspace"
    target.write_text("old")
    target.chmod(0o755)
    chosen.python.parent.mkdir(parents=True)
    (chosen.prefix / "lib" / "python3.13" / "site-packages" / "poolhouse").mkdir(parents=True)
    (chosen.prefix / "lib" / "python3.13" / "site-packages" / "poolhouse" / "__init__.py").write_text("")
    chosen.python.write_text("#!/usr/bin/env python3\nimport json,sys,os\nprint(json.dumps([sys.argv[1:],os.environ.get('PYTHONPATH')]))\n")
    chosen.python.chmod(0o700)
    runtime.gateway(target, "poolhouse.workspace.cli", "main")
    arguments = ["inbox", "--label", "literal $(echo unsafe)"]
    done = subprocess.run([sys.executable, str(target), *arguments], capture_output=True,
                          text=True, check=True, env={**os.environ, "PYTHONPATH": "hostile-source"})
    argv, source = json.loads(done.stdout)
    assert argv[0] == "-I" and argv[-3:] == arguments and source is None
    assert "poolhouse.workspace.cli" in argv[2]
    assert "sys.argv[0]='poolhouse-workspace'" in argv[2]


def test_console_preflight_rejects_symlinks_and_foreign_owners_before_writing(tmp_path, chosen, monkeypatch):
    first = tmp_path / "poolhouse"
    first.write_text("original")
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    (tmp_path / "poolhouse-workspace").symlink_to(victim)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps({"poolhouse": "poolhouse.cli:main", "poolhouse-workspace": "poolhouse.workspace.cli:main"})))
    with pytest.raises(OSError, match="symbolic"):
        runtime_launchers.install(tmp_path)
    assert first.read_text() == "original" and victim.read_text() == "untouched"
    (tmp_path / "poolhouse-workspace").unlink()
    monkeypatch.setattr(os, "getuid", lambda: first.stat().st_uid + 1)
    with pytest.raises(OSError, match="account"):
        runtime_launchers.install(tmp_path)
    assert first.read_text() == "original"


@pytest.mark.parametrize("value", ["os:system", "poolhouse.cli:main;echo", "poolhouse.cli:main.extra"])
def test_console_metadata_refuses_foreign_or_executable_entrypoints(tmp_path, chosen, monkeypatch, value):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps({"poolhouse": value})))
    with pytest.raises(ValueError, match="belong"):
        runtime_launchers.install(tmp_path)
    assert not (tmp_path / "poolhouse").exists()


def test_default_launcher_layout_ignores_an_old_shared_interpreter_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    foreign = tmp_path / "shared-python" / "bin"
    runtime_store.write_state({"launchers": str(foreign)})
    assert runtime_cli._launchers("") == home.state("bin")


def test_no_launcher_code_rewrites_a_tools_settings_file():
    assert [name for name in ("claude_hooks", "codex_hooks", "hook_command") if hasattr(runtime_launchers, name)] == []
