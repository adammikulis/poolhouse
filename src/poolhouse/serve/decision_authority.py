"""Current resident authority from the owning daemon's fixed sealed self endpoint."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import ssl
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from poolhouse import home
from poolhouse.fleet import tls
from poolhouse.fleet.discovery import Beacon, derive_token, memberships
from poolhouse.fleet.membership import Roster
from poolhouse.fleet.remote import Member
from poolhouse.fleet.studio_transport import MemberExecution
from poolhouse.serve.decision_residents import DecisionModel, fingerprint
from poolhouse.serve.process import started_at

if TYPE_CHECKING:
    from poolhouse.serve.decision_launch import Bootstrap


def _plain(path: Path) -> None:
    tls._plain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError("The resident issuer identity must use its existing private files.")
    if os.name != "nt" and info.st_uid != os.getuid():
        raise PermissionError("The resident issuer identity belongs to another account.")


def existing(owned: Bootstrap) -> tuple[tls.Identity, bytes]:
    """Read only already-enrolled fixed files. Missing identity never creates one."""
    folder = tls.device_directory()
    cert, key, meta = folder / "cert.pem", folder / "key.pem", folder / "cert.json"
    machine = home.state("machine-id")
    for path in (cert, key, meta, machine):
        _plain(path)
    if os.name != "nt" and key.stat().st_mode & 0o077:
        raise PermissionError("The resident issuer key is not account-private.")
    held = json.loads(meta.read_text())
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    expiry = held.get("not_after") if isinstance(held, dict) else None
    if (type(expiry) not in {int, float} or not math.isfinite(expiry) or expiry <= time.time()
            or base64.b64encode(der).decode() != owned.issuer["certificate"]
            or machine.read_text().strip() != owned.issuer["machine"]
            or held.get("fingerprint") != hashlib.sha256(der).hexdigest()):
        raise PermissionError("The resident issuer's enrolled device identity changed.")
    # Explicit fixed default path: environment-selected paths cannot choose authority.
    rows = [row for row in memberships(home.state("pool.key"))
            if hashlib.sha256(row.key).hexdigest() == owned.issuer["pool"]]
    if len(rows) != 1 or not Roster(rows[0].key).is_active(hashlib.sha256(der).hexdigest()):
        raise PermissionError("The resident issuer is no longer enrolled in this pool.")
    return tls.Identity(cert, key, der, expiry), rows[0].key


def model_reply(value: dict) -> DecisionModel:
    """A typed descriptor from the fixed issuer, never a caller-selected import."""
    if (not isinstance(value, dict) or set(value) != {"source", "identity", "memory_bytes", "calibration"}
            or not isinstance(value["identity"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["identity"])
            or type(value["memory_bytes"]) is not int or value["memory_bytes"] <= 0
            or (value["calibration"] is not None and not isinstance(value["calibration"], dict))):
        raise ValueError("The decision issuer returned an invalid model descriptor.")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {
            "name", "base_dir", "tokenizer_dir", "head", "lora_dir", "pointer_dim", "temperature", "dtype", "details"}:
        raise ValueError("The decision issuer returned an invalid fixed model source.")
    for key in ("base_dir", "tokenizer_dir", "head", "lora_dir"):
        path = source[key]
        if key == "lora_dir" and path is None:
            continue
        if not isinstance(path, str) or "\x00" in path or not Path(path).is_absolute():
            raise ValueError("The decision issuer returned an invalid model file identity.")
    if (not isinstance(source["name"], str) or not 0 < len(source["name"]) <= 256
            or type(source["pointer_dim"]) is not int or not 0 < source["pointer_dim"] <= 8192
            or type(source["temperature"]) not in {int, float} or not math.isfinite(source["temperature"])
            or source["temperature"] <= 0 or source["dtype"] not in {"bfloat16", "float16", "float32"}):
        raise ValueError("The decision issuer returned unsupported model settings.")
    json.dumps(value, allow_nan=False)
    return DecisionModel(**value)


class DecisionAuthorization:
    """Recheck the exact signed immutable permit at every queue/load/compute/result seam."""

    def __init__(self, owned: Bootstrap, request: dict, worker: dict):
        if not isinstance(request, dict) or set(request) != {"permit_id", "request_id", "model_digest", "record"}:
            raise ValueError("A decision request contains only its permit and bounded record.")
        self.owned = owned
        self.body = {"permit_id": request["permit_id"], "request_id": request["request_id"],
                     "record_digest": fingerprint(request["record"]), "model_digest": request["model_digest"],
                     "runtime_digest": owned.issuer["runtime"], "worker": dict(worker),
                     "broker": {"pid": os.getpid(), "started": started_at(os.getpid()), "instance": owned.instance},
                     "issuer_instance": owned.issuer["issuer_instance"]}
        self.owned.sign({**self.body, "stage": "queue"})

    def __call__(self, stage: str) -> DecisionModel:
        payload = self.owned.sign({**self.body, "stage": stage})
        identity, key = existing(self.owned)
        parts = urlsplit(self.owned.issuer["endpoint"])
        member = Member(self.owned.issuer["endpoint"], derive_token(key), timeout=5,
                        beacon=Beacon("resident-issuer", host=parts.hostname, port=parts.port,
                                      machine=self.owned.issuer["machine"], cert=self.owned.issuer["certificate"]))
        def current():
            self.owned.sign({**self.body, "stage": stage})
            existing(self.owned)
            return True
        reply = MemberExecution(member, current, identity=identity).request("decision-authorize", payload)
        expected = {"permit_id": self.body["permit_id"], "request_id": self.body["request_id"],
                    "record_digest": self.body["record_digest"], "model_digest": self.body["model_digest"],
                    "runtime": self.body["runtime_digest"], "stage": stage}
        if (not isinstance(reply, dict) or set(reply) != set(expected) | {"authorized", "model"}
                or reply["authorized"] is not True or any(reply.get(key) != value for key, value in expected.items())):
            raise PermissionError("Current resident decision authorization is unavailable.")
        current()
        return model_reply(reply["model"])
