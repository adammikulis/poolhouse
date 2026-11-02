"""Only retained parent-owned coding snapshots can be reopened across tool processes."""
from __future__ import annotations

import json
import selectors
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from poolhouse.coding_workspace import Workspace
from poolhouse.fleet import conversation_graph
from poolhouse.platform import terminate_process_group
from tests.test_coding_workspace import (  # noqa: F401
    binding,
    call,
    coding_owner,
    private_snapshot_storage,
    workspace,
)

pytest_plugins = ["tests.sandbox_kit"]


@pytest.fixture(autouse=True)
def bound_workspace(workspace, coding_owner):  # noqa: F811
    workspace.owner, workspace.expected = coding_owner


def opened(workspace, saved, *, current):  # noqa: F811
    with conversation_graph.opened(workspace.owner.root) as graph, graph.transaction():
        graph.put_doc(f"coding:{workspace.expected.conversation}", {"version": 1, "workspace": saved})
    return Workspace.reopened(workspace.source, workspace.owner, workspace.expected, current=current)


def copied(workspace):  # noqa: F811
    return json.loads(json.dumps(workspace.receipt()))


def test_reopen_verifies_full_manifest_and_continues_stock_tools(workspace, binding):  # noqa: F811
    assert not call(workspace, binding, "write", {"path": "code.txt", "content": "first stage"}).get("isError")
    saved = copied(workspace)
    with opened(workspace, saved, current=lambda: True) as resumed:
        assert resumed.revision == 1 and resumed.current()
        assert resumed.master != workspace.master
        assert (resumed.master / "code.txt").read_text() == "first stage"
        assert not call(resumed, binding, "edit", {"path": "code.txt", "edits": [{"oldText": "first stage", "newText": "second stage"}]}).get("isError")
        assert resumed.revision == 2
        second = copied(resumed)
    with opened(workspace, second, current=lambda: True) as resumed:
        assert (resumed.master / "code.txt").read_text() == "second stage"
    assert (workspace.source / "code.txt").read_text() == "before"


@pytest.mark.parametrize("current", [False, 1, None, "yes"])
def test_revoked_or_forged_current_identity_is_rejected_before_receipt_access(workspace, current):  # noqa: F811
    with pytest.raises(ValueError, match="original coding session"), opened(workspace, None, current=lambda: current):
        pytest.fail("uncurrent actor reopened snapshots")


@pytest.mark.parametrize("revision", [True, False, -1, "0", 0.0])
def test_epoch_must_be_strict_nonnegative_integer(workspace, revision):  # noqa: F811
    saved = copied(workspace)
    saved["revision"] = revision
    with pytest.raises(ValueError, match="Invalid coding workspace"), opened(workspace, saved, current=lambda: True):
        pytest.fail("invalid epoch was accepted")


@pytest.mark.parametrize("key", ["source_identity", "directory_identity"])
def test_replaced_source_or_namespace_identity_cannot_be_adopted(workspace, key):  # noqa: F811
    saved = copied(workspace)
    saved[key][1] += 1
    with pytest.raises(ValueError, match="roots changed"), opened(workspace, saved, current=lambda: True):
        pytest.fail("changed root was accepted")


def test_changed_file_bytes_are_rejected_even_with_original_tree_hash(workspace):  # noqa: F811
    saved = copied(workspace)
    (workspace.master / "code.txt").write_text("untrusted replacement")
    with pytest.raises(ValueError, match="manifest changed"), opened(workspace, saved, current=lambda: True):
        pytest.fail("changed snapshot bytes were accepted")


@pytest.mark.parametrize("column,value", [(1, "100755"), (2, "0" * 64), (3, 999)])
def test_full_modes_content_digest_and_size_are_verified(workspace, column, value):  # noqa: F811
    saved = copied(workspace)
    saved["master"]["manifest"]["entries"][0][column] = value
    with pytest.raises(ValueError, match="manifest changed"), opened(workspace, saved, current=lambda: True):
        pytest.fail("changed manifest was accepted")


def test_worker_stage_is_never_adopted_as_a_canonical_master(workspace):  # noqa: F811
    saved = copied(workspace)
    stage = workspace.begin()
    saved["master"]["name"] = stage.physical.name
    with pytest.raises(ValueError, match="Invalid coding snapshot"), opened(workspace, saved, current=lambda: True):
        pytest.fail("orphan tool stage was adopted")


