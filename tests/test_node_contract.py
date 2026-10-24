"""Canonical data shape drift and refusal boundaries."""
from __future__ import annotations

import ast
import json
import pickle
import re
import shutil
import struct
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


@pytest.mark.parametrize("change", ["unknown", "duplicate", "dangling", "private", "authority", "union", "version", "member_collision", "variant_collision", "codec_collision"])
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
    elif change == "codec_collision":
        text = text.replace('name = "NodeIdentity"', 'name = "ValueError"')
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
        adapter.write_text(adapter.read_text().replace('"AuthenticatedCaller"', '"ProcessObservation"'))
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
    import tomllib
    mapping = json.loads((ROOT / "contracts/node/operation-mapping.json").read_text())
    meta, _, _ = load(ROOT / "contracts/node")
    root_names = {row["name"] for row in tomllib.loads((ROOT / "contracts/node/operations.toml").read_text())["operation"]}
    declared = {row["name"]: row for row in meta["operations"] if row["name"] in root_names}
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


def domain_fixture(tmp_path):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source, ignore=shutil.ignore_patterns("domains"))
    (source / "domains").mkdir()
    return source


def domain_value():
    return ('version = 1\nstatus = "proposal"\noperation = []\n'
            '[[type]]\nname = "DomainValue"\nkind = "scalar"\nvisibility = "public"\nencoding = "hex32"\nissuer = "data"\n')


def test_domain_fragments_merge_before_global_type_validation(tmp_path):
    source = domain_fixture(tmp_path)
    (source / "domains" / "z_value.toml").write_text(domain_value())
    (source / "domains" / "a_envelope.toml").write_text(
        'version = 1\nstatus = "proposal"\noperation = []\n'
        '[[type]]\nname = "DomainEnvelope"\nkind = "record"\nvisibility = "public"\n'
        'fields = [{name = "value", type = "DomainValue"}]\n')
    meta, rows, first = load(source)
    assert [row["file"] for row in meta["domains"]] == ["a_envelope.toml", "z_value.toml"]
    assert [row["name"] for row in rows].index("DomainValue") < [row["name"] for row in rows].index("DomainEnvelope")
    assert load(source)[2] == first
    value = source / "domains" / "z_value.toml"
    value.write_text(value.read_text().replace('encoding = "hex32"', 'encoding = "uuid"'))
    assert load(source)[2] != first


@pytest.mark.parametrize("change", ["unknown", "empty", "missing_array", "version", "duplicate", "private", "cycle", "duplicate_operation", "unknown_entry"])
def test_domain_fragments_refuse_ambiguous_and_escaping_data(tmp_path, change):
    source = domain_fixture(tmp_path)
    path = source / "domains" / "sample.toml"
    text = domain_value()
    if change == "unknown":
        text = 'unexpected = true\n' + text
    elif change == "empty":
        text = 'version = 1\nstatus = "proposal"\noperation = []\ntype = []\n'
    elif change == "missing_array":
        text = text.replace('operation = []\n', '')
    elif change == "version":
        text = text.replace('version = 1', 'version = true')
    elif change == "duplicate":
        text = text.replace('name = "DomainValue"', 'name = "DeviceId"')
    elif change in {"private", "cycle"}:
        item = "SessionLease" if change == "private" else "DomainValue"
        text = ('version = 1\nstatus = "proposal"\noperation = []\n'
                '[[type]]\nname = "DomainValue"\nkind = "record"\nvisibility = "public"\n'
                'fields = [{name = "value", type = "' + item + '"}]\n')
    elif change == "duplicate_operation":
        text = (source / "operations.toml").read_text().replace('authorization = "node-checked-code"\n', '')
        text = text.replace('[[operation]]', 'type = []\n[[operation]]', 1)
    elif change == "unknown_entry":
        (source / "domains" / "nested").mkdir()
    path.write_text(text)
    with pytest.raises(ValueError):
        load(source)


@pytest.mark.parametrize("target", ["directory", "entry"])
def test_domain_fragments_reject_symlink_metadata_before_reading(tmp_path, monkeypatch, target):
    source = domain_fixture(tmp_path)
    entry = source / "domains" / "sample.toml"
    entry.write_text(domain_value())
    selected = source / "domains" if target == "directory" else entry
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == selected or original(path))
    with pytest.raises(ValueError):
        load(source)


