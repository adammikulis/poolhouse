"""Windows only: whether the inbound rule the daemon needs to be reached is in place, said with the reason and the line to run where it is not.

Nothing here changes the firewall: the installer and `python -m poolhouse.device_setup`, which a person runs, are what add rules, and no
administrator prompt is ever raised. The rule is TCP for the daemon's port, on private networks.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from poolhouse.log import warn

from .onboard.lan import DEFAULT_HTTP_PORT

FIREWALL_RULE_HTTP = "Poolhouse"
POWERSHELL = "powershell.exe"
TIMEOUT_S = 300
PUBLIC = (r"Windows treats this network as Public, which blocks your other devices from reaching Poolhouse here: "
          r"in Settings > Network & internet, open this network's properties and choose Private")

NOT_ADDED = ("the Windows firewall rule that lets your other devices reach Poolhouse is not there, and starting Poolhouse does not change the "
             "firewall: run this line once in an administrator prompt")

Run = Callable[[str], str]


def powershell(script: str) -> str:
    """What PowerShell prints for ``script``; nothing when it cannot run."""
    try:
        done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                              timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def rules() -> list[tuple[str, str, int]]:
    """``(name, protocol, port)`` of each rule: the daemon's TCP port."""
    return [(FIREWALL_RULE_HTTP, "TCP", DEFAULT_HTTP_PORT)]


def line() -> str:
    """The rule as one line a person can paste into an administrator's prompt."""
    return " && ".join(f'netsh advfirewall firewall add rule name="{name}" dir=in action=allow protocol={proto} localport={port}'
                       for name, proto, port in rules())


def missing(run: Run = powershell) -> list[tuple[str, str, int]]:
    """The rules this Windows does not hold."""
    names = ",".join(f"'{name}'" for name, _, _ in rules())
    held = set(run(f"@({names}) | Where-Object {{ Get-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue }}").splitlines())
    return [rule for rule in rules() if rule[0] not in held]


def public_only(run: Run = powershell) -> bool:
    """Whether every network this Windows is connected to is classed Public."""
    kinds = run("(Get-NetConnectionProfile | ForEach-Object { $_.NetworkCategory }) -join ','").split(",")
    return bool(kinds) and "Private" not in kinds and "DomainAuthenticated" not in kinds and "Public" in kinds


def check(run: Run = powershell) -> bool:
    """Whether the rule is in place on a network that lets it work. False, after saying what to do, when not; nothing is changed."""
    if missing(run):
        warn(f"{NOT_ADDED}: {line()}")
        return False
    if public_only(run):
        warn(PUBLIC)
        return False
    return True
