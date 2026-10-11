"""Every POOLHOUSE_* variable the docs name is read (or set) somewhere in the shipped source.

A documented switch that the code does not know silently does nothing; this caught CHANGELOG.md naming
``POOLHOUSE_UNMANAGED`` for the variable the code reads as ``POOLHOUSE_ADOPT_UNMANAGED``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "CHANGELOG.md", ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
SOURCE_DIRS = ["src", "scripts", "packaging", "app/src", "app/src-tauri/src", "app/poolhouse-node/src", "contracts", "tests/pool_check"]
"""`tests/pool_check` is the code of the `scripts/pool-check` command, which reads POOLHOUSE_POOL_CHECK."""
SOURCE_FILES = ["pyproject.toml"]
TEXT = {".py", ".rs", ".ts", ".tsx", ".js", ".sh", ".ps1", ".toml", ".json", ".yml", ".yaml", ".nsi", ".cmd", ".md", ""}
NAME = re.compile(r"\bPOOLHOUSE_[A-Z0-9_]*[A-Z0-9]\b")

EXTERNAL: dict[str, str] = {
    "POOLHOUSE_FROZEN_BINARY": "standalone daemon input for packaging conversation tests",
    "POOLHOUSE_FROZEN_CODING_BINARY": "standalone daemon input for packaging coding tests",
    "POOLHOUSE_ACCEPTANCE_DECIDE_CACHE": "installed decide cache input for native gym acceptance tests",
    "POOLHOUSE_ACCEPTANCE_DECISION_DEVICE": "accelerator selection for native gym acceptance tests",
    "POOLHOUSE_ACCEPTANCE_VISION": "opt-in native vision gym acceptance test",
    "POOLHOUSE_TEST_GGUF": "opt-in input of the real-model tests, read by tests/ only",
    "POOLHOUSE_TEST_SSHD": "opt-in switch of the localhost-sshd onboarding test, read by tests/ only",
    "POOLHOUSE_PUSH_MAIN": "documented as inert: the Bash guard no longer reads it; release-main approval replaces it",
    "POOLHOUSE_FLEET_TLS": "documented as removed: the pool has no TLS-off switch (docs/pool-encryption.md); tests/ sets it to prove it is ignored",
    "POOLHOUSE_NET": "opt-in switch of the live web smoke test, read by tests/ only",
    "POOLHOUSE_MANUAL_DIALOG":"opt-in switch of the manual notification-dialog test, read by tests/ only",
}
"""Documented variables that are intentionally not in the shipped source, each with the reason."""


def source_text() -> str:
    parts = []
    files = [ROOT / f for f in SOURCE_FILES]
    for d in SOURCE_DIRS:
        files += [p for p in (ROOT / d).rglob("*") if p.is_file() and p.suffix in TEXT and "node_modules" not in p.parts]
    for p in files:
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(parts)


def documented() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for doc in DOCS:
        for name in sorted(set(NAME.findall(doc.read_text(encoding="utf-8")))):
            found.setdefault(name, []).append(doc.relative_to(ROOT).as_posix())
    return found


def test_every_documented_poolhouse_variable_exists_in_the_source():
    source = source_text()
    known = set(NAME.findall(source))
    docs = documented()
    assert len(docs) > 20, "the scan found almost nothing; the pattern or the paths are wrong"
    assert len(known) > 20
    missing = {n: where for n, where in docs.items() if n not in known and n not in EXTERNAL}
    assert not missing, f"documented but never read or set in the source: {missing}"


def test_every_allow_listed_external_variable_has_a_reason_and_is_still_documented():
    docs = documented()
    for name, reason in EXTERNAL.items():
        assert reason.strip()
        assert name in docs, f"{name} is no longer documented: drop it from EXTERNAL"


def test_the_external_variables_are_really_read_by_a_test():
    tests = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("test_*.py")
                      if p.name != Path(__file__).name)
    for name in EXTERNAL:
        assert name in tests, f"{name} is read by no test: it is documented for nothing"


SWITCHES = ROOT / "docs" / "switches.md"
COLUMNS = {"A": 5, "B": 7, "C": 2, "D": 6}
"""The cells a row of each class's table has: A and B and D say who may set a switch and what replaced it or why it stays."""


def switch_rows() -> dict[str, list[list[str]]]:
    """The rows of docs/switches.md by class (its `## A`, `## B`, `## C`, `## D` tables), as lists of cells."""
    rows: dict[str, list[list[str]]] = {}
    where = ""
    for line in SWITCHES.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            where = line[3:4]
        elif where in COLUMNS and line.startswith("|") and not line.startswith(("| ---", "| Names", "| Switch")):
            rows.setdefault(where, []).append([cell.strip() for cell in line.strip().strip("|").split(" | ")])
    return rows


def test_every_variable_the_source_reads_has_a_row_in_the_switch_table():
    named = {name for table in switch_rows().values() for row in table for name in NAME.findall(row[0])}
    wanted = set(NAME.findall(source_text())) | set(EXTERNAL)
    assert not wanted - named, f"in no row of docs/switches.md: {sorted(wanted - named)}"


def test_each_class_table_has_the_cells_its_class_promises_and_none_is_empty():
    rows = switch_rows()
    assert set(rows) == set(COLUMNS)
    for kind, table in rows.items():
        for row in table:
            assert len(row) == COLUMNS[kind] and all(cell for cell in row), f"{kind}: {row[0][:60]}"


def test_the_counts_at_the_top_of_the_switch_table_are_the_rows():
    rows = switch_rows()
    counted = {kind: len({n for row in table for n in NAME.findall(row[0])}) + sum(not NAME.search(row[0]) for row in table)
               for kind, table in rows.items()}
    stated = dict(re.findall(r"\b([ABCD]) (\d+)\b", re.search(r"^Counts: (.*)$", SWITCHES.read_text(encoding="utf-8"), re.M)[1]))
    assert {kind: str(n) for kind, n in counted.items()} == stated


def test_a_name_is_in_one_class_only():
    seen: dict[str, str] = {}
    for kind, table in switch_rows().items():
        for row in table:
            for name in NAME.findall(row[0]):
                assert seen.setdefault(name, kind) == kind, f"{name} is in class {seen[name]} and {kind}"
