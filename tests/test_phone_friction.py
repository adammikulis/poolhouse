"""The friction budget of the phone: how many things a person does, first run and routine, never grows without a reason.

docs/phone-approval.md ("The friction budget") lists each step a person sees; contracts/phone-friction.json holds the counts the
app's `ApprovalFlow` checks; this file holds the ceiling. A step added anywhere fails here until the ceiling is raised on purpose,
in the same change that says why in the document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "phone-approval.md").read_text(encoding="utf-8")
CONTRACT = json.loads((ROOT / "contracts" / "phone-friction.json").read_text(encoding="utf-8"))
CEILING = {"first_run": 3, "approve_routine": 2, "approve_sensitive": 3}
"""What a person does: a command, a scan and a fingerprint the first time; a tap and a fingerprint for a routine request; one more tap, to read, for the rest."""
SECTIONS = {"first_run": "**First run, on a computer and a phone (the first phone).**", "approve_routine": "**A routine approval (one request, one or many asks).**",
            "approve_sensitive": "**A sensitive or irreversible approval.**"}


def steps(heading: str) -> list[str]:
    """The numbered rows of the table that follows ``heading`` in the document."""
    after = DOC.split(heading, 1)[1]
    rows = []
    for line in after.split("\n")[1:]:
        if line.startswith("|"):
            if re.match(r"\|\s*\d+\s*\|", line):
                rows.append(line)
        elif rows:
            break
    return rows


def test_the_document_lists_exactly_the_steps_the_contract_counts_and_no_more_than_the_ceiling():
    assert set(CONTRACT) - {"about"} == set(CEILING)
    for name, heading in SECTIONS.items():
        listed = steps(heading)
        assert len(listed) == CONTRACT[name], f"{name}: the document lists {len(listed)} steps and the contract counts {CONTRACT[name]}"
        assert CONTRACT[name] <= CEILING[name], f"{name} grew to {CONTRACT[name]}: the ceiling is {CEILING[name]}"


def reasons(heading: str) -> list[str]:
    """The last cell of each step of the table under ``heading``: why the step is needed, empty when it says nothing."""
    return [row.strip().strip("|").split("|")[-1].strip() for row in steps(heading)]


def test_every_first_run_step_and_the_one_extra_tap_of_a_sensitive_request_say_why():
    assert all(reasons(SECTIONS["first_run"])), "every first-run step says why it is needed"
    extra = steps(SECTIONS["approve_sensitive"])[0]
    assert "whole text" in extra and reasons(SECTIONS["approve_sensitive"])[0], "the extra tap has its reason in the table"


def test_one_fingerprint_is_asked_for_a_decision_and_the_notification_is_the_only_step_it_needs():
    for name in ("approve_routine", "approve_sensitive"):
        rows = [r.lower() for r in steps(SECTIONS[name])]
        assert sum("fingerprint" in r for r in rows) == 1, f"{name}: one biometric prompt per approval"
        assert not any("type" in r.split("|")[2] for r in rows), "nothing is typed to approve"
