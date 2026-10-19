"""The board tally names each writer by what its rows say and counts rows per day."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import board_tally as tally  # noqa: E402

DAY = tally.DAY_MS


def status(body: str, subject: str = "") -> dict:
    return {"type": "status", "body": body, "subject": subject}


def test_a_row_is_named_by_what_it_records():
    assert tally.label("message", status("test-run key=ab12 file=tests/test_a.py state=running agent=x")).startswith("message status test-run")
    assert tally.label("message", status("test-result key=ab12 outcome=pass")).startswith("message status test-result")
    assert tally.label("message", status("test-job job=j tier=all owner=x state=running")).endswith("test-job start")
    assert tally.label("message", status("test-job job=j tier=all outcome=pass")).endswith("test-job end")
    assert tally.label("message", status("done: finished on 0.3dev", "done")).startswith("message status done")
    assert tally.label("lease", {"action": "acquire", "resources": ["claim:install:/a/b"]}) == "lease install acquire"
    assert tally.label("lease", {"action": "expire", "resources": []}) == "lease ? expire"
    assert tally.label("landing", {"ev": "beat"}) == "landing beat"
    assert tally.label("identity", {"retired": True}) == "identity retired"


def test_announcements_with_different_numbers_and_commits_count_as_one_writer():
    one = tally.label("message", {"type": "milestone", "body": "tree land/12-3-4 unlanded commits 8h old"})
    other = tally.label("message", {"type": "milestone", "body": "tree land/99-1-2 unlanded commits 2h old"})
    assert one == other == "message milestone: tree"
    assert tally.label("message", {"type": "milestone", "body": "runtime ensure 5b730b1 (was 067449c)"}) == "message milestone: runtime"


def test_an_announcement_an_agent_wrote_is_free_text():
    assert tally.label("message", {"type": "joined", "body": "reviewing the tally"}) == "message joined: free text"
    assert tally.label("message", {"type": "joined", "body": "branch-worker"}) == "message joined: branch-worker"


def test_the_tally_counts_rows_by_writer_and_sender_family_within_the_days_asked():
    rows = [("message", "claude-aaaaaa", status("test-run key=1"), 10 * DAY + n) for n in range(3)]
    rows += [("message", "agent-bbbbbb", status("test-run key=2"), 11 * DAY)]
    rows += [("lease", "chatgpt-cccccc", {"action": "acquire", "resources": ["claim:area:/x"]}, 1 * DAY)]
    counts, writers, first, last = tally.tally(rows)
    assert counts["lease area acquire"] == 1 and sum(counts.values()) == 5
    run = next(name for name in counts if name.startswith("message status test-run"))
    assert counts[run] == 4 and writers[run] == {"claude": 3, "agent": 1}
    counts, _, first, last = tally.tally(rows, since_ms=5 * DAY)
    assert sum(counts.values()) == 4 and first == 10 * DAY and last == 11 * DAY


def test_the_report_lists_the_largest_writer_first_with_rows_per_day():
    rows = [("message", "claude-aaaaaa", status("test-run key=1"), n * DAY // 2) for n in range(5)]
    rows += [("audit", "node", {"event": "retire"}, 0)]
    lines = tally.report(*tally.tally(rows))
    assert lines[0].startswith("6 rows over 2.0 days, 3 a day")
    assert "test-run" in lines[1] and "claude 5" in lines[1] and "audit retire" in lines[2]
