"""The commands page's table is made from the registry, so no command can be missing from it."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

from poolhouse.cli import PYPROJECT, commands
from poolhouse.cli.reference import HELP, TABLE, Row
from poolhouse.cli.registry import rows, table

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "docs" / "commands.md"


def committed() -> list[str]:
    """The table as docs/commands.md carries it, under "# The commands"."""
    lines = PAGE.read_text(encoding="utf-8").splitlines()
    start = lines.index("# The commands")
    first = next(i for i in range(start, len(lines)) if lines[i].startswith("| "))
    last = next(i for i in range(first, len(lines)) if not lines[i].startswith("| "))
    return lines[first:last]


def test_the_committed_table_is_the_one_the_registry_makes():
    assert committed() == table(PYPROJECT).splitlines(), (
        "docs/commands.md's table and poolhouse.cli.registry disagree; "
        "scripts/reference --write writes the registry's")


def test_every_command_has_a_line_and_every_line_a_command():
    installed = set(commands())
    assert set(HELP) == installed, (
        f"in the registry and not installed: {sorted(set(HELP) - installed)}; "
        f"installed and not in the registry: {sorted(installed - set(HELP))}")


def test_every_row_names_a_command_that_exists():
    declared = set(commands())
    for row in TABLE:
        assert row.word == "" or row.word in declared, f"{row.invocation!r} names no command"


def test_a_row_is_about_the_word_either_spelling_names():
    assert Row("poolhouse-serve fit", "x").word == "serve"
    assert Row("poolhouse serve fit", "x").word == "serve"
    assert Row("poolhouse device-setup [--yes]", "x").word == "device-setup"
    assert Row("poolhouse runtime ensure\\|status", "x").word == "runtime"
    assert Row("poolhouse <command>", "x").word == ""


def test_every_registered_command_is_in_the_table_even_with_no_prose_of_its_own(monkeypatch):
    monkeypatch.setattr(importlib.import_module("poolhouse.cli.registry"), "TABLE", ())
    made = {row.word for row in rows(PYPROJECT)}
    assert set(commands()) <= made, f"missing from the generated table: {sorted(set(commands()) - made)}"
    assert "| `poolhouse traind` |" in table(PYPROJECT)


def test_a_command_without_prose_gets_a_row_from_its_registry_summary(monkeypatch):
    module = importlib.import_module("poolhouse.cli.registry")
    declared = module.registry(REPO / "pyproject.toml")
    declared["quince"] = module.Command("quince:main", "Counts quinces.")
    monkeypatch.setattr(module, "registry", lambda _pyproject: declared)
    assert Row("poolhouse quince", "Counts quinces.") in rows(PYPROJECT)


def test_the_pool_row_names_listen_and_the_developer_commands_are_written_through_the_front_door():
    invocations = [row.invocation for row in TABLE]
    assert any(i.startswith("poolhouse-pool ") and "listen" in i for i in invocations)
    for word in ("test", "test-devices", "open", "help", "device", "device-setup", "features",
                 "credentials", "local-network"):
        assert not any(i.split()[0] == f"poolhouse-{word}" for i in invocations), word
        assert any(i.split()[:2] == ["poolhouse", word] for i in invocations), word


def test_the_script_prints_what_the_page_holds():
    ran = subprocess.run([sys.executable, str(REPO / "scripts" / "reference")],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    assert ran.stdout.splitlines() == committed()


def test_reference_check_rejects_a_stale_table_without_writing(tmp_path):
    page = tmp_path / "commands.md"
    original = PAGE.read_text(encoding="utf-8")
    page.write_text(original, encoding="utf-8")
    command = [sys.executable, str(REPO / "scripts/reference"), "--check", "--page", str(page)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    stale = original.replace("| `poolhouse-models", "| `stale-models", 1)
    assert stale != original
    page.write_text(stale, encoding="utf-8")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1 and "stale" in result.stderr
    assert page.read_text(encoding="utf-8") == stale
