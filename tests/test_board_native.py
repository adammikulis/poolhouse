"""Native board and lease regressions through isolated compiler admission."""
import pytest
from native_cargo import native_node_units, native_targets_built  # noqa: F401 (fixtures)

BOARD = ('board_rows', 'sessions', 'session_profile', 'idle', 'node_attacks', 'lease', 'lease_parties', 'claims', 'claims_foreign', 'landing')
NATIVE_TARGETS = (*BOARD, 'worktree_evidence')


@pytest.mark.timeout(120, func_only=True)
def test_native_board_and_lease_targets(native_targets_built):  # noqa: F811
    native_targets_built.run_targets(BOARD)


@pytest.mark.timeout(120, func_only=True)
def test_native_worktree_evidence(native_targets_built):  # noqa: F811
    native_targets_built.run_targets(('worktree_evidence',))
