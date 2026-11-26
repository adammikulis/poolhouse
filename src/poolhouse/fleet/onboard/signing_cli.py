"""``poolhouse pool signing``: see the signing key; exporting, rotating and revoking it are refused to an agent at once."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from poolhouse import home
from poolhouse.caller import AgentRefused
from poolhouse.log import say
from poolhouse.sentinel.permit import mint

from .signing import UNLOCK_ENV, KeyStoreError, SigningKeys

__all__ = ["add_signing", "cmd_signing"]


def add_signing(sub: Any, common: Any) -> None:
    p = common(sub.add_parser("signing", help="the signing key: show it; export, rotate and revoke "
                                              "are refused to an agent"))
    p.add_argument("action", choices=["show", "export", "rotate", "revoke"])
    p.add_argument("value", nargs="?", default="",
                   help="export: the file to write, encrypted under the passphrase in "
                        f"${UNLOCK_ENV} (refused at once when it is not set); revoke: a key id")


def _emit(args: argparse.Namespace, document: dict[str, Any], text: str) -> None:
    say(json.dumps(document, indent=1, default=str) if args.json else text)


def cmd_signing(args: argparse.Namespace) -> int:
    directory = Path(args.state) if args.state else home.state("onboard")
    keys = SigningKeys(directory)
    try:
        return _run(args, keys, directory)
    except (AgentRefused, KeyStoreError) as exc:
        _emit(args, {"error": str(exc)}, f"error: {exc}")
        return 2


def _run(args: argparse.Namespace, keys: SigningKeys, directory: Path) -> int:
    doc = keys.meta()
    if args.action == "show":
        _emit(args, {"key_id": doc["key_id"], "store": doc["store"], "public": doc["public"],
                     "rotations": len(doc["rotations"]), "revoked": doc["revoked"]},
              f"signing key {doc['key_id']} ({doc['store']})")
    elif args.action == "export":
        grant = mint("export", doc["key_id"])
        passphrase = os.environ.get(UNLOCK_ENV, "")
        if not passphrase:
            why = f"set {UNLOCK_ENV} to the passphrase for the exported key; nothing is asked and nothing was written"
            _emit(args, {"error": why}, f"error: {why}")
            return 2
        keys.export(grant, Path(args.value), passphrase)
        _emit(args, {"exported": args.value}, f"wrote {args.value} (encrypted)")
    elif args.action == "rotate":
        fresh = keys.rotate(mint("rotate", doc["key_id"]))
        _emit(args, {"key_id": fresh["key_id"], "old": doc["key_id"]},
              f"new signing key {fresh['key_id']}; members see it announced and adopt it at their next fetch")
    elif args.action == "revoke":
        listed = keys.revoke(mint("revoke", args.value), args.value)
        _emit(args, {"revoked": listed}, f"revoked {args.value}")
    return 0
