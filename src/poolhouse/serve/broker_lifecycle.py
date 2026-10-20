"""Seal broker admission before proving an idle retirement is safe."""
from __future__ import annotations

from functools import wraps

from poolhouse import maintenance


def _coordination_only(row) -> bool:
    """Only repository coordination claims are unrelated to broker device work.

    Native ``lease_list`` can redact queued resources on another board. Empty,
    unknown or mixed resource sets therefore remain work, regardless of owner PID.
    """
    if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']
            or row.get('state') not in ('held', 'queued')):
        return False
    resources = row.get('resources')
    return isinstance(resources, list) and bool(resources) and all(
        isinstance(resource, dict) and set(resource) == {'type', 'kind', 'name'}
        and resource['type'] == 'claim' and resource['kind'] in ('branch', 'worktree', 'area', 'install')
        and isinstance(resource['name'], str) and bool(resource['name'].strip())
        for resource in resources)


def admitted(method):
    """Count whole calls, including native waits and background or nested callers."""
    @wraps(method)
    def entered(broker, *args, **kwargs):
        with maintenance.read(existing=method.__name__ in {'adopt', 'keep_leases', 'detach', 'reap'}):
            return guarded(broker, *args, **kwargs)
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
    return entered


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
            # Compute, serving and uninspectable rows matter, even on another board.
            if not isinstance(listing, dict) or not isinstance(listing.get("leases"), list):
                raise RuntimeError("The broker cannot verify the device's active leases.")
            if any(not _coordination_only(row) for row in listing['leases']):
                return False
            sealed = True
            return True
        finally:
            if not sealed:
                broker._retiring = False
                broker._cond.notify_all()