def test_redirected_master_is_refused(workspace):  # noqa: F811
    saved = copied(workspace)
    moved = workspace.directory / "retained-master"
    workspace.master.rename(moved)
    workspace.master.symlink_to(moved)
    with pytest.raises(ValueError, match="directory changed"), opened(workspace, saved, current=lambda: True):
        pytest.fail("redirected snapshot was accepted")


def test_unknown_exclusion_cannot_hide_a_future_result(workspace):  # noqa: F811
    saved = copied(workspace)
    saved["initial"]["manifest"]["excluded"].append(b"ordinary-code.txt".hex())
    saved["initial"]["manifest"]["excluded"].sort()
    with pytest.raises(ValueError, match="exclusions"), opened(workspace, saved, current=lambda: True):
        pytest.fail("unknown exclusion was accepted")


def test_reopened_namespace_lock_excludes_another_real_process(workspace):  # noqa: F811
    saved = copied(workspace)
    source = str(Path(__file__).resolve().parents[1] / "src")
    script = (f"import sys;sys.path.insert(0,{source!r})\n"
              "from poolhouse import lock\n"
              "with open(sys.argv[1],'r+') as held: print(lock.take(held))\n")
    def probe():
        done = subprocess.run([sys.executable, "-I", "-c", script, str(workspace.directory / "workspace.lock")],
                              text=True, capture_output=True, timeout=10, check=True)
        return done.stdout.strip()
    with opened(workspace, saved, current=lambda: True):
        assert probe() == "False"
    assert probe() == "True"


def test_root_replacement_prevents_another_tool_stage(workspace):  # noqa: F811
    workspace.source.rename(workspace.source.with_name("displaced-source"))
    workspace.source.mkdir()
    assert not workspace.current()
    with pytest.raises(ValueError, match="roots changed"):
        workspace.begin()


def test_root_replacement_inside_publication_guard_keeps_old_master(workspace):  # noqa: F811
    stage = workspace.begin()
    (stage.physical / "code.txt").write_text("result")
    previous = workspace.master
    @contextmanager
    def changed(_capture, _revision):
        workspace.source.rename(workspace.source.with_name("displaced-source"))
        workspace.source.mkdir()
        yield
    with pytest.raises(ValueError, match="session changed"):
        workspace.finish(stage, changed, writable=True)
    assert workspace.master == previous and workspace.revision == 0


def test_same_policy_stale_workspace_cannot_overwrite_newer_master(workspace):  # noqa: F811
    original = workspace.owner.workspace_state(workspace.expected)
    with Workspace.reopened(workspace.source, workspace.owner, workspace.expected, current=lambda: True) as active:
        stage = active.begin()
        (stage.physical / "code.txt").write_text("newer result")
        def guard(capture, revision):
            return workspace.owner.coding_guard(workspace.expected, active.persisted,
                active.next_receipt(capture, revision), current=active.current)
        active.finish(stage, guard, writable=True)
        replacement = active.receipt()
    assert workspace.owner.workspace_state(workspace.expected) == replacement
    with pytest.raises(ValueError, match="workspace changed"), workspace.owner.coding_guard(
            workspace.expected, original, replacement, current=lambda: True):
        pytest.fail("stale workspace entered publication")
    assert workspace.owner.workspace_state(workspace.expected) == replacement


def test_unexpected_receipt_change_inside_guard_rolls_back_own_publication(workspace):  # noqa: F811
    original = workspace.owner.workspace_state(workspace.expected)
    stage = workspace.begin()
    (stage.physical / "code.txt").write_text("result")
    previous_master = workspace.master
    @contextmanager
    def guard(capture, revision):
        with workspace.owner.coding_guard(workspace.expected, original, workspace.next_receipt(capture, revision),
                                           current=workspace.current) as graph:
            graph.put_doc(f"coding:{workspace.expected.conversation}", {"workspace": {**original, "revision": 99}})
            yield
    with pytest.raises(ValueError, match="during publication"):
        workspace.finish(stage, guard, writable=True)
    assert workspace.master == previous_master and workspace.revision == 0
    assert workspace.owner.workspace_state(workspace.expected) == original


