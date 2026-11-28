"""Tracked guard code is an agent's to edit in a linked worktree on its own branch, and nowhere its files run as a guard. A real
repository with a primary checkout, a sibling worktree and one nested as the harness makes them; the guards are asked as the hooks ask them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import devbranch, guardpaths, protectedwrites, worktreerules

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "scripts" / "hooks"
DEV = "9.9dev"
KEY = "scripts/release-key"
CODE = (KEY, "scripts/dev-branch-rule", "scripts/hooks/harness-ancestry", "scripts/hooks/claude-harness-bind",
        "src/poolhouse/credentials/__init__.py", "src/poolhouse/credentials/environment.py", "src/poolhouse/workspace/harness_ancestry.py")
QUIET = ("CLAUDE_PROJECT_DIR", "POOLHOUSE_DEV_BRANCH", "POOLHOUSE_GUARD", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


def git(cwd, *args):
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def write(tree, name, text):
    path = tree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(tree, name, text):
    write(tree, name, text)
    git(tree, "add", "--", name)
    git(tree, "commit", "-q", "-m", f"chore: {name}")
    return git(tree, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A primary checkout on the development branch with a sibling and a nested worktree, each on a branch of its own."""
    for name in QUIET:
        monkeypatch.delenv(name, raising=False)
    for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        monkeypatch.setenv(name, os.devnull)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    primary = tmp_path / "proj"
    primary.mkdir()
    git(primary, "init", "-q", "-b", DEV)
    for key, value in (("user.name", "Test Agent"), ("user.email", "agent@example.invalid"), ("commit.gpgsign", "false")):
        git(primary, "config", key, value)
    for name in (*CODE, "scripts/hooks/primary-only", "docs/a.md", ".gitignore"):
        write(primary, name, ".claude/\n" if name == ".gitignore" else "#!/bin/sh\ntrue\n")
        (primary / name).chmod(0o755)
    git(primary, "add", "-A")
    git(primary, "commit", "-q", "-m", "chore: seed")
    git(primary, "update-ref", f"refs/remotes/origin/{DEV}", "HEAD")
    sibling = tmp_path / "proj-fix"
    git(primary, "worktree", "add", "-q", "-b", "fix/guard", str(sibling))
    nested = primary / ".claude" / "worktrees" / "agent-x"
    git(primary, "worktree", "add", "-q", "-b", "fix/nested", str(nested))
    return SimpleNamespace(base=tmp_path, primary=primary, sibling=sibling, nested=nested)


def edit(path, cwd, hooks=HOOKS, **env):
    """Why the edit guard in ``hooks`` refuses a write to ``path`` asked from ``cwd``, empty when it lets it through."""
    event = {"tool_name": "Edit", "cwd": str(cwd), "tool_input": {"file_path": str(path), "old_string": "true", "new_string": "false"}}
    done = subprocess.run([sys.executable, str(hooks / "claude-edit-guard")], input=json.dumps(event), text=True, capture_output=True,
                          timeout=60, check=False, env={**{k: v for k, v in os.environ.items() if k not in QUIET}, **env})
    assert done.returncode in (0, 2), done.stderr
    return done.stderr if done.returncode == 2 else ""


def bash(command, cwd):
    return worktreerules.hard_bash_refusal(command, str(cwd))


def open_in(tree):
    """The control of a bypass test: the same file is open in an ordinary worktree."""
    assert edit(tree / KEY, tree) == ""
    assert bash(f"echo x > {KEY}", tree) == ""


@pytest.mark.parametrize("name", CODE)
@pytest.mark.parametrize("where", ["sibling", "nested"])
def test_tracked_guard_code_is_edited_in_a_linked_worktree_on_its_own_branch(repo, where, name):
    tree = getattr(repo, where)
    assert edit(tree / name, tree) == ""
    assert edit(name, tree) == ""
    assert edit(tree / name, repo.primary) == ""


def test_a_new_module_of_the_guarded_package_is_written_in_that_worktree(repo):
    assert edit(repo.sibling / "src/poolhouse/credentials/added.py", repo.sibling) == ""


