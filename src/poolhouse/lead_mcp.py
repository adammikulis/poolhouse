"""``python -m poolhouse.lead_mcp --state DIR --board B --lead L --name N``: the stdio MCP server `lead` of a delegate that runs without a sandbox.

Its two tools, `ask` and `note`, go to the session that started the delegate, as the delegate's own session. It reads standard input and writes
standard output, and listens on no port.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import IO, Any

from poolhouse import delegate_store as store, lead_bridge, node_launch
from poolhouse.board.client import NodeError
from poolhouse.errors import Denied
from poolhouse.isolation import client, session_verbs
from poolhouse.workspace.harness_session import HarnessSession

__all__ = ["run"]


def run(argv: Sequence[str] | None = None, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    """Serve the two tools until standard input ends."""
    words = list(sys.argv[1:] if argv is None else argv)
    given = dict(zip(words[::2], words[1::2], strict=True))
    state, board, lead, name = (given[key] for key in ("--state", "--board", "--lead", "--name"))
    os.environ["POOLHOUSE_HOME"] = state   # the delegate's own home holds no state
    held: list[lead_bridge.Bridge] = []

    def bridge() -> lead_bridge.Bridge:
        if not held:
            mine = HarnessSession(name, board=board, state=node_launch.default_state())
            held.append(lead_bridge.Bridge(mine.session(), lead, name, lambda said, detail="": store.set_status(name, said, detail)))
        return held[0]

    handler = lead_bridge.reach(bridge).run

    def listed() -> list[dict[str, Any]]:
        return [client.tool(tool, verb.help, verb.argument, verb.hint) for tool, verb in session_verbs.LEAD.items()]

    def called(tool: str, arguments: Mapping[str, Any]) -> tuple[bool, str]:
        try:
            return True, str(handler(tool, arguments))
        except (Denied, NodeError, OSError, ValueError, KeyError) as why:
            return False, f"{tool} failed: {type(why).__name__}"

    return client.serve(client.Streams(stdin or sys.stdin, stdout or sys.stdout, sys.stderr), listed, called)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
