"""The `phone` command of `poolhouse-workspace`: add the phone a person approves from, list the phones, remove one.

`add` and `remove` are a person's at a terminal. `add` turns on the listener for phones and shows a code to scan; that is the
whole of a further phone's setup; the first phone needs the person's yes to the question the node itself asks (docs/phone-approval.md,
"The first phone"). `remove` asks a key the node
holds to approve, with the typed yes. `list` and `offer` are any session's.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from poolhouse import node_launch, person
from poolhouse.board import person as person_session, phone, presence
from poolhouse.board.client import Client, Denied
from poolhouse.board.session import Session
from poolhouse.command import flag
from poolhouse.log import say, warn
from poolhouse.workspace import approval_cli, board_cli, qr

__all__ = ["COMMAND", "OPTIONS"]

VERBS = ("add", "list", "remove", "offer")
POLL_S = 1.0
OPTIONS = [
    flag("verb", choices=VERBS, help="add (a person: shows a code to scan), list, remove NAME (a person), offer (a link for one more phone)"),
    flag("name", nargs="?", default="", help="remove: the phone's name"),
    flag("--port", type=int, default=phone.PORT, help="add: the port phones talk to (default 7449)"),
    flag("--image", default="", metavar="FILE", help="add (for a further phone), offer: also write the code as an image file a screen can show"),
    flag("--wait", type=float, default=600.0, metavar="SECONDS", help="add: how long to wait for the phone (the code is good for ten minutes)"),
]


def _words(fingerprint: str) -> str:
    """The first 24 characters of a key's fingerprint in groups of four: what a person compares across two screens."""
    return " ".join(fingerprint[i:i + 4] for i in range(0, 24, 4))


def _row(p: dict[str, Any]) -> str:
    return f"{p['name']:<24} {p['state']:<8} key {_words(p['fingerprint'])}  added {time.strftime('%Y-%m-%d %H:%M', time.gmtime(p['added_ms'] / 1000))} UTC  {'push on' if p['push'] else 'no push'}"


def _listening(client: Client, port: int) -> dict[str, Any]:
    """The node's phone listener, turned on when it is not: running `phone add` is the person's yes."""
    info = client.call("phone_info")
    if not info["listening"]:
        say(f"This turns on the listener for your phone on port {port} of this device's local networks.")
        phone.listen_on(client.state, f"0.0.0.0:{port}")
        node_launch.stop_node(client.state)
        node_launch.ensure_node(client.state)
        info = client.call("phone_info")
    if not info["listening"]:
        raise Denied("denied", "the listener for phones did not start: see the node's log")
    if not info["endpoints"]:
        warn("this device has no address on a local network or a tailnet, so a phone cannot reach it yet: join a network and run this again")
    return dict(info)


def _show(code: str, image: str) -> None:
    say("Open the poolhouse app on your phone and scan this code (it works once, for ten minutes):\n")
    say(qr.terminal(code))
    if image:
        say(f"\nThe same code as an image: {qr.write_svg(Path(image), code)}")


def _code(client: Client, carrier: Session, further: bool) -> str:
    """The link to scan. The first phone's is the node's own, made when the person says yes to the question it asks (the operating system's
    prompt, or the person's terminal); a further phone's is asked of the node, which a phone that is already added approves."""
    if further:
        return str(carrier.call("phone_offer")["link"])
    return str(presence.ask(client, carrier.token, "phone", "", presence.Voice(say=say))["link"])


def _add(args: argparse.Namespace) -> int:
    person.require_person("adding a phone")
    client = Client()
    if args.image and not client.call("phone_info")["keys"]:
        raise Denied("denied", "The first phone is added from the code drawn in this terminal, with no image file: a file could be read by any program that runs as you, "
                     "and the first phone is added on the strength of the code alone. Run it again without --image.")
    carrier = person_session.on(client, approval_cli.POOL)
    _listening(client, args.port)
    before = {p["id"] for p in phone.phones(carrier)}
    code = _code(client, carrier, bool(client.call("phone_info")["keys"]))
    _show(code, args.image)
    end = time.monotonic() + args.wait
    say("\nWaiting for your phone...")
    while time.monotonic() < end:
        new = [p for p in phone.phones(carrier) if p["id"] not in before]
        if new:
            p = new[0]
            say(f"\nThe phone \"{p['name']}\" (key {_words(p['fingerprint'])}) " + (
                "is added." if p["state"] == "active" else "scanned the code and waits for one of your phones to approve it."))
            say("If that is not your phone, remove it: poolhouse-workspace phone remove \"" + p["name"] + "\"")
            return 0
        time.sleep(POLL_S)
    say("No phone scanned the code in time; run the command again for a new one.")
    return 3


def _remove(args: argparse.Namespace) -> int:
    person.require_person("removing a phone")
    carrier, key = approval_cli.held_key(Client())
    target = phone.find(phone.phones(carrier), args.name)
    say(f"This asks your key to remove the phone \"{target['name']}\" (key {_words(target['fingerprint'])}).")
    done = approval_cli.answer(carrier, key, dict(carrier.call("phone_remove", phone=target["id"])), "approve")
    say(f"{target['name']}: {done['state']}")
    return 0 if done["state"] == "approved" else 3


def _own(args: argparse.Namespace, s: Session) -> int:
    if args.verb == "list":
        held = phone.phones(s)
        say(json.dumps(held) if args.json else "\n".join(_row(p) for p in held if p["state"] != "removed") or "(no phone is added)")
        return 0
    link = str(s.call("phone_offer")["link"])
    say(json.dumps({"link": link}) if args.json else f"Open this link on the new phone (good once, for ten minutes; an added phone approves it):\n{link}")
    if args.image:
        say(f"The same code as an image: {qr.write_svg(Path(args.image), link)}")
    return 0


def command(args: argparse.Namespace) -> int:
    """The `phone` command."""
    if args.verb == "add":
        return _add(args)
    if args.verb == "remove":
        return _remove(args)
    return _own(args, board_cli.connect(args))


COMMAND = board_cli.guarded(command)
