"""Separate processes race and crash around the durable coding action owner."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, replace

import pytest

from poolhouse import coding_actions, pi_tools
from poolhouse.coding_workspace import Workspace
from poolhouse.harnesspolicy import Decision
from poolhouse.platform import terminate_process_group

from .test_coding_actions import IDENTITY, IMPLEMENTATION, action, owner  # noqa: F401
from .test_coding_workspace import (  # noqa: F401
    binding,
    coding_owner,
    private_snapshot_storage,
    workspace,
)

pytest_plugins = ["tests.sandbox_kit"]

WORKER = '''import json,os,sys
from pathlib import Path
from poolhouse import coding_actions,pi_tools
from poolhouse.coding_workspace import Workspace
from poolhouse.fleet.conversations import Conversations
body=json.loads(sys.argv[1])
store=Conversations(body['root'])
expected=store.coding_state(body['cid'])
if body['mode']=='initialize':
    execution=store.begin_execution(expected,coding_actions.Intent(body['message'],body['identity'],body['workspace'],body['implementation']),current=lambda:True)
    print(json.dumps(execution.id),flush=True)
else:
    execution=coding_actions.Execution(expected,**body['execution'])
    action=coding_actions.Action(**body['action'])
    try:
        started=store.start_action(execution,action,current=lambda:True)
    except ValueError:
        started=False
    if body['mode']=='consume':
        if started:
            with open(body['effect'],'a') as stream: stream.write('one effect\\n')
        print(json.dumps(started),flush=True)
    else:
        assert started
        if body['mode']=='after-consumption': os._exit(17)
        with Workspace.reopened(Path(body['source']),store,expected,current=lambda:True) as scope:
            result={}
            def execute(stage):
                configuration={**body['binding'],'root':str(stage.physical),'tool_root':str(stage.alias),'write':True}
                answer=pi_tools.execute(configuration,{'tool':action.tool,'input':json.loads(action.arguments)})
                assert not answer.get('isError'),answer
                result.update(answer)
                if body['mode']=='after-child': os._exit(17)
                return answer
            def guard(capture,revision):
                if body['mode']=='after-capture': os._exit(17)
                return store.action_guard(execution,action,scope.next_receipt(capture,revision),result,current=lambda:True)
            scope.run(execute,guard,writable=True)
        os._exit(17)
'''


def spawned(body):
    return subprocess.Popen([sys.executable, "-c", WORKER, json.dumps(body)], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)


def finished(process, *, code=0):
    try:
        out, err = process.communicate(timeout=20)
        assert process.returncode == code, err
        return json.loads(out) if out else None
    finally:
        if process.poll() is None:
            terminate_process_group(process, force=True)
            process.communicate(timeout=3)


def context(store, execution, call, mode, **more):
    return {"root": str(store.root), "cid": execution.policy.conversation,
            "execution": {key: value for key, value in asdict(execution).items() if key != "policy"},
            "action": asdict(call), "mode": mode, **more}


def test_two_processes_initialize_only_one_run_for_the_same_user_message(owner):  # noqa: F811
    store, execution, scope = owner
    store.append(execution.policy.conversation, "user", "Start this explicit new coding turn")
    body = {"root": str(store.root), "cid": execution.policy.conversation, "mode": "initialize", "message": 1,
            "identity": IDENTITY, "workspace": scope.receipt(), "implementation": IMPLEMENTATION}
    first, second = spawned(body), spawned(body)
    ids = [finished(first), finished(second)]
    assert ids[0] == ids[1] and ids[0] != execution.id
    repeated = store.begin_execution(execution.policy, coding_actions.Intent(1, IDENTITY, scope.receipt(), IMPLEMENTATION), current=lambda: True)
    assert repeated.id == ids[0]


@pytest.mark.parametrize("distinct", [False, True])
def test_two_processes_consume_only_one_action_on_a_workspace(owner, tmp_path, distinct):  # noqa: F811
    store, execution, scope = owner
    first = action(scope)
    second = action(scope, "different-id", content="different") if distinct else first
    store.reserve_action(execution, first, current=lambda: True)
    store.reserve_action(execution, second, current=lambda: True)
    effect = tmp_path / "effect.txt"
    children = [spawned(context(store, execution, call, "consume", effect=str(effect))) for call in (first, second)]
    assert sorted(finished(child) for child in children) == [False, True]
    assert effect.read_text() == "one effect\n"


@pytest.mark.parametrize("boundary", ["after-consumption", "after-child", "after-capture", "after-commit"])
def test_crash_windows_never_repeat_the_consumed_stock_tool(owner, binding, boundary):  # noqa: F811
    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    child = spawned(context(store, execution, call, boundary, source=str(scope.source), binding=binding))
    finished(child, code=17)
    committed = boundary == "after-commit"
    row = store.action_state(execution, call, current=lambda: True)
    assert row["state"] == ("completed" if committed else "running")
    assert not store.start_action(execution, call, current=lambda: True)
    with Workspace.reopened(scope.source, store, execution.policy, current=lambda: True) as resumed:
        assert (resumed.master / "code.txt").read_text() == ("after" if committed else "before")
        assert resumed.revision == int(committed)
    assert (scope.source / "code.txt").read_text() == "before"


def test_missing_action_tombstones_interrupt_every_new_intent(owner):  # noqa: F811
    from poolhouse.fleet import conversation_graph

    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    with conversation_graph.opened(store.root) as graph, graph.transaction():
        key = f"coding:{execution.policy.conversation}"
        saved = graph.get_doc(key, {})
        saved["executions"][execution.id]["actions"].clear()
        graph.put_doc(key, saved)
    with pytest.raises(ValueError, match="history is missing"):
        store.reserve_action(execution, replace(call, id="fresh-invented-id"), current=lambda: True)
    with pytest.raises(ValueError, match="history is missing"):
        store.begin_execution(execution.policy, coding_actions.Intent(0, IDENTITY, scope.receipt(), IMPLEMENTATION), current=lambda: True)


def test_readonly_stock_tool_completes_with_only_private_epoch_bookkeeping(owner, binding):  # noqa: F811
    store, execution, scope = owner
    call = coding_actions.Action.prepare("read-1", "read", {"path": "code.txt"}, scope.receipt(), Decision("allow", "safe", "read selected file"))
    store.reserve_action(execution, call, current=lambda: True)
    assert store.start_action(execution, call, current=lambda: True)
    result = {}
    def execute(stage):
        answer = pi_tools.execute({**binding, "root": str(stage.physical), "tool_root": str(stage.alias), "write": False},
                                  {"tool": "read", "input": {"path": "code.txt"}})
        result.update(answer)
        return answer
    def guard(capture, revision):
        return store.action_guard(execution, call, scope.next_receipt(capture, revision), result, current=lambda: True)
    assert not scope.run(execute, guard, writable=False).get("isError")
    row = store.action_state(execution, call, current=lambda: True)
    assert row["state"] == "completed" and row["result_digest"] == coding_actions.digest(result)
    assert scope.revision == 1 and store.workspace_state(execution.policy) == scope.receipt()
    assert (scope.source / "code.txt").read_text() == (scope.master / "code.txt").read_text() == "before"


@pytest.mark.parametrize("mutation", ["content", "mode", "missing"])
def test_readonly_completion_refuses_any_changed_snapshot_manifest(owner, mutation):  # noqa: F811
    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    assert store.start_action(execution, call, current=lambda: True)
    def execute(stage):
        target = stage.physical / "code.txt"
        if mutation == "content":
            target.write_text("changed")
        elif mutation == "mode":
            target.chmod(0o755)
        else:
            target.unlink()
        return {"content": []}
    def guard(capture, revision):
        pytest.fail("Changed readonly bytes reached the publication guard")
    with pytest.raises(ValueError, match="read-only coding tool changed"):
        scope.run(execute, guard, writable=False)
    assert scope.revision == 0 and (scope.master / "code.txt").read_text() == "before"
    assert store.action_state(execution, call, current=lambda: True)["state"] == "running"


def test_stale_readonly_stage_cannot_complete_after_an_identical_manifest_epoch(owner):  # noqa: F811
    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    assert store.start_action(execution, call, current=lambda: True)
    first, stale = scope.begin(), scope.begin()
    result = {"content": []}
    def guard(capture, revision):
        return store.action_guard(execution, call, scope.next_receipt(capture, revision), result, current=lambda: True)
    scope.finish(first, guard, writable=False)
    with pytest.raises(ValueError, match="changed"):
        scope.finish(stale, guard, writable=False)
    assert scope.revision == 1 and store.action_state(execution, call, current=lambda: True)["state"] == "completed"
