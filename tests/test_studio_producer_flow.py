"""Same-Mac software flow: real SDK/bridge processes, sealed TLS and HMAC inbox.

The fixture passes a publicly authenticated anchor privately to the SDK. Installed
holder bootstrap and physical cross-device behavior remain separate hardware proofs.
"""
import contextlib
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_studio_producer_factory import runtime_app as _runtime_app

from poolhouse.fleet import producer_bootstrap, producer_control, producer_process
from poolhouse.fleet.job_records import process_started
from poolhouse.platform import start_process

SOURCE = str(Path(__file__).resolve().parents[1] / 'src')
runtime_app = _runtime_app
DRIVER = '''
import os,sys,socket,time
sys.path.insert(0,sys.argv[1])
from poolhouse.fleet.producer_client import ProducerClient
from poolhouse.fleet.producer_control import receive_frame,send_frame
from poolhouse.fleet.job_records import process_started
with socket.socket(fileno=int(sys.argv[2])) as private:
    private.set_inheritable(False)
    anchor=receive_frame(private,deadline=time.monotonic()+10)
    client=ProducerClient.__new__(ProducerClient)
    client._initialize(anchor)
    send_frame(private,{'pid':os.getpid(),'started':process_started(os.getpid())},deadline=time.monotonic()+10)
    while True:
        try: request=receive_frame(private,deadline=time.monotonic()+30)
        except OSError: break
        if request.get('method') not in {'receivers','offer','status','publish','receipt','retry','withdraw'}:
            break
        try:
            result=getattr(client,request['method'])(*request['args'])
            answer={'ok':True,'result':result}
        except (OSError,ValueError,RuntimeError,KeyError) as error:
            answer={'ok':False,'error':type(error).__name__}
        send_frame(private,answer,deadline=time.monotonic()+10)
'''


class Driver:
    def __init__(self, anchor):
        self.channel, inherited = socket.socketpair()
        self.process = None
        ready = False
        try:
            self.process = start_process([sys.executable, '-I', '-B', '-c', DRIVER, SOURCE, str(inherited.fileno())],
                                         pass_fds=(inherited.fileno(),))
            producer_control.send_frame(self.channel, anchor, deadline=time.monotonic() + 10)
            observed = producer_control.receive_frame(self.channel, deadline=time.monotonic() + 10)
            assert observed == {'pid': self.process.pid, 'started': process_started(self.process.pid)}
            ready = True
        finally:
            inherited.close()
            if not ready:
                self.close()

    def command(self, method, *args):
        producer_control.send_frame(self.channel, {'method': method, 'args': args}, deadline=time.monotonic() + 10)
        return producer_control.receive_frame(self.channel, deadline=time.monotonic() + 10)

    def call(self, method, *args):
        result = self.command(method, *args)
        assert result['ok'], result
        return result['result']

    def close(self):
        with contextlib.suppress(OSError):
            self.channel.shutdown(socket.SHUT_RDWR)
        self.channel.close()
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
                return False
        return True


def api(app, cookie, path, body=None):
    status, reply, _ = app.call('/ui/studio' + path, method='POST' if body is not None else 'GET', cookie=cookie, body=body)
    assert status in (200, 201), (status, reply)
    return reply


