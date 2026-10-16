"""The commit-msg hook says when it has no names to refuse, and still refuses a name it is given."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "commit-msg"
NAME = "Quillon Thistlewick"


@pytest.fixture
def home(tmp_path) -> Path:
    where = tmp_path / "home"
    (where / ".config").mkdir(parents=True)
    return where


def run_hook(tmp_path: Path, home: Path, message: str, **env: str) -> subprocess.CompletedProcess:
    repo = tmp_path / "repo"
    if not repo.exists():
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    text = tmp_path / "MSG"
    text.write_text(message)
    clean = {k: v for k, v in os.environ.items() if not k.startswith("NAMES_") and k != "SKIP_NAME_CHECK"}
    return subprocess.run(["sh", str(HOOK), str(text)], cwd=repo, text=True, capture_output=True, check=False,
                          env={**clean, "HOME": str(home), "PYTHON": sys.executable, **env})


def test_a_message_is_not_checked_without_any_name_source_and_the_hook_says_so(tmp_path, home):
    done = run_hook(tmp_path, home, f"fix: thanks to {NAME}\n")
    assert done.returncode == 0
    assert "commit-msg: name check inactive" in done.stderr
    assert "pii-deny.txt" in done.stderr and "NAMES_GRAPH" in done.stderr


def test_a_listed_name_is_refused_and_no_inactive_notice_is_printed(tmp_path, home):
    (home / ".config" / "pii-deny.txt").write_text(NAME + "\n")
    done = run_hook(tmp_path, home, f"fix: thanks to {NAME}\n")
    assert done.returncode == 1 and "refusing a message that names a real person" in done.stderr
    assert "inactive" not in done.stderr


def test_a_clean_message_with_a_source_prints_nothing(tmp_path, home):
    (home / ".config" / "pii-deny.txt").write_text(NAME + "\n")
    done = run_hook(tmp_path, home, "fix: nothing personal here\n")
    assert done.returncode == 0 and done.stderr == ""


def test_a_configured_source_that_cannot_be_read_is_named(tmp_path, home):
    (home / ".config" / "pii-deny.txt").write_text(NAME + "\n")
    missing = tmp_path / "no-such-graph.json"
    done = run_hook(tmp_path, home, "fix: nothing personal here\n", NAMES_GRAPH=str(missing))
    assert done.returncode == 0
    assert "NAMES_GRAPH" in done.stderr and str(missing) in done.stderr and "could not be read" in done.stderr


def test_skipping_the_name_check_stays_silent(tmp_path, home):
    done = run_hook(tmp_path, home, f"fix: thanks to {NAME}\n", SKIP_NAME_CHECK="1")
    assert done.returncode == 0 and done.stderr == ""
