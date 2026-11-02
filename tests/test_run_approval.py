"""An agent runs `release-key create --write` and `github-protection-apply --apply` only through the owner's one-time approval:
the question, the hook that records the answer, the Bash guard's ticket, and the scripts that spend it, against real hook processes."""

from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import types
import venv
from pathlib import Path
from typing import NamedTuple

import pytest
from person_support import (
    HOOKS,
    ROOT,
    SESSION,
    answer_event,
    environment,
    git,
    rows,
    run_hook as hook_process,
    say,
)

from poolhouse import credentials, protectedwrites, runtime
from poolhouse.workspace import person_auth, person_store, run_approval, run_hook as writer
from tests import memory_keys
from tests.github_support import GOOD, REPO, ruleset, serve

ring = memory_keys.ring
SLUG = REPO
ORIGIN = f"https://github.com/{SLUG}.git"
KEY = ["release-key", "create", "--write"]
APPLY = ["github-protection-apply", "--apply", "--repo", SLUG]
AGENT = {"CLAUDECODE": "1"}
NO_TERMINAL = (False, False)
OTHER = "sess-someone-else"


FLAGS = "#!/usr/bin/env python3\nimport sys\nprint(f'flags isolated={sys.flags.isolated} nobytecode={sys.flags.dont_write_bytecode} cache={sys.pycache_prefix}')\n"


@pytest.fixture(scope="session")
def runtime_template(tmp_path_factory):
    """A real virtual environment holding a package called poolhouse that the runtime's own verification accepts; copied for each test."""
    where = tmp_path_factory.mktemp("runtime-template") / "venv"
    venv.create(where, with_pip=False, symlinks=True)
    site = next((where / "lib").glob("python*/site-packages"))
    (site / "poolhouse" / "fleet").mkdir(parents=True)
    (site / "poolhouse" / "__init__.py").write_text("")
    (site / "poolhouse-0.0.1.dist-info").mkdir()
    (site / "poolhouse-0.0.1.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: poolhouse\nVersion: 0.0.1\n")
    return where


def select_runtime(template, repo):
    """Install a runtime built from the commit at ``repo``'s HEAD under the active state root and select it, as `poolhouse runtime ensure` does; its prefix."""
    commit = git(repo, "rev-parse", "HEAD")
    root = runtime.directory()
    prefix = root / commit / secrets.token_hex(16)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for here, folders, files in os.walk(template):
        target = prefix / Path(here).relative_to(template)
        target.mkdir(exist_ok=True)
        for name in [*folders, *files]:
            source = Path(here, name)
            if source.is_symlink():
                (target / name).symlink_to(source.readlink())  # as it is: a link to the interpreter, not a copy of its mode
            elif source.is_file():
                shutil.copyfile(source, target / name)
    (next((prefix / "lib").glob("python*/site-packages")) / "poolhouse" / "fleet" / "built-from").write_text(commit)
    for folder in (root, prefix.parent, prefix):
        folder.chmod(0o700)
    runtime.publish(runtime.Runtime(prefix, commit, "0.0.1", runtime.identity()))
    return prefix


@pytest.fixture
def world(tmp_path, monkeypatch, runtime_template):
    """A checkout with the scripts, a GitHub origin and an origin/main, a selected runtime built from it, a person state, and this session's project set; ``(tmp, state, repo)``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for name in ("release-key", "github-protection-apply", "agent-identity", "github-protection"):
        script = repo / "scripts" / name
        script.parent.mkdir(exist_ok=True)
        script.write_text(FLAGS + ("raise SystemExit(1)\n" if name == "agent-identity" else "") if name in ("agent-identity", "github-protection") else "#!/bin/sh\n")
        script.chmod(0o755)
    (repo / "src").mkdir()
    (repo / "src" / "code.py").write_text("x = 1\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "note.md").write_text("note\n")
    git(repo, "add", "scripts", "src", "docs")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "remote", "add", "origin", ORIGIN)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    for name in [*os.environ]:
        if name in ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE") or name.startswith(run_approval.LOADER_PREFIXES):
            monkeypatch.delenv(name)
    monkeypatch.delenv(run_approval.TICKET_ENV, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    select_runtime(runtime_template, repo)
    return tmp_path, tmp_path / "state", repo


class Asked(NamedTuple):
    """What `run-approval propose` printed: the question and what to put under it."""

    question: str
    yes: str
    no: str
    yes_says: str
    no_says: str
    header: str


def propose(repo, state, *words, project=None, cwd=None):
    env = {"CLAUDE_PROJECT_DIR": str(repo) if project is None else project}
    return subprocess.run([sys.executable, str(HOOKS / "run-approval"), "propose", *words], cwd=cwd or repo, text=True,
                          capture_output=True, timeout=60, check=False, env=environment(state, **env))


def asked(done):
    assert done.returncode == 0, done.stderr
    text, _, rest = done.stdout.strip().partition("\n\nheader: ")
    header, _, rest = rest.partition("\noptions: ")
    labels, _, says = rest.partition("\ndescriptions: ")
    (yes, no), (yes_says, no_says) = labels.split(" | "), says.split(" | ")
    return Asked(text, yes, no, yes_says, no_says, header)


def answer_of(repo, ask, label=None, *, shown=None, **more):
    """The PostToolUse input of an AskUserQuestion that showed ``ask`` (or ``shown``, the one question) and was answered with ``label``."""
    label = ask.yes if label is None else label
    question = shown or {"question": ask.question, "header": ask.header, "multiSelect": False,
                         "options": [{"label": ask.yes, "description": ask.yes_says}, {"label": ask.no, "description": ask.no_says}]}
    return answer_event(repo, ask.question, label, tool_input={"questions": [question]}, **more)


def hooked(event, state, repo, **env):
    return hook_process(event, state, **{"CLAUDE_PROJECT_DIR": str(repo), **env})


def approve(tmp, state, repo, *words, **event):
    """Bind the session, ask the question for ``words`` and answer it with the approval; the hook's process."""
    say(tmp, state, repo, "hello", prompt_id="bind")
    return hooked(answer_of(repo, asked(propose(repo, state, *words)), **event), state, repo)


def records(state, kind):
    return [r for r in rows(state) if r["type"] == "authorization" and r["kind"] == kind]


def spent(state):
    return [r for r in rows(state) if r["type"] == "transition" and r["state"] == "used"]


def guard(command, state, cwd, env=None, project=None, **event):
    env = environment(state, POOLHOUSE_HOOK_STATE=str(state.parent / "hook-state"),
                      CLAUDE_PROJECT_DIR=str(cwd) if project is None else project, **(env or {}))
    return subprocess.run([sys.executable, str(HOOKS / "claude-bash-guard")], text=True, capture_output=True, timeout=60,
                          check=False, env=env, input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                                                                  "cwd": str(cwd), **event}))