def wait(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    pytest.fail('The isolated software publication did not settle')


@pytest.fixture
def flow(runtime_app, monkeypatch):
    app, cookie, runtime = runtime_app
    monkeypatch.setattr(producer_process, '_command', lambda fd: [sys.executable, '-I', '-B', '-c',
        f'import sys;sys.path.insert(0,{SOURCE!r});from poolhouse.fleet.producer_process import child;child({fd})'])
    with socket.socket(socket.AF_UNIX) as channel:
        channel.connect(runtime.server.bootstrap_path)
        nonce, deadline = 'a' * 64, time.monotonic() + 5
        producer_control.send_frame(channel, {'nonce': nonce}, deadline=deadline)
        anchor = producer_bootstrap.authenticate(app.ui.root, nonce,
            producer_control.receive_frame(channel, deadline=deadline), channel)
    driver = Driver(anchor)
    try:
        receivers = driver.call('receivers')
        offer = driver.call('offer', receivers[0]['id'], 'Generic JSON process')
        inventory = api(app, cookie, '/sources')
        provider = next(item for item in inventory['providers'] if item['id'] == 'software-producer')
        assert any(item['id'] == offer['id'] for item in provider['offers'])
        source = api(app, cookie, '/sources', {'kind': 'webhook', 'provider': 'software-producer',
            'label': 'External software records', 'producer': {'id': offer['id'], 'revision': offer['revision']},
            'idempotency_key': 'source-create'})['source']
        assert source['available'], source
        assert driver.call('status', offer['id'])['offer']['state'] == 'connected'
        assert app.ui.studio_streams.store.indexed('subscription', runtime.context['scope']) == []
        yield app, cookie, runtime, driver, offer, source
    finally:
        failing = sys.exc_info()[0] is not None
        clean = False
        try:
            clean = driver.close()
        finally:
            owned = [row['owned'] for row in runtime.host.rows.values()]
            runtime.close()
            assert all(owner.process.poll() is not None for owner in owned)
        if not failing:
            assert clean, 'The owned SDK did not exit after its private channel closed'


def start(flow):
    app, cookie, _runtime, _driver, _offer, source = flow
    workflow = api(app, cookie, '/workflows', {'title': 'Software records',
        'graph': {'nodes': [{'id': 'record', 'type': 'stream.record'}], 'edges': []}})['workflow']
    sub = api(app, cookie, '/subscriptions', {'workflow_id': workflow['id'], 'revision': workflow['revision'],
        'source_id': source['id'], 'input_node': 'record', 'output_nodes': ['record'],
        'destinations': [{'kind': 'live'}], 'idempotency_key': 'subscription-create'})['subscription']
    assert not sub.get('grant')
    return api(app, cookie, '/subscriptions/' + sub['id'] + '/start',
               {'revision': sub['revision'], 'idempotency_key': 'explicit-start'})['subscription']


def events(flow, sub):
    app, cookie, *_ = flow
    return api(app, cookie, '/subscriptions/' + sub['id'] + '/events?tail=1&limit=20')['events']


def test_real_sdk_source_start_publish_and_duplicate_share_one_inbox(flow, monkeypatch):
    _app, _cookie, runtime, driver, offer, _source = flow
    sub = start(flow)
    calls, original = [], runtime.local._send

    def counted(*args):
        calls.append(args[0]['event_id'])
        return original(*args)

    monkeypatch.setattr(runtime.local, '_send', counted)
    record = {'question': 'How long?', 'retrieved_sources': [{'label': 'Policy', 'text': 'Thirty days.'}]}
    first = driver.call('publish', offer['id'], 'record-1', record)
    assert first['state'] == 'done' and first['receipt']['id']
    assert driver.call('publish', offer['id'], 'record-1', record)['receipt']['id'] == first['receipt']['id']
    assert driver.call('receipt', offer['id'], 'record-1')['receipt']['id'] == first['receipt']['id']
    page = wait(lambda: (rows if rows and rows[0]['state'] == 'done' else None) if (rows := events(flow, sub)) else None)
    assert len(page) == 1 and page[0]['result']['outputs'] == [{'node': 'record', 'port': 'record', 'value': record}]
    assert calls == ['record-1']


def test_committed_ingress_with_lost_ack_requires_receipt_without_resend(flow, monkeypatch):
    _app, _cookie, runtime, driver, offer, _source = flow
    sub = start(flow)
    calls, original = [], runtime.local._send

    def lost(*args):
        calls.append(args[0]['event_id'])
        status, reply = original(*args)
        assert status == 200 and reply['id']
        raise OSError('Controlled loss after actual receiver commit')

    monkeypatch.setattr(runtime.local, '_send', lost)
    pending = driver.call('publish', offer['id'], 'lost-ack', {'record': 7})
    assert pending['state'] == 'interrupted' and 'receipt' not in pending
    settled = driver.call('receipt', offer['id'], 'lost-ack')
    assert settled['state'] == 'done'
    assert driver.call('publish', offer['id'], 'lost-ack', {'record': 7})['receipt']['id'] == settled['receipt']['id']
    assert len(events(flow, sub)) == 1 and calls == ['lost-ack']