def test_revocation_after_receipt_save_rolls_back_pointer_and_graph(workspace):  # noqa: F811
    original = workspace.owner.workspace_state(workspace.expected)
    stage = workspace.begin()
    (stage.physical / "code.txt").write_text("result")
    previous_master = workspace.master
    answers = iter([True, True, False])
    def guard(capture, revision):
        return workspace.owner.coding_guard(workspace.expected, original, workspace.next_receipt(capture, revision),
                                            current=lambda: next(answers))
    with pytest.raises(ValueError, match="before committing"):
        workspace.finish(stage, guard, writable=True)
    assert workspace.master == previous_master and workspace.revision == 0
    assert workspace.owner.workspace_state(workspace.expected) == original


def test_missing_existing_receipt_never_rebuilds_a_workspace(workspace):  # noqa: F811
    with conversation_graph.opened(workspace.owner.root) as graph, graph.transaction():
        graph.put_doc(f"coding:{workspace.expected.conversation}", {"version": 1})
    with pytest.raises(ValueError, match="stays interrupted"), Workspace.reopened(
            workspace.source, workspace.owner, workspace.expected, current=lambda: True):
        pytest.fail("missing receipt rebuilt a workspace")


def test_two_processes_reread_master_after_waiting_for_workspace_lock(workspace, binding):  # noqa: F811
    source = str(Path(__file__).resolve().parents[1] / "src")
    script = (f"import sys;sys.path.insert(0,{source!r})\n"
              "import json\nfrom pathlib import Path\n"
              "from poolhouse.fleet.conversations import Conversations\n"
              "from poolhouse.coding_workspace import Workspace\n"
              "class Observed(Conversations):\n"
              " def workspace_state(self, expected):\n"
              "  saved=super().workspace_state(expected)\n"
              "  if not hasattr(self,'observed'):\n"
              "   self.observed=True;print(json.dumps(['pre',saved['revision']]),flush=True)\n"
              "  return saved\n"
              "owner=Observed(Path(sys.argv[1]));expected=owner.coding_state(sys.argv[2])\n"
              "with Workspace.reopened(Path(sys.argv[3]),owner,expected,current=lambda:True) as opened:\n"
              " print(json.dumps(['open',opened.revision,(opened.master/'code.txt').read_text()]),flush=True)\n")
    child = None
    try:
        with Workspace.reopened(workspace.source, workspace.owner, workspace.expected, current=lambda: True) as active:
            child = subprocess.Popen([sys.executable, "-I", "-c", script, str(workspace.owner.root),
                workspace.expected.conversation, str(workspace.source)], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True)
            with selectors.DefaultSelector() as selected:
                selected.register(child.stdout, selectors.EVENT_READ)
                assert selected.select(10), "second bridge did not read its initial receipt"
            assert json.loads(child.stdout.readline()) == ["pre", 0]
            def guard(capture, revision):
                return workspace.owner.coding_guard(workspace.expected, active.persisted,
                    active.next_receipt(capture, revision), current=active.current)
            answer = call(active, binding, "write", {"path": "code.txt", "content": "parent committed result"}, guard=guard)
            assert not answer.get("isError")
        output, errors = child.communicate(timeout=20)
        assert child.returncode == 0, errors
        assert json.loads(output) == ["open", 1, "parent committed result"]
        assert workspace.owner.workspace_state(workspace.expected)["revision"] == 1
    finally:
        if child is not None and child.poll() is None:
            terminate_process_group(child, force=True)
            child.communicate(timeout=10)


def test_policy_revocation_during_capture_rejects_before_any_tool(workspace, monkeypatch):  # noqa: F811
    restore = Workspace._restore_capture
    changed = False
    def revoke(active, saved):
        nonlocal changed
        result = restore(active, saved)
        if not changed:
            changed = True
            workspace.owner.update(workspace.expected.conversation, expected_revision=0, settings={"execution_mode": "plan"})
        return result
    monkeypatch.setattr(Workspace, "_restore_capture", revoke)
    with pytest.raises(ValueError, match="session changed"), Workspace.reopened(
            workspace.source, workspace.owner, workspace.expected, current=lambda: True):
        pytest.fail("revoked policy reached a coding tool")