def launch_of(done):
    """The words of the command line the guard put in place of the agent's, an empty list when it left the command alone."""
    if not done.stdout.strip():
        return []
    return shlex.split(json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"]["command"])


def ticket_of(done):
    """The ticket file the guard's command line names, an empty string when it names none."""
    held = [w.split("=", 1)[1] for w in launch_of(done) if w.startswith(f"{run_approval.TICKET_ENV}=")]
    return held[0] if held else ""


def secret_in(path):
    return Path(path).read_text()


def hand_over(state, secret):
    """A new private file holding ``secret`` in the ticket folder, as the guard leaves one; its path."""
    folder = state / "person" / run_approval.TICKET_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / secrets.token_hex(8)
    path.write_text(secret)
    path.chmod(0o600)
    return str(path)


def ticket_for(tmp, state, repo, *words):
    """Approve ``words`` and run its exact command through the guard; the ticket's secret."""
    assert approve(tmp, state, repo, *words).returncode == 0
    path = ticket_of(guard(" ".join(f"scripts/{w}" if i == 0 else w for i, w in enumerate(words)), state, repo))
    assert path, "the guard gave no ticket"
    return secret_in(path)


# -- the question ---------------------------------------------------------------------------


@pytest.mark.parametrize("words", [KEY, APPLY, [*APPLY, "--strict-tags", "--owner-review"]])
def test_the_question_is_made_of_the_command_the_repository_the_commit_and_the_registry(world, words):
    _, state, repo = world
    ask = asked(propose(repo, state, *words))
    sha = git(repo, "rev-parse", "HEAD")
    assert ask.question.startswith(f"Run scripts/{words[0]} ")
    assert f"Repository: {SLUG}" in ask.question and f"Commit: {sha}" in ask.question and f"Checkout: {repo.resolve()}" in ask.question
    assert ask.yes.startswith("Approve scripts/") and ask.no and ask.header == "Run script" and ask.yes_says and ask.no_says
    assert ask.question.splitlines()[-1].startswith("authorize run-script ") and " 15m x1 #" in ask.question.splitlines()[-1]


def test_the_options_of_a_covered_command_are_the_same_whatever_order_its_flags_come_in(world):
    _, state, repo = world
    one = asked(propose(repo, state, *APPLY, "--strict-tags", "--owner-review"))
    two = asked(propose(repo, state, "github-protection-apply", "--owner-review", "--repo", SLUG, "--strict-tags", "--apply"))
    assert one == two


@pytest.mark.parametrize("words", [
    ["release-key", "create"], ["release-key", "rotate", "--write"], ["release-key", "show-public"], ["release-key", "agent"],
    [*KEY, "extra"], ["github-protection-apply", "--apply"], ["github-protection-apply", "--repo", SLUG],
    ["github-protection-apply", "--apply", "--repo", "someone/else"], [*APPLY, "--apply"], [*APPLY, "--restore", "backup.json"],
    [*APPLY, "--repo", SLUG], ["agent-identity", "setup"], ["../../bin/ls"], ["release-key/../x"], []])
def test_only_the_two_covered_commands_can_be_put_to_the_owner(world, words):
    _, state, repo = world
    done = propose(repo, state, *words)
    assert done.returncode != 0 and "question" not in done.stdout and not (state / "person").exists()


def test_the_question_shows_how_the_code_differs_from_the_remotes_main_and_what_guards_it_touches(world, runtime_template):
    template = runtime_template
    _, state, repo = world
    plain = asked(propose(repo, state, *KEY)).question
    assert "Changed since origin/main (as last fetched) in scripts and src: none" in plain
    assert "This script since origin/main: unchanged" in plain and "Guards changed since origin/main: none" in plain
    (repo / "scripts" / "release-key").write_text("#!/bin/sh\ncat /dev/null\ncat /dev/null\n")
    (repo / "src" / "code.py").write_text("x = 2\n")
    git(repo, "add", "scripts", "src")
    git(repo, "commit", "-q", "-m", "a committed change to the release script")
    select_runtime(template, repo)
    changed = asked(propose(repo, state, *KEY)).question
    assert "Changed since origin/main (as last fetched) in scripts and src: 2 files changed" in changed
    assert "This script since origin/main: 1 file changed, 2 insertions(+)" in changed
    assert "Guards changed since origin/main: scripts/release-key" in changed


def test_without_a_main_on_the_remote_to_compare_against_nothing_is_asked(world):
    _, state, repo = world
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert propose(repo, state, *KEY).returncode != 0


def test_a_change_in_scripts_or_src_stops_the_question_but_a_change_elsewhere_does_not(world):
    _, state, repo = world
    (repo / "docs" / "note.md").write_text("changed\n")
    assert propose(repo, state, *KEY).returncode == 0
    (repo / "src" / "code.py").write_text("x = 2\n")
    assert propose(repo, state, *KEY).returncode != 0
    git(repo, "checkout", "--", "src/code.py")
    (repo / "scripts" / "extra.py").write_text("import sys\n")
    assert propose(repo, state, *KEY).returncode != 0
    (repo / "scripts" / "extra.py").unlink()
    (repo / "scripts" / "release-key").write_text("#!/bin/sh\necho changed\n")
    assert propose(repo, state, *KEY).returncode != 0


def test_a_flag_that_hides_a_change_from_git_does_not_hide_it_from_the_check(world):
    _, state, repo = world
    for flag in ("--assume-unchanged", "--skip-worktree"):
        git(repo, "update-index", flag, "scripts/release-key")
        (repo / "scripts" / "release-key").write_text(f"#!/bin/sh\necho {flag}\n")
        assert git(repo, "status", "--porcelain", "--", "scripts") == "", flag  # git itself is blind to it
        done = propose(repo, state, *KEY)
        assert done.returncode != 0 and "scripts/release-key differs from the commit" in done.stderr, flag
        (repo / "scripts" / "release-key").write_text("#!/bin/sh\n")
        git(repo, "update-index", flag.replace("--", "--no-"), "scripts/release-key")
    git(repo, "update-index", "--skip-worktree", "src/code.py")
    (repo / "src" / "code.py").unlink()
    assert "src/code.py is missing" in propose(repo, state, *KEY).stderr


def test_a_staged_change_and_a_changed_executable_bit_are_changes(world):
    _, state, repo = world
    (repo / "src" / "code.py").write_text("x = 5\n")
    git(repo, "add", "src/code.py")
    assert propose(repo, state, *KEY).returncode != 0
    git(repo, "restore", "--staged", "--worktree", "src/code.py")
    (repo / "scripts" / "release-key").chmod(0o644)
    assert "executable bit" in propose(repo, state, *KEY).stderr


def test_a_file_that_git_ignores_is_still_a_file_that_runs(world):
    _, state, repo = world
    (repo / ".git" / "info" / "exclude").write_text("scripts/extra.py\n*.pyc\n")
    (repo / "scripts" / "extra.py").write_text("import sys\n")
    assert "scripts/extra.py is not a committed file" in propose(repo, state, *KEY).stderr
    (repo / "scripts" / "extra.py").unlink()
    (repo / "src" / "shadow.pyc").write_bytes(b"\0")
    assert "src/shadow.pyc is not a committed file" in propose(repo, state, *KEY).stderr


def test_compiled_caches_do_not_stop_the_question_because_the_run_never_reads_them(world):
    _, state, repo = world
    (repo / "src" / "__pycache__").mkdir()
    (repo / "src" / "__pycache__" / "code.cpython-313.pyc").write_bytes(b"\0")
    assert propose(repo, state, *KEY).returncode == 0
    (repo / "src" / "__pycache__" / "code.cpython-313.pyc").unlink()
    (repo / "src" / "__pycache__").rmdir()
    (repo / "src" / "__pycache__").symlink_to(repo / "docs")
    assert "src/__pycache__ is a symbolic link; scripts and src hold none" in propose(repo, state, *KEY).stderr


def test_a_symbolic_link_in_the_checked_folders_is_refused_with_its_path_named_whatever_it_points_at(world):
    tmp, state, repo = world
    (repo / "scripts" / "linked").symlink_to("release-key")
    assert "scripts/linked is a symbolic link; scripts and src hold none" in propose(repo, state, *KEY).stderr
    (repo / "scripts" / "linked").unlink()
    (repo / "src" / "code.py").unlink()
    (repo / "src" / "code.py").symlink_to(tmp / "elsewhere.py")
    assert "src/code.py is a symbolic link; scripts and src hold none" in propose(repo, state, *KEY).stderr
    (repo / "src" / "code.py").unlink()
    git(repo, "checkout", "--", "src/code.py")
    (repo / "src" / "linked-folder").symlink_to(tmp)
    assert "src/linked-folder is a symbolic link; scripts and src hold none" in propose(repo, state, *KEY).stderr


@pytest.mark.parametrize("target", ["release-key", "github-protection-apply", "/etc/hosts", "../../elsewhere"])
def test_a_committed_symbolic_link_is_refused_even_when_nothing_has_changed_since(world, runtime_template, target):
    _, state, repo = world
    (repo / "scripts" / "linked").symlink_to(target)
    git(repo, "add", "scripts/linked")
    git(repo, "commit", "-q", "-m", "a link")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    select_runtime(runtime_template, repo)
    assert "scripts/linked is committed as a symbolic link; scripts and src hold none" in propose(repo, state, *KEY).stderr


def test_a_folder_that_cannot_be_listed_is_a_reason_and_not_a_gap(world):
    """The execute-only folder of the review: Python imports from it, a walk cannot list it."""
    if os.geteuid() == 0:
        pytest.skip("root reads every folder")
    _, state, repo = world
    shadow = repo / "src" / "psutil"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("print('SHADOW')\n")
    shadow.chmod(0o111)
    try:
        done = propose(repo, state, *KEY)
        assert done.returncode != 0 and "src/psutil" in done.stderr and "Traceback" not in done.stderr
        shadow.chmod(0o311)
        assert propose(repo, state, *KEY).returncode != 0  # searchable but not listable
        shadow.chmod(0o755)
        assert "src/psutil/__init__.py is not a committed file" in propose(repo, state, *KEY).stderr
    finally:
        shadow.chmod(0o755)


def test_a_committed_folder_or_file_that_cannot_be_read_is_a_reason_and_not_a_crash(world):
    if os.geteuid() == 0:
        pytest.skip("root reads every file")
    _, state, repo = world
    for path, mode in ((repo / "src", 0o311), (repo / "src" / "code.py", 0o000), (repo / "scripts", 0o111)):
        before = path.stat().st_mode & 0o777
        path.chmod(mode)
        try:
            done = propose(repo, state, *KEY)
            assert done.returncode != 0 and "Traceback" not in done.stderr and "src" in done.stderr + "scripts", path
        finally:
            path.chmod(before)
    assert propose(repo, state, *KEY).returncode == 0


@pytest.mark.parametrize("name", ["__pycache__x", "x__pycache__", "__pycache__.d", ".__pycache__", "__PYCACHE__", "pycache", "__pycache"])
def test_only_a_folder_named_exactly_pycache_is_left_out_of_the_check(world, name):
    _, state, repo = world
    (repo / "src" / name).mkdir()
    (repo / "src" / name / "shadow.py").write_text("print('SHADOW')\n")
    assert f"src/{name}/shadow.py is not a committed file" in propose(repo, state, *KEY).stderr


def test_a_pycache_that_is_a_file_or_holds_a_folder_of_code_is_not_left_out(world):
    _, state, repo = world
    (repo / "src" / "__pycache__").write_text("a file, not a folder\n")
    assert "src/__pycache__ is not a committed file" in propose(repo, state, *KEY).stderr
    (repo / "src" / "__pycache__").unlink()
    (repo / "src" / "__pycache__").mkdir()
    (repo / "src" / "__pycache__" / "shadow.py").write_text("print('SHADOW')\n")  # never read by the run: bytecode caches only
    assert propose(repo, state, *KEY).returncode == 0


def test_a_missing_folder_a_replaced_file_and_a_second_copy_are_changes(world):
    _, state, repo = world
    (repo / "src" / "code.py").unlink()
    (repo / "src" / "code.py").mkdir()
    (repo / "src" / "code.py" / "inner.py").write_text("x = 1\n")
    assert "is not a committed file" in propose(repo, state, *KEY).stderr
    shutil.rmtree(repo / "src" / "code.py")
    git(repo, "checkout", "--", "src/code.py")
    shutil.rmtree(repo / "scripts")
    assert "is missing" in propose(repo, state, *KEY).stderr or "scripts is not a plain folder" in propose(repo, state, *KEY).stderr
    git(repo, "checkout", "--", "scripts")
    (repo / "src" / "deeper" / "deepest").mkdir(parents=True)
    (repo / "src" / "deeper" / "deepest" / "x.py").write_text("x = 1\n")
    assert "src/deeper/deepest/x.py is not a committed file" in propose(repo, state, *KEY).stderr


@pytest.mark.parametrize("url", ["https://example.com/o/r.git", "https://github.com.evil.example/o/r.git",
                                 "git@github.com:o/r/extra.git", "/srv/git/r.git", "https://github.com/o"])
def test_an_origin_that_is_not_a_github_repository_is_not_a_repository_to_approve(world, url):
    _, state, repo = world
    git(repo, "remote", "set-url", "origin", url)
    assert propose(repo, state, *KEY).returncode != 0


@pytest.mark.parametrize("url", [f"git@github.com:{SLUG}.git", f"ssh://git@github.com/{SLUG}.git", f"https://github.com/{SLUG}"])
def test_the_usual_forms_of_a_github_origin_name_the_same_repository(world, url):
    _, state, repo = world
    git(repo, "remote", "set-url", "origin", url)
    assert f"Repository: {SLUG}" in asked(propose(repo, state, *KEY)).question


def evil_repo(repo):
    """A repository of its own inside the project's worktrees folder, with the scripts, a fake origin and an origin/main."""
    evil = repo / ".claude" / "worktrees" / "evil"
    evil.mkdir(parents=True)
    git(evil, "init", "-q", "-b", "main")
    for folder in ("scripts", "src"):
        subprocess.run(["cp", "-R", str(repo / folder), str(evil / folder)], check=True)
    git(evil, "add", "scripts", "src")
    git(evil, "commit", "-q", "-m", "evil")
    git(evil, "remote", "add", "origin", "https://github.com/attacker/stolen.git")
    git(evil, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(evil, "fetch", "-q", str(repo), "main")  # the commit the runtime was built from is there too
    return evil


def test_a_repository_made_inside_the_project_is_not_a_worktree_of_the_project(world):
    tmp, state, repo = world
    evil = evil_repo(repo)
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(evil, state, *KEY, project=""))  # asked where no session project is named, the tag is valid
    assert "attacker/stolen" in ask.question
    hooked(answer_of(evil, ask), state, repo)
    assert records(state, "run-script") == []
    assert propose(evil, state, *KEY, project=str(repo)).returncode != 0
    assert ticket_of(guard("scripts/release-key create --write", state, evil, project=str(repo))) == ""
    assert spent(state) == []


def test_a_real_worktree_of_the_project_is_one(world):
    tmp, state, repo = world
    other = tmp / "linked-worktree"
    git(repo, "worktree", "add", "-q", "-b", "side", str(other))
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(other, state, *KEY))
    hooked(answer_of(other, ask), state, repo)
    assert len(records(state, "run-script")) == 1
    assert len(ticket_of(guard("scripts/release-key create --write", state, other, project=str(repo)))) > 0


