"""What runs on a device, Mac or Linux: start or stop its node under the real supervisor, and make one call to the node's local API.

    python tests/pool_check/wire.py start --state DIR --binary PATH [--arg ARG ...] [--key HEX [--level phone|native|computer|weak]]
    python tests/pool_check/wire.py stop  --state DIR
    python tests/pool_check/wire.py call  --state DIR METHOD [--board B] [--token T] [--params JSON]
    python tests/pool_check/wire.py presence --state DIR --public HEX
    python tests/pool_check/wire.py cert --state DIR

Every command prints one JSON line: ``{"ok": true, "result": ...}`` or ``{"ok": false, "code": ..., "error": ...}``. The rig runs
this file the same way on both kinds of device (a subprocess on the Mac, `docker exec` in a container), so a device's node is
started, supervised and asked through the same code wherever it runs. Unlike `board.client.Client` it never starts a node
that is not running: a node the rig killed stays dead until the supervisor, or the rig, brings it back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from poolhouse import node_binary, node_launch, node_supervise
from poolhouse.node_health import API_VERSION, exchange

PRESENCE = ["--presence", "auto"]
"""How a node of a rig or a test asks for the first key: nobody is asked (`auto`), the way the installed node runs."""


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


def answer_presence(state: Path, public: str) -> dict:
    """Ask as the person's own session (registered with the secret the node keeps for the person) for the one-time secret of the first key.
    The node asks nobody (`--presence auto`), so the answer is there at once; the reply is the node's own status of the ask."""
    me = call(state, "register_person", "pool", "", {"session": "the-rig-person", "proof": (Path(state) / "person" / "ui.secret").read_text().strip()})
    if not me["ok"]:
        return me
    return call(state, "presence_ask", "", me["result"]["token"], {"purpose": "key", "public": public, "terminal": ""})


def post_many(state: Path, session: tuple[str, str], count: int, size: int, prefix: str) -> dict:
    """Write ``count`` messages of about ``size`` bytes as ``session`` (a board and a token) in one process: the quick way to give a sync something to carry."""
    (board, token), filler = session, "x" * size
    for number in range(count):
        out = call(state, "post", board, token, {"kind": "message", "fields": {"type": "note", "to": "#general", "body": f"{prefix} {number} {filler}", "subject": ""}})
        if not out["ok"]:
            return out
    return {"ok": True, "result": {"posted": count}}


CLAIMS = {
    "weak": {"method": "keystore-typed", "os": "macos", "prompt": "typed-yes"},
    "computer": {"method": "keystore-prompt", "os": "macos", "prompt": "os-dialog"},
    "native": {"method": "helper", "os": "macos", "prompt": "os-dialog"},
    "phone": {"method": "phone-keystore", "os": "android", "prompt": "biometric"},
}
"""The claim the person-keys file records for a key of each level: what the enrolment that earns the level would have said."""


def plant_key(state: Path, public: str, level: str = "phone") -> None:
    """Write the Ed25519 key ``public`` (hex) into the person keys file of ``state`` with the claim of ``level``, as the enrolment that earns
    that claim records it: the node reads the file when it starts and lists the key in a pool with that claim. The key is planted, not held in
    custody: no phone, protected helper or prompt is behind the claim. Only a file the account owns can do this."""
    path = state / "persons.json"
    held = json.loads(path.read_text()) if path.exists() else {}
    held[hashlib.sha256(bytes.fromhex(public)).hexdigest()] = {"public": public, "label": f"the pool check's {level} key", "at_ms": 1, "alg": "ed25519", "claim": CLAIMS[level]}
    path.write_text(json.dumps(held))


def start(state: Path, binary: Path, args: list[str], key: str = "", level: str = "phone") -> dict:
    """Pin ``binary`` for the node of ``state`` and start it under its supervisor with ``args``, after ``key`` was planted in its person keys at ``level``; its health."""
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if key:
        plant_key(state, key, level)
    node_supervise.point(state, binary, node_binary.sha256(binary))
    return {"ok": True, "result": node_launch.ensure_node(state, extra=args)}


def start_options(one: argparse.ArgumentParser) -> None:
    """The options of `start`: the node binary, its arguments and the key (and its level) planted in its person keys before it starts."""
    one.add_argument("--binary", required=True)
    one.add_argument("--arg", action="append", default=[])
    one.add_argument("--key", default="")
    one.add_argument("--level", default="phone")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "stop", "call", "post-many", "presence", "cert"):
        one = sub.add_parser(name)
        one.add_argument("--state", required=True)
        if name == "presence":
            one.add_argument("--public", required=True)
        if name == "start":
            start_options(one)
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
            out = start(state, Path(args.binary), [f"{a}" for a in args.arg], args.key, args.level)
        elif args.cmd == "presence":
            out = answer_presence(state, args.public)
        elif args.cmd == "cert":
            out = {"ok": True, "result": {"cert": (state / "device.cert").read_bytes().hex()}}
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
