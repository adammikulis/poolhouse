"""Per-tool staged processes cannot mutate canonical coding bytes after return."""
from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import time
from contextlib import contextmanager, nullcontext

import pytest

from poolhouse import pi_tools
from poolhouse.activity.source_snapshot import git_output
from poolhouse.coding_workspace import Workspace

pytest_plugins = ["tests.sandbox_kit"]


@pytest.fixture(autouse=True)
def private_snapshot_storage(tmp_path, monkeypatch):
    storage = tmp_path / "snapshot-storage"
    storage.mkdir(mode=0o700)
    monkeypatch.setattr(tempfile, "tempdir", str(storage))


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "selected-repository"
    root.mkdir()
    git_output(["init", "--quiet", str(root)], cwd=tmp_path)
    (root / "code.txt").write_text("before")
    made = Workspace(root)
    yield made
    shutil.rmtree(made.directory)


@pytest.fixture
def binding(tmp_path, seatbelt):
    binary = shutil.which("pi")
    if not binary:
        pytest.skip("Installed stock Pi is required")
    selected = pi_tools.runtime(binary)
    worker = tmp_path / "worker.mjs"
    worker.write_text(pi_tools.WORKER)
    return {**selected, "worker": str(worker), "write": True}


def permitted(_capture, _revision):
    return nullcontext()


def call(workspace, binding, tool, inputs, **options):
    writable, guard = options.get("writable", True), options.get("guard", permitted)
    def execute(stage):
        configuration = {**binding, "root": str(stage.physical), "tool_root": str(stage.alias), "write": writable}
        return pi_tools.execute(configuration, {"tool": tool, "input": inputs})
    return workspace.run(execute, guard, writable=writable)


def test_stock_tools_publish_private_manifest_without_editing_selected_repo(workspace, binding):
    answer = call(workspace, binding, "write", {"path": "code.txt", "content": "after"})
    assert not answer.get("isError")
    assert (workspace.master / "code.txt").read_text() == "after"
    assert (workspace.source / "code.txt").read_text() == "before"
    assert workspace.changes[0].path == b"code.txt"
    assert workspace.changes[0].before != workspace.changes[0].after
    answer = call(workspace, binding, "read", {"path": "code.txt"}, writable=False)
    assert "after" in json.dumps(answer)


def test_old_detached_writer_cannot_change_capture_master_or_next_stage(workspace, binding):
    stages = []
    def execute(stage):
        stages.append(stage)
        alias = shlex.quote(str(stage.alias / "cross-epoch"))
        command = ("(while [ ! -f release ]; do sleep .02; done; "
                   f"printf late > late-write; printf escaped > {alias}; printf done > finished) "
                   ">/dev/null 2>&1 &")
        config = {**binding, "root": str(stage.physical), "tool_root": str(stage.alias)}
        return pi_tools.execute(config, {"tool": "bash", "input": {"command": command}})
    try:
        workspace.run(execute, permitted, writable=True)
        captured = workspace.last
        old = stages[0]
        call(workspace, binding, "read", {"path": "code.txt"}, writable=False)
    finally:
        if stages:
            (stages[0].physical / "release").write_text("")
    deadline = time.monotonic() + 3
    while not (old.physical / "finished").exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert (old.physical / "finished").exists()
    assert (old.physical / "late-write").read_text() == "late"
    assert not (workspace.master / "late-write").exists()
    assert not (captured.directory / "late-write").exists()
    assert not (workspace.alias / "cross-epoch").exists()
    assert not (workspace.source / "late-write").exists()


def test_actual_read_only_policy_and_parent_gate_both_prevent_publication(workspace, binding):
    answer = call(workspace, binding, "bash", {"command": "printf changed > code.txt"}, writable=False)
    assert answer.get("isError")
    assert (workspace.master / "code.txt").read_text() == "before"
    assert workspace.revision == 0


def test_revocation_after_child_returns_rejects_its_captured_edits(workspace, binding):
    @contextmanager
    def revoked(_capture, _revision):
        raise PermissionError("The session permissions changed")
        yield
    with pytest.raises(PermissionError, match="permissions changed"):
        call(workspace, binding, "write", {"path": "code.txt", "content": "stale"}, guard=revoked)
    assert (workspace.master / "code.txt").read_text() == "before"
    assert workspace.revision == 0 and workspace.last is None


def test_tool_failure_does_not_promote_partial_edits(workspace, binding):
    answer = call(workspace, binding, "bash", {"command": "printf partial > code.txt; exit 1"})
    assert answer.get("isError")
    assert (workspace.master / "code.txt").read_text() == "before"
    assert workspace.revision == 0


def test_absolute_selected_repo_writes_are_denied_even_to_acting_worker(workspace, binding):
    answer = call(workspace, binding, "write", {"path": str(workspace.source / "code.txt"), "content": "escape"})
    assert answer.get("isError")
    assert (workspace.source / "code.txt").read_text() == "before"


