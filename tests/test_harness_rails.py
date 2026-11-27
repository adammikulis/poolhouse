"""The rails around Claude Code and Codex on a local model: the role policy, the hook, the session files."""

import base64
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from confined_kit import repository
from harness_kit import held
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse import (
    claude,
    codex,
    coding,
    coding_policy,
    harnesshook,
    harnessid,
    harnessing,
    isolation,
    lead_bridge,
    private_path,
    runtime,
)
from poolhouse.board import credentials, person, session as board_session
from poolhouse.board.client import Denied as NodeDenied
from poolhouse.harnesspolicy import decide
from poolhouse.interventions import Call

SRC = str(Path(__file__).resolve().parent.parent / "src")
pytest_plugins = ["tests.confined_kit"]
CLIENT = str(Path(__file__).resolve().parent.parent / "src" / "poolhouse" / "isolation" / "client.py")


def context(tmp_path, socket="/tmp/ph-x/s", client=CLIENT, pid=4242):
    """What a harness's files are made from, for a launch that has not started: the places and the one client a hook runs."""
    return isolation.Context(tmp_path, tmp_path / ".claude-config", str(tmp_path), os.path.realpath(sys.executable), client, socket, pid,
                             "http://127.0.0.1:1", "qwen", 4096, "bridge-token-never-in-a-command")


def _hook(event, payload, *args, env=None):
    """Run the hook as a harness does: a child process, the event on stdin."""
    base = {**os.environ, "PYTHONPATH": SRC, **(env or {})}
    return subprocess.run([sys.executable, "-m", "poolhouse.harnesshook", event, *args],
                          input=payload if isinstance(payload, str) else json.dumps(payload),
                          capture_output=True, text=True, env=base, timeout=60, check=False)


def _verdict(done):
    return json.loads(done.stdout)["hookSpecificOutput"]


def _command_text(command):
    return base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le") if os.name == "nt" else command


