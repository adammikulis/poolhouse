"""A daemon, its page and the members it reaches share one node `Pool` that is handed down, never rebuilt by whoever needs it.

Two daemons in one process (a test of two devices) each read their own node only if nothing between them asks for "the" node.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.fleet import studio_pool
from poolhouse.fleet.service_pool import Pool
from poolhouse.fleet.studio_service import scope

FLEET = Path(__file__).resolve().parents[1] / "src" / "poolhouse" / "fleet"

BUILDS_THE_DEFAULT = {"service_pool.py"}
"""The module that builds a `Pool` nobody handed in."""

ASKS_FOR_THE_DEFAULT = {
    "api.py": "the request handler of a daemon given no pool",
    "daemon.py": "the daemon builds its pool once and hands it to its page and handler",
    "remote.py": "a `Member` or a discovery given no pool, as a command line tool is",
    "roster.py": "the command line listing of the pool's devices",
    "ui.py": "a page given no pool",
    "onboard/share_cli.py": "the share command",
}
"""The modules that may ask `service_pool.default()` for it, and why."""


def calls(path: Path):
    """Each call in ``path`` as the name it is made by: ``Pool`` for ``Pool()`` and ``service_pool.Pool()``, ``default`` for ``service_pool.default()``."""
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                yield ("default" if function.id == "default_pool" else function.id), node.lineno
            elif isinstance(function, ast.Attribute):
                module = function.value.id if isinstance(function.value, ast.Name) else ""
                if (module, function.attr) == ("service_pool", "default"):
                    yield "default", node.lineno
                elif function.attr == "Pool":
                    yield "Pool", node.lineno


def named(wanted: str):
    return sorted((str(path.relative_to(FLEET)), line) for path in FLEET.rglob("*.py")
                  for name, line in calls(path) if name == wanted)


def test_only_the_service_pool_module_builds_a_pool():
    built = [(where, line) for where, line in named("Pool") if where not in BUILDS_THE_DEFAULT]
    assert built == [], f"these build a Pool of their own instead of taking the one they were given: {built}"


def test_only_the_named_entry_points_ask_for_the_default_pool():
    asked = {where for where, _ in named("default") if where != "service_pool.py"}
    assert asked <= set(ASKS_FOR_THE_DEFAULT), f"ask for the default pool only from {sorted(ASKS_FOR_THE_DEFAULT)}: {sorted(asked - set(ASKS_FOR_THE_DEFAULT))}"
    assert set(ASKS_FOR_THE_DEFAULT) <= asked, f"no longer asks for the default pool: {sorted(set(ASKS_FOR_THE_DEFAULT) - asked)}"


def test_a_scope_whose_pool_identity_is_in_hand_asks_no_node_and_one_without_it_asks_the_page_pool(tmp_path):
    """Each node read costs a round trip and a connection makes thousands: the scope takes the pool identity a caller already read."""
    ui = SimpleNamespace(root=tmp_path, pool=Pool(tmp_path / "no-node"))  # a state directory with no node in it
    group = studio_pool.scope_of("a" * 32)
    assert scope(ui, group) == scope(ui, group)
    with pytest.raises(PermissionError):
        scope(ui)