@pytest.mark.parametrize("command", [
    f"sed -i s/true/false/ {KEY}", "echo x > src/poolhouse/credentials/environment.py", "git rm -q scripts/dev-branch-rule",
    "rm -rf src/poolhouse/credentials", "cp /tmp/x scripts/hooks/harness-ancestry", "mv scripts/dev-branch-rule docs/rule",
])
def test_the_shell_writes_guard_code_in_that_worktree(repo, command):
    assert bash(command, repo.sibling) == ""
    assert bash(command, repo.primary), command
    assert protectedwrites.refusal(command, str(repo.sibling), lambda _: "", dict(os.environ)), "no branch code for another caller"


def test_a_whole_worktree_is_still_not_removed_by_hand(repo):
    open_in(repo.sibling)
    assert bash(f"rm -rf {repo.sibling}", repo.primary)
    assert bash("find . -delete", repo.sibling)


@pytest.mark.parametrize("spelled", [
    "{primary}/" + KEY, "../proj/" + KEY, "scripts/../../proj/" + KEY, "{sibling}/../proj/" + KEY, "../PROJ/Scripts/Release-Key",
    "{primary}//scripts/./release-key", "../proj/src/poolhouse/credentials/environment.py",
])
def test_the_primary_checkout_stays_closed_however_its_path_is_spelled(repo, spelled):
    open_in(repo.sibling)
    path = spelled.format(primary=repo.primary, sibling=repo.sibling)
    assert "refused to an agent" in edit(path, repo.sibling)
    assert bash(f"echo x > {path}", repo.sibling), path


def test_a_nested_worktree_does_not_reach_the_primary_by_stepping_up(repo):
    open_in(repo.nested)
    assert edit("../../../" + KEY, repo.nested)
    assert bash(f"sed -i s/a/b/ ../../../{KEY}", repo.nested)


def test_a_link_from_the_worktree_into_the_primary_is_the_primary(repo):
    open_in(repo.nested)
    (repo.sibling / KEY).unlink()
    (repo.sibling / KEY).symlink_to(repo.primary / KEY)
    shutil.rmtree(repo.sibling / "src/poolhouse/credentials")
    (repo.sibling / "src/poolhouse/credentials").symlink_to(repo.primary / "src/poolhouse/credentials")
    (repo.sibling / "through").symlink_to(repo.primary / "docs")
    for name in (KEY, "src/poolhouse/credentials/environment.py", "src/poolhouse/credentials/new.py", f"through/../{KEY}"):
        assert edit(f"{repo.sibling}/{name}", repo.sibling), name
        assert bash(f"echo x > {name}", repo.sibling), name


def test_a_hard_link_to_a_guard_is_not_branch_code_and_the_shell_makes_none(repo):
    open_in(repo.nested)
    for command in (f"ln {repo.primary / KEY} spare", f"cp -l {repo.primary / KEY} spare", f"cp -al {repo.primary}/scripts spare"):
        assert bash(command, repo.sibling), command
    (repo.sibling / KEY).unlink()
    os.link(repo.primary / KEY, repo.sibling / KEY)
    assert edit(repo.sibling / KEY, repo.sibling)
    assert bash(f"echo x > {KEY}", repo.sibling)


def test_a_link_from_the_primary_into_a_worktree_keeps_that_file_closed(repo):
    open_in(repo.nested)
    shutil.rmtree(repo.primary / "src/poolhouse/credentials")
    (repo.primary / "src/poolhouse/credentials").symlink_to(repo.sibling / "src/poolhouse/credentials")
    assert edit(repo.sibling / "src/poolhouse/credentials/environment.py", repo.sibling)


