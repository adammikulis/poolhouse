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
