"""The pool identity Studio scopes its grants, epochs, libraries and device sources by.

The node names a pool by a 32 character id. Studio keeps its authority records under a 64 character
identity, so the node's id is bound once, here, into a domain-separated digest.
"""

from __future__ import annotations

import hashlib

from .service_pool import POOL_ID, Pool

DOMAIN = b"poolhouse/studio-pool/v1\n"
UNFOUNDED = hashlib.sha256(DOMAIN + b"unfounded").hexdigest()
"""The identity of a device alone; no pool id (32 hexadecimal characters) digests to it."""


def scope_of(pool: str) -> str:
    """The Studio identity of the node pool ``pool``."""
    if not isinstance(pool, str) or not POOL_ID.fullmatch(pool):
        raise PermissionError("The node returned an invalid pool identity.")
    return hashlib.sha256(DOMAIN + pool.encode("ascii")).hexdigest()


def current(pool: Pool) -> str:
    """The Studio identity of the pool the node of ``pool`` is in now."""
    return scope_of(pool.pool_id())


def own(pool: Pool) -> str:
    """The identity this device's own work is kept under: `current`, or `UNFOUNDED` while its node is in no founded pool.

    Only what stays on the device uses it. Whatever another device takes part in asks `current`, which an unfounded node fails.
    """
    found = pool.pool_id(or_unfounded=True)
    return scope_of(found) if found else UNFOUNDED


def of_service(pool: Pool, fingerprint: str) -> str:
    """The Studio identity of the pool whose node, the one of ``pool``, authorizes the compute service with ``fingerprint``."""
    return scope_of(pool.authorize(fingerprint).pool)
