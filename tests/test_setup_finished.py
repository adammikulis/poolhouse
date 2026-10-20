"""Successful first-run completion must satisfy the browser's actual success contract."""
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
