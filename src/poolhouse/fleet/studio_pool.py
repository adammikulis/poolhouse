"""The pool identity Studio scopes its grants, epochs, libraries and device sources by.

The node names a pool by a 32 character id. Studio keeps its authority records under a 64 character
identity, so the node's id is bound once, here, into a domain-separated digest.
"""

from __future__ import annotations

import hashlib

from .service_pool import POOL_ID, Pool

DOMAIN = b"poolhouse/studio-pool/v1\n"


def scope_of(pool: str) -> str:
    """The Studio identity of the node pool ``pool``."""
    if not isinstance(pool, str) or not POOL_ID.fullmatch(pool):
        raise PermissionError("The node returned an invalid pool identity.")
    return hashlib.sha256(DOMAIN + pool.encode("ascii")).hexdigest()


def current() -> str:
    """The Studio identity of the pool this device's node is in now."""
    return scope_of(Pool().pool_id())


def of_service(fingerprint: str) -> str:
    """The Studio identity of the pool whose node authorizes the compute service with ``fingerprint``."""
    return scope_of(Pool().authorize(fingerprint).pool)
