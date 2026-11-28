"""What an agent is told when it starts: the standard of its role, and facts the program reads from their real source, so nobody types them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse import (
    brief_facts,
    coding_policy,
    delegate_candidate,
    delegate_confined,
    delegate_review,
    delegate_store as store,
)
from poolhouse.isolation import preflight, profile
from poolhouse.isolation.spec import Spec
from poolhouse.serve.backend import LlamaServerBackend
from poolhouse.serve.broker import Broker
from poolhouse.serve.broker_holds import MODEL_START
from poolhouse.serve.manager import ServerManager
from poolhouse.testing.canned import canned_binary

OTHER_ROLES = ("Claude Code", "the landed, updated result", "integration and sync", "update owner", "live proof of the original workflow",
               "Required briefing for every agent")
"""What the standards of other agents say, none of which a delegate can do or is meant for it."""


def a_delegate(where="/tmp/work", **more) -> store.Delegate:
    made = store.Delegate("child-aaaaaa", "lead-bbbbbb", "board", "fix the parser", str(where), "m", source=str(where), edit=True)
    return replace(made, **more)


def a_project(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def committed(root: Path) -> str:
    """A repository of ``root`` with its files committed on branch ``work``; the commit."""
    def git(*words):
        argv = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *words]
        return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "work")
    git("add", "-A")
    git("commit", "-q", "--allow-empty", "-m", "start")
    return git("rev-parse", "--short=12", "HEAD")


@pytest.mark.parametrize("role", ["edit", "read", "review", "review-shell"])
def test_a_delegate_and_its_reviewer_are_held_to_their_own_part_and_to_the_projects_rules(role, tmp_path):
    rec = a_delegate(tmp_path, edit=role != "read", sandboxed=role == "review-shell")
    brief = (delegate_review.review(rec, 1, "answer", "change", 8000).brief if role.startswith("review") else delegate_confined.brief_of(rec))
    for words in OTHER_ROLES:
        assert words not in brief, words
    assert "The project's AGENTS.md binds your work" in brief and "could not" in brief
    assert ("land and install nothing" if role.startswith("review") else "lead-bbbbbb reviews it, lands it, installs it") in brief


@pytest.mark.parametrize("edit", [False, True])
def test_the_shared_prompt_is_the_same_whatever_the_sandbox_branch_or_project(edit, tmp_path):
    plain = a_delegate(tmp_path, edit=edit)
    others = (replace(plain, branch="delegate/child-aaaaaa"), replace(plain, sandboxed=False), replace(plain, source="", max_turns=3))
    assert {delegate_confined.brief_of(each) for each in others} == {delegate_confined.brief_of(plain)}
    reviews = {delegate_review.review(each, 1, "a", "c", 8000).brief for each in (plain, replace(plain, source="", lead="other-cccccc"))}
    assert len(reviews) == 1


@pytest.mark.parametrize(("files", "said", "unsaid"), [
    ({"pyproject.toml": '[tool.poolhouse.tests]\nrun = "make check"\nfile = "make one F=<file>"\n', "scripts/test": "#!/bin/sh\n",
      "conftest.py": ""}, ["declares", "run `make check`", "one file `make one F=<file>`"], ["scripts/test", "pytest"]),
    ({"scripts/test": "#!/bin/sh\n", "pyproject.toml": "[tool.pytest.ini_options]\n"}, ["its own runner, `scripts/test`"], ["pytest"]),
    ({"pyproject.toml": "[tool.pytest.ini_options]\n"}, ["`python -m pytest` (one file: `python -m pytest <file>`)"], []),
    ({"package.json": '{"scripts": {"test": "jest"}}', "pnpm-lock.yaml": ""}, ["`pnpm test`"], ["npm test`;", "pytest"]),
    ({"package.json": '{"scripts": {"test": "jest"}}', "Cargo.toml": "[package]\n"}, ["`npm test`", "`cargo test`"], []),
    ({"go.mod": "module x\n"}, ["`go test ./...`"], []),
])
def test_the_test_command_is_what_the_project_declares_else_what_its_files_imply(tmp_path, files, said, unsaid):
    [line] = brief_facts.tests_of(a_project(tmp_path, files))
    assert all(each in line for each in said) and not any(each in line for each in unsaid), line


@pytest.mark.parametrize("files", [
    {}, {"README.md": "run the tests\n", "tests/test_x.py": ""}, {"package.json": '{"scripts": {"test": "echo \\"Error: no test specified\\""}}'},
    {"pyproject.toml": "not toml ["}, {"package.json": "[1]"},
])
def test_a_project_that_says_nothing_of_its_tests_has_nothing_said_for_it(tmp_path, files):
    assert brief_facts.tests_of(a_project(tmp_path, files)) == []


def test_a_declaration_reached_by_a_link_out_of_the_project_is_not_read(tmp_path):
    outside = a_project(tmp_path / "outside", {"pyproject.toml": '[tool.poolhouse.tests]\nrun = "PRIVATE-VALUE"\n'})
    project = a_project(tmp_path / "project", {})
    (project / "pyproject.toml").symlink_to(outside / "pyproject.toml")
    assert brief_facts.tests_of(project) == []


@pytest.mark.parametrize(("files", "said"), [
    ({".claude/settings.json": json.dumps({"attribution": {"commit": "Co-Authored-By: A <a@example.invalid>"}})},
     "Co-Authored-By: A <a@example.invalid>"),
    ({"docs/claude-code.md": "# Guide\n\n## Commit attribution\n\nEnd commit messages with\nthe session's trailer.\n\n## Other\n\nno\n"},
     "(docs/claude-code.md): End commit messages with the session's trailer."),
    ({"AGENTS.md": "# Rules\n"}, ""),
])
def test_the_commit_attribution_is_the_one_the_project_declares(tmp_path, files, said):
    found = brief_facts.attribution(a_project(tmp_path, files))
    assert said in found and bool(found) is bool(said) and "Other" not in found


def test_what_the_broker_holds_reserved_is_said_and_a_models_own_start_claim_is_not(tmp_path):
    broker = Broker(ServerManager(LlamaServerBackend(binary=canned_binary(tmp_path)), state_file=tmp_path / "leases.json"),
                    room=lambda: 1 << 36, scan=lambda: [])
    assert broker.claim("quiet-window", os.getpid(), {"purpose": "timing runs\ntonight"})["granted"]
    assert broker.claim(f"{MODEL_START}m", os.getpid(), {"purpose": "a start"})["granted"]
    try:
        [line] = brief_facts.reserved(broker.snapshot()["claims"])
    finally:
        broker.unclaim("quiet-window", os.getpid())
        broker.unclaim(f"{MODEL_START}m", os.getpid())
    assert line.startswith("Reserved on this computer: quiet-window since ") and 'for "timing runs tonight"' in line, line
    assert brief_facts.reserved(broker.snapshot()["claims"]) == []


def built(tmp_path, *, commits=False, window=(), mode=coding_policy.EXECUTE, permissions=coding_policy.GUARDED):
    stage, home, temp = (str((tmp_path / each).resolve()) for each in ("stage", "home", "temp"))
    for each in (stage, home, temp):
        Path(each).mkdir(exist_ok=True)
    spec = Spec("opencode", stage, home, sys.executable, 40001, window=window, temp=temp, withheld=(40009,), commits=commits,
                policy=coding_policy.Policy(permissions, mode))
    return profile.build(spec, env={"TMPDIR": temp})


def test_the_sandbox_facts_follow_the_policy_that_was_built(tmp_path):
    kept = " ".join(brief_facts.sandbox(built(tmp_path), (str(tmp_path / ".venv"),)))
    assert "Commit: you cannot commit" in kept and ".claude" in kept and "environments (.venv) are read-only" in kept, kept
    assert "Listening: no program can listen" in kept
    committing = " ".join(brief_facts.sandbox(built(tmp_path, commits=True, window=(40100, 40101))))
    assert "Commit: git works here" in committing and "cannot commit" not in committing, committing
    assert "one of 2 ports" in committing and "environments" not in committing
    reading = " ".join(brief_facts.sandbox(built(tmp_path, permissions=coding_policy.READ_ONLY)))
    assert "Your working directory is read-only" in reading and "Commit:" not in reading, reading
    if sys.platform == "darwin":
        assert "every withheld loopback port (1), the model server's among them" in kept and "the shared /private/tmp is refused" in kept, kept
        assert "GPU compute" in kept and "sandbox-exec" in kept
    else:
        assert "no localhost port" in kept and "no real model" in kept, kept


def test_a_pass_with_no_sandbox_is_told_its_folder_its_bounds_and_how_it_reaches_its_lead(tmp_path):
    project = a_project(tmp_path, {"go.mod": "module x\n"})
    head = committed(project)
    rec = a_delegate(project, sandboxed=False, review_rounds=2, max_turns=7)
    work = delegate_confined.work_pass(rec)
    told = brief_facts.first_message(rec, work, 600.0, folder=str(project))
    assert told.startswith(rec.message()) and f"the project folder itself, {project}" in told
    for fact in (f"Branch and base: branch work at commit {head}.", "There is no sandbox", "`go test ./...`", "at most 600s", "at most 7 turn(s)",
                 "no progress for 600s", "in up to 2 round(s)", "Lead: lead-bbbbbb, reached with lead_note and lead_ask"):
        assert fact in told, fact
    review = delegate_review.review(rec, 1, "a", "c", 8000)
    reviewing = brief_facts.first_message(rec, review, 600.0, folder=str(tmp_path / "copy"))
    assert "a copy of the project folder" in reviewing and "end with your verdict" in reviewing and "lead_ask" not in reviewing
    assert "Tests" not in reviewing, "a review with no shell is told no command"


def test_a_helper_is_told_its_checkout_tests_workers_landing_and_report_with_few_git_calls(tmp_path, monkeypatch):
    project = a_project(tmp_path, {"go.mod": "module x\n", "src/a": "", "AGENTS.md": "## Commit attribution\n\nTrailer line.\n"})
    head = committed(project)
    real, calls = delegate_candidate.git, []
    monkeypatch.setattr(delegate_candidate, "git", lambda *words, **more: calls.append(words) or real(*words, **more))
    lines = brief_facts.helper(project / "src", "lead-bbbbbb", {"budget": 7, "in_use": 2})
    told = brief_facts.block(lines)
    for fact in (f"{project.resolve()}, branch work at commit {head}", "`go test ./...`", "shares 7 on this computer, 2 in use", "Trailer line.",
                 "never merge", "Report to lead-bbbbbb"):
        assert fact in told, fact
    assert len(calls) <= 3 and len(told) < 2000, "a hook's cost and the brief stay small"
    assert brief_facts.helper(tmp_path.parent, "lead-bbbbbb") == []


def test_the_preflight_looks_for_node_as_well_as_the_test_program(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    commands = [command for _, command in preflight.checks(tmp_path)]
    assert "command -v node" in commands and any(command.endswith("command -v jest") for command in commands)
