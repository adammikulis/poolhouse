"""The phone protocol vectors the Android app is checked against, held to docs/phone-api.md from the Python side.

The app's own checks (`app/android/core`, `ProtocolTest`) compute the same values in Kotlin from the same fixture, so the two
implementations agree on the bytes of linking and signing. Nothing here needs an Android tool.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]
VECTORS = json.loads((ROOT / "tests" / "fixtures" / "phone-api-vectors.json").read_text(encoding="utf-8"))


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


def test_the_code_key_the_proofs_and_the_key_words_follow_section_2():
    v = VECTORS
    code = re.sub(r"[^A-Z0-9]", "", v["typed_code"].upper())
    assert code == v["code"] and set(code) <= set("23456789ABCDEFGHJKMNPQRSTVWXYZ")
    key = hashlib.pbkdf2_hmac("sha256", code.encode(), f"poolhouse/phone-link/v1|{v['pin']}".encode(), 200_000, 32)
    assert key.hex() == v["code_key"]
    assert v["link_message"] == f"poolhouse/phone-link/v1\n{v['pin']}\n{v['key']}\n{v['name']}"
    assert b64(hmac.new(key, v["link_message"].encode(), hashlib.sha256).digest()) == v["link_proof"]
    linked = f"poolhouse/phone-linked/v1\n{v['pin']}\n{v['key']}\n{v['phone']}"
    assert b64(hmac.new(key, linked.encode(), hashlib.sha256).digest()) == v["computer_proof"]
    digits = hashlib.sha256(base64.b64decode(v["key"])).hexdigest()[:24]
    assert v["words"] == " ".join(digits[i:i + 4] for i in range(0, 24, 4))


def test_every_request_signs_the_string_of_section_3_and_its_signature_verifies():
    public = serialization.load_der_public_key(base64.b64decode(VECTORS["key"]))
    for request in VECTORS["requests"]:
        message = "\n".join(["poolhouse/phone-request/v1", VECTORS["pin"], VECTORS["phone"], request["method"], request["path"],
                             str(request["time_ms"]), request["nonce"], hashlib.sha256(request["body"].encode()).hexdigest()])
        assert request["message"] == message
        public.verify(base64.b64decode(request["signature"]), message.encode(), ec.ECDSA(hashes.SHA256()))
