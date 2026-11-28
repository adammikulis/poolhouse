"""Windows runs Poolhouse itself: the launcher, restart, login start and firewall rules are the machine's own.

The launcher is told it is on Windows and the daemon, the stop request and the firewall are stood in for, so no process is
started and nothing on this computer changes. The same paths run for real in .github/workflows/windows-app.yml.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.fleet import autostart, launch, windows_firewall

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "app" / "src-tauri" / "windows" / "hooks.nsh"


@pytest.fixture
def windows(monkeypatch, tmp_path):
    """The launcher on Windows with nothing on its port; returns its state root."""
    monkeypatch.setattr(launch, "sys", SimpleNamespace(platform="win32", argv=["poolhouse"]))
    monkeypatch.setattr(launch, "already_running", lambda port: None)
    monkeypatch.setattr(launch, "port_held", lambda port, timeout=2.0: False)
    monkeypatch.setattr(windows_firewall.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="", returncode=0))
    return tmp_path / "root"


def test_on_windows_the_launcher_checks_the_firewall_then_starts_the_daemon_on_this_computer(windows, monkeypatch):
    events = []
    monkeypatch.setattr(launch.windows_firewall, "check", lambda: events.append("firewall") or False)
    code = launch.main(["--no-browser", "--port", "8791", "--root", str(windows)],
                       daemon_main=lambda argv: events.append(argv) or 0)
    assert code == 0 and events == ["firewall", ["--port", "8791", "--root", str(windows)]]


def test_on_windows_a_restart_asks_the_running_daemon_and_proves_its_successor(windows, monkeypatch):
    asked, finished = [], []
    monkeypatch.setattr(launch, "already_running", lambda port: {"name": "pc", "launcher_control": "a" * 32})
    monkeypatch.setattr(launch, "launch_of", lambda root, port: ["old"])
    monkeypatch.setattr(launch, "request_replacement", lambda root, port, running, expected, restart: asked.append((root, port, restart)))
    monkeypatch.setattr(launch, "_wait_for_exit", lambda port: True)
    monkeypatch.setattr(launch, "finish", lambda arguments, previous, probe, release: finished.append((arguments, previous)) or 0)
    code = launch.main(["--restart", "--no-browser", "--port", "8791", "--root", str(windows)], daemon_main=lambda argv: 1)
    assert code == 0 and asked == [(windows, 8791, "preserve")]
    assert finished == [(["--port", "8791", "--root", str(windows)], ["old"])]


def test_the_windows_login_start_runs_the_daemon_command_like_every_computer(monkeypatch):
    monkeypatch.setattr(autostart, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(autostart.shutil, "which", lambda name: None)
    assert autostart.plan("login") == [sys.executable, "-m", "poolhouse.cli.daemon"]


WANTED = {("Poolhouse", "TCP", 8770), ("Poolhouse node", "TCP", 7447), ("Poolhouse node discovery", "UDP", 7448),
          ("Poolhouse phones", "TCP", 7450)}


def test_the_rules_are_every_port_poolhouse_listens_on_for_this_network():
    assert set(windows_firewall.rules()) == WANTED
    assert windows_firewall.line().count("profile=private") == len(WANTED)
    assert "profile=any" not in windows_firewall.line() and "profile=public" not in windows_firewall.line()


def test_the_installer_adds_and_removes_exactly_those_rules_on_private_networks():
    script = HOOKS.read_text(encoding="utf-8")
    added = {(name, protocol, int(port)) for name, protocol, port in
             re.findall(r'add rule name="([^"]+)" dir=in action=allow profile=private protocol=(TCP|UDP) localport=(\d+)', script)}
    assert added == WANTED
    assert set(re.findall(r'delete rule name="([^"]+)"', script)) == {name for name, _, _ in WANTED}
    config = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    assert config["bundle"]["windows"]["nsis"]["installerHooks"] == "windows/hooks.nsh"
