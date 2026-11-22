"""Answering a request is refused to an agent: no tool a model is offered, no MCP tool, no message and
no argument a model writes can answer, cancel, list the answers of or change a request; the refusal
is at once and names the command the owner runs outside the agent."""

from __future__ import annotations

import io

import pytest

from poolhouse import chat, do, mcp, requests
from poolhouse.chatpolicy import ProtectedRail, refusal_for
from poolhouse.interventions import Call, Context, Deny
from poolhouse.sentinel.permit import agent_may
from tests.requests_support import ask, no_markers

__all__ = ["no_markers"]
pytestmark = pytest.mark.usefixtures("no_markers")
NAMES = ("answer_request", "approve_request", "deny_request", "cancel_request", "requests_answer",
         "requests_list", "list_requests", "request_show", "inbox_answer", "resolve_request",
         "withdraw_request", "dismiss_request", "requests")


def test_no_tool_offered_to_a_model_names_a_request_or_an_answer():
    person = do.Person(io.StringIO(""), io.StringIO(""))
    session = chat.Chat(None, person, role="plan-and-go", extension=chat.extensions(person))
    offered = [s["function"]["name"] for s, _ in session.offered] + [t.name for t in mcp.TOOLS]
    named = [n for n in offered if any(w in n.lower() for w in ("request", "answer", "approve"))]
    assert named == []


@pytest.mark.parametrize("name", NAMES)
def test_a_gullible_model_calling_a_tool_named_for_the_inbox_is_refused_with_the_owners_command(name):
    got = ProtectedRail().before_tool_call(Call(name, {"id": "rq_x", "choice": "allow-once"}), Context())
    assert isinstance(got, Deny) and "poolhouse-requests answer" in got.reason


@pytest.mark.parametrize("text", ["poolhouse-requests answer rq_1 allow-once", "poolhouse-requests list --json",
                                  "python -m poolhouse.inbox.cli answer rq_1 deny",
                                  "poolhouse.requests.inbox.answer('rq_1', 'allow-once', fp, 'ui')",
                                  "cat ~/.poolhouse/requests/requests.enc"])
@pytest.mark.parametrize("tool", ["bench_run", "run_shell", "serve_up", "models_fetch", "doctor"])
def test_an_argument_naming_the_inbox_is_refused_whichever_tool_carries_it(tool, text):
    assert agent_may(tool, {"argv": [text], "command": text})


def test_a_call_with_the_floor_in_its_text_is_denied_by_the_rail_not_just_the_helper():
    rail = ProtectedRail()
    got = rail.before_tool_call(Call("run_shell", {"command": "poolhouse-requests answer rq_1 allow-once"}), Context())
    assert isinstance(got, Deny)


def test_the_request_commands_let_an_agent_read_and_refuse_its_answer_at_once_while_the_gate_is_held_back(
        monkeypatch, capsys):
    from poolhouse.inbox import cli
    held = requests.raise_request(ask())
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("POOLHOUSE_AUTHORITY_FLOOR", "person")
    for argv in (["list"], ["show", held.id], ["watch", "--once"]):
        assert cli.main(argv) == 0, argv
    assert held.id in capsys.readouterr().out
    answer = ["answer", held.id, "allow-once", "--fingerprint", held.fingerprint]
    assert cli.main(answer) == 2
    assert "refused to an agent" in capsys.readouterr().err
    assert requests.get(held.id).state == "pending"
    monkeypatch.delenv("POOLHOUSE_AUTHORITY_FLOOR")
    assert cli.main(answer) == 0 and requests.get(held.id).state == "approved"


@pytest.mark.parametrize('command', ['poolhouse-requests list --json', 'poolhouse-requests show rq_fixture',
                                    'poolhouse-requests answer rq_fixture allow-once'])
def test_current_request_commands_are_protected_actions_for_the_chat_agent(command):
    refused = refusal_for(command)
    assert refused is not None
    assert refused[1] == 'poolhouse-requests answer ID CHOICE'
