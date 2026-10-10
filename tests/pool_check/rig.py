"""The pool check: build one image, start N devices (the Mac and N-1 Linux containers), run every step, clean up, report.

    scripts/pool-check [--devices N] [--only STEP ...] [--list] [--clean]

See docs/pool-check.md for what each step proves and what none of them can.
"""

from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
import time
from pathlib import Path

from pool_check import (
    dock,
    steps_build,
    steps_members,
    steps_nat,
    steps_pool,
    steps_resilience,
    steps_talk,
    steps_tests,
)
from pool_check.device import PLATFORM_NAME, WslHostedDevice, free_port
from pool_check.report import Report
from pool_check.world import Layout, World

ROOT = dock.ROOT
SLOW_ENV = "POOLHOUSE_POOL_CHECK"
PLATFORMS = ("docker", "native", "windows-wsl")
STEPS = [
    ("join-open", steps_pool.join_open),
    ("join-secure", steps_pool.join_secure),
    ("join-wrong-code", steps_pool.a_wrong_code_is_refused),
    ("members-listed", steps_pool.members_listed),
    ("entries-converge", steps_pool.every_member_converges_on_every_entry),
    ("direct-message", steps_talk.a_direct_message_reaches_its_session),
    ("claim-pool-wide", steps_talk.a_claim_is_held_pool_wide),
    ("lease-queues", steps_resilience.a_lease_queues_behind_another_device),
    ("build-stale", steps_build.one_stale_device_shows_as_stale_only_for_itself),
    ("build-wire", steps_build.a_wire_mismatch_shows_needs_update_with_the_fix),
    ("build-update", steps_build.a_device_on_an_old_build_updates_itself_from_the_pool),
    ("join-order", steps_members.join_order_does_not_matter),
    ("member-removal", steps_members.a_removed_device_is_refused_by_every_member),
    ("cert-swap", steps_members.a_swapped_certificate_is_refused),
    ("partition-heal", steps_resilience.a_partition_heals_and_both_sides_merge),
    ("crash-mid-sync", steps_resilience.a_node_killed_mid_sync_resumes),
    ("tests-on-device", steps_tests.a_tiny_test_file_runs_on_a_linux_device),
    ("tests-on-undialable", steps_tests.a_member_nobody_can_dial_is_named_with_the_fix),
    ("nat-board", steps_nat.the_nat_device_joins_and_the_board_flows_both_ways),
    ("nat-claims", steps_nat.a_claim_and_a_lease_agree_with_the_nat_device),
    ("nat-build", steps_nat.the_mac_device_sees_the_build_of_the_nat_device),
    ("nat-tests", steps_nat.tests_on_the_nat_device_say_what_they_need),
    ("nat-removal", steps_nat.removal_reaches_the_nat_device),
    ("nat-setup-plan", steps_nat.the_wsl_setup_plan_names_the_nat_fixes),
    ("chain-of-dials", steps_members.sync_follows_the_chain_of_dials),
]


def node_binary(given: str) -> Path:
    """The node binary of this machine: the one named, else the one `cargo build -p poolhouse-node` makes here."""
    if given:
        return Path(given).resolve()
    done = subprocess.run(["cargo", "build", "-p", "poolhouse-node"], cwd=ROOT / "app", capture_output=True, text=True, check=False)
    if done.returncode:
        raise SystemExit(f"cargo build -p poolhouse-node failed:\n{done.stderr[-2000:]}")
    return ROOT / "app" / "target" / "debug" / ("poolhouse-node.exe" if sys.platform == "win32" else "poolhouse-node")


def parser() -> argparse.ArgumentParser:
    """The command line of scripts/pool-check."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", choices=PLATFORMS, default="docker", help="docker: this machine and Linux containers (needs Docker); native: every device is a node on this machine "
                    "(what a CI runner of any operating system runs; needs no Docker)")
    ap.add_argument("--devices", type=int, default=3, help="devices in the pool: this machine and N-1 more, Linux containers or native nodes (default 3)")
    ap.add_argument("--wsl", action="store_true", help="add a device behind NAT that dials out only and reads as WSL2 (docs/pool-check.md says what that reproduces)")
    ap.add_argument("--only", action="append", default=[], metavar="STEP", help="run only this step (repeatable)")
    ap.add_argument("--list", action="store_true", help="list the steps and exit")
    ap.add_argument("--binary", default="", help="the first device's poolhouse-node binary (default: built with cargo here)")
    ap.add_argument("--distro", default=dock.DISTRO, help=f"the Linux distribution image of the containers (default {dock.DISTRO}; debian:bookworm-slim and ubuntu:22.04 also work)")
    ap.add_argument("--wsl-distro", default="Ubuntu", help="with --platform windows-wsl: the WSL distribution that is the second device")
    ap.add_argument("--wsl-checkout", default="/root/poolhouse", help="with --platform windows-wsl: where this tree is installed inside that distribution")
    ap.add_argument("--wsl-binary", default="", help="with --platform windows-wsl: the node binary inside that distribution (the Windows device's is --binary)")
    ap.add_argument("--clean", action="store_true", help="remove this rig's Docker image when done (the next run builds it again)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    from poolhouse.log import say, warn

    if args.list:
        for name, step in STEPS:
            say(f"{name:22} {(step.__doc__ or '').strip().splitlines()[0]}")
        return 0
    hosted = None
    if args.platform == "windows-wsl":
        hosted = WslHostedDevice(name="wsl", port=free_port(), distro=args.wsl_distro, checkout=args.wsl_checkout, binary_text=args.wsl_binary)
    native = args.platform != "docker"
    if args.devices < 2:
        warn("a pool check needs at least 2 devices")
        return 2
    if native and args.wsl:
        warn("--wsl is a container behind NAT: it needs --platform docker")
        return 2
    if not native and (why := dock.available()):
        warn(why)
        return 2
    chosen = [(n, s) for n, s in STEPS if not args.only or n in args.only]
    unknown = set(args.only) - {n for n, _ in STEPS}
    if unknown:
        warn(f"no such step: {', '.join(sorted(unknown))}; --list names them")
        return 2
    binary = node_binary(args.binary)
    began = time.monotonic()
    image = "" if native else dock.build_image(args.distro)
    say(f"starting {args.devices if hosted is None else 2} native devices on {PLATFORM_NAME}" if native else
        f"image {image} ready in {time.monotonic() - began:.0f}s; starting {args.devices} devices ({PLATFORM_NAME} and {args.devices - 1} Linux containers)")
    run = secrets.token_hex(3)
    world = World(Layout(args.devices, binary, image, run, wsl=1 if args.wsl else 0, native=native, hosted=hosted))
    report = Report()
    left: list[str] = []
    try:
        for name, step in chosen:
            report.step(name, lambda step=step: step(world))
    finally:
        left = world.stop()
        if args.clean and not native:
            dock.remove_images()
    if left:
        warn(f"left behind by this run: {left}")
    say(report.summary())
    return 1 if report.failed or left else 0


if __name__ == "__main__":
    sys.exit(main())
