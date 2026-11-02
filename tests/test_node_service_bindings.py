"""Canonical node authorization for subordinate compute certificates."""

import testunits
from native_cargo import native_node_units, native_targets_built  # noqa: F401

NATIVE_TARGETS = (testunits.LIB, "service_bindings")


def test_service_binding_signatures_membership_and_generation(native_targets_built):  # noqa: F811
    output = native_targets_built.start_built(testunits.LIB, ["services::tests::"])
    assert "8 passed; 0 failed" in output


def test_service_bindings_over_real_node_tls(native_targets_built):  # noqa: F811
    output = native_targets_built.start_built("service_bindings")
    assert "3 passed; 0 failed" in output
