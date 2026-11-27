"""Every package and top-level module under src/poolhouse has a caller, a command, or a reason."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest
from test_layers import EXPERIMENTAL

from poolhouse import cli

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "poolhouse"

STANDALONE = {
    "core": "the public library for a program outside this repository that serves a model with no pool (docs/core.md); the broker shares its "
            "pieces in poolhouse.serve and never imports it, which would close the core/serve import cycle",
    "pi_tools": "the tool-proxy model's per-tool worker, kept until the native path is proven (docs/design-isolated-launcher.md, 15.2 and 18.10); "
                "Pi's launcher no longer calls it, since Pi's own tools run inside the confinement",
    "protectedwrites":"loaded by file through poolhouse.worktreerules, so the hook scripts need no package on the path",
}


def _pieces() -> list[str]:
    names = []
    for path in sorted(SRC.iterdir()):
        if path.name.startswith((".", "_")):
            continue
        if path.is_dir() and any(path.glob("*.py")):
            names.append(path.name)
        elif path.suffix == ".py":
            names.append(path.stem)
    return names


def _segment(path: Path) -> str:
    parts = path.relative_to(SRC).parts
    return parts[0] if len(parts) > 1 else path.stem


def _imported_by(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    package = ["poolhouse", *path.relative_to(SRC).parts[:-1]]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "poolhouse" and len(parts) > 1:
                    out.add(parts[1])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                parts = base + (node.module.split(".") if node.module else [])
            else:
                parts = (node.module or "").split(".")
            if parts[:1] != ["poolhouse"]:
                continue
            if len(parts) > 1:
                out.add(parts[1])
            else:
                out.update(alias.name for alias in node.names)
    return out


def _callers() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        mine = _segment(path)
        for name in _imported_by(path):
            if name != mine:
                found.setdefault(name, set()).add(path.relative_to(REPO).as_posix())
    return found


def _console_targets() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data.get("project", {}).get("scripts", {}).values())


def _commands() -> set[str]:
    out = set()
    for target in _console_targets():
        parts = target.split(":")[0].split(".")
        if parts[:1] == ["poolhouse"] and len(parts) > 1:
            out.add(parts[1])
    return out


def _lazy_commands() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for target in set(cli.commands().values()) - _console_targets():
        parts = target.partition(":")[0].split(".")
        if parts[:1] == ["poolhouse"] and len(parts) > 1:
            out.setdefault(parts[1], set()).add(target)
    return out


CALLERS = _callers()
COMMANDS = _commands()


@pytest.mark.parametrize("name", _pieces())
def test_piece_is_wired_in(name: str) -> None:
    if name in EXPERIMENTAL:
        used = {path for path in CALLERS.get(name, ()) if path.split("/")[2].removesuffix(".py") in EXPERIMENTAL}
        assert (SRC / name / "__main__.py").is_file() or used, \
            f"poolhouse.{name} is experimental: it runs as python -m poolhouse.{name} or another experimental package imports it"
        return
    if name in CALLERS or name in COMMANDS or name in STANDALONE:
        return
    targets = _lazy_commands().get(name, set())
    if targets:
        for target in targets:
            assert callable(cli.load(target)), f"{target} is not callable"
        return
    pytest.fail(
        f"poolhouse.{name} is imported by nothing in src/poolhouse, backs no console script, "
        f"and is not in STANDALONE. Wire it in, give it a command, or name it in STANDALONE "
        f"in tests/test_wiring.py with a one-line reason."
    )


def test_standalone_names_only_real_pieces() -> None:
    stale = sorted(set(STANDALONE) - set(_pieces()))
    assert not stale, f"STANDALONE names what is not there: {', '.join(stale)}"


@pytest.mark.parametrize("target", ("poolhouse.runtime_cli:missing_handler", "poolhouse.runtime_cli:SETTLE"))
def test_a_lazy_registry_target_must_resolve_to_a_callable(monkeypatch, target):
    monkeypatch.setattr(cli, "commands", lambda: {"runtime": target})
    with pytest.raises((AttributeError, AssertionError)):
        test_piece_is_wired_in("runtime_cli")


def test_an_undeclared_command_does_not_supply_wiring(monkeypatch):
    monkeypatch.setattr(cli, "commands", lambda: {})
    with pytest.raises(pytest.fail.Exception, match="runtime_cli"):
        test_piece_is_wired_in("runtime_cli")


def test_an_unrelated_target_string_is_not_a_caller(tmp_path, monkeypatch):
    source = tmp_path / "unused.py"
    source.write_text('TARGET = "poolhouse.runtime_cli:main"\n')
    monkeypatch.setitem(globals(), "SRC", tmp_path)
    assert _imported_by(source) == set()
    assert _callers() == {}


@pytest.mark.parametrize(("name", "caller"), [("coding_workspace", "src/poolhouse/isolation/workspace.py"), ("isolation", "src/poolhouse/claude.py"),
                                                ("isolation", "src/poolhouse/codex.py"), ("isolation", "src/poolhouse/pi.py"),
                                                ("isolation", "src/poolhouse/harnessing.py")])
def test_the_isolated_launcher_and_the_stage_it_builds_have_their_callers_outside_the_tests(name: str, caller: str) -> None:
    """Neither is standalone any more: the launchers reach the library through `isolation.prepare`, and the library reaches the stage."""
    assert name not in STANDALONE and caller in CALLERS.get(name, set())
