"""The setup tool is fixed data; it cannot select an implementation or cause effects."""
import copy
import hashlib
import json
import os
import shutil
import struct
import subprocess
import threading
from types import SimpleNamespace

import pytest

from poolhouse import gate, home
from poolhouse.client import Client
from poolhouse.files import write_json
from poolhouse.fleet import setup_jobs, setup_probe
from poolhouse.fleet.setup_jobs import PreparationStopped
from poolhouse.platform import private_file
from poolhouse.serve import broker_wire
from poolhouse.serve.backend import LlamaServerBackend, ServerSpec
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.ports import free_port
from poolhouse.serve.process import started_at
from poolhouse.testing.canned import (
    CANNED_BODY_HEADER,
    CANNED_HEADER,
    CANNED_HEADER_VALUE,
    CANNED_MARKER,
    CANNED_MODEL_ID,
    canned_chat_reply,
)

pytest_plugins = ['serve_node', 'native_server_kit']


@pytest.fixture
def native_probe(tmp_path, monkeypatch, serve_leases, probe_executable):
    monkeypatch.setenv(broker_wire.LOCAL_ENV, '1')
    binary = tmp_path / 'runtime' / 'fixture-server'
    binary.parent.mkdir()
    shutil.copy2(probe_executable, binary)
    model = tmp_path / 'model.gguf'
    model.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    manager = ServerManager(LlamaServerBackend(binary=binary), state_file=home.state('servers.json'))
    info = manager.lease(ServerSpec(model=model, port=free_port(), context=2048), roam=False, check_flags=False,
                         preflight=False, warmup_request=False)
    server, thread = None, None
    try:
        server = broker_wire._Server(manager.broker)
        path = broker_wire.record_path()
        write_json(path, {'pid': os.getpid(), 'pid_started': started_at(os.getpid()),
                         'port': server.server_address[1], 'token': server.token, 'runtime': server.runtime})
        private_file(path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        receipt = broker_wire.startup(info.port)
        assert receipt and receipt['pid'] == info.pid
        target = {'model': str(model), 'artifact_digest': hashlib.sha256(model.read_bytes()).hexdigest(),
                  'port': info.port, 'context': 2048}
        yield SimpleNamespace(target=target, info=info, model=model, manager=manager, receipt=receipt)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)
            assert not thread.is_alive()
        manager.release(info, grace_s=.1)
        assert info.process.poll() is not None


def complete(jobs, row):
    worker = jobs._worker
    if worker is not None:
        worker.join(timeout=15)
        assert not worker.is_alive()
    return jobs.get(row['id'])


def command():
    return {'idempotency_key': 'a' * 32, 'binding': {'scope': 'fixture', 'device': 'fixture'},
            'selection': {'model_id': 'fixture'}}


def test_actual_native_broker_node_turn_and_sdk_complete_three_inert_requests(tmp_path, native_probe):
    jobs = setup_jobs.Jobs(tmp_path / 'jobs')
    control = setup_probe.Control(lambda: True)

    def operation(progress):
        return setup_probe.observe(native_probe.target, progress, control)

    try:
        row = jobs.prepare(command(), operation, current=lambda: True)
        result = complete(jobs, row)
        assert result['state'] == 'done', result['error']
        assert result['result']['chat_ready'] is True and result['result']['tools_ready'] is True
        assert set(result['probes']) == set(setup_jobs.PROBES)
        assert {value['status'] for value in result['probes'].values()} == {'accepted'}
        assert {value['startup_id'] for value in result['probes'].values()} == {native_probe.receipt['id']}
        assert result['result']['evidence']['parsers'] == dict.fromkeys(setup_jobs.PROBES, 'generic')
        assert jobs.prepare(command(), operation, current=lambda: True)['id'] == row['id']
        assert native_probe.info.log_path.read_text().splitlines().count('POST') == 3
        assert gate.snapshot().get('gpu', []) == []
        assert native_probe.info.process.poll() is None
    finally:
        control.set()


def test_changed_loaded_artifact_is_unverified_without_probe_or_server_stop(tmp_path, native_probe):
    native_probe.model.write_bytes(native_probe.model.read_bytes() + b'changed')
    jobs = setup_jobs.Jobs(tmp_path / 'jobs')
    control = setup_probe.Control(lambda: True)
    try:
        row = jobs.prepare(command(), lambda progress: setup_probe.observe(native_probe.target, progress, control),
                           current=lambda: True)
        result = complete(jobs, row)
        assert result['state'] == 'done'
        assert result['result']['chat_ready'] is False and result['result']['tools_ready'] is False
        assert 'probes' not in result
        assert native_probe.info.log_path.read_text().splitlines().count('POST') == 0
        assert native_probe.info.process.poll() is None
    finally:
        control.set()


