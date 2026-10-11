"""What runs on a device, Mac or Linux: start or stop its node under the real supervisor, and make one call to the node's local API.

    python tests/pool_check/wire.py start --state DIR --binary PATH [--arg ARG ...]
    python tests/pool_check/wire.py stop  --state DIR
    python tests/pool_check/wire.py call  --state DIR METHOD [--board B] [--token T] [--params JSON]
    python tests/pool_check/wire.py expect-enrol --state DIR

Every command prints one JSON line: ``{"ok": true, "result": ...}`` or ``{"ok": false, "code": ..., "error": ...}``. The rig runs
this file the same way on both kinds of device (a subprocess on the Mac, `docker exec` in a container), so a device's node is
started, supervised and asked through the same code wherever it runs. Unlike `board.client.Client` it never starts a node
that is not running: a node the rig killed stays dead until the supervisor, or the rig, brings it back.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from poolhouse import node_binary, node_launch, node_supervise
from poolhouse.board import grants
from poolhouse.node_health import API_VERSION, exchange


def call(state: Path, method: str, board: str = "", token: str = "", params: dict | None = None) -> dict:
    """The reply envelope of one request to the node on ``state``'s socket."""
    request: dict = {"v": API_VERSION, "id": 1, "method": method, "params": params or {}}
    if board:
        request["board"] = board
    if token:
        request["token"] = token
    reply = json.loads(exchange(state, json.dumps(request).encode(), 120.0))
    if reply.get("ok") is True:
        return {"ok": True, "result": reply["result"]}
    return {"ok": False, "code": reply["error"]["code"], "error": reply["error"]["message"]}


def post_many(state: Path, session: tuple[str, str], count: int, size: int, prefix: str) -> dict:
    """Write ``count`` messages of about ``size`` bytes as ``session`` (a board and a token) in one process: the quick way to give a sync something to carry."""
    (board, token), filler = session, "x" * size
    for number in range(count):
        out = call(state, "post", board, token, {"kind": "message", "fields": {"type": "note", "to": "#general", "body": f"{prefix} {number} {filler}", "subject": ""}})
        if not out["ok"]:
            return out
    return {"ok": True, "result": {"posted": count}}


def start(state: Path, binary: Path, args: list[str]) -> dict:
    """Pin ``binary`` for the node of ``state`` and start it under its supervisor with ``args``; its health."""
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    node_supervise.point(state, binary, node_binary.sha256(binary))
    # the rig posts 1,500 entries in one burst to cut a sync in half: the per-sender limit of a session is the node's, not the step's subject
    (state / "write-limits.json").write_text(json.dumps({"sends": 1_000_000, "subagent_sends": 1_000_000}))
    return {"ok": True, "result": node_launch.ensure_node(state, extra=args)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "stop", "call", "post-many", "expect-enrol"):
        one = sub.add_parser(name)
        one.add_argument("--state", required=True)
        if name == "start":
            one.add_argument("--binary", required=True)
            one.add_argument("--arg", action="append", default=[])
        if name == "post-many":
            one.add_argument("--board", required=True)
            one.add_argument("--token", required=True)
            one.add_argument("--count", type=int, required=True)
            one.add_argument("--size", type=int, required=True)
            one.add_argument("--prefix", required=True)
        if name == "call":
            one.add_argument("method")
            one.add_argument("--board", default="")
            one.add_argument("--token", default="")
            one.add_argument("--params", default="{}")
    args = ap.parse_args(argv)
    state = Path(args.state)
    try:
        if args.cmd == "start":
            out = start(state, Path(args.binary), [f"{a}" for a in args.arg])
        elif args.cmd == "expect-enrol":
            out = {"ok": True, "result": {"secret": grants.expect_enrolment(state)}}
        elif args.cmd == "post-many":
            out = post_many(state, (args.board, args.token), args.count, args.size, args.prefix)
        elif args.cmd == "stop":
            out = {"ok": True, "result": {"stopped": node_launch.stop_node(state)}}
        else:
            out = call(state, args.method, args.board, args.token, json.loads(args.params))
    except (OSError, ValueError) as exc:
        out = {"ok": False, "code": type(exc).__name__, "error": str(exc)}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
