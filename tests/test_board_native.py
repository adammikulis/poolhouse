"""Native board and lease regressions through isolated compiler admission."""
pytest_plugins = ('native_cargo',)


def test_native_board_and_lease_targets(native_node_units):
    native_node_units.run_targets(('board_rows', 'sessions', 'idle', 'node_attacks', 'lease',
                                  'lease_parties', 'claims', 'claims_foreign', 'landing'))


def test_native_worktree_evidence(native_node_units):
    native_node_units.run_targets(('worktree_evidence',))