def test_generated_collection_minimum_unique_and_nullable_union(tmp_path, monkeypatch):
    import importlib.util

    from node_contract import python as generator
    source = domain_fixture(tmp_path)
    (source / "domains" / "collection.toml").write_text(
        'version = 1\nstatus = "proposal"\noperation = []\n'
        '[[type]]\nname = "DomainChoices"\nkind = "list"\nvisibility = "public"\n'
        'item = "DeviceId"\nmin_items = 1\nmax_items = 2\nunique = true\n'
        '[[type]]\nname = "DomainChoice"\nkind = "union"\nvisibility = "public"\ntag = "kind"\n'
        'variants = [{tag = "selected", type = "Empty"}]\n'
        '[[type]]\nname = "OptionalDomainChoice"\nkind = "optional"\nvisibility = "public"\nitem = "DomainChoice"\n')
    meta, rows, digest = load(source)
    path = tmp_path / "generated_contract.py"
    path.write_text(generator.render(meta, rows, digest))
    spec = importlib.util.spec_from_file_location("node_contract_collection_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec.loader.exec_module(module)
    valid = ["ab" * 32, "cd" * 32]
    assert module.DomainChoices.from_wire(valid).to_wire() == valid
    for invalid in ([], [valid[0], valid[0]], [*valid, "ef" * 32]):
        with pytest.raises(ValueError):
            module.DomainChoices.from_wire(invalid)
    assert module.OptionalDomainChoice.from_wire(None).to_wire() is None
    selected = {"kind": "selected", "value": {}}
    assert module.OptionalDomainChoice.from_wire(selected).to_wire() == selected


@pytest.mark.parametrize("change", ["minimum_bool", "minimum_negative", "minimum_over_max", "unique_nonbool"])
def test_collection_bounds_refuse_ambiguous_definitions(tmp_path, change):
    source = domain_fixture(tmp_path)
    constraints = {"minimum_bool": "min_items = true", "minimum_negative": "min_items = -1",
                   "minimum_over_max": "min_items = 3", "unique_nonbool": "unique = 1"}
    (source / "domains" / "collection.toml").write_text(
        'version = 1\nstatus = "proposal"\noperation = []\n'
        '[[type]]\nname = "DomainChoices"\nkind = "list"\nvisibility = "public"\n'
        'item = "DeviceId"\nmax_items = 2\n' + constraints[change] + '\n')
    with pytest.raises(ValueError):
        load(source)


def test_every_contract_domain_has_pinned_operation_evidence():
    import json
    import tomllib
    meta, _, _ = load(ROOT / "contracts/node")
    for domain in meta.get("domains", []):
        name = Path(domain["file"]).stem
        source = tomllib.loads((ROOT / "contracts/node/domains" / domain["file"]).read_text())
        evidence = json.loads((ROOT / "contracts/node/domain-evidence" / (name + ".json")).read_text())
        mappings = evidence.get("operation_mapping", evidence.get("operations"))
        actual = [row.get("operation", row.get("name")) for row in mappings]
        assert len(set(actual)) == len(actual), name
        declared = {row["name"]: row for row in source["operation"]}
        assert set(actual) == set(declared), name
        assert len(evidence["source_commit"]) == 40
        for row in mappings:
            operation = declared[row.get("operation", row.get("name"))]
            for field in ("request", "response", "authorization"):
                if field in row:
                    assert row[field] == operation[field], (name, operation["name"], field)


def test_domain_observations_preserve_fractional_margin_and_unknown_startup():
    for margin in ({"kind": "surplus", "value": {"numerator": "17", "denominator": "2"}},
                   {"kind": "shortfall", "value": {"numerator": "1", "denominator": "2"}}):
        assert public.ModelByteMargin.from_wire(margin).to_wire() == margin
    with pytest.raises(ValueError):
        public.ModelByteMargin.from_wire({"kind": "shortfall", "value": {"numerator": "0", "denominator": "2"}})
    with pytest.raises(ValueError):
        public.ModelExactRatio.from_wire({"numerator": "1", "denominator": "0"})
    unknown = {"kind": "unknown", "value": {"reason": "service state unavailable", "observed_at": "1", "last_known": None}}
    assert public.StartupServiceObservation.from_wire(unknown).to_wire() == unknown


def test_domain_sets_and_empty_approval_snapshot_have_closed_wire_shapes():
    monday = {"kind": "mon", "value": {}}
    assert public.SettingsWeekdays.from_wire([monday]).to_wire() == [monday]
    for days in ([], [monday, monday]):
        with pytest.raises(ValueError):
            public.SettingsWeekdays.from_wire(days)
    snapshot = {"snapshot_id": "ab" * 32, "revision": "1", "items": [], "complete": True,
                "next_cursor": None, "cursor": "cd" * 32}
    assert public.ApprovalWatchSnapshot.from_wire(snapshot).to_wire() == snapshot
    snapshot["approved"] = True
    with pytest.raises(ValueError):
        public.ApprovalWatchSnapshot.from_wire(snapshot)


def test_python_package_facade_preserves_identity_and_freezes_codec():
    import importlib
    from typing import get_type_hints

    from poolhouse.api.node_operations import OPERATION_TYPES
    codec = importlib.import_module("poolhouse.api.node_contract._codec")
    group = importlib.import_module(public.ModelFitBinding.__module__)
    assert public.ModelFitBinding is group.ModelFitBinding
    assert public.ModelView is OPERATION_TYPES["models.get"][1]
    assert get_type_hints(public.ModelFitBinding)["model"] is public.ModelRef
    value = public.DeviceId("ab" * 32)
    assert pickle.dumps(value)
    assert pickle.whichmodule(type(value), type(value).__qualname__) == public.DeviceId.__module__
    assert getattr(importlib.import_module(public.DeviceId.__module__), public.DeviceId.__qualname__) is public.DeviceId
    interface = ast.parse((ROOT / "src/poolhouse/api/node_contract/__init__.pyi").read_text())
    exports = next(ast.literal_eval(node.value) for node in interface.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets))
    assert exports == public.__all__
    imports = {alias.asname or alias.name for node in interface.body if isinstance(node, ast.ImportFrom)
               for alias in node.names if not (alias.asname or alias.name).startswith("_")}
    assert imports == set(exports) - {"CONTRACT_VERSION", "SCHEMA_SHA256"}
    with pytest.raises(TypeError):
        codec._TYPES["InjectedType"] = str
    with pytest.raises(RuntimeError):
        codec._register(public.DeviceId)
    assert not (ROOT / "src/poolhouse/api/node_contract.py").exists()
    for path in (ROOT / "src/poolhouse/api/node_contract").glob("*.py"):
        assert len(path.read_text().splitlines()) <= 900, path.name


