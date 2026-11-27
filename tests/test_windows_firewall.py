"""The Windows firewall check at start, and the sentence for a WSL machine the network cannot reach.

PowerShell does not run on the machines these tests run on, so the commands the check sends are the thing under test: `run` stands in
for `powershell.exe` and records what it was given. Nothing here is a model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poolhouse.fleet import windows_firewall as firewall
from poolhouse.fleet.onboard.lan import DEFAULT_HTTP_PORT
from poolhouse.fleet.wsl_network import nat_notice


class Windows:
    """What PowerShell answers: the rules held now and the connection categories, in a shell that is an administrator's."""

    def __init__(self, held=(), categories="Private"):
        self.held, self.categories, self.sent = list(held), categories, []

    def __call__(self, script: str) -> str:
        self.sent.append(script)
        if "IsInRole" in script:
            return "True"
        if "New-NetFirewallRule" in script:
            self.held = [name for name, _, _ in firewall.rules()]
            return ""
        if "Get-NetFirewallRule" in script:
            return "\n".join(self.held)
        if "Get-NetConnectionProfile" in script:
            return self.categories
        return ""


def test_the_one_rule_is_the_daemons_port_and_there_is_no_beacon_rule():
    assert firewall.rules() == [("Poolhouse", "TCP", DEFAULT_HTTP_PORT)]
    assert "UDP" not in firewall.line()


def test_with_the_rule_held_nothing_is_added_and_nothing_is_said(capsys):
    windows = Windows(held=["Poolhouse"])
    assert firewall.check(windows) is True
    assert not any("New-NetFirewallRule" in s for s in windows.sent) and capsys.readouterr().out == ""


def test_starting_never_adds_a_rule_even_in_an_administrators_shell_and_says_the_line_to_run(capsys):
    windows = Windows()
    assert firewall.check(windows) is False
    said = capsys.readouterr().err
    assert firewall.NOT_ADDED in said and firewall.line() in said
    assert not any("New-NetFirewallRule" in s or "netsh" in s or "RunAs" in s or "Start-Process" in s for s in windows.sent)


def test_a_network_windows_calls_public_is_named_with_the_setting_to_change(capsys):
    windows = Windows(held=["Poolhouse"], categories="Public")
    assert firewall.check(windows) is False
    assert "choose Private" in capsys.readouterr().err
    assert firewall.public_only(Windows(categories="Public,Private")) is False
    assert firewall.public_only(Windows(categories="")) is False


@pytest.mark.parametrize("address", ["172.20.144.1", "172.16.0.9", "172.31.255.254"])
def test_a_wsl_machine_on_the_hyper_v_network_is_told_how_to_be_reached(address):
    said = nat_notice(address)
    assert address in said and "cannot find it or reach it" in said and "python -m poolhouse.device_setup" in said


@pytest.mark.parametrize("address", ["192.168.2.59", "10.0.0.4", "172.32.0.1", "", "not an address"])
def test_a_machine_on_the_network_itself_is_not_told_anything(address):
    assert nat_notice(address) == ""


def test_nothing_in_the_source_raises_a_windows_administrator_prompt():
    root = Path(__file__).resolve().parent.parent
    files = [*(root / "src" / "poolhouse").rglob("*.py"), *(root / "packaging").glob("*.ps1")]
    raising = [str(f.relative_to(root)) for f in files if "-Verb RunAs" in f.read_text(encoding="utf-8", errors="replace")]
    assert raising == [], "an administrator prompt waits for a person: decide by rule or refuse at once instead"
