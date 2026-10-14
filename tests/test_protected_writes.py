"""An agent's shell may not write the guards, the hook settings, git's hooks, the authority registry or touch the person record."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import protectedwrites, worktreerules

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "claude-bash-guard"


@pytest.fixture
def tree(tmp_path):
    for name in ("scripts/hooks/pre-push", "scripts/hooks/claude-bash-guard", "scripts/hooks/claude-edit-guard",
                 ".claude/settings.json", ".git/config", ".git/hooks/pre-push", "src/poolhouse/x.py", "docs/x.md",
                 "src/poolhouse/workspace/person_store.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    (tmp_path / "l").symlink_to(tmp_path / ".claude")
    (tmp_path / "hl").symlink_to(tmp_path / ".git" / "hooks")
    return tmp_path


def why(command: str, tree: Path, released: bool = False, environ: dict | None = None) -> str:
    return protectedwrites.refusal(command, str(tree), lambda cwd: released, environ or {"HOME": str(tree / "home")})


WRITES_A_GUARD = [
    "sed -i 's/exit 0/exit 1/' scripts/hooks/pre-push",
    "sed -i.bak -e 's/a/b/' scripts/hooks/claude-bash-guard",
    "sed -i s/a/b/ scripts/hooks/claude-tool-guard",
    "perl -pi -e 's/a/b/' scripts/hooks/claude-edit-guard",
    "cat > scripts/hooks/claude-bash-guard",
    "echo x >> scripts/hooks/claude-edit-guard",
    "echo x >| scripts/hooks/pre-push",
    "echo x &> scripts/hooks/pre-push",
    "echo x | tee scripts/hooks/claude-edit-guard",
    "echo x | tee -a .claude/settings.json",
    "cp /tmp/x scripts/hooks/pre-push",
    "cp /tmp/x .claude/settings.json",
    "cp -f /tmp/x .claude/settings.local.json",
    "install -m 755 /tmp/x scripts/hooks/pre-push",
    "mv /tmp/x scripts/hooks/pre-push",
    "mv scripts/hooks/pre-push /tmp/gone",
    "ln -sf /tmp/x scripts/hooks/pre-push",
    "rm scripts/hooks/pre-push",
    "rm -rf scripts/hooks",
    "rm -rf .claude",
    "chmod 000 scripts/hooks/pre-push",
    "truncate -s0 .claude/settings.local.json",
    "dd if=/tmp/x of=scripts/hooks/pre-push",
    "echo '{\"env\":{\"POOLHOUSE_GUARD\":\"off\"}}' > .claude/settings.local.json",
    "printf x > .git/hooks/pre-push",
    "echo x >> .git/config",
    "cp /tmp/x .claude/agents/branch-worker.md",
    "cp /tmp/x src/poolhouse/workspace/person_store.py",
    "cp /tmp/x ~/.claude/settings.json",
    "echo x > ~/.gitconfig",
    "echo x > ~/.poolhouse/authority.json",
    "sed -i 's/person/delegated/' ~/.poolhouse/authority.json",
    "cd scripts/hooks && sed -i s/a/b/ pre-push",
    "cd scripts && echo x >> hooks/claude-edit-guard",
    "cd .claude; echo x > settings.json",
    "HOOKS=scripts/hooks; echo x > $HOOKS/pre-push",
    "echo x > scripts/hooks/pre-p*",
    "cp /tmp/x scripts/hooks/{pre-push,other}",
    "echo x > .claude/settings.local.js*",
    "sudo env FOO=1 nohup sed -i s/a/b/ scripts/hooks/pre-push",
    "command cp /tmp/x scripts/hooks/pre-push",
    "bash -c 'echo x > scripts/hooks/pre-push'",
    "sh -c \"sed -i s/a/b/ .claude/settings.json\"",
    "bash -c \"bash -c 'echo x > scripts/hooks/pre-push'\"",
    "eval \"echo x > scripts/hooks/pre-push\"",
    "echo $(echo x > scripts/hooks/pre-push)",
    "echo `cp /tmp/x scripts/hooks/pre-push`",
    "ls; true && echo x > scripts/hooks/pre-push",
    "git checkout origin/dev -- scripts/hooks/pre-push",
    "git restore --source=HEAD~3 scripts/hooks/pre-push",
    "git rm scripts/hooks/pre-push",
    "git -C . checkout -- .claude/settings.json",
    "find scripts/hooks -name pre-push -delete",
    "find .claude -type f -exec rm {} \\;",
    "find . -name '*.json' -delete",
    "find . -delete",
    "chmod -R 000 .",
    "chmod -x scripts/hooks/pre-push",
    "chmod 000 .claude/settings.json",
    "rm -rf .",
    "rm -r scripts",
    "cp /tmp/pre-push scripts/hooks/",
    "cp -t scripts/hooks /tmp/pre-push",
    "git checkout origin/dev -- .",
    "git restore --source=HEAD~3 .",
    "git restore -s origin/dev scripts",
    "git rm -r scripts/hooks",
    "mv scripts /tmp/scripts",
    "mv scripts/hooks /tmp/h",
    "mv . /tmp/everything",
    "mv /tmp/pre-push scripts/hooks/",
    "mv -t scripts/hooks /tmp/pre-push",
    "git mv scripts/hooks/pre-push /tmp/gone",
    "git mv /tmp/x scripts/hooks/pre-push",
    "git mv scripts/hooks /tmp/h",
    "find . -name '*.py' -exec rm {} +",
    "find src -name '*.py' -exec sed -i s/a/b/ {} +",
    "find . -name '*.py' -exec sh -c 'echo x' \\;",
    "find . -name person_store.py -delete",
    "find . -exec echo {} \\; -delete",
    "find . -name '*.py' -exec wc -l {} \\; -exec rm {} \\;",
    "find . -exec ./wc {} +",
    "find .claude -delete",
    "find docs .claude -delete",
    "find -L docs scripts -exec rm {} +",
    "find . \\( -name '*.json' -o -name '*.js' \\) -delete",
    "find . \\( -name '*.py' \\) -exec rm {} +",
    "find . -name x -fprint .claude/settings.json",
    "chmod -R 000 .",
    "chmod -R a-x .",
    "chmod -R 644 .",
    "chmod -R -x .",
    "chmod -R u-x scripts",
    "chmod -R u+w .claude/settings.json",
    "echo x > .Claude/settings.json",
    "echo x > .claude/SETTINGS.JSON",
    "cp /tmp/x Scripts/Hooks/Claude-Bash-Guard",
    "sed -i s/a/b/ .GIT/Hooks/Pre-Push",
    "mv /tmp/x SCRIPTS/hooks/PRE-PUSH",
    "echo x > ~/.Poolhouse/Authority.json",
    "echo x > l/settings.local.json",
    "cp /tmp/x l/agents/new.md",
    "echo x > hl/pre-commit",
    "cd l && echo x > settings.local.json",
    "tee l/commands/new.md",
    "echo x > l/settings.local.js*",
    "echo x > l/*.json",
]

TOUCHES_GIT_OR_GITHUB = [
    "git config core.hooksPath /dev/null",
    "git config --global core.hooksPath /tmp/hooks",
    "git config --local core.fsmonitor /tmp/x",
    "git config --unset core.hooksPath",
    "git -c core.hooksPath=/dev/null push origin dev",
    "git -c core.fsmonitor=/tmp/x status",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git push origin dev",
    "env GIT_CONFIG_GLOBAL=/tmp/x git push origin dev",
    "gh pr merge 12 --squash",
    "gh pr merge --auto 12",
    "gh api -X PUT repos/o/r/rulesets/5 -f enforcement=disabled",
    "gh api --method DELETE repos/o/r/branches/main/protection",
    "gh api repos/o/r/rulesets -f name=x",
    "gh api -X POST repos/o/r/actions/secrets/X -f value=y",
    "gh api graphql -f query='mutation { deleteBranchProtectionRule(input: {}) { clientMutationId } }'",
    "gh secret set PYPI_API_TOKEN",
    "gh ruleset delete 1",
    "gh repo edit --visibility public",
    "gh auth token",
    "gh alias set --shell x 'rm -rf /'",
    "gh workflow run release.yml",
    "gh release create v1",
    "curl -X PUT https://api.github.com/repos/o/r/rulesets/1 -d '{}'",
]

TOUCHES_A_SECRET = [
    "cat ~/.poolhouse/node/client/board/agent-aaaaaa.token",
    "ls ~/.poolhouse/person",
    "cat $HOME/.poolhouse/person/statements.log.key",
    "ls ~/.Poolhouse/PERSON",
    "cd ~/.poolhouse && cat node/person/x",
    "grep -r token ~/.poolhouse/node/client",
]

MENTIONS_A_GUARD_IN_CODE = [
    "python3 -c \"open('scripts/hooks/pre-push','w').write('exit 0')\"",
    "python3 -c \"import shutil; shutil.copy('/tmp/x', '.claude/settings.json')\"",
    "node -e \"require('fs').writeFileSync('.claude/settings.json','')\"",
    "ruby -e 'File.write(\".git/hooks/pre-push\", \"\")'",
    "perl -e 'open F, \">scripts/hooks/claude-bash-guard\"'",
    "python3 - <<'EOF'\nopen('scripts/hooks/pre-push','w').write('exit 0')\nEOF",
    "python3 <<EOF\nimport pathlib\npathlib.Path('.claude/settings.local.json').write_text('{}')\nEOF",
    "bash <<'EOF'\necho x > scripts/hooks/pre-push\nEOF",
]


@pytest.mark.parametrize("command", [*WRITES_A_GUARD, *TOUCHES_GIT_OR_GITHUB, *MENTIONS_A_GUARD_IN_CODE])
def test_a_shell_form_that_widens_access_is_refused_and_says_what_to_do(command, tree):
    assert why(command, tree), command


@pytest.mark.parametrize("command", TOUCHES_A_SECRET)
def test_the_person_record_and_the_tokens_are_not_read_or_listed(command, tree):
    assert why(command, tree, environ={"HOME": str(Path.home())}), command


def test_the_active_state_root_is_protected_where_it_is(tree):
    root = tree / "elsewhere"
    environ = {"HOME": str(tree / "home"), "POOLHOUSE_HOME": str(root)}
    for command in (f"cat {root}/node/client/b/x.token", f"echo x > {root}/authority.json", f"ls {root}/person"):
        assert why(command, tree, environ=environ), command
    assert not why(f"ls {root}/sentinel", tree, environ=environ)


def test_a_script_file_that_names_a_guard_is_refused_when_it_is_run(tree):
    (tree / "evil.py").write_text("open('scripts/hooks/pre-push', 'w').write('exit 0')\n", encoding="utf-8")
    (tree / "evil.sh").write_text("echo x > scripts/hooks/pre-push\n", encoding="utf-8")
    (tree / "fine.py").write_text("print('hello')\n", encoding="utf-8")
    assert why("python3 evil.py", tree) and why("bash evil.sh", tree) and why("sh ./evil.sh", tree)
    assert not why("python3 fine.py", tree)


def test_a_link_to_a_guard_is_a_guard(tree):
    (tree / "link").symlink_to(tree / "scripts" / "hooks" / "pre-push")
    assert why("echo x > link", tree) and why("sed -i s/a/b/ link", tree)


ALLOWED = [
    "cat scripts/hooks/claude-bash-guard",
    "grep -n exit scripts/hooks/pre-push",
    "sed -n 1,20p .claude/settings.json",
    "sed -n '1,5p' scripts/hooks/pre-push | head",
    "git log -p -- scripts/hooks/pre-push",
    "git diff HEAD -- .claude/settings.json",
    "git add scripts/hooks/pre-push",
    "scripts/hooks/person-consume propose",
    "python3 scripts/hooks/person-consume propose",
    "bash scripts/hooks/pre-push",
    "git status --short",
    "ls .claude scripts/hooks",
    "cp scripts/hooks/pre-push /tmp/pre-push.bak",
    "git commit -m 'fix: the pre-push hook and .git/hooks and ~/.poolhouse/person are named here'",
    "git config user.name someone",
    "git config --get core.hooksPath",
    "git config --list",
    "git push origin dev",
    "python3 -m pytest tests/test_pre_push.py",
    "scripts/test all tests/test_bash_guard.py",
    "echo hi > /tmp/x",
    "cat > tests/test_x.py <<'EOF'\nimport pytest\nEOF",
    "sed -i s/a/b/ src/poolhouse/x.py docs/x.md",
    "python3 - <<'EOF'\nprint(1)\nEOF",
    "python3 -c 'print(1)'",
    "node -e 'console.log(1)'",
    "gh pr create --title x --body y",
    "gh pr view 3",
    "gh api repos/o/r/pulls",
    "gh api -X POST repos/o/r/issues/3/comments -f body=hi",
    "gh run list",
    "curl -s https://api.github.com/repos/o/r",
    "poolhouse-workspace authority show",
    "echo 'see .claude/settings.json' > notes.txt",
    "ls ~/.poolhouse",
    "rm -rf /tmp/scratch",
    "mv a b",
    "find . -name '*.pyc' -delete",
    "find . -type f -name '*.orig' -exec rm {} +",
    "git checkout -- .",
    "git restore .",
    "git restore --staged .",
    "cp /tmp/new-hook scripts/hooks/",
    "cp /tmp/x scripts/hooks/claude-session-end",
    "chmod +x scripts/hooks/claude-session-end",
    "chmod -R u+w build",
    "chmod 755 .",
    "rm -rf build dist",
    "rm -rf .claude/worktrees/old",
    "cd src && ls",
    "find . -name '*.py'",
    "HOME_COPY=$HOME; echo $HOME_COPY",
    "bash -c 'echo hello'",
    "eval 'echo hello'",
    "find . -name '*.py' -exec wc -l {} +",
    "find src -name '*.py' -exec grep -l foo {} +",
    "find . -name '*.py' -exec wc -l {} \\;",
    "find . -type f -exec grep -l foo {} \\; -exec wc -l {} \\;",
    "find . -name '*.py' -exec wc -l {} + | tail -1",
    "find docs src/poolhouse -name '*.py' -exec wc -l {} +",
    "find -L docs -type f -exec wc -l {} +",
    "mv foo.txt .",
    "mv foo.txt src/",
    "mv -t src foo.txt",
    "mv foo.txt scripts/hooks/note.md",
    "git mv a.txt .",
    "chmod -R u+w .",
    "chmod -R +x .",
    "chmod -R 755 .",
    "chmod -R go-w .",
    "chmod -R u+w .claude scripts",
    "echo x > l/notes.txt",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_ordinary_commands_that_name_a_guard_or_the_person_record_are_not_refused(command, tree):
    assert not why(command, tree), command


def test_merging_a_pull_request_is_allowed_while_the_person_s_release_approval_is_live(tree):
    assert why("gh pr merge 12 --squash", tree, released=False)
    assert not why("gh pr merge 12 --squash", tree, released=True)
    assert why("gh api -X PUT repos/o/r/rulesets/5 -f x=y", tree, released=True)


def test_the_guard_script_refuses_the_bypasses_the_review_found(tree):
    for command in ("sed -i s/a/b/ scripts/hooks/pre-push", "git -c core.hooksPath=/dev/null push origin dev",
                    "gh pr merge 1", "bash -c 'cat > .claude/settings.local.json'"):
        done = subprocess.run([sys.executable, str(GUARD)], text=True, capture_output=True,
                              env={k: v for k, v in os.environ.items() if k != "POOLHOUSE_GUARD"},
                              input=json.dumps({"tool_name": "Bash", "cwd": str(tree), "tool_input": {"command": command}}))
        assert done.returncode == 2 and "blocked:" in done.stderr, command


def test_the_edit_rules_refuse_what_the_file_tools_may_not_write(tree):
    for path in (".claude/agents/branch-worker.md", ".claude/commands/x.md", ".git/hooks/pre-push", ".git/config",
                 "scripts/hooks/pre-push"):
        assert worktreerules.edit_refusal([path], str(tree)), path
    assert not worktreerules.edit_refusal(["src/poolhouse/x.py", "AGENTS.md"], str(tree))


def test_the_files_a_person_reviews_before_they_land_are_named_in_one_place():
    assert protectedwrites.touches_guards(["src/poolhouse/x.py", "docs/a.md"]) == []
    named = ["AGENTS.md", "CLAUDE.md", "src/poolhouse/authority.py", "src/poolhouse/person.py",
             "src/poolhouse/real_machine.py", "src/poolhouse/board/credentials.py", "scripts/agent_sandbox_profile.py",
             "src/poolhouse/workspace/authority_cli.py", ".claude/agents/a.md", ".claude/settings.json", "scripts/hooks/pre-push",
             "scripts/hooks/claude-edit-guard", "scripts/hooks/claude-tool-guard", "scripts/hooks/weakened-assertions",
             "src/poolhouse/workspace/person_release.py", ".github/workflows/ci.yml",
             ".github/CODEOWNERS", "scripts/github-protection", "scripts/land_board.py", "scripts/land",
             "src/poolhouse/worktreerules.py", "src/poolhouse/protectedwrites.py"]
    assert protectedwrites.touches_guards(["src/poolhouse/x.py", *named]) == named


def test_a_path_that_differs_only_in_case_is_the_same_guard_and_the_same_reviewed_file():
    spelled = [".Claude/settings.json", ".claude/SETTINGS.JSON", "Scripts/Hooks/Claude-Bash-Guard", "agents.md",
               "Src/Poolhouse/Authority.py", ".GITHUB/workflows/ci.yml"]
    assert protectedwrites.touches_guards(["src/poolhouse/x.py", *spelled]) == spelled
    assert protectedwrites.guarded("/r/.Claude/Settings.json") and protectedwrites.guarded("/r/.GIT/Hooks/pre-push")
    assert protectedwrites.secret("/home/me/.Poolhouse/Person/key")
    assert not protectedwrites.guarded("/r/.claude/worktrees/x")


def test_reading_a_command_line_costs_milliseconds(tree):
    import time
    commands = ["git status --short", "scripts/test all tests/test_authority.py --no-reuse",
                "git commit -q -F - <<'EOF'\nfix: x\nEOF", "python3 - <<'EOF'\nprint(1)\nEOF",
                "find . -name '*.pyc' -delete", "grep -rn authority src docs | head -50", "cp a b && rm -rf build dist"]
    began = time.perf_counter()
    for _ in range(20):
        for command in commands:
            why(command, tree)
    assert (time.perf_counter() - began) / (20 * len(commands)) < 0.02
