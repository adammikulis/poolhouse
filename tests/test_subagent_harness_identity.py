"""A subagent's identity comes from the harness: the start hook registers every kind of subagent, even at the board's limit, and each command it runs is run as it, with no flag, no token typed and no change to the command."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from git_world import DEV, World
from node_kit import SRC

from poolhouse import authority
from poolhouse.board import session as board_session
from poolhouse.board.client import Denied
from poolhouse.caller import AgentRefused

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
LEAD = ""
"""The agent id of a call of the lead's own session: its hook events carry none."""


def wrapped(exports: str, command: str, cwd_file: Path) -> str:
    """The text Claude Code gives the shell of a command: its environment file's lines, then the command quoted after `eval`."""
    return (f"source /nonexistent/snapshot.sh 2>/dev/null || true && {exports}: && eval {shlex.quote(command)} < /dev/null "
            f"&& pwd -P >| {cwd_file}")


class Scene:
    """A project, a real node with a lead session, and the real session, start, stop and Bash hooks run as Claude Code runs them."""

    def __init__(self, tmp_path: Path, node, monkeypatch) -> None:
        self.node, self.world = node, World(tmp_path, node)
        owner = node.member("project-owner")
        node.allow(owner, "source_add")  # the hooks act only within a project the node already holds
        node.client.call("project_add", "", owner.token, id=node.board, kind="git", path=str(self.world.proj.root))
        self.lead = node.member("sess1")
        self.hooks = ROOT / "scripts/hooks"
        binary = tmp_path / "bin"
        binary.mkdir()
        wrapper = binary / "poolhouse-workspace"
        wrapper.write_text(f"#!{sys.executable}\nimport sys\nfrom poolhouse.workspace.cli import main\n"
                           "raise SystemExit(main(sys.argv[1:]))\n")
        wrapper.chmod(0o700)
        monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("PYTHONPATH", SRC)
        monkeypatch.setenv("POOLHOUSE_HOOK_STATE", str(tmp_path / "hookstate"))
        monkeypatch.setenv("POOLHOUSE_NONINTERACTIVE", "1")
        monkeypatch.setenv("POOLHOUSE_RUNTIME_ENSURE", "off")
        monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", DEV)
        monkeypatch.chdir(tmp_path)
        self.tmp = tmp_path
        harness = tmp_path / "claude-config" / "session-env" / "sess1"
        harness.mkdir(parents=True, mode=0o700)
        self.exports = harness / "sessionstart-hook-0.sh"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))

    def session_start(self, source: str = "startup", session: str = "sess1") -> Path:
        """Run the session start hook, as for ``source``, with the shell environment file the harness offers it; that file."""
        exports = self.exports.parents[1] / session / self.exports.name
        exports.parent.mkdir(mode=0o700, exist_ok=True)
        event = {"session_id": session, "source": source, "cwd": str(self.world.proj.root), "model": "claude-sonnet-5-5"}
        done = subprocess.run([sys.executable, str(self.hooks / "claude-session-start")], input=json.dumps(event), text=True,
                              capture_output=True, timeout=90, check=False,
                              env={**os.environ, **self.inherited(), "CLAUDE_ENV_FILE": str(exports)})
        assert done.returncode == 0 and exports.is_file(), done.stderr
        return exports

    def inherited(self) -> dict[str, str]:
        """The environment every command of the lead's session, and so of its subagents, starts with: the lead's name and the
        native session the harness exports for it."""
        native = {"CLAUDE_CODE_SESSION_ID": "sess1", "POOLHOUSE_SESSION_ID": "sess1", "POOLHOUSE_SESSION_HARNESS": "claude-code"}
        return {**self.node.env(self.lead, {"POOLHOUSE_DEV_BRANCH": DEV, "PATH": os.environ["PATH"], **native}), "CLAUDECODE": "1"}

    def hook(self, name: str, event: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.hooks / name)], input=json.dumps(event), text=True,
                              capture_output=True, timeout=90, check=False)

    def event(self, agent: str, kind: str = "general-purpose", **more: object) -> dict:
        return {"agent_type": kind, "agent_id": agent, "session_id": "sess1", "cwd": str(self.world.proj.root), **more}

    def start(self, agent: str, kind: str = "general-purpose") -> str:
        """Start a subagent with an empty brief; the context its hook delivers."""
        done = self.hook("claude-subagent-start", self.event(agent, kind))
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] if done.stdout else ""

    def stop(self, agent: str, kind: str = "general-purpose") -> None:
        done = self.hook("claude-subagent-stop", self.event(agent, kind))
        assert done.returncode == 0 and not done.stdout.strip(), (done.stdout, done.stderr)

    def hand_back(self, agent: str, kind: str = "general-purpose") -> None:
        """End the turns of a subagent whose tree holds unlanded work: a turn end only notifies, never blocks, and its identity is kept."""
        for _ in range(3):
            self.stop(agent, kind)

    def finish(self, agent: str) -> None:
        """The subagent says it is done: it retires its own session, which is the only thing that ends an identity."""
        done = self.node.cli("retire", who=self.node.adopt(self.name(agent)))
        assert done.returncode == 0, done.stderr

    def name(self, agent: str) -> str:
        return board_session.find("claude-code", agent, client=self.node.client)

    def asked(self, agent: str, command: str, cwd: Path | None = None, **event: str) -> subprocess.CompletedProcess:
        """The Bash hook's answer to a call, of the subagent ``agent`` or of the lead's own session when it is `LEAD`; ``event``
        replaces fields of the hook's event."""
        asked = {"tool_name": "Bash", "session_id": "sess1", "cwd": str(cwd or self.world.proj.root), "permission_mode": "auto",
                 "tool_input": {"command": command, "description": "a step"}, **({"agent_id": agent} if agent else {}), **event}
        return self.hook("claude-bash-guard", asked)

    def allow(self, agent: str, command: str, cwd: Path | None = None, tool: str = "Bash") -> None:
        guard = self.asked(agent, command, cwd, tool_name=tool)
        assert guard.returncode == 0 and not guard.stdout.strip(), (guard.stdout, guard.stderr)

    def shell(self, command: str, cwd: Path | None = None, program: str = "/bin/bash",
              session: str = "sess1") -> subprocess.CompletedProcess:
        """Run ``command`` unchanged in a shell the lead's session ``session`` starts, wrapped as the harness wraps it."""
        text = wrapped((self.exports.parents[1] / session / self.exports.name).read_text(), command, self.tmp / "claude-0a1b-cwd")
        return subprocess.run([program, "-c", text], cwd=cwd or self.world.proj.root,
                              env={**self.inherited(), "CLAUDE_CODE_SESSION_ID": session},
                              text=True, capture_output=True, timeout=120, check=False)

    def run(self, agent: str, command: str, cwd: Path | None = None, tool: str = "Bash") -> subprocess.CompletedProcess:
        """The call as it happens: the Bash hook allows it as written, then its shell runs it."""
        self.allow(agent, command, cwd, tool)
        return self.shell(command, cwd)

    def lead_runs(self, command: str) -> subprocess.CompletedProcess:
        """A call of the lead's own session, as it happens."""
        self.allow(LEAD, command)
        return self.shell(command)


