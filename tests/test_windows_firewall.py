"""The Windows firewall check at start.

PowerShell does not run on the machines these tests run on, so the commands the check sends are the thing under test: `run` stands in
for `powershell.exe` and records what it was given. Nothing here is a model.
"""

from __future__ import annotations

from pathlib import Path

from poolhouse.fleet import windows_firewall as firewall

EVERY = [name for name, _, _ in firewall.rules()]


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


def test_no_rule_opens_a_port_on_a_public_network():
    assert firewall.line().count("profile=private") == len(EVERY), "a public network gets no open port"


def test_with_the_rules_held_nothing_is_added_and_nothing_is_said(capsys):
    windows = Windows(held=EVERY)
    assert firewall.check(windows) is True
    assert not any("New-NetFirewallRule" in s for s in windows.sent) and capsys.readouterr().out == ""


def test_starting_never_adds_a_rule_even_in_an_administrators_shell_and_says_the_line_to_run(capsys):
    windows = Windows()
    assert firewall.check(windows) is False
    said = capsys.readouterr().err
    assert firewall.NOT_ADDED in said and firewall.line() in said
    assert not any("New-NetFirewallRule" in s or "netsh" in s or "RunAs" in s or "Start-Process" in s for s in windows.sent)


def test_a_network_windows_calls_public_is_named_with_the_setting_to_change(capsys):
    windows = Windows(held=EVERY, categories="Public")
    assert firewall.check(windows) is False
    assert "choose Private" in capsys.readouterr().err
    assert firewall.public_only(Windows(categories="Public,Private")) is False
    assert firewall.public_only(Windows(categories="")) is False


def test_nothing_in_the_source_raises_a_windows_administrator_prompt():
    root = Path(__file__).resolve().parent.parent
    files = [*(root / "src" / "poolhouse").rglob("*.py"), *(root / "packaging").glob("*.ps1")]
    raising = [str(f.relative_to(root)) for f in files if "-Verb RunAs" in f.read_text(encoding="utf-8", errors="replace")]
    assert raising == [], "an administrator prompt waits for a person: decide by rule or refuse at once instead"
