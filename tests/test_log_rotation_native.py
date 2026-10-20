"""Signed log segments retain logical operations through the maintained native build."""
import testnode
from native_cargo import native_node_units  # noqa: F401 (fixture)


def test_native_log_rotation(native_node_units):  # noqa: F811
    native_node_units.run_targets(('log_rotation', 'log_rotation_review', 'log_rotation_node',
                                  'log_rotation_semantics'))


def test_native_log_replacement_failure(native_node_units):  # noqa: F811
    output = native_node_units.execute([testnode.tool('cargo'), 'test', '--locked', '--offline',
        '-p', 'poolhouse-node', '--lib', 'log::replacement_tests', '--', '--test-threads=1'])
    assert 'test result: ok. 2 passed; 0 failed;' in output