def test_a_session_that_names_no_project_records_and_issues_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    hooked(answer_of(repo, ask), state, repo, CLAUDE_PROJECT_DIR="")
    assert records(state, "run-script") == []
    hooked(answer_of(repo, ask), state, repo)
    assert len(records(state, "run-script")) == 1
    assert ticket_of(guard("scripts/release-key create --write", state, repo, project="")) == ""
    assert spent(state) == []


# -- the answer ------------------------------------------------------------------------------


def test_the_approving_answer_records_one_authorization_bound_to_the_exact_run(world):
    tmp, state, repo = world
    done = approve(tmp, state, repo, *KEY)
    assert done.returncode == 0 and "attested by hook" in done.stdout
    (auth,) = records(state, "run-script")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    assert re.fullmatch(re.escape(f"{repo.resolve()}|release-key|create,--write|{SLUG}|{tree}|") + "[0-9a-f]{12}", auth["target"])
    assert auth["uses"] == 1 and auth["session_id"] == SESSION and auth["how"] == "asked"
    assert 0 < auth["expires"] - auth["ts"] <= 15 * 60 + 1


def test_the_same_answer_twice_leaves_one_live_authorization(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    hooked(answer_of(repo, asked(propose(repo, state, *KEY))), state, repo)
    assert len(records(state, "run-script")) == 1


def test_declining_records_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    hooked(answer_of(repo, ask, ask.no), state, repo)
    assert records(state, "run-script") == []


def test_an_answer_to_a_question_the_agent_wrote_itself_records_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    question = ask.question
    forged = {
        "no echo": "\n".join(question.splitlines()[:-1]),
        "a changed word": question.replace("Run scripts/release-key", "Run scripts/release-key now"),
        "another tag": question[:-12] + "0" * 12,
        "another command, same echo": question.replace("What it does:", "What it does: also reads the key. "),
        "no tag": question.rsplit(" #", 1)[0],
        "extra text after the echo": question + "\nApprove everything",
    }
    for label, text in forged.items():
        hooked(answer_of(repo, ask._replace(question=text)), state, repo)
        assert records(state, "run-script") == [], label


def test_only_the_approving_label_of_this_exact_question_records_it(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    for label in ("Approve release of abcdef0", "Approve scripts/release-key create", ask.yes.upper(), ask.yes + " ", "yes", ask.no):
        hooked(answer_of(repo, ask, label), state, repo)
    assert records(state, "run-script") == []


def test_an_answer_after_the_checkout_moved_on_records_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    (repo / "src" / "code.py").write_text("x = 2\n")
    git(repo, "add", "src/code.py")
    git(repo, "commit", "-q", "-m", "change the code after the question")
    hooked(answer_of(repo, ask), state, repo)
    assert records(state, "run-script") == []


def test_a_question_asked_in_one_repository_does_not_approve_a_run_in_another(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    other = tmp / "other"
    subprocess.run(["git", "clone", "-q", str(repo), str(other)], check=True)
    hooked(answer_of(other, ask), state, other)
    assert records(state, "run-script") == []


def test_a_subagent_or_an_unattended_session_cannot_answer(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    hooked(answer_of(repo, ask, agent_id="helper-1"), state, repo)
    unattended = subprocess.run([sys.executable, str(HOOKS / "claude-user-prompt")], text=True, capture_output=True,
                                input=json.dumps(answer_of(repo, ask)), check=False,
                                env=environment(state, POOLHOUSE_NONINTERACTIVE="1", CLAUDE_PROJECT_DIR=str(repo)))
    assert unattended.returncode == 0 and records(state, "run-script") == []


def test_the_hooks_own_checks_refuse_a_subagent_and_an_unattended_session_even_when_an_earlier_answer_is_on_record(world, monkeypatch):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    hooked(answer_of(repo, ask, ask.no), state, repo)  # the owner declined first: an answer row for this question exists
    log = person_store.open_log()
    assert writer.on_event(answer_of(repo, ask, agent_id="helper-1"), log) is None
    monkeypatch.setenv("POOLHOUSE_NONINTERACTIVE", "1")
    assert writer.on_event(answer_of(repo, ask), log) is None
    monkeypatch.delenv("POOLHOUSE_NONINTERACTIVE")
    assert writer.on_event(answer_of(repo, ask, session_id="line\nbreak"), log) is None
    assert records(state, "run-script") == []
    assert writer.on_event(answer_of(repo, ask), log) is not None
    assert len(records(state, "run-script")) == 1


def test_a_question_asked_with_more_than_the_printed_text_records_nothing(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    shown = {"question": ask.question, "header": ask.header, "multiSelect": False,
             "options": [{"label": ask.yes, "description": ask.yes_says}, {"label": ask.no, "description": ask.no_says}]}
    other = {"question": "A second thing?", "header": "Other", "multiSelect": False,
             "options": [{"label": "a", "description": "a"}, {"label": "b", "description": "b"}]}
    variants = {
        "another description": {**shown, "options": [{"label": ask.yes, "description": "Safe and tested."}, shown["options"][1]]},
        "a third option": {**shown, "options": [*shown["options"], {"label": "Run and publish", "description": "more"}]},
        "an option fewer": {**shown, "options": shown["options"][:1]},
        "options in the other order": {**shown, "options": shown["options"][::-1]},
        "a preview on an option": {**shown, "options": [{**shown["options"][0], "preview": "the key is safe"}, shown["options"][1]]},
        "another header": {**shown, "header": "Safe"},
        "multiple choice": {**shown, "multiSelect": True},
        "another field": {**shown, "note": "x"},
        "no options": {key: value for key, value in shown.items() if key != "options"},
    }
    said = {
        "another description": "the options, their order or their descriptions differ", "a third option": "the options, their order or their descriptions differ",
        "an option fewer": "the options, their order or their descriptions differ", "options in the other order": "the options, their order or their descriptions differ",
        "a preview on an option": "an option has an unexpected field (preview)", "another header": "the header is not 'Run script'",
        "multiple choice": "the question allows more than one choice", "another field": "the question has an unexpected field (note)",
        "no options": "the options are missing"}
    for label, variant in variants.items():
        done = hooked(answer_of(repo, ask, shown=variant), state, repo)
        assert records(state, "run-script") == [], label
        assert f"the approval was not recorded: {said[label]}" in done.stdout, (label, done.stdout)
    for label, tool_input, why in (("two questions", {"questions": [shown, other]}, "more than one question"),
                                   ("two questions, ours second", {"questions": [other, shown]}, "more than one question"),
                                   ("an empty tool_input", {}, "carries no record of what was asked")):
        done = hooked({**answer_of(repo, ask), "tool_input": tool_input}, state, repo)
        assert why in done.stdout and records(state, "run-script") == [], label
    done = hooked({k: v for k, v in answer_of(repo, ask).items() if k != "tool_input"}, state, repo)
    assert "carries no record of what was asked" in done.stdout and records(state, "run-script") == []
    hooked(answer_of(repo, ask), state, repo)
    assert len(records(state, "run-script")) == 1


@pytest.mark.parametrize("words", ["yes, run the release key", "approved: scripts/release-key create --write", "/allow run-script",
                                   "go ahead and apply the protection", "ok"])
def test_typed_words_never_approve_a_run(world, words):
    tmp, state, repo = world
    say(tmp, state, repo, words, prompt_id="typed")
    assert records(state, "run-script") == []


def test_stopping_ends_the_approval(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    say(tmp, state, repo, "stop", prompt_id="stop")
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert spent(state) == []


# -- the Bash guard's ticket -----------------------------------------------------------------


def test_the_main_sessions_exact_command_gets_one_ticket_and_spends_the_approval(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    done = guard("scripts/release-key create --write", state, repo)
    path = ticket_of(done)
    secret = secret_in(path)
    assert done.returncode == 0 and len(secret) == 32
    assert len(spent(state)) == 1
    (ticket,) = records(state, "run-ticket")
    assert re.search(re.escape(f"|{SLUG}|{git(repo, 'rev-parse', 'HEAD^{tree}')}|") + f"[0-9a-f]{{12}}#{secret}$", ticket["target"])
    assert 0 < ticket["expires"] - ticket["ts"] <= 61
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert len(spent(state)) == 1


def test_the_command_that_runs_is_a_fixed_line_with_a_cleared_environment_and_no_secret_in_it(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    done = guard("scripts/release-key create --write", state, repo)
    words = launch_of(done)
    secret = secret_in(ticket_of(done))
    command = json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    assert secret not in command and "export" not in command and "\n" not in command
    assert words[:2] == ["/usr/bin/env", "-i"]
    named = {w.split("=", 1)[0] for w in words[2:] if "=" in w and not w.startswith(("-", "/", "pycache"))}
    assert named <= {"HOME", "PATH", "CLAUDECODE", run_approval.TICKET_ENV, "POOLHOUSE_HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM"}
    assert {"HOME", "PATH", "CLAUDECODE", run_approval.TICKET_ENV} <= named
    start = next(i for i, w in enumerate(words) if w == "-I")
    assert words[start - 1] == str(runtime.available().python) and words[start:start + 3] == ["-I", "-B", "-X"]
    assert words[start + 3].startswith("pycache_prefix=") and not Path(words[start + 3].split("=", 1)[1]).exists()
    assert words[start + 4:] == [str(repo.resolve() / "scripts" / "release-key"), "create", "--write"]


def test_the_ticket_file_is_private_and_alone_in_a_private_folder(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    path = Path(ticket_of(guard("scripts/release-key create --write", state, repo)))
    assert path.parent == state / "person" / run_approval.TICKET_DIR
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    assert [p.name for p in path.parent.iterdir()] == [path.name]


@pytest.mark.parametrize("command", ["scripts/release-key create --write", "./scripts/release-key create --write",
                                     "{repo}/scripts/release-key create --write", "scripts/release-key  create   --write",
                                     " scripts/release-key create --write\t"])
def test_the_exact_approved_command_gets_a_ticket_however_its_path_is_written(world, command):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    assert ticket_of(guard(command.replace("{repo}", str(repo)), state, repo))


@pytest.mark.parametrize("command", [
    "cd scripts && ./release-key create --write", "scripts/release-key create --write && echo done", "scripts/release-key create --write; id",
    "scripts/release-key create --write | cat", "scripts/release-key create --write > /tmp/out",
    "scripts/release-key create --write $(id)", "scripts/release-key create --write `id`", "FOO=1 scripts/release-key create --write",
    "env scripts/release-key create --write", "sh -c 'scripts/release-key create --write'", "python3 scripts/release-key create --write",
    "scripts/release-key create --write extra", "scripts/release-key create", "scripts/release-key create --write --write",
    "scripts/release-key rotate --write", "scripts/github-protection-apply --apply --repo ownerlogin/poolhouse-test",
    "cp scripts/release-key /tmp/x && /tmp/x create --write", "scripts/release-key\ncreate --write",
    "scripts/release-key create --write\nscripts/release-key create --write", "scripts/release-key create --write # ok",
    "'scripts/release-key' create --write", "scripts/release-key create '--write'"])
def test_nothing_but_the_exact_approved_command_gets_a_ticket(world, command):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    assert ticket_of(guard(command, state, repo)) == "" and spent(state) == [], command


@pytest.mark.parametrize("space", ["\N{NO-BREAK SPACE}", "\N{EM SPACE}", "\x0b", "\x0c", "\r", "\N{IDEOGRAPHIC SPACE}", "\N{NEXT LINE}", "\x1c"])
def test_whitespace_that_the_shell_does_not_split_on_is_refused_and_leaves_the_approval_unspent(world, space):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    for command in (f"scripts/release-key{space}create --write", f"scripts/release-key create --write{space}",
                    f"{space}scripts/release-key create --write", f"scripts/release-key create{space}--write"):
        assert ticket_of(guard(command, state, repo)) == "" and spent(state) == [], repr(command)
    assert ticket_of(guard("scripts/release-key create --write", state, repo))


def test_a_command_run_from_a_folder_outside_the_checkout_or_in_another_checkout_gets_no_ticket(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    other = tmp / "other"
    subprocess.run(["git", "clone", "-q", str(repo), str(other)], check=True)
    assert ticket_of(guard("scripts/release-key create --write", state, tmp, project=str(repo))) == ""
    assert ticket_of(guard("scripts/release-key create --write", state, other, project=str(repo))) == ""
    assert ticket_of(guard(f"{other}/scripts/release-key create --write", state, repo)) == ""
    assert spent(state) == []


def test_a_copy_or_a_link_named_like_the_script_is_not_the_script(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    evil = tmp / "evil" / "release-key"
    evil.parent.mkdir()
    evil.write_text("#!/bin/sh\n")
    evil.chmod(0o755)
    assert ticket_of(guard(f"{evil} create --write", state, repo)) == ""
    link = repo / "scripts" / "linked"
    link.symlink_to(evil)
    assert ticket_of(guard(f"{link} create --write", state, repo)) == ""
    assert spent(state) == []


def test_a_subagent_gets_no_ticket_and_leaves_the_approval_for_the_main_session(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    for event in ({"agent_id": "helper-1"}, {"agent_id": "helper-2", "agent_type": "general-purpose"}):
        assert ticket_of(guard("scripts/release-key create --write", state, repo, **event)) == ""
    assert spent(state) == []
    assert ticket_of(guard("scripts/release-key create --write", state, repo))


def test_no_approval_means_no_ticket(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert records(state, "run-ticket") == []


def test_an_approval_of_one_command_gives_no_ticket_for_the_other(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    assert ticket_of(guard(" ".join(f"scripts/{w}" if w == APPLY[0] else w for w in APPLY), state, repo)) == ""
    assert ticket_of(guard("scripts/github-protection-apply --apply --repo ownerlogin/poolhouse-test --strict-tags", state, repo)) == ""
    assert spent(state) == []


def test_an_approval_spoken_in_another_session_gives_the_main_session_no_ticket(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    hooked(answer_of(repo, asked(propose(repo, state, *KEY)), session_id=OTHER), state, repo)
    assert len(records(state, "run-script")) == 1
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert spent(state) == []


def test_an_approval_is_spent_only_when_the_checkout_still_matches_it(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    (repo / "src" / "code.py").write_text("x = 3\n")
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    git(repo, "checkout", "--", "src/code.py")
    assert ticket_of(guard("scripts/release-key create --write", state, repo))


def test_an_expired_approval_gives_no_ticket(world, monkeypatch):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 16 * 60)
    with pytest.raises(person_auth.NotAuthorized):
        writer.issue("scripts/release-key create --write", str(repo), session=SESSION)
    monkeypatch.setattr(time, "time", real)
    assert writer.issue("scripts/release-key create --write", str(repo), session=SESSION).startswith("/usr/bin/env -i ")


def test_the_guard_does_not_stamp_a_command_that_only_names_the_script_in_a_search(world):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    for command in ("grep -n create scripts/release-key", "git add scripts/release-key", "cat scripts/release-key"):
        done = guard(command, state, repo)
        assert done.returncode == 0 and ticket_of(done) == "", command
    assert spent(state) == []


# -- the scripts that spend the ticket ---------------------------------------------------------


def load(name, monkeypatch, repo):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(ROOT / "scripts" / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", repo)
    return module


def recording_gh(tmp, name="bin"):
    """A `gh` that records its arguments, its standard input, its environment and its configuration folder, in a folder of the trusted ones."""
    where, log = tmp / name, tmp / f"{name}-log"
    where.mkdir()
    log.mkdir()
    gh = where / "gh"
    gh.write_text(f'#!/bin/sh\nn=$(ls "{log}" | grep -c "^stdin")\ncat > "{log}/stdin.$n"\necho "$@" > "{log}/argv.$n"\n'
                  f'env > "{log}/env.$n"\n[ -z "$GH_CONFIG_DIR" ] || ls -A "$GH_CONFIG_DIR" > "{log}/config.$n"\nexit 0\n')
    gh.chmod(0o755)
    return where, log


@pytest.fixture
def release(world, monkeypatch):
    """The release-key script on the world's checkout with a trusted recording `gh`; ``(world..., module, gh log)``."""
    tmp, state, repo = world
    module = load("release-key", monkeypatch, repo)
    where, log = recording_gh(tmp)
    monkeypatch.setattr(run_approval, "TRUSTED_DIRS", (str(where), "/usr/bin", "/bin"))
    monkeypatch.setattr(run_approval, "_code_roots", lambda _found: (os.path.realpath(ROOT / "src"),))
    signing = repo / "signing.py"
    signing.write_text('RELEASE_KEY = ""\n')
    monkeypatch.setattr(module, "SIGNING", signing)
    monkeypatch.setenv("PATH", f"{where}{os.pathsep}{os.environ['PATH']}")
    credentials.set("PRIMER", "x", keychain=True)  # a person provisions the keystore once; an agent run only reads it
    monkeypatch.setenv("CLAUDECODE", "1")
    return tmp, state, repo, module, log


def test_an_approved_agent_run_makes_the_key_without_showing_it_and_pins_the_repository(release, monkeypatch, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    held = (log / "stdin.0").read_bytes()
    assert held.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
    assert held == base64.b64decode(str(credentials.get(rk.ITEM)))
    assert (log / "argv.0").read_text().split() == ["secret", "set", "RELEASE_SIGNING_KEY", "--env", "release", "--repo", SLUG]
    out = capsys.readouterr()
    assert "PRIVATE KEY" not in out.out + out.err
    assert 'RELEASE_KEY = "ssh-ed25519 ' in (repo / "signing.py").read_text()
    assert run_approval.TICKET_ENV not in os.environ


def test_the_ticket_file_is_read_and_removed_and_the_ticket_spent_once(release, monkeypatch, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    path = hand_over(state, secret)
    monkeypatch.setenv(run_approval.TICKET_ENV, path)
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    assert not Path(path).exists()
    monkeypatch.setattr(credentials, "get", lambda *_: None)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert "run-approval propose release-key create --write" in capsys.readouterr().err
    assert not (log / "stdin.1").exists()


def test_without_a_ticket_an_agent_is_refused_with_the_way_to_ask(release, capsys):
    _tmp, _state, _repo, rk, log = release
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    err = capsys.readouterr().err
    assert "for a person" in err and "scripts/hooks/run-approval propose release-key create --write" in err
    assert list(log.iterdir()) == [] and credentials.get(rk.ITEM) is None


@pytest.mark.parametrize("secret", ["", "0" * 32, "f" * 31, "x" * 32, "../" * 8, "a" * 33, "0123456789abcdef0123456789abcdef"])
def test_a_ticket_the_guard_did_not_issue_opens_nothing(release, monkeypatch, secret):
    tmp, state, repo, rk, log = release
    approve(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == [] and credentials.get(rk.ITEM) is None
    assert spent(state) == []


def test_only_a_private_file_of_ours_in_the_ticket_folder_is_a_ticket(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    folder = state / "person" / run_approval.TICKET_DIR
    folder.mkdir(parents=True, exist_ok=True)
    outside = tmp / "outside"
    outside.write_text(secret)
    outside.chmod(0o600)
    open_file = Path(hand_over(state, secret))
    open_file.chmod(0o644)
    linked = folder / "linked"
    linked.symlink_to(outside)
    directory = folder / "dir"
    directory.mkdir()
    for named in (outside, open_file, linked, directory, folder / "missing", folder / ".." / "tickets" / ".." / "tickets" / "x"):
        monkeypatch.setenv(run_approval.TICKET_ENV, str(named))
        assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1, named
    assert outside.exists() and open_file.exists() and directory.exists()
    assert list(log.iterdir()) == []
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0


@pytest.mark.parametrize("name", ["POOLHOUSE_RUN_APPROVED", "POOLHOUSE_RUN_ALLOW", "POOLHOUSE_AGENT_APPROVED", "POOLHOUSE_GUARD",
                                  "POOLHOUSE_PUSH_MAIN", "POOLHOUSE_ALLOW_AGENT", "POOLHOUSE_PERSON", "POOLHOUSE_RUN_TICKET"])
def test_no_variable_opens_the_scripts_to_an_agent(release, monkeypatch, name):
    tmp, state, repo, rk, log = release
    approve(tmp, state, repo, *KEY)
    monkeypatch.setenv(name, "1" if name != "POOLHOUSE_GUARD" else "off")
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == [] and spent(state) == []


@pytest.mark.parametrize("name", ["PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH",
                                  "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH"])
def test_an_approved_run_starts_with_no_loader_or_interpreter_variable_set(release, monkeypatch, name, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(name, "")  # an empty value is as much a setting as any: the check reads the name
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert name in capsys.readouterr().err
    assert list(log.iterdir()) == [] and len(spent(state)) == 1  # the guard's exchange only: the ticket itself is unspent
    monkeypatch.delenv(name)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0


def test_a_person_at_a_terminal_may_have_such_variables(release, monkeypatch):
    _tmp, _state, _repo, rk, log = release
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("PYTHONPATH", "/somewhere")
    assert rk.main(["create"], (True, True), None) == 0
    assert (log / "argv.0").exists()


def test_a_ticket_belongs_to_the_session_that_was_asked(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, OTHER) == 1
    assert list(log.iterdir()) == []
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0


def test_a_ticket_for_one_command_runs_no_other_and_keeps_its_value(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    for command in (["rotate", "--write"], ["create"], ["show-public"], ["agent"], ["create", "--write", "--write"]):
        monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
        assert rk.main(command, NO_TERMINAL, SESSION) == 1, command
    assert list(log.iterdir()) == []
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0


def test_a_ticket_for_another_script_does_not_run_this_one(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *APPLY)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_a_ticket_dies_after_a_minute(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 2 * 60)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_a_change_to_the_script_after_the_ticket_voids_it(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    (repo / "scripts" / "release-key").write_text("#!/bin/sh\ncat /dev/null\n")
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_the_origin_moving_after_the_ticket_voids_it(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    git(repo, "remote", "set-url", "origin", "https://github.com/attacker/stolen.git")
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert list(log.iterdir()) == []


def test_the_environment_of_an_approved_run_cannot_redirect_the_key(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    evil, evil_log = recording_gh(tmp, "evil")
    for name, value in (("GH_REPO", "attacker/stolen"), ("GH_HOST", "attacker.example"), ("GH_TOKEN", "t0ken"),
                        ("GITHUB_TOKEN", "t0ken"), ("GH_ENTERPRISE_TOKEN", "t0ken"), ("HTTPS_PROXY", "http://attacker.example:3128"),
                        ("XDG_CONFIG_HOME", str(tmp / "evil-config")), ("GH_CONFIG_DIR", str(tmp / "evil-config")),
                        ("SSL_CERT_FILE", str(tmp / "evil.pem")), ("SSH_AUTH_SOCK", str(tmp / "evil.sock")),
                        ("HOME", str(tmp / "evil-home")), ("TMPDIR", str(tmp / "evil-tmp"))):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", f"{evil}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    assert list(evil_log.iterdir()) == []
    seen = dict(line.split("=", 1) for line in (log / "env.0").read_text().splitlines() if "=" in line)
    assert not {"GH_REPO", "GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "HTTPS_PROXY", "XDG_CONFIG_HOME",
                "SSL_CERT_FILE", "SSH_AUTH_SOCK", "TMPDIR", run_approval.TICKET_ENV} & set(seen)
    assert str(evil) not in seen["PATH"] and seen["CLAUDECODE"] == "1"
    assert seen["HOME"] == str(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve())


def test_gh_runs_with_a_configuration_folder_that_is_empty_and_ours_and_gone_after(release, monkeypatch, tmp_path):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    evil = tmp / "evil-config"
    evil.mkdir()
    (evil / "config.yml").write_text("http_unix_socket: /tmp/evil.sock\n")
    monkeypatch.setenv("GH_CONFIG_DIR", str(evil))
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    seen = dict(line.split("=", 1) for line in (log / "env.0").read_text().splitlines() if "=" in line)
    assert seen["GH_CONFIG_DIR"] != str(evil) and (log / "config.0").read_text() == ""
    assert not Path(seen["GH_CONFIG_DIR"]).exists()


def test_the_key_is_made_in_a_private_folder_that_is_gone_after(release, monkeypatch, tmp_path):
    tmp, _state, _repo, rk, _log = release
    private = tmp / "scratch"
    private.mkdir()
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("TMPDIR", str(private))
    monkeypatch.setattr("tempfile.tempdir", None)
    seen = []
    real = rk._run
    monkeypatch.setattr(rk, "_run", lambda argv, **kw: seen.append([(p.stat().st_mode & 0o777) for p in private.iterdir()]) or real(argv, **kw))
    assert rk.main(["create"], (True, True), None) in (0, 1)
    assert seen and seen[0] == [0o700]
    assert list(private.iterdir()) == []


def test_a_person_at_a_terminal_still_runs_with_no_ticket_and_the_old_arguments(release, monkeypatch):
    _tmp, _state, _repo, rk, log = release
    monkeypatch.delenv("CLAUDECODE")
    assert rk.main(["create"], (True, True), None) == 0
    assert (log / "argv.0").read_text().split() == ["secret", "set", "RELEASE_SIGNING_KEY", "--env", "release"]


def test_every_other_command_of_the_script_stays_a_persons_even_with_a_live_approval(release):
    tmp, state, repo, rk, log = release
    approve(tmp, state, repo, *KEY)
    for command in (["rotate"], ["rotate", "--write"], ["show-public"], ["agent"], ["create"]):
        assert rk.main(command, NO_TERMINAL, SESSION) == 1, command
    assert list(log.iterdir()) == [] and spent(state) == []


# -- the protection script ---------------------------------------------------------------------


def before_state():
    replies = {**GOOD}
    replies[f"repos/{REPO}/rulesets"] = [*GOOD[f"repos/{REPO}/rulesets"], {"id": 4, "name": "default"}]
    replies[f"repos/{REPO}/rulesets/4"] = ruleset("default", "branch", ["~DEFAULT_BRANCH"], [{"type": "update"}])
    replies[f"repos/{REPO}/environments/release/deployment-branch-policies"] = {"branch_policies": []}
    return replies


@pytest.fixture
def protect(world, monkeypatch):
    """The protection script on the world's checkout, a `gh` serving a repository, the agent marker set; ``(tmp, state, repo, module, log)``."""
    tmp, state, repo = world
    module = load("github-protection-apply", monkeypatch, repo)
    module.real_identity, module.real_check = module.identity, module.check
    log = serve(tmp, monkeypatch, before_state())
    monkeypatch.setattr(module, "identity", lambda _repo: (True, "agent identity: ready"))
    module.checked_with = []
    monkeypatch.setattr(module, "check", lambda repo, args: module.checked_with.append(module.environment_of_run()) or 0)
    monkeypatch.setattr(run_approval, "TRUSTED_DIRS", (str(tmp / "bin"), "/usr/bin", "/bin"))
    monkeypatch.setattr(run_approval, "_code_roots", lambda _found: (os.path.realpath(ROOT / "src"),))
    monkeypatch.setattr(run_approval, "KEPT", (*run_approval.KEPT, "GH_LOG", "GH_REPLIES", "GH_FAIL", "GH_NOAUTH"))
    monkeypatch.setenv("CLAUDECODE", "1")
    return tmp, state, repo, module, log


def writes(log):
    return [(m, p) for m, p, *_ in (line.split(" ", 2) for line in log.read_text().splitlines()) if m != "GET"]


def test_an_agent_may_look_at_what_the_apply_would_change_with_no_approval(protect, capsys):
    _tmp, state, _repo, ap, log = protect
    assert ap.main(["--repo", REPO], NO_TERMINAL, SESSION) == 0
    out = capsys.readouterr().out
    assert "== release environment" in out and "== remove the ruleset named default" in out
    assert writes(log) == [] and not (state / "github-protection").exists()


def test_an_agent_that_applies_with_no_approval_is_refused_before_any_request_and_told_how_to_ask(protect, capsys):
    _tmp, state, _repo, ap, log = protect
    assert ap.main(["--repo", REPO, "--apply"], NO_TERMINAL, SESSION) == 1
    assert log.read_text() == "" and not (state / "github-protection").exists()
    err = capsys.readouterr().err
    assert "for a person" in err and f"run-approval propose github-protection-apply --apply --repo {REPO}" in err


def test_an_approved_agent_run_applies_to_the_origins_repository_in_order(protect, monkeypatch):
    tmp, state, repo, ap, log = protect
    secret = ticket_for(tmp, state, repo, *APPLY)
    monkeypatch.setenv("GH_REPO", "attacker/stolen")
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert ap.main(["--apply", "--repo", REPO], NO_TERMINAL, SESSION) == 0
    (children,) = ap.checked_with
    assert children["PATH"].startswith(str(tmp / "bin")) and "GH_REPO" not in children and run_approval.TICKET_ENV not in children
    assert children["GH_CONFIG_DIR"] and not Path(children["GH_CONFIG_DIR"]).exists()
    base = f"repos/{REPO}"
    done = writes(log)
    assert done[:2] == [("PUT", f"{base}/environments/release"), ("POST", f"{base}/environments/release/deployment-branch-policies")]
    assert ("DELETE", f"{base}/rulesets/4") in done and done[-1] == ("PATCH", base)
    assert all(base in path for _m, path in done)
    assert len(list((state / "github-protection").glob("backup-*.json"))) == 1


def test_an_approval_of_the_apply_covers_neither_a_restore_nor_another_repository(protect, monkeypatch):
    tmp, state, repo, ap, log = protect
    secret = ticket_for(tmp, state, repo, *APPLY)
    for command in (["--apply", "--repo", "attacker/stolen"], ["--apply"], ["--restore", str(tmp / "b.json"), "--apply"],
                    ["--apply", "--repo", REPO, "--strict-tags"], ["--apply", "--repo", REPO, "--owner-review"]):
        monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
        assert ap.main(command, NO_TERMINAL, SESSION) == 1, command
    assert log.read_text() == ""


def test_the_release_environment_is_made_even_before_the_agent_identity_is(protect, monkeypatch, capsys):
    _tmp, _state, _repo, ap, log = protect
    monkeypatch.setattr(ap, "identity", lambda _repo: (False, "agent identity: not set up; run scripts/agent-identity setup"))
    monkeypatch.delenv("CLAUDECODE")
    assert ap.main(["--repo", REPO, "--apply"], (True, True)) == 1
    base = f"repos/{REPO}"
    assert writes(log) == [("PUT", f"{base}/environments/release"), ("POST", f"{base}/environments/release/deployment-branch-policies")]
    err = capsys.readouterr().err
    assert "scripts/agent-identity setup" in err and "release environment" in err


# -- the error message that named a flag the script does not have -----------------------------------


def test_the_hint_for_a_missing_environment_names_a_command_the_script_accepts(release, monkeypatch, capsys):
    tmp, _state, _repo, rk, _log = release
    failing = tmp / "failing"
    failing.mkdir()
    (failing / "gh").write_text('#!/bin/sh\necho "gh: HTTP 404 environments/release/secrets/public-key" >&2\nexit 1\n')
    (failing / "gh").chmod(0o755)
    monkeypatch.setattr(run_approval, "TRUSTED_DIRS", (str(failing), "/usr/bin", "/bin"))
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("PATH", f"{failing}{os.pathsep}{os.environ['PATH']}")
    assert rk.main(["create"], (True, True)) == 1
    err = capsys.readouterr().err
    command = next(part for part in err.split("`") if part.startswith("scripts/github-protection-apply"))
    assert "--identity-ready" not in err
    ap = load("github-protection-apply", monkeypatch, tmp)
    with pytest.raises(SystemExit) as helped:
        ap.main(["--help"])
    assert helped.value.code == 0
    flags = [word for word in shlex.split(command) if word.startswith("--")]
    assert flags and all(flag in capsys.readouterr().out for flag in flags)


# -- who can reach the writers --------------------------------------------------------------------


def test_the_code_an_approved_run_executes_is_guarded_as_the_person_record_code_is():
    for path in ("src/poolhouse/workspace/run_approval.py", "src/poolhouse/workspace/run_hook.py", "scripts/hooks/run-approval",
                 "scripts/release-key", "scripts/github-protection-apply", "scripts/github-protection", "scripts/agent-identity",
                 "src/poolhouse/credentials/__init__.py", "src/poolhouse/credentials/writing.py",
                 ".git/refs/remotes/origin/main", ".git/packed-refs"):
        assert protectedwrites.guarded(str(ROOT / path)), path
    assert not protectedwrites.guarded(str(ROOT / "scripts" / "test"))
    assert protectedwrites.guarded(str(ROOT / "src" / "poolhouse" / "credentials"))


@pytest.mark.parametrize("command", [
    "scripts/hooks/run-approval ticket", "python3 scripts/hooks/run-approval ticket", "sh scripts/hooks/run-approval", "scripts/hooks/run-approval",
    "python3 -c 'from poolhouse.workspace import run_hook'", "python3 -c 'from poolhouse.workspace.run_hook import issue'",
    "python3 -c 'from poolhouse.workspace import run_approval'", "python3 -m poolhouse.workspace.run_hook",
    "python3 -c 'import poolhouse.workspace.run_approval'"])
def test_an_agent_command_cannot_reach_the_ticket_or_the_writers(world, command):
    _, state, repo = world
    for env in ({}, {"POOLHOUSE_GUARD": "off"}, {"POOLHOUSE_GUARD": "off", **AGENT}):
        done = guard(command, state, repo, env=env)
        assert done.returncode == 2 and "person record" in done.stderr, (command, env)


def test_an_agent_may_ask_the_question_and_run_the_covered_commands(world):
    _, state, repo = world
    for command in ("scripts/hooks/run-approval propose release-key create --write", "scripts/release-key create --write",
                    f"scripts/github-protection-apply --repo {SLUG}"):
        assert guard(command, state, repo).returncode == 0, command


def test_the_real_scripts_are_not_mistaken_by_the_guard_for_something_that_names_the_person_record(world):
    _, state, _ = world
    for command in ("scripts/release-key show-public", "scripts/github-protection-apply --repo ownerlogin/poolhouse-test",
                    "scripts/hooks/run-approval propose release-key create --write"):
        done = guard(command, state, ROOT)
        assert done.returncode == 0, (command, done.stderr)


def test_only_guarded_code_writes_the_person_record():
    found = set()
    for path in sorted((ROOT / "src" / "poolhouse").rglob("*.py")):
        if "person_record" in path.read_text(encoding="utf-8") and path.name != "person_record.py":
            found.add(path.relative_to(ROOT).as_posix())
    assert found and all(protectedwrites.guarded(str(ROOT / one)) for one in found), found


def test_the_log_still_verifies_after_every_step_of_a_run(release, monkeypatch):
    tmp, state, repo, rk, _log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    assert person_store.open_log().verify().ok
    kinds = [(r["type"], r.get("kind") or r.get("state")) for r in rows(state) if r["type"] in ("authorization", "transition")]
    assert kinds == [("authorization", "run-script"), ("transition", "used"), ("authorization", "run-ticket"), ("transition", "used")]


# -- the interpreter and the code that runs --------------------------------------------------------


def test_the_question_names_the_interpreter_and_the_source_that_will_run(world):
    _, state, repo = world
    ask = asked(propose(repo, state, *KEY))
    built = git(repo, "rev-parse", "HEAD")[:12]
    assert f"Interpreter: {runtime.available().python}, isolated (-I), from the runtime built from {built}" in ask.question
    assert "Code: the scripts of this checkout; the Poolhouse of that runtime or of this checkout's src, and nothing else" in ask.question


def test_no_selected_runtime_means_no_question_and_no_other_interpreter(world):
    _, state, repo = world
    (runtime.directory() / "selected.json").unlink()
    done = propose(repo, state, *KEY)
    assert done.returncode != 0 and "no runtime is selected" in done.stderr and "does not pass" not in done.stderr


def test_a_selected_runtime_that_fails_its_verification_is_named_and_never_replaced(world):
    _, state, repo = world
    site = next((runtime.available().prefix / "lib").glob("python*/site-packages"))
    (site / "poolhouse" / "fleet" / "built-from").write_text("0" * 40)
    done = propose(repo, state, *KEY)
    assert done.returncode != 0 and "does not pass its verification" in done.stderr and "no runtime is selected" not in done.stderr


def test_a_pth_file_that_puts_another_poolhouse_first_makes_the_runtime_fail_its_verification(world, tmp_path):
    _, state, repo = world
    site = next((runtime.available().prefix / "lib").glob("python*/site-packages"))
    (tmp_path / "evil" / "poolhouse").mkdir(parents=True)
    (tmp_path / "evil" / "poolhouse" / "__init__.py").write_text("")
    (site / "zz-evil.pth").write_text(f"import sys; sys.path.insert(0, {str(tmp_path / 'evil')!r})\n")
    assert runtime.available() is None
    assert "does not pass its verification" in propose(repo, state, *KEY).stderr


def test_a_runtime_built_from_another_src_is_refused_and_the_one_of_this_commit_is_accepted(world, runtime_template):
    _, state, repo = world
    (repo / "src" / "code.py").write_text("x = 2\n")
    git(repo, "add", "src/code.py")
    git(repo, "commit", "-q", "-m", "change the src")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    done = propose(repo, state, *KEY)
    assert done.returncode != 0 and "whose src is not this checkout's src" in done.stderr
    select_runtime(runtime_template, repo)
    assert propose(repo, state, *KEY).returncode == 0


def test_a_commit_that_changes_only_other_folders_keeps_the_runtime_of_the_commit_before(world):
    _, state, repo = world
    (repo / "docs" / "note.md").write_text("another note\n")
    git(repo, "add", "docs/note.md")
    git(repo, "commit", "-q", "-m", "docs")
    assert propose(repo, state, *KEY).returncode == 0


def test_the_interpreter_is_part_of_what_was_approved(world, runtime_template):
    tmp, state, repo = world
    approve(tmp, state, repo, *KEY)
    select_runtime(runtime_template, repo)  # another runtime of the same commit: another interpreter
    assert ticket_of(guard("scripts/release-key create --write", state, repo)) == ""
    assert spent(state) == []


def test_an_approval_that_no_longer_holds_says_why_in_one_sentence(world):
    tmp, state, repo = world
    say(tmp, state, repo, "hello", prompt_id="bind")
    ask = asked(propose(repo, state, *KEY))
    (repo / "docs" / "note.md").write_text("a later note\n")
    git(repo, "add", "docs/note.md")
    git(repo, "commit", "-q", "-m", "a commit after the question")
    done = hooked(answer_of(repo, ask), state, repo)
    assert "the approval was not recorded: the checkout, the runtime or the repository is no longer what the question said" in done.stdout
    assert records(state, "run-script") == []
    declined = hooked(answer_of(repo, ask, ask.no), state, repo)
    assert "not recorded" not in declined.stdout


def test_the_question_lists_the_changed_files_bounded_and_with_every_odd_name_made_safe(world, runtime_template):
    _, state, repo = world
    odd = ["a b.py", "caf\N{LATIN SMALL LETTER E WITH ACUTE}.py", "new\nline.py", "q'uote.py", "back\\slash.py"]
    for name in [*odd, *(f"zz{i:02d}.py" for i in range(20))]:
        (repo / "src" / name).write_text("x = 1\n")
    git(repo, "add", "src")
    git(repo, "commit", "-q", "-m", "files with odd names")
    select_runtime(runtime_template, repo)
    question = asked(propose(repo, state, *KEY)).question
    line = next(line for line in question.splitlines() if line.startswith("Changed files in scripts and src ("))
    assert line.startswith("Changed files in scripts and src (25: ") and line.endswith(" and 5 more)")
    assert "A 'src/a b.py'" in line and "src/caf\\xe9.py" in line and "src/new\\nline.py" in line and "src/zz14.py" in line
    assert "src/zz15.py" not in line and "\N{LATIN SMALL LETTER E WITH ACUTE}" not in question
    assert "Guards changed since origin/main: none" in question


def module_at(name, file=None, path=None):
    made = types.ModuleType(name)
    if file:
        made.__file__ = file
    if path is not None:
        made.__path__ = path
    return made


def test_every_loaded_poolhouse_module_must_lie_under_the_approved_code(world, tmp_path):
    _, _state, repo = world
    found = run_approval.facts(repo, repo / "scripts" / "release-key", ["create", "--write"], str(repo))
    src, prefix = str((repo / "src").resolve()), str(runtime.available().prefix.resolve())
    assert run_approval._code_roots(found) == (src, prefix)
    fine = {"poolhouse": module_at("poolhouse", file=f"{src}/poolhouse/__init__.py"),
            "poolhouse.x": module_at("poolhouse.x", file=f"{prefix}/lib/python/site-packages/poolhouse/x.py"),
            "poolhouse.ns": module_at("poolhouse.ns", path=[f"{src}/poolhouse/ns"]),
            "json": module_at("json", file="/elsewhere/json.py"), "poolhousefake": module_at("poolhousefake", file="/elsewhere/f.py")}
    run_approval.verify_loaded(found, modules=fine)
    (repo / "src" / "linkmod.py").symlink_to(tmp_path / "elsewhere.py")
    for module in {
            "elsewhere": module_at("poolhouse.shadow", file=str(tmp_path / "evil" / "shadow.py")),
            "a sibling that shares the prefix of the string": module_at("poolhouse.sib", file=f"{src}evil/x.py"),
            "a way out through ..": module_at("poolhouse.up", file=f"{src}/../evil/x.py"),
            "a link out": module_at("poolhouse.link", file=f"{src}/linkmod.py"),
            "a namespace outside": module_at("poolhouse.ns2", path=["/elsewhere/ns2"]),
            "a namespace half outside": module_at("poolhouse.ns3", path=[f"{src}/ns3", "/elsewhere/ns3"]),
            "nothing to check": module_at("poolhouse.void")}.values():
        with pytest.raises(run_approval.Refused, match=re.escape(module.__name__)):
            run_approval.verify_loaded(found, modules={**fine, module.__name__: module})


def test_a_poolhouse_module_from_elsewhere_stops_the_run_before_anything_is_spent_or_made(release, monkeypatch, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setitem(sys.modules, "poolhouse.shadow", module_at("poolhouse.shadow", file=str(tmp / "evil" / "shadow.py")))
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert "poolhouse.shadow was loaded from" in capsys.readouterr().err
    assert list(log.iterdir()) == [] and credentials.get(rk.ITEM) is None and len(spent(state)) == 1
    monkeypatch.delitem(sys.modules, "poolhouse.shadow")
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0


def test_a_poolhouse_module_that_arrives_after_the_admission_stops_the_run_before_the_key_is_made(release, monkeypatch, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    real = run_approval.admit

    def admit_then_poison(*given, **more):
        approval = real(*given, **more)
        monkeypatch.setitem(sys.modules, "poolhouse.late", module_at("poolhouse.late", file=str(tmp / "evil" / "late.py")))
        return approval

    monkeypatch.setattr(run_approval, "admit", admit_then_poison)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert "poolhouse.late was loaded from" in capsys.readouterr().err
    assert list(log.iterdir()) == [] and credentials.get(rk.ITEM) is None


def test_a_poolhouse_module_that_arrives_after_the_admission_stops_the_protection_before_it_writes(protect, monkeypatch, capsys):
    tmp, state, repo, ap, log = protect
    secret = ticket_for(tmp, state, repo, *APPLY)
    real = run_approval.admit

    def admit_then_poison(*given, **more):
        approval = real(*given, **more)
        monkeypatch.setitem(sys.modules, "poolhouse.late", module_at("poolhouse.late", file=str(tmp / "evil" / "late.py")))
        return approval

    monkeypatch.setattr(run_approval, "admit", admit_then_poison)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert ap.main(["--apply", "--repo", REPO], NO_TERMINAL, SESSION) == 1
    assert "poolhouse.late was loaded from" in capsys.readouterr().err
    assert writes(log) == [] and not (state / "github-protection").exists()


def test_without_a_selected_runtime_the_script_says_why_and_a_person_needs_none(release, monkeypatch, capsys):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    (runtime.directory() / "selected.json").unlink()
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 1
    assert "no runtime is selected" in capsys.readouterr().err and list(log.iterdir()) == []
    monkeypatch.delenv("CLAUDECODE")
    assert rk.main(["create"], (True, True), None) == 0


def test_children_of_an_approved_run_read_and_write_no_bytecode(release, monkeypatch):
    tmp, state, repo, rk, log = release
    secret = ticket_for(tmp, state, repo, *KEY)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert rk.main(["create", "--write"], NO_TERMINAL, SESSION) == 0
    seen = dict(line.split("=", 1) for line in (log / "env.0").read_text().splitlines() if "=" in line)
    assert seen["PYTHONDONTWRITEBYTECODE"] == "1" and seen["PYTHONPYCACHEPREFIX"].endswith(".pyc")
    assert not Path(seen["PYTHONPYCACHEPREFIX"]).exists()


def test_the_python_children_of_the_protection_script_are_isolated_and_read_no_bytecode(protect, monkeypatch, capsys):
    tmp, state, repo, ap, _log = protect
    secret = ticket_for(tmp, state, repo, *APPLY)
    monkeypatch.setattr(ap, "identity", ap.real_identity)
    monkeypatch.setattr(ap, "AGENT_IDENTITY", repo / "scripts" / "agent-identity")
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert ap.main(["--apply", "--repo", REPO], NO_TERMINAL, SESSION) == 1  # the stand-in reports no identity and its flags
    shown = re.search(r"flags isolated=1 nobytecode=1 cache=(\S+?\.pyc)", capsys.readouterr().err)
    assert shown and not Path(shown[1]).exists()
    secret = ticket_for(tmp, state, repo, *APPLY)
    monkeypatch.setattr(ap, "identity", lambda _repo: (True, "agent identity: ready"))
    monkeypatch.setattr(ap, "check", ap.real_check)
    monkeypatch.setenv(run_approval.TICKET_ENV, hand_over(state, secret))
    assert ap.main(["--apply", "--repo", REPO], NO_TERMINAL, SESSION) == 0
    shown = re.search(r"flags isolated=1 nobytecode=1 cache=(\S+?\.pyc)", capsys.readouterr().out)
    assert shown and not Path(shown[1]).exists()


def test_a_persons_python_children_start_as_they_always_did(protect, monkeypatch, capsys):
    _tmp, _state, repo, ap, _log = protect
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setattr(ap, "identity", ap.real_identity)
    monkeypatch.setattr(ap, "AGENT_IDENTITY", repo / "scripts" / "agent-identity")
    ap.main(["--repo", REPO, "--apply"], (True, True))
    assert "flags isolated=0" in capsys.readouterr().err


# -- the guard: the diff base and the ticket ---------------------------------------------------------


def load_guard(monkeypatch):
    loader = importlib.machinery.SourceFileLoader("claude_bash_guard", str(HOOKS / "claude-bash-guard"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    return module


def test_the_guards_own_agent_check_keeps_a_subagent_from_asking_for_a_ticket(monkeypatch):
    guard_module = load_guard(monkeypatch)
    asked_for = []
    monkeypatch.setattr(writer, "issue", lambda text, cwd, **more: asked_for.append((text, cwd)) or "A FIXED LINE")
    given = {"command": "scripts/release-key create --write"}
    assert guard_module.run_stamped({"agent_id": "helper-1", "cwd": "/somewhere"}, given) is None
    assert asked_for == []
    assert guard_module.run_stamped({"cwd": "/somewhere"}, given) == {"command": "A FIXED LINE"}
    assert asked_for == [(given["command"], "/somewhere")]
    for other in ("scripts/release-key create --write; id", "cat scripts/release-key", "ls"):
        assert guard_module.run_stamped({"cwd": "/somewhere"}, {"command": other}) is None
    assert len(asked_for) == 1


def test_the_guard_gives_nothing_when_the_exchange_is_refused_for_any_reason(monkeypatch):
    guard_module = load_guard(monkeypatch)
    for error in (run_approval.Refused("x"), run_approval.NotARun("x"), person_auth.NotAuthorized("x"), OSError("x")):
        def refuse(*_given, error=error, **_more):
            raise error

        monkeypatch.setattr(writer, "issue", refuse)
        assert guard_module.run_stamped({"cwd": "/somewhere"}, {"command": "scripts/release-key create --write"}) is None


MOVES = ["git update-ref refs/remotes/origin/main HEAD", "git update-ref -d refs/remotes/origin/main", "git update-ref --stdin",
         "git update-ref remotes/origin/main HEAD", "git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main",
         "git fetch /tmp/evil main:refs/remotes/origin/main", "git fetch origin +refs/heads/*:refs/remotes/origin/*",
         "git -C /somewhere update-ref refs/remotes/origin/main abc", "git pull /tmp/evil main:remotes/origin/main",
         "git push . HEAD:refs/remotes/origin/main", "sh -c 'git update-ref refs/remotes/origin/main HEAD'",
         "env GIT_DIR=x git update-ref refs/remotes/origin/main HEAD", "cd /x && git fetch /tmp/e main:refs/remotes/origin/main"]


@pytest.mark.parametrize("command", MOVES)
def test_an_agent_cannot_move_the_ref_the_diff_of_the_question_is_taken_against(world, command):
    _, state, repo = world
    for env in ({}, {"POOLHOUSE_GUARD": "off"}, {"POOLHOUSE_GUARD": "off", **AGENT}):
        done = guard(command, state, repo, env=env)
        assert done.returncode == 2 and "remote-tracking refs" in done.stderr, (command, env)


@pytest.mark.parametrize("command", ["git fetch origin", "git fetch", "git fetch origin 0.3dev", "git update-ref refs/heads/side HEAD",
                                     "git symbolic-ref HEAD refs/heads/side", "git log refs/remotes/origin/main", "git pull --ff-only origin 0.3dev",
                                     "git push origin HEAD:refs/heads/side", "git diff refs/remotes/origin/main HEAD"])
def test_reading_the_remote_tracking_refs_and_moving_the_agents_own_refs_pass(world, command):
    _, state, repo = world
    assert guard(command, state, repo).returncode == 0, command


@pytest.mark.parametrize("command", ["echo abc > .git/refs/remotes/origin/main", "cp /tmp/x .git/packed-refs", "printf x >> .git/packed-refs",
                                     "mv /tmp/x .git/refs/remotes/origin/main", "tee .git/refs/remotes/origin/main", "rm .git/refs/remotes/origin/main",
                                     "sed -i s/a/b/ .git/packed-refs"])
def test_the_files_that_hold_the_remote_tracking_refs_are_not_an_agents_to_write(world, command):
    _, state, repo = world
    for env in ({}, {"POOLHOUSE_GUARD": "off", **AGENT}):
        done = guard(command, state, repo, env=env)
        assert done.returncode == 2, (command, env, done.stderr)
