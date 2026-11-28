"""Set this device up to join a pool, the same one command on macOS, Linux, Windows and WSL.

    python -m poolhouse.device_setup [--dry-run] [--wait SECONDS]

It lists every change this device needs, makes them,
and ends with READY, or NOT READY and the one fix. Per platform: Windows and WSL get inbound firewall rules (only where the shell is
already an administrator's; otherwise refused at once with the reason, since nothing here raises an administrator prompt) and WSL gets
mirrored networking, which is a line in the Windows user's .wslconfig that this never writes (it says what to add; where the line is there already
WSL restarts, which ends this session, so run the same command again); every platform gets the node (a binary already
present, else a cargo build; this never installs Rust), started with its network on, then the join check.
Rerunning changes nothing that is already right. No secret is printed.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from poolhouse import device_host, node_binary, node_join, node_join_check, runtime
from poolhouse.command import Group, flag
from poolhouse.log import say, warn
from poolhouse.node_launch import default_state

POOLHOUSE_ENTRY_POINT = "connects this computer to its pool before anything is installed"
SECTION = "[wsl2]"
SETTING = "networkingMode"
WANTED = "mirrored"
WSL_FIX = ("add networkingMode=mirrored under [wsl2] in the Windows user's %UserProfile%\\.wslconfig (Poolhouse never writes that file), "
           "run `wsl --shutdown`, then run this again")
RUST_FIX = "Rust is not installed here, and Poolhouse never installs it: install it from https://rustup.rs, then run this again"
OFFSITE_DIRECTORIES = ("bundle", "dist")


def is_mirrored(text: str) -> bool:
    """Whether ``text`` of a .wslconfig sets `networkingMode=mirrored` under [wsl2]."""
    inside = False
    for line in text.splitlines():
        if line.strip().startswith("["):
            inside = line.strip().lower() == SECTION
            continue
        key, _, value = line.partition("=")
        if inside and key.strip().lower() == SETTING.lower():
            return value.strip().lower() == WANTED
    return False


@dataclass(frozen=True)
class Source:
    """Where the node binary comes from: `runtime`, `found`, `build` (cargo is here) or `missing` (it is not)."""
    kind: str
    path: str = ""


def node_source(repo: Path, *, selected: Path | None, cargo: str | None) -> Source:
    """The first of: the selected runtime's node, a release binary already in the repository, a cargo build; else Rust is missing."""
    if selected is not None and node_binary.location(selected).is_file():
        return Source("runtime", str(node_binary.location(selected)))
    for folder in (repo / "app" / "target" / "release", *(repo / name for name in OFFSITE_DIRECTORIES)):
        if (folder / node_binary.name()).is_file():
            return Source("found", str(folder / node_binary.name()))
    return Source("build") if cargo else Source("missing")


@dataclass(frozen=True)
class Needs:
    """What this device lacks: the kind of machine, its WSL networking mode, the .wslconfig setting, firewall rules, a node."""
    kind: str
    mode: str
    mirrored_set: bool
    firewall_missing: bool
    source: Source

    @property
    def restart_wsl(self) -> bool:
        return self.kind == "wsl" and self.mode == "nat" and self.mirrored_set

    @property
    def config_missing(self) -> bool:
        return self.kind == "wsl" and self.mode == "nat" and not self.mirrored_set


def plan(needs: Needs, *, port: int = node_join.DEFAULT_PORT, beacon: int = node_join.DEFAULT_BEACON_PORT) -> list[str]:
    """Each change the setup will make on this device, one line each, or the one it stops at."""
    steps = []
    if needs.firewall_missing:
        steps.append(f"add Windows inbound allow rules for TCP {port} and UDP {beacon} (only where this shell is already an administrator's, else refused with the reason)")
    if needs.config_missing:
        steps.append(f"stop: {WSL_FIX}")
        return steps
    if needs.restart_wsl:
        steps.append("restart WSL (mirrored networking takes effect then), which ends this session; run the same command again afterwards")
        return steps
    steps.append({"runtime": "use the node of the selected runtime", "found": f"use the node binary at {needs.source.path}",
                  "build": "build the node with cargo (a few minutes)",
                  "missing": f"stop: {RUST_FIX}"}[needs.source.kind])
    if needs.source.kind != "missing":
        steps.append(f"start the node with its network on (TCP {port}, UDP discovery {beacon}), and run the join check")
    return steps


def survey(repo: Path) -> Needs:
    """Look at this machine and say what it lacks."""
    kind = device_host.current_kind()
    windows = kind in ("windows", "wsl")
    mode = node_join_check.wsl_mode(node_join_check.route_address()) if kind == "wsl" else "mirrored"
    config = device_host.wslconfig_path(kind) if kind == "wsl" else None
    mirrored = config is not None and config.is_file() and is_mirrored(config.read_text(encoding="utf-8"))
    return Needs(kind, mode, mirrored, windows and device_host.firewall_missing(), node_source(repo, selected=runtime.selection_prefix(), cargo=shutil.which("cargo")))


def announce(steps: list[str]) -> None:
    """Show the steps before they are made. Nothing is asked and nothing waits."""
    say("This will change on this device:\n" + "\n".join(f"  - {s}" for s in steps))


def prepare(needs: Needs) -> str:
    """Do the Windows-side changes; the restart message when WSL had to restart, else ''."""
    if needs.firewall_missing:
        device_host.add_firewall()
        if device_host.firewall_missing():
            raise node_join.PoolError("the firewall rules were not added: " + device_host.NOT_ELEVATED)
    if needs.config_missing:
        raise node_join.PoolError(WSL_FIX)
    if needs.restart_wsl:
        say("Restarting WSL; run the same command again afterwards.")
        device_host.restart_wsl()
        return "restarting"
    return ""


def binary_of(needs: Needs, repo: Path) -> Path | None:
    """The node binary for this device, building it when it is not already there."""
    if needs.source.kind == "missing":
        raise node_join.PoolError(RUST_FIX)
    if needs.source.kind == "build":
        return node_binary.build(repo)
    return Path(needs.source.path)


def _setup(a) -> int:
    repo = Path(__file__).resolve().parents[2]
    try:
        needs = survey(repo)
        steps = plan(needs)
        if a.dry_run:
            say("\n".join(f"  - {s}" for s in steps))
            return 0
        announce(steps)
        if prepare(needs):
            return 0
        check = node_join_check.Check(state=default_state(), port=node_join.DEFAULT_PORT, beacon_port=node_join.DEFAULT_BEACON_PORT,
                                      wait_s=a.wait, binary=binary_of(needs, repo))
        node_join.consent(check.port, check.beacon_port)
        ok = node_join_check.walk(check)
        say(node_join_check.summary(check))
        return 0 if ok else 1
    except OSError as exc:
        warn(f"device-setup: NOT READY. {exc}")
        return 1


GROUP = Group("poolhouse.device_setup", "Set this device up to join a pool: the same command on macOS, Linux, Windows and WSL.", run=_setup, options=[
    flag("--dry-run", action="store_true", help="list the changes and make none"),
    flag("--wait", type=float, default=90.0, help="seconds to wait for another device at each step that needs it"),
])

main = GROUP.run


if __name__ == "__main__":
    sys.exit(main())