def test_parallel_bypass_cannot_be_used_as_setup_compute_admission(tmp_path, native_probe):
    jobs = setup_jobs.Jobs(tmp_path / 'jobs')
    control = setup_probe.Control(lambda: True)

    def operation(progress):
        with gate.parallel('isolated explicit test'):
            return setup_probe.observe(native_probe.target, progress, control)

    try:
        result = complete(jobs, jobs.prepare(command(), operation, current=lambda: True))
        assert result['state'] == 'interrupted' and 'probes' not in result
        assert native_probe.info.log_path.read_text().splitlines().count('POST') == 0
    finally:
        control.set()


def test_native_fixture_compiler_failure_cleans_its_owned_directory(tmp_path, monkeypatch):
    import native_server_kit

    class Factory:
        def mktemp(self, name):
            path = tmp_path / name
            path.mkdir()
            return path

    monkeypatch.setattr(native_server_kit, 'SOURCE', 'invalid fixture C source')
    with pytest.raises(AssertionError), native_server_kit.compiled(Factory()):
        pytest.fail('failed compiler must not yield a binary')
    assert list(tmp_path.iterdir()) == []


def test_native_fixture_metadata_exit_is_reaped_and_compiled_directory_cleaned(tmp_path):
    import native_server_kit

    class Factory:
        def mktemp(self, name):
            path = tmp_path / name
            path.mkdir()
            return path

    with native_server_kit.compiled(Factory()) as binary:
        child = subprocess.Popen([str(binary), '--help'], stdout=subprocess.PIPE)
        try:
            output, _ = child.communicate(timeout=3)
            assert child.returncode == 0 and output.startswith(b'usage: fixture')
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
    assert list(tmp_path.iterdir()) == []