class TestPolicy:
    def test_a_read_runs_in_every_role(self):
        for role in ("read-only", "guarded", "guarded"):
            assert decide(coding_policy.Policy(role), Call("Read", {"file_path": "/w/a.py"}), roots=("/w",)).action == "allow"
            assert decide(coding_policy.Policy(role), Call("Bash", {"command": "git status"}), roots=("/w",)).action == "allow"

    def test_a_destructive_call_is_denied_at_once_where_the_role_acts_and_where_it_does_not_and_yolo_runs_it(self):
        call = ("Bash", {"command": "rm -rf /tmp/somewhere"})
        guarded = decide(coding_policy.Policy("guarded"), Call(*call), roots=("/w",))
        assert (guarded.action, guarded.label) == ("deny", "destructive")
        assert "the guarded profile runs no destructive call" in guarded.reason
        readonly = decide(coding_policy.Policy("read-only"), Call(*call), roots=("/w",))
        assert readonly.action == "deny" and "runs no acting call" in readonly.reason
        assert decide(coding_policy.Policy("yolo"), Call(*call), roots=("/w",)).action == "allow"

    def test_a_valid_edit_obeys_each_permission_profile(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text("a")
        edit = ("Edit", {"file_path": str(target), "old_string": "a", "new_string": "b"})
        assert decide(coding_policy.Policy("guarded"), Call(*edit), roots=(str(tmp_path),)).action == "allow"
        assert decide(coding_policy.Policy("yolo"), Call(*edit), roots=(str(tmp_path),)).action == "allow"
        assert decide(coding_policy.Policy("read-only"), Call(*edit), roots=(str(tmp_path),)).action == "deny"

    def test_a_tool_nobody_classified_is_denied_at_once_as_unsure(self):
        got = decide(coding_policy.Policy("guarded"), Call("SomeNewTool", {"x": "y"}), roots=("/w",))
        assert (got.action, got.label) == ("deny", "unsure") and "runs no unsure call" in got.reason
        assert decide(coding_policy.Policy("guarded"), Call("Bash", None), roots=("/w",)).action == "deny"

    def test_text_naming_the_launchers_files_is_denied_in_every_role(self):
        for role in ("guarded", "yolo"):
            hit = decide(coding_policy.Policy(role), Call("Edit", {"file_path": "/state/harness/ab/settings.json", "old_string": "a",
                                        "new_string": "b"}), roots=("/state",), protected=("/state/harness/ab",))
            assert hit.action == "deny"

    @pytest.mark.parametrize("command", [
        'git commit -m "fix role change"', 'git commit -m "turn off the sentinel mode flag in tests"',
        'git commit -m "allow the new host in the proxy config"', 'git log --grep "release the quarantine"',
        'grep -rn "disable the guard rails" docs'])
    def test_a_coding_command_whose_words_name_a_chat_protected_action_is_not_refused_for_them(self, tmp_path, command):
        got = decide(coding_policy.Policy("guarded"), Call("Bash", {"command": command}), roots=(str(tmp_path),))
        assert got.action == "allow", got.reason

    def test_nothing_in_the_call_or_the_environment_changes_the_role(self, monkeypatch):
        for name in ("POOLHOUSE_ROLE", "POOLHOUSE_GUARD", "CLAUDE_ROLE"):
            monkeypatch.setenv(name, "off")
        call = {"command": "rm -rf /tmp/x", "permissions": "guarded", "executionMode": "execute", "poolhouse_role": "guarded",
                "permission_mode": "bypassPermissions", "dangerouslyDisableSandbox": True}
        assert decide(coding_policy.Policy("read-only"), Call("Bash", call), roots=("/w",)).action == "deny"
        assert decide(coding_policy.Policy("guarded"), Call("Bash", call), roots=("/w",)).action == "deny"


class TestHook:
    def _rail(self, **over):
        return harnesshook.Rail(**{"policy": coding_policy.Policy("guarded"), "agent": "local-test", "roots": ("/w",), **over})

    def test_a_safe_call_is_allowed(self):
        out = harnesshook.pre({"tool_name": "Read", "tool_input": {"file_path": "/w/a"}}, self._rail())
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_a_destructive_call_is_denied_at_once_with_the_reason(self, tmp_path):
        payload = {"tool_name": "Bash", "tool_input": {"command": f"rm -rf {tmp_path}/x"}, "cwd": str(tmp_path),
                   "session_id": "s1"}
        began = time.time()
        out = harnesshook.pre(payload, harnesshook.Rail(coding_policy.Policy("guarded"), "local-test", (str(tmp_path),), ()))
        answer = out["hookSpecificOutput"]
        assert answer["permissionDecision"] == "deny" and time.time() - began < 30
        assert "the guarded profile runs no destructive call" in answer["permissionDecisionReason"]

    def test_as_a_child_process_a_denied_call_is_denied_and_a_safe_one_allowed(self):
        deny = _verdict(_hook("pre", {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}},
                              "--permissions", "read-only", "--root", "/w"))
        assert deny["permissionDecision"] == "deny" and deny["hookEventName"] == "PreToolUse"
        allow = _verdict(_hook("pre", {"tool_name": "Read", "tool_input": {"file_path": "/w/a"}},
                               "--permissions", "guarded", "--root", "/w"))
        assert allow["permissionDecision"] == "allow"

    def test_a_destructive_call_is_denied_at_once_by_the_hook_with_no_keystore_and_no_terminal(self):
        began = time.time()
        done = _hook("pre", {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}},
                     "--permissions", "guarded", "--root", "/w",
                     env={"POOLHOUSE_HOME": str(Path(os.environ["POOLHOUSE_HOME"]) / "hook")})
        answer = _verdict(done)
        assert answer["permissionDecision"] == "deny"
        assert "the guarded profile runs no destructive call" in answer["permissionDecisionReason"]
        assert time.time() - began < 60, "the hook waits for nothing"

    def test_no_environment_variable_or_argument_lifts_the_policy(self):
        done = _hook("pre", {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x",
                                                                 "permissions": "guarded", "executionMode": "execute"}},
                     "--permissions", "read-only", "--root", "/w",
                     env={"POOLHOUSE_ROLE": "guarded", "POOLHOUSE_GUARD": "off"})
        assert _verdict(done)["permissionDecision"] == "deny"

    def test_a_hook_that_fails_internally_does_not_block_the_call_and_logs_it(self):
        done = _hook("pre", "not json", "--permissions", "guarded")
        assert (done.returncode, done.stdout) == (0, "")
        assert "did not block the call" in done.stderr and "diagnostic=" in done.stderr

    def test_the_post_hook_warns_without_blocking_when_nudge_is_missing(self, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        assert "notification unavailable" in harnesshook.post("local-test")["hookSpecificOutput"]["additionalContext"]


class TestSessionFiles:
    def test_the_settings_are_outside_the_tree_read_only_and_hold_no_secret(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-synthetic-private-key")
        tree = tmp_path / "tree"
        tree.mkdir()
        files = harnessing.session_files(tree)
        try:
            here = context(tmp_path)
            path = files.write("settings.json", claude.settings(here.hook("pre"), here.hook("post"), harnessing.WAIT_S, here.hook("stop")))
            files.lock()
            assert tree not in path.parents and files.path not in (tree, *tree.parents)
            assert not os.access(path, os.W_OK)
            if os.name == "nt":
                assert private_path.problem(files.path) == ""
                with pytest.raises(PermissionError):
                    path.write_text("replace protected settings")
            else:
                assert not os.access(files.path, os.W_OK)
            text = path.read_text()
            for secret in ("ANTHROPIC", "sk-synthetic-private-key", "bridge-token-never-in-a-command", "password"):
                assert secret not in text
            hooks = json.loads(text)["hooks"]
            command = hooks["PreToolUse"][0]["hooks"][0]["command"]
            assert shlex.split(command)[-2:] == ["hook", "pre"] and "--socket" in command and "poolhouse.harnesshook" not in command
            assert {"PreToolUse", "PostToolUse", "Stop", "SubagentStop"} <= set(hooks)
            assert "disableAllHooks" not in text
        finally:
            files.release()
        assert not files.path.exists()

    def test_a_working_directory_that_holds_the_session_is_refused(self):
        from poolhouse import home

        with pytest.raises(ValueError, match="overlap"):
            harnessing.session_files(home.state("harness"))

    def test_the_hook_command_names_this_interpreter_the_client_and_quotes_its_paths(self, tmp_path):
        odd = tmp_path / "a dir"
        cmd = context(tmp_path, socket=str(odd / "s p"), client=str(odd / "client.py")).hook("pre")
        assert shlex.split(cmd) == [os.path.realpath(sys.executable), "-I", str(odd / "client.py"), "--socket", str(odd / "s p"), "--pid", "4242",
                                    "hook", "pre"]

    def test_generated_hook_executes_with_quoted_project_paths_against_a_real_bridge(self, tmp_path):
        """The hook a harness runs is the client: it asks a real session bridge, whose rail the launcher fixed, and prints the answer."""
        odd = tmp_path / "a dir & user's project"
        odd.mkdir()
        rail = harnesshook.Rail(coding_policy.Policy("read-only"), "x y", (str(odd),), ())
        bridge = isolation.SessionBridge(isolation.Handlers(lambda event, payload: harnesshook.pre(payload, rail), lambda *_: {}),
                                         isolation.Terms(4096), root=str(Path("/tmp")), unadmitted="a test rig: the only client is the hook command the test runs")
        socket_path = bridge.start()
        try:
            cmd = context(tmp_path, socket=socket_path, pid=os.getpid()).hook("pre")
            payload = {"tool_name": "Read", "tool_input": {"file_path": str(odd / "source.py")}, "cwd": str(odd)}
            done = subprocess.run(shlex.split(cmd), input=json.dumps(payload), capture_output=True, text=True, timeout=60, check=False)
        finally:
            bridge.stop()
        assert done.returncode == 0, done.stderr
        assert _verdict(done)["permissionDecision"] == "allow"


class TestAdmission:
    def test_a_model_the_wiring_limit_cannot_hold_is_refused_with_what_is_needed_and_the_limit_is_left(self):
        said = []

        class Plan:
            enough_now, needed_mb, current_mb, default_mb = False, 120000, 98000, 98000

        assert harnessing.admitted("/m/big.gguf", 262144, said.append, plan=lambda *a: Plan()) is False
        assert "120,000 MB" in said[0] and "98,000 MB" in said[0] and "does not change it" in said[0] and "smaller --ctx" in said[0]
        assert "--apply" not in said[0] and "poolhouse-serve" not in said[0], "no command of Poolhouse raises it"
        assert harnessing.admitted("/m/big.gguf", 262144, said.append,
                                   plan=lambda *a: type("P", (), {"enough_now": True})()) is True


class TestCodex:
    def test_the_config_names_the_provider_the_window_and_the_hook(self):
        mcp = ("/py", "-I", "/home/client.py", "--socket", "/tmp/ph-x/s", "--pid", "7", "mcp")
        text = codex.config_toml("http://127.0.0.1:8080/", "qwen", 262144, ("PRE", "POST", 300.0), mcp)
        got = tomllib.loads(text)
        assert got["model_provider"] == "poolhouse" and got["model"] == "qwen"
        provider = got["model_providers"]["poolhouse"]
        assert provider["base_url"] == "http://127.0.0.1:8080/v1" and provider["wire_api"] == "responses"
        assert got["model_context_window"] == 262144
        assert got["model_auto_compact_token_limit"] == int(262144 * 0.9)
        assert got["features"]["hooks"] is True
        assert got["features"]["code_mode"]["direct_only_tool_namespaces"] == ["mcp__lead"]
        assert got["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "PRE"
        assert got["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "POST"
        assert "SessionStart" not in got["hooks"], "no hook of the harness's own reaches a poolhouse command the box cannot run"
        assert got["mcp_servers"] == {"lead": {"command": "/py", "args": list(mcp[1:]), "startup_timeout_sec": 30,
                                               "tool_timeout_sec": int(lead_bridge.tool_timeout()), "required": True}}
        assert text == codex.config_toml("http://127.0.0.1:8080/", "qwen", 262144, ("PRE", "POST", 300.0), mcp)

    def test_codexs_own_sandbox_is_off_inside_the_confinement_and_it_never_asks(self):
        assert codex.FLAGS[:4] == ("--sandbox", "danger-full-access", "--ask-for-approval", "never"), "the mount enforces the role"
        assert "--dangerously-bypass-hook-trust" in codex.FLAGS and not hasattr(codex, "policy_flags")

    def test_the_environment_has_its_own_home_and_the_bridge_token_and_no_api_key(self, tmp_path):
        env = isolation.environment(isolation.Inputs("codex", str(tmp_path), ("/usr/bin",), "http://127.0.0.1:8080", "bridge-token"))
        assert env["CODEX_HOME"] == str(tmp_path / ".codex-home") and env["POOLHOUSE_API_KEY"] == "bridge-token"
        assert not hasattr(codex, "environment") and {"OPENAI_API_KEY", "CODEX_API_KEY"}.isdisjoint(env) and set(env) <= isolation.allowed("codex")


class _Config:
    class serving:
        slot_context = 262144


def _fake_serving(seen):
    import contextlib

    @contextlib.contextmanager
    def serving(model, want, say, by):
        seen["model"], seen["want"] = model, want
        yield "http://127.0.0.1:8899", _Config(), "/models/Qwen3.8-27B-Q4_K_M.gguf"
        seen["released"] = True
    return serving


@pytest.mark.usefixtures("confinable", "from_a_terminal")
class TestLaunch:
    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch, tmp_path):
        monkeypatch.setattr(harnessid, "invite", lambda name, project, parent, say, **kwargs: harnessid.HarnessSession(name, board="demo", base=tmp_path / "workspace"))
        monkeypatch.setattr(claude, "alias_of", lambda url, model: "qwen-27b")
        monkeypatch.setattr(codex, "alias_of", lambda url, model: "qwen-27b")
        repository(tmp_path / "tree")
        monkeypatch.chdir(tmp_path / "tree")

    def test_claude_gets_the_hooks_window_and_a_settings_file_that_goes_when_it_exits(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(harnessing, "serving", _fake_serving(seen))
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        def run(confinement):
            seen["command"], seen["env"] = list(confinement.argv), dict(confinement.env)
            seen["path"] = Path(confinement.argv[2])
            seen["settings"] = json.loads(seen["path"].read_text())
            seen["writable"] = os.access(seen["path"], os.W_OK)
            return 0

        assert claude.launch(["--sandbox", "--claude", str(binary), "--permissions", "guarded", "--", "-p", "go"], say=lambda _: None,
                             run_claude=run) == 0
        assert seen["model"] == harnessing.DEFAULT_MODEL and seen["want"].ctx == 0 and seen["want"].slots == 1
        assert seen["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "262144"
        assert seen["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "262144"
        command = seen["settings"]["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert shlex.split(command)[-2:] == ["hook", "pre"] and "--permissions" not in command, "the rail is the launcher's, in no command"
        assert not seen["writable"] and not seen["path"].exists() and seen["released"]

    def test_codex_gets_its_own_home_with_the_provider_and_the_hook_flag(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(harnessing, "serving", _fake_serving(seen))
        binary = tmp_path / "codex"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        def run(confinement):
            seen["command"] = list(confinement.argv)
            seen["text"] = (confinement.config / "config.toml").read_text()
            seen["config"] = tomllib.loads(seen["text"])
            seen["home"] = confinement.home
            return 3

        assert codex.launch(["qwen", "--sandbox", "--codex", str(binary), "--", "exec", "fix it"], say=lambda _: None,
                            run_codex=run) == 3
        assert seen["config"]["model_context_window"] == 262144
        bridge = seen["config"]["model_providers"]["poolhouse"]["base_url"]
        assert bridge.startswith("http://127.0.0.1:") and ":8899/" not in bridge, "Codex reaches the bridge; the server's port is in no profile"
        assert list(seen["config"]["mcp_servers"]) == ["lead"], "one server, the lead's"
        scope = seen["config"]["mcp_servers"]["lead"]
        assert scope["required"] is True
        assert scope["command"] == os.path.realpath(sys.executable)
        assert scope["args"][-1] == "mcp" and "--socket" in scope["args"] and "env" not in scope
        assert "POOLHOUSE_WORKSPACE_TOKEN" not in seen["text"] and "POOLHOUSE_BOARD" not in seen["text"]
        assert "--dangerously-bypass-hook-trust" in seen["command"] and seen["command"][-2:] == ["exec", "fix it"]
        assert tmp_path / "tree" not in seen["home"].parents and not seen["home"].exists()

    def test_an_unknown_profile_is_refused_before_anything_is_served(self, monkeypatch, tmp_path):
        monkeypatch.setattr(harnessing, "serving", lambda *a, **k: pytest.fail("served"))
        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        with pytest.raises(SystemExit) as refused:
            claude.launch(["--claude", str(binary), "--permissions", "root"], say=lambda _: None)
        assert refused.value.code == 2


class TestWorkspaceCommands:
    def test_a_hostile_label_is_one_argument_and_never_a_shell_line(self, tmp_path, monkeypatch):
        hostile = "x; touch /tmp/pwned $(id) `id`"
        seen = []
        def run(command, **kwargs):
            seen.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, 'nudge text', '')
        monkeypatch.setattr(harnesshook, '_reader_run', run)
        monkeypatch.chdir(tmp_path)
        assert harnesshook.nudge(hostile) == "nudge text"
        command, options = seen[0]
        assert command == runtime.module_argv('poolhouse.workspace.notification_reader', tmp_path,
                                               hostile, str(Path.cwd()))
        assert options.get('shell', False) is False
        assert options['timeout'] == harnesshook.NUDGE_S


class TestHarnessSession:
    @pytest.fixture
    def kit(self, workspace_node, tmp_path):  # noqa: F811
        lead = workspace_node.member("lead")
        project = repository(tmp_path / "proj")
        workspace_node.allow(lead, "source_add")
        workspace_node.client.call("project_add", "", lead.token, id=workspace_node.board, kind="folder",
                                   path=str(project))
        return SimpleNamespace(node=workspace_node, lead=lead, board=workspace_node.session(lead), project=project)

    @staticmethod
    def agents(kit, retired=False):
        return {a.name: a for a in kit.board.agents(retired=retired)}

    @staticmethod
    def token_file(kit, session):
        return kit.node.state / "client" / kit.node.board / f"{session.name}.token"

    def test_the_name_is_local_model_harness_cleaned_to_what_an_id_allows(self):
        assert harnessid.agent_name("Qwen3.8-27B-UD-Q4_K_XL", "codex") == "local-qwen3.8-27b-ud-q4_k_xl-codex"
        assert harnessid.agent_name("x" * 80, "codex") == harnessid.agent_name("x" * 80, "codex")
        assert len(harnessid.agent_name("x" * 80, "codex")) <= 48
        assert harnessid.agent_name("m", "codex", "mine") == "mine"

    def test_a_launcher_makes_a_subagent_session_of_the_session_that_starts_it_without_printing_a_token(self, kit):
        said = []
        session = harnessid.invite("local-test-codex", kit.project, kit.lead.name, said.append)
        assert session.minted and not session.persistent and session.board == kit.node.board
        assert self.agents(kit)[session.name].parent == kit.lead.name
        token_file = self.token_file(kit, session)
        assert private_path.problem(token_file) == ""
        secret = token_file.read_text().strip()
        assert secret not in "".join(said) and secret != kit.lead.token
        assert session.flags() == ["--agent", session.name]
        assert credentials.load(kit.node.state, kit.node.board, session.name) == secret

    def test_a_launcher_that_no_session_starts_makes_one_for_its_terminal_and_never_under_the_person(self, kit):
        persons = person.on(kit.node.client, kit.node.board)
        made = harnessid.invite("local-test-codex", kit.project, "", lambda _: None)
        rows = self.agents(kit)
        assert made.parent.name != persons.name and rows[made.name].parent == made.parent.name
        assert (rows[made.parent.name].harness, rows[made.parent.name].parent) == ("poolhouse-launch", "")

    def test_a_launcher_run_by_a_person_names_the_persons_session_as_its_parent(self, kit):
        persons = person.on(kit.node.client, kit.node.board)
        session = harnessid.invite("local-test-codex", kit.project, persons.name, lambda _: None)
        assert self.agents(kit)[session.name].parent == persons.name and session.parent.name == persons.name

    def test_a_launcher_run_by_an_agent_is_a_subagent_of_that_agents_session(self, kit, monkeypatch):
        monkeypatch.setenv(board_session.AGENT_ENV, kit.lead.name)
        session = harnessid.invite("local-test-codex", kit.project, "", lambda _: None)
        assert self.agents(kit)[session.name].parent == kit.lead.name

    def test_the_default_parent_of_a_launch_is_the_session_of_its_environment(self, kit, monkeypatch):
        monkeypatch.setenv(board_session.AGENT_ENV, kit.lead.name)
        args = harnessing.parser("codex", "codex", 8080, 1).parse_args(["qwen", "--project", str(kit.project)])
        with harnessing.opened(args, "codex", ("http://127.0.0.1:9", "qwen", 0), lambda _: None) as run:
            assert self.agents(kit)[run.session.name].parent == kit.lead.name
            assert f"update owner: {kit.lead.name}" in run.brief and f"the lead ({kit.lead.name})" in run.brief
        assert self.agents(kit, retired=True)[run.session.name].retired

    def test_a_launcher_whose_named_parent_has_no_session_is_refused(self, kit):
        said = []
        with pytest.raises(ValueError, match="could not be made"):
            harnessid.invite("local-test-codex", kit.project, "ghost", said.append)
        assert said[0].startswith("workspace connection refused") and "ghost" in said[0]

    def test_the_session_is_on_the_board_with_the_quiet_defaults(self, kit):
        session = harnessid.invite("local-test-codex", kit.project, kit.lead.name, lambda _: None)
        assert session.name in self.agents(kit)
        assert not session.session().subs()

    def test_a_launcher_records_its_model_as_claimed(self, kit, monkeypatch):
        session = harnessid.invite("local-test-codex", kit.project, kit.lead.name, lambda _: None)
        assert session.record_model("qwen-27b", "codex") is True
        agent = self.agents(kit)[session.name]
        assert (agent.model, agent.model_state, agent.harness) == ("qwen-27b", "claimed", "codex")
        monkeypatch.setenv("CLAUDECODE", "1")
        assert session.record_model("other", "codex") is True
        assert self.agents(kit)[session.name].model == "other"
        assert harnessid.HarnessSession("x").record_model("m", "codex") is False

    @pytest.mark.usefixtures("confinable", "from_a_terminal")
    def test_ending_the_session_retires_it_and_removes_harness_files(self, kit, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(harnessing, "serving", _fake_serving(seen))
        monkeypatch.setattr(codex, "alias_of", lambda url, model: "qwen")
        binary = tmp_path / "codex"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        def run(confinement):
            seen["home"] = confinement.home
            seen["name"] = confinement.agent
            assert seen["name"] != kit.lead.name, "the harness acts as a helper made for this launch, never as the launcher"
            assert not self.agents(kit)[seen["name"]].retired
            assert credentials.load(kit.node.state, kit.node.board, seen["name"])
            return 0

        assert codex.launch(["--sandbox", "--codex", str(binary), "--project", str(kit.project), "--as", kit.lead.name, "--", "exec", "go"],
                            say=lambda _: None, run_codex=run) == 0
        assert self.agents(kit, retired=True)[seen["name"]].retired
        assert not (kit.node.state / "client" / kit.node.board / f"{seen['name']}.token").exists()
        assert not seen["home"].exists()
        assert self.agents(kit)[kit.lead.name].parent == "" and kit.board.whoami().name == kit.lead.name

    def test_a_canned_endpoint_cannot_verify_a_delegated_agents_model(self, kit, monkeypatch):
        from poolhouse.testing.canned import CANNED_MODEL_ID, Canned, CannedServer

        monkeypatch.setenv("CLAUDECODE", "1")
        session = harnessid.invite("local-qwen", kit.project, kit.lead.name, lambda _: None)
        endpoint = CannedServer(Canned())
        try:
            assert session.record_model(CANNED_MODEL_ID, "codex", endpoint.base_url)
            assert self.agents(kit)[session.name].model_state == "claimed"
        finally:
            endpoint.close()
            session.revoke()

    def test_a_launched_session_is_a_distinct_private_child_that_a_revocation_ends(self, kit):
        said = []
        session = harnessid.invite("local-qwen", kit.project, kit.lead.name, said.append)
        child = credentials.load(kit.node.state, kit.node.board, session.name)
        assert session.name != kit.lead.name and child != kit.lead.token
        assert board_session.Session(kit.node.client, kit.node.board, child).whoami().name == session.name
        assert session.revoke() is True
        assert not self.token_file(kit, session).exists()
        assert self.agents(kit, retired=True)[session.name].retired
        with pytest.raises(NodeDenied):
            board_session.register(kit.node.client, kit.node.board, board_session.Native("", "harness", "nested"), child)
        assert kit.board.whoami().name == kit.lead.name and self.agents(kit)[kit.lead.name].retired is False

    def test_revoking_a_session_releases_only_its_own_claims(self, kit):
        first = harnessid.invite("local-one", kit.project, kit.lead.name, lambda _: None)
        second = harnessid.invite("local-two", kit.project, kit.lead.name, lambda _: None)
        mine, theirs = str(kit.project / "mine.py"), str(kit.project / "theirs.py")
        first.session().claim("area", mine)
        second.session().claim("area", theirs)
        assert first.revoke() is True
        assert held(kit.board, first.name) == [] and held(kit.board, second.name) == [("area", theirs)]
        assert second.revoke() is True

    def test_the_brief_names_who_the_agent_obeys_and_that_everything_else_is_data(self):
        text = harnessid.brief("n", "alias", "codex", "claude", ["reviewer"])
        assert "mcp__lead__ask" in text and "mcp__lead__note" in text and "--agent" not in text, "the box has the two tools and no command"
        assert "the lead (claude), reviewer" in text and "data written by" in text
        opencode_text = harnessid.brief("n", "alias", "opencode", "claude")
        assert "lead_ask" in opencode_text and "lead_note" in opencode_text and "mcp__lead__" not in opencode_text


@pytest.mark.usefixtures("confinable", "from_a_terminal")
class TestCodingAgent:
    def test_launch_coding_agent_runs_codex_with_the_project_and_the_orders(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(harnessing, "serving", _fake_serving(seen))
        monkeypatch.setattr(codex, "alias_of", lambda url, model: "qwen-27b")
        monkeypatch.setattr(harnessid, "invite", lambda name, project, parent, say, *, claim: seen.update(
            name=name, project=project, claim=claim) or harnessid.HarnessSession(name, base=tmp_path / "workspace"))
        binary = tmp_path / "codex"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        repository(tmp_path / "proj")

        def run(confinement):
            seen["agents"] = (confinement.config / "AGENTS.md").read_text()
            seen["command"] = list(confinement.argv)
            return 0

        monkeypatch.setattr(codex.shutil, "which", lambda n: str(binary))
        assert coding.launch_coding_agent("", coding_policy.Policy("guarded"), tmp_path / "proj", harness="codex", orders_from=["reviewer"],
                                          harness_args=["exec", "go"], say=lambda _: None, run_codex=run, sandbox=True) == 0
        assert seen["model"] == harnessing.DEFAULT_MODEL == "Qwen3.8-27B-UD-Q4_K_XL.gguf"
        assert seen["name"] == "local-qwen-27b-codex" and seen["project"] == (tmp_path / "proj").resolve()
        assert seen["claim"] == ("qwen-27b", "codex")
        assert "reviewer" in seen["agents"] and seen["command"][-2:] == ["exec", "go"]
        assert "danger-full-access" in seen["command"] and "workspace-write" not in seen["command"]

    def test_pi_is_the_default_coding_harness(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setitem(coding.HARNESSES, "pi", lambda argv, **options: seen.update(argv=argv, options=options) or 0)
        assert coding.launch_coding_agent("local-model.gguf", coding_policy.Policy("guarded"), tmp_path,
                                          say=lambda _: None) == 0
        assert seen["argv"] == ["local-model.gguf", "--permissions", "guarded", "--execution-mode", "execute", "--project", str(tmp_path)]

    def test_an_unknown_harness_is_refused(self):
        assert coding.launch_coding_agent("", coding_policy.Policy("read-only"), ".", "bash", say=lambda _: None) == 2


def test_explicit_head_and_none_override_measured_profile(monkeypatch):
    from types import SimpleNamespace

    from poolhouse import hub
    from poolhouse.serve.serving import Config, Serving

    measured = Config(serving=Serving(model="qwen.gguf", draft="old-head.gguf", spec_type="draft-mtp"))
    monkeypatch.setattr(harnessing.profile, "profile_for", lambda _, **__: SimpleNamespace(config=lambda **_: measured))
    monkeypatch.setattr(harnessing.profile, "said", lambda _: "measured profile")
    monkeypatch.setattr(harnessing.chat_template, "trained_context", lambda _: 262144)
    monkeypatch.setattr(hub, "head_choice", lambda model, asked: None if asked == "none" else SimpleNamespace(
        serving=lambda: "requested MTP head", over=lambda: {"draft": asked, "spec_type": "draft-mtp"}))
    selected = harnessing.config_for("qwen.gguf", harnessing.Want(ctx=262144, draft="matching-head.gguf"), lambda _: None)
    assert selected.serving.draft == "matching-head.gguf"
    assert selected.serving.slot_context == 262144
    disabled = harnessing.config_for("qwen.gguf", harnessing.Want(ctx=262144, draft="none"), lambda _: None)
    assert disabled.serving.draft == "" and disabled.serving.mtp is False
    automatic = harnessing.config_for("qwen.gguf", harnessing.Want(ctx=262144, draft="auto"), lambda _: None)
    assert automatic.serving.draft == "old-head.gguf"


def test_automatic_context_comes_from_the_device_model_fit(monkeypatch):
    from types import SimpleNamespace

    from poolhouse import hub
    monkeypatch.setattr(hub, "located", lambda model, loose=True: Path("qwen.gguf"))
    monkeypatch.setattr(harnessing.chat_template, "trained_context", lambda _: 200000)
    monkeypatch.setattr(harnessing.suggest, "largest_fit", lambda *a, **k: SimpleNamespace(context=98304, verdict="yellow", n_gpu_layers="auto",
                            kv_cache_type="q8_0", flash_attn=True, batch=512))
    monkeypatch.setattr(harnessing.profile, "profile_for", lambda _, **__: None)
    monkeypatch.setattr(hub, "head_choice", lambda *_: None)
    said = []
    config = harnessing.config_for("qwen.gguf", harnessing.Want(ctx=0), said.append)
    assert config.serving.slot_context == 98304
    assert any("automatically selected 98,304" in line for line in said)


@pytest.mark.parametrize("line, expected", [
    ("poolhouse-workspace inbox --agent own", "allow"),
    ("poolhouse-workspace send codex note 'sensor count 8' --agent own", "allow"),
    ("poolhouse-workspace inbox --agent other", "deny"),
    ("env POOLHOUSE_WORKSPACE_AGENT=other poolhouse-workspace inbox --agent other", "deny"),
    ("command poolhouse-workspace inbox --agent other", "deny"),
    ("poolhouse-workspace inbox --agent own --agent other", "deny"),
    ("poolhouse-workspace inbox --agent own --token-file /tmp/other", "deny"),
    ("poolhouse-workspace inbox", "allow"),
    ("poolhouse-workspace setup --agents own --agent own", "deny"),
    ("poolhouse-workspace inbox --agent own && rm README.md", "deny"),
])
def test_workspace_hook_binds_own_identity_and_keeps_human_commands_blocked(line, expected, tmp_path):
    payload = {"tool_name": "Bash", "tool_input": {"command": line}, "cwd": str(tmp_path)}
    rail = harnesshook.Rail(coding_policy.Policy("guarded"), "own", roots=(str(tmp_path),))
    answer = harnesshook.pre(payload, rail)
    assert answer["hookSpecificOutput"]["permissionDecision"] == expected


@pytest.mark.parametrize("name,role,action", [
    ("mcp__workspace__workspace_inbox", "read-only", "allow"),
    ("mcp__workspace__workspace_reputation", "read-only", "allow"),
    ("mcp__workspace__workspace_send", "guarded", "allow"),
    ("mcp__workspace__workspace_send", "read-only", "deny"),
    ("mcp__foreign__workspace_send", "guarded", "deny"),
])
def test_bound_workspace_mcp_obeys_role(name, role, action):
    assert decide(coding_policy.Policy(role), Call(name, {"to": "codex", "kind": "status", "text": "ready"})).action == action
