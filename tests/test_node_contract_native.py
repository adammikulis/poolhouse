"""Canonical native data encoding vectors."""
import json
import time

from native_cargo import native_node_units, native_targets_built  # noqa: F401

NATIVE_TARGETS = ("contract", "lib")


def test_native_contract_vectors(native_targets_built):  # noqa: F811
    native_targets_built.run_targets(("contract",))


def test_private_contract_vectors_stay_inside_the_node_crate(native_targets_built):  # noqa: F811
    output = native_targets_built.start_built("lib", ["contract_private_tests::"])
    assert "4 passed; 0 failed" in output, output


def test_rust_consumer_can_use_public_contract_but_not_private_data(native_targets_built, tmp_path):  # noqa: F811
    from pathlib import Path

    from native_cargo import RUN_S, testnode, testunits

    node = native_targets_built
    dependencies = node.target / "debug/deps"
    # Dep-info paths are workspace-relative; Cargo's receipt identifies the exact library.
    output = node.build_output(testunits.build_argv(testnode.tool("cargo"), ["contract"]),
                               node.staged, node.environment)
    artifacts = [json.loads(line) for line in output.splitlines()]
    libraries = [Path(filename) for row in artifacts if row.get("reason") == "compiler-artifact"
                 and (row.get("target") or {}).get("src_path") == str(node.staged / "poolhouse-node/src/lib.rs")
                 for filename in row.get("filenames", []) if filename.endswith(".rlib")]
    assert len(libraries) == 1, libraries
    library = libraries[0]
    source = tmp_path / "consumer.rs"
    command = [testnode.tool("rustc"), "--edition=2021", "--crate-type=lib", "--emit=metadata",
               "--error-format=json", "--out-dir", str(tmp_path), "--extern", f"poolhouse_node={library}",
               "-L", f"dependency={dependencies}", str(source)]
    source.write_text("pub fn accepts(_: poolhouse_node::contract::DeviceId) {}\n")
    code, _, errors = node.launch(command, tmp_path, node.environment, time.monotonic() + RUN_S)
    assert code == 0, errors
    private = ["SessionLease", "StateStoreBinding", "ProcessObservation", "AuditToken",
               "WorkerNodeFrame", "WorkerDecisionOptions", "WorkerNullableInput", "StudioWorkerOutputReservation"]
    source.write_text("use poolhouse_node::contract::{" + ",".join(private) + "};\n")
    code, _, errors = node.launch(command, tmp_path, node.environment, time.monotonic() + RUN_S)
    diagnostics = [json.loads(line) for line in errors.splitlines()]
    refused = [row["message"] for row in diagnostics if (row.get("code") or {}).get("code") == "E0603"]
    assert code != 0 and all(any(f"`{name}`" in message for message in refused) for name in private), errors
