"""The Claude Code hook that refuses shells which should have been poolhouse commands.

Every rule it enforces was written after the thing it refuses cost real time, and the guard
itself has one property worth testing hard: it must not fire on text being *written*. It
blocked its own installation twice that way, and it cannot be repaired through the tool it
guards -- so a false positive is far more expensive here than elsewhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "claude-bash-guard"

BLOCKED, ALLOWED = 2, 0


def guard(command: str, tool: str = "Bash", **env: str) -> int:
    done = subprocess.run(
        [sys.executable, str(GUARD)], text=True, capture_output=True, env={**os.environ, **env},
        input=json.dumps({"tool_name": tool, "cwd": "/", "tool_input": {"command": command}}))
    assert done.returncode in (BLOCKED, ALLOWED), done.stderr
    if done.returncode == BLOCKED:
        assert "blocked:" in done.stderr, "a refusal has to say what to do instead"
    return done.returncode


@pytest.mark.parametrize("command, why", [
    ('until ! pgrep -f bench; do sleep 20; done', "a waiter that can never fire"),
    ('nohup python3 run.py &', "a background job nobody watches"),
    ('echo hi; nohup ./x &', "still a nohup after a semicolon"),
    ('/opt/homebrew/bin/llama-server --port 8080 -m x.gguf', "a server started by hand"),
    ("find ~/.cache -name '*.gguf'", "hunting for GGUFs"),
    ('curl -s http://127.0.0.1:8080/v1/chat/completions -d "{}"', "an ad-hoc model probe"),
    ('hf download unsloth/Qwen3.5-4B-GGUF', "a download by hand"),
    ('pkill -f llama-server', "a kill that leaves the lease behind"),
    ('SKIP_NAME_CHECK=1 git commit -m x', "the name hook is not an agent's to skip"),
    ('SKIP_BUDGETS=1 git commit -m x', "the budgets hook is not an agent's to skip"),
    ('git commit --no-verify -m x', "no hook at all"),
    ('git commit -n -m x', "the short spelling of the same"),
    ('git commit -anm x', "a bundled short flag"),
    ('git -C /w push --no-verify origin dev', "no pre-push hook"),
    ('env SKIP_BUDGETS=1 git commit -m x', "through env"),
    ('git add -A && git commit -m x', "staging everything sweeps in another agent's half-written work"),
    ('git add --all', "the long spelling of the same"),
    ('git add . ', "the dot is the same"),
    ('git add -u', "every tracked change is the same"),
    ('git commit -am "x"', "commit -a stages everything too"),
    ('git stash', "a bare stash lands on the stack every worktree shares"),
    ('git stash pop', "a pop can restore another agent's stash over this work"),
    ('git add x.py && git stash && git pull', "a bare stash in a chain"),
    ('git stash -u', "untracked files stashed with no tag"),
    ('git stash push -u', "a push with no tag cannot be found again"),
    ('git stash save x', "save is a push by another name"),
    ('git stash push -u -m "tag"', "a tagged push still lands on the shared stack"),
    ('git stash apply 9f7ee3e3', "an apply is no safer than a pop"),
    ('git stash drop stash@{0}', "a drop can delete another agent's entry"),
    ('git stash clear', "clear deletes every agent's entries"),
    ('git stash branch b', "branch pops the shared stack"),
    ('git stash create', "create builds a stash commit"),
    ('git stash store abc', "store pushes onto the shared stack"),
    ('git stash -u -m x', "untracked, with a message"),
    ('git -C /w stash', "past a -C path"),
    ('GIT_TRACE=1 git stash pop', "past a NAME=value prefix"),
    ('git stash list && git stash drop', "a read-only list then a drop"),
    ('git stash list; git stash pop', "a list then a pop"),
    ('git push origin main', "a push to main is the owner's to make"),
    ('git push origin 0.2dev:main', "the same, through a refspec"),
    ('git push --force origin main', "a forced push most of all"),
    ('git push --force origin 0.2dev', "a forced push rewrites the development branch"),
    ('git push -f origin 0.2dev', "the short spelling of the same"),
    ('git push --all origin', "every branch at once includes main"),
    ('git push --tags', "a tag is a release"),
    ('git push origin --delete work', "a deletion is not a push of the development branch"),
    ('git merge --ff-only work && git push origin main', "still main after a merge"),
    ('POOLHOUSE_PUSH_MAIN=yes git push origin main', "the variable an agent can set opens nothing"),
    ('git merge --ff-only 0.2dev && POOLHOUSE_PUSH_MAIN=yes git push origin main', "nor after a merge"),
    ('POOLHOUSE_PUSH_MAIN=yes git push --force origin main',
     "the opener opens a push of main, never a forced one"),
    ('POOLHOUSE_PUSH_MAIN=yes git push --all origin', "nor every branch at once"),
    ('POOLHOUSE_PUSH_MAIN=yes git push origin --delete main', "nor a deletion"),
    ('POOLHOUSE_PUSH_MAIN=1 git push origin main', "and only the word yes opens it"),
    ('FOO=1 git push --force origin main', "an assignment in front is still a forced push"),
    ('FOO=1 git add -A', "and still stages everything"),
    ('CUDA_VISIBLE_DEVICES=0 llama-server -m model.gguf', "and still starts a server by hand"),
    ('FOO=1 nohup poolhouse-bench run &', "and still backgrounds a job nobody watches"),
    ('./build/bin/llama-server -m x.gguf --port 8081', "a built binary run by hand"),
    ('sudo llama-server -m x.gguf', "through sudo"),
    ('exec llama-server', "no arguments at all"),
    ('cd x && llama-server -m a.gguf', "after a cd"),
    ('POOLHOUSE_BROKER_LOCAL=1 poolhouse-serve up m.gguf', "a private broker with no queue"),
    ('poolhouse-serve up m.gguf --anyway', "a flag that skipped the lease"),
    ('poolhouse-serve up m.gguf --context 256k --direct', "a direct start"),
    ('poolhouse-serve up m.gguf --no-broker', "a start that skips the broker"),
])
def test_the_shells_that_should_have_been_commands_are_refused(command, why):
    assert guard(command) == BLOCKED, why


@pytest.mark.parametrize("command", [
    'git status',
    'python3 -m pytest tests -q',
    'poolhouse-serve up model.gguf --port 8080',
    'poolhouse-serve up Qwen3.8-27B-UD-Q4_K_XL.gguf --context 256k --kv q8_0 --no-wait',
    'poolhouse-serve down Qwen3.8-27B',
    'ls /opt/homebrew/bin/llama-server',
    'poolhouse-bench sweep --serve foo.gguf --smoke',
    'pgrep -fl llama-server',
    'grep -rn llama-server src/',
    'ls ~/.cache/huggingface',
    'git add HANDOFF.md',
    'git add src/one.py tests/test_one.py && git commit -m "x"',
    'git add -p src/one.py',
    'git commit -m "x"',
    'git commit -m "docs: the guard refuses git add -A"',
    "grep -rn 'git push' docs/",
    'git push origin 0.2dev',
    'git merge --ff-only work && git push origin 0.2dev',
    'git stash list --format="%H %gs"',
    'git stash show --stat 9f7ee3e3',
    'git -C /w stash list',
    'git stash show -p',
    'git commit -m "fix: a bare git stash is refused"',
    'git log --oneline origin/main..main',
    'git rev-list --left-right --count origin/main...main',
    'PYTHONPATH=src python3 -m pytest tests -q -n 4',
    'POOLHOUSE_WINDOW_POSITION=3460,20 poolhouse-scrape look https://example.invalid',
])
def test_ordinary_work_is_not_refused(command):
    """A guard that fires on ordinary commands is a guard that gets switched off."""
    assert guard(command) == ALLOWED


@pytest.mark.parametrize("body", [
    'never nohup a job &',
    'SKIP_NAME_CHECK=1 is a person\'s to set, not an agent\'s',
    'do not run llama-server by hand',
    'no pkill -f llama-server either',
])
def test_writing_about_a_forbidden_shell_is_not_running_one(body):
    """The guard blocked its own installation twice: the command wrote documentation about a
    forbidden shell through a heredoc and the pattern matched the prose."""
    assert guard(f"cat > notes.md <<'MD'\n{body}\nMD") == ALLOWED


def test_a_heredoc_body_does_not_hide_a_real_command_after_it():
    """Skipping to the terminator must not swallow what follows it."""
    assert guard("cat > f <<'MD'\nharmless\nMD\npkill -f llama-server") == BLOCKED


def test_only_bash_is_guarded():
    assert guard("nohup python3 run.py &", tool="Read") == ALLOWED


def test_the_guard_can_be_switched_off_for_a_session():
    """POOLHOUSE_GUARD=off is the escape hatch; needing it means a rule is wrong, but it must work."""
    assert guard("pkill -f llama-server", POOLHOUSE_GUARD="off") == ALLOWED



def test_the_refusal_names_the_command_that_leases():
    done = subprocess.run(
        [sys.executable, str(GUARD)], text=True, capture_output=True,
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "llama-server -m x"}}))
    assert done.returncode == BLOCKED
    assert "poolhouse-serve up MODEL" in done.stderr and "poolhouse-serve down" in done.stderr


def test_the_stash_refusal_names_the_stash_free_ways():
    done = subprocess.run(
        [sys.executable, str(GUARD)], text=True, capture_output=True,
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git stash"}}))
    assert done.returncode == BLOCKED
    for text in ("shared by every worktree", "wip:", "git restore --source=<parent-sha>",
                 "git restore --source=HEAD", "git worktree add", "WIP commit first"):
        assert text in done.stderr


def test_the_guard_is_wired_to_the_bash_tool():
    """A refusal rule that nothing calls does not exist: settings.json runs this file before every Bash call."""
    settings = json.loads((GUARD.parents[2] / ".claude" / "settings.json").read_text())
    bash = [h for entry in settings["hooks"]["PreToolUse"] if entry["matcher"] == "Bash" for h in entry["hooks"]]
    assert any(h["command"].endswith("scripts/hooks/claude-bash-guard") for h in bash)


@pytest.mark.parametrize("command", ["SKIP_BUDGETS=1 git commit -m x", "git stash", "git stash pop", "git commit --no-verify -m x", "git commit -n -m x"])
def test_skipping_the_commit_hooks_stays_refused_with_the_guard_off(command):
    """The off switch is for the soft rules: no spelling of a hook skip is one of them."""
    assert guard(command, POOLHOUSE_GUARD="off") == BLOCKED
