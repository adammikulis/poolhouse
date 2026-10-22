"""The Windows firewall step of opening Poolhouse, and the sentence for a WSL machine the network cannot reach.

PowerShell does not run on the machines these tests run on, so the commands the step sends are the thing under test: `run` stands in
for `powershell.exe` and records what it was given. Nothing here is a model.
"""

from __future__ import annotations

import base64

import pytest

from poolhouse.fleet import windows_firewall as firewall
from poolhouse.fleet.discovery import DEFAULT_HTTP_PORT, default_port
from poolhouse.fleet.wsl_network import nat_notice


class Windows:
    """What PowerShell answers: the rules held now, the connection categories, and what an elevated prompt adds."""

    def __init__(self, held=(), categories="Private", grants=True):
        self.held, self.categories, self.grants, self.sent = list(held), categories, grants, []

    def __call__(self, script: str) -> str:
        self.sent.append(script)
        if "Get-NetFirewallRule" in script:
            return "\n".join(self.held)
        if "Get-NetConnectionProfile" in script:
            return self.categories
        if "-Verb RunAs" in script and self.grants:
            self.held = [name for name, _, _ in firewall.rules()]
        return ""

    def elevated(self) -> str:
        (script,) = [s for s in self.sent if "-Verb RunAs" in s]
        encoded = script.split("'-EncodedCommand','")[1].split("'")[0]
        return base64.b64decode(encoded).decode("utf-16-le")


def test_the_rules_are_the_daemons_port_and_the_beacons_port():
    assert firewall.rules() == [("poolhouse traind", "TCP", DEFAULT_HTTP_PORT), ("poolhouse discovery", "UDP", default_port())]


def test_with_both_rules_held_nothing_is_asked_of_windows(capsys):
    windows = Windows(held=["poolhouse traind", "poolhouse discovery"])
    assert firewall.ensure(windows) is True
    assert not any("-Verb RunAs" in s for s in windows.sent) and capsys.readouterr().out == ""


def test_missing_rules_are_added_by_one_elevated_prompt_that_says_so_first(capsys):
    windows = Windows(held=["poolhouse traind"])
    assert firewall.ensure(windows) is True
    assert "press Yes" in capsys.readouterr().out
    added = windows.elevated()
    assert "poolhouse discovery" in added and f"-Protocol UDP -LocalPort {default_port()}" in added and "-Profile Private" in added
    assert "poolhouse traind" not in added, "a rule that is held is not added again"
    assert firewall.missing(windows) == []


def test_a_prompt_that_was_declined_is_said_in_a_sentence_and_nothing_is_claimed(capsys):
    windows = Windows(grants=False)
    assert firewall.ensure(windows) is False
    assert "open Poolhouse again and press Yes" in capsys.readouterr().err


def test_a_network_windows_calls_public_is_named_with_the_setting_to_change(capsys):
    windows = Windows(held=["poolhouse traind", "poolhouse discovery"], categories="Public")
    assert firewall.ensure(windows) is False
    assert "choose Private" in capsys.readouterr().err
    assert firewall.public_only(Windows(categories="Public,Private")) is False
    assert firewall.public_only(Windows(categories="")) is False


@pytest.mark.parametrize("address", ["172.20.144.1", "172.16.0.9", "172.31.255.254"])
def test_a_wsl_machine_on_the_hyper_v_network_is_told_how_to_be_reached(address):
    said = nat_notice(address)
    assert address in said and "cannot find it or reach it" in said and "python -m poolhouse.device_setup --yes" in said


@pytest.mark.parametrize("address", ["192.168.2.59", "10.0.0.4", "172.32.0.1", "", "not an address"])
def test_a_machine_on_the_network_itself_is_not_told_anything(address):
    assert nat_notice(address) == ""
