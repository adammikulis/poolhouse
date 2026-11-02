"""Fixed sealed software-producer actions, with caller identity from the TLS connection."""
from __future__ import annotations

import base64
import hashlib
import json
import ssl

from poolhouse.json_objects import unique_object

from .producer_control import BRIDGE_BYTES, METADATA_BYTES

_COMMAND = {'source', 'command_id'}
_DATA = {'version', 'action', 'binding', 'event_id', 'raw', 'wire_digest', 'nonce', 'retry_key'}
FIELDS = {'producer-offers': set(), 'producer-inspect': {'offer_id', 'revision'},
          'producer-connect': _COMMAND | {'offer_id', 'revision'},
          'producer-activate': _COMMAND, 'producer-status': _COMMAND, 'producer-revoke': _COMMAND,
          'producer-authorize': _COMMAND | {'stage', 'challenge', 'binding_digest'},
          'producer-publish': _DATA, 'producer-receipt': _DATA, 'producer-retry': _DATA}
ACTIONS = frozenset(FIELDS)


def answer(handler, ui, pool, action, raw):
    """Called only after the node-authorized sealed member guard; ``pool`` is the caller's current pool."""
    if action not in ACTIONS:
        return False
    channel = handler.connection
    if (handler.command != 'POST' or not isinstance(channel, ssl.SSLSocket)
            or channel.version() != 'TLSv1.3' or not pool):
        raise PermissionError('Software producers require a current encrypted pool device.')
    certificate = channel.getpeercert(binary_form=True)
    if not certificate:
        raise PermissionError('The current producer device certificate is missing.')
    caller = {'certificate': base64.b64encode(certificate).decode('ascii'),
            'pool': pool}
    if hashlib.sha256(certificate).hexdigest() != handler._member:
        raise PermissionError('The sealed caller and TLS device do not match.')
    maximum = BRIDGE_BYTES if action == 'producer-publish' else METADATA_BYTES
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum:
        raise ValueError('The software producer request exceeds its fixed action limit.')
    request = json.loads(raw, object_pairs_hook=unique_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite producer request.')))
    if not isinstance(request, dict) or set(request) != FIELDS[action]:
        raise ValueError('Choose the exact fixed software producer action.')
    json.dumps(request, allow_nan=False)
    provider = getattr(ui, 'studio_producers', None)
    if provider is None:
        raise PermissionError('This app has no current software producer service.')
    result = provider.member(caller, action, request)
    if not isinstance(result, dict):
        raise ValueError('The software producer service returned an invalid receipt.')
    handler._send(200, result)
    return True
