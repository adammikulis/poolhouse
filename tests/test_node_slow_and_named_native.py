"""The node crate's tests of slow members and typed names (`app/poolhouse-node/tests/slow_and_named.rs`), run through the runner, with the
library's own unit tests (the periodic sync in `net`, the lines an approval shows in `approvals::content` and the rest)."""

import pytest
import testunits
from native_cargo import native_node_units, native_targets_built  # noqa: F401

NATIVE_TARGETS = (testunits.LIB,)


@pytest.mark.timeout(300, func_only=True)
@pytest.mark.parametrize("target", ["slow_and_named"])
def test_a_slow_member_holds_up_no_other_and_a_typed_name_is_shown_clean(target, native_targets_built):  # noqa: F811
    native_targets_built.run_targets((target,))


@pytest.mark.slow
@pytest.mark.timeout(600, func_only=True)
def test_the_node_librarys_own_unit_tests(native_targets_built):  # noqa: F811
    native_targets_built.run_targets(NATIVE_TARGETS)
