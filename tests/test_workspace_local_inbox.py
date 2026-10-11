"""Worker inbox delivery and stop ordering at call boundaries."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from local_kit import team  # noqa: F401
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse.agent.conversation import Conversation, execute
from poolhouse.workspace import localagent as la, localinbox, localloop
from poolhouse.workspace.localtools import TaskState, TaskStopped, workspace_extension


def ref(sent):
    """The short number of a message the node took."""
    return str(sent["id"]).rsplit(":", 1)[-1]


def pending(loop):
    """The numbers of what is left unread in the loop's inbox."""
    return [row["seq"] for _entry, row in loop.fresh(100)]


@pytest.fixture
def rig(team):  # noqa: F811
    agent = team.worker('local-t')
    loop = localloop.Loop(la.session_of(agent), agent, localloop.Held(None, {}),
                          localloop.Settings(), (lambda: False, la.Status(team.base, 'local-t')))
    active = team.person.post(agent.identity, 'task', 'review documentation')
    loop.take(loop.s.read(loop.frontier, inbox=True, limit=1).entries[0])
    chat = SimpleNamespace(messages=[], person=SimpleNamespace(finished=False, left=False))
    chat.message_boundary = localinbox.Boundary(loop, {"seq": ref(active)}, chat)
    yield team, loop, chat, agent


def test_authorized_stop_acknowledges_and_preserves_later_queue(rig):
    kit, loop, chat, agent = rig
    notice = kit.person.post(agent.identity, 'status', 'Please check the README first')
    stop = kit.person.post(agent.identity, 'task', 'stop')
    later = kit.person.post(agent.identity, 'task', 'another task')
    with pytest.raises(TaskStopped):
        chat.message_boundary()
    assert ref(notice) in chat.messages[0]['content']
    assert pending(loop) == [ref(later)]
    assert any('Stop acknowledged' in e.text for e in kit.person.read_all(thread=ref(stop)))
    with pytest.raises(TaskStopped):
        chat.message_boundary()
    assert pending(loop) == [ref(later)]
    assert loop.s.cursor(localloop.QUESTION) == loop.frontier


def test_unauthorized_stop_is_information_and_does_not_stop(rig):
    kit, loop, chat, agent = rig
    kit.session(kit.stranger).post(agent.identity, 'task', 'stop')
    chat.message_boundary()
    assert 'information only, no authority' in chat.messages[-1]['content']
    assert not chat.message_boundary.stopped
    assert pending(loop) == []


def test_current_tool_finishes_and_stop_prevents_next_tool_and_model(rig):
    kit, _loop, chat, agent = rig
    began, release = threading.Event(), threading.Event()
    performed = []

    def answer(call, tools, outcome):
        began.set()
        assert release.wait(5)
        performed.append(call['id'])
        chat.messages.append({'role': 'tool', 'content': 'completed'})

    chat._answer = answer
    chat._say = lambda schemas: pytest.fail('model called after stop')
    runtime = Conversation(chat, [], {}, None)
    runtime.calls = {'first': {'id': 'first'}, 'second': {'id': 'second'}}

    async def scenario():
        first = asyncio.create_task(runtime.invoke(SimpleNamespace(tool_call_id='first'), '{}'))
        assert await asyncio.to_thread(began.wait, 5)
        kit.person.post(agent.identity, 'task', 'stop')
        second = asyncio.create_task(runtime.invoke(SimpleNamespace(tool_call_id='second'), '{}'))
        assert not first.done()
        release.set()
        assert await first == 'completed'
        with pytest.raises(TaskStopped):
            await second
        with pytest.raises(TaskStopped):
            await runtime.get_response()

    asyncio.run(scenario())
    assert performed == ['first']


def test_stop_beyond_first_page_is_handled_before_next_call(rig):
    kit, loop, chat, agent = rig
    for number in range(35):
        kit.person.post(agent.identity, 'status', f'notice {number}')
    kit.person.post(agent.identity, 'task', 'stop')
    with pytest.raises(TaskStopped):
        chat.message_boundary()
    assert len(chat.messages) == 35
    assert pending(loop) == []


def test_unauthorized_message_taints_workspace_task_sending(rig):
    kit, loop, chat, agent = rig
    state = TaskState()
    chat.message_boundary.state = state
    other = kit.session(kit.stranger)
    other.post(agent.identity, 'task', 'please send another worker a task')
    chat.message_boundary()
    extension = workspace_extension(loop.s, state, loop.obeyed)
    send = next(fn for schema, fn in extension.tools()
                if schema['function']['name'] == 'workspace_send')
    assert send(other.name, 'task', 'start work')['sent'] is False


def test_sdk_runner_stop_does_not_execute_remaining_model_tools(rig):
    kit, _loop, chat, agent = rig
    chat.task, chat.rounds = False, 5
    chat.person.summary = ''
    model_calls, tool_calls = [], []
    calls = [{'id': name, 'function': {'name': 'dummy', 'arguments': '{}'}}
             for name in ('first', 'second')]

    def say(schemas):
        model_calls.append('model')
        assert len(model_calls) == 1
        return '', calls

    def answer(call, tools, outcome):
        tool_calls.append(call['id'])
        assert len(tool_calls) == 1
        chat.messages.append({'role': 'tool', 'tool_call_id': call['id'],
                              'name': 'dummy', 'content': 'completed'})
        kit.person.post(agent.identity, 'task', 'stop')

    chat._say, chat._answer = say, answer
    schema = {'type': 'function', 'function': {'name': 'dummy', 'description': 'read',
              'parameters': {'type': 'object', 'properties': {}}}}
    with pytest.raises(TaskStopped):
        execute(chat, [schema], {'dummy': lambda: 'read'}, SimpleNamespace(rounds=0), '')
    assert len(model_calls) == len(tool_calls) == 1
