"""The product acknowledgement persists once and never becomes execution authority."""
import json

import pytest
from launch_support import signed
from test_fleet_ui import Serving

from poolhouse.fleet import notice_routes, settings
from poolhouse.fleet.session import parse_cookie
from poolhouse.fleet.settings import Settings, apply_preferences


class NoticeServing(Serving):
    @staticmethod
    def initial_settings():
        return Settings()


def test_acceptance_is_sticky_across_stale_preferences_and_restart(tmp_path):
    path = tmp_path / 'settings.json'
    original = Settings(name='device', setup_done=True)
    original.save(path)
    stale = Settings.load(path)
    original.accept_notice(path, current=lambda: True)
    assert Settings.load(path).experimental_notice_accepted is True
    stale.context = 4096
    stale.save(path)
    restored = Settings.load(path)
    assert restored.experimental_notice_accepted is True and restored.context == 4096
    assert restored.setup_done and restored.name == 'device'


def test_acceptance_preserves_unknown_state_and_rejects_corrupt_storage(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'name': 'device', 'unrelated': {'preserve': True}}))
    held = Settings.load(path)
    held.accept_notice(path, current=lambda: True)
    assert json.loads(path.read_text())['unrelated'] == {'preserve': True}
    path.write_text('{broken')
    held.experimental_notice_accepted = False
    with pytest.raises(ValueError):
        held.accept_notice(path, current=lambda: True)
    assert path.read_text() == '{broken' and held.experimental_notice_accepted is False


@pytest.mark.parametrize('value', [1, 'true', None, [], {}])
def test_acknowledgement_is_an_exact_boolean(value, tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'experimental_notice_accepted': value}))
    assert Settings.load(path).experimental_notice_accepted is False
    held = Settings()
    apply_preferences(held, {'experimental_notice_accepted': True})
    assert held.experimental_notice_accepted is False


def test_current_origin_is_rechecked_before_persistence(tmp_path):
    path = tmp_path / 'settings.json'
    held = Settings(name='device')
    calls = iter([True, False])
    with pytest.raises(PermissionError):
        held.accept_notice(path, current=lambda: next(calls))
    assert not path.exists() and held.experimental_notice_accepted is False


def headers(served, **extra):
    return {'Origin': f'http://127.0.0.1:{served.port}', 'Sec-Fetch-Site': 'same-origin', **extra}


def test_actual_authenticated_accept_is_idempotent_and_survives_restart(tmp_path):
    served = signed(NoticeServing(tmp_path))
    try:
        initial = served.call('/ui/setup')[1]
        assert initial['experimental_notice_accepted'] is False and isinstance(initial['version'], str)
        for _ in range(2):
            code, body, _ = served.call('/ui/notice/accept', method='POST', body={'accepted': True}, headers=headers(served))
            assert code == 200 and body == {'accepted': True}
        served.ui.settings = Settings.load(served.ui.settings_path)
        assert served.call('/ui/setup')[1]['experimental_notice_accepted'] is True
    finally:
        served.close()


@pytest.mark.parametrize('extra', [{'Authorization': 'Bearer fake'}, {'X-Poolhouse-Agent': 'agent'},
                                 {'Origin': 'https://other.invalid'}, {'Sec-Fetch-Site': 'cross-site'}])
def test_agents_and_foreign_origins_cannot_accept(tmp_path, extra):
    served = signed(NoticeServing(tmp_path))
    try:
        code, _, _ = served.call('/ui/notice/accept', method='POST', body={'accepted': True}, headers=headers(served, **extra))
        assert code == 403 and served.ui.settings.experimental_notice_accepted is False
    finally:
        served.close()


def test_absent_revoked_weak_session_and_persistence_failure_remain_unaccepted(tmp_path, monkeypatch):
    served = signed(NoticeServing(tmp_path))
    try:
        code, _, _ = served.call('/ui/notice/accept', method='POST', body={'accepted': True}, headers=headers(served), cookie='')
        assert code == 403
        weak = served.ui.sessions.open('token', 'token')
        code, _, _ = served.call('/ui/notice/accept', method='POST', body={'accepted': True}, headers=headers(served),
                                 cookie=served.ui.sessions.cookie_header(weak))
        assert code == 403
        monkeypatch.setattr(settings, 'write_json', lambda *_: (_ for _ in ()).throw(OSError('write refused')))
        code, _, _ = served.call('/ui/notice/accept', method='POST', body={'accepted': True}, headers=headers(served))
        assert code == 503 and not served.ui.settings.experimental_notice_accepted
        served.ui.sessions.close(parse_cookie(served.cookie))
        code, _, _ = served.call('/ui/notice/accept', method='POST', body={'accepted': True}, headers=headers(served))
        assert code == 403
    finally:
        served.close()


def test_bug_report_target_is_fixed_and_opens_only_on_explicit_authenticated_post(tmp_path, monkeypatch):
    served = signed(NoticeServing(tmp_path))
    opened = []
    monkeypatch.setattr(notice_routes.platform, 'open_path', lambda path: opened.append(path) or 'open')
    try:
        served.call('/ui/setup')
        assert opened == []
        assert served.call('/ui/notice/issues', method='POST', body={'url': 'https://other.invalid'}, headers=headers(served))[0] == 400
        assert opened == []
        assert served.call('/ui/notice/issues', method='POST', body={}, headers=headers(served))[0] == 200
        assert opened == [notice_routes.GIT_URL + '/issues']
    finally:
        served.close()


def test_duplicate_acceptance_fields_are_rejected_over_actual_http(tmp_path):
    import http.client

    served = signed(NoticeServing(tmp_path))
    try:
        channel = http.client.HTTPConnection('127.0.0.1', served.port)
        try:
            channel.request('POST', '/ui/notice/accept', body=b'{"accepted":false,"accepted":true}',
                            headers={**headers(served), 'Cookie': served.cookie,
                                     'X-Poolhouse-UI': '1', 'Content-Type': 'application/json'})
            response = channel.getresponse()
            response.read()
            assert response.status == 400
            assert not Settings.load(served.ui.settings_path).experimental_notice_accepted
        finally:
            channel.close()
    finally:
        served.close()
