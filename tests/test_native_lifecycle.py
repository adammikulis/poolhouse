"""Common H state logic with test-only provenance; this is not native authentication proof."""
import testunits
from native_cargo import native_node_units, native_targets_built  # noqa: F401

NATIVE_TARGETS = (testunits.LIB,)


def test_native_lifecycle_core(native_targets_built):  # noqa: F811
    output = native_targets_built.start_built(testunits.LIB, ["lifecycle::tests::"])
    assert "test result: ok. 27 passed; 0 failed;" in output, output


def test_native_source_context_decoder(native_targets_built):  # noqa: F811
    output = native_targets_built.start_built(testunits.LIB, ["lifecycle::source_context_tests::"])
    assert "test result: ok. 10 passed; 0 failed;" in output, output
