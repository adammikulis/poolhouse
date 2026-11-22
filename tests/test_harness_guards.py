"""What the pre-push hook and the Bash and edit guards refuse an agent at once, whatever switch it sets, and what they leave alone."""

from __future__ import annotations

import ast
import json
import shlex
import subprocess
import sys

import pytest
from harness_support import HOOKS, ROOT, ZERO, bind, commit, environment, git, repository, tip

from poolhouse.workspace import harness_ancestry

AGENT = {"CLAUDECODE": "1"}
NAMED = harness_ancestry.under_harness()


@pytest.fixture
def world(tmp_path):
    repo = repository(tmp_path / "repo")
    git(repo, "checkout", "-q", "-b", "0.9dev")
    return tmp_path, tmp_path / "state", repo


def line(local, remote, ref="main", target_ref=""):
    return f"refs/heads/{ref} {local} refs/heads/{target_ref or ref} {remote}\n"


def release(repo):
    return line(tip(repo), tip(repo, "origin/main"))


def tag(repo):
    return line(tip(repo), ZERO, "v1").replace("refs/heads/v1", "refs/tags/v1")


def push(repo, state, text, **env):
    source = repo / ".git" / "push-input"
    source.write_text(text)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexec sh " + shlex.quote((HOOKS / "pre-push").as_posix()) + ' "$@"\n')
    hook.chmod(0o755)
    git(repo, "config", "--local", "poolhouse.hooks.repository", str(repo.resolve() / ".git"))
    return subprocess.run(["git", "hook", "run", "--to-stdin=" + str(source), "pre-push", "--", "origin",
                           "https://example.invalid/x.git"], cwd=repo, text=True, capture_output=True,
                          env=environment(state, PYTHON=sys.executable, **env))


def test_an_agents_push_of_main_is_refused_at_once_with_the_way_to_release(world):
    _, state, repo = world
    done = push(repo, state, release(repo), **AGENT)
    assert done.returncode != 0 and "pre-push: refused:" in done.stderr and "refs/heads/promote/<date>" in done.stderr


def test_the_opener_variable_no_longer_opens_main_for_an_agent(world):
    _, state, repo = world
    assert push(repo, state, release(repo), POOLHOUSE_PUSH_MAIN="yes", **AGENT).returncode != 0


def test_the_development_branch_is_pushed_with_no_question_and_nothing_recorded(world):
    _, state, repo = world
    held = tip(repo, "HEAD")
    commit(repo, "README.md")
    done = push(repo, state, line(tip(repo, "HEAD"), held, "0.9dev"), **AGENT)
    assert done.returncode == 0 and done.stderr == ""
    assert not (state / "person").exists()


@pytest.mark.skipif(NAMED, reason="this run is itself under a harness known by its name")
def test_a_push_from_a_terminal_of_your_own_meets_nothing(world):
    _, state, repo = world
    for text in (release(repo), tag(repo)):
        done = push(repo, state, text, CLAUDECODE="")
        assert done.returncode == 0 and done.stderr == ""


@pytest.mark.skipif(NAMED, reason="this run is itself under a harness known by its name")
def test_dropping_the_marker_variable_does_not_make_an_agent_a_terminal_of_your_own(world):
    """The test process stands in for the harness: its prompt hook recorded it, then the push is made with every marker dropped."""
    _, state, repo = world
    assert push(repo, state, release(repo)).returncode == 0, "nothing recorded: nothing above this process is a harness"
    assert bind(state).returncode == 0
    for text in (release(repo), tag(repo), line(tip(repo), ZERO, "work")):
        done = push(repo, state, text)
        assert done.returncode != 0 and "pre-push: refused:" in done.stderr, text
    held = tip(repo, "HEAD")
    commit(repo, "README.md")
    assert push(repo, state, line(tip(repo, "HEAD"), held, "0.9dev")).returncode == 0, "the development branch stays open"


