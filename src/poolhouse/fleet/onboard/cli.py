"""``poolhouse-pool bootstrap | share | fetch | signing``: the file, source and installation commands.

Every command takes ``--json`` and prints one JSON document; nothing is scraped from text.
State lives under ``--state`` (default ``<state root>/onboard``): this machine's certificate and
the pool signing key. Who is in the pool is the Rust node's: see ``poolhouse-pool members``.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import socket
import time
from pathlib import Path
from typing import Any

from poolhouse import home, macauth
from poolhouse.files import read_json, write_json
from poolhouse.log import say
from poolhouse.units import parse_duration

from ..tailnet import detect
from ..tls import TlsUnavailable, identity, pinned_context
from .bootstrap import BootstrapServer, Terms
from .devices import Devices, short
from .lan import primary_ip
from .manifest import ManifestError, RotationAnnounced, Signer, key_fingerprint, verify
from .routes import resolve
from .share_cli import add_share, cmd_share
from .signing import KeyStoreError, SigningKeys
from .signing_cli import add_signing, cmd_signing, confirm_signing
from .ssh_cli import add_ssh, cmd_ssh
from .transfer import (
    Downloader,
    MemberSource,
    NotShareable,
    Share,
    TransferError,
    fetch_manifest,
)
from .trusted import remember

__all__ = ["add_service_commands", "run"]


def state_dir(args: argparse.Namespace) -> Path:
    return Path(args.state) if getattr(args, "state", "") else home.state("onboard")


def _emit(args: argparse.Namespace, document: dict[str, Any], text: str) -> None:
    say(json.dumps(document, indent=1, default=str) if args.json else text)


def add_service_commands(sub: Any, common: Any) -> None:
    """Add file, source and installation commands without membership operations."""
    p = common(sub.add_parser("bootstrap", help="offer poolhouse to a machine that has none"))
    p.add_argument("--share", default="", help="a directory holding the wheel or archive to "
                                               "offer")
    p.add_argument("--valid", default="10m")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--ssh", default="", metavar="[USER@]HOST",
                   help="install over the system's ssh instead of serving an address: the "
                        "owner's keys, a host key typed in full, a fixed audited script")
    add_ssh(p)
    p.add_argument("--advertise", default="", help="the address to put in the URL (default: "
                                                   "this machine's LAN address when --host is "
                                                   "a wildcard)")

    add_share(sub, common)
    add_signing(sub, common)

    p = common(sub.add_parser("fetch", help="fetch files from a machine this one joined with"))
    p.add_argument("names", nargs="+")
    p.add_argument("--from", dest="source", required=True, metavar="HOST:PORT",
                   help="a machine running 'share'")
    p.add_argument("--into", default="", help="where to stage them (default: "
                                              "<state>/staging)")


def _identity(directory: Path) -> Any:
    try:
        return identity(directory / "tls", socket.gethostname()[:40])
    except TlsUnavailable as exc:
        raise SystemExit(f"cannot make a certificate: {exc}") from None


def cmd_bootstrap(args: argparse.Namespace) -> int:
    if args.ssh:
        return cmd_ssh(args)
    directory = state_dir(args)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    share = Path(args.share) if args.share else None
    if share is None or not share.is_dir():
        _emit(args, {"error": "--share DIR with the wheel or archive to offer"},
              "error: name a directory holding the wheel or archive with --share")
        return 2
    keys = SigningKeys(directory)
    entries = [Signer.entry_for(f, kind="wheel" if f.suffix == ".whl" else "sdist")
               for f in sorted(share.iterdir())
               if f.is_file() and f.suffix in (".whl", ".gz", ".zip")]
    try:
        raw = keys.sign(entries, serial=int(time.time()), confirm=confirm_signing)
    except KeyStoreError as exc:
        _emit(args, {"error": str(exc)}, f"error: {exc}")
        return 2
    manifest = verify(raw, keys.public)
    ident = _identity(directory)
    valid = parse_duration(args.valid) or 600.0
    advertise = args.advertise or (primary_ip() if args.host in ("", "0.0.0.0")  # noqa: S104
                                   else args.host)
    with BootstrapServer(Share(share, raw, manifest), ident,
                         terms=Terms(valid, socket.gethostname(), advertise=advertise),
                         address=(args.host, args.port)) as server:
        offer = server.offer
        _emit(args, {"url": offer.url, "expires_in_s": valid, "certificate": offer.fingerprint,
                     "signing_key": offer.key_id, "manifest_sha256": offer.manifest_sha256,
                     "files": [e.name for e in entries], "command": offer.command()},
              f"open this on the new machine: {offer.url}\ncertificate {short(offer.fingerprint)}"
              f", good for {int(valid)} s")
        with contextlib.suppress(KeyboardInterrupt):
            time.sleep(valid)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    directory = state_dir(args)
    trust = read_json(directory / "trust.json", {})
    host, _, port = args.source.rpartition(":")
    if not (trust.get("certificate") and trust.get("signing_key") and trust.get("device_secret") and host
            and port.isdigit()):
        _emit(args, {"error": "join a machine first, and name HOST:PORT"},
              "error: join a machine first, and name HOST:PORT")
        return 2
    try:
        host = resolve(host, int(port), Devices(directory / "devices.json"), detect)
    except OSError as exc:
        _emit(args, {"error": str(exc)}, f"error: {exc}")
        return 2
    secret = macauth.derive(base64.urlsafe_b64decode(trust["device_secret"]))
    member = MemberSource(f"https://{host}:{port}", secret,
                      pinned_context(trust["certificate"]), name=host)
    try:
        raw = fetch_manifest(member)
        manifest = verify(raw, base64.b64decode(trust["signing_key"]),
                          min_serial=int(trust.get("serial", 0)),
                          revoked_keys=trust.get("revoked", []))
        remember(raw, base64.b64decode(trust["signing_key"]))
        staged = []
        for name in args.names:
            got = Downloader(manifest, [member], Path(args.into) if args.into
                             else directory / "staging").download(name)
            staged.append(str(got))
    except RotationAnnounced as exc:
        write_json(directory / "trust.json",
                   {**trust, "pending_key": base64.b64encode(exc.new_public).decode()})
        _emit(args, {"error": str(exc), "pending_key": key_fingerprint(exc.new_public)},
              f"error: {exc}")
        return 2
    except (ManifestError, TransferError, OSError) as exc:
        reason = exc.source if isinstance(exc, NotShareable) and exc.source else str(exc)
        _emit(args, {"error": str(exc), "source": getattr(exc, "source", "")},
              f"error: {exc}" + (f" ({reason})" if reason != str(exc) else ""))
        return 2
    write_json(directory / "trust.json", {
        **trust, "serial": manifest.serial,
        "revoked": sorted({*trust.get("revoked", []), *manifest.revoked_keys})})
    _emit(args, {"staged": staged, "serial": manifest.serial},
          "staged (not installed): " + ", ".join(staged))
    return 0


def run(args: argparse.Namespace) -> int:
    fn = {"bootstrap": cmd_bootstrap, "share": cmd_share, "fetch": cmd_fetch, "signing": cmd_signing}[args.cmd]
    return fn(args)