def test_native_fixture_says_it_is_canned_on_every_response(probe_executable):
    import http.client

    child = subprocess.Popen([str(probe_executable), '--port', '0'], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        link = http.client.HTTPConnection('127.0.0.1', int(child.stdout.readline()), timeout=10)
        link.request('GET', '/health')
        health = link.getresponse()
        assert health.getheader(CANNED_HEADER) == CANNED_HEADER_VALUE
        assert json.loads(health.read())['x_canned'] == CANNED_MARKER
        for sent, bare in (({'messages': []}, False), ({'messages': [{'tool_call_id': 'c', 'content': '5'}]}, True)):
            link = http.client.HTTPConnection('127.0.0.1', link.port, timeout=10)
            link.request('POST', '/v1/chat/completions', body=json.dumps(sent))
            answer = link.getresponse()
            assert answer.getheader(CANNED_HEADER) == CANNED_HEADER_VALUE
            assert (answer.getheader(CANNED_BODY_HEADER) == 'unmarked') is bare
            held = json.loads(answer.read())
            text = held['choices'][0]['message']['content']
            assert held['model'] == CANNED_MODEL_ID and held['x_canned'] == CANNED_MARKER
            assert text.startswith(CANNED_MARKER) is not bare
    finally:
        child.terminate()
        said = child.communicate(timeout=10)[1]
    assert CANNED_MARKER in said


def test_local_parser_mismatch_cannot_adopt_broker_readiness(tmp_path, native_probe, monkeypatch):
    monkeypatch.setattr(setup_probe, 'startup', SimpleNamespace(parser_identity=lambda: 'f' * 64))
    jobs = setup_jobs.Jobs(tmp_path / 'jobs')
    control = setup_probe.Control(lambda: True)
    try:
        row = jobs.prepare(command(), lambda progress: setup_probe.observe(native_probe.target, progress, control),
                           current=lambda: True)
        result = complete(jobs, row)
        assert result['state'] == 'done' and result['result']['chat_ready'] is False
        assert 'probes' not in result and 'serving_receipt' not in result
        assert native_probe.info.log_path.read_text().splitlines().count('POST') == 0
    finally:
        control.set()


@pytest.mark.parametrize('change', ['artifact', 'authority'])
def test_change_after_chat_stops_future_probe_requests(tmp_path, native_probe, monkeypatch, change):
    active = [True]
    jobs = setup_jobs.Jobs(tmp_path / 'jobs')
    original = jobs._probe

    def checked(row, phase, request_digest, completion, current):
        result = original(row, phase, request_digest, completion, current)
        if phase == 'chat' and completion is not None:
            if change == 'artifact':
                native_probe.model.write_bytes(native_probe.model.read_bytes() + b'changed')
            else:
                active[0] = False
        return result

    monkeypatch.setattr(jobs, '_probe', checked)
    control = setup_probe.Control(lambda: active[0])
    try:
        row = jobs.prepare(command(), lambda progress: setup_probe.observe(native_probe.target, progress, control),
                           current=lambda: active[0])
        result = complete(jobs, row)
        assert result['state'] == 'interrupted' and set(result['probes']) == {'chat'}
        assert result['probes']['chat']['status'] == 'accepted'
        assert native_probe.info.log_path.read_text().splitlines().count('POST') == 1
        assert gate.snapshot().get('gpu', []) == []
        assert native_probe.info.process.poll() is None
    finally:
        control.set()


def reply(arguments='{"left":2,"right":3}', *, calls=None, content=None, finish='tool_calls'):
    call = {'id': 'call_fixed', 'type': 'function', 'function': {
        'name': 'poolhouse_setup_sum', 'arguments': arguments}}
    _, sent = canned_chat_reply('', bare=True, finish_reason=finish, message={
        'content': content, 'tool_calls': [call] if calls is None else calls})
    return Client.normalize(json.loads(sent))


def test_tool_reply_requires_actual_structured_fixed_call():
    validated = setup_probe._call(reply())
    assert validated == {'id': 'call_fixed', 'type': 'function', 'function': {
        'name': 'poolhouse_setup_sum', 'arguments': '{"left":2,"right":3}'}}
    assert setup_probe._accepted('tool-call', reply()) is True
    assert setup_probe._call(reply(calls=[], content='Call poolhouse_setup_sum(2,3)')) is None


@pytest.mark.parametrize('arguments', [
    '{"left":true,"right":3}', '{"left":2.0,"right":3}',
    '{"left":2,"right":3,"right":3}', '{"left":2,"right":3,"command":"inert"}',
    '{"left":2,"right":4}', '{"left":NaN,"right":3}', '[]', 'null', 'x' * 1025])
def test_tool_reply_rejects_nonexact_or_ambiguous_arguments(arguments):
    assert setup_probe._call(reply(arguments)) is None


@pytest.mark.parametrize('change', ['name', 'type', 'extra', 'duplicate', 'id', 'truncated'])
def test_tool_reply_rejects_extra_or_different_calls(change):
    call = copy.deepcopy(reply().tool_calls[0])
    calls, finish = [call], 'tool_calls'
    if change == 'name':
        call['function']['name'] = 'unavailable_tool'
    elif change == 'type':
        call['type'] = 'other'
    elif change == 'extra':
        call['function']['path'] = '/private/unavailable'
    elif change == 'duplicate':
        calls.append(copy.deepcopy(call))
    elif change == 'id':
        call['id'] = 'bad\nidentifier'
    else:
        finish = 'length'
    assert setup_probe._call(reply(calls=calls, finish=finish)) is None


def test_consumed_result_requires_exact_final_content_without_another_call():
    assert setup_probe._accepted('tool-result', reply(calls=[], content='5', finish='stop')) is True
    for answer in ('The result is 5.', '', None):
        assert setup_probe._accepted('tool-result', reply(calls=[], content=answer, finish='stop')) is False
    assert setup_probe._accepted('tool-result', reply(content='5')) is False
    assert setup_probe._accepted('tool-result', reply(calls=[], content='5', finish='length')) is False


def test_transport_current_check_uses_only_supplied_local_callback_and_cancel_event():
    active, calls = [True], []

    def current():
        calls.append('local')
        return active[0]

    control = setup_probe.Control(current)
    assert control.is_set() is False
    control.set()
    assert control.is_set() is True
    active[0] = False
    with pytest.raises(PreparationStopped):
        control.is_set()
    assert calls == ['local', 'local', 'local']


@pytest.mark.parametrize('field,value', [('port', True), ('port', 0), ('port', 65536),
    ('context', True), ('context', -1), ('artifact_digest', 'x' * 64), ('model', 'relative.gguf')])
def test_invalid_target_cannot_reach_broker_or_compute(field, value):
    target = {'model': '/private/models/fixture.gguf', 'artifact_digest': 'a' * 64,
              'port': 12345, 'context': 4096, field: value}
    with pytest.raises(ValueError, match='target'):
        setup_probe._Probe(target, None, None)
