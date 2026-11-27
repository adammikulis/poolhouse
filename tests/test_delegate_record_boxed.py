"""The record and the notices of a delegate that runs in the real confinement.

PLUMBING ONLY: no model is served and no OpenCode runs. The confinement, its preflight, both bridges, the board and git are real; the marked program
of tests/test_delegate_review.py (NOT A MODEL and NOT OPENCODE) stands in the harness's place and the model side is the kit's
`CANNED-NOT-A-MODEL` server.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import test_delegate_review as review

from poolhouse import delegate, delegate_record

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]
checked, ran = review.checked, review.ran


def events(name: str, kind: str) -> list[dict]:
    found, after, more = [], 0, True
    while more:
        page, after, more = delegate_record.read(name, after)
        found += page
    return [each for each in found if each["kind"] == kind]


def told(world, name: str) -> list[tuple[str, str]]:
    entries = world.node.session(world.lead).read(limit=100).entries
    return [(e.fields.get("subject", ""), e.text) for e in entries if e.sender == name and e.fields.get("subject", "").startswith("delegate")]


def test_a_run_broken_off_in_the_box_is_recorded_attempt_by_attempt_and_the_lead_is_told_of_the_break_and_the_restart(checked, capsys):
    checked.plan[:] = ["breaks", "up"]
    run = ran(checked, "break")
    assert run.status == 0 and [each for each in run.passes if each[0] == "work"] == [("work", 0, 0), ("work", 0, 1)], run.row
    attempts = [(each["phase"], each["attempt"]) for each in events(run.name, "attempt")]
    assert attempts[:2] == [("work", 0), ("work", 1)], attempts
    first = events(run.name, "attempt_end")[0]
    assert "the model server went away" in first["interrupted"] and first["why"], first
    assert any("interrupted:" in each["detail"] and each["state"] == "loading" for each in events(run.name, "state"))
    assert events(run.name, "preflight") and events(run.name, "model") and events(run.name, "call")
    subjects = [subject for subject, _text in told(checked, run.name)]
    assert subjects[:2] == ["delegate notice: attempt ended in error", "delegate notice: restart"], subjects
    assert subjects[-1].startswith("delegate done"), subjects
    text = "\n".join(text for _subject, text in told(checked, run.name))
    assert "the model server went away" in text, text
    capsys.readouterr()
    assert delegate.main(["follow", run.name]) == 0
    shown = capsys.readouterr().out
    assert "restart: work pass, attempt 1" in shown and "interrupted: the harness reported an error" in shown, shown


def test_the_boxes_own_token_is_never_written_to_the_record(checked, tmp_path):
    script = tmp_path / "release" / "bin" / "opencode"
    said = 'ended("WORKER-WORDS done")'
    assert said in script.read_text()
    script.write_text(script.read_text().replace(
        said, 'ended("WORKER-WORDS done said=" + config["provider"]["poolhouse"]["options"]["apiKey"] + " END")'))
    run = ran(checked, "tell-token", "--no-review")
    token = run.row["text"].split("WORKER-WORDS done said=")[1].split(" END")[0]
    assert len(token) > 20, "the box was given a token and said it"
    held = (delegate_record.folder(run.name) / delegate_record.FILE).read_text()
    assert "WORKER-WORDS done said=" in held and token not in held and "[REDACTED:" in held


def test_a_failed_preflight_and_a_review_that_fails_are_recorded_and_told(checked):
    project = Path(checked.project)
    (project / "scripts").mkdir()
    (project / "scripts" / "test").write_text("#!/bin/sh\necho 'PLUMBING: the runner cannot start here'\nexit 1\n")
    (project / "scripts" / "test").chmod(0o755)
    (project / "tests").mkdir()
    (project / "tests" / "test_one.py").write_text("def test_one():\n    pass\n")
    for words in (("add", "-A"), ("commit", "--allow-empty", "-qm", "a runner that fails")):
        subprocess.run(["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *words], check=True,
                       capture_output=True)
    run = ran(checked, "reviewer-dies", "--review-rounds", "1")
    found = events(run.name, "preflight")
    assert found and not found[0]["ok"] and "the runner cannot start here" in found[0]["untested"], found
    notices = dict(told(checked, run.name))
    assert "the runner cannot start here" in notices["delegate notice: preflight failed"], notices
    assert "review pass 1" in notices["delegate notice: attempt ended in error"], notices
    assert [subject for subject, _text in told(checked, run.name)].count("delegate notice: preflight failed") == 1, "one telling of one finding"
