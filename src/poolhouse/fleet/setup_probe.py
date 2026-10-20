"""One explicit, inert SDK roundtrip against a currently receipted native server."""
import json
import os
import re
from pathlib import Path

from poolhouse import gate, http_cancel
from poolhouse.client import Client, Request, Transport, families
from poolhouse.json_objects import unique_object
from poolhouse.serve import broker_wire, startup
from poolhouse.studio.store import digest

from .setup_jobs import PreparationStopped

TOOL = {'type': 'function', 'function': {
    'name': 'poolhouse_setup_sum', 'description': 'Return the fixed sum for this setup check.',
    'parameters': {'type': 'object', 'properties': {
        'left': {'type': 'integer', 'enum': [2]}, 'right': {'type': 'integer', 'enum': [3]}},
        'required': ['left', 'right'], 'additionalProperties': False}}}


class Control(http_cancel.Cancellation):
    """Only cheap actual session/context checks belong in the HTTP polling path."""

    def __init__(self, current):
        super().__init__()
        self.current = current

    def is_set(self):
        if self.current() is not True:
            raise PreparationStopped('The current preparation session ended.')
        return super().is_set()


def _call(reply):
    if reply.truncated or not isinstance(reply.tool_calls, list) or len(reply.tool_calls) != 1:
        return None
    call = reply.tool_calls[0]
    if (not isinstance(call, dict) or set(call) != {'id', 'type', 'function'}
            or call['type'] != 'function' or not isinstance(call['id'], str)
            or not 0 < len(call['id']) <= 128 or any(ord(char) < 32 for char in call['id'])):
        return None
    function = call['function']
    if (not isinstance(function, dict) or set(function) != {'name', 'arguments'}
            or function['name'] != TOOL['function']['name']
            or not isinstance(function['arguments'], str) or len(function['arguments']) > 1024):
        return None
    try:
        args = json.loads(function['arguments'], object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        return None
    if (not isinstance(args, dict) or set(args) != {'left', 'right'}
            or type(args['left']) is not int or type(args['right']) is not int
            or args != {'left': 2, 'right': 3}):
        return None
    return {'id': call['id'], 'type': 'function', 'function': {
        'name': TOOL['function']['name'], 'arguments': '{"left":2,"right":3}'}}


def _accepted(phase, reply):
    if reply.truncated:
        return False
    if phase == 'tool-call':
        return _call(reply) is not None
    if not isinstance(reply.content, str) or reply.tool_calls:
        return False
    return bool(reply.content.strip()) if phase == 'chat' else reply.content.strip() == '5'


def checked_startup(target):
    """Read-only retained proof for a server-derived target; never start or lease."""
    receipt = broker_wire.startup(target['port'])
    if (not isinstance(receipt, dict) or receipt.get('port') != target['port']
            or receipt.get('settings', {}).get('context') != target['context']
            or receipt.get('runtime', {}).get('parser') != startup.parser_identity()
            or not isinstance(receipt.get('id'), str)
            or re.fullmatch(r'[a-f0-9]{64}', receipt['id']) is None):
        return None
    parts = receipt.get('artifacts', {}).get('model')
    expected = {'path': target['model'], 'sha256': target['artifact_digest']}
    if not isinstance(parts, list) or sum(part == expected for part in parts) != 1:
        return None
    return receipt


class _Probe:
    def __init__(self, target, progress, control):
        if (not isinstance(target, dict) or set(target) != {'model', 'artifact_digest', 'port', 'context'}
                or not isinstance(target['model'], str) or not Path(target['model']).is_absolute()
                or not isinstance(target['artifact_digest'], str)
                or re.fullmatch(r'[a-f0-9]{64}', target['artifact_digest']) is None
                or type(target['port']) is not int or not 0 < target['port'] < 65536
                or type(target['context']) is not int or target['context'] <= 0):
            raise ValueError('Invalid setup probe target.')
        self.target, self.progress, self.control = target, progress, control
        self.url = f'http://127.0.0.1:{target["port"]}'
        self.client = Client(self.url, model=Path(target['model']).name,
                             request=Request(temperature=0, n_predict=128, spec_draft_max=None),
                             transport=Transport(timeout=60, tries=1))
        self.receipt = None
        self.ticket, self.device, self.parsers = '', '', {}

    def _check(self):
        self.progress.check()
        if self.control.is_set() or checked_startup(self.target) != self.receipt:
            raise PreparationStopped('The observed setup server changed.')
        held = gate.snapshot().get(self.device, [])
        if not any(row['ticket'] == self.ticket and row['pid'] == os.getpid()
                   and row['running'] is True for row in held):
            raise PreparationStopped('The admitted setup turn ended.')

    def _request(self, phase, messages, *, tools=None):
        self._check()
        arguments = {'tools': tools} if tools else {}
        fingerprint = digest({'startup': self.receipt['id'],
                              'body': self.client.build_body(messages, **arguments)})
        claim = self.progress.probe(phase, fingerprint)
        if claim['fresh'] is not True:
            raise PreparationStopped('This setup request already has a durable intent.')
        self._check()
        with http_cancel.scope(self.control):
            reply = self.client.chat(messages, **arguments)
        self._check()
        accepted = _accepted(phase, reply)
        response_digest = digest(reply.raw)
        self.parsers[phase] = families.for_model_id(reply.raw.get('model')).name
        self.progress.probe(phase, fingerprint, response_digest=response_digest, accepted=accepted)
        return reply, accepted

    def run(self):
        self.progress.check()
        self.receipt = checked_startup(self.target)
        if self.receipt is None:
            return {'chat_ready': False, 'tools_ready': False, 'reason': 'Current loaded model evidence is unavailable.'}
        self.progress.serving({'startup_id': self.receipt['id'],
            'artifact_digest': self.target['artifact_digest'], 'port': self.target['port'],
            'context': self.target['context'], 'runtime_digest': self.receipt['runtime']['binary']['sha256'],
            'parser': self.receipt['runtime']['parser']})
        self.device = gate.device_of(self.url)
        with gate.turn(self.url, cancelled=self.control.is_set) as ticket:
            if not isinstance(ticket, str) or not ticket or not self.device:
                raise PreparationStopped('An independent serial setup turn is required.')
            self.ticket = ticket
            self._check()
            self.progress.stage('probe')
            _, chat = self._request('chat', [{'role': 'user', 'content': 'Reply with the word ready.'}])
            if not chat:
                return self._result(False, False)
            messages = [{'role': 'user', 'content':
                         'Call poolhouse_setup_sum exactly once with left 2 and right 3. Do not answer in text.'}]
            reply, called = self._request('tool-call', messages, tools=[TOOL])
            if not called:
                return self._result(True, False)
            call = _call(reply)
            messages.extend([{'role': 'assistant', 'content': None, 'tool_calls': [call]},
                             {'role': 'tool', 'tool_call_id': call['id'], 'content': '5'},
                             {'role': 'user', 'content': 'Return only the value supplied by the tool.'}])
            _, consumed = self._request('tool-result', messages)
            return self._result(True, consumed)

    def _result(self, chat, tools):
        self._check()
        return {'chat_ready': chat, 'tools_ready': tools, 'evidence': {
            'startup_id': self.receipt['id'], 'artifact_digest': self.target['artifact_digest'],
            'context': self.target['context'], 'parsers': dict(self.parsers)}}


def observe(target, progress, control):
    """Trusted fixed preparation only; no endpoint or callback is accepted from wire data."""
    return _Probe(target, progress, control).run()
