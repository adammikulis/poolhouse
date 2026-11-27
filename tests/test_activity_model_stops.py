"""Every stop of a model server is written to the activity log with who asked and why (`poolhouse.serve.inuse.stopped`).

The server is the canned plumbing server (`poolhouse.testing.canned`, marked CANNED, never a model) as a real process on a free port.
"""

from __future__ import annotations

from test_serve_admission import weights
from test_serve_refused_key import (  # noqa: F401  (fixtures)
    asked,
    backend,
    earlier,
    machine,
    manager,
    processes,
)

from poolhouse import activity
from poolhouse.serve import refused_keys
from poolhouse.serve.broker import Broker

pytest_plugins = ["serve_node"]


def test_every_stop_is_written_with_who_asked_and_why(tmp_path, serve_leases, manager, processes):  # noqa: F811
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model)
    refused_keys.settle(Broker(manager, scan=lambda: []), asked(model), lambda _line: None)
    assert old.process.wait(timeout=10) is not None
    (row,) = [one for one in activity.log().entries() if getattr(one, "kind", "") == "model.stop"]
    assert row.subject == f"port:{old.port}" and row.meta["pid"] == old.process.pid
    assert "refuses this build's key" in row.meta["why"] and row.refs["for"]