def test_the_pre_push_hook_asks_the_ancestry_check_and_nothing_else_about_who_pushes():
    text = (HOOKS / "pre-push").read_text()
    assert "harness-ancestry" in text and "person-consume" not in text
    assert not (HOOKS / "person-consume").exists() and not (HOOKS / "claude-user-prompt").exists()


def guard(name, event, state, **env):
    return subprocess.run([sys.executable, str(HOOKS / name)], input=json.dumps(event), text=True,
                          capture_output=True, timeout=60, check=False, env=environment(state, **env))


def bash(command, state, cwd=ROOT, **env):
    return guard("claude-bash-guard", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)},
                 state, **env)


FORCES = ["git push --force origin main", "git push origin main --force-with-lease", "git push -f origin 0.9dev",
          "git push origin --tags", "git push origin --delete work", "git push --all origin", "git push --mirror"]
PREFIXES = ["", "env -u CLAUDECODE ", "env -i ", "env FOO=1 ", "unset CLAUDECODE; ", "sh -c 'X' ", "bash -c \"X\" ",
            "sudo ", "(", "git -C /tmp/x "]
OTHERS = ["poolhouse-workspace mint someone --role human", "cat ~/.poolhouse/person/harness-sessions.json",
          "python3 -c 'from poolhouse.workspace.harness_ancestry import bind'",
          "python3 -c 'from poolhouse.workspace import harness_ancestry'", "python3 -m poolhouse.workspace.harness_ancestry",
          f"python3 {HOOKS}/claude-harness-bind < /tmp/event.json", f"{HOOKS}/claude-harness-bind",
          "CLAUDE_CONFIG_DIR=/tmp/x python3 -c 1"]


def wrap(prefix, command):
    if prefix.endswith("-C /tmp/x "):
        return command.replace("git ", "git -C /tmp/x ", 1)
    if "X" in prefix:
        return prefix.replace("X", command)
    return prefix + command


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("command", FORCES)
def test_force_tag_delete_and_mirror_pushes_are_refused_through_every_prefix(tmp_path, prefix, command):
    for env in ({}, {"POOLHOUSE_GUARD": "off"}, {"POOLHOUSE_GUARD": "off", **AGENT}):
        done = bash(wrap(prefix, command), tmp_path / "state", **env)
        assert done.returncode == 2, (prefix, command, env)
        assert "No switch opens this rule" in done.stderr and "refused to an agent" in done.stderr


@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_push_of_main_with_no_accepted_review_is_refused_through_every_prefix_and_says_what_is_missing(tmp_path, prefix):
    for env in ({}, {"POOLHOUSE_GUARD": "off"}):
        done = bash(wrap(prefix, "git push origin main"), tmp_path / "state", cwd=tmp_path, **env)
        assert done.returncode == 2 and "land-review" in done.stderr and "Right now: " in done.stderr, (prefix, env)
        assert "person" not in done.stderr.lower().replace("person folder", "")


@pytest.mark.parametrize("command", OTHERS)
def test_the_person_folder_minting_and_hook_rules_hold_with_the_guard_switched_off(tmp_path, command):
    for env in ({}, {"POOLHOUSE_GUARD": "off"}, {"POOLHOUSE_GUARD": "off", **AGENT}):
        assert bash(command, tmp_path / "state", **env).returncode == 2, (command, env)


def test_the_guard_lets_an_agent_read_the_ancestry_answer_and_write_about_the_hooks(tmp_path):
    state = tmp_path / "state"
    assert bash(f"{HOOKS}/harness-ancestry; true", state).returncode == 0
    for text in ('git commit -m "docs: mention ~/.poolhouse/person/harness-sessions.json and harness_ancestry"',
                 "git add scripts/hooks/harness-ancestry scripts/hooks/claude-harness-bind",
                 "scripts/test all tests/test_harness_ancestry.py", "grep -rn 'git push origin main' docs/"):
        assert bash(text, state).returncode == 0, text


