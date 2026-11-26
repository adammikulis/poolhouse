"""Seal broker admission before proving an idle retirement is safe."""
from __future__ import annotations

from functools import wraps

from poolhouse import maintenance
from poolhouse.serve.admission import tag_of


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


def _server_row(broker, row) -> bool:
    """Whether ``row`` is the node's lease of a standing server this broker carries: held, bound to that server's port and
    holding only its memory and its slot."""
    if not isinstance(row, dict) or row.get('state') != 'held':
        return False
    held = next((h for h in broker.servers.values() if row.get('tag') == tag_of(h.port)), None)
    resources = row.get('resources')
    return held is not None and not held.loading and isinstance(resources, list) and bool(resources) and all(
        isinstance(one, dict) and one.get('type') in ('memory_mb', 'model_slot') for one in resources)


def retire(broker, *, carrying: bool = False) -> bool:
    """Drain idle owned caches, then seal; busy or uncertain work stays owned.

    Run under updater intent before its exclusive turn. The accounting reader
    lets nested normal GPU admission finish while new work is excluded.

    With ``carrying`` the standing servers are not work: the holders of each are on disk (`Broker._write_held`) and the
    broker that replaces this one takes the servers and their holders in again, so a broker running older code than the
    selected runtime can be replaced while a server is up. A server still loading, a waiting ask, a claim, a core grant
    and any call in flight stay owned.
    """
    with maintenance.read(existing=True), broker._cond:
        if broker._retiring:
            return True
        if _busy(broker, carrying):
            return False
        if broker._decision_hosts is not None and not broker._decision_hosts.drain_idle():
            return False
        broker._retiring = True
        sealed = False
        try:
            if _busy(broker, carrying):
                return False
            listing = broker.table.listing()
            # Compute, serving and uninspectable rows matter, even on another board.
            if not isinstance(listing, dict) or not isinstance(listing.get("leases"), list):
                raise RuntimeError("The broker cannot verify the device's active leases.")
            if any(not _coordination_only(row) and not (carrying and _server_row(broker, row))
                   for row in listing['leases']):
                return False
            if carrying:
                broker._write_held()
            sealed = True
            return True
        finally:
            if not sealed:
                broker._retiring = False
                broker._cond.notify_all()


def _busy(broker, carrying: bool = False) -> bool:
    servers = any(held.loading for held in broker.servers.values()) if carrying else bool(broker.servers)
    return bool(broker._active_calls or servers or broker.queue or broker.claims
                or broker.claim_waiters or broker.cores)
