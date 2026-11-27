"""Every harness the launcher starts reaches its lead by the same two tools, `ask` and `note`, and by nothing else.

The launch is real: the stage, both bridges, the proof under the real Seatbelt, the harness's own launcher (`poolhouse-claude`, `-codex`, `-pi`,
`-opencode`), a real node and a lead session on it. What is not real is the harness binary (a marked script that is never started: the runner reads
what the launcher rendered for it) and the model behind it (the kit's plumbing server, `CANNED-NOT-A-MODEL`). The server each harness is told to
start is run as a real process and spoken to over MCP, as the harness would: it lists the two tools, a note reaches the lead's inbox labelled as
data, a question waits for the lead's answer, and a workspace verb is no tool. The
launch's own hook allows the two tools. A server that does not answer fails the test at once. The brief each harness reads names those two tools and no command
that is not in the box (the live failure: a local model was told to run `poolhouse-workspace inbox` and said it was not available).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import tomllib
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from poolhouse import claude, codex, harnessing, lead_bridge, opencode, pi
from poolhouse.isolation import client, session_bridge, session_verbs
from tests import isolation_kit as kit

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]
pytestmark = pytest.mark.usefixtures("confinable", "from_a_terminal")


def servers(harness: str, home: Path) -> tuple[list[str], str]:
    """The command the harness is told to start as its MCP server `lead`, and the brief it reads, from what the launcher wrote for it."""
    if harness == "claude":
        found = json.loads((home / "session/mcp.json").read_text())["mcpServers"]
        brief = (home / "session/brief.md").read_text()
    elif harness == "codex":
        found = tomllib.loads((home / ".codex-home/config.toml").read_text())["mcp_servers"]
        brief = (home / ".codex-home/AGENTS.md").read_text()
    elif harness == "pi":
        found = json.loads((home / ".pi-config/mcp.json").read_text())["mcpServers"]
        assert found["lead"]["exposure"] == "direct", "Pi declares the two tools to the model only when they are direct"
        brief = ""
    else:
        found = {name: {"command": each["command"][0], "args": each["command"][1:]}
                 for name, each in json.loads((home / ".opencode-config/opencode.json").read_text())["mcp"].items()}
        brief = (home / ".opencode-config/poolhouse-brief.md").read_text()
    assert list(found) == ["lead"], f"{harness}: one server, and it is the lead's"
    return [found["lead"]["command"], *found["lead"]["args"]], brief


WAIT_S = 20.0
"""The longest the server may take over one request. Past it the test fails with what was asked, whatever holds the answer."""


class Speaker:
    """A real MCP server process (the bridge's client) spoken to line by line, as a harness speaks to it."""

    def __init__(self, argv: list[str]) -> None:
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.count = 0
        self.lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        threading.Thread(target=lambda: [self.lines.put(line) for line in self.process.stdout], daemon=True).start()

    def rpc(self, method: str, **params: object) -> dict:
        self.count += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.count, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        try:
            line = self.lines.get(timeout=WAIT_S)
        except queue.Empty:
            lead_bridge.STOPPED.set()   # a question the launcher still waits on ends, so the launch closes
            self.process.kill()
            pytest.fail(f"the lead server did not answer {method} {params.get('name', '')} within {WAIT_S:.0f}s")
        return json.loads(line)["result"]

    def call(self, name: str, **arguments: object) -> tuple[bool, str]:
        done = self.rpc("tools/call", name=name, arguments=arguments)
        return done["isError"], done["content"][0]["text"]

    def close(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=10)


RUNNER = {"claude": "run_claude", "codex": "run_codex", "pi": "run_pi", "opencode": "run_opencode"}
TASK = {"claude": ["--print", "go"], "codex": ["exec", "go"], "pi": ["go"], "opencode": ["go"]}
"""What each launcher takes after `--` for a one-shot run: Claude Code's print mode, Codex's `exec`, the message."""


def launching(harness: str, tmp_path: Path, lead_node, port: int, run, *extra: str) -> int:
    """The harness's own launcher on the canned server, with a stand-in binary that is never run and ``run`` as its runner; its exit status."""
    _node, lead, project = lead_node
    binary = tmp_path / f"{harness}-NOT-THE-HARNESS"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    module = {"claude": claude, "codex": codex, "pi": pi, "opencode": opencode}[harness]
    words = ["--on", f"http://127.0.0.1:{port}", "--as", lead.name, "--project", str(project), f"--{harness}", str(binary), *extra, "--", *TASK[harness]]
    said: list[str] = []
    code = module.launch(words, say=said.append, **{RUNNER[harness]: run})
    assert code == 0, said
    return code


@pytest.fixture
def canned(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), kit.Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for module in (claude, codex, pi, opencode):
        monkeypatch.setattr(module, "alias_of", lambda _url, _model: kit.MODEL)
    monkeypatch.setattr(harnessing, "window_of", lambda _url: 8192)
    monkeypatch.setattr(lead_bridge, "POLL_S", 0.05)
    yield server.server_port
    server.shutdown()
    server.server_close()
    lead_bridge.STOPPED.clear()


def answering(node, lead, agent: str, text: str):
    """The lead answers the question of the helper agent from another thread, while the helper's tool waits for it; the node names the helper."""
    done = threading.Event()

    def answer():
        while not done.wait(0.05):
            if any(entry.sender == agent and entry.fields["type"] == "question" for entry in node.session(lead).read(inbox=True).entries):
                node.session(lead).post(agent, "answer", text)
                return

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    return thread, done


def judged(argv: list[str], tool: str) -> str:
    """What the launch's own hook decides about a call of tool, asked as the harness asks it."""
    done = subprocess.run([*argv[:-1], "hook", "pre"], input=json.dumps({"tool_name": tool, "tool_input": {}}), capture_output=True, text=True,
                          timeout=WAIT_S, check=True)
    return json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize("harness", ["claude", "codex", "pi", "opencode"])
def test_every_harness_has_the_two_lead_tools_and_a_brief_that_names_them_and_no_command_outside_the_box(harness, lead_node, canned, tmp_path):
    node, lead, _project = lead_node
    seen: dict[str, object] = {}

    def run(confinement):
        argv, brief = servers(harness, confinement.home)
        if harness == "pi":
            brief = confinement.argv[confinement.argv.index("--append-system-prompt") + 1]
        speaker = Speaker(argv)
        try:
            speaker.rpc("initialize")
            seen["tools"] = {each["name"]: sorted(each["inputSchema"]["properties"]) for each in speaker.rpc("tools/list")["tools"]}
            seen["note"] = speaker.call("note", text="halfway: src/feature.py is written")
            seen["verb"] = speaker.call("workspace_send", to="someone-else", type="status", body="hello")
            seen["inbox"] = speaker.call("workspace_inbox")
            answers, done = answering(node, lead, confinement.plan.agent, "main.py")
            try:
                seen["ask"] = speaker.call("ask", question="which module is the main one?")
            finally:
                done.set()
                answers.join(WAIT_S)
        finally:
            speaker.close()
        seen["brief"], seen["name"] = brief, confinement.plan.agent
        return 0

    code = launching(harness, tmp_path, lead_node, canned, run)
    assert code == 0
    assert seen["tools"] == {"ask": ["question"], "note": ["text"]}, f"{harness}: these two tools and no other"
    failed, said = seen["note"]
    assert not failed and said.startswith(f"sent to {lead.name} as message ")
    assert seen["verb"][0] and seen["inbox"][0], "a workspace verb is no tool of the box"
    failed, said = seen["ask"]
    assert not failed and f"{lead.name} answered:" in said and "main.py" in said
    entries = node.session(lead).read(limit=100).entries
    mine = [entry for entry in entries if entry.sender == seen["name"] and entry.kind == "message"]
    assert {entry.fields["type"] for entry in mine} >= {"status", "question"} and all(lead_bridge.LABEL.format(name=seen["name"]) in entry.text for entry in mine)
    assert all(session_verbs.LABEL in entry.text for entry in mine), "the bridge labels every text too"
    brief = str(seen["brief"])
    names = harness_names(harness)
    assert all(name in brief for name in names), f"{harness}: the brief names {names}: {brief}"
    assert "poolhouse-workspace inbox" not in brief and "--agent" not in brief, "no command that is not in the box"


def harness_names(harness: str) -> tuple[str, str]:
    return ("lead_ask", "lead_note") if harness == "opencode" else ("mcp__lead__ask", "mcp__lead__note")


def test_a_read_only_session_reaches_its_lead_too_and_a_launch_with_no_lead_says_so(lead_node, canned, tmp_path):
    """Telling the lead is what a read-only agent is for; neither tool changes the workspace. A launch that no session started has no one to tell."""
    lead = lead_node[1]
    seen: dict[str, object] = {}

    def run(confinement):
        argv = servers("claude", confinement.home)[0]
        seen["judged"] = [judged(argv, f"mcp__lead__{name}") for name in session_verbs.LEAD]
        speaker = Speaker(argv)
        try:
            speaker.rpc("initialize")
            seen["note"] = speaker.call("note", text="read the tree: nothing to change")
        finally:
            speaker.close()
        return 0

    assert launching("claude", tmp_path, lead_node, canned, run, "--permissions", "read-only") == 0
    failed, said = seen["note"]
    assert not failed and said.startswith(f"sent to {lead.name} as message "), "a read-only session may tell its lead"
    assert seen["judged"] == ["allow", "allow"], "and its hook lets it call both tools"
    alone = lead_bridge.for_helper(type("Helper", (), {"parent": None})())
    assert alone.run("ask", {"question": "q"}) == lead_bridge.ALONE and alone.run("note", {"text": "t"}) == lead_bridge.ALONE
    assert alone.news() == "", "and nobody sends it anything"
    assert set(session_verbs.LEAD) == {"ask", "note"}


def notified(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """The launch's own post-tool hook, run as the harness runs it after a tool."""
    return subprocess.run([*argv[:-1], "hook", "post"], input=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Read"}),
                          capture_output=True, text=True, timeout=WAIT_S, check=True)


def context_of(done: subprocess.CompletedProcess[str]) -> str:
    """What the hook added to the model's context; empty when it answered nothing."""
    return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] if done.stdout.strip() else ""


@pytest.mark.parametrize("harness", ["claude", "codex", "pi", "opencode"])
def test_what_the_lead_sends_reaches_a_confined_agent_once_with_a_tool_result_and_nobody_elses_does(harness, lead_node, canned, tmp_path):
    """The live failure: the hook told the box to run `poolhouse-workspace inbox`, which it does not have."""
    node, lead, _project = lead_node
    seen: dict[str, str] = {}

    def run(confinement):
        argv, agent = servers(harness, confinement.home)[0], confinement.plan.agent
        speaker = Speaker(argv)
        try:
            speaker.rpc("initialize")
            node.session(lead).post(agent, "status", "an early word \x1b[31mfrom the lead")
            node.session(node.member("someone-else")).post(agent, "status", "a stranger's word")
            seen["first"], seen["again"] = context_of(notified(argv)), context_of(notified(argv))
            node.session(lead).post(agent, "status", "sent before the question")
            answers, done = answering(node, lead, agent, "main.py")
            try:
                failed, seen["answer"] = speaker.call("ask", question="which module is the main one?")
                assert not failed, seen["answer"]
            finally:
                done.set()
                answers.join(WAIT_S)
            seen["after"], seen["last"] = context_of(notified(argv)), context_of(notified(argv))
        finally:
            speaker.close()
        return 0

    assert launching(harness, tmp_path, lead_node, canned, run) == 0
    label = lead_bridge.FROM_LEAD.format(lead=lead.name)
    assert seen["first"].startswith(f"{label}\n") and "an early word from the lead" in seen["first"], seen
    assert "stranger" not in "".join(seen.values()) and "\x1b" not in seen["first"]
    assert "inbox" not in seen["first"] and "poolhouse-workspace" not in seen["first"], "no command that is not in the box"
    assert seen["again"] == "" and seen["last"] == "", "each message is given once, and nothing is said when there is nothing"
    assert "main.py" in seen["answer"] and "sent before the question" not in seen["answer"], "an earlier message is no answer"
    assert "sent before the question" in seen["after"] and "main.py" not in seen["after"], "it is not lost, and the answer is not repeated"


def test_a_confined_session_whose_token_is_gone_is_told_only_the_fixed_sentence_and_the_kind_of_failure(lead_node, canned, tmp_path):
    """The real handler and a really missing helper token: the reason names a file of this computer, which stays outside the box."""
    node = lead_node[0]
    seen: dict[str, object] = {}

    def run(confinement):
        argv = servers("claude", confinement.home)[0]
        tokens = list(node.client.state.glob(f"client/*/{confinement.plan.agent}.token"))
        assert tokens, "the helper has a token to lose"
        for token in tokens:
            token.unlink()
        speaker = Speaker(argv)
        try:
            speaker.rpc("initialize")
            seen["note"] = speaker.call("note", text="halfway")
            seen["ask"] = speaker.call("ask", question="which?")
        finally:
            speaker.close()
        seen["post"] = notified(argv)
        return 0

    assert launching("claude", tmp_path, lead_node, canned, run) == 0
    for verb in session_verbs.LEAD:
        assert seen[verb] == (True, session_bridge.VERB_FAILED.format(verb=verb, kind="Denied")), seen[verb]
    done = seen["post"]
    assert done.stdout == "" and done.stderr.strip() == session_bridge.NEWS_FAILED.format(kind="Denied"), (done.stdout, done.stderr)
    assert str(node.client.state) not in str(seen) and ".token" not in str(seen)


def test_a_notification_that_overlaps_another_is_answered_empty_and_never_repeats_the_one_before():
    """What a notification answers is given to the model once, so no frame is answered with what another frame was answered."""
    release, entered = threading.Event(), threading.Event()
    calls: list[int] = []

    def hook(_event: str, _payload: dict) -> dict:
        calls.append(1)
        if len(calls) == 1:
            return {"said": "given once [PLUMBING, NOT A MODEL]"}
        entered.set()
        release.wait(WAIT_S)
        return {}

    made = session_bridge.SessionBridge(session_bridge.Handlers(hook=hook, workspace=lambda _name, _arguments: None), session_bridge.Terms(4096),
                                        root="/tmp", idle=WAIT_S, unadmitted="a test of overlapping notifications")
    made.start()

    def post() -> dict:
        with client.connect(made.path, os.getpid()) as sock:
            return client.call(sock, {"verb": "hook", "event": "post", "payload": {}})

    try:
        assert post() == {"answer": {"said": "given once [PLUMBING, NOT A MODEL]"}}
        slow = threading.Thread(target=post, daemon=True)
        slow.start()
        assert entered.wait(WAIT_S)
        assert post() == {"answer": {}}
        release.set()
        slow.join(WAIT_S)
    finally:
        release.set()
        made.stop()
