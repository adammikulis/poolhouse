"""Windows only: the inbound rules the daemon needs to be found and reached, added with the one administrator prompt.

The rules are those `discovery.windows_firewall_rules` names: TCP for the daemon and UDP for its beacons, on private networks.
"""

from __future__ import annotations

import base64
import subprocess
from collections.abc import Callable

from poolhouse.log import say, warn

from . import discovery

POWERSHELL = "powershell.exe"
TIMEOUT_S = 300
PUBLIC = (r"Windows treats this network as Public, which blocks your other devices from reaching Poolhouse here: "
          r"in Settings > Network & internet, open this network's properties and choose Private")

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
    """``(name, protocol, port)`` of each rule: the daemon's TCP port and the beacons' UDP port."""
    return [(discovery.FIREWALL_RULE_HTTP, "TCP", discovery.DEFAULT_HTTP_PORT), (discovery.FIREWALL_RULE_DISCOVERY, "UDP", discovery.default_port())]


def missing(run: Run = powershell) -> list[tuple[str, str, int]]:
    """The rules this Windows does not hold."""
    names = ",".join(f"'{name}'" for name, _, _ in rules())
    held = set(run(f"@({names}) | Where-Object {{ Get-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue }}").splitlines())
    return [rule for rule in rules() if rule[0] not in held]


def add(absent: list[tuple[str, str, int]], run: Run = powershell) -> None:
    """Add ``absent`` in an elevated PowerShell: Windows asks the person once."""
    adds = "; ".join(f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow -Profile Private -Protocol {proto} -LocalPort {port} | Out-Null"
                     for name, proto, port in absent)
    encoded = base64.b64encode(adds.encode("utf-16-le")).decode("ascii")
    run(f"Start-Process {POWERSHELL} -Verb RunAs -Wait -ArgumentList '-NoProfile','-EncodedCommand','{encoded}'")


def public_only(run: Run = powershell) -> bool:
    """Whether every network this Windows is connected to is classed Public."""
    kinds = run("(Get-NetConnectionProfile | ForEach-Object { $_.NetworkCategory }) -join ','").split(",")
    return bool(kinds) and "Private" not in kinds and "DomainAuthenticated" not in kinds and "Public" in kinds


def ensure(run: Run = powershell) -> bool:
    """Make sure the rules are in place, asking Windows once; False, after saying what to do, when they are not."""
    absent = missing(run)
    if absent:
        say("Windows will ask once to let Poolhouse be found by your other devices: press Yes.")
        add(absent, run)
        absent = missing(run)
    if absent:
        warn("Windows did not add the firewall rules, so your other devices cannot find or reach this one: open Poolhouse again and press Yes when Windows asks.")
        return False
    if public_only(run):
        warn(PUBLIC)
        return False
    return True