@pytest.mark.parametrize("branch", ["main", "Main", "master", DEV, "9.9DEV", "0.3dev", "8.0dev"])
def test_a_worktree_on_main_or_a_development_branch_is_closed(repo, branch):
    open_in(repo.nested)
    git(repo.sibling, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    assert edit(repo.sibling / KEY, repo.sibling), branch
    assert bash(f"echo x > {KEY}", repo.sibling), branch


def test_a_detached_worktree_and_the_branch_named_as_development_are_closed(repo, monkeypatch):
    open_in(repo.nested)
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "fix/guard")
    assert edit(repo.sibling / KEY, repo.sibling, POOLHOUSE_DEV_BRANCH="fix/guard")
    assert bash(f"echo x > {KEY}", repo.sibling)
    monkeypatch.delenv("POOLHOUSE_DEV_BRANCH")
    git(repo.sibling, "checkout", "-q", "--detach")
    assert edit(repo.sibling / KEY, repo.sibling)


@pytest.mark.parametrize("points_at", [".git", ".git/worktrees/proj-fix"])
def test_a_folder_that_only_claims_to_be_a_worktree_is_closed(repo, points_at):
    open_in(repo.sibling)
    forged = repo.base / "forged"
    write(forged, KEY, "x\n")
    write(forged, ".git", f"gitdir: {repo.primary / points_at}\n")
    assert edit(forged / KEY, forged)
    assert bash(f"echo x > {KEY}", forged)


def test_a_worktree_pointed_at_the_primary_does_not_open_it(repo):
    open_in(repo.nested)
    write(repo.primary, "scripts/.git", f"gitdir: {repo.primary}/.git/worktrees/proj-fix\n")
    write(repo.primary, ".git/worktrees/proj-fix/gitdir", f"{repo.primary}/scripts/.git\n")
    (repo.base / "proj-link").symlink_to(repo.primary)
    for path in (repo.primary / KEY, repo.base / "proj-link" / KEY):
        assert edit(path, repo.nested), path
        assert bash(f"echo x > {path}", repo.nested), path


@pytest.mark.parametrize("path", [
    "{primary}/.git/config", "{primary}/.git/hooks/pre-push", "{primary}/.git/worktrees/proj-fix/config.worktree", "{home}/.gitconfig",
    "{home}/.poolhouse/authority.json", "{home}/.poolhouse/person/key", "{sibling}/docs/scripts/release-key",
    "{home}/.poolhouse/runtimes/a/lib/python3.12/site-packages/poolhouse/credentials/environment.py",
    "{home}/venv/lib/python3.12/site-packages/poolhouse/worktreerules.py",
])
def test_what_runs_or_holds_authority_stays_closed_from_a_worktree(repo, path):
    open_in(repo.sibling)
    named = path.format(primary=repo.primary, sibling=repo.sibling, home=repo.base / "home")
    assert edit(named, repo.sibling), named
    assert bash(f"echo x > {named}", repo.sibling), named


def running_in(tree):
    """A copy of this checkout's guards inside ``tree``, as a session started there runs them."""
    for name in ("claude-edit-guard", "rules_loader.py"):
        shutil.copy(HOOKS / name, tree / "scripts" / "hooks" / name)
    (tree / "src" / "poolhouse").mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "src" / "poolhouse").glob("*.py"):
        if source.stem in ("worktreerules", "protectedwrites", "guardpaths", "guardgit", "devbranch"):
            shutil.copy(source, tree / "src" / "poolhouse" / source.name)
    return tree / "scripts" / "hooks"


def test_the_tree_whose_hooks_run_is_closed_to_the_session_that_runs_them(repo):
    hooks = running_in(repo.sibling)
    assert edit(repo.nested / KEY, repo.sibling, hooks=hooks) == ""
    assert edit(repo.sibling / KEY, repo.sibling, hooks=hooks)
    assert edit(repo.sibling / "src/poolhouse/credentials/environment.py", repo.sibling, hooks=hooks)


def test_the_harness_project_folder_is_closed(repo, monkeypatch):
    open_in(repo.nested)
    for named in (repo.sibling, repo.sibling / "docs"):
        assert edit(repo.sibling / KEY, repo.sibling, CLAUDE_PROJECT_DIR=str(named)), named
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo.sibling))
    assert bash(f"echo x > {KEY}", repo.sibling)


