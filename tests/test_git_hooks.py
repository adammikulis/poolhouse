"""The start-of-run hook installer runs once, in the controller, and only when a wanted hook is missing."""

from __future__ import annotations

import subprocess

import git_hooks
import pytest


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "install-hooks.sh").write_text(f"#!/bin/sh\ntouch {tmp_path / 'ran'}\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def link_all(root):
    hooks = root / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in git_hooks.WANTED:
        (hooks / name).symlink_to(f"../../scripts/hooks/{name}")


def test_a_link_into_the_hook_scripts_and_a_wrapper_that_runs_one_are_in_place(tmp_path):
    link = tmp_path / "linked"
    link.symlink_to("../../scripts/hooks/pre-push")
    wrapper = tmp_path / "wrapper"
    wrapper.write_text('#!/bin/sh\nexec "$ROOT/scripts/hooks/commit-msg" "$@"\n')
    foreign = tmp_path / "foreign"
    foreign.write_text("#!/bin/sh\nexit 0\n")
    assert git_hooks.in_place(link) and git_hooks.in_place(wrapper)
    assert not git_hooks.in_place(foreign) and not git_hooks.in_place(tmp_path / "absent")


def test_the_installer_runs_when_a_wanted_hook_is_missing(repo, tmp_path):
    git_hooks.install(repo)
    assert (tmp_path / "ran").exists()


def test_the_installer_does_not_run_when_every_wanted_hook_is_in_place(repo, tmp_path):
    link_all(repo)
    git_hooks.install(repo)
    assert not (tmp_path / "ran").exists()


def test_a_worker_of_a_parallel_run_leaves_the_installer_to_the_controller(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    git_hooks.install(repo)
    assert not (tmp_path / "ran").exists()
