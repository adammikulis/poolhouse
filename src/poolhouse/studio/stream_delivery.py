"""One HTTPS delivery attempt to a currently authorized server-registered destination.

The caller owns durable intent and reconciliation. An uncertain send is never retried here.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket

from poolhouse import httpguard
from poolhouse.graph.guard import MAX_BODY
from poolhouse.json_objects import unique_object

_TOKEN = re.compile(r'[A-Za-z0-9_.:-]{1,200}\Z')
_DIGEST = re.compile(r'[a-f0-9]{64}\Z')


def _current(authorize):
    if authorize() is not True:
        raise PermissionError('Delivery authorization is no longer current')


def _prepare(destination, envelope, authorize):
    _current(authorize)
    if (not isinstance(destination, dict) or set(destination) != {'id', 'revision', 'url'}
            or not isinstance(destination['id'], str) or not _TOKEN.fullmatch(destination['id'])
            or type(destination['revision']) not in (str, int) or not str(destination['revision'])):
        raise ValueError('Select a registered delivery destination')
    url = destination['url']
    parts, host, port = httpguard.split(url)
    if (url != url.strip() or parts.scheme != 'https' or parts.fragment or '@' in parts.netloc
            or '%' in host or not host.isascii()):
        raise ValueError('Delivery requires an exact HTTPS destination')
    if (not isinstance(envelope, dict) or set(envelope) != {'delivery_id', 'request_digest', 'body'}
            or not isinstance(envelope['delivery_id'], str) or not _TOKEN.fullmatch(envelope['delivery_id'])
            or not isinstance(envelope['request_digest'], str) or not _DIGEST.fullmatch(envelope['request_digest'])
            or not isinstance(envelope['body'], dict)):
        raise ValueError('Delivery requires a fixed intent and JSON object')
    body = json.dumps(envelope['body'], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(body) > MAX_BODY or hashlib.sha256(body).hexdigest() != envelope['request_digest']:
        raise ValueError('The delivery body does not match its recorded intent')
    _current(authorize)
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        addresses = [row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    if not addresses or any('%' in address or not httpguard.allowed_address(address) for address in addresses):
        raise ValueError('Delivery destinations must resolve entirely to public addresses')
    _current(authorize)
    return url, host, port, addresses[0], body


def deliver(destination: dict, envelope: dict, *, authorize) -> dict:
    """Return done only for an exact TLS-authenticated receipt; never infer non-delivery.

    ``authorize`` must validate the registered destination revision and current caller scope,
    and return exactly True. No workflow or receiver field selects headers or transport.
    """
    try:
        url, host, port, address, body = _prepare(destination, envelope, authorize)
    except (ValueError, TypeError, KeyError, OSError, PermissionError):
        return {'state': 'blocked', 'reason': 'preflight_refused'}

    def exact(value):
        _current(authorize)
        if value != url:
            raise httpguard.Refused('Delivery cannot change its destination')

    def pinned(name, number):
        _current(authorize)
        if name != host or number != port:
            raise httpguard.Refused('Delivery cannot change its address')
        return [address]  # One connection attempt: no retry after a possibly committed POST.

    limits = httpguard.Limits(max_bytes=MAX_BODY, follow_redirects=False, resolver=pinned, vet=exact)
    headers = {'Content-Type': 'application/json', 'Idempotency-Key': envelope['delivery_id'],
               'X-Poolhouse-Request-Digest': envelope['request_digest']}
    try:
        _current(authorize)
    except PermissionError:
        return {'state': 'blocked', 'reason': 'authorization_revoked'}
    try:
        response = httpguard.fetch(url, method='POST', data=body, headers=headers, limits=limits)
        _current(authorize)
        if not 200 <= response.status < 300:
            return {'state': 'interrupted', 'status': response.status, 'reason': 'receipt_unconfirmed'}
        receipt = json.loads(response.body, object_pairs_hook=unique_object)
        expected = {key: envelope[key] for key in ('delivery_id', 'request_digest')}
        if receipt != expected or not isinstance(receipt, dict):
            return {'state': 'interrupted', 'status': response.status, 'reason': 'receipt_unconfirmed'}
        return {'state': 'done', 'status': response.status, 'receipt': expected}
    except (ValueError, TypeError, OSError, PermissionError):
        return {'state': 'interrupted', 'reason': 'delivery_uncertain'}
