"""A daemon restores its Studio, with its authority records under its own root, at startup."""

from poolhouse.fleet.daemon import DaemonOptions, DaemonRuntime
from poolhouse.studio.store import StudioStore


def test_actual_daemon_startup_restores_studio_with_its_authority_under_its_root(tmp_path):
    root = tmp_path / 'traind'
    store = StudioStore(root / 'studio.ladybug')
    store.close()
    options = DaemonOptions(root=root, name='startup-device', port=0, announce=False,
                            track='off', device_report=lambda: {'cpus': 1})
    runtime = DaemonRuntime(options)
    runtime.configure()
    runtime.services()
    try:
        runtime.initialize_listener()
        try:
            # This is the same eager restoration hook that runs before report_startup/run.
            runtime.configure_interface()
            assert runtime.interface.studio.store.graph is not None
            assert runtime.interface.studio_authority.root == (root / 'studio-authority').absolute()
        finally:
            runtime.httpd.server_close()
    finally:
        runtime._shutdown_jobs()