@pytest.fixture
def scene(tmp_path, workspace_node, monkeypatch):
    made = Scene(tmp_path, workspace_node, monkeypatch)
    made.session_start()
    return made


def test_a_general_purpose_subagent_with_an_empty_brief_is_registered_and_records_a_review_with_no_flag(scene):
    _, sha = scene.world.start("fix/x", {"docs/a.md": "changed\n"})
    context = scene.start("rev1")
    child = scene.name("rev1")
    assert child and child != scene.lead.name and f"You are the subagent {child}, spawned by {scene.lead.name}" in context
    done = scene.run("rev1", f"poolhouse-workspace land-review fix/x {sha} --verdict accept")
    assert done.returncode == 0, done.stderr
    accepts = scene.node.client.call("land_accepts", scene.node.board, scene.lead.token, sha=sha)
    assert child in json.dumps(accepts) and scene.lead.name not in json.dumps(accepts)


def test_a_subagent_command_acts_as_the_subagent_not_the_session_that_started_it(scene):
    scene.start("rev1")
    child = scene.name("rev1")
    done = scene.run("rev1", "poolhouse-workspace whoami --json")
    assert done.returncode == 0 and json.loads(done.stdout)["id"] == child, done.stderr
    sent = scene.node.cli("send", scene.world.other.name, "note", "hello", "--agent", scene.lead.name, who=scene.lead,
                          env={"POOLHOUSE_SUBAGENT": "rev1"})
    assert sent.returncode == 0 and f"--agent {scene.lead.name} is not you: running as {child}" in sent.stderr, sent.stderr
    inbox = json.loads(scene.node.cli("inbox", "--json", who=scene.world.other).stdout)
    assert [m["from"] for m in inbox] == [child]


