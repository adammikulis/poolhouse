"""Bounded authenticated dispatch of the commands a remote coordinator answers."""

import re

from poolhouse.workspace.identity import HUMAN, Denied

READS = frozenset({'audit-verify', 'audit-head'})
REQUEST_ID = re.compile(r'[a-f0-9]{32}')
MAX_OUTCOME = 256 * 1024


def prepare(ws, token, argv, parser, handlers):
    who = ws.auth(token)
    if who.role == HUMAN:
        raise Denied('the coordinator RPC accepts agent credentials, never person credentials')
    if not argv or argv[0] not in READS:
        raise Denied('this command is not a remote coordination operation')
    ws._may(who, 'read')
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        raise ValueError('invalid coordination command arguments') from error
    if args.agent or args.token_file:
        raise Denied('the remote actor is authenticated by its token')
    args.json = True
    if any(getattr(args, field, '') == '-' for field in ('body', 'text', 'payload')):
        raise ValueError('remote command text is supplied in the bounded request')
    return who, args, handlers[argv[0]]


def execute(ws, token, document, parser, handlers):
    if set(document) != {'workspace', 'request_id', 'argv'}:
        raise ValueError('a coordination call contains workspace, request_id and argv')
    argv, request_id = document['argv'], document['request_id']
    if type(argv) is not list or len(argv) > 80 or any(type(arg) is not str for arg in argv):
        raise ValueError('argv is a bounded string list')
    if type(request_id) is not str or not REQUEST_ID.fullmatch(request_id):
        raise ValueError('a stable request ID is required')
    _who, args, handler = prepare(ws, token, argv, parser, handlers)
    return handler(args, ws, token)
