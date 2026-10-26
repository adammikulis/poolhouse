"""Pool membership commands backed by this device's Rust node."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from poolhouse import errors
from poolhouse.api import _node, pool
from poolhouse.fleet.onboard.cli import add_service_commands, run as run_service
from poolhouse.fleet.plan import PREFERENCES
from poolhouse.fleet.runtime_paths import default_root
from poolhouse.log import say


def parser() -> argparse.ArgumentParser:
    """Return the parser for pool membership operations."""
    root = argparse.ArgumentParser(prog="poolhouse-pool", description=__doc__)
    commands = root.add_subparsers(dest="cmd", required=True)
    for name in ("status", "members", "found", "add-device", "join", "remove", "leave", "sync"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true", help="print the structured node result")
        if name == "found":
            command.add_argument("name")
            command.add_argument("--policy", choices=("open", "secure"), default="secure")
        elif name == "add-device":
            command.add_argument("--ttl", type=int, default=300, help="seconds the join code remains valid")
        elif name == "join":
            command.add_argument("code", nargs="?", default="")
            command.add_argument("--host", required=True, help="the device offering the pool")
            command.add_argument("--port", type=int, default=pool.DEFAULT_PORT)
            command.add_argument("--fingerprint", default="", help="the signed beacon's fingerprint for an open pool")
        elif name == "remove":
            command.add_argument("device")
        elif name == "members":
            command.add_argument("--include-removed", action="store_true")
    _compute_commands(commands)
    def common(command):
        command.add_argument("--json", action="store_true")
        command.add_argument("--state", default="")
        return command
    add_service_commands(commands, common)
    return root


def _compute_commands(commands) -> None:
    for name in ("plan", "pause", "resume"):
        command = commands.add_parser(name)
        command.set_defaults(cmd=name, pool_key=None, root=str(default_root()))
        command.add_argument("--json", action="store_true")
        command.add_argument("--port", type=int, default=8770)
        command.add_argument("--timeout", type=float, default=2.0)
        if name == "plan":
            command.add_argument("--users", type=int, required=True)
            command.add_argument("--context", type=int, required=True)
            command.add_argument("--prefer", choices=sorted(PREFERENCES), default="quality")
            command.add_argument("--apply", action="store_true")
        elif name == "pause":
            command.add_argument("--for", dest="span", default="")
            command.add_argument("--reason", default="")


def execute(args: argparse.Namespace) -> Any:
    """Run one operation through the node-backed pool API."""
    match args.cmd:
        case "status":
            return pool.status()
        case "members":
            return pool.members(include_removed=args.include_removed)
        case "found":
            return pool.found(args.name, args.policy)
        case "add-device":
            return pool.add_device(args.ttl)
        case "join":
            return pool.join(args.host, args.code, port=args.port, fingerprint=args.fingerprint)
        case "remove":
            return pool.remove(args.device)
        case "leave":
            return _node.call("pool_leave", token=_node.session().token)
        case "sync":
            pool.sync()
            return pool.status()
        case _:
            raise errors.Error("Unknown pool operation.")


def _structured(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_structured(one) for one in value]
    return value


def main(argv: list[str] | None = None) -> int:
    """Run the public pool command and preserve the node's operation state."""
    args = parser().parse_args(argv)
    try:
        if args.cmd in ("plan", "pause", "resume"):
            from poolhouse.fleet.join import cmd_pause, cmd_plan
            return (cmd_plan if args.cmd == "plan" else cmd_pause)(args)
        if args.cmd in ("bootstrap", "share", "fetch", "signing", "model-sources"):
            return run_service(args)
        result = _structured(execute(args))
    except errors.Error as exc:
        result = {"error": str(exc)}
        if isinstance(exc, pool.Refusal):
            result.update(needs=exc.needs, where=exc.where, then=exc.then)
        say(json.dumps(result) if args.json else str(exc))
        return 1
    say(json.dumps(result, indent=None if args.json else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