def test_a_stale_agent_name_falls_back_to_the_callers_own_identity(scene):
    done = scene.node.cli("whoami", "--json", "--agent", "claude-000000", who=scene.lead)
    assert done.returncode == 0 and json.loads(done.stdout)["id"] == scene.lead.name
    assert "claude-000000 is not a live session here: running as " + scene.lead.name in done.stderr
    assert scene.node.cli("whoami", "--agent", "claude-000000").returncode == 3


def test_a_resumed_worker_keeps_its_identity_and_claims(scene, tmp_path, monkeypatch):
    tree = scene.world.proj.branch("agent-w1", {"docs/a.md": "changed\n"})
    private = tmp_path / "private-terms"
    private.write_text("finished\n")
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_DENYLIST", str(private))
    scene.start("w1", "branch-worker")
    child = scene.name("w1")
    scene.hand_back("w1", "branch-worker")
    assert scene.name("w1") == child and scene.node.cli("whoami", who=scene.node.adopt(child)).returncode == 0
    context = scene.start("w1", "branch-worker")
    assert scene.name("w1") == child and child in context
    for kind, key in (("branch", "agent-w1"), ("worktree", str(tree))):
        claimed = scene.run("w1", f"poolhouse-workspace claim {kind} {key}", cwd=tree)
        assert claimed.returncode == 0 and child in claimed.stdout, claimed.stderr


def test_a_finished_reviewer_is_retired_and_its_name_stops_working(scene):
    _, sha = scene.world.start("fix/y", {"docs/a.md": "changed\n"})
    scene.start("rev2")
    child = scene.name("rev2")
    assert scene.run("rev2", f"poolhouse-workspace land-review fix/y {sha} --verdict accept").returncode == 0
    token = scene.node.adopt(child).token
    scene.stop("rev2")
    scene.node.client.call("whoami", scene.node.board, token)
    scene.finish("rev2")
    with pytest.raises(Denied):
        scene.node.client.call("whoami", scene.node.board, token)
    agents = {a.name: a for a in scene.node.session(scene.lead).agents(retired=True)}
    assert agents[child].retired


def test_a_lead_registers_any_number_of_subagents_and_a_finished_one_is_retired(scene):
    agents = [f"a{i}" for i in range(20)]
    for agent in agents:
        context = scene.start(agent, "branch-worker")
        assert scene.name(agent) and scene.name(agent) in context, context
    old = scene.name("a0")
    scene.finish("a0")
    live = {a.name for a in scene.node.session(scene.lead).agents()}
    assert len(live) >= 19 and old not in live


