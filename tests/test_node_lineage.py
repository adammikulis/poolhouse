"""Which process registered a main session, against a real node: any process registers it (a live sibling gets a token of its own and
cannot spend the grants of the terminal that runs the session; a terminal whose processes ended hands it to the next one), a session holds
a few tokens, and a client whose token the node does not know registers again by itself and asks once more, with nobody asked to do
anything."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import subprocess
import sys
import termios

import pytest
from node_kit import SRC, STRIPPED

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


def test_another_terminal_registers_a_registered_main_session_and_the_first_keeps_it(lead):
    node = lead
    same = registered_by_a_child(node, "lead", own_session=False)
    assert same["name"] == node.lead.name, "a process started from the registrant's own terminal is the same lineage"
    apart = registered_by_a_child(node, "lead", own_session=True)
    assert apart["name"] == node.lead.name and "token" in apart and "refused" not in apart, f"another terminal registers the session too: {apart}"
    assert node.session(node.lead).whoami().name == node.lead.name, "and ended nothing the first held"
    assert board_session.Session(node.client, node.board, apart["token"], apart["name"]).whoami().name == node.lead.name
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
    "    held.call('task_mode', mode='open')\n"
    "    out['spent'] = True\n"
    "except Denied as err:\n"
    "    out['refused'] = str(err)\n"
    "    out['hint'] = err.grant\n"
    "print(json.dumps(out))\n"
)


def test_a_token_read_from_its_file_in_another_live_terminal_reads_and_spends_no_grant(lead):
    node = lead
    node.allow(node.lead, "task_authority")
    args = (str(node.state), node.board, node.lead.name)
    here = json.loads(child_output(SPEND, *args, node=node, own_terminal=False))
    assert here == {"whoami": node.lead.name, "spent": True}, "the terminal that registered the session spends its grant"
    apart = json.loads(child_output(SPEND, *args, node=node, own_terminal=True))
    assert apart["whoami"] == node.lead.name, "a process of another terminal that read the file is the session for reading"
    assert "spent" not in apart and apart["hint"] is None, apart
    said = apart["refused"]
    assert "still running" in said and node.lead.name in said, said
    for word in ("rebind", "phone", "typed", "new session"):
        assert word not in said, (word, said)
    uses = [e for e in board_grants.log(node.session(node.lead)) if e["op"] == "use"]
    assert len(uses) == 1, "only the use from the registering terminal was written"


def test_a_client_registers_a_main_session_without_a_token_and_a_subagent_with_its_parents(lead, monkeypatch):
    node = lead
    sent: list[str] = []
    real = node.client.call

    def spy(method, board="", token="", **params):
        if method == "register":
            sent.append(token)
        return real(method, board, token, **params)

    monkeypatch.setattr(node.client, "call", spy)
    native = board_session.Native("claude-sonnet-5-5", "claude-code", "lead")
    credentials.store(node.state, node.board, node.lead.name, "0" * 64)
    again = board_session.register(node.client, node.board, native)
    assert again.name == node.lead.name and sent == [""], "no token goes with the registration of a main session, so a stale one in the file is no obstacle"
    sent.clear()
    kid = board_session.register(node.client, node.board, board_session.Native("claude-sonnet-5-5", "claude-code", "kid"), again.token)
    assert kid.parent == node.lead.name and sent == [again.token], "a parent's token is what a subagent presents"


def test_a_session_holds_eight_tokens_and_past_them_the_oldest_but_the_first_goes(lead):
    node = lead
    native = board_session.Native("claude-sonnet-5-5", "claude-code", "lead")

    def whoami(token: str) -> str:
        return board_session.Session(node.client, node.board, token, node.lead.name).whoami().name

    tokens = [node.lead.token, *(board_session.register(node.client, node.board, native).token for _ in range(7))]
    assert [whoami(t) for t in tokens] == [node.lead.name] * 8
    for _ in range(3):
        assert registered_by_a_child(node, "lead", own_session=True)["name"] == node.lead.name
    assert whoami(tokens[0]) == node.lead.name, "the first token is never the one that goes"
    ninth = board_session.register(node.client, node.board, native).token
    assert whoami(tokens[0]) == whoami(ninth) == node.lead.name, "the first token stays"
    with pytest.raises(Denied):
        whoami(tokens[1])


EXIT = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from poolhouse.board import session\n"
    "from poolhouse.board.client import Client\n"
    "state, board, native = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n"
    "got = session.register(Client(state), board, session.Native('claude-sonnet-5-5', 'claude-code', native))\n"
    "print(json.dumps({'name': got.name, 'token': got.token}))\n"
)


def test_a_session_registered_from_a_terminal_that_has_ended_moves_to_the_process_that_registers_it_next(workspace_node):
    node = workspace_node
    first = json.loads(child_output(EXIT, str(node.state), node.board, "walker", node=node, own_terminal=True))
    # the terminal's only process ended with the command: nothing of the lineage the session was registered from runs
    second = json.loads(child_output(EXIT, str(node.state), node.board, "walker", node=node, own_terminal=True))
    assert second["name"] == first["name"], "the session resumed in another terminal is the session it was"
    with pytest.raises(Denied, match="the token is not known"):
        board_session.Session(node.client, node.board, first["token"], first["name"]).whoami()
    assert board_session.Session(node.client, node.board, second["token"], second["name"]).whoami().name == first["name"]
    # a person is asked for nothing: this test's own process registers it too, and it is simply a sibling of a lineage that has ended
    mine = board_session.register(node.client, node.board, board_session.Native("claude-sonnet-5-5", "claude-code", "walker"))
    assert mine.name == first["name"]


def test_a_command_whose_token_the_node_does_not_know_registers_its_session_again_and_runs(lead):
    node = lead
    credentials.store(node.state, node.board, node.lead.name, "0" * 64)
    done = node.cli("whoami", "--json", who=node.lead, env={"CLAUDE_CODE_SESSION_ID": "lead"})
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert json.loads(done.stdout)["id"] == node.lead.name, "the command ran as the session it always ran as"
    fresh = credentials.load(node.state, node.board, node.lead.name)
    assert fresh != "0" * 64 and node.client.call("whoami", node.board, fresh)["name"] == node.lead.name, "the new token is kept for the next command"


def test_a_subagent_command_whose_token_the_node_does_not_know_is_registered_again_by_its_parent_and_runs(lead):
    node = lead
    kid = node.member("kid", parent=node.lead)
    credentials.store(node.state, node.board, kid.name, "0" * 64)
    done = node.cli("whoami", "--json", env={"POOLHOUSE_SUBAGENT": "kid"})
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert json.loads(done.stdout)["id"] == kid.name and json.loads(done.stdout)["parent"] == node.lead.name
    fresh = credentials.load(node.state, node.board, kid.name)
    assert fresh != "0" * 64 and node.client.call("whoami", node.board, fresh)["name"] == kid.name


def test_a_session_the_node_retired_for_idleness_comes_back_with_its_next_command(lead):
    node = lead
    node.session(node.lead).retire()
    with pytest.raises(Denied, match="the token is not known"):
        node.session(node.lead).whoami()
    done = node.cli("whoami", "--json", who=node.lead, env={"CLAUDE_CODE_SESSION_ID": "lead"})
    assert done.returncode == 0 and json.loads(done.stdout)["id"] == node.lead.name, (done.stdout, done.stderr)


def test_a_session_its_parent_ended_for_cause_stays_ended_and_its_command_says_why(lead):
    node = lead
    kid = node.member("kid", parent=node.lead)
    node.session(node.lead).retire(kid.name, "revoked")
    done = node.cli("whoami", "--json", env={"POOLHOUSE_SUBAGENT": "kid"})
    assert done.returncode != 0, (done.stdout, done.stderr)
    said = done.stdout + done.stderr
    assert "ended for cause (revoked)" in said, said
    for word in ("rebind", "phone", "typed"):
        assert word not in said, (word, said)
    assert node.session(node.lead).whoami().name == node.lead.name, "and ended nothing of the parent's"


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
