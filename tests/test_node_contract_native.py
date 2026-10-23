"""Canonical native data encoding vectors."""
from native_cargo import native_node_units, native_targets_built  # noqa: F401

NATIVE_TARGETS = ("contract",)


def test_native_contract_vectors(native_targets_built):  # noqa: F811
    native_targets_built.run_targets(NATIVE_TARGETS)
