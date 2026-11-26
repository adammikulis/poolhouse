"""Join this device to a pool by the beacon: start the node with its network on, and show who is in. A library: `poolhouse pool listen` and
`poolhouse pool join` are the commands.

A device in no pool joins an `open` pool whose signed beacon it hears on the same network; each enrolment is recorded (by `open`, with the certificate fingerprint) and can be revoked
with `member_remove`. A `secure` pool takes no one without a join code; a pool is founded on this device by its own key, with nothing asked. `pool-join-check` (scripts/) walks every step
of this on one device and says which fails and what to change.
"""

from __future__ import annotations

import time
from pathlib import Path

from poolhouse import node_binary, node_pool, node_supervise
from poolhouse.log import say
from poolhouse.node_health import call, node_health
from poolhouse.node_launch import NodeUnavailable, ensure_node, stop_node

DEFAULT_PORT = 7447
DEFAULT_BEACON_PORT = 7448
POLL_S = 1.0


class PoolError(OSError):
    """The node could not be made to join, and the message says what to do."""


EXPLANATION = """\
This turns on the node's network so this device can find and join another one on the same network.
  What:  the node listens on TCP {port} and sends and listens for a small signed beacon on UDP {beacon_port}.
  Why:   so two of your devices on the same wifi or cable can pool without a code (the pool's policy open),
         or be joined with one (secure). Nothing leaves this network.
  What you will see: your computer may ask to allow this program to find devices on your local network
         (macOS: Local Network; Windows: a firewall prompt). Allow it. It asks once, only now.
  Undo:  stop the node with python -m poolhouse.node_launch stop.
"""


def consent(port: int, beacon_port: int) -> None:
    """Show what turning the network on does. Nothing is asked and nothing waits.

    Nothing that runs by itself (a test, an install, a first run) calls this or turns the network on.
    """
    say(EXPLANATION.format(port=port, beacon_port=beacon_port))


def network_args() -> list[str]:
    """The arguments that turn the node's network on (`--lan`: every interface, TCP 7447, the beacon on UDP 7448)."""
    return node_pool.network_args()


def pin_binary(state: Path, binary: Path) -> None:
    """Make the node of ``state`` run ``binary`` (recorded with its checksum, checked at every start)."""
    if not binary.is_file():
        raise PoolError(f"{binary} is not a file; build it with: cargo build --release -p poolhouse-node (in app/)")
    node_supervise.point(state, binary, node_binary.sha256(binary))


def status(state: Path) -> dict:
    """The node's `pool_status`: the pool, the policy, the members, and what the beacon has sent and heard."""
    return call(state, "pool_status")


def _wants_restart(state: Path, port: int) -> bool:
    """Whether a running node has its network off, or on another port than the one asked for."""
    try:
        listen = status(state).get("listen")
    except (OSError, ValueError):
        return False
    return not listen or not str(listen).endswith(f":{port}")


def start(state: Path, *, port: int = DEFAULT_PORT, binary: Path | None = None,
          extra: list[str] | None = None) -> dict:
    """The node's health once it runs with its network on, restarting it when it ran without."""
    if binary is not None:
        pin_binary(state, binary)
    if node_health(state) is not None and _wants_restart(state, port):
        stop_node(state)
    try:
        return ensure_node(state, extra=[*network_args(), *(extra or [])])
    except (NodeUnavailable, node_binary.NodeBinaryError) as exc:
        raise PoolError(f"{exc}; build the node with: cargo build --release -p poolhouse-node (in app/), then pass --binary") from exc


def others(shown: dict) -> list[dict]:
    """The active members other than this device."""
    return [m for m in shown.get("members", []) if m.get("status") == "active" and not m.get("self")]


def join(state: Path, *, wait_s: float = 0.0, **start_args) -> dict:
    """Start the node with its network on and wait up to ``wait_s`` for another device; the pool status."""
    start(state, **start_args)
    end = time.monotonic() + wait_s
    while True:
        shown = status(state)
        if others(shown) or time.monotonic() >= end:
            return shown
        time.sleep(POLL_S)
