"""Successful first-run completion must satisfy the browser's actual success contract."""
import http.client
import json

import pytest
from test_fleet_ui import Serving

from poolhouse.fleet.settings import Settings


def test_done_returns_explicit_success_and_remembers_completion(tmp_path):
    app = Serving(tmp_path)
    try:
        assert app.call('/ui/setup')[1]['needs_setup'] is True
        for _ in range(2):
            status, response, _ = app.call('/ui/setup/done', method='POST')
            assert status == 200
            assert response['ok'] is True
            assert response['needs_setup'] is False
        assert Settings.load(app.ui.settings_path).setup_done is True
        assert app.call('/ui/setup')[1]['needs_setup'] is False
    finally:
        app.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_deferred_setup_saves_system_theme_once_before_finishing(tmp_path, theme):
    app = Serving(tmp_path)
    try:
        status, response, _ = app.call('/ui/setup/done', method='POST', body={'system_theme':theme})
        assert status == 200 and response['ok'] is True
        assert response['needs_setup_theme'] is False
        saved = Settings.load(app.ui.settings_path)
        assert saved.setup_done and saved.appearance_initialized
        assert saved.appearance['active_theme'] == theme
        other = 'light' if theme == 'dark' else 'dark'
        assert app.call('/ui/setup/done', method='POST', body={'system_theme':other})[0] == 200
        assert Settings.load(app.ui.settings_path).appearance['active_theme'] == theme
    finally:
        app.close()


def test_invalid_system_theme_cannot_finish_or_write_preferences(tmp_path):
    app = Serving(tmp_path)
    try:
        before = app.ui.settings.public()
        for value in [None, True, {}, 'system']:
            status, result, _ = app.call('/ui/setup/done', method='POST', body={'system_theme':value})
            assert status == 400 and result['error']
            assert app.ui.settings.public() == before
        assert app.call('/ui/setup')[1]['needs_setup'] is True
    finally:
        app.close()


@pytest.mark.parametrize("body", [None, [], "light", 42, {"context":16384}])
def test_malformed_done_body_cannot_finish_setup(tmp_path, body):
    app = Serving(tmp_path)
    try:
        before = app.ui.settings.public()
        connection = http.client.HTTPConnection('127.0.0.1', app.port, timeout=5)
        try:
            connection.request('POST', '/ui/setup/done', body=json.dumps(body),
                               headers={'Content-Type':'application/json', 'X-Poolhouse-UI':'1'})
            response = connection.getresponse()
            assert response.status == 400
            assert json.loads(response.read())['error']
        finally:
            connection.close()
        assert app.ui.settings.public() == before
        assert app.call('/ui/setup')[1]['needs_setup'] is True
    finally:
        app.close()