def test_a_guard_script_an_agent_may_edit_is_read_before_it_runs(repo):
    assert bash(f"{KEY} --help", repo.sibling) == ""
    write(repo.sibling, KEY, "#!/bin/sh\necho x > .git/hooks/pre-push\n")
    for command in (KEY, f"bash {KEY}", f"sh -c './{KEY}'"):
        assert "names .git/hooks" in bash(command, repo.sibling), command


@pytest.fixture
def landed(repo):
    """A branch that changes guard code (`fix/guard`, also pushed) and one that changes none (`docs-only`)."""
    changed = commit(repo.sibling, KEY, "#!/bin/sh\nfalse\n")
    git(repo.primary, "update-ref", "refs/remotes/origin/fix/guard", changed)
    git(repo.primary, "worktree", "add", "-q", "-b", "docs-only", str(repo.base / "proj-docs"))
    commit(repo.base / "proj-docs", "docs/b.md", "b\n")
    git(repo.primary, "worktree", "remove", str(repo.base / "proj-docs"))
    repo.changed = changed
    return repo


BRINGS = [
    "git merge --ff-only fix/guard", "git merge origin/fix/guard", "git switch fix/guard", "git checkout fix/guard",
    "git pull . fix/guard", "git pull origin fix/guard", "git pull other " + DEV, f"git checkout fix/guard -- {KEY}",
    "git checkout fix/guard -- scripts", f"git restore --source=fix/guard -- {KEY}", f"git restore -s fix/guard {KEY}",
    "git read-tree -u --reset fix/guard", "git reset --hard fix/guard", "git cherry-pick fix/guard", "git rebase fix/guard",
    f"git update-index --cacheinfo 100755,{'a' * 40},{KEY}", "git update-index --index-info", "git apply change.patch",
    "bash -c 'git merge --ff-only fix/guard'", "git merge --ff-only $WHICH", "git merge --ff-only $(cat which)",
]


@pytest.mark.parametrize("command", BRINGS)
def test_unlanded_guard_code_is_not_brought_into_the_primary(landed, command):
    write(landed.primary, "change.patch", f"--- a/{KEY}\n+++ b/{KEY}\n")
    for text, cwd in ((command, landed.primary), (command.replace("git ", f"git -C {landed.primary} "), landed.sibling)):
        why = bash(text, cwd)
        assert "refused to an agent" in why and "guard" in why, (text, why)


def test_another_spelling_of_the_work_tree_does_not_bring_it_in_either(landed):
    for command in (f"git --work-tree={landed.primary} checkout fix/guard -- {KEY}",
                    f"git --work-tree {landed.primary} checkout fix/guard -- {KEY}",
                    f"GIT_WORK_TREE={landed.primary} git checkout fix/guard -- {KEY}",
                    f"cd {landed.primary} && git merge --ff-only fix/guard"):
        assert bash(command, landed.sibling), command


@pytest.mark.parametrize("command", [
    "git switch remote-only", "git checkout remote-only", "git merge", "git rebase", "git restore --sourc=fix/guard -- scripts",
    "git reset --har fix/guard", "git bisect start fix/guard HEAD", "gh pr checkout 12", "git -c core.worktree={primary} checkout fix/guard -- scripts",
    "export GIT_WORK_TREE={primary}; git checkout fix/guard -- scripts", "git read-tree -m HEAD fix/guard",
])
def test_a_revision_named_indirectly_is_read_all_the_same(landed, command):
    git(landed.primary, "update-ref", "refs/remotes/origin/remote-only", landed.changed)
    git(landed.primary, "config", f"branch.{DEV}.remote", ".")
    git(landed.primary, "config", f"branch.{DEV}.merge", "refs/heads/fix/guard")
    cwd = landed.sibling if "{primary}" in command else landed.primary
    assert bash(command.format(primary=landed.primary), cwd), command


def test_the_release_check_loads_no_code_from_the_folder_the_command_runs_in(tmp_path):
    for name, text in (("poolhouse/__init__.py", ""), ("poolhouse/workspace/__init__.py", ""),
                       ("poolhouse/workspace/release_check.py", "def refusal(*a, **k):\n    return ''\n")):
        write(tmp_path / "planted", name, text)
    assert worktreerules.release_refusal(str(tmp_path / "planted"))


