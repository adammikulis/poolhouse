"""Red-team: the git processes that run over a tree something else wrote. The tree hash of a test run, the git of a delegate's reader and
the git that gives a confined session its history are each fed hostile file names, configuration and variables, and must read them as data."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from poolhouse import delegate_candidate
from poolhouse.activity.gate import tree_hash
from poolhouse.isolation import history

pytestmark = pytest.mark.redteam
NAMES = ["--exec=touch from-an-option", "-n", "$(touch from-a-substitution)", "; touch from-a-separator", "`touch from-backticks`",
         "two\nlines", "quote'\"d", ":(exclude)a.txt"]


def posix_files(test):
    return pytest.mark.skipif(os.name == "nt", reason="NTFS takes no such file name, and a folder's mode does not stop a write there")(test)


def untried_on_windows(test):
    return pytest.mark.skipif(os.name == "nt", reason="never run on Windows, though no limit of that system is known: the trap programs are "
                                                      "shell scripts that git has started only on POSIX systems here")(test)


def git(where: Path, *words: str) -> str:
    done = subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                           *words], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def repository(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "chore: seed")
    return root


class Trap:
    """A program a hostile configuration names: it records that it ran and passes its input on."""

    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.ran = folder / "ran"
        self.program = folder / "program"
        self.program.write_text(f'#!/bin/sh\necho "$0 $*" >> "{self.ran}"\ncat 2>/dev/null\n')
        self.program.chmod(0o755)


def made_by_a_name(folder: Path) -> list[str]:
    """The files under ``folder`` that only running one of `NAMES` as a command would have made."""
    return sorted(str(found) for found in folder.rglob("from-*"))


def from_nothing(root: Path, index: Path) -> str:
    """The tree git writes for the working copy starting from no index at all."""
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    subprocess.run(["git", "-C", str(root), "add", "-A"], env=env, check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(root), "write-tree"], env=env, check=True, capture_output=True, text=True).stdout.strip()


@posix_files
def test_a_working_tree_of_hostile_names_is_hashed_as_data(tmp_path, monkeypatch):
    root = repository(tmp_path / "pro ject; touch from-the-root $(touch from-the-root-too)")
    for name in NAMES:
        (root / name).write_text(name)
    (root / ".gitattributes").write_text("* filter=not-configured diff=not-configured merge=not-configured\n")
    expected = from_nothing(root, tmp_path / "long-way")
    index = (root / ".git" / "index").read_bytes()
    monkeypatch.chdir(tmp_path)
    assert tree_hash(root) == expected
    assert set(git(root, "ls-tree", "--name-only", "-z", expected).split("\0")) >= set(NAMES)
    assert (root / ".git" / "index").read_bytes() == index
    assert made_by_a_name(tmp_path) == []


@untried_on_windows
@pytest.mark.parametrize("key", ["core.fsmonitor", "filter.x.clean", "filter.x.process"])
@pytest.mark.parametrize("state", ["same", "moved", "ignored"])
def test_a_nested_repository_the_index_holds_runs_nothing_it_configures_while_the_tree_is_hashed(tmp_path, key, state):
    root = repository(tmp_path / "project")
    inner = repository(root / "nested")
    git(root, "add", "nested")
    git(root, "commit", "-q", "-m", "chore: link")
    if state == "ignored":
        (root / ".gitignore").write_text("nested\n")
    if state == "moved":
        (inner / "b.txt").write_text("b\n")
        git(inner, "add", "b.txt")
        git(inner, "commit", "-q", "-m", "feat: move on")
    head = git(inner, "rev-parse", "HEAD")
    trap = Trap(tmp_path / "trap")
    git(inner, "config", key, str(trap.program))
    (inner / ".git" / "info").mkdir(exist_ok=True)
    (inner / ".git" / "info" / "attributes").write_text("* filter=x\n")
    time.sleep(1.1)   # past the second the index was written in, so git reads the rewritten file's content
    (inner / "a.txt").write_text("b\n")
    found = tree_hash(root)
    assert not trap.ran.exists(), trap.ran.read_text()
    assert f"160000 commit {head}\tnested" in git(root, "ls-tree", found, "nested"), "the tree names the commit the nested checkout is at"


def linked(root: Path, name: str = "nested") -> Path:
    """A repository at ``root / name`` that the commit of ``root`` holds as a nested repository."""
    inner = repository(root / name)
    git(root, "--literal-pathspecs", "add", "--", name)
    git(root, "commit", "-q", "-m", "chore: link")
    return inner


def armed(inner: Path, folder: Path, *keys: str) -> dict[str, Trap]:
    """A trap of its own under each of ``keys`` in the configuration of ``inner``, whose attributes send every file through filter and
    driver ``x``, and one of its files rewritten so that git has to read it again."""
    traps = {}
    for key in keys:
        traps[key] = Trap(folder / key)
        git(inner, "config", key, str(traps[key].program))
    (inner / ".git" / "info").mkdir(exist_ok=True)
    (inner / ".git" / "info" / "attributes").write_text("* filter=x diff=x\n")
    time.sleep(1.1)   # past the second the index was written in, so git reads the rewritten file's content
    (inner / "a.txt").write_text("b\n")
    return traps


def ran(traps: dict[str, Trap]) -> set[str]:
    return {key for key, trap in traps.items() if trap.ran.exists()}


def by_git_itself(root: Path, index: Path) -> str:
    """The tree plain ``git add -A`` writes for the working copy of ``root`` from a copy of its index, asked before anything is armed."""
    shutil.copy2(root / ".git" / "index", index)
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    subprocess.run(["git", "-C", str(root), "add", "-A"], env=env, check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(root), "write-tree"], env=env, check=True, capture_output=True, text=True).stdout.strip()


SPARSE = {"setting": None, "everything": "/*", "nested-outside": "/a.txt"}


@posix_files
@pytest.mark.parametrize("sparse", SPARSE)
def test_a_sparse_checkout_does_not_open_a_nested_repository_while_the_tree_is_hashed(tmp_path, monkeypatch, sparse):
    root = repository(tmp_path / "project")
    names = ["nested", "[a]*", ":(exclude)a.txt", "-n", "two words"]
    inners = [linked(root, name) for name in names]
    (root / "deep").mkdir()
    (root / "deep" / "held.txt").write_text("held\n")
    git(root, "add", "deep")
    git(root, "commit", "-q", "-m", "chore: deep")
    if SPARSE[sparse] is None:
        git(root, "config", "core.sparseCheckout", "true")
        (root / ".git" / "info").mkdir(exist_ok=True)
        (root / ".git" / "info" / "sparse-checkout").write_text("/*\n")
    else:
        git(root, "sparse-checkout", "set", "--no-cone", SPARSE[sparse])
    (root / "a.txt").write_text("changed\n")
    (root / "deep").mkdir(exist_ok=True)
    (root / "deep" / "held.txt").write_text("changed too\n")
    expected = by_git_itself(root, tmp_path / "long-way")
    traps = {name: armed(inner, tmp_path / f"trap{at}", "core.fsmonitor", "filter.x.clean")
             for at, (name, inner) in enumerate(zip(names, inners, strict=True))}
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    found = tree_hash(root / "deep")
    assert {name: ran(each) for name, each in traps.items()} == {name: set() for name in names}
    assert found == expected, "the tree is the one git writes for the whole checkout, asked from any folder of it"


@untried_on_windows
@pytest.mark.parametrize("pattern", ["vendor/", "vendor", "vendor/*", "/vendor/lib", "lib", "*"])
def test_a_nested_repository_under_an_ignored_path_is_hashed_and_stays_closed(tmp_path, pattern):
    root = repository(tmp_path / "project")
    (root / "vendor").mkdir()
    inner = linked(root, "vendor/lib")
    (root / ".gitignore").write_text(pattern + "\n")
    (root / "a.txt").write_text("changed\n")
    expected = by_git_itself(root, tmp_path / "long-way")
    traps = armed(inner, tmp_path / "trap", "core.fsmonitor", "filter.x.clean")
    found = tree_hash(root)
    assert ran(traps) == set()
    assert found == expected


@untried_on_windows
@pytest.mark.parametrize("index", ["nothing-else", "cone", "sparse-index", "split", "unmerged"])
def test_a_nested_repository_stays_closed_however_the_index_holds_it(tmp_path, index):
    root = repository(tmp_path / "project")
    for folder in ("keep", "out"):
        (root / folder).mkdir()
        (root / folder / "held.txt").write_text("held\n")
    git(root, "add", "keep", "out")
    git(root, "commit", "-q", "-m", "chore: folders")
    inner = linked(root, "out/nested")
    if index == "nothing-else":
        git(root, "rm", "-q", "-r", "a.txt", "keep", "out/held.txt")
    elif index in ("cone", "sparse-index"):
        git(root, "sparse-checkout", "set", "--cone", "--sparse-index" if index == "sparse-index" else "--no-sparse-index", "keep")
    elif index == "split":
        git(root, "update-index", "--split-index")
    else:
        commit = git(inner, "rev-parse", "HEAD")
        rows = "".join(f"160000 {commit} {stage}\tout/nested\n" for stage in (1, 2, 3))
        subprocess.run(["git", "-C", str(root), "update-index", "--index-info"], input=f"0 {'0' * 40}\tout/nested\n{rows}", text=True,
                       check=True)
    assert (inner / "a.txt").exists()
    expected = by_git_itself(root, tmp_path / "long-way")
    traps = armed(inner, tmp_path / "trap", "core.fsmonitor", "filter.x.clean")
    found = tree_hash(root)
    assert ran(traps) == set()
    assert found == expected


@untried_on_windows
@pytest.mark.parametrize("name", ["nésted", "über/nésted"])
def test_a_nested_repository_is_found_whatever_code_page_text_is_read_in(tmp_path, monkeypatch, name):
    root = repository(tmp_path / "project")
    (root / name).parent.mkdir(exist_ok=True)
    inner = linked(root, name)
    expected = by_git_itself(root, tmp_path / "long-way")
    traps = armed(inner, tmp_path / "trap", "core.fsmonitor", "filter.x.clean")
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252")   # how Windows reads a child's output with no encoding named
    found = tree_hash(root)
    assert ran(traps) == set()
    assert found == expected


@pytest.mark.parametrize("store", ["writable", "sealed"])
def test_a_checkout_under_a_name_outside_ascii_is_hashed_whatever_code_page_text_is_read_in(tmp_path, monkeypatch, store):
    if store == "sealed" and os.name == "nt":
        pytest.skip("a folder's mode does not stop Windows writing into it")
    root = repository(tmp_path / "José")
    linked(root, "nésted")
    (root / "new.txt").write_text("not yet an object\n")
    shutil.copytree(root, tmp_path / "copy", symlinks=True)
    expected = by_git_itself(tmp_path / "copy", tmp_path / "long-way")
    objects = root / ".git" / "objects"
    folders = [objects, *(each for each in objects.rglob("*") if each.is_dir())] if store == "sealed" else []
    for folder in folders:
        folder.chmod(0o500)
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252")
    try:
        assert tree_hash(root) == expected
    finally:
        for folder in folders:
            folder.chmod(0o700)


def test_a_nested_repository_with_no_commit_to_name_gives_no_tree_hash(tmp_path):
    root = repository(tmp_path / "project")
    inner = repository(root / "nested")
    git(root, "add", "nested")
    git(root, "commit", "-q", "-m", "chore: link")
    git(inner, "symbolic-ref", "HEAD", "refs/heads/never-made")
    assert tree_hash(root) == ""


@posix_files
def test_a_store_nothing_may_write_takes_new_objects_aside_whatever_the_checkouts_path_says(tmp_path, monkeypatch):
    root = repository(tmp_path / "repo:elsewhere;$(touch from-the-path)")
    (root / "new.txt").write_text("not yet an object\n")
    expected = from_nothing(repository_copy(root, tmp_path / "copy"), tmp_path / "long-way")
    objects = root / ".git" / "objects"
    held = sorted(objects.rglob("*"))
    folders = [objects, *(each for each in held if each.is_dir())]
    for folder in folders:
        folder.chmod(0o500)
    monkeypatch.chdir(tmp_path)
    try:
        assert tree_hash(root) == expected
        assert sorted(objects.rglob("*")) == held, "nothing is written into a store that may not be written"
    finally:
        for folder in folders:
            folder.chmod(0o700)
    assert made_by_a_name(tmp_path) == []


def repository_copy(root: Path, into: Path) -> Path:
    subprocess.run(["cp", "-R", str(root), str(into)], check=True)
    return into


def hostile_project(tmp_path: Path, monkeypatch) -> tuple[Path, Trap]:
    """A project whose own configuration, the account's configuration and the caller's variables all name a program, and whose names and branch
    are shell text."""
    root = repository(tmp_path / "project $(touch from-the-root)")
    git(root, "checkout", "-q", "-b", "$(touch${IFS}from-the-branch);x")
    for name in NAMES:
        (root / name).write_text(name)
    (root / ".gitattributes").write_text("* filter=x diff=x\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "--upload-pack=touch from-the-subject")
    trap = Trap(tmp_path / "trap")
    for key in ("core.fsmonitor", "diff.external", "diff.x.textconv", "diff.x.command", "filter.x.clean", "filter.x.smudge"):
        git(root, "config", key, str(trap.program))
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(f"[core]\n\tfsmonitor = {trap.program}\n[diff]\n\texternal = {trap.program}\n")
    elsewhere = repository(tmp_path / "elsewhere")
    (elsewhere / "only-elsewhere.txt").write_text("x")
    git(elsewhere, "add", "-A")
    git(elsewhere, "commit", "-q", "-m", "chore: elsewhere")
    for name, value in {"HOME": str(home), "GIT_DIR": str(elsewhere / ".git"), "GIT_WORK_TREE": str(elsewhere),
                        "GIT_EXTERNAL_DIFF": str(trap.program), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                        "GIT_CONFIG_VALUE_0": str(trap.program), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    return root, trap


@posix_files
def test_a_delegates_reader_of_a_hostile_repository_runs_nothing_and_reads_that_repository(tmp_path, monkeypatch):
    root, trap = hostile_project(tmp_path, monkeypatch)
    listed = delegate_candidate.listed(root)
    assert set(listed) == {"a.txt", ".gitattributes", *NAMES}, "each name is one name, read from this repository and no other"
    assert delegate_candidate.git(root, "rev-parse", "--show-toplevel").decode().strip() == os.path.realpath(root)
    with pytest.raises(ValueError, match="git failed"):
        delegate_candidate.git(root, "rev-parse", "--verify", "--end-of-options", "--exec=touch from-an-argument")
    assert not trap.ran.exists(), trap.ran.read_text()
    assert made_by_a_name(tmp_path) == []


@untried_on_windows
@pytest.mark.parametrize("key", ["core.fsmonitor", "filter.x.clean", "filter.x.process"])
@pytest.mark.parametrize("words", [("status", "--porcelain=v1", "-z"), ("--literal-pathspecs", "diff", "--binary", "HEAD")],
                         ids=["status", "diff"])
def test_a_delegates_reader_runs_nothing_a_nested_repository_configures(tmp_path, key, words):
    root = repository(tmp_path / "project")
    inner = repository(root / "nested")
    (root / ".gitmodules").write_text('[submodule "nested"]\n\tpath = nested\n\turl = ./nested\n\tignore = none\n')
    git(root, "add", "nested", ".gitmodules")
    git(root, "commit", "-q", "-m", "chore: link")
    trap = Trap(tmp_path / "trap")
    git(inner, "config", key, str(trap.program))
    (inner / ".git" / "info").mkdir(exist_ok=True)
    (inner / ".git" / "info" / "attributes").write_text("* filter=x\n")
    time.sleep(1.1)   # past the second the index was written in, so git reads the rewritten file's content
    (inner / "a.txt").write_text("b\n")
    delegate_candidate.git(root, *words)
    assert not trap.ran.exists(), trap.ran.read_text()


@untried_on_windows
@pytest.mark.parametrize("setting", ["diff", "log"])
def test_a_delegates_diff_of_a_moved_nested_repository_opens_nothing_in_it(tmp_path, setting):
    root = repository(tmp_path / "project")
    inner = linked(root)
    git(root, "config", "diff.submodule", setting)
    (inner / "a.txt").write_text("moved\n")
    git(inner, "commit", "-q", "-am", "feat: moved")
    traps = armed(inner, tmp_path / "trap", "core.fsmonitor", "filter.x.clean", "diff.external", "diff.x.textconv", "diff.x.command")
    patch = delegate_candidate.git(root, "--literal-pathspecs", "diff", "--binary", "HEAD", "--", "nested")
    assert ran(traps) == set()
    assert f"+Subproject commit {git(inner, '-c', 'core.fsmonitor=false', 'rev-parse', 'HEAD')}".encode() in patch


@untried_on_windows
@pytest.mark.parametrize("how", [(), ("--intent-to-add",)], ids=["add", "intent"])
def test_a_delegates_add_naming_a_nested_repository_opens_nothing_in_it(tmp_path, how):
    root = repository(tmp_path / "project")
    inner = linked(root)
    traps = armed(inner, tmp_path / "trap", "core.fsmonitor", "filter.x.clean", "filter.x.process")
    (root / "a.txt").write_text("changed\n")
    delegate_candidate.git(root, "--literal-pathspecs", "add", *how, "--", "nested/", "a.txt")
    delegate_candidate.git(root, "--literal-pathspecs", "add", *how, "--", "nested")
    delegate_candidate.git(root, "--literal-pathspecs", "add", *how, "--", "./nested", str(root / "nested"))
    assert ran(traps) == set()
    assert delegate_candidate.git(root, "diff", "--cached", "--name-only") == (b"" if how else b"a.txt\n")
    for key in traps:
        git(inner, "config", "--unset", key)
    git(inner, "commit", "-q", "-am", "feat: moved")
    delegate_candidate.git(root, "--literal-pathspecs", "add", "--", "nested")
    assert git(root, "ls-files", "-s", "nested").split()[1] == git(inner, "rev-parse", "HEAD"), "a nested repository at a new commit is added"


@posix_files
@pytest.mark.parametrize("folder,added", [("deep", b"deep/held.txt\n"), (".", b"a.txt\ndeep/held.txt\n")], ids=["folder", "all"])
def test_a_delegates_add_of_a_folder_holding_a_nested_repository_opens_nothing_in_it(tmp_path, folder, added):
    root = repository(tmp_path / "project")
    (root / "deep").mkdir()
    inners = [linked(root, name) for name in ("deep/nested", "deep/[a]*")]
    (root / "deep" / "held.txt").write_text("held\n")
    traps = [armed(inner, tmp_path / f"trap{at}", "core.fsmonitor", "filter.x.process") for at, inner in enumerate(inners)]
    if folder == ".":
        (root / "a.txt").write_text("changed\n")
    delegate_candidate.git(root, "--literal-pathspecs", "add", "--", folder)
    assert [ran(each) for each in traps] == [set(), set()]
    assert delegate_candidate.git(root, "diff", "--cached", "--name-only") == added


@untried_on_windows
@pytest.mark.parametrize("pattern", ["deep/", "deep/nested", "nested", "*"])
def test_a_delegates_add_over_an_ignored_nested_repository_adds_the_rest(tmp_path, pattern):
    root = repository(tmp_path / "project")
    (root / "deep").mkdir()
    inner = linked(root, "deep/nested")
    (root / ".gitignore").write_text(pattern + "\n")
    traps = armed(inner, tmp_path / "trap", "core.fsmonitor", "filter.x.process")
    (root / "a.txt").write_text("changed\n")
    delegate_candidate.git(root, "--literal-pathspecs", "add", "--", ".")
    assert ran(traps) == set()
    assert b"a.txt\n" in delegate_candidate.git(root, "diff", "--cached", "--name-only")


@pytest.mark.parametrize("marked", [False, True])
def test_a_delegates_add_leaves_the_index_marks_as_it_found_them(tmp_path, marked):
    root = repository(tmp_path / "project")
    linked(root)
    if marked:
        git(root, "update-index", "--assume-unchanged", "nested")
    before = git(root, "ls-files", "-v")
    (root / "a.txt").write_text("changed\n")
    delegate_candidate.git(root, "--literal-pathspecs", "add", "--", "a.txt")
    assert git(root, "ls-files", "-v") == before
    with pytest.raises(ValueError, match="git failed"):
        delegate_candidate.git(root, "--literal-pathspecs", "add", "--", "missing.txt")
    assert git(root, "ls-files", "-v") == before, "an add that fails takes its marks off too"


OWN = ["core.fsmonitor", "diff.external", "diff.x.textconv", "diff.x.command", "filter.x.clean", "filter.x.smudge"]
ASKED = {"status": ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"),
         "diff": ("--literal-pathspecs", "diff", "--binary", "HEAD", "--", "a.txt", "new.txt"),
         "intent": ("--literal-pathspecs", "add", "--intent-to-add", "--", "a.txt", "new.txt"),
         "add": ("--literal-pathspecs", "add", "--", "a.txt", "new.txt"),
         "commit": ("-c", "commit.gpgSign=false", "commit", "--quiet", "--no-verify", "-m", "x"),
         "worktree": ("worktree", "add", "--quiet", "--no-track", "-b", "side", "../side", "HEAD"),
         "listing": ("ls-files", "-z", "--cached", "--others", "--exclude-standard")}
CLEAN = frozenset({"filter.x.clean"})
FILTERS = {"status": CLEAN, "diff": CLEAN, "add": CLEAN, "commit": CLEAN, "worktree": frozenset({"filter.x.smudge"})}
"""The content filter each command runs; ``intent`` may run the clean filter, ``listing`` reads no contents."""


def hostile_account(monkeypatch, folder: Path) -> Trap:
    """An account configuration and caller variables that each name the program of the trap returned."""
    trap = Trap(folder / "trap")
    config = folder / ".gitconfig"
    config.write_text(f'[core]\n\tfsmonitor = {trap.program}\n[diff]\n\texternal = {trap.program}\n'
                      f'[filter "x"]\n\tprocess = {trap.program}\n')
    for name, value in {"HOME": str(folder), "GIT_CONFIG_GLOBAL": str(config), "GIT_EXTERNAL_DIFF": str(trap.program),
                        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "filter.x.process", "GIT_CONFIG_VALUE_0": str(trap.program)}.items():
        monkeypatch.setenv(name, value)
    return trap


@untried_on_windows
@pytest.mark.parametrize("command", ASKED)
def test_a_delegates_commands_run_no_program_of_the_repository_but_its_content_filters(tmp_path, monkeypatch, command):
    """The part of the surface that stays open: a content filter the repository's own configuration names decides what git compares, commits
    and checks out, so the commands that read or write file contents run it."""
    root = repository(tmp_path / "project")
    (root / "new.txt").write_text("n\n")
    if command == "commit":
        git(root, "add", "new.txt")
    traps = armed(root, tmp_path / "trap", *OWN)
    who = {f"GIT_{role}_{field}": "t@example.invalid" for role in ("AUTHOR", "COMMITTER") for field in ("NAME", "EMAIL")}
    account = hostile_account(monkeypatch, tmp_path / "home")
    said = delegate_candidate.git(root, *ASKED[command], env=who)
    assert not account.ran.exists(), "nothing the account's configuration or the caller's variables name runs"
    if command == "intent":
        assert ran(traps) <= {"filter.x.clean"}
    else:   # a content filter still runs, which is why the surface is recorded as partly covered; nothing else does
        assert ran(traps) == FILTERS.get(command, set())
    if command == "diff":
        assert b"\n-a\n+b\n" in said, "the patch is git's own, whatever program the repository names to write one"


@posix_files
def test_a_hostile_projects_history_is_given_to_a_stage_without_running_anything(tmp_path, monkeypatch):
    stage = repository_without_a_commit(tmp_path / "stage")
    root, trap = hostile_project(tmp_path, monkeypatch)
    assert history.give(root, stage, [b"--exec=touch from-an-option", b"two\nlines"]) == ""
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    monkeypatch.delenv("GIT_CONFIG_COUNT")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    assert not trap.ran.exists(), trap.ran.read_text()
    assert made_by_a_name(tmp_path) == []
    assert git(stage, "symbolic-ref", "HEAD") == "refs/heads/$(touch${IFS}from-the-branch);x"
    assert "--upload-pack=touch from-the-subject" in git(stage, "log", "--format=%s")
    assert "elsewhere" not in git(stage, "log", "--format=%s")
    assert str(trap.program) not in (stage / ".git" / "config").read_text(), "the project's settings are not carried"


def repository_without_a_commit(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q")
    return root
