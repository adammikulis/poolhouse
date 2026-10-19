"""Durable dispatch identities for independent device execution."""

from types import SimpleNamespace

import pytest

from poolhouse.studio.contracts import validate_graph
from poolhouse.studio.engine import WorkflowEngine
from poolhouse.studio.packages import export_package
from poolhouse.studio.store import StudioStore

PRINCIPAL = {"id": "test-session", "scope": "test-board", "owner": True}


def graph():
    return {"nodes": [{"id": name, "type": "prompt", "version": 1, "parameters": {"template": name},
                        "execution": {"machine": name}} for name in ["device-a", "device-b"]], "edges": [], "groups": {}}


class Dispatcher:
    def __init__(self, store):
        self.store, self.started, self.stopped = store, [], []

    def is_remote(self, node):
        return True

    def validate(self, nodes, principal):
        return []

    def start(self, run, node, inputs, context, execution):
        saved = self.store.get("run", run["id"], run["scope"])
        assert saved["nodes"][node["id"]]["execution"] == execution
        self.started.append(execution["id"])

    def poll(self, execution, context):
        return None

    def cancel(self, execution, principal):
        self.stopped.append(execution["id"])

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    store = StudioStore(tmp_path / "studio.ladybug")
    runner = SimpleNamespace(files_root=tmp_path / "files", jobs={})
    dispatcher = Dispatcher(store)
    engine = WorkflowEngine(store, runner, SimpleNamespace(), lambda principal, node, inputs: True, dispatcher)
    engine.close()
    try:
        yield store, engine, dispatcher
    finally:
        store.close()


def test_device_bindings_validated_but_never_exported():
    assert validate_graph(graph())["valid"]
    package = export_package(graph(), package_id="devices", version="1.0.0", name="Example")
    assert all("execution" not in node for node in package["graph"]["nodes"])
    bad = graph()
    bad["nodes"][0]["execution"] = {"url": "https://invalid.local"}
    assert not validate_graph(bad)["valid"]


def test_independent_devices_dispatch_without_local_runner_slots(setup):
    store, engine, dispatcher = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Devices", graph())
    run = engine.advance(engine.submit(PRINCIPAL, workflow, [], "devices"))
    assert len(dispatcher.started) == 2
    assert {state["state"] for state in run["nodes"].values()} == {"running"}
    assert len({state["execution"]["machine"] for state in run["nodes"].values()}) == 2
    engine.advance(run)
    assert len(dispatcher.started) == 2


def test_recovery_polls_bound_target_without_resubmission(setup):
    store, engine, dispatcher = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Devices", graph())
    run = engine.advance(engine.submit(PRINCIPAL, workflow, [], "devices"))
    engine._recover()
    recovered = store.get("run", run["id"], PRINCIPAL["scope"])
    engine.advance(recovered)
    assert len(dispatcher.started) == 2
    assert {state["state"] for state in recovered["nodes"].values()} == {"running"}


def test_cancellation_routes_to_original_target_id(setup):
    store, engine, dispatcher = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Devices", graph())
    run = engine.advance(engine.submit(PRINCIPAL, workflow, [], "devices"))
    engine.cancel(PRINCIPAL, run["id"])
    assert sorted(dispatcher.started) == sorted(dispatcher.stopped)


def test_remote_dependency_failure_prevents_all_dispatch(setup):
    store, engine, dispatcher = setup
    dispatcher.validate = lambda nodes, principal: ["missing runtime"]
    workflow = store.save_workflow(PRINCIPAL["scope"], "Devices", graph())
    with pytest.raises(ValueError, match="missing runtime"):
        engine.submit(PRINCIPAL, workflow, [], "devices")
    assert dispatcher.started == []
    assert store.records("run", PRINCIPAL["scope"]) == []
