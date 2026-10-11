"""A device of the rig as the session the library's own code takes: the same calls `poolhouse.board.founding` makes against a node, sent to the node of that device.

Nothing here knows how a pool is founded. It turns the rig's `Refused` into the node's own refusal, so the library's code sees what it sees on a real machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pool_check.device import Device, Refused

from poolhouse.board.client import Denied, Invalid, NodeError

POOL_BOARD = "pool"


class RigClient:
    """The node's client for one device of the rig."""

    def __init__(self, device: Device) -> None:
        self.device = device
        self.state = Path(device.state_text)

    def call(self, method: str, board: str = "", token: str = "", **params: Any) -> Any:
        try:
            return self.device.call(method, board, token, **params)
        except Refused as refused:
            kind = {"denied": Denied, "invalid": Invalid}.get(refused.code, NodeError)
            raise kind(refused.code, f"{refused.code}: {refused.message}") from None


class RigSession:
    """The `pool` board's session of a device, with the token its admin session holds."""

    def __init__(self, device: Device) -> None:
        self.client = RigClient(device)
        self.token, self.name = device.admin_session()
        self.board = POOL_BOARD

    def call(self, method: str, **params: Any) -> Any:
        return self.client.call(method, self.board, self.token, **params)
