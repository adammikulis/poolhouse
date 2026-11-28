"""The source repository that the runtime cutover tests build and switch against.

A test module names this kit as a plugin (``pytest_plugins = ["cutover_kit"]``) to get the `source`
fixture. Nothing here is autouse: a fixture of a plugin applies to every test of the run, and a test
module doubling as a plugin is how one module's keystore stub reached the tests of another.
"""

from __future__ import annotations

import pytest
from test_runtime_trust import commit, git

from poolhouse import runtime_store


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    head = commit(repo, "file")
    git(repo, "update-ref", "refs/remotes/origin/dev", head)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "dev")
    runtime_store.write_state({"checkout": str(repo)})
    return repo, head
