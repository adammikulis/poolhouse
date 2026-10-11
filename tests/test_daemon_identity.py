"""The daemon serves under the certificate the device joined with, and a daemon on an isolated root keeps its own."""

from poolhouse import home
from poolhouse.fleet import daemon, tls
from poolhouse.fleet.onboard import cli as onboard


def test_production_daemon_reuses_joining_certificate_and_isolated_roots_stay_separate(tmp_path, monkeypatch):
    monkeypatch.setenv('POOLHOUSE_HOME', str(tmp_path / 'installation'))
    joined = onboard._identity(home.state('onboard'))
    serving = tls.identity(daemon.identity_directory(daemon.default_root()), 'daemon')
    assert serving.beacon == joined.beacon
    isolated = tmp_path / 'isolated-daemon'
    assert daemon.identity_directory(isolated) == isolated / 'tls'
