"""Canonical data shape drift and refusal boundaries."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from poolhouse.api import node_contract as public

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from generate_node_contract import outputs  # noqa: E402
from node_contract.load import load  # noqa: E402


def test_generated_artifacts_match_canonical_source():
    for path, expected in outputs(ROOT).items():
        assert path.read_text() == expected, str(path.relative_to(ROOT))


def test_public_ids_are_nominal_data_and_do_not_export_private_capabilities():
    assert public.DeviceId("ab" * 32) != public.CertificateDigest("ab" * 32)
    assert not hasattr(public, "SessionLease")
    assert not hasattr(public, "ReceiptId")
    assert not hasattr(public, "ProcessObservation")
    assert not hasattr(public, "AdmittedProducer")


@pytest.mark.parametrize("kind,invalid", [
    (public.U32, "4294967296"), (public.U64, "18446744073709551616"),
    (public.U64, "01"), (public.U64, "-0"), (public.U64, 1),
    (public.U64, True), (public.U64, "\uff11"), (public.U64, "1e2"),
    (public.DeviceId, "AB" * 32), (public.DeviceId, "ab" * 31),
    (public.InstallationId, "not-an-installation"),
])
def test_public_scalar_refusals(kind, invalid):
    with pytest.raises(ValueError):
        kind(invalid)


def identity():
    return {"device_id": "ab" * 32, "certificate_digest": "cd" * 32,
            "installation_id": "00000000-0000-4000-8000-000000000001",
            "public_key": "ef" * 32, "contract_version": "1"}


def test_public_record_roundtrip_preserves_untrusted_claims_without_authenticating_them():
    claim = identity()
    decoded = public.NodeIdentity.from_wire(claim)
    assert decoded.to_wire() == claim
    assert decoded.device_id == public.DeviceId(claim["device_id"])
    assert public.U64("18446744073709551615").value == "18446744073709551615"


@pytest.mark.parametrize("change", ["unknown", "missing", "numeric"])
def test_public_record_refuses_unknown_missing_and_wrongly_encoded_fields(change):
    claim = identity()
    if change == "unknown":
        claim["approved"] = True
    elif change == "missing":
        del claim["installation_id"]
    else:
        claim["contract_version"] = 1
    with pytest.raises(ValueError):
        public.NodeIdentity.from_wire(claim)


@pytest.mark.parametrize("change", ["unknown", "duplicate", "dangling", "private", "authority", "union", "version", "member_collision", "variant_collision"])
def test_source_refuses_ambiguous_and_authority_bearing_definitions(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "types.toml"
    text = path.read_text()
    if change == "unknown":
        text = text.replace('name = "DeviceId"', 'name = "DeviceId"\ntrusted = true')
    elif change == "duplicate":
        text += '\n[[type]]\nname = "DeviceId"\nkind = "scalar"\nvisibility = "public"\nencoding = "hex32"\nissuer = "node"\n'
    elif change == "dangling":
        text = text.replace('type = "Ed25519PublicKey"', 'type = "Absent"')
    elif change == "private":
        text = text.replace('type = "Ed25519PublicKey"', 'type = "SessionLease"')
    elif change == "authority":
        text = text.replace('name = "NodeIdentity"', 'name = "AdmittedProducer"')
    elif change == "union":
        text = text.replace('tag = "kind"', 'tag = "value"')
    elif change == "member_collision":
        text = text.replace('name = "contract_version"', 'name = "to_wire"')
    elif change == "variant_collision":
        text = text.replace('{tag = "none", type = "NoParent"}, {tag = "thread", type = "ParentThread"}',
                            '{tag = "a_b", type = "NoParent"}, {tag = "a__b", type = "ParentThread"}')
    else:
        meta = source / "contract.toml"
        meta.write_text(meta.read_text().replace("version = 1", "version = 2"))
    path.write_text(text)
    with pytest.raises(ValueError):
        load(source)


@pytest.mark.parametrize("change", ["terminal", "duplicate", "atomic", "hold", "borrow", "handle", "error", "shortcut", "initial", "terminals", "checks", "spawn_plan"])
def test_lifecycle_and_adapter_refusals(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    lifecycle = source / "lifecycle.toml"
    adapter = source / "adapters.toml"
    if change == "terminal":
        lifecycle.write_text(lifecycle.read_text().replace('from = "released", event = "consume"', 'from = "consumed", event = "consume"'))
    elif change == "duplicate":
        lifecycle.write_text(lifecycle.read_text().replace('from = "bound", event = "release", to = "released"', 'from = "reserved", event = "bind", to = "released"'))
    elif change == "atomic":
        lifecycle.write_text(lifecycle.read_text().replace('"atomic-single-consumer"', '"unchecked-consumer"'))
    elif change == "hold":
        lifecycle.write_text(lifecycle.read_text().replace('"single-use-hold"', '"reusable-hold"'))
    elif change == "borrow":
        adapter.write_text(adapter.read_text().replace('type = "HeldProcess", ownership = "move"', 'type = "HeldProcess", ownership = "borrow"'))
    elif change == "handle":
        adapter.write_text(adapter.read_text().replace('"KernelCaller"', '"ProcessObservation"'))
    elif change == "shortcut":
        lifecycle.write_text(lifecycle.read_text().replace('from = "released", event = "consume"', 'from = "reserved", event = "consume"'))
    elif change == "initial":
        lifecycle.write_text(lifecycle.read_text().replace('initial = "reserved"', 'initial = "bound"'))
    elif change == "terminals":
        lifecycle.write_text(lifecycle.read_text().replace('terminal = ["consumed", "cancelled", "invalidated"]', 'terminal = ["consumed", "cancelled"]'))
    elif change == "checks":
        lifecycle.write_text(lifecycle.read_text().replace('"current-generations", ', ''))
    elif change == "spawn_plan":
        adapter.write_text(adapter.read_text().replace(', {name = "plan", type = "SpawnPlan", ownership = "borrow"}', ''))
    elif change == "error":
        adapter.write_text(adapter.read_text().replace('"Io"', '"Self"'))
    with pytest.raises(ValueError):
        load(source)


def job_snapshot():
    return {"job_id": "00000000-0000-4000-8000-000000000001",
            "generation": "00000000-0000-4000-8000-000000000002", "revision": "1",
            "project_id": "00000000-0000-4000-8000-000000000003",
            "name": "A run", "operation": "models.inspect",
            "state": {"kind": "preparing", "value": {}}, "return_code": None,
            "submitted_at": "1700000000000000", "started_at": None, "finished_at": None}


def test_job_projection_and_operation_types_are_closed_data():
    from poolhouse.api.node_operations import OPERATION_TYPES
    value = job_snapshot()
    assert public.JobSnapshot.from_wire(value).to_wire() == value
    assert OPERATION_TYPES["jobs.cancel"] == (public.JobCancelRequest, public.JobView, public.JobError)
    assert public.ResourceGeneration(value["generation"]) != public.ProjectId(value["generation"])
    assert public.ExitCode("-2147483648").value == "-2147483648"
    assert public.Boolean(True).value is True


@pytest.mark.parametrize("change", ["missing_null", "process", "environment", "state", "state_extra", "control", "surrogate", "bytes", "exit_zero", "exit_plus", "exit_range"])
def test_job_projection_refuses_ambiguous_or_sensitive_fields(change):
    value = job_snapshot()
    if change == "missing_null":
        del value["started_at"]
    elif change == "process":
        value["pid"] = 42
    elif change == "environment":
        value["env"] = {"TOKEN": "should-not-be-public"}
    elif change == "state":
        value["state"] = {"kind": "unknown", "value": {}}
    elif change == "state_extra":
        value["state"]["value"] = {"authority": True}
    elif change == "control":
        value["name"] = "run\x00name"
    elif change == "surrogate":
        value["name"] = "\ud800"
    elif change == "bytes":
        value["name"] = "é" * 129
    elif change == "exit_zero":
        value["return_code"] = "-0"
    elif change == "exit_plus":
        value["return_code"] = "+1"
    elif change == "exit_range":
        value["return_code"] = "2147483648"
    with pytest.raises(ValueError):
        public.JobSnapshot.from_wire(value)


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_job_cancellation_boolean_is_strict(value):
    with pytest.raises(ValueError):
        public.JobCancellation.from_wire({"requested": value, "settled": False})


@pytest.mark.parametrize("change", ["private", "duplicate", "durable", "authority", "unknown"])
def test_operation_source_refuses_missing_types_and_unsafe_shapes(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "operations.toml"
    text = path.read_text()
    if change == "private":
        text = text.replace('response = "JobView"', 'response = "SessionLease"')
    elif change == "duplicate":
        text = text.replace('name = "jobs.cancel"', 'name = "jobs.get"')
    elif change == "durable":
        text = text.replace('retry = "read-current-state"', 'retry = "durable-principal-resource-generation-payload-bound"')
    elif change == "authority":
        text = text.replace('"authenticated-current-principal"', '"caller-claims-person"')
    elif change == "unknown":
        text += '\ntrust_payload = true\n'
    path.write_text(text)
    with pytest.raises(ValueError):
        load(source)


def test_source_refuses_nested_nullable_values(tmp_path):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "types.toml"
    path.write_text(path.read_text().replace('item = "ExitCode"', 'item = "OptionalUnixMicros"'))
    with pytest.raises(ValueError, match="nonnullable"):
        load(source)


def test_job_lists_and_output_are_bounded_and_nominal():
    view = {"job": job_snapshot(), "cancellation": {"requested": False, "settled": False}}
    page = public.JobViewList.from_wire([view] * 100)
    assert len(page.items) == 100
    assert page.to_wire() == [view] * 100
    with pytest.raises(ValueError):
        public.JobViewList.from_wire([view] * 101)
    with pytest.raises(ValueError):
        public.JobViewList.from_wire({})
    with pytest.raises(ValueError):
        public.JobViewList([public.JobView.from_wire(view)])
    assert public.JobListCursor("ab" * 32) != public.JobOutputCursor("ab" * 32)
    assert public.JobOutputBytes("00c3a90aff").value == "00c3a90aff"
    assert public.JobOutputBytes("ff" * 65536).value == "ff" * 65536
    assert public.JobPageLimit("100").value == "100"
    assert public.JobOutputByteLimit("65536").value == "65536"


@pytest.mark.parametrize("kind,value", [
    (public.JobPageLimit, "0"), (public.JobPageLimit, "101"),
    (public.JobPageLimit, "01"), (public.JobPageLimit, 1),
    (public.JobOutputByteLimit, "0"), (public.JobOutputByteLimit, "65537"),
    (public.JobOutputBytes, "0"), (public.JobOutputBytes, "AA"),
    (public.JobOutputBytes, "ff" * 65537),
])
def test_job_page_and_output_scalar_refusals(kind, value):
    with pytest.raises(ValueError):
        kind(value)


@pytest.mark.parametrize("change", ["list_bound", "list_private", "range_type", "range_order", "range_overflow"])
def test_collection_and_numeric_source_constraints_refuse(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "types.toml"
    text = path.read_text()
    if change == "list_bound":
        text = text.replace('max_items = 100', 'max_items = 0')
    elif change == "list_private":
        text = text.replace('item = "JobView"', 'item = "SessionLease"')
    elif change == "range_type":
        text = text.replace('minimum = 1', 'minimum = true')
    elif change == "range_order":
        text = text.replace('maximum = 100', 'maximum = 0')
    elif change == "range_overflow":
        text = text.replace('maximum = 100', 'maximum = 4294967296')
    path.write_text(text)
    with pytest.raises(ValueError):
        load(source)


def test_shared_job_scalar_vectors():
    import json
    vectors = json.loads((ROOT / "contracts/node/vectors.json").read_text())
    for row in vectors["job_scalars"]:
        kind = getattr(public, row["type"])
        for value in row["accepted"]:
            assert kind(value).value == value
        for value in row["refused"]:
            with pytest.raises(ValueError):
                kind(value)


def agent_control_snapshot():
    return {"agent_id": "00000000-0000-4000-8000-000000000001",
            "generation": "00000000-0000-4000-8000-000000000002",
            "project_id": "00000000-0000-4000-8000-000000000003",
            "project_generation": "00000000-0000-4000-8000-000000000004",
            "revision": "1", "control_revision": "3", "name": "local-worker", "state": {"kind": "working", "value": {}},
            "pause_requested": True, "running": None, "steps": None, "tasks": "0",
            "ignored": None, "memory_bytes": None}


def test_agent_snapshot_distinguishes_pause_intent_and_unknown_observations():
    value = agent_control_snapshot()
    assert public.AgentControlSnapshot.from_wire(value).to_wire() == value
    assert public.AgentId(value["agent_id"]) != public.JobId(value["agent_id"])
    assert public.TaskId("task-001").value == "task-001"
    assert public.TaskId("task:0123456789abcdef0123456789abcdef").value.startswith("task:")


@pytest.mark.parametrize("change", ["missing_unknown", "authority", "process", "lease", "numeric_bool", "unknown_state"])
def test_agent_snapshot_refuses_authority_process_and_ambiguous_fields(change):
    value = agent_control_snapshot()
    if change == "missing_unknown":
        del value["memory_bytes"]
    elif change == "authority":
        value["person"] = True
    elif change == "process":
        value["pid"] = 42
    elif change == "lease":
        value["lease"] = "private-lease"
    elif change == "numeric_bool":
        value["running"] = 1
    elif change == "unknown_state":
        value["state"] = {"kind": "assume_ready", "value": {}}
    with pytest.raises(ValueError):
        public.AgentControlSnapshot.from_wire(value)


def task_stop_request():
    return {**{key: value for key, value in agent_control_snapshot().items()
               if key in {"agent_id", "generation", "project_id", "project_generation"}},
            "request_id": "00000000-0000-4000-8000-000000000005",
            "task": {"board_id": "ab" * 32, "task_id": "task-001"},
            "task_generation": "00000000-0000-4000-8000-000000000006",
            "execution_id": "00000000-0000-4000-8000-000000000007",
            "execution_generation": "00000000-0000-4000-8000-000000000008"}


def test_task_stop_names_exact_execution_and_preserves_opaque_task_id():
    from poolhouse.api.node_operations import OPERATION_TYPES
    value = task_stop_request()
    assert public.AgentTaskStopRequest.from_wire(value).to_wire() == value
    assert OPERATION_TYPES["agents.task.stop"][0] is public.AgentTaskStopRequest
    assert OPERATION_TYPES["agents.stop"][0] is public.AgentControlRequest
    with pytest.raises(ValueError):
        public.AgentControlRequest.from_wire(value)
    for field in ("task_generation", "execution_id", "execution_generation", "project_generation"):
        missing = dict(value)
        del missing[field]
        with pytest.raises(ValueError):
            public.AgentTaskStopRequest.from_wire(missing)


@pytest.mark.parametrize("change", ["project_generation", "task_generation", "execution_generation", "task_request"])
def test_agent_operation_source_requires_generation_bindings(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "types.toml"
    text = path.read_text()
    if change in {"project_generation", "task_generation", "execution_generation"}:
        text = text.replace('{name = "' + change + '", type = "ResourceGeneration"}',
                            '{name = "' + change + '", type = "ProjectId"}')
        path.write_text(text)
    else:
        path = source / "operations.toml"
        path.write_text(path.read_text().replace('request = "AgentTaskStopRequest"', 'request = "AgentTaskStartRequest"'))
    with pytest.raises(ValueError, match="generation binding"):
        load(source)


def test_agent_control_requires_compare_and_swap_revision():
    value = {key: value for key, value in task_stop_request().items()
             if key in {"agent_id", "generation", "project_id", "project_generation", "request_id"}}
    with pytest.raises(ValueError):
        public.AgentControlRequest.from_wire(value)
    value["expected_control_revision"] = "3"
    assert public.AgentControlRequest.from_wire(value).to_wire() == value


def test_agent_operation_source_refuses_missing_control_revision(tmp_path):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "types.toml"
    path.write_text(path.read_text().replace(', {name = "expected_control_revision", type = "U64"}', ''))
    with pytest.raises(ValueError, match="generation binding"):
        load(source)


def test_shared_timestamp_conversion_vectors():
    import json
    vectors = json.loads((ROOT / "contracts/node/vectors.json").read_text())["unix_microseconds"]
    for seconds, micros, expected in vectors["accepted"]:
        assert public.UnixMicros.from_parts(int(seconds), int(micros)).value == expected
    for seconds, micros in vectors["refused"]:
        with pytest.raises(ValueError):
            public.UnixMicros.from_parts(int(seconds), int(micros))
    for seconds, micros in [(True, 0), (0, False), (1.0, 0), (0, "1")]:
        with pytest.raises(ValueError):
            public.UnixMicros.from_parts(seconds, micros)
    assert not hasattr(public.U64, "from_parts")
    assert not hasattr(public, "AuditToken")


@pytest.mark.parametrize("change", ["unit_type", "unit_range", "public_token"])
def test_source_refuses_ambiguous_native_representations(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "types.toml"
    text = path.read_text()
    if change == "unit_type":
        text = text.replace('unit = "unix-microseconds"', 'unit = "unix-seconds"')
    elif change == "unit_range":
        text = text.replace('unit = "unix-microseconds"', 'unit = "unix-microseconds"\nminimum = 0\nmaximum = 100')
    else:
        text = text.replace('name = "AuditToken"\nkind = "scalar"\nencoding = "audit-token-le32"\nissuer = "kernel-adapter"\nvisibility = "private"',
                            'name = "AuditToken"\nkind = "scalar"\nencoding = "audit-token-le32"\nissuer = "kernel-adapter"\nvisibility = "public"')
    path.write_text(text)
    with pytest.raises(ValueError):
        load(source)


def test_retained_operation_mapping_matches_current_declarations():
    import json
    mapping = json.loads((ROOT / "contracts/node/operation-mapping.json").read_text())
    meta, _, _ = load(ROOT / "contracts/node")
    declared = {row["name"]: row for row in meta["operations"]}
    mapped = {row["operation"]: row for row in mapping["operations"]}
    assert len(mapped) == len(mapping["operations"])
    assert set(mapped) == set(declared)
    for name, operation in declared.items():
        row = mapped[name]
        for source, target in (("request", "request_type"), ("response", "result_type"), ("error", "error_type"),
                               ("subscription", "canonical_subscription"), ("cancellation", "canonical_request_cancellation")):
            assert row[target] == operation[source], name
        references = [row["contract_declaration"], *row["legacy_handlers"], *row["retained_callers"],
                      *(item["site"] for item in row.get("related_unmapped", []))]
        assert all(reference in mapping["sites"] for reference in references), name
    output = mapped["jobs.output.read"]
    assert "member_metrics" not in output["retained_callers"]
    assert "member_metrics" in {item["site"] for item in output["related_unmapped"]}
