"""What a packaged app does with nothing else installed: it runs its own node and services, and its daemon has the pool actions.

A frozen app is stood in for by the two things PyInstaller sets (``sys.frozen`` and ``sys._MEIPASS``) over a scratch tree;
no process is started.
"""

from __future__ import annotations

import hashlib
import importlib.util
import runpy
import sys
from pathlib import Path

import pytest

from poolhouse import jobs, node_binary, node_supervise
from poolhouse.cli import daemon as cli_daemon
from poolhouse.fleet import frozen_dispatch

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    """A packaged app unpacked at the returned folder, on a computer with no runtime selected."""
    held = tmp_path / "unpacked"
    node = held / "node" / node_binary.name()
    node.parent.mkdir(parents=True)
    node.write_bytes(b"the node the app carries")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(held), raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    return held


@pytest.fixture
def started(monkeypatch):
    """Every command a detached start asks for, with its environment; nothing runs."""
    asked: list[tuple[list[str], dict[str, str]]] = []

    def start(command, environment, out, disclaimed):
        asked.append((command, environment))
        return 4242, None

    monkeypatch.setattr(jobs, "_start", start)
    return asked


def test_with_no_runtime_the_supervisor_starts_the_node_the_app_carries(packaged, tmp_path):
    binary, sha = node_supervise.resolve(tmp_path / "node-state")
    assert binary == packaged / "node" / node_binary.name()
    assert sha == hashlib.sha256(b"the node the app carries").hexdigest()


@pytest.mark.parametrize("module", ["poolhouse.node_launch", "poolhouse.serve.cli"])
def test_the_app_runs_its_own_services_as_programs_that_outlive_it(packaged, started, tmp_path, module):
    jobs.detach(module, ["status"], log=tmp_path / "service.log")
    command, environment = started[0]
    assert command == [sys.executable, "-m", module, "status"]
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1", "or it shares the app's unpacked files, which the app removes"
    with pytest.raises(OSError, match="standalone runtime"):   # a worker still needs an interpreter of its own
        jobs.detach("poolhouse.train.run", [], log=tmp_path / "worker.log")
    assert len(started) == 1


def test_a_bundle_is_frozen_with_the_node_where_the_daemon_looks_for_it(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bundle_build", ROOT / "packaging" / "build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    built = tmp_path / "poolhouse-node-for-this-target"
    built.write_bytes(b"built node")
    monkeypatch.setattr(builder, "ROOT", tmp_path / "checkout")
    monkeypatch.setattr(builder, "wheels", lambda: [])
    monkeypatch.setattr(builder, "node", lambda *_: built)
    monkeypatch.setattr(builder, "report", lambda made: None)
    found = []
    carried = tmp_path / "checkout" / ".build-work" / "app-node" / node_binary.name()
    monkeypatch.setattr(builder, "daemon", lambda: found.append(carried.read_bytes() if carried.is_file() else None))
    assert builder.main(["--bundle", "--no-window"]) == 0
    assert found == [b"built node"]
    assert '"node")' in (ROOT / "packaging" / "poolhouse.spec").read_text(encoding="utf-8")


def test_the_packaged_launcher_starts_the_daemon_that_holds_the_pool_actions(monkeypatch):
    asked, served = {}, []
    monkeypatch.setattr(frozen_dispatch, "main", lambda argv=None, **how: asked.update(how) or 0)
    monkeypatch.setattr(cli_daemon, "main", lambda argv: served.append(argv) or 7)
    with pytest.raises(SystemExit) as ended:
        runpy.run_path(str(ROOT / "packaging" / "launcher-headless.py"), run_name="__main__")
    assert ended.value.code == 0
    assert asked["daemon_main"](["--port", "9876"]) == 7 and served == [["--port", "9876"]]
