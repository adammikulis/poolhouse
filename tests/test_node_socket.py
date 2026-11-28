"""The node's platform side: who may connect to its local socket, how soon it takes a client, and that a published
file gets its name once (without a hard link on Android, which lets no app make one)."""

import pytest
import testunits
from native_cargo import native_node_units, native_targets_built  # noqa: F401  (fixtures)

NATIVE_TARGETS = (testunits.LIB,)


@pytest.mark.timeout(120, func_only=True)
def test_the_socket_names_its_client_and_waits_for_one_instead_of_sleeping(native_targets_built):  # noqa: F811
    native_targets_built.run("socket")
