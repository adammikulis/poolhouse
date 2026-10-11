"""Which process registered a main session, against a real node: another terminal session cannot register as it, a refusal gives
out and ends nothing, a session holds a few tokens and is refused more, and a person moves it to their own terminal with a
key's signature and a typed yes."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import subprocess
import sys
import termios

import pytest
from node_kit import SRC, STRIPPED, TYPED

from poolhouse.board import credentials, grants as board_grants, session as board_session
from poolhouse.board.client import Denied

pytest_plugins = ["node_kit"]
REGISTER = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from poolhouse.board import session\n"
    "from poolhouse.board.client import Client, Denied, Quota\n"
    "state, board, native = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n"
    "try:\n"
    "    got = session.register(Client(state), board, session.Native('claude-sonnet-5-5', 'claude-code', native))\n"
    "    print(json.dumps({'name': got.name, 'token': got.token}))\n"
    "except (Denied, Quota) as err:\n"
    "    print(json.dumps({'refused': str(err), 'code': err.code}))\n"
)


def child_output(code: str, *args: str, node, own_terminal: bool) -> str:
    """What a process running ``code`` prints, started from this test (in its terminal) or leading a terminal of its own."""
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED} | {"PYTHONPATH": SRC, "POOLHOUSE_HOME": str(node.state.parent)}
    argv = [sys.executable, "-c", code, *args]
    if not own_terminal:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60, check=False)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip().splitlines()[-1]
    leader, follower = pty.openpty()
    try:
        proc = subprocess.Popen(argv, env=env, stdin=follower, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        out, err = proc.communicate(timeout=60)
    finally:
        os.close(follower)
        os.close(leader)
    assert proc.returncode == 0, err
    return out.strip().splitlines()[-1]


def registered_by_a_child(node, native: str, *, own_session: bool) -> dict:
    """What a process starting from this test registering ``native`` gets: from this test's terminal, or from one of its own."""
    return json.loads(child_output(REGISTER, str(node.state), node.board, native, node=node, own_terminal=own_session))


@pytest.fixture
def lead(workspace_node):
    workspace_node.lead = workspace_node.member("lead")
    return workspace_node


def test_another_terminal_session_does_not_get_a_token_for_a_registered_main_session(lead):
    node = lead
    same = registered_by_a_child(node, "lead", own_session=False)
    assert same["name"] == node.lead.name, "a process started from the registrant's own terminal is the same lineage"
    apart = registered_by_a_child(node, "lead", own_session=True)
    assert "token" not in apart and apart["code"] == "denied" and "another process lineage" in apart["refused"] and "rebind" in apart["refused"], apart
    assert node.lead.token not in apart["refused"] and same["token"] not in apart["refused"]
    assert node.session(node.lead).whoami().name == node.lead.name, "the refusal ended nothing"
    stranger = registered_by_a_child(node, "someone-else", own_session=True)
    assert stranger["name"] != node.lead.name and "token" in stranger, "a session nobody registered yet registers from anywhere"


SPEND = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from poolhouse.board import credentials, session\n"
    "from poolhouse.board.client import Client, Denied\n"
    "state, board, name = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n"
    "held = session.Session(Client(state), board, credentials.load(state, board, name), name)\n"
    "out = {'whoami': held.whoami().name}\n"
    "try:\n"
    "    held.call('land_brake', pause=False)\n"
    "    out['spent'] = True\n"
    "except Denied as err:\n"
    "    out['refused'] = str(err)\n"
    "    out['hint'] = err.grant\n"
    "print(json.dumps(out))\n"
)


def test_a_token_read_from_its_file_in_another_terminal_reads_and_spends_no_grant(lead):
    node = lead
    node.allow(node.lead, "land_control")
    args = (str(node.state), node.board, node.lead.name)
    here = json.loads(child_output(SPEND, *args, node=node, own_terminal=False))
    assert here == {"whoami": node.lead.name, "spent": True}, "the terminal that registered the session spends its grant"
    apart = json.loads(child_output(SPEND, *args, node=node, own_terminal=True))
    assert apart["whoami"] == node.lead.name, "a process of another terminal that read the file is the session for reading"
    assert "spent" not in apart and "rebind" in apart["refused"] and apart["hint"] is None, apart
    uses = [e for e in board_grants.log(node.session(node.lead)) if e["op"] == "use"]
    assert len(uses) == 1, "only the use from the registering terminal was written"


