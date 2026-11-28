"""The command guards leave ordinary work alone: rule files that do not load block nothing but the built-in hard rules, a search or
a message that mentions what a rule matches runs, the data of a heredoc is no command, and the guards answer in time under every
Python they may run under. Every command here is only judged, never run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse.guard import unboxed

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "scripts" / "hooks"
SYSTEM = Path("/usr/bin/python3")
PYTHONS = [sys.executable, *([str(SYSTEM)] if SYSTEM.exists() else [])]
"""This Python and, where there is one, the system's own, which may be older and runs a hook whose harness finds it first."""
KEY = "~/.poolhouse/person/key"


@pytest.fixture
def tree(tmp_path) -> Path:
    (tmp_path / "home").mkdir()
    (tmp_path / "work").mkdir()
    return tmp_path / "work"


def ask(hook: Path, tool: str, given: dict, tree: Path, python: str = sys.executable) -> subprocess.CompletedProcess:
    sent = {"tool_name": tool, "cwd": str(tree), "tool_input": given}
    env = {**os.environ, "HOME": str(tree.parent / "home"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([python, str(hook)], input=json.dumps(sent), text=True, capture_output=True, env=env, timeout=90)


def bash(command: str, tree: Path, python: str = sys.executable) -> subprocess.CompletedProcess:
    return ask(HOOKS / "claude-bash-guard", "Bash", {"command": command}, tree, python)


DAMAGE = {
    "a conflict in the shell reading": ("protectedshell.py", "conflict"),
    "a conflict in the protected writes": ("protectedwrites.py", "conflict"),
    "a conflict in the worktree rules": ("worktreerules.py", "conflict"),
    "no shell reading": ("protectedshell.py", "gone"),
    "no protected writes": ("protectedwrites.py", "gone"),
    "a name that is not there": ("protectedshell.py", "name"),
}


@pytest.fixture(params=list(DAMAGE.values()), ids=list(DAMAGE))
def damaged(request, tmp_path) -> Path:
    """A copy of the hooks and the rules with one rule file damaged; the hooks folder of it."""
    copy = tmp_path / "copy"
    shutil.copytree(HOOKS, copy / "scripts" / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "src" / "poolhouse").mkdir(parents=True)
    for name in ("worktreerules.py", "protectedwrites.py", "protectedshell.py"):
        shutil.copy(ROOT / "src" / "poolhouse" / name, copy / "src" / "poolhouse" / name)
    name, how = request.param
    target = copy / "src" / "poolhouse" / name
    if how == "gone":
        target.unlink()
    else:
        added = "<<<<<<< HEAD\nX = 1\n=======\nX = 2\n>>>>>>> other\n" if how == "conflict" else "X = not_defined_anywhere\n"
        target.write_text(target.read_text(encoding="utf-8") + added, encoding="utf-8")
    return copy / "scripts" / "hooks"


@pytest.mark.parametrize("name,tool,given", [
    ("claude-bash-guard", "Bash", {"command": "git merge --abort"}),
    ("claude-bash-guard", "Bash", {"command": "git status"}),
    ("claude-bash-guard", "Monitor", {"command": "tail -f x.log"}),
    ("claude-tool-guard", "Read", {"file_path": "x.py"}),
    ("claude-tool-guard", "Grep", {"pattern": "X", "path": "."}),
    ("claude-tool-guard", "SendMessage", {"to": "a", "message": "help"}),
    ("claude-tool-guard", "mcp__shell__run", {"command": "git status"}),
], ids=["abort a merge", "status", "monitor", "read", "grep", "message", "another shell"])
def test_rules_that_do_not_load_leave_the_session_its_tools(name, tool, given, damaged, tree) -> None:
    done = ask(damaged / name, tool, given, tree)
    assert done.returncode == 0, done.stderr[-600:]


@pytest.mark.redteam
@pytest.mark.parametrize("command", ["git push --force origin x", "git stash", f"cat {KEY}", "git commit --no-verify -m x"])
def test_rules_that_do_not_load_keep_the_built_in_hard_rules(command, damaged, tree) -> None:
    done = ask(damaged / "claude-bash-guard", "Bash", {"command": command}, tree)
    assert done.returncode == 2 and "could not judge" not in done.stderr, (done.returncode, done.stderr[-600:])


MENTIONS = [
    "rg 'CLAUDE_CONFIG_DIR=' scripts",
    "grep -n 'POOLHOUSE_BROKER_LOCAL=' tests/conftest.py",
    "grep -n \"CLAUDE_CONFIG_DIR=\" notes.txt",
    "rg \"pkill.*llama\" docs",
    "grep -rn 'llama-server' src | head -20",
    "grep -n 'git add -A' AGENTS.md",
    "grep -n 'git stash' docs/agent-policy.md",
    "grep -rn -- '--no-verify' scripts",
    "rg -n -e 'SKIP_BUDGETS=' scripts",
    "grep -E 'git push --force|--tags' docs/landing.md",
    "git log --grep='POOLHOUSE_BROKER_LOCAL=' --oneline",
    "git log --grep 'pkill llama' --oneline",
    "sed -n '/CLAUDE_CONFIG_DIR=/p' notes.txt",
    "awk '/pkill.*llama/' docs/agent-policy.md",
    "git commit -m 'docs: explain why llama-server is leased'",
    "git commit -m 'POOLHOUSE_BROKER_LOCAL='",
    "git commit --message=\"say why CLAUDE_CONFIG_DIR= is refused\"",
    "ls a && git log -S CLAUDE_CONFIG_DIR\\= --oneline",
]


@pytest.mark.parametrize("command", MENTIONS)
def test_a_search_or_a_message_that_mentions_what_a_rule_matches_runs(command, tree) -> None:
    done = bash(command, tree)
    assert done.returncode == 0, (command, done.stderr[-600:])


@pytest.mark.redteam
@pytest.mark.parametrize("command", ["noglob git stash", "! git commit --no-verify -m x", "{ git stash; }", "\\git add -A",
                                     "if true; then \"git\" stash; fi"])
def test_a_command_is_still_seen_past_the_words_before_it(command, tree) -> None:
    done = bash(command, tree)
    assert done.returncode == 2, (command, done.stderr[-600:])


BODY = "git reset --hard\ngit checkout main\nllama-server -m x\ngit add -A\ngit stash\nrm -rf /\ncat ~/.ssh/id_rsa"
"""Prose a heredoc carries: commands only in a shell that reads them as its own."""
PROSE_BODIES = {
    "after a quote in a quoted default": "echo \"${x:-'}\"\ncat > d.md <<'EOF'\n@@\nEOF",
    "after a hash in arithmetic": "(( n++ #1 ))\ncat > d.md <<'EOF'\n@@\nEOF",
    "a line that starts like the delimiter": "x=$(cat <<'M'\nMerge notes (draft)\n@@\nM\n); echo $x",
    "a python name that starts like it": "x=$(python3 <<'PY'\nPYTHON = len('a')\n'''\n@@\n'''\nPY\n); echo $x",
    "an example beside it": "out=$(cat <<'E'\nExample (one)\n@@\nE\n)",
    "a delimiter with a colon": "cat > d.md <<'END:DOC'\n@@\nEND:DOC",
    "a delimiter with a space": "cat > d.md <<'END DOC'\n@@\nEND DOC",
}


@pytest.mark.parametrize("command", list(PROSE_BODIES.values()), ids=list(PROSE_BODIES))
def test_a_heredoc_body_stays_data_where_the_reading_is_unsure(command, tree) -> None:
    shaped = command.replace("@@", BODY)
    done = bash(shaped, tree)
    assert done.returncode == 0, (shaped, done.stderr[-600:])
    ground = unboxed.Ground(str(tree), str(tree.parent / "home"), str(tree.parent / "home" / ".poolhouse"))
    if "python3" not in shaped:
        assert not unboxed.refusal("Bash", {"command": shaped}, ground), shaped


INLINE = ["python3 -c 'x = a[t-3]'", "ls '[z-a]'", "python3 -c \"\nprint(sys.argv[1])\nrows = v[x-1]\n\"", "find . -name '[z-a]' -delete",
          "cp a['z-a'] b", "echo " + "a" * 300, "cp x " + "a" * 300, "ls a**b"]


@pytest.mark.parametrize("python", PYTHONS)
@pytest.mark.parametrize("command", INLINE, ids=range(len(INLINE)))
def test_ordinary_inline_code_runs_under_every_python(command, python, tree) -> None:
    done = bash(command, tree, python)
    assert done.returncode == 0, (python, command[:100], done.stderr[-600:])


HOSTILE = {
    "a forced push before an unsure heredoc": "git push --force origin t\necho \"${x:-'}\"\ncat > o <<'EOF'\n" + "(" * 150_000
                                              + "\nEOF\n",
    "a forced push before a body left open": "git push --force origin t\ncat > o <<'EOF'\n" + "(" * 150_000 + "\nEOF \n",
    "interpreters named over and over": "(python" * 6000 + "; git push --force origin t",
    "one long stretch of operators": "(" * 100_000 + "; git push --force origin t",
    "wrappers over and over": "env -x " * 20_000 + "; git push --force origin t",
}


@pytest.mark.redteam
@pytest.mark.parametrize("python", PYTHONS)
@pytest.mark.parametrize("command", list(HOSTILE.values()), ids=list(HOSTILE))
def test_a_hostile_command_is_refused_in_time_under_every_python(command, python, tree) -> None:
    start = time.monotonic()
    done = bash(command, tree, python)
    assert done.returncode == 2, (python, done.returncode, done.stderr[-600:])
    assert time.monotonic() - start < 75  # the guard's own bound of 55 seconds and the start of two processes


@pytest.mark.redteam
@pytest.mark.parametrize("command", ["(python" * 6000, "echo " + "a " * 15_000, "echo ;" + "a" * 6000],
                         ids=["interpreters", "long", "unbroken"])
def test_where_no_clock_stops_a_match_a_long_text_is_refused_before_the_patterns(command, tree) -> None:
    """Windows and a Python before 3.11, asked here by saying so to the guard."""
    code = (f"import sys; sys.path.insert(0, {str(HOOKS)!r}); import rules_loader\n"
            "rules_loader.INTERRUPTS = False\n"
            "import importlib.machinery, importlib.util\n"
            f"loader = importlib.machinery.SourceFileLoader('guard', {str(HOOKS / 'claude-bash-guard')!r})\n"
            "guard = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader)); loader.exec_module(guard)\n"
            "print(guard.judge({'tool_name': 'Bash', 'cwd': '.', 'tool_input': {'command': sys.stdin.read()}}, False, "
            "rules_loader.load()))")
    done = subprocess.run([sys.executable, "-c", code], input=command, capture_output=True, text=True, timeout=60, cwd=tree,
                          env={**os.environ, "HOME": str(tree.parent / "home")})
    assert "finish in time" in done.stdout, (done.stdout[:300], done.stderr[-600:])


@pytest.mark.parametrize("python", PYTHONS)
def test_a_large_ordinary_write_runs_under_every_python(python, tree) -> None:
    lines = "".join(f"line {i}: some plain text, src/a/b{i}.py (see notes) | more; text\n" for i in range(2500))
    done = bash("cat > out.md <<'EOF'\n" + lines + "EOF\necho done", tree, python)
    assert done.returncode == 0, done.stderr[-600:]
