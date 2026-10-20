"""Default daemon profile restores Studio without requiring an explicit pool-key path."""

import pytest

from poolhouse.fleet.daemon import DaemonOptions, DaemonRuntime
from poolhouse.fleet.discovery import key_path
from poolhouse.studio.store import StudioStore


@pytest.mark.parametrize('profile', [None, 'custom-profile'])
def test_actual_daemon_startup_restores_studio_with_default_or_string_profile(tmp_path, profile):
    root = tmp_path / 'traind'
    store = StudioStore(root / 'studio.ladybug')
    store.close()
    selected = None if profile is None else str(tmp_path / profile / 'pool.key')
    options = DaemonOptions(root=root, name='startup-device', port=0, announce=False,
                            pool_key_path=selected, track='off', device_report=lambda: {'cpus': 1})
    runtime = DaemonRuntime(options)
    runtime.configure()
    runtime.services()
    try:
        runtime.initialize_listener()
        try:
            # This is the same eager restoration hook that runs before report_startup/run.
            runtime.configure_interface()
            assert runtime.interface.studio.store.graph is not None
            assert runtime.interface.studio_authority.root == key_path(selected).parent.absolute()
            assert runtime.interface.pool_key_path == selected
        finally:
            runtime.httpd.server_close()
    finally:
        runtime._shutdown_jobs()
