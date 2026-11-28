"""Startup and binary information arguments remain literal argv values."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from poolhouse.fleet import windows_firewall
from poolhouse.serve import backend


def test_the_windows_start_asks_the_firewall_only_questions_of_fixed_text(monkeypatch):
    held = "\n".join(name for name, _, _ in windows_firewall.rules())   # every rule held, so the network's kind is asked too
    run = Mock(return_value=SimpleNamespace(stdout=held))
    monkeypatch.setattr(windows_firewall.subprocess, "run", run)
    assert windows_firewall.check() is True
    assert run.call_count == 2
    for asked in run.call_args_list:
        assert asked.args[0][:4] == [windows_firewall.POWERSHELL, "-NoProfile", "-NonInteractive", "-Command"] and len(asked.args[0]) == 5
        assert not asked.kwargs.get("shell")


@pytest.mark.parametrize("devices, option", [(False, "--help"), (True, "--list-devices")])
def test_binary_information_uses_one_literal_path_and_fixed_flag(monkeypatch, devices, option):
    path = Path("C:/tools/binary name; $(payload).exe")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="information", stderr=""))
    monkeypatch.setattr(backend.subprocess, "run", run)
    monkeypatch.setattr(backend, "child_env", lambda path: {})
    assert backend._binary_info(path, devices=devices, timeout=2).strip() == "information"
    assert run.call_args.args[0] == [str(path), option]
    assert run.call_args.kwargs["timeout"] == 2
    assert not run.call_args.kwargs.get("shell")
