"""A phone made of keys, with no test framework in it: a P-256 key that signs and a push key, as the tests of the phone (`phone_kit`) and the
pool check use it, with the client of a node's phone endpoint (`pool_check.wire.Endpoint`). Nothing in the node knows it is not a phone.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pool_check.wire import Endpoint

from poolhouse.board import grants

__all__ = ["Endpoint", "PhoneKeys"]


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

    def signature(self, view: dict[str, Any]) -> dict[str, Any]:
        """The `decide` body approving a request to sign an act: the signature is over the act's own bytes, which the phone reads from the signed action."""
        message = bytes.fromhex(view["challenge"]["action"]["message"])
        return {"id": view["id"], "decision": "approve", "key": self.fingerprint, "sig": self.sign(message),
                "text_sha256": hashlib.sha256(view["text"].encode()).hexdigest()}

    def decision(self, view: dict[str, Any], decision: str, pick: str = "") -> dict[str, Any]:
        """The `decide` body answering the request ``view`` as the node showed it; ``pick`` is the number the person chose when the request has one."""
        body = grants.sign_approval(_Signing(self), view, decision)
        return {**body, "pick": pick} if pick else body


class _Signing:
    """A phone as the signer `grants.sign_approval` takes: a public key and a signature over bytes."""

    def __init__(self, phone: PhoneKeys) -> None:
        self._phone = phone

    @property
    def public(self) -> bytes:
        return bytes.fromhex(self._phone.public)

    def sign_bytes(self, data: bytes) -> bytes:
        return bytes.fromhex(self._phone.sign(data))
