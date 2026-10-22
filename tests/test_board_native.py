"""Native board and lease regressions through isolated compiler admission."""
from native_cargo import native_node_units  # noqa: F401 (fixture)


def test_native_board_and_lease_targets(native_node_units):  # noqa: F811
    native_node_units.run_targets(('board_rows', 'sessions', 'session_profile', 'idle', 'node_attacks', 'lease',
                                  'lease_parties', 'claims', 'claims_foreign', 'landing'))


def test_native_worktree_evidence(native_node_units):  # noqa: F811
    native_node_units.run_targets(('worktree_evidence',))
