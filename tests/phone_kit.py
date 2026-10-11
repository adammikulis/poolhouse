"""A phone for tests, made of keys and requests: a P-256 key that signs, a push key, and a client of the node's phone endpoint over
real TLS 1.3 pinned to the certificate the node made. Nothing in the node knows a test is running.

Import `phone_node` into a test module (`pytest_plugins = ["phone_kit"]`) to get a node that listens for phones on loopback.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from poolhouse import node_launch
from poolhouse.board import grants, phone


class Endpoint:
    """The node's phone endpoint, reached over TLS 1.3 with the one certificate it made trusted and nothing else."""

    def __init__(self, state: Path, port: int) -> None:
        self.port = port
        self.pem = ssl.DER_cert_to_PEM_cert((state / "phone.cert").read_bytes())

    def request(self, op: str, body: dict[str, Any] | None = None, *, token: str = "", method: str = "POST") -> tuple[int, dict[str, Any]]:
        """One request: the status and the JSON of the reply."""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False
        context.load_verify_locations(cadata=self.pem)
        payload = json.dumps(body or {}).encode()
        auth = f"Authorization: Bearer {token}\r\n" if token else ""
        head = f"{method} /phone/v1/{op} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n{auth}Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
        with socket.create_connection(("127.0.0.1", self.port), timeout=20) as raw, context.wrap_socket(raw, server_hostname="poolhouse-phone") as tls:
            tls.sendall(head.encode() + payload)
            got = b""
            while chunk := tls.recv(65536):
                got += chunk
        status = int(got.split(b" ", 2)[1])
        return status, json.loads(got.partition(b"\r\n\r\n")[2] or b"{}")

    def ok(self, op: str, body: dict[str, Any] | None = None, *, token: str = "") -> dict[str, Any]:
        """The result of a request that must succeed."""
        status, reply = self.request(op, body, token=token)
        assert reply.get("ok") is True, (status, reply)
        return dict(reply["result"])


class PhoneKeys:
    """A phone: an approval key, a push key and what the node gave it when it was added."""

    def __init__(self, name: str = "Pixel") -> None:
        self.name = name
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.push = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.token = ""
        self.id = ""

    @property
    def public(self) -> str:
        return self.key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint).hex()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(bytes.fromhex(self.public)).hexdigest()

    def sign(self, data: bytes) -> str:
        """The DER signature (hex) of this phone's key over ``data``."""
        return self.key.sign(data, ec.ECDSA(hashes.SHA256())).hex()

    def add_body(self, device: str, secret: str) -> dict[str, Any]:
        """What the phone sends to be added to the node whose certificate fingerprint is ``device``."""
        push = self.push.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint).hex()
        body = {"secret": secret, "name": self.name, "key": self.public, "p256dh": push, "auth": self.auth.hex(), "at_ms": int(time.time() * 1000)}
        proof = {"op": "phone_add", "node": device, "name": self.name, "key": self.public, "push": push, "auth": self.auth.hex(), "at_ms": body["at_ms"]}
        return {**body, "proof": self.sign(b"poolhouse/phone-add/v1\n" + grants.canonical(proof).encode())}

    def decision(self, view: dict[str, Any], decision: str) -> dict[str, Any]:
        """The `decide` body answering the request ``view`` as the node showed it."""
        return grants.sign_approval(_Signing(self), view, decision)


class _Signing:
    """A phone as the signer `grants.sign_approval` takes: a public key and a signature over bytes."""

    def __init__(self, phone_: PhoneKeys) -> None:
        self._phone = phone_

    @property
    def public(self) -> bytes:
        return bytes.fromhex(self._phone.public)

    def sign_bytes(self, data: bytes) -> bytes:
        return bytes.fromhex(self._phone.sign(data))


def endpoint_of(team: Any) -> Endpoint:
    """The phone endpoint of the node ``team`` runs."""
    return Endpoint(team.state, int(team.client.call("phone_info")["port"]))


def add_phone(team: Any, name: str = "Pixel", *, by: PhoneKeys | None = None) -> PhoneKeys:
    """Add a phone as the person's command does: leave the code, let the phone use it (a later phone waits for ``by`` to approve it)."""
    endpoint = endpoint_of(team)
    secret = phone.expect_phone(team.state)
    device = str(team.client.call("phone_info", secret=secret)["device"])
    added = PhoneKeys(name)
    reply = endpoint.ok("add", added.add_body(device, secret))
    added.token, added.id = reply["token"], reply["phone"]
    if by is not None:
        assert reply["state"] == "pending"
        seen = endpoint.ok("get", {"id": reply["approval"]}, token=by.token)
        assert endpoint.ok("decide", by.decision(seen, "approve"))["state"] == "approved"
    else:
        assert reply["state"] == "active"
    return added


@pytest.fixture
def phone_node(workspace_node):
    """The test node, restarted listening for phones on loopback: the way a person's `phone add` turns it on."""
    node_launch.stop_node(workspace_node.state)
    phone.listen_on(workspace_node.state, "127.0.0.1:0")
    node_launch.ensure_node(workspace_node.state)
    return workspace_node