@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("kind", ["unknown", "symlink"])
def test_generator_refuses_shadow_or_symlink_type_interfaces(tmp_path, monkeypatch, capsys, check, kind):
    import generate_node_contract as generator
    shutil.copytree(ROOT / "contracts/node", tmp_path / "contracts/node")
    package = tmp_path / "src/poolhouse/api/node_contract"
    package.mkdir(parents=True)
    if kind == "unknown":
        (package / "_core.pyi").write_text("# unauthorized type shadow\n")
    else:
        outside = tmp_path / "outside.pyi"
        outside.write_text("# original\n")
        (package / "__init__.pyi").symlink_to(outside)
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_node_contract.py", *(["--check"] if check else [])])
    with pytest.raises(SystemExit, match="1"):
        generator.main()
    assert "unknown or stale generated contract package entry" in capsys.readouterr().err
    assert not (package / "__init__.py").exists()
    if kind == "symlink":
        assert outside.read_text() == "# original\n"


@pytest.mark.parametrize("method, change", [
    ("authenticate_producer", "launch"),
    ("receive_producer_message", "borrow"),
    ("hold_spawn", "binding"),
    ("hold_spawn", "plan"),
    ("inspect_caller", "running"),
    ("inspect_caller", "binding"),
    ("cleanup_before_consume", "obligation"),
    ("cancel_hold", "revoked"),
    ("terminate", "obligation"),
    ("release", "flat_failure"),
    ("hold_spawn", "flat_failure"),
    ("terminate", "unit_result"),
    ("cancel_hold", "wrong_phase"),
    ("receive_producer_message", "duplicate"),
    ("inspect_caller", "unknown"),
    ("release", "unknown_failure"),
])
def test_native_adapter_refuses_lost_provenance_or_custody(tmp_path, method, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "adapters.toml"
    text = path.read_text()
    chunks = text.split("[[method]]")
    index = next(i for i, chunk in enumerate(chunks) if f'name = "{method}"' in chunk)
    chunk = chunks[index]
    if change in {"launch", "binding", "plan", "running", "obligation", "revoked"}:
        chunk, count = re.subn(r',? ?\{name = "' + change + r'", type = "[A-Za-z]+", ownership = "[a-z]+"\}', "", chunk)
        assert count == 1
    elif change == "borrow":
        chunk = chunk.replace('ownership = "borrow"', 'ownership = "move"')
    elif change == "flat_failure":
        chunk = chunk[:chunk.index("failure =")] + 'failure = "NativeAdapterError"\n'
    elif change == "unknown_failure":
        chunk = chunk.replace('failure = "OwnedReleaseFailure"', 'failure = "MissingFailure"')
    elif change == "unit_result":
        chunk = chunk.replace('result = "HolderCleanup"', 'result = "unit"')
    elif change == "wrong_phase":
        chunk = chunk.replace('type = "TerminalCleanupObligation"', 'type = "BeforeConsumeObligation"')
    elif change == "duplicate":
        chunk += "\n[[method]]" + chunk
    elif change == "unknown":
        chunk = chunk.replace(f'name = "{method}"', 'name = "inspect_unbound"')
    assert chunk != chunks[index]
    chunks[index] = chunk
    path.write_text("[[method]]".join(chunks))
    with pytest.raises(ValueError):
        load(source)


def test_native_adapter_declarations_remain_private_and_without_proof_issuers():
    from node_contract import adapters
    source = adapters.render(adapters.load(ROOT / "contracts/node"))
    assert "pub(crate) trait NativeCallerPlatform<G: NativeGraph>" in source
    assert ": native_seal::Platform" in source
    assert "pub trait NativeCallerPlatform" not in source
    assert "pub(crate) trait NativeGraph: native_seal::Graph + Sized" in source
    assert "type Binding: BindingProjection<Self>;" in source
    for name in ("VerifiedProducerLaunch", "AuthenticatedProducerMessage", "HolderCleanup", "OwnedCleanupFailure"):
        assert not hasattr(public, name)
    assert "impl NativeCallerPlatform" not in source


@pytest.mark.parametrize("requirement", [
    "version", "authenticated-native-birth-or-same-producer-resume",
    "authenticated-native-invocation", "stable-original-occurrence-identity",
    "producer-stream-global-event-deduplication", "current-producer-boot-and-admission",
    "one-effect-permit-pending-on-loss-or-crash-no-replacement",
    "atomically-revoke-authorization-before-returning-physical-cleanup-obligations",
])
def test_lifecycle_v2_refuses_weakened_event_or_cleanup_semantics(tmp_path, requirement):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "lifecycle.toml"
    text = path.read_text()
    if requirement == "version":
        text = text.replace("version = 2", "version = 1")
    else:
        text = text.replace('"' + requirement + '"', '"unchecked-replacement"', 1)
    assert text != path.read_text()
    path.write_text(text)
    with pytest.raises(ValueError):
        load(source)


def test_finite_f64_shared_vectors_preserve_exact_bits_without_coercion():
    vectors = json.loads((ROOT / "contracts/node/vectors.json").read_text())["finite_f64"]
    for bits in vectors["accepted"]:
        value = public.FiniteF64(bits)
        number = value.to_float()
        assert struct.pack(">d", number).hex() == bits
        assert public.FiniteF64.from_float(number) == value
    assert public.FiniteF64("0000000000000000") != public.FiniteF64("8000000000000000")
    for invalid in vectors["refused"]:
        with pytest.raises(ValueError):
            public.FiniteF64(invalid)
    for invalid in (float("inf"), float("-inf"), float("nan"), 1, True, "1", None):
        with pytest.raises(ValueError):
            public.FiniteF64.from_float(invalid)


def test_utf8_content_preserves_controls_and_counts_bytes_without_normalization():
    vectors = json.loads((ROOT / "contracts/node/vectors.json").read_text())["utf8"]
    for text in vectors["accepted"]:
        assert public.Utf8Chunk(text).value.encode("utf-8") == text.encode("utf-8")
    assert public.Utf8Chunk("é") != public.Utf8Chunk("e\u0301")
    boundary = vectors["boundary"]["text"] * vectors["boundary"]["count"]
    assert len(public.Utf8Chunk(boundary).value.encode("utf-8")) == vectors["boundary"]["bytes"]
    for invalid in [*vectors["wrong_types"], boundary + "a", *(json.loads(raw) for raw in vectors["json_refused"])]:
        with pytest.raises(ValueError):
            public.Utf8Chunk(invalid)
    with pytest.raises(ValueError):
        public.JobDisplayName("line\nend")


def test_connectivity_observations_preserve_prejoin_readiness_unknowns_and_paging():
    vectors = json.loads((ROOT / "contracts/node/vectors.json").read_text())["connectivity"]
    local = vectors["local"]
    assert public.ConnectivityLocalView.from_wire(local).to_wire() == local
    assert local["beacons_sent"] is None and local["beacons_heard"] == "0"
    page = vectors["sync_members_page"]
    assert public.ConnectivityMemberSyncSnapshot.from_wire(page).to_wire() == page
    final_page = {**page, "complete": True, "next_cursor": None}
    assert public.ConnectivityMemberSyncSnapshot.from_wire(final_page).to_wire() == final_page
