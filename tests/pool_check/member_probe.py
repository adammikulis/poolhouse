"""Speak the protocol members speak to a device as another device: connect with this device's own certificate, send requests, print the replies.

    python member_probe.py --state DIR --host H --port P [--expect FINGERPRINT] [REQUEST_JSON ...]

The identity is the one in ``DIR`` (`device.key`, `device.cert`), so the other end sees exactly what it sees when that device's node
dials it. Prints one JSON line: the fingerprint of the certificate the other end showed, then each reply, or the error where the
connection or a request failed (a TLS alert when the other end refuses this device at the handshake or on its first request).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import ssl
import struct
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509 import load_der_x509_certificate


def context(state: Path) -> ssl.SSLContext:
    """A TLS 1.3 client context showing the device certificate kept in ``state``."""
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex((state / "device.key").read_text().strip()))
    cert = load_der_x509_certificate((state / "device.cert").read_bytes())
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with tempfile.TemporaryDirectory() as folder:
        identity_file = Path(folder) / "id.pem"
        identity_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ctx.load_cert_chain(str(identity_file))
    return ctx


def frame(sock: ssl.SSLSocket, value: dict) -> dict | None:
    body = json.dumps(value).encode()
    sock.sendall(struct.pack(">I", len(body)) + body)
    head = b""
    while len(head) < 4:
        chunk = sock.recv(4 - len(head))
        if not chunk:
            return None
        head += chunk
    (size,) = struct.unpack(">I", head)
    data = b""
    while len(data) < size:
        data += sock.recv(size - len(data))
    return json.loads(data)


def probe(state: Path, host: str, port: int, expect: str, requests: list[dict]) -> dict:
    out: dict = {"replies": []}
    try:
        with socket.create_connection((host, port), timeout=10) as raw, context(state).wrap_socket(raw, server_hostname=host) as sock:
            shown = hashlib.sha256(sock.getpeercert(binary_form=True) or b"").hexdigest()
            out["shown"] = shown
            if expect and shown != expect:
                out["error"] = f"the certificate is not the one pinned (expected {expect[:12]}, shown {shown[:12]})"
                return out
            for request in requests:
                out["replies"].append(frame(sock, request))
    except (OSError, ssl.SSLError, ValueError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--expect", default="")
    ap.add_argument("request", nargs="*")
    args = ap.parse_args(argv)
    sys.stdout.write(json.dumps(probe(Path(args.state), args.host, args.port, args.expect, [json.loads(r) for r in args.request])) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
