"""Request context authority and default delivery boundaries."""

import pytest

from poolhouse import http
from poolhouse.fleet import remote
from poolhouse.fleet.join_request import DEFAULT_JOIN, JoinRequest
from poolhouse.fleet.onboard.joining import JoinOptions


@pytest.mark.parametrize('admission', [JoinOptions(action='create'), JoinOptions(existing=True)])
def test_device_join_refuses_new_admission_actions_before_any_side_effect(admission, monkeypatch):
    from poolhouse.fleet import join

    def side_effect(*_args, **_kwargs):
        pytest.fail('a refused admission mutated state or reached the network')
    monkeypatch.setattr(join, 'default_root', side_effect)
    monkeypatch.setattr(join, 'already_running', side_effect)
    monkeypatch.setattr(join, 'join_by_passphrase', side_effect)
    with pytest.raises(ValueError, match='explicit pool admission actions'):
        join.join_machine(JoinRequest(admission=admission))


def test_device_join_profile_keeps_its_original_distinct_port_and_wait_defaults():
    assert DEFAULT_JOIN.admission.mode is None and DEFAULT_JOIN.admission.port is None
    assert DEFAULT_JOIN.admission.timeout_s == 2.0 and DEFAULT_JOIN.wait_s == 20.0
    assert DEFAULT_JOIN.port == 8770
    assert 'fixture-passphrase' not in repr(JoinRequest(passphrase='fixture-passphrase'))


@pytest.mark.parametrize('timeout', [None, 0, 0.25])
def test_member_uses_its_own_token_and_original_timeout_fallback(monkeypatch, timeout):
    seen = []
    def response(url, *, data, method, options):
        seen.append((url, data, method, options))
        return http.Reply(200, b'accepted', {})
    monkeypatch.setattr(remote, 'request_bytes', response)
    member = remote.Member('http://127.0.0.1:9123', 'member-token', timeout=7)
    options = http.Options(token='substituted-token', headers={'X-Check': 'yes'},
                           timeout=timeout, retry=remote.READS)
    assert member._request('PUT', '/record', data=b'\x00\xff', options=options)[:2] == (200, b'accepted')
    url, body, method, forwarded = seen[0]
    assert url == 'http://127.0.0.1:9123/record' and body == b'\x00\xff' and method == 'PUT'
    assert forwarded.token == 'member-token' and options.token == 'substituted-token'
    assert forwarded.timeout == (timeout or 7) and forwarded.retry == remote.READS
    assert dict(forwarded.headers) == {'X-Check': 'yes'}


def test_embedding_delivery_defaults_and_explicit_options_are_preserved(monkeypatch):
    import importlib

    embedding = importlib.import_module('poolhouse.client.embed')
    assert embedding.DEFAULT_EMBED_OPTIONS.timeout == 60.0
    assert embedding.DEFAULT_EMBED_OPTIONS.retry == http.Retry(tries=3)
    seen = []
    def response(url, *, payload, options):
        seen.append((url, payload, options))
        return {'data': [{'embedding': [1.0, 2.0]}]}
    monkeypatch.setattr(embedding, 'request_json', response)
    options = http.Options(token='fixture-token', timeout=0.25, retry=http.Retry(tries=4))
    assert embedding.embed('hello', model='fixture-model', options=options) == [[1.0, 2.0]]
    assert seen == [('http://127.0.0.1:8080/v1/embeddings',
                     {'input': ['hello'], 'model': 'fixture-model'}, options)]


def test_health_polling_delays_keep_the_original_defaults():
    from poolhouse.client.health import DEFAULT_DELAYS

    assert DEFAULT_DELAYS.initial == 0.25 and DEFAULT_DELAYS.maximum == 2.0


@pytest.mark.parametrize('error', [PermissionError('private-token'), RuntimeError('unexpected')])
def test_unavailable_benchmark_memory_is_not_a_zero_measurement(monkeypatch, capsys, error):
    from poolhouse.bench import backends, holding

    monkeypatch.setattr(backends, 'props_of', lambda _url: {})
    monkeypatch.setattr(backends, 'llama_served_by', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(holding, 'serving_pids', lambda *_args: [123])
    def processes(_pids):
        raise error
    monkeypatch.setattr(holding, 'process_tree', processes)
    if isinstance(error, RuntimeError):
        with pytest.raises(RuntimeError, match='unexpected'):
            holding.footprint('http://127.0.0.1:12345')
    else:
        result = holding.footprint('http://127.0.0.1:12345')
        assert 'resident_bytes' not in result and 'resident_peak' not in result
        assert 'private-token' not in capsys.readouterr().err