def test_parent_capture_excludes_credentials_and_preserves_omitted_paths(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git_output(["init", "--quiet", str(root)], cwd=tmp_path)
    (root / "code.py").write_text("SAFE = True\n")
    (root / ".env").write_text("PRIVATE=not-model-input\n")
    made = Workspace(root)
    try:
        assert b".env" in made.initial.excluded
        assert not (made.master / ".env").exists()
        assert (root / ".env").read_text() == "PRIVATE=not-model-input\n"
    finally:
        shutil.rmtree(made.directory)


def test_deletions_and_renames_are_explicit_manifest_deltas(workspace, binding):
    answer = call(workspace, binding, "bash", {"command": "mv code.txt renamed.txt"})
    assert not answer.get("isError")
    changes = {change.path: change for change in workspace.changes}
    assert changes[b"code.txt"].before and changes[b"code.txt"].after is None
    assert changes[b"renamed.txt"].before is None and changes[b"renamed.txt"].after
    assert (workspace.source / "code.txt").read_text() == "before"


def test_owner_edits_are_preserved_while_private_result_is_captured(workspace, binding):
    def execute(stage):
        (workspace.source / "code.txt").write_text("owner edit")
        return pi_tools.execute({**binding, "root": str(stage.physical), "tool_root": str(stage.alias)},
                                {"tool": "write", "input": {"path": "code.txt", "content": "agent edit"}})
    workspace.run(execute, permitted, writable=True)
    assert (workspace.source / "code.txt").read_text() == "owner edit"
    assert (workspace.master / "code.txt").read_text() == "agent edit"


def test_guard_failure_on_exit_rolls_back_all_parent_publication(workspace, binding):
    initial = workspace.master
    @contextmanager
    def invalidated(_capture, _revision):
        yield
        raise PermissionError("The policy transaction did not commit")
    with pytest.raises(PermissionError, match="did not commit"):
        call(workspace, binding, "write", {"path": "code.txt", "content": "stale"}, guard=invalidated)
    assert workspace.master == initial
    assert workspace.last is None and workspace.changes == () and workspace.revision == 0
    assert (workspace.master / "code.txt").read_text() == "before"


def test_current_revision_is_checked_inside_publication_guard(workspace, binding):
    initial = workspace.master
    @contextmanager
    def invalidated(_capture, _revision):
        workspace.revision += 1
        yield
    with pytest.raises(ValueError, match="session changed"):
        call(workspace, binding, "write", {"path": "code.txt", "content": "stale"}, guard=invalidated)
    assert workspace.master == initial and workspace.last is None
    assert workspace.revision == 1
    assert (workspace.master / "code.txt").read_text() == "before"


def test_parent_index_never_executes_repository_defined_filters(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git_output(["init", "--quiet", str(root)], cwd=tmp_path)
    marker = tmp_path / "filter-executed"
    command = "touch " + shlex.quote(str(marker)) + "; cat"
    git_output(["config", "filter.untrusted.clean", command], cwd=root)
    (root / ".gitattributes").write_text("*.txt filter=untrusted\n")
    (root / "code.txt").write_text("plain bytes")
    made = Workspace(root)
    try:
        made.run(lambda stage: {"content": []}, permitted, writable=True)
        assert not marker.exists()
        assert (made.master / "code.txt").read_text() == "plain bytes"
        assert b"filter.untrusted" not in (made.master / ".git/config").read_bytes()
    finally:
        shutil.rmtree(made.directory)


def test_executable_mode_changes_are_explicit_manifest_deltas(workspace, binding):
    answer = call(workspace, binding, "bash", {"command": "chmod +x code.txt"})
    assert not answer.get("isError")
    change, = workspace.changes
    assert change.before == change.after
    assert change.before_mode == b"100644" and change.after_mode == b"100755"
    assert not (workspace.source / "code.txt").stat().st_mode & 0o111


def test_guard_invalidation_cannot_be_undone_by_stale_stage_replay(workspace):
    stage = workspace.begin()
    @contextmanager
    def invalidated(_capture, _revision):
        workspace.revision += 1
        yield
    with pytest.raises(ValueError, match="session changed"):
        workspace.finish(stage, invalidated, writable=True)
    assert workspace.revision == 1
    with pytest.raises(ValueError, match="session changed"):
        workspace.finish(stage, permitted, writable=True)
    assert workspace.revision == 1 and workspace.last is None


def test_known_harness_configuration_is_omitted_from_stock_tool_inputs(tmp_path, binding):
    root = tmp_path / "repo"
    root.mkdir()
    git_output(["init", "--quiet", str(root)], cwd=tmp_path)
    (root / ".mcp.json").write_text('{"credential":"private-marker"}')
    (root / ".pi").mkdir()
    (root / ".pi/settings.json").write_text('{"key":"private-marker"}')
    (root / "code.txt").write_text("safe")
    made = Workspace(root)
    try:
        for path in (".mcp.json", ".pi/settings.json"):
            answer = call(made, binding, "read", {"path": path}, writable=False)
            assert answer.get("isError") and "private-marker" not in json.dumps(answer)
        assert not (made.master / ".pi").exists() and not (made.master / ".mcp.json").exists()
        assert b".pi" in made.initial.excluded and b".mcp.json" in made.initial.excluded
        assert "private-marker" in (root / ".mcp.json").read_text()
    finally:
        shutil.rmtree(made.directory)


def test_whole_control_prefix_is_denied_in_actual_worker(workspace, binding):
    answer = call(workspace, binding, "write", {"path": ".claude/arbitrary.json", "content": "unsafe"})
    assert answer.get("isError")
    assert not (workspace.master / ".claude").exists()


def test_parent_rejects_even_ignored_new_control_prefix_before_capture(workspace):
    def bypassed_worker(stage):
        (stage.physical / ".gitignore").write_text(".claude/\n")
        (stage.physical / ".claude").mkdir()
        (stage.physical / ".claude/arbitrary.json").write_text("unsafe")
        return {"content": []}
    with pytest.raises(ValueError, match="trusted control path"):
        workspace.run(bypassed_worker, permitted, writable=True)
    assert workspace.revision == 0 and workspace.last is None
    assert not (workspace.master / ".claude").exists()
