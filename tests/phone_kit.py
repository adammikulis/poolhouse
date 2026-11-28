"""A phone made of a real P-256 key, signing requests the way the app does (docs/phone-api.md)."""

from __future__ import annotations

import base64
import dataclasses
import json
import secrets
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from poolhouse import phone_mailbox

PIN = "ab" * 32


class Key:
    """The phone's key; ``phone`` is the id it was linked under."""

    def __init__(self) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public = base64.b64encode(self.private.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        self.phone = ""

    def sign(self, message: bytes) -> str:
        return base64.b64encode(self.private.sign(message, ec.ECDSA(hashes.SHA256()))).decode()

    def signed(self, method: str, path: str, body: object = None, **how: object) -> phone_mailbox.Signed:
        """``how`` sets ``pin``, ``at`` (seconds) or ``nonce`` other than a fresh request's own."""
        text = "" if body is None else json.dumps(body)
        stamp = int(float(how.get("at", time.time())) * 1000)
        unsigned = phone_mailbox.Signed(str(how.get("pin", PIN)), self.phone, method, path, stamp,
                                        str(how.get("nonce") or secrets.token_urlsafe(18)), text, "")
        return dataclasses.replace(unsigned, signature=self.sign(unsigned.bytes()))

    def message(self, session: str, text: str, **more: object) -> phone_mailbox.Signed:
        return self.signed("POST", f"/phone/v1/sessions/{session}/messages", {"text": text}, **more)

    def answer(self, session: str, question: str, answers: dict[str, str]) -> phone_mailbox.Signed:
        return self.signed("POST", f"/phone/v1/sessions/{session}/questions/{question}/answer", {"answers": answers})


def linked(store: phone_mailbox.Store, name: str = "Pixel") -> Key:
    key = Key()
    key.phone = store.link(name, key.public).id
    return key