def test_the_switch_still_turns_off_a_soft_rule_and_nothing_else(tmp_path):
    state = tmp_path / "state"
    assert bash("git add -A", state).returncode == 2
    assert bash("git add -A", state, POOLHOUSE_GUARD="off").returncode == 0
    assert bash("git add -A", state, POOLHOUSE_GUARD="off", **AGENT).returncode == 0


def edit(path, state, **env):
    return guard("claude-edit-guard", {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x\n"},
                                       "cwd": str(ROOT)}, state, **env)


def test_nothing_writes_the_person_folder_through_a_path_a_link_or_a_moved_root(tmp_path):
    state = tmp_path / "state"
    (state / "person").mkdir(parents=True)
    link = tmp_path / "innocent"
    link.symlink_to(state / "person")
    targets = [state / "person" / "harness-sessions.json", link / "x", "/home/u/.poolhouse/person/ui.secret"]
    for path in targets:
        for env in ({}, {"POOLHOUSE_GUARD": "off", **AGENT}):
            done = edit(path, state, **env)
            assert done.returncode == 2 and "no switch opens this rule" in done.stderr, (path, env)
            assert "person folder" in done.stderr


@pytest.mark.parametrize("path", ["src/poolhouse/workspace/harness_ancestry.py", "scripts/hooks/claude-harness-bind",
                                  "scripts/hooks/harness-ancestry"])
def test_the_code_that_records_the_harness_is_not_an_agents_to_edit(tmp_path, path):
    for env in ({}, {"POOLHOUSE_GUARD": "off", **AGENT}):
        done = edit(ROOT / path, tmp_path / "state", **env)
        assert done.returncode == 2 and "no switch opens this rule" in done.stderr, (path, env)
    assert edit(ROOT / "src/poolhouse/workspace/notes.py", tmp_path / "state", **AGENT).returncode == 0


def test_an_ordinary_file_and_the_soft_switch_still_work(tmp_path):
    assert edit(tmp_path / "x.txt", tmp_path / "state").returncode == 0
    assert edit(tmp_path / "x.txt", tmp_path / "state", POOLHOUSE_GUARD="off", **AGENT).returncode == 0


def importers(module):
    """The src/poolhouse files that import a module called ``module``."""
    found = set()
    for path in sorted((ROOT / "src" / "poolhouse").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or "", *((node.module or "") + "." + a.name for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            if any(n.split(".")[-1] == module for n in names):
                found.add(path.relative_to(ROOT / "src" / "poolhouse").as_posix())
    return sorted(found)


def test_no_library_module_imports_the_code_that_records_the_harness():
    assert importers("harness_ancestry") == []


def test_the_scripts_that_reach_the_harness_record_are_the_hooks_only():
    reaching = sorted(p.name for p in HOOKS.iterdir() if p.is_file() and "harness_ancestry" in p.read_text(errors="replace"))
    assert reaching == ["claude-bash-guard", "harness-ancestry", "workspace_hook.py"]


def test_no_console_script_names_the_harness_record():
    assert "harness_ancestry" not in (ROOT / "pyproject.toml").read_text()


def test_the_people_and_approval_code_this_replaced_is_gone():
    for name in ("person_release", "person_auth", "person_record", "person_store", "person_hook", "person_intent",
                 "person_transcript", "person_targets", "person_view", "person_ancestry", "run_approval", "run_hook", "guard_patch"):
        assert not (ROOT / "src" / "poolhouse" / "workspace" / f"{name}.py").exists(), name
    assert not (HOOKS / "run-approval").exists()


def test_the_harness_bind_hook_is_wired_for_prompts_and_the_session_start_hook_binds():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())["hooks"]
    assert "claude-harness-bind" in json.dumps(settings["UserPromptSubmit"])
    assert "claude-user-prompt" not in json.dumps(settings)
    assert not [e for e in settings["PostToolUse"] if e.get("matcher") == "AskUserQuestion"]
    assert "claude-session-start" in json.dumps(settings["SessionStart"])
    assert "bind_harness" in (HOOKS / "claude-session-start").read_text()
