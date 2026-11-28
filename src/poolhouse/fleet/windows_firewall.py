"""Windows only: whether the inbound rules other devices need to reach this one are in place, said with the reason and the line to run where they are not.

Nothing here changes the firewall: the installer and `python -m poolhouse.device_setup`, which a person runs, are what add rules, and no
administrator prompt is raised from here. The rules are `poolhouse.lan.RULES`, on private networks only.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from poolhouse.lan import RULES
from poolhouse.log import warn

POWERSHELL = "powershell.exe"
TIMEOUT_S = 300
PUBLIC = (r"Windows treats this network as Public, which blocks your other devices from reaching Poolhouse here: "
          r"in Settings > Network & internet, open this network's properties and choose Private")

NOT_ADDED = ("a Windows firewall rule that lets your other devices reach Poolhouse is not there, and starting Poolhouse does not change the "
             "firewall: install Poolhouse again and allow the administrator prompt, or run this line once in an administrator prompt")

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
    """``(name, protocol, port)`` of each rule."""
    return list(RULES)


def line() -> str:
    """The rules as one line a person can paste into an administrator's prompt."""
    return " && ".join(f'netsh advfirewall firewall add rule name="{name}" dir=in action=allow profile=private protocol={proto} localport={port}'
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
    """Whether the rules are in place on a network that lets them work. False, after saying what to do, when not; nothing is changed."""
    if missing(run):
        warn(f"{NOT_ADDED}: {line()}")
        return False
    if public_only(run):
        warn(PUBLIC)
        return False
    return True
