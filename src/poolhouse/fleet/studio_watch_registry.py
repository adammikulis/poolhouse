"""Connect native selection proofs to the current authoritative resource registry."""
from __future__ import annotations

from types import SimpleNamespace

from poolhouse.studio import stream_sources
from poolhouse.studio.store import digest

from .native_picks import NativePicks


def create_native_picks(store, *, selection_epoch, interactive_current, create_source):
    """Build a callback-only bridge, with no implicit registry or authority fallback.

    ``selection_epoch(principal, create=bool)`` belongs to the authority service. Creating
    selection state may only create a disabled tombstone; it must never enable execution.
    ``interactive_current`` verifies the original actual interactive session, pool and device.
    ``create_source(principal, binding, request_id, expected_epoch)`` must atomically compare
    the original selection generation while persisting the request-idempotent resource.
    """

    def epoch(principal):
        if not interactive_current(principal):
            raise PermissionError('Sign in before selecting an original source')
        value = selection_epoch(principal, create=True)
        if not interactive_current(principal):
            raise PermissionError('The source selection session ended')
        return value

    def current(principal, expected):
        if not interactive_current(principal):
            return False
        try:
            return selection_epoch(principal, create=False) == expected
        except (KeyError, PermissionError):
            return False

    def create_watch(principal, path, mode, request_id):
        # Recover the immutable original proof instead of adopting whatever epoch happens
        # to be current after a revocation concurrent with the native completion.
        with store.lock:
            row = store.get('native-pick', request_id, principal['scope'])
            if (row['state'] != 'completing' or row['principal'] != principal or row['mode'] != mode
                    or row['selection'] != digest({'mode': mode, 'path': path})):
                raise PermissionError('The native selection proof does not match this resource')
            expected = row['epoch']
            def authorize():
                return current(principal, expected)
            if not authorize():
                raise PermissionError('The source selection was revoked')
            binding = stream_sources.bind(path, mode, authorize=authorize)
            binding['id'] = digest({'scope': principal['scope'], 'request': request_id, 'epoch': expected})[:32]
            if not authorize():
                raise PermissionError('The source selection was revoked')
            source = create_source(principal, binding, request_id, expected)
            return {'id': source['id'], 'title': source['title']}

    records = SimpleNamespace(purpose='studio-source', lock=store.lock, read=lambda ident: store.graph.get_doc('studio.native-pick:' + ident),
                              write=lambda row: store.put('native-pick', row))
    return NativePicks(records, epoch=epoch, current=current, create_resource=create_watch)
