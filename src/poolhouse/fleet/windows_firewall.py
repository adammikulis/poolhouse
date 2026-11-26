"""Windows only: the inbound rule the daemon needs to be reached, added where this shell is already an administrator's and refused at once, with the
reason and the line to run, where it is not: no administrator prompt is ever raised.

The rule is TCP for the daemon's port, on private networks. The Rust node's own ports are opened by `poolhouse.device_host`.
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

NOT_ELEVATED = ("Windows lets only an administrator's PowerShell add the rule that lets your other devices reach Poolhouse, and nothing in Poolhouse asks for "
                "one: run this line once in an administrator prompt")
_ELEVATED = "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"

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


def elevated(run: Run = powershell) -> bool:
    """Whether this PowerShell is an administrator's already."""
    return run(_ELEVATED) == "True"


def add(absent: list[tuple[str, str, int]], run: Run = powershell) -> None:
    """Add ``absent`` in this PowerShell, which the caller found to be an administrator's."""
    run("; ".join(f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow -Profile Private -Protocol {proto} -LocalPort {port} | Out-Null"
                  for name, proto, port in absent))


def public_only(run: Run = powershell) -> bool:
    """Whether every network this Windows is connected to is classed Public."""
    kinds = run("(Get-NetConnectionProfile | ForEach-Object { $_.NetworkCategory }) -join ','").split(",")
    return bool(kinds) and "Private" not in kinds and "DomainAuthenticated" not in kinds and "Public" in kinds


def ensure(run: Run = powershell) -> bool:
    """Make sure the rules are in place: added where this shell is an administrator's, refused at once with the reason and the line to run where it is
    not. False, after saying what to do, when they are not in place."""
    absent = missing(run)
    if absent and not elevated(run):
        warn(f"{NOT_ELEVATED}: {line()}")
        return False
    if absent:
        add(absent, run)
        absent = missing(run)
    if absent:
        warn(f"Windows did not add the firewall rules, so your other devices cannot reach this one: {NOT_ELEVATED}: {line()}")
        return False
    if public_only(run):
        warn(PUBLIC)
        return False
    return True
