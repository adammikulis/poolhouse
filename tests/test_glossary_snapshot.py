"""Vocabulary checks use the data in the tree being checked."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from gates import glossary_words  # noqa: E402

WORD = "clu" + "ster"
FILES = ("glossary-rules.toml", "glossary-allowlist.toml")


def policy(root: Path) -> None:
    (root / "contracts").mkdir(parents=True)
    for name in FILES:
        shutil.copy(REPO / "contracts" / name, root / "contracts" / name)


def allow(root: Path) -> None:
    path = root / "contracts" / FILES[1]
    path.write_text(path.read_text() + f'\n[[allow]]\npath = "sample.py"\nwords = ["{WORD}"]\nreason = "wire field"\n')


def test_checked_tree_policy_is_independent_of_loaded_script(tmp_path):
    policy(tmp_path)
    (tmp_path / "sample.py").write_text(f'{WORD} = 1\n')
    assert glossary_words.find(tmp_path)
    allow(tmp_path)
    assert glossary_words.find(tmp_path) == []
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "nomenclature.py").write_text('raise RuntimeError("untrusted code")')
    assert glossary_words.find(tmp_path) == []


@pytest.mark.parametrize("change", ["missing", "empty", "malformed", "unknown", "type", "family", "pattern", "reason", "symlink"])
def test_invalid_policy_is_refused(tmp_path, change):
    policy(tmp_path)
    path = tmp_path / "contracts" / FILES[1]
    if change == "missing":
        path.unlink()
    elif change == "symlink":
        path.unlink()
        path.symlink_to(REPO / "contracts" / FILES[1])
    elif change == "empty":
        (tmp_path / "contracts" / FILES[0]).write_text("")
    else:
        additions = {"malformed": "[", "unknown": "\nunknown = 1\n", "type": '\n[[allow]]\npath = 1\nwords = []\nreason = "wire"',
                     "family": '\n[[allow]]\npath = "sample.py"\nwords = ["unknown"]\nreason = "wire"',
                     "reason": '\n[[exclude]]\npath = "sample.py"\nreason = " "'}
        if change == "pattern":
            path = tmp_path / "contracts" / FILES[0]
            addition = '\n[[gate]]\npattern = "["\nfamily = "wire"\nsay = "wire"'
        else:
            addition = additions[change]
        path.write_text(path.read_text() + addition)
    with pytest.raises(ValueError):
        glossary_words.find(tmp_path)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "repo"
    policy(root)
    (root / "budgets.json").write_text("{}")
    shutil.copy(REPO / "pyproject.toml", root / "pyproject.toml")
    env = {**os.environ, "HOME": str(tmp_path / "home"), "GIT_AUTHOR_NAME": "Test Agent",
           "GIT_AUTHOR_EMAIL": "test@agents.invalid", "GIT_COMMITTER_NAME": "Test Agent",
           "GIT_COMMITTER_EMAIL": "test@agents.invalid", "POOLHOUSE_AGENT": "test"}
    Path(env["HOME"]).mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True)
    git("init", "-q", "-b", "0.3dev")
    git("add", "contracts/glossary-rules.toml", "contracts/glossary-allowlist.toml", "pyproject.toml", "budgets.json")
    git("commit", "-qm", "chore: fixture")
    def hook():
        return subprocess.run([sys.executable, str(REPO / "scripts" / "hooks" / "budgets")],
                              cwd=root, env=env, capture_output=True, text=True)
    return root, git, hook


def test_staged_allowance_is_used_and_unstaged_allowance_is_not(checkout):
    root, git, hook = checkout
    (root / "sample.py").write_text(f'{WORD} = 1\n')
    git("add", "sample.py")
    allow(root)
    assert hook().returncode == 1
    git("add", "contracts/glossary-allowlist.toml")
    assert hook().returncode == 0


def test_head_policy_is_not_replaced_by_staged_policy(checkout):
    root, git, hook = checkout
    allow(root)
    (root / "sample.py").write_text(f'{WORD} = 1\n')
    git("add", "sample.py", "contracts/glossary-allowlist.toml")
    git("commit", "-qm", "chore: wire fixture")
    shutil.copy(REPO / "contracts" / FILES[1], root / "contracts" / FILES[1])
    (root / "sample.py").write_text(f'{WORD} = 2\n')
    git("add", "sample.py", "contracts/glossary-allowlist.toml")
    done = hook()
    assert done.returncode == 1 and "glossary-words" in done.stderr


@pytest.mark.parametrize("change", ["deleted", "malformed", "symlink"])
def test_policy_only_invalid_commit_is_refused(checkout, change):
    root, git, hook = checkout
    path = root / "contracts" / FILES[1]
    if change == "deleted":
        git("rm", "contracts/glossary-allowlist.toml")
    else:
        path.unlink()
        if change == "symlink":
            path.symlink_to(REPO / "contracts" / FILES[1])
        else:
            path.write_text("[")
        git("add", "contracts/glossary-allowlist.toml")
    done = hook()
    assert done.returncode == 1 and "policy" in done.stderr
