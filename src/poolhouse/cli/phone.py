"""``poolhouse phone``: link a phone, list and remove phones, switch one into the pool and connect projects, through this computer's
running daemon (the same operations as the app's Phones screen). ``hook EVENT`` runs one hook of a connected project
(`poolhouse.phone_hook`) on the harness's event, read from standard input.

Linking a phone, removing one and switching it into the pool are the person's: a linked phone speaks as them, so a command an agent
runs is refused at once."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from poolhouse import lazy, phone_hook
from poolhouse.command import Group, flag
from poolhouse.log import say
from poolhouse.phone_mailbox import Store

__all__ = ["main", "terminal_qr"]

TIMEOUT_S = 30.0
WHY = "a linked phone speaks as you to your agents, so only you link, remove or switch one"
COMMANDS = Group("poolhouse-phone", "Phones linked to this computer.", errors=(OSError, RuntimeError, ValueError))
main = COMMANDS.run


class _Daemon:
    """This computer's daemon, signed in with a one-use ticket as the app's window signs in. What reaches it is loaded on use: the
    harness runs `hook` after every tool call, which loads none of it."""

    def __init__(self, root: Path | None = None) -> None:
        self.http = lazy.load("poolhouse.http")
        secret = lazy.load("poolhouse.fleet.launch_secret")
        record = secret.read(root or lazy.load("poolhouse.fleet.runtime_paths", "default_root")())
        self.base = f"http://127.0.0.1:{int(record['port'])}"
        got = self._ask("POST", "/ui/launch/ticket", {}, {secret.HEADER: str(record["secret"])})
        signing = {"X-Poolhouse-UI": "1", "Content-Type": "application/json"}
        with self.http.open_stream(f"{self.base}/ui/session", data=json.dumps({"ticket": got["ticket"]}).encode(), method="POST",
                                   options=self.http.Options(headers=signing, timeout=TIMEOUT_S)) as answer:
            self.cookie = answer.headers.get("Set-Cookie", "").split(";")[0]

    def _ask(self, method: str, path: str, payload: dict[str, Any] | None, headers: dict[str, str]) -> dict[str, Any]:
        options = self.http.Options(headers={"X-Poolhouse-UI": "1", **headers}, timeout=TIMEOUT_S)
        return self.http.request_json(f"{self.base}{path}", payload=payload, method=method, options=options)

    def call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._ask(method, path, payload, {"Cookie": self.cookie})


def _persons(action: str) -> None:
    """Refuse ``action`` at once to an agent's process."""
    lazy.load("poolhouse.caller", "refuse_agent")(action, WHY)


def terminal_qr(text: str) -> str:
    """The QR code of ``text`` in half-block characters, light modules drawn, for a terminal of either theme."""
    rows = lazy.load("poolhouse.fleet.phone.qr", "matrix")(text)
    width = len(rows) + 4
    padded = [[False] * width] * 2 + [[False, False, *row, False, False] for row in rows] + [[False] * width] * 2
    if len(padded) % 2:
        padded.append([False] * width)
    drawn = {(False, False): "█", (False, True): "▀", (True, False): "▄", (True, True): " "}
    return "\n".join("".join(drawn[(top, below)] for top, below in zip(padded[i], padded[i + 1], strict=True))
                     for i in range(0, len(padded), 2))


@COMMANDS.command("link", help="show a code that links one phone, for ten minutes")
def link(args: argparse.Namespace) -> int:
    _persons("link a phone")
    made = _Daemon().call("POST", "/ui/phones/offer", {})
    say(terminal_qr(made["link"]))
    say("Scan this in the Poolhouse app, or type in it:")
    say(f"  address  {' or '.join(made['endpoints'])}")
    say(f"  code     {made['code']}")
    say("The code links one phone and lasts ten minutes.")
    return 0


@COMMANDS.command("list", help="the linked phones, whether each is in the pool, and the connected projects")
def listed(args: argparse.Namespace) -> int:
    held = _Daemon().call("GET", "/ui/phones")
    for phone in held["phones"]:
        pool = "in the pool" if phone["pool_use"] else "not in the pool"
        say(f"{phone['id']}  {phone['name']}  {'online' if phone['online'] else 'offline'}  {pool}  key {phone['words']}")
    if not held["phones"]:
        say("no phone is linked; `poolhouse phone link` shows a code")
    for project in held["projects"]:
        say(f"project {project['path']}  {'connected' if project['connected'] else 'off'}")
    return 0


@COMMANDS.command("remove", help="unlink a phone: it can do nothing more here", options=[flag("phone")])
def remove(args: argparse.Namespace) -> int:
    _persons("remove a phone")
    _Daemon().call("DELETE", f"/ui/phones/{args.phone}")
    say(f"removed {args.phone}")
    return 0


@COMMANDS.command("pool-use", help="switch a phone into or out of the pool (off until switched on)",
                  options=[flag("phone"), flag("state", choices=("on", "off"))])
def pool_use(args: argparse.Namespace) -> int:
    _persons("switch a phone into the pool")
    _Daemon().call("POST", f"/ui/phones/{args.phone}/pool-use", {"enabled": args.state == "on"})
    say(f"{args.phone} is {'in' if args.state == 'on' else 'out of'} the pool")
    return 0


def _project(args: argparse.Namespace, on: bool) -> int:
    folder = str(Path(args.project).expanduser().resolve())
    _Daemon().call("POST", "/ui/phones/projects", {"path": folder, "connected": on})
    say(f"{'connected' if on else 'disconnected'} {folder}")
    return 0


@COMMANDS.command("connect", help="let linked phones talk to the Claude Code sessions of a project",
                  options=[flag("--project", default=".", metavar="DIR")])
def connect(args: argparse.Namespace) -> int:
    return _project(args, True)


@COMMANDS.command("disconnect", help="stop linked phones talking to a project's sessions",
                  options=[flag("--project", default=".", metavar="DIR")])
def disconnect(args: argparse.Namespace) -> int:
    return _project(args, False)


@COMMANDS.command("hook", help="run one hook of a project connected to linked phones; the harness calls it",
                  options=[flag("event", choices=sorted(phone_hook.EVENTS))])
def hook(args: argparse.Namespace) -> int:
    """Status 2 wakes the session with what is on standard error; any failure is reported and lets the session go on."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        return phone_hook.run(args.event, event if isinstance(event, dict) else {}, Store(), sys.stdout, sys.stderr)
    except (OSError, ValueError, RuntimeError) as why:
        sys.stderr.write(f"poolhouse phone hook {args.event}: {type(why).__name__}: {why}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
