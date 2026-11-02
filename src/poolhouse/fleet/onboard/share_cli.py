"""``poolhouse pool share``: serve files to the devices that may have them.

``--models`` serves the machine's model store instead of a folder (`modelstore.py`): the GGUF and
safetensors files `hub.discover` finds in the usual folders (or only in the ``--models-dir``
folders), never a path outside them.

Each file has a sharing level (`sharing.py`). Programs are ``open``; a model is ``owner`` unless
said otherwise, because a model whose licence status is not known is treated as restricted.
For an ``owner`` file the first transfer asks the person at the terminal to confirm they
accepted its licence (typing the licence id back), and writes down who, when, which licence and
its URL. Without a terminal nothing is recorded and the file stays withheld.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from poolhouse import home, macauth, sentinel
from poolhouse.log import say, warn
from poolhouse.sentinel.human import HumanRequired, mint
from poolhouse.units import parse_duration

from ..service_pool import Pool
from ..tls import TlsUnavailable, identity
from . import modelstore
from .manifest import SHARING_LEVELS, Entry, Signer, verify
from .sharing import OPEN, OWNER, Access, Licences
from .signing import KeyStoreError, SigningKeys
from .signing_cli import confirm_signing
from .transfer import Share, ShareServer

__all__ = ["DEFAULT_SHARE_PORT", "add_share", "cmd_share", "quarantine_veto"]

logger = logging.getLogger(__name__)

DEFAULT_SHARE_PORT = 8773
"""Where ``pool share`` listens unless told otherwise."""

SUFFIX_KIND = {".whl": "wheel", ".gz": "sdist", ".zip": "sdist", ".gguf": "model",
               ".safetensors": "model"}


def add_share(sub: Any, common: Any) -> None:
    p = common(sub.add_parser("share", help="serve files, with a signed manifest, to the "
                                            "devices of this pool"))
    p.add_argument("--dir", default="", help="the files to share (a flat folder)")
    p.add_argument("--models", action="store_true",
                   help="share this machine's model store (GGUF and safetensors found in the "
                        "usual folders) instead of --dir; models go to the owner's own devices "
                        "once the owner has accepted their licences")
    p.add_argument("--models-dir", action="append", default=[], metavar="DIR",
                   help="with --models: look only in these folders (repeatable)")
    p.add_argument("--for", dest="span", default="10m")
    p.add_argument("--port", type=int, default=DEFAULT_SHARE_PORT,
                   help="the port to listen on (0: any)")
    p.add_argument("--host", default="", help="the address to listen on (default: every "
                                              "interface)")
    p.add_argument("--sharing", action="append", default=[], metavar="NAME=LEVEL",
                   help="open, owner or never for a file (default: programs open, models owner); "
                        "with --models NAME may also be a repository (owner/name)")
    p.add_argument("--licence", action="append", default=[], metavar="NAME=ID,URL",
                   help="the licence a model comes under")
    p.add_argument("--source", action="append", default=[], metavar="NAME=URL",
                   help="where a model comes from, for a device that must fetch it itself")


def _joins(items: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in items if "=" in item)


def _entries(share_dir: Path, args: argparse.Namespace) -> list[Entry]:
    level, licence, source = _joins(args.sharing), _joins(args.licence), _joins(args.source)
    out = []
    for f in sorted(share_dir.iterdir()):
        if not f.is_file():
            continue
        kind = SUFFIX_KIND.get(f.suffix, "other")
        chosen = level.get(f.name, OWNER if kind == "model" else OPEN)
        lid, _, url = licence.get(f.name, "").partition(",")
        out.append(Signer.entry_for(f, kind=kind, sharing=chosen, licence=lid,
                                    licence_url=url, source=source.get(f.name, "")))
    return out


def _confirm_licences(entries: list[Entry], licences: Licences) -> None:
    """Ask once, at a terminal, for each owner-level file whose licence is not on record."""
    for entry in entries:
        if entry.sharing != OWNER or not entry.licence or licences.accepted(entry):
            continue
        try:
            warn(f"{entry.name} is under {entry.licence} ({entry.licence_url or 'no URL'}).")
            licences.record(mint("accept-licence", entry.licence), entry)
        except HumanRequired as exc:
            warn(f"{entry.name} stays withheld: {exc}")


def _emit(args: argparse.Namespace, document: dict[str, Any], text: str) -> None:
    say(json.dumps(document, indent=1, default=str) if args.json else text)


def quarantine_veto(entry: Entry) -> str:
    """The serving side's veto: why a file sentinel holds in quarantine is not handed out.
    A download is quarantined under its sha256 (`poolhouse.net.hold`)."""
    try:
        if sentinel.default().store.blocked("artifact", f"download:{entry.sha256}"):
            return "this copy is in quarantine on the serving device"
    except (OSError, ImportError, ValueError) as exc:
        logger.warning("could not ask sentinel about %s, withholding it: %s", entry.name, exc)
        return "the serving device could not check its quarantine"
    return ""


def service_gate(pool: Pool):
    """The gate a `ShareServer` asks: a request signed with the secret the node derives for two of its authorized services."""
    def secrets_now() -> list[str]:
        own = pool.own().fingerprint
        return [pool.secret(row.fingerprint) for row in pool.services() if row.fingerprint != own]

    auth = macauth.Authenticator(secrets_now)

    def gate(method: str, target: str, headers: Any, who: str) -> Access | None:
        return Access() if auth.check(method, target, headers, None, who).ok else None
    return gate


def cmd_share(args: argparse.Namespace) -> int:
    directory = Path(args.state) if args.state else home.state("onboard")
    share_dir, pool = Path(args.dir) if args.dir else directory, Pool()
    bad = [v for v in _joins(args.sharing).values() if v not in SHARING_LEVELS]
    if bad or not (args.models or (share_dir.is_dir() and args.dir)):
        _emit(args, {"error": "needs --dir (or --models) and sharing levels of "
                              f"{'/'.join(SHARING_LEVELS)}"},
              "error: needs --dir (or --models)")
        return 2
    try:
        pool.own()
    except PermissionError as exc:
        _emit(args, {"error": str(exc)}, f"error: {exc}")
        return 2
    skipped: list[str] = []
    paths = roots = None
    try:
        ident = identity(directory / "tls", "share")
        keys = SigningKeys(directory)
        if args.models:
            found, roots = modelstore.candidates(args.models_dir or None)
            store = modelstore.build(
                found, roots, digests=modelstore.Digests(directory / "model-digests.json"),
                terms=modelstore.Terms(_joins(args.sharing), _joins(args.licence),
                                       _joins(args.source)), veto=quarantine_veto)
            entries, paths, skipped = store.entries, store.paths, store.skipped
        else:
            entries = _entries(share_dir, args)
        raw = keys.sign(entries, serial=modelstore.next_serial(directory / "share-serial.json"),
                        confirm=confirm_signing)
    except (TlsUnavailable, KeyStoreError) as exc:
        _emit(args, {"error": str(exc)}, f"error: {exc}")
        return 2
    licences = Licences(directory / "licences.json")
    _confirm_licences(entries, licences)
    gate = service_gate(pool)
    span = parse_duration(args.span) or 600.0
    with ShareServer(Share(share_dir, raw, verify(raw, keys.public), licences, quarantine_veto,
                           paths, roots or ()),
                     authenticate=gate, ident=ident,
                     address=(args.host or "0.0.0.0", args.port)) as server:  # noqa: S104
        _emit(args, {"sharing": True, "port": server.port, "certificate": ident.fingerprint,
                     "files": [{"name": e.name, "size": e.size, "sharing": e.sharing,
                                "licence_recorded": bool(licences.accepted(e))}
                               for e in entries], "skipped": skipped},
              f"sharing {len(entries)} files on port {server.port}")
        with contextlib.suppress(KeyboardInterrupt):
            time.sleep(span)
    return 0
