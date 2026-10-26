"""Current node-authorized HTTPS compute service bindings."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from poolhouse import macauth, node_health, node_launch

from . import tls

POOL_ID = re.compile(r"[0-9a-f]{32}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
GENERATION = re.compile(r"[0-9a-f]{32}\Z")
DOMAIN = b"poolhouse/service-request/v1"
FIELDS = {"device", "name", "endpoint", "cert", "fingerprint", "generation", "service"}


@dataclass(frozen=True, slots=True)
class Binding:
    """One compute service authorized by its pool's node."""

    pool: str
    device: str
    name: str
    endpoint: str
    cert: str
    fingerprint: str
    generation: str
    service: str

    @property
    def certificate(self) -> str:
        return base64.b64encode(bytes.fromhex(self.cert)).decode("ascii")


def binding(pool: str, row: dict) -> Binding:
    """Validate a current node service reply."""
    if (not isinstance(pool, str) or not POOL_ID.fullmatch(pool) or not isinstance(row, dict)
            or set(row) != FIELDS or any(not isinstance(value, str) for value in row.values())
            or not HEX64.fullmatch(row["device"]) or not HEX64.fullmatch(row["fingerprint"])
            or not GENERATION.fullmatch(row["generation"]) or row["service"] != "compute"):
        raise PermissionError("The node returned an invalid compute service binding.")
    try:
        der = bytes.fromhex(row["cert"])
        address = urlsplit(row["endpoint"])
        if (not der or der.hex() != row["cert"] or hashlib.sha256(der).hexdigest() != row["fingerprint"]
                or address.scheme != "https" or not address.hostname or not address.port
                or address.username or address.password or address.path not in ("", "/")
                or address.query or address.fragment):
            raise ValueError("binding")
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec
        key = x509.load_der_x509_certificate(der).public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("service key")
    except (ValueError, TypeError) as exc:
        raise PermissionError("The node returned an invalid compute service certificate or endpoint.") from exc
    return Binding(pool=pool, **row)


class Pool:
    """Read compute authorization from the live Rust node."""

    def __init__(self, state: Path | None = None) -> None:
        self.state = state or node_launch.default_state()

    def _call(self, method: str, params: dict | None = None) -> dict:
        try:
            return node_health.call(self.state, method, params)
        except (OSError, ValueError) as exc:
            raise PermissionError("Current pool compute authorization is unavailable; the node must answer.") from exc

    def pool_id(self) -> str:
        """The canonical pool id reported by this local node."""
        result = self._call("pool_status")
        pool = result.get("pool") if isinstance(result, dict) else None
        if not isinstance(pool, str) or not POOL_ID.fullmatch(pool):
            raise PermissionError("The node returned an invalid pool identity.")
        return pool

    def services(self) -> list[Binding]:
        reply = self._call("service_members")
        if not isinstance(reply, dict) or set(reply) != {"pool", "services"} or not isinstance(reply["services"], list):
            raise PermissionError("The node returned an invalid compute service list.")
        rows = [binding(reply["pool"], row) for row in reply["services"]]
        if len({row.device for row in rows}) != len(rows) or len({row.fingerprint for row in rows}) != len(rows):
            raise PermissionError("The node returned conflicting compute service identities.")
        return rows

    def authorize(self, fingerprint: str) -> Binding:
        if not isinstance(fingerprint, str) or not HEX64.fullmatch(fingerprint):
            raise PermissionError("A compute service is named by its certificate fingerprint.")
        reply = self._call("service_authorize", {"fingerprint": fingerprint})
        if not isinstance(reply, dict) or set(reply) != FIELDS | {"pool"}:
            raise PermissionError("The node returned invalid compute service authorization.")
        result = binding(reply["pool"], {name: reply[name] for name in FIELDS})
        if result.fingerprint != fingerprint:
            raise PermissionError("The node authorized another compute service certificate.")
        return result

    def own(self, identity: tls.Identity | None = None) -> Binding:
        """The current compute service bound to this local node and TLS identity."""
        identity = identity or tls.local()
        current = self.authorize(identity.fingerprint)
        status = self._call("pool_status")
        if (not isinstance(status, dict) or status.get("pool") != current.pool
                or status.get("fingerprint") != current.device or current.cert != identity.der.hex()):
            raise PermissionError("The compute service is not bound to this local node.")
        return current

    def groups_of(self, fingerprint: str) -> frozenset[str]:
        return frozenset({self.authorize(fingerprint).pool})

    def is_active(self, fingerprint: str) -> bool:
        self.authorize(fingerprint)
        return True

    def stamp(self) -> tuple[Binding, ...]:
        return tuple(self.services())

    def certs_pem(self) -> str:
        return "".join(ssl.DER_cert_to_PEM_cert(bytes.fromhex(row.cert)) for row in self.services())

    def secret(self, fingerprint: str, identity: tls.Identity | None = None) -> str:
        """Derive the request secret for two currently authorized service identities."""
        own = identity or tls.local()
        local, remote = self.own(own), self.authorize(fingerprint)
        if (local.pool != remote.pool or local.cert != own.der.hex()
                or local.fingerprint == remote.fingerprint or local.device == remote.device
                or local.endpoint.rstrip("/") == remote.endpoint.rstrip("/")):
            raise PermissionError("Compute requests need two distinct services in the same current pool.")
        return request_secret(own, local, remote)


def request_secret(identity: tls.Identity, local: Binding, remote: Binding) -> str:
    """Derive a domain-bound P-256 service request secret."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    try:
        private = serialization.load_pem_private_key(identity.keyfile.read_bytes(), password=None)
        own_public = x509.load_der_x509_certificate(identity.der).public_key()
        public = x509.load_der_x509_certificate(bytes.fromhex(remote.cert)).public_key()
        if (not isinstance(private, ec.EllipticCurvePrivateKey) or not isinstance(private.curve, ec.SECP256R1)
                or not isinstance(own_public, ec.EllipticCurvePublicKey)
                or private.public_key().public_numbers() != own_public.public_numbers()
                or not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(public.curve, ec.SECP256R1)):
            raise ValueError("service key")
        parties = sorted(({"device": row.device, "fingerprint": row.fingerprint,
                           "generation": row.generation, "service": row.service} for row in (local, remote)),
                         key=lambda row: (row["device"], row["fingerprint"]))
        context = json.dumps({"domain": DOMAIN.decode(), "pool": local.pool, "services": parties},
                             sort_keys=True, separators=(",", ":")).encode()
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(context).digest(), info=DOMAIN).derive(
            private.exchange(ec.ECDH(), public))
        return macauth.derive(key)
    except (OSError, ValueError, TypeError) as exc:
        raise PermissionError("The existing compute service key does not match its authorized certificate.") from exc
