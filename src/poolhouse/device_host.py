"""What setting a device up does to the machine it runs on: Windows firewall rules and the WSL restart; the WSL config is only read.

`device_setup` decides; this acts. Windows things go through PowerShell (from WSL, the Windows `powershell.exe`).
Nothing here opens a socket or prints a secret.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from poolhouse.home import user_home
from poolhouse.lan import NODE_RULES, RULES

HYPERV_ID = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
POWERSHELL = "powershell.exe"
TIMEOUT = 600

NOT_ELEVATED = ("the Windows firewall rules need an administrator's PowerShell, and nothing in Poolhouse asks for one: run this from an administrator "
                "PowerShell once, or add the rules there yourself")
_ELEVATED = "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"
_HYPERV_PRESENT = "(Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue)"


def host_kind(platform: str, proc_version: str) -> str:
    """`windows`, `wsl`, `mac` or `linux`."""
    if platform == "win32":
        return "windows"
    if platform == "darwin":
        return "mac"
    return "wsl" if "microsoft" in proc_version.lower() else "linux"


def current_kind() -> str:
    """The kind of the machine this runs on."""
    try:
        version = Path("/proc/version").read_text(encoding="utf-8")
    except OSError:
        version = ""
    return host_kind(sys.platform, version)


def _powershell(script: str) -> str:
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    return done.stdout.strip()


def rules_script() -> str:
    """PowerShell that adds each missing inbound rule, on private networks only: Windows's, and the node's ports of the WSL VM."""
    adds = "; ".join(f"if (-not (Get-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue)) "
                     f"{{ New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow -Profile Private -Protocol {proto} -LocalPort {port} | Out-Null }}"
                     for name, proto, port in RULES)
    vm = "; ".join(f"if (-not (Get-NetFirewallHyperVRule -Name '{_vm_rule(name)}' -ErrorAction SilentlyContinue)) "
                   f"{{ New-NetFirewallHyperVRule -Name '{_vm_rule(name)}' -DisplayName '{name}' -Direction Inbound -Action Allow -Profiles Private "
                   f"-VMCreatorId '{HYPERV_ID}' -Protocol {proto} -LocalPorts {port} | Out-Null }}"
                   for name, proto, port in NODE_RULES)
    return f"{adds}; if ({_HYPERV_PRESENT}) {{ {vm} }}"


def _vm_rule(name: str) -> str:
    return name.replace(" ", "-") + "-wsl"


def firewall_missing() -> bool:
    """Whether the inbound rules (Windows's or the WSL VM's) still need adding."""
    names = ",".join(f"'{name}'" for name, _, _ in RULES)
    vm_names = ",".join(f"'{_vm_rule(name)}'" for name, _, _ in NODE_RULES)
    script = (f"$m = @({names}) | Where-Object {{ -not (Get-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue) }}; "
              f"$h = if ({_HYPERV_PRESENT}) {{ @({vm_names}) | Where-Object {{ -not (Get-NetFirewallHyperVRule -Name $_ -ErrorAction SilentlyContinue) }} }}; "
              "if ($m -or $h) { 'missing' } else { 'ok' }")
    return _powershell(script) != "ok"


def add_firewall() -> None:
    """Add the rules where this PowerShell is already an administrator's (the rule that allows it); refuse at once with the reason (`PermissionError`)
    where it is not: no administrator prompt is raised."""
    if _powershell(_ELEVATED) != "True":
        raise PermissionError(NOT_ELEVATED)
    _powershell(rules_script())


def wslconfig_path(kind: str) -> Path:
    """The Windows user's .wslconfig, seen from here."""
    if kind == "windows":
        return user_home() / ".wslconfig"
    profile = subprocess.run(["wslpath", "-u", _powershell("$env:USERPROFILE")], capture_output=True, text=True, timeout=TIMEOUT, check=False).stdout.strip()
    return Path(profile) / ".wslconfig"


def restart_wsl() -> None:
    """`wsl --shutdown`: ends this session when it runs from inside WSL."""
    subprocess.run(["wsl.exe", "--shutdown"], timeout=TIMEOUT, check=False)
