"""The documents of what runs now do not name the files the node replaced."""

from __future__ import annotations

import re

import pytest
from conftest import REPO

RETIRED = {
    r"\bclaims\.json\b": "claims are leases in the node's leases.json",
    r"(?<![-\w])audit\.jsonl\b": "the audit trail is #audit entries in a board's log (authority-audit.jsonl is the authority registry's own)",
}
DESIGN_RECORDS = {"board-replication.md"}  # says these files are gone


@pytest.mark.parametrize("pattern", RETIRED)
def test_no_document_names_a_file_the_node_replaced(pattern):
    found = [f"{path.name}:{number}" for path in sorted((REPO / "docs").glob("*.md")) if path.name not in DESIGN_RECORDS
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if re.search(pattern, line)]
    assert found == [], f"{RETIRED[pattern]}: {found}"


@pytest.mark.parametrize("name", ["HANDOFF.md", "docs/release-readiness.md"])
def test_the_release_documents_name_only_documents_that_exist(name):
    lines = (REPO / name).read_text(encoding="utf-8").splitlines()
    missing = [f"{name}:{number}: {path}" for number, line in enumerate(lines, 1) if "does not exist" not in line
               for path in re.findall(r"`(docs/[\w-]+\.md)`", line) if not (REPO / path).exists()]
    assert missing == []
