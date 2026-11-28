"""The device daemon's command hands its arguments to the daemon."""

from poolhouse.api import pool
from poolhouse.board import model_uploads
from poolhouse.cli import daemon as composition, phone_actions
from poolhouse.fleet import autostart


def test_daemon_entrypoint_runs_the_daemon_with_its_arguments(monkeypatch):
    received = []

    def start(argv, **services):
        received.append((argv, services))
        return 7

    monkeypatch.setattr(composition.daemon, "run", start)
    assert composition.main(["--no-web"]) == 7
    assert received == [(["--no-web"], {"pool_actions": pool, "announcer": model_uploads.board_notice, "phone_actions": phone_actions})]


def test_autostart_uses_composed_daemon(monkeypatch):
    monkeypatch.setattr(autostart.shutil, 'which', lambda name: None)
    assert autostart._executable()[-1] == "poolhouse.cli.daemon"
