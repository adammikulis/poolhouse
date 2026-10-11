"""What runs on a device, Mac or Linux: start or stop its node under the real supervisor, and make one call to the node's local API.

    python tests/pool_check/wire.py start --state DIR --binary PATH [--arg ARG ...]
    python tests/pool_check/wire.py stop  --state DIR
    python tests/pool_check/wire.py call  --state DIR METHOD [--board B] [--token T] [--params JSON]
    python tests/pool_check/wire.py presence --state DIR --token T --purpose key|phone [--about TEXT]
    python tests/pool_check/wire.py phone-listen --state DIR
    python tests/pool_check/wire.py phone --state DIR OP [--token T] [--body JSON] [--method GET]
    python tests/pool_check/wire.py cert --state DIR

Every command prints one JSON line: ``{"ok": true, "result": ...}`` or ``{"ok": false, "code": ..., "error": ...}``. The rig runs
this file the same way on both kinds of device (a subprocess on the Mac, `docker exec` in a container), so a device's node is
started, supervised and asked through the same code wherever it runs. Unlike `board.client.Client` it never starts a node
that is not running: a node the rig killed stays dead until the supervisor, or the rig, brings it back.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
from pathlib import Path
from typing import Any

from poolhouse import node_binary, node_launch, node_supervise
from poolhouse.board import phone
from poolhouse.node_health import API_VERSION, exchange

PRESENCE = ["--presence", "terminal"]
"""How a node of a rig or a test asks the person for the first key or phone: on a terminal the node reads itself, where `answer_presence` types
yes. It is the node a person installs; only the installed software on the real home asks with the operating system's prompt."""


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


def _screen_says(master: int, text: str, seconds: float) -> bool:
    """Whether the pseudo-terminal's person side shows ``text`` within ``seconds``."""
    import select
    import time

    end, shown = time.monotonic() + seconds, b""
    while time.monotonic() < end and text.encode() not in shown:
        if select.select([master], [], [], 0.2)[0]:
            shown += os.read(master, 4096)
    return text.encode() in shown


def answer_presence(state: Path, token: str, purpose: str, about: str) -> dict:
    """Play the person at the node's own question: ask as the session ``token`` for the one-time secret of the first key or phone, name a
    pseudo-terminal of this device as the terminal, and type `yes` there when the node has written its question on it.

    This is what a person does at a terminal, against the node as shipped: the node writes the question itself and reads the line itself,
    so the answer does not pass through the process that asked (docs/pool-check.md). Only the rig's person runs it; the node has no
    switch that answers for anyone.
    """
    import pty
    import time

    master, slave = pty.openpty()
    try:
        asked = call(state, "presence_ask", "", token, {"purpose": purpose, "about": about, "terminal": os.ttyname(slave)})
        if not asked["ok"] or asked["result"]["state"] != "asking":
            return asked
        if not _screen_says(master, "Type yes", 20.0):
            return {"ok": False, "code": "TimeoutError", "error": "the node wrote no question on the terminal it was given"}
        os.write(master, b"yes\n")
        said, end = asked, time.monotonic() + 20
        while said["ok"] and said["result"]["state"] == "asking" and time.monotonic() < end:
            time.sleep(0.1)
            said = call(state, "presence_status", "", token)
        return said
    finally:
        os.close(master)
        os.close(slave)


def post_many(state: Path, session: tuple[str, str], count: int, size: int, prefix: str) -> dict:
    """Write ``count`` messages of about ``size`` bytes as ``session`` (a board and a token) in one process: the quick way to give a sync something to carry."""
    (board, token), filler = session, "x" * size
    for number in range(count):
        out = call(state, "post", board, token, {"kind": "message", "fields": {"type": "note", "to": "#general", "body": f"{prefix} {number} {filler}", "subject": ""}})
        if not out["ok"]:
            return out
    return {"ok": True, "result": {"posted": count}}


class Endpoint:
    """The node's phone endpoint, reached over TLS 1.3 with the one certificate it made trusted and nothing else."""

    def __init__(self, state: Path, port: int, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self.pem = ssl.DER_cert_to_PEM_cert((Path(state) / "phone.cert").read_bytes())

    def request(self, op: str, body: dict[str, Any] | None = None, *, token: str = "", method: str = "POST") -> tuple[int, dict[str, Any]]:
        """One request: the status and the JSON of the reply."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False
        context.load_verify_locations(cadata=self.pem)
        payload = json.dumps(body or {}).encode()
        auth = f"Authorization: Bearer {token}\r\n" if token else ""
        head = f"{method} /phone/v1/{op} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n{auth}Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
        with socket.create_connection((self.host, self.port), timeout=20) as raw, context.wrap_socket(raw, server_hostname="poolhouse-phone") as tls:
            tls.sendall(head.encode() + payload)
            got = b""
            while chunk := tls.recv(65536):
                got += chunk
        return int(got.split(b" ", 2)[1]), json.loads(got.partition(b"\r\n\r\n")[2] or b"{}")

    def ok(self, op: str, body: dict[str, Any] | None = None, *, token: str = "") -> dict[str, Any]:
        """The result of a request that must succeed."""
        status, reply = self.request(op, body, token=token)
        assert reply.get("ok") is True, (status, reply)
        return dict(reply["result"])


def phone_call(state: Path, op: str, token: str, body: dict, method: str) -> dict:
    """One request of a phone to the endpoint the node of ``state`` listens on, over TLS pinned to its certificate, made from this device: the status and the reply."""
    status, reply = Endpoint(state, int(call(state, "phone_info")["result"]["port"])).request(op, body, token=token, method=method)
    return {"ok": True, "result": {"status": status, "reply": reply}}


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
    for name in ("start", "stop", "call", "post-many", "presence", "cert", "phone-listen", "phone"):
        one = sub.add_parser(name)
        one.add_argument("--state", required=True)
        if name == "presence":
            one.add_argument("--token", required=True)
            one.add_argument("--purpose", required=True, choices=("key", "phone"))
            one.add_argument("--about", default="the pool check's person")
        if name == "phone":
            one.add_argument("op")
            one.add_argument("--token", default="")
            one.add_argument("--body", default="{}")
            one.add_argument("--method", default="POST")
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
        elif args.cmd == "presence":
            out = answer_presence(state, args.token, args.purpose, args.about)
        elif args.cmd == "phone-listen":
            phone.listen_on(state, "127.0.0.1:0")
            out = {"ok": True, "result": {"listening": True}}
        elif args.cmd == "phone":
            out = phone_call(state, args.op, args.token, json.loads(args.body), args.method)
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