def test_a_session_registered_before_presents_its_own_token_when_it_registers_again_and_falls_back_when_that_token_is_gone(lead, monkeypatch):
    node = lead
    sent: list[str] = []
    real = node.client.call

    def spy(method, board="", token="", **params):
        if method == "register":
            sent.append(token)
        return real(method, board, token, **params)

    monkeypatch.setattr(node.client, "call", spy)
    native = board_session.Native("claude-sonnet-5-5", "claude-code", "lead")
    again = board_session.register(node.client, node.board, native)
    assert again.name == node.lead.name and sent == [node.lead.token], "the token the client keeps goes with the registration"
    sent.clear()
    credentials.store(node.state, node.board, node.lead.name, "0" * 64)
    third = board_session.register(node.client, node.board, native)
    assert third.name == node.lead.name and sent == ["0" * 64, ""], "a token the node no longer holds is tried once and the session registers without it"
    sent.clear()
    kid = board_session.register(node.client, node.board, board_session.Native("claude-sonnet-5-5", "claude-code", "kid"), third.token)
    assert kid.parent == node.lead.name and sent == [third.token], "a parent's token is what a subagent presents"


def test_a_session_holds_eight_tokens_and_past_them_the_oldest_but_the_first_goes(lead):
    node = lead
    native = board_session.Native("claude-sonnet-5-5", "claude-code", "lead")

    def whoami(token: str) -> str:
        return board_session.Session(node.client, node.board, token, node.lead.name).whoami().name

    tokens = [node.lead.token, *(board_session.register(node.client, node.board, native).token for _ in range(7))]
    assert [whoami(t) for t in tokens] == [node.lead.name] * 8
    for _ in range(3):
        assert registered_by_a_child(node, "lead", own_session=True)["code"] == "denied"
    assert [whoami(t) for t in tokens] == [node.lead.name] * 8, "refused registrations ended nothing"
    ninth = board_session.register(node.client, node.board, native).token
    assert whoami(tokens[0]) == whoami(ninth) == node.lead.name, "the first token stays"
    with pytest.raises(Denied):
        whoami(tokens[1])
    assert [whoami(t) for t in tokens[2:]] == [node.lead.name] * 6


def test_a_move_signed_in_another_terminal_moves_nothing(lead):
    node = lead
    key = node.keep_person_key(node.lead)
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from poolhouse.board import grants\n"
        "from poolhouse.board.client import Client\n"
        "print(json.dumps(grants.here(Client(Path(sys.argv[1]))).anchor))\n"
    )
    elsewhere = json.loads(child_output(code, str(node.state), node=node, own_terminal=True))
    signed = board_grants.sign_rebind(key, node.board, node.lead.name, board_grants.Where(node.client.call("hello")["device"], elsewhere))
    with pytest.raises(Denied, match="another terminal"):
        node.client.call("rebind", node.board, "", harness="claude-code", session="lead", **signed)
    assert node.session(node.lead).whoami().name == node.lead.name, "nothing was moved"


def test_a_signed_move_carries_the_session_to_the_process_that_sends_it_and_ends_the_tokens_it_held(lead):
    node = lead
    key = node.keep_person_key(node.lead)
    done = board_grants.rebind(node.client, node.board, "claude-code", "lead", key)
    assert done["name"] == node.lead.name and done["moved"] is True and done["tokens_ended"] >= 1
    with pytest.raises(Denied):
        node.session(node.lead).whoami()
    fresh = board_session.register(node.client, node.board, board_session.Native("claude-sonnet-5-5", "claude-code", "lead"))
    assert fresh.name == node.lead.name and board_session.Session(node.client, node.board, fresh.token, fresh.name).whoami().name == fresh.name


