"""The git commands that decide what may land read commits as stored, whatever the checkout's configuration, attributes and environment say."""

from __future__ import annotations

import ast
import os
import shlex
import struct
import subprocess
import sys
from pathlib import Path

import land_git
import land_review
import pytest
from land_support import AGENT_PUSH, DEV, ROOT, World, git

pytest_plugins = ["node_kit"]

SHARED = "src/poolhouse/credentials/shared.py"
BASE = "one\ntwo\nthree\nfour\nfive\n"
DISJOINT = ("ONE\ntwo\nthree\nfour\nfive\n", "one\ntwo\nthree\nfour\nFIVE\n", "ONE\ntwo\nthree\nfour\nFIVE\n")
CLASH = ("one\ntwo\nTHREE\nfour\nfive\n", "one\ntwo\nthree-b\nfour\nfive\n", None)
FORGED = "forged = 1\n"
AUDITED = ("scripts/land_review.py", "scripts/land_steps.py", "scripts/land_exec.py", "scripts/hooks/pushed")


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


def run(tree, *args, env=None, stdin=None, check=True):
    """``git -C tree args`` in the test's own environment plus ``env``; the completed process."""
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, check=check, input=stdin,
                          env={**os.environ, **(env or {})})


def common(world) -> Path:
    """The git directory the worktrees of the project share."""
    return Path(git(world.proj.root, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def driver_command(tmp_path) -> str:
    """A merge driver command that writes ``FORGED`` over the merged file."""
    script = tmp_path / "forge-merge.py"
    script.write_text(f"import sys\nopen(sys.argv[1], 'w').write({FORGED!r})\n", encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} %A"


def attributes(world, text: str) -> None:
    """Set the attributes only this clone reads."""
    (common(world) / "info").mkdir(exist_ok=True)
    (common(world) / "info" / "attributes").write_text(text, encoding="utf-8")


def driver_in_repository(world, tree, tmp_path) -> dict[str, str]:
    git(world.proj.root, "config", "merge.evil.driver", driver_command(tmp_path))
    attributes(world, f"{SHARED} merge=evil\n")
    return {}


def driver_by_default_in_repository(world, tree, tmp_path) -> dict[str, str]:
    git(world.proj.root, "config", "merge.evil.driver", driver_command(tmp_path))
    git(world.proj.root, "config", "merge.default", "evil")
    return {}


def driver_in_home(world, tree, tmp_path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(f'[merge "evil"]\n\tdriver = {driver_command(tmp_path)}\n[merge]\n\tdefault = evil\n', encoding="utf-8")
    return {"HOME": str(home)}


def driver_in_file_named_by(variable: str):
    """An attack that sets ``variable`` to a configuration file whose default merge driver forges the merge."""
    def attack(world, tree, tmp_path) -> dict[str, str]:
        path = tmp_path / f"{variable}.config"
        path.write_text(f'[merge "evil"]\n\tdriver = {driver_command(tmp_path)}\n[merge]\n\tdefault = evil\n', encoding="utf-8")
        return {variable: str(path)}
    return attack


def union_in_worktree(world, tree, tmp_path) -> dict[str, str]:
    (tree / ".gitattributes").write_text("* merge=union\n", encoding="utf-8")
    return {}


def union_in_repository(world, tree, tmp_path) -> dict[str, str]:
    attributes(world, "* merge=union\n")
    return {}


DRIVERS = {"repository": driver_in_repository, "repository-default": driver_by_default_in_repository, "home": driver_in_home,
           "global-file": driver_in_file_named_by("GIT_CONFIG_GLOBAL"), "system-file": driver_in_file_named_by("GIT_CONFIG_SYSTEM")}
UNIONS = {"worktree": union_in_worktree, "repository": union_in_repository}


def contested(world, name: str, edits) -> tuple[Path, str, str]:
    """A branch whose reviewed tip rewrites ``SHARED`` and origin's own rewrite of it, landed after the branch was cut."""
    world.proj.advance(SHARED, BASE)
    tree, tip = world.start(name, {SHARED: edits[0]})
    world.accept(name, tip)
    return tree, tip, world.proj.advance(SHARED, edits[1])


def merge_in(tree, moved: str, tip: str, env) -> tuple[str, str]:
    """The tree and the merge commit of ``moved`` and ``tip`` that git makes in the environment ``env``."""
    made = run(tree, "merge-tree", "--write-tree", moved, tip, env=env, check=False).stdout.split()[0]
    return made, run(tree, "commit-tree", made, "-p", moved, "-p", tip, "-m", "chore: merge", env=env).stdout.strip()


def clean_merge(tree, moved: str, tip: str, text: str, tmp_path) -> tuple[str, str]:
    """The tree and the merge commit that hold ``moved`` with ``SHARED`` replaced by ``text``, built without a merge."""
    env = {"GIT_INDEX_FILE": str(tmp_path / "clean-index")}
    run(tree, "read-tree", moved, env=env)
    blob = run(tree, "hash-object", "-w", "--stdin", stdin=text).stdout.strip()
    run(tree, "update-index", "--add", "--cacheinfo", f"100644,{blob},{SHARED}", env=env)
    made = run(tree, "write-tree", env=env).stdout.strip()
    return made, run(tree, "commit-tree", made, "-p", moved, "-p", tip, "-m", "chore: merge").stdout.strip()


def push(world, tree, sha: str, env) -> subprocess.CompletedProcess[str]:
    """``git push`` of ``sha`` to the development branch as an agent, through the installed hook."""
    return subprocess.run(["git", "push", "origin", f"{sha}:refs/heads/{DEV}"], cwd=tree, capture_output=True, text=True, check=False,
                          env={**world.env(world.lander), **AGENT_PUSH, **env})


@pytest.mark.parametrize("kind", sorted(DRIVERS))
def test_a_merge_driver_of_the_checkout_does_not_change_the_merge(world, tmp_path, monkeypatch, kind):
    tree, tip, moved = contested(world, "driven", DISJOINT)
    env = DRIVERS[kind](world, tree, tmp_path)
    expected, _ = clean_merge(tree, moved, tip, DISJOINT[2], tmp_path)
    assert merge_in(tree, moved, tip, env)[0] != expected, "the driver does not act on this checkout"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert land_git.merge_tree(tree, moved, tip) == (expected, [])


@pytest.mark.parametrize("kind", sorted(UNIONS))
def test_a_union_attribute_of_the_checkout_does_not_hide_a_conflict(world, tmp_path, kind):
    tree, tip, moved = contested(world, "clashing", CLASH)
    UNIONS[kind](world, tree, tmp_path)
    assert run(tree, "merge-tree", "--write-tree", moved, tip).returncode == 0, "the attribute does not act on this checkout"
    assert land_git.merge_tree(tree, moved, tip) == ("", [SHARED])


def test_the_lander_merges_origin_into_a_reviewed_tip_whatever_the_checkout_driver_says(world, tmp_path):
    world.install_hook()
    tree, tip, moved = contested(world, "landed-driven", DISJOINT)
    driver_in_repository(world, tree, tmp_path)
    code, out = world.land(tree, hostile=AGENT_PUSH)
    assert code == 0, out
    head = world.proj.origin_head()
    assert git(world.proj.root, "rev-list", "--parents", "-n", "1", head).split()[1:] == [moved, tip]
    assert git(world.proj.root, "show", f"{head}:{SHARED}") == DISJOINT[2].strip()


def test_the_lander_stops_at_a_conflict_a_union_attribute_would_merge(world, tmp_path):
    world.install_hook()
    tree, tip, moved = contested(world, "landed-united", CLASH)
    union_in_repository(world, tree, tmp_path)
    code, out = world.land(tree, hostile=AGENT_PUSH)
    assert code == 5 and f"conflicts in {SHARED}" in out, out
    assert world.proj.origin_head() == moved and git(tree, "rev-parse", "HEAD") == tip


def test_python_that_does_not_parse_is_found_under_a_name_git_would_quote(world):
    world.install_hook()
    tree, sha = world.start("quoted", {"tests/test_caf\u00e9.py": "def (:\n"})
    refused = push(world, tree, sha, {})
    assert refused.returncode != 0 and "does not parse" in refused.stderr and "no-data-files" not in refused.stderr, refused.stderr
    assert world.proj.origin_head() != sha


def test_a_file_under_a_name_git_would_quote_is_pushed_when_it_is_ordinary_and_refused_when_it_is_data(world):
    world.install_hook()
    tree, sha = world.start("quoted-data", {"docs/donn\u00e9es.csv": "a,b\n"})
    refused = push(world, tree, sha, {})
    assert refused.returncode != 0 and "docs/donn\u00e9es.csv: a .csv file is data or weights" in refused.stderr, refused.stderr
    ordinary, fine = world.start("quoted-doc", {"docs/r\u00e9sum\u00e9.md": "text\n"})
    done = push(world, ordinary, fine, {})
    assert done.returncode == 0, done.stderr
    assert world.proj.origin_head() == fine


@pytest.mark.parametrize("rules", ["* diff=fine\n", "*.py -diff\n", "* binary\n"])
def test_a_diff_setting_does_not_hide_a_sensitive_path_from_the_review_check(world, rules):
    git(world.proj.root, "config", "diff.external", "true")
    git(world.proj.root, "config", "diff.fine.textconv", "true")
    git(world.proj.root, "config", "diff.renames", "copies")
    attributes(world, rules)
    tree, sha = world.start("hidden", {"src/poolhouse/credentials/hidden.py": "VALUE = 1\n"})
    needed, _, first = land_review.required(tree, world.proj.origin_head(), sha)
    assert needed and first == "src/poolhouse/credentials/hidden.py"


def test_a_command_that_reads_commits_is_refused_in_the_checkout(world):
    tree, sha = world.start("repository-context", {"docs/a.md": "changed\n"})
    for args in (("diff-tree", "-r", sha), ("merge-tree", "--write-tree", sha, sha), ("rev-list", sha), ("log", sha), ("show", sha)):
        with pytest.raises(ValueError, match="sealed"):
            land_git.git(tree, *args)
    assert land_git.sealed(tree, "rev-list", "--count", sha).stdout.strip() == "2"


def test_a_sealed_command_stores_nothing_in_the_checkout_unless_it_is_to(world):
    tree, _ = world.start("reads", {"docs/a.md": "changed\n"})
    kept = land_git.sealed(tree, "hash-object", "-w", "--stdin", stdin="read only\n").stdout.strip()
    assert run(tree, "cat-file", "-e", kept, check=False).returncode != 0
    stored = land_git.sealed(tree, "hash-object", "-w", "--stdin", stdin="stored\n", write=True).stdout.strip()
    assert run(tree, "cat-file", "-e", stored, check=False).returncode == 0


def test_a_base_must_be_a_commit_id_and_anything_else_puts_the_whole_tip_in_the_diff(world):
    tree, sha = world.start("named-base", {"docs/a.md": "changed\n"})
    origin = world.proj.origin_head()
    assert land_git.commit_id(tree, origin) == origin and land_git.commit_id(tree, origin[:12]) == ""
    assert land_git.commit_id(tree, DEV) == "" and land_git.commit_id(tree, "1" * 40) == ""
    assert not land_review.required(tree, origin, sha)[0]
    needed, start, _ = land_review.required(tree, DEV, sha)
    assert needed and start == land_git.empty_tree(tree)


def forge_commit_graph(tree, commit: str, parent: str, as_parent: str) -> None:
    """Write the commit graph of the clone with ``as_parent`` listed as the second parent of ``commit`` in place of ``parent``."""
    run(tree, "commit-graph", "write", "--reachable")
    path = common_path(tree) / "objects" / "info" / "commit-graph"
    data = bytearray(path.read_bytes())
    chunks = {bytes(data[8 + n * 12:12 + n * 12]): struct.unpack(">Q", data[12 + n * 12:20 + n * 12])[0] for n in range(data[6] + 1)}
    oids = sorted(run(tree, "rev-list", "--all").stdout.split())
    entry = chunks[b"CDAT"] + oids.index(commit) * 36 + 24
    assert struct.unpack(">I", data[entry:entry + 4])[0] == oids.index(parent)
    data[entry:entry + 4] = struct.pack(">I", oids.index(as_parent))
    path.unlink()
    path.write_bytes(data)


def common_path(tree) -> Path:
    """The git directory the worktree ``tree`` shares."""
    return Path(run(tree, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())


def test_a_forged_commit_graph_does_not_change_the_parents_of_a_merge(world, tmp_path):
    tree, tip, moved = contested(world, "graphed", DISJOINT)
    smuggled = world.start("smuggled", {"src/poolhouse/credentials/smuggled.py": "VALUE = 1\n"})[1]
    made, _ = clean_merge(tree, moved, tip, DISJOINT[2], tmp_path)
    merge = run(tree, "commit-tree", made, "-p", moved, "-p", smuggled, "-m", "chore: merge").stdout.strip()
    run(tree, "branch", "merged", merge)
    assert land_review.merged_parents(tree, merge) == ("", ""), "the merge of the tip and origin is not this commit's merge"
    forge_commit_graph(tree, merge, smuggled, tip)
    assert run(tree, "rev-list", "--parents", "-n", "1", merge).stdout.split()[1:] == [moved, tip], "the graph does not act on this clone"
    assert land_review.merged_parents(tree, merge) == ("", "")


def test_a_path_that_is_not_utf8_stops_the_review_check(world, tmp_path):
    tree, tip = world.start("bytes", {"docs/a.md": "changed\n"})
    env = {"GIT_INDEX_FILE": str(tmp_path / "bytes-index")}
    run(tree, "read-tree", tip, env=env)
    blob = run(tree, "hash-object", "-w", "--stdin", stdin="x\n").stdout.strip()
    run(tree, "update-index", "--add", "--cacheinfo", f"100644,{blob},docs/\udcff.md", env=env)
    odd = run(tree, "commit-tree", run(tree, "write-tree", env=env).stdout.strip(), "-p", tip, "-m", "chore: odd name").stdout.strip()
    with pytest.raises(RuntimeError, match="not UTF-8"):
        land_review.required(tree, world.proj.origin_head(), odd)


def test_a_shallow_file_does_not_cut_the_history_the_lander_reads(world):
    tree, sha = world.start("shallow", {"docs/a.md": "changed\n"})
    (common_path(tree) / "shallow").write_text(f"{sha}\n", encoding="utf-8")
    assert run(tree, "rev-list", "--count", sha).stdout.strip() == "1", "the shallow file does not act on this clone"
    assert land_git.sealed(tree, "rev-list", "--count", sha).stdout.strip() == "2"


def test_only_the_git_helper_starts_git_for_the_lander_and_the_push_check():
    found = {path: [n.lineno for n in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8")))
                    if isinstance(n, ast.Constant) and n.value == "git"] for path in AUDITED}
    assert not any(found.values()), f"these files start git themselves, on these lines; use scripts/land_git.py: {found}"
