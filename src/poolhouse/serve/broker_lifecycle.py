"""Seal broker admission before proving an idle retirement is safe."""
from __future__ import annotations

from functools import wraps


def admitted(method):
    """Count whole calls, including native waits and background or nested callers."""
    @wraps(method)
    def guarded(broker, *args, **kwargs):
        with broker._cond:
            if broker._retiring:
                raise RuntimeError("The idle broker is retiring; reconnect to its replacement.")
            broker._active_calls += 1
        try:
            return method(broker, *args, **kwargs)
        finally:
            with broker._cond:
                broker._active_calls -= 1
                broker._cond.notify_all()
    return guarded


def retire(broker) -> bool:
    """Permanently seal an empty broker; a busy or uninspectable one stays open."""
    with broker._cond:
        if broker._retiring:
            return True
        broker._retiring = True
        sealed = False
        try:
            if (broker._active_calls or broker.servers or broker.queue or broker.claims
                    or broker.claim_waiters or broker.cores
                    or (broker._decision_hosts is not None and broker._decision_hosts.residents)):
                return False
            listing = broker.table.listing()
            # All native rows matter, including queued work and non-Python owners.
            if not isinstance(listing, dict) or not isinstance(listing.get("leases"), list):
                raise RuntimeError("The broker cannot verify the device's active leases.")
            if listing["leases"]:
                return False
            sealed = True
            return True
        finally:
            if not sealed:
                broker._retiring = False
                broker._cond.notify_all()