def test_a_subagent_the_board_cannot_register_is_told_and_so_is_the_board(scene):
    event = scene.event("a4")
    del event["agent_id"]
    done = scene.hook("claude-subagent-start", event)
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "could not register you" in context and "no agent_id" in context
    heard = [e.text for e in scene.node.session(scene.lead).read(channel="#announcements").entries]
    assert any("has no identity" in line for line in heard)


def test_the_stamp_removes_the_leads_name_for_a_subagent_the_board_never_registered(scene):
    done = scene.run("ghost", "poolhouse-workspace whoami")
    assert done.returncode == 3 and "no name on the board" in done.stderr
    env = scene.run("ghost", "env")
    assert scene.lead.name not in env.stdout and "POOLHOUSE_SUBAGENT=ghost" in env.stdout


@pytest.mark.redteam
def test_a_subagent_cannot_name_another_identity_or_the_subagent_variable(scene):
    scene.start("rev1")
    for command in (f"poolhouse-workspace inbox --agent {scene.lead.name}", f"POOLHOUSE_WORKSPACE_AGENT={scene.lead.name} true",
                    "POOLHOUSE_SUBAGENT=other true", "poolhouse-workspace inbox --token-file /x"):
        asked = {"tool_name": "Bash", "agent_id": "rev1", "session_id": "sess1", "cwd": "/", "tool_input": {"command": command}}
        done = scene.hook("claude-bash-guard", asked)
        assert done.returncode == 2 and "you are" in done.stderr, (command, done.stderr)
    benign = {"tool_name": "Bash", "agent_id": "rev1", "session_id": "sess1", "cwd": "/",
              "tool_input": {"command": 'grep -rn "POOLHOUSE_WORKSPACE_AGENT" src scripts'}}
    assert scene.hook("claude-bash-guard", benign).returncode == 0


@pytest.mark.redteam
def test_a_subagent_is_never_audited_as_the_lead_even_with_the_leads_name_in_its_environment(scene, monkeypatch):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    monkeypatch.setenv("POOLHOUSE_BOARD", scene.node.board)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", scene.lead.name)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", scene.lead.session)
    monkeypatch.setenv("CLAUDECODE", "1")

    def audited() -> str:
        return [row for row in authority.audit_rows() if row["event"] == "authority.use"][-1]["agent"]

    assert authority.require("sentinel.policy", "mode") == authority.DELEGATED and audited() == scene.lead.name
    monkeypatch.setenv("POOLHOUSE_SUBAGENT", "rev1")
    assert authority.require("sentinel.policy", "mode") == authority.DELEGATED and audited() == ""
    authority.set_state(["sentinel.policy"], authority.PERSON, by=scene.lead.name)
    for subagent in ("", "rev1"):
        monkeypatch.setenv("POOLHOUSE_SUBAGENT", subagent)
        with pytest.raises(AgentRefused, match="held back from agents"):
            authority.require("sentinel.policy", "mode")


def without_marker(*argv: str) -> str:
    """A command that takes the subagent's own marker out of the environment from inside a program, where no rule of the Bash
    guard reads it, and runs ``argv`` in what is left."""
    code = ("import os, subprocess, sys\n"
            "for name in list(os.environ):\n"
            "    if name.startswith(('POOLHOUSE_SUB', 'POOLHOUSE_WORKSPACE')):\n"
            "        del os.environ[name]\n"
            "raise SystemExit(subprocess.run(sys.argv[1:]).returncode)\n")
    return " ".join(shlex.quote(word) for word in (sys.executable, "-c", code, *argv))


def test_a_subagent_that_drops_its_own_marker_has_no_identity_instead_of_the_leads(scene):
    scene.start("rev1")
    whoami = scene.run("rev1", without_marker("poolhouse-workspace", "whoami", "--json"))
    assert whoami.returncode != 0 and scene.lead.name not in whoami.stdout, whoami.stdout
    gate = "from poolhouse import authority; print(authority.require('sentinel.policy', 'mode', (False, False)))"
    passed = scene.run("rev1", without_marker(sys.executable, "-c", gate))
    assert passed.returncode != 0 and "delegated" not in passed.stdout, (passed.stdout, passed.stderr)