@pytest.mark.parametrize("word", ["';touch pwned;'", "'$(touch pwned)'", "--upload-pack=touch", "'--output=pwned'", "'x..;touch pwned'"])
def test_a_hostile_revision_is_one_argument_and_starts_nothing_else(landed, word):
    assert bash("git merge --ff-only fix/guard", landed.primary), "the control: the revision is read"
    for command in (f"git merge --ff-only -- {word}", f"git merge {word} fix/guard", f"git switch {word}"):
        bash(command, landed.primary)
    assert not list(landed.base.rglob("pwned*"))


def test_the_guard_paths_of_a_pull_request_hold_the_code_open_on_a_branch_and_the_rules_that_open_it():
    for name in (*CODE, "scripts/lead-review", "scripts/hooks/claude-bash-guard", "src/poolhouse/guardpaths.py",
                 "src/poolhouse/guardgit.py", "src/poolhouse/protectedwrites.py", "src/poolhouse/worktreerules.py",
                 "src/poolhouse/workspace/release_check.py", ".claude/settings.json"):
        assert guardpaths.reviewed(name), name
    for name in ("src/poolhouse/graph/guard.py", "docs/agent-policy.md", "tests/test_guard_branches.py"):
        assert not guardpaths.reviewed(name), name


def test_the_default_development_branch_is_a_reviewed_one():
    assert devbranch.DEFAULT in guardpaths.PUBLISHED


def test_a_commit_staged_first_is_not_checked_out_afterwards(landed):
    git(landed.primary, "reset", "-q", "--mixed", landed.changed)
    for command in (f"git checkout -- {KEY}", f"git restore -- {KEY}", f"git checkout-index -f -- {KEY}", "git reset --hard"):
        assert bash(command, landed.primary), command


@pytest.mark.parametrize("command", [
    "git merge --ff-only docs-only", "git switch docs-only", "git switch -c work/new", "git checkout -- docs/a.md",
    "git checkout docs-only -- docs/b.md", "git pull", f"git pull --ff-only origin {DEV}", "git merge origin/" + DEV,
    "git reset HEAD -- docs/a.md", f"git restore -- {KEY}", "git status", "git log fix/guard", "git diff fix/guard",
    "git apply --check change.patch",
])
def test_git_that_brings_no_unlanded_guard_code_into_the_primary_runs(landed, command):
    git(landed.primary, "config", "branch." + DEV + ".remote", ".")
    git(landed.primary, "config", "branch." + DEV + ".merge", f"refs/remotes/origin/{DEV}")
    assert bash("git merge --ff-only fix/guard", landed.primary), "the control: unlanded guard code is not brought in"
    assert bash(command, landed.primary) == "", command


def test_guard_code_that_landed_on_development_is_brought_into_the_primary_and_an_older_one_is_not(landed):
    assert bash("git merge --ff-only origin/fix/guard", landed.primary)
    git(landed.primary, "update-ref", f"refs/remotes/origin/{DEV}", landed.changed)
    for command in (f"git merge --ff-only origin/{DEV}", "git merge --ff-only fix/guard"):
        assert bash(command, landed.primary) == "", command
    git(landed.primary, "merge", "-q", "--ff-only", "fix/guard")
    git(landed.primary, "branch", "older", "HEAD~1")
    assert bash("git switch older", landed.primary), "a switch back to older guard code changes what runs"


def test_a_worktree_that_runs_no_guard_takes_any_branch(landed):
    assert bash("git merge --ff-only fix/guard", landed.primary)
    assert bash("git merge --ff-only fix/guard", landed.nested) == ""
    assert bash("git cherry-pick fix/guard", landed.nested) == ""


def test_the_project_folder_of_a_harness_takes_no_unlanded_guard_code(landed, monkeypatch):
    assert bash("git merge --ff-only fix/guard", landed.nested) == ""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(landed.nested))
    assert bash("git merge --ff-only fix/guard", landed.nested)
