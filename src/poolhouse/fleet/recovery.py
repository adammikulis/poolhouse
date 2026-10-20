"""The pool passphrase kept in the OS keystore, and the recovery file that joins a machine without it."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import authority, home, keystore, lock
from poolhouse.files import read_json, write_json
from poolhouse.log import say, warn
from poolhouse.platform import private_file
from poolhouse.sentinel import human

from . import studio_epoch
from .discovery import (
    DEFAULT_POOL,
    DiscoveryError,
    Membership,
    adopt,
    memberships,
    pools_path,
    require_name,
)

PURPOSE = "fleet-passphrase"
HEADER = (
    "# poolhouse pool recovery file",
    "# Anyone holding this file can run commands on every machine in the pool.",
    "# `poolhouse-pool recovery import FILE` joins a machine with it; it does not reveal the passphrase.",
)


def _store() -> keystore.Keystore:
    return keystore.default()


def passphrases_path(path: Path | str | None = None) -> Path:
    """The file holding the keystore-wrapped passphrases, beside the memberships."""
    return pools_path(path).with_suffix(".passphrases")


def _held(path: Path | str | None) -> dict[str, str]:
    raw = read_json(passphrases_path(path), {})
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def remember(passphrase: str, group: str = DEFAULT_POOL, path: Path | str | None = None,
             say: Callable[[str], None] = warn) -> None:
    """Store ``passphrase`` for ``group`` under the keystore; ``say`` gets a sentence when it could not."""
    try:
        blob = _store().wrap(PURPOSE, group, passphrase.strip().encode())
        target = passphrases_path(path)
        with lock.rewriting(target):
            rows = _held(path)
            rows[group] = base64.b64encode(blob).decode()
            write_json(target, rows)
            private_file(target)
    except (keystore.KeystoreError, lock.Busy, OSError) as exc:
        say(f"The passphrase was not saved ({exc}); keep it, or export a recovery file.")


def recall(group: str = "", path: Path | str | None = None) -> str | None:
    """The stored passphrase for ``group`` (default: the first pool), or None."""
    if not group:
        rows = memberships(path)
        group = rows[0].group if rows else DEFAULT_POOL
    blob = _held(path).get(group)
    if blob is None:
        return None
    return _store().unwrap(PURPOSE, group, base64.b64decode(blob)).decode()


def forget(group: str, path: Path | str | None = None) -> None:
    """Drop the stored passphrase for ``group``; a file another command holds, or one that cannot be
    locked, is reported and left as it was."""
    leaving = [hashlib.sha256(member.key).hexdigest() for member in memberships(path) if member.group == group]
    if leaving:
        studio_epoch.invalidate(pools_path(path).parent, leaving, "forget")
    try:
        with lock.rewriting(passphrases_path(path)):
            rows = _held(path)
            kept = {g: v for g, v in rows.items() if g != group}
            if kept == rows:
                return
            if kept:
                write_json(passphrases_path(path), kept)
            else:
                passphrases_path(path).unlink(missing_ok=True)
    except (lock.Busy, OSError) as exc:
        warn(f"The stored passphrase for {group} was not removed ({exc}); try again.")


def export_recovery(file: Path | str, group: str = "", path: Path | str | None = None) -> Membership:
    """Write the group and key of a pool this machine is in to ``file`` (mode 600)."""
    rows = memberships(path)
    held = next((m for m in rows if not group or m.group == group), None)
    if held is None:
        raise DiscoveryError("this machine is in no pool" if not group else f"not in a pool called '{group}'")
    body = json.dumps({"group": held.group, "key": held.key.decode(), "mode": held.mode}, indent=1)
    target = home.expand(file)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write("\n".join(HEADER) + "\n" + body + "\n")
    private_file(target)
    return held


def parse_recovery(text: str) -> Membership:
    """Read a named 256-bit key from bounded recovery-file text."""
    try:
        if not isinstance(text, str) or len(text.encode("utf-8")) > 8192:
            raise ValueError("recovery file is too large")
        data = json.loads("\n".join(ln for ln in text.splitlines() if not ln.startswith("#")))
        if not isinstance(data, dict) or not isinstance(data.get("key"), str):
            raise ValueError("invalid recovery object")
        key = data["key"]
        if len(base64.b64decode(key + "=" * (-len(key) % 4), altchars=b"-_", validate=True)) != 32:
            raise ValueError("invalid recovery key")
        return Membership(group=require_name(data.get("group")), key=key.encode("ascii"),
                          mode=data["mode"], selection="manual")
    except (KeyError, ValueError, TypeError, UnicodeError, DiscoveryError) as exc:
        raise DiscoveryError("not a valid pool recovery file") from exc


def import_recovery(file: Path | str, path: Path | str | None = None) -> Membership:
    """Join from a recovery file, replacing any membership of the same pool."""
    with home.expand(file).open() as source:
        member = parse_recovery(source.read(8193))
    return adopt(member, path)


def add_commands(sub: Any) -> None:
    """Register ``passphrase`` and ``recovery export|import`` on a subparsers object."""
    p = sub.add_parser("passphrase", help="print the stored passphrase (a person at a terminal)")
    p.add_argument("--group", default="", help="which pool (default: the first)")
    r = sub.add_parser("recovery", help="export this machine's pool key to FILE, or import FILE to join "
                       "without the passphrase (export needs a person at a terminal)")
    r.add_argument("op", choices=("export", "import"))
    r.add_argument("file")
    r.add_argument("--group", default="", help="export: which pool (default: the first)")


def run(args: argparse.Namespace) -> int:
    """Run ``passphrase`` or ``recovery``; the exit status."""
    path = args.pool_key
    try:
        if args.cmd == "passphrase":
            human.require_person("passphrase")
            words = recall(args.group, path)
            if words is None:
                warn("no passphrase is stored for this pool")
                return 1
            say(words)
        elif args.op == "export":
            authority.require("fleet.recovery", "recovery export")
            held = export_recovery(args.file, args.group, path)
            say(f"wrote the key for pool '{held.group}' to {args.file}")
            say("Anyone holding that file can run commands on every machine in the pool.")
        else:
            held = import_recovery(args.file, path)
            say(f"joined pool '{held.group}'")
    except (human.HumanRequired, DiscoveryError, keystore.KeystoreError, OSError) as exc:
        warn(f"error: {exc}")
        return 2
    return 0
