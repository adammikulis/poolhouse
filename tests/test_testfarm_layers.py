"""Check A, the part that exists: the layers of poolhouse.testfarm import only what each layer may, in one direction."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from test_layers import REPO, ROOT, _loaded, imported

from poolhouse import home
from poolhouse.testfarm import cli
from poolhouse.testfarm.core import state
from poolhouse.testfarm.model import ConfigError

FARM = ROOT / "testfarm"
L0 = ("files", "lock", "platform", "redact", "sandbox", "errors", "log")
"""The modules of poolhouse the runner's core may import, and no others."""
LEGACY = frozenset({"client.py", "consent.py", "dev.py", "devices.py", "farm.py", "ledger.py", "report.py"})
"""The files of the carriers and the farm that the generalised path replaces; they hold the old imports until they are deleted."""
NEVER = ("scripts", "tests", "poolhouse.board", "poolhouse.workspace", "poolhouse.home", "poolhouse.serve", "poolhouse.fleet")
PLUGINS = {"presets": (), "transports": (), "resources": ("poolhouse.leases", "poolhouse.serve.grant")}
"""The Poolhouse modules each kind of plugin may import besides the runner's core and model."""


def outside(path: Path, stdlib_and: tuple[str, ...]) -> set[str]:
    """The modules ``path`` imports that are neither the standard library nor under one of ``stdlib_and``."""
    return {name for name in imported(path) if name.split(".")[0] not in sys.stdlib_module_names
            and not any(name == allowed or name.startswith(allowed + ".") for allowed in stdlib_and)}


def modules(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def plugin_violations(package: Path, extra: tuple[str, ...]) -> dict[str, set[str]]:
    """For each module of a plugin directory, what it imports besides L2, L1, the standard library and ``extra``."""
    allowed = ("poolhouse.testfarm.core", "poolhouse.testfarm.model", *extra)
    return {str(path.relative_to(package)): found for path in modules(package) if (found := outside(path, allowed))}


def test_the_core_imports_the_model_the_listed_core_modules_and_nothing_else():
    allowed = ("poolhouse.testfarm.core", "poolhouse.testfarm.model", *(f"poolhouse.{name}" for name in L0))
    for path in modules(FARM / "core"):
        assert not (found := outside(path, allowed)), f"{path.relative_to(REPO)} imports {sorted(found)}"
        assert "poolhouse.testfarm.presets" not in imported(path) and not any(
            name.startswith(("poolhouse.testfarm.transports", "poolhouse.testfarm.resources")) for name in imported(path))


def test_a_plugin_imports_only_the_modules_the_test_names(tmp_path):
    for kind, extra in PLUGINS.items():
        if (FARM / kind).is_dir():
            assert plugin_violations(FARM / kind, extra) == {}
    plugin = tmp_path / "presets"
    plugin.mkdir()
    (plugin / "good.py").write_text("import json\nfrom poolhouse.testfarm.core import select\nfrom poolhouse.testfarm.model import Unit\n")
    (plugin / "bad.py").write_text("import poolhouse.fleet\nfrom poolhouse.testfarm.transports import pool\nfrom poolhouse import board\nimport numpy\n")
    (plugin / "lease.py").write_text("from poolhouse import leases\nimport poolhouse.serve.grant\n")
    assert plugin_violations(plugin, PLUGINS["presets"]) == {
        "bad.py": {"poolhouse.fleet", "poolhouse.testfarm.transports", "poolhouse.testfarm.transports.pool", "poolhouse.board", "numpy"},
        "lease.py": {"poolhouse.leases", "poolhouse.serve.grant"}}
    assert set(plugin_violations(plugin, PLUGINS["resources"])) == {"bad.py"}


def test_nothing_under_testfarm_outside_the_files_a_later_step_replaces_reaches_the_repository_or_the_pools_state():
    for path in modules(FARM):
        if path.name in LEGACY and path.parent == FARM:
            continue
        reached = {name for name in imported(path) if name.split(".")[0] in ("scripts", "tests") or name in NEVER
                   or any(name.startswith(prefix + ".") for prefix in NEVER if prefix.startswith("poolhouse."))}
        allowed_here = {"poolhouse.home"} if path == FARM / "cli.py" else set()
        assert not reached - allowed_here, f"{path.relative_to(REPO)} imports {sorted(reached)}"
    assert {path.name for path in FARM.glob("*.py")} >= LEGACY, "a legacy file was deleted: remove it from LEGACY"


def test_only_one_module_starts_a_command_of_a_project():
    starters = re.compile(r"\b(subprocess|Popen|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|pty)\b|sandbox\.run\(")
    for path in modules(FARM):
        if (path.parent == FARM and path.name in LEGACY) or path == FARM / "core" / "spawn.py":
            continue
        assert not starters.search(path.read_text(encoding="utf-8")), f"{path.relative_to(REPO)} starts a process; core/spawn.py is the one place"
    assert starters.search("subprocess.run(['sh'])") and starters.search("sandbox.run(argv, policy)") and not starters.search("an empty list")


def test_importing_the_runner_loads_no_fleet_board_numpy_or_pytest():
    for module in ("poolhouse.testfarm", "poolhouse.testfarm.model", "poolhouse.testfarm.api", "poolhouse.testfarm.cli"):
        loaded = _loaded(module)
        heavy = {name for name in loaded if name.startswith(("poolhouse.fleet", "poolhouse.board", "poolhouse.workspace"))}
        assert not heavy, f"{module} loads {sorted(heavy)}"
    code = "import sys, poolhouse.testfarm.cli; print(sorted(m for m in sys.modules if m.split('.')[0] in ('numpy', 'pytest')))"
    import subprocess
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    assert done.returncode == 0 and done.stdout.strip() == "[]", done.stdout + done.stderr


def test_the_state_path_is_not_under_the_pool_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("POOLHOUSE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(sys, "platform", platform)
        found = state.state_dir(home.user_home())
        assert found.name == "poolhouse-testfarm" and found.is_relative_to(tmp_path)
        for pool in (home.state(), home.account_roots()[0], tmp_path / ".poolhouse"):
            assert not found.is_relative_to(pool) and not pool.is_relative_to(found), (found, pool)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert state.state_dir(home.user_home()) == tmp_path / "xdg" / "poolhouse-testfarm"


def test_a_pool_home_that_contains_the_state_path_is_refused_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "Library"))
    with pytest.raises(ConfigError, match=r"under the pool's home"):
        cli.state_location()
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / ".poolhouse"))
    assert cli.state_location() == tmp_path / "Library" / "Application Support" / "poolhouse-testfarm"