def test_a_captured_move_is_not_used_twice_and_is_for_one_node_and_one_session(lead):
    node = lead
    key = node.keep_person_key(node.lead)
    here = board_grants.here(node.client)
    signed = board_grants.sign_rebind(key, node.board, node.lead.name, here)
    sent = {"harness": "claude-code", "session": "lead", **signed}
    assert node.client.call("rebind", node.board, "", **sent)["moved"] is True
    with pytest.raises(Denied, match="used already"):
        node.client.call("rebind", node.board, "", **sent)
    elsewhere = {"harness": "claude-code", "session": "lead", **board_grants.sign_rebind(key, node.board, node.lead.name, board_grants.Where("ab" * 32, here.anchor))}
    with pytest.raises(Denied, match="for this node"):
        node.client.call("rebind", node.board, "", **elsewhere)
    stale = {"harness": "claude-code", "session": "lead", **board_grants.sign_rebind(key, node.board, node.lead.name, here, now_ms=1)}
    with pytest.raises(Exception, match="five minutes"):
        node.client.call("rebind", node.board, "", **stale)


def test_a_person_at_a_new_terminal_moves_the_session_there_and_the_harness_registers_from_that_terminal(lead):
    node = lead
    node.keep_person_key(node.lead)
    script = (f"{sys.executable} -m poolhouse.workspace.cli rebind claude-code lead --json; "
              f"{sys.executable} -c \"{REGISTER}\" {node.state} {node.board} lead")
    code, out, err = node.terminal(program=["/bin/sh", "-c", script], typed=TYPED, timeout=120)
    assert code == 0, (out, err)
    assert '"moved"' in out and node.lead.name in out and "Type yes to sign it: " in out and "move the session " + node.lead.name in out, out
    assert '"token"' in out, "the harness started in that terminal registers as the session"
    with pytest.raises(Denied):
        node.session(node.lead).whoami()
    refused = registered_by_a_child(node, "lead", own_session=False)
    assert refused["code"] == "denied", "the terminal the session was in before no longer registers it"


def test_the_move_asks_the_person_and_a_no_moves_nothing(lead):
    node = lead
    node.keep_person_key(node.lead)
    script = f"{sys.executable} -m poolhouse.workspace.cli rebind claude-code lead --json"
    code, out, err = node.terminal(program=["/bin/sh", "-c", script], typed={**TYPED, "Type yes": "no"}, timeout=120)
    assert code != 0 and "confirmation" in out + err, (out, err)
    assert node.session(node.lead).whoami().name == node.lead.name, "nothing was moved"


def test_an_agent_marked_process_is_not_offered_the_key_to_move_a_session(lead):
    node = lead
    node.keep_person_key(node.lead)
    code, out, err = node.terminal("rebind", "claude-code", "lead", "--json", who=node.lead, env={"CLAUDECODE": "1"}, typed=TYPED, timeout=120)
    assert code != 0 and node.session(node.lead).whoami().name == node.lead.name, (out, err)


API_SESSION = "from poolhouse.api import _node\nprint(_node.session().name)\n"


def api_session_of_a_child(node, *, own_session: bool) -> str:
    return child_output(API_SESSION, node=node, own_terminal=own_session)


def test_a_program_that_registers_a_session_of_its_own_gets_one_per_terminal(lead):
    node = lead
    here = [api_session_of_a_child(node, own_session=False) for _ in range(2)]
    assert here[0] == here[1], "the same terminal gets the same session again"
    apart = [api_session_of_a_child(node, own_session=True) for _ in range(2)]
    assert len({*apart, here[0]}) == 3, "each terminal has a session of its own, and none is refused for the lineage of another"


PERSON_SESSION = (
    "import sys\n"
    "from pathlib import Path\n"
    "from poolhouse.board import person\n"
    "from poolhouse.board.client import Client\n"
    "print(person.on(Client(Path(sys.argv[1])), sys.argv[2]).name)\n"
)


def test_the_persons_session_is_one_for_each_terminal_since_a_token_spends_a_grant_only_where_it_registered(lead):
    node = lead
    args = (str(node.state), node.board)
    here = [child_output(PERSON_SESSION, *args, node=node, own_terminal=False) for _ in range(2)]
    assert here[0] == here[1], "the same terminal gets the same session again"
    apart = child_output(PERSON_SESSION, *args, node=node, own_terminal=True)
    assert apart != here[0], "another terminal has a session of its own, and is not handed the token another terminal registered"