def test_a_subagent_that_runs_a_command_through_the_monitor_tool_acts_as_the_subagent(scene):
    scene.start("rev1")
    child = scene.name("rev1")
    done = scene.run("rev1", "poolhouse-workspace whoami --json", tool="Monitor")
    assert done.returncode == 0 and json.loads(done.stdout)["id"] == child, done.stderr


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "auto", "bypassPermissions"])
def test_the_bash_hook_never_changes_the_input_of_a_call(scene, mode):
    """A harness reviews the input the agent wrote: a hook that changes it gets the call refused, and the agent told to send other text."""
    scene.start("rev1")
    for command in ("true", "poolhouse-workspace who worktree", "poolhouse-workspace inbox", "git -C /x commit -m y",
                    "scripts/test all tests/test_x.py"):
        for agent, tool in (("rev1", "Bash"), ("rev1", "Monitor"), (LEAD, "Bash")):
            done = scene.asked(agent, command, tool_name=tool, permission_mode=mode)
            assert done.returncode == 0 and not done.stdout.strip() and not done.stderr.strip(), (command, done.stdout, done.stderr)


ASK = "poolhouse-workspace whoami --json"


def who(done: subprocess.CompletedProcess) -> str:
    """The name the command in a finished shell acted under."""
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["id"]


def nobody(done: subprocess.CompletedProcess, lead: str, why: str) -> None:
    assert done.returncode != 0 and lead not in done.stdout, done.stdout
    assert "no workspace identity" in done.stderr and why in done.stderr, done.stderr


def test_calls_waiting_together_are_each_run_as_the_caller_that_issued_them(scene):
    scene.start("rev1")
    scene.start("rev2")
    commands = {"rev1": ASK, "rev2": ASK + " 2>/dev/null", LEAD: ASK + " | cat"}
    for agent, command in commands.items():
        scene.allow(agent, command)
    names = {"rev1": scene.name("rev1"), "rev2": scene.name("rev2"), LEAD: scene.lead.name}
    for agent in ("rev2", LEAD, "rev1"):
        assert who(scene.shell(commands[agent])) == names[agent], agent


def test_the_same_command_from_two_callers_in_two_folders_runs_each_as_itself(scene):
    scene.start("rev1")
    other = scene.world.proj.branch("agent-rev1", {"docs/a.md": "changed\n"})
    scene.allow(LEAD, ASK)
    scene.allow("rev1", ASK, cwd=other)
    assert who(scene.shell(ASK, cwd=other)) == scene.name("rev1")
    assert who(scene.shell(ASK)) == scene.lead.name


@pytest.mark.redteam
def test_the_same_command_from_two_callers_in_one_folder_names_nobody_and_then_clears(scene):
    scene.start("rev1")
    scene.allow(LEAD, ASK)
    scene.allow("rev1", ASK)
    for _ in range(2):
        nobody(scene.shell(ASK), scene.lead.name, "two callers issued this same command")
    assert who(scene.run("rev1", ASK)) == scene.name("rev1")
    assert who(scene.lead_runs(ASK)) == scene.lead.name


@pytest.mark.redteam
def test_a_shell_no_record_matches_while_a_subagents_call_waits_names_nobody(scene):
    """If the harness ever ran other text than it asked about, a subagent's shell must not fall back on the lead."""
    scene.start("rev1")
    scene.allow("rev1", ASK)
    nobody(scene.shell(f"true; {ASK}"), scene.lead.name, "no record of its caller")
    assert who(scene.shell(ASK)) == scene.name("rev1")


