"""`scripts/prose_to_code.py` lists agent guidance, says what enforces it only with evidence, and what changed."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "prose_to_code.py"
SKILL = ROOT / ".claude" / "skills" / "prose-to-code" / "SKILL.md"

RULES = """# Rules

- Never `frobnicate --everything` in this repository.
- Never push the `widget_release_branch` by hand.
- Never run `zzz_unheard_of_tool` here.
- Keep the outcome fixed.
- The server listens on a port and answers.
- Wrap a long rule over two
  lines and never lose its first line.

```
Never read this fenced line as a rule.
```

See [the landing steps](docs/landing.md).
"""
FILES = {
    "AGENTS.md": RULES,
    "docs/landing.md": "Always open a pull request.\n",
    "docs/unrelated.md": "Never read a document nothing links to.\n",
    ".claude/agents/worker.md": "---\nname: worker\ndescription: Never listed.\n---\n\nRun the affected tests.\n",
    "src/poolhouse/briefing.py": '"""Never listed: a docstring."""\n\nBRIEF = (\n    "Hello from the lead. "\n'
                                 '    "Do not push the development branch directly."\n)\n',
    "scripts/hooks/guard": '#!/usr/bin/env python3\nREFUSED = "frobnicate --everything is refused here"\n'
                           'BRANCH = "widget_release_branch"\n',
    "scripts/hooks/orphan": '#!/usr/bin/env python3\nORPHAN = "orphan rule text"\n',
    ".claude/settings.json": '{"hooks": "scripts/hooks/guard"}\n',
}


def tree(tmp_path: Path, files: dict[str, str] = FILES) -> Path:
    root = tmp_path / "tree"
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8")
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    return root


def run(root: Path, *words: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--root", str(root), "--state", str(root.parent / "state.json"), *words]
    done = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == code, done.stdout + done.stderr
    return done


def scan(root: Path, *words: str) -> dict:
    return json.loads(run(root, "--json", *words).stdout)


def by_text(found: dict, words: str) -> dict:
    [row] = [row for row in found["items"] if words in row["text"]]
    return row


def test_instructions_are_found_with_file_and_line_and_other_prose_is_not(tmp_path):
    found = scan(tree(tmp_path))
    where = {row["text"]: (row["path"], row["line"]) for row in found["items"]}
    assert where["Never `frobnicate --everything` in this repository."] == ("AGENTS.md", 3)
    assert where["Wrap a long rule over two lines and never lose its first line."] == ("AGENTS.md", 8)
    assert where["Always open a pull request."] == ("docs/landing.md", 1)
    assert where["Run the affected tests."] == (".claude/agents/worker.md", 6)
    assert where["Do not push the development branch directly."] == ("src/poolhouse/briefing.py", 5)
    everything = " ".join(where)
    for absent in ("listens on a port", "fenced line", "nothing links to", "Never listed", "Hello from the lead"):
        assert absent not in everything


def test_a_document_named_by_its_path_alone_is_read_like_a_linked_one(tmp_path):
    files = {**FILES, "docs/landing.md": 'Always open a pull request. Detail: docs/agent-extra.md, "Landing".\n',
             "docs/agent-extra.md": "Never leave a branch behind.\n"}
    assert by_text(scan(tree(tmp_path, files)), "branch behind")["path"] == "docs/agent-extra.md"


def test_a_status_is_claimed_only_with_something_to_show(tmp_path):
    root = tree(tmp_path)
    found = scan(root)
    named = by_text(found, "frobnicate")
    assert named["status"] == "unknown" and "scripts/hooks/guard:2" in " ".join(named["evidence"])
    absent = by_text(found, "zzz_unheard_of_tool")
    assert absent["status"] == "prose" and "zzz_unheard_of_tool" in absent["note"]
    silent = by_text(found, "Keep the outcome fixed")
    assert silent["status"] == "unknown" and silent["note"] == "names nothing to search for"
    assert not [row for row in found["items"] if row["status"] in ("enforced", "partly")]


def test_a_name_any_code_holds_is_never_called_prose_only(tmp_path):
    files = {**FILES, "app/node/src/quota.rs": "// zzz_unheard_of_tool is refused here\n"}
    row = by_text(scan(tree(tmp_path, files)), "zzz_unheard_of_tool")
    assert row["status"] == "unknown" and row["evidence"] == ["zzz_unheard_of_tool at app/node/src/quota.rs:1"]


def test_a_link_counts_only_while_its_evidence_holds_and_something_starts_it(tmp_path):
    root = tree(tmp_path)
    links = (
        '[[link]]\nmatch = "frobnicate"\nverdict = "enforced"\nevidence = ["scripts/hooks/guard::is refused here"]\n'
        '[[link]]\nmatch = "widget_release_branch"\nverdict = "partly"\nevidence = ["scripts/hooks/guard::gone text"]\n'
        '[[link]]\nmatch = "outcome fixed"\nverdict = "enforced"\nevidence = ["scripts/hooks/orphan::orphan rule"]\n'
        '[[link]]\nmatch = "a sentence nobody wrote"\nverdict = "enforced"\nevidence = ["AGENTS.md::Rules"]\n')
    (root / "scripts" / "prose_links.toml").write_text(links, encoding="utf-8")
    found = scan(root)
    held = by_text(found, "frobnicate")
    assert (held["status"], held["evidence"], held["open"]) == ("enforced", ["scripts/hooks/guard:2"], False)
    moved = by_text(found, "widget_release_branch")
    assert moved["status"] == "unknown" and "no longer holds 'gone text'" in moved["note"]
    idle = by_text(found, "Keep the outcome fixed")
    assert idle["status"] == "unknown" and "nothing starts scripts/hooks/orphan" in idle["note"]
    assert found["unmatched"] == ["link 'a sentence nobody wrote'"]
    assert "stale records" in run(root).stdout


def test_a_rerun_says_what_is_new_and_what_was_resolved_and_ids_survive_a_move(tmp_path):
    root = tree(tmp_path)
    first = scan(root)
    assert first["changes"] == {"first": True, "new": [], "resolved": []}
    kept = by_text(first, "frobnicate")["id"]
    gone = by_text(first, "zzz_unheard_of_tool")["id"]
    moved = "Preamble.\n\n" + RULES.replace("- Never run `zzz_unheard_of_tool` here.\n", "") + "\nNever add a second queue.\n"
    (root / "AGENTS.md").write_text(moved, encoding="utf-8")
    second = scan(root)
    assert by_text(second, "frobnicate")["id"] == kept
    assert second["changes"]["new"] == [by_text(second, "second queue")["id"]]
    assert [(r["id"], r["how"]) for r in second["changes"]["resolved"]] == [(gone, "sentence gone or reworded")]
    assert scan(root)["changes"] == {"first": False, "new": [], "resolved": []}


def test_a_path_limits_the_scan_and_leaves_the_rest_of_the_last_run(tmp_path):
    root = tree(tmp_path)
    scan(root)
    narrow = scan(root, "docs")
    assert {row["path"] for row in narrow["items"]} == {"docs/landing.md"}
    assert narrow["changes"]["resolved"] == []
    assert scan(root)["changes"] == {"first": False, "new": [], "resolved": []}
    unlinked = scan(root, "docs/unrelated.md", "--no-save")
    assert [row["path"] for row in unlinked["items"]] == ["docs/unrelated.md"]
    assert "no guidance found under: nowhere" in run(root, "nowhere", "--no-save").stdout


def test_a_sentence_this_run_did_not_read_is_not_called_resolved(tmp_path):
    root = tree(tmp_path)
    [asked] = scan(root, "docs/unrelated.md")["items"]
    assert scan(root)["changes"]["resolved"] == []
    (root / "docs" / "unrelated.md").unlink()
    assert [r["id"] for r in scan(root)["changes"]["resolved"]] == [asked["id"]]


def test_looking_without_saving_leaves_the_last_run(tmp_path):
    root = tree(tmp_path)
    run(root, "--no-save")
    assert not (tmp_path / "state.json").exists()


def test_a_link_and_a_decision_are_recorded_by_the_script(tmp_path):
    root = tree(tmp_path)
    found = scan(root)
    target = by_text(found, "frobnicate")["id"]
    other = by_text(found, "Keep the outcome fixed")["id"]
    refused = run(root, "--link", target, "--verdict", "enforced", "--evidence", "scripts/hooks/guard::not there", code=2)
    assert "no longer holds" in refused.stderr and not (root / "scripts" / "prose_links.toml").exists()
    run(root, "--link", target, "--verdict", "enforced", "--evidence", 'scripts/hooks/guard::"widget_release_branch"')
    run(root, "--decide", other, "--decision", "keep", "--note", 'a judgment, "not" a check')
    after = scan(root)
    assert by_text(after, "frobnicate")["status"] == "enforced"
    kept = by_text(after, "Keep the outcome fixed")
    assert (kept["decision"], kept["open"], kept["note"]) == ("keep", False, 'a judgment, "not" a check')
    assert {(r["id"], r["how"]) for r in after["changes"]["resolved"]} == {
        (target, "now enforced"), (other, "decided: keep")}
    run(root, "--decide", "p-0000000000", "--decision", "keep", "--note", "x", code=2)


def test_this_repository_is_scanned_and_its_recorded_links_are_read(tmp_path):
    found = json.loads(run_here(tmp_path, "AGENTS.md", "scripts/hooks").stdout)
    paths = {row["path"] for row in found["items"]}
    assert "AGENTS.md" in paths and any(path.startswith("scripts/hooks/") for path in paths)
    refusal = by_text(found, "unconditional refusal")
    assert refusal["status"] == "enforced" and refusal["evidence"][0].startswith("scripts/gates/refusal_stubs.py:")


def run_here(tmp_path: Path, *words: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--json", "--state", str(tmp_path / "state.json"), *words]
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    return done


def test_the_skill_runs_the_script_that_does_the_work():
    text = SKILL.read_text(encoding="utf-8")
    assert re.search(r"^name: prose-to-code$", text, re.M)
    assert "!`python3 scripts/prose_to_code.py $ARGUMENTS`" in text
    assert len(text.split("\n")) < 60
