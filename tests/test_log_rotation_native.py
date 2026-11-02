"""Signed log segments retain logical operations through the maintained native build."""
import pytest
import testunits
from native_cargo import native_node_units, native_targets_built  # noqa: F401 (fixtures)

ROTATION = ('log_rotation', 'log_rotation_review', 'log_rotation_node', 'log_rotation_semantics')
NATIVE_TARGETS = (*ROTATION, testunits.LIB)


@pytest.mark.timeout(120, func_only=True)
def test_native_log_rotation(native_targets_built):  # noqa: F811
    native_targets_built.run_targets(ROTATION)


@pytest.mark.timeout(120, func_only=True)
def test_native_log_replacement_failure(native_targets_built):  # noqa: F811
    output = native_targets_built.run('replacement')
    assert 'test result: ok. 2 passed; 0 failed;' in output
