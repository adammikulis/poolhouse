"""Native Windows has no sandbox backend yet: it says so, with the reason, and starts nothing.

The tests run on every platform: the backend is a plain object and the selection reads ``sys.platform``.
"""

from __future__ import annotations

import os
import sys

import pytest

from poolhouse import sandbox
from poolhouse.sandbox import Policy, SandboxUnavailable, run, windows


def test_native_windows_is_unavailable_and_says_why_the_network_cannot_be_confined_to_the_model(monkeypatch):
    monkeypatch.setattr(windows.shutil, "which", lambda name: None)
    state = windows.Windows().available()
    assert not state.ok and state.reason == windows.REASON
    assert "network" in state.reason and "one" in state.reason and "WSL2" not in state.reason


def test_native_windows_names_the_wsl2_route_when_it_is_installed(monkeypatch):
    monkeypatch.setattr(windows.shutil, "which", lambda name: "C:/Windows/System32/wsl.exe" if name == "wsl.exe" else None)
    state = windows.Windows().available()
    assert not state.ok and state.reason == windows.REASON + windows.ROUTE and "WSL2" in state.reason


def test_a_command_is_not_started_when_the_backend_is_native_windows(tmp_path):
    marker = tmp_path / "ran"
    held = Policy("windows", read=(str(tmp_path),), write=(str(tmp_path),), exec=(os.path.realpath(sys.executable),), env={})
    with pytest.raises(SandboxUnavailable, match="cannot confine the network of a coding agent"):
        run([os.path.realpath(sys.executable), "-c", f"open({str(marker)!r}, 'w')"], held, via=windows.Windows())
    assert not marker.exists()


@pytest.mark.parametrize(("platform", "expected"), [("darwin", "seatbelt"), ("linux", "bubblewrap"), ("win32", "windows")])
def test_each_platform_gets_its_own_backend(monkeypatch, platform, expected):
    monkeypatch.setattr(sys, "platform", platform)
    assert sandbox.backend().name == expected