@pytest.mark.redteam
def test_a_record_holds_no_command_text_and_cannot_put_other_code_in_a_shell(scene, tmp_path):
    scene.start("rev1")
    scene.allow("rev1", "true synthetic-private-argument")
    [held] = (tmp_path / "hookstate" / "shell").glob("*/call-*.json")
    assert "synthetic-private-argument" not in held.read_text()
    planted = tmp_path / "planted"
    changed = {**json.loads(held.read_text()), "name": f"$(touch {planted})", "board": f"; touch {planted}",
               "lines": f"touch {planted}"}
    held.write_text(json.dumps(changed))
    done = scene.shell("true synthetic-private-argument")
    assert done.returncode == 0 and not planted.exists(), done.stderr


@pytest.mark.parametrize("program", ["/bin/bash", "zsh"])
def test_a_command_with_quotes_pipes_lines_and_a_heredoc_finds_its_caller(scene, program):
    found = shutil.which(program)
    if not found:
        pytest.skip(f"{program} is not installed")
    scene.start("rev1")
    command = ("printf '%s' \"it's here\" >/dev/null\ncat <<'EOF' >/dev/null\nbody && eval 'nothing'\nEOF\n"
               f"true && eval 'true' && {ASK} | cat")
    scene.allow("rev1", command)
    assert who(scene.shell(command, program=found)) == scene.name("rev1")


def test_a_resumed_session_that_holds_the_loader_twice_still_runs_a_subagent_as_itself(scene):
    scene.session_start()
    assert scene.exports.read_text().count("_poolhouse_caller=") == 2
    scene.start("rev1")
    assert who(scene.run("rev1", ASK)) == scene.name("rev1")
    assert who(scene.lead_runs(ASK)) == scene.lead.name


@pytest.mark.redteam
def test_before_a_sessions_shells_name_their_caller_a_subagents_command_runs_as_the_lead_and_is_told_so(scene):
    """The protection a session begun before the loader does not have: a command that records no review verdict inherits the lead's
    identity, and is only told so."""
    scene.start("rev1")
    for agent, command, told in (("rev1", ASK, True), ("rev1", "git commit -m x", True), ("rev1", "ls", False), (LEAD, ASK, False)):
        done = scene.asked(agent, command, session_id="an-earlier-session")
        assert done.returncode == 0, (command, done.stderr)
        said = json.loads(done.stdout)["hookSpecificOutput"] if done.stdout else {}
        assert set(said) == ({"hookEventName", "additionalContext"} if told else set()), (command, said)
        assert not told or "runs as the session that started you" in said["additionalContext"]
    unnamed = subprocess.run(["/bin/sh", "-c", ASK], cwd=scene.world.proj.root, env=scene.inherited(), text=True,
                             capture_output=True, timeout=120, check=False)
    assert who(unnamed) == scene.lead.name
    refused = scene.asked("rev1", f"poolhouse-workspace inbox --agent {scene.lead.name}", session_id="an-earlier-session")
    assert refused.returncode == 2 and "you are" in refused.stderr


SHA = "a" * 40
RECORDS_A_VERDICT = [
    f"scripts/lead-review 12 {SHA} --verdict accept --note ok",
    f"cd scripts && ./lead-review 12 {SHA} --verdict reject --note no",
    f"python3 scripts/lead-review 12 {SHA} --verdict accept --note ok",
    f"GH_PAGER=cat timeout 60 scripts/lead-review 12 {SHA} --verdict accept --note ok",
    f"bash -c 'scripts/lead-review 12 {SHA} --verdict accept --note ok'",
    f"bash <<'EOF'\nscripts/lead-review 12 {SHA} --verdict accept --note ok\nEOF",
    f"poolhouse-workspace land-review fix/x {SHA} --verdict accept",
    f"true; /x/bin/poolhouse-workspace land-review fix/x {SHA}",
    f"poolhouse workspace land-review fix/x {SHA}",
    f"python3 -m poolhouse.workspace land-review fix/x {SHA}",
    "poolhouse-delegate grade quick-fix accept --critique right",
    "poolhouse delegate grade quick-fix accept --critique right",
    "gh pr review 12 --approve",
    "gh pr review 12 --request-changes -b no",
    f"gh api -X POST repos/o/r/statuses/{SHA} -f state=success",
    "gh api repos/o/r/pulls/12/reviews -f event=APPROVE",
]
RECORDS_NO_VERDICT = [
    "cat scripts/lead-review",
    "grep -n verdict scripts/lead-review",
    "rg 'land-review' docs",
    "git commit -m 'lead-review and land-review are named here'",
    "echo \"run scripts/lead-review when ready\"",
    "poolhouse-workspace inbox",
    "poolhouse-delegate record quick-fix",
    "gh pr view 12",
    "gh pr review 12 --comment -b seen",
    "gh api repos/o/r/pulls/12/reviews",
    "scripts/test all tests/test_lead_review.py",
]


@pytest.mark.redteam
@pytest.mark.parametrize("command", RECORDS_A_VERDICT)
def test_before_a_sessions_shells_name_their_caller_a_subagent_records_no_review_verdict(scene, command):
    scene.start("rev1")
    done = scene.asked("rev1", command, session_id="an-earlier-session")
    assert done.returncode == 2 and not done.stdout.strip(), (done.returncode, done.stdout)
    assert "records a review verdict" in done.stderr and "Restarting, resuming or clearing the session" in done.stderr, done.stderr
    for agent, session in ((LEAD, "an-earlier-session"), ("rev1", "sess1")):
        allowed = scene.asked(agent, command, session_id=session)
        assert "records a review verdict" not in allowed.stderr, (agent, session, allowed.stderr)


@pytest.mark.parametrize("command", RECORDS_NO_VERDICT)
def test_before_a_sessions_shells_name_their_caller_every_other_command_of_a_subagent_runs(scene, command):
    scene.start("rev1")
    done = scene.asked("rev1", command, session_id="an-earlier-session")
    assert done.returncode == 0, (command, done.stderr)


@pytest.mark.redteam
def test_a_compacted_session_counts_the_shell_line_only_once_a_shell_has_run_it(scene):
    """Claude Code keeps the shell environment it read before it compacted, so the line the start hook adds then may reach no shell:
    until one has run it, a subagent records no review verdict."""
    scene.start("rev1")
    scene.session_start("compact", "sess2")
    assert scene.asked("rev1", RECORDS_A_VERDICT[0], session_id="sess2").returncode == 2
    ran = scene.shell("true", session="sess2")
    assert ran.returncode == 0, ran.stderr
    allowed = scene.asked("rev1", RECORDS_A_VERDICT[0], session_id="sess2")
    assert allowed.returncode == 0, allowed.stderr


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "fork"])
def test_a_session_whose_shells_read_the_line_anew_counts_it_at_once(scene, source):
    scene.start("rev1")
    scene.session_start(source, "sess2")
    allowed = scene.asked("rev1", RECORDS_A_VERDICT[0], session_id="sess2")
    assert allowed.returncode == 0, allowed.stderr


@pytest.mark.redteam
@pytest.mark.parametrize("record", ['{"generation": "x"}', '{"generation": Infinity}', '{"name": 1}', "[" * 100000],
                         ids=["generation", "infinity", "name", "nesting"])
def test_a_subagent_whose_identity_record_cannot_be_read_runs_nothing_rather_than_running_as_the_lead(scene, tmp_path, record):
    scene.start("rev1")
    [held] = (tmp_path / "hookstate" / "subagents").glob(hashlib.sha256(b"rev1").hexdigest()[:24] + ".json")
    held.write_text(record)
    for command in ("true", "git push --force origin x"):
        asked = {"tool_name": "Bash", "agent_id": "rev1", "session_id": "sess1", "cwd": str(scene.world.proj.root), "tool_input": {"command": command}}
        done = scene.hook("claude-bash-guard", asked)
        assert done.returncode == 2 and not done.stdout.strip(), (command, done.returncode, done.stderr)
