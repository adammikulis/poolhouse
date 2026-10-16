"""scripts/agent-sandbox: the profiles it derives, the staged files and manifest, and the probes."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import agent_sandbox_probe as probe  # noqa: E402
import agent_sandbox_profile as profile  # noqa: E402
import agent_sandbox_stage as stage  # noqa: E402


@pytest.fixture
def layout(tmp_path):
    home = tmp_path / "home"
    return profile.Layout(
        home=home, state=home / ".poolhouse", primary=tmp_path / "poolhouse",
        worktrees=(tmp_path / "poolhouse-feature",), scratch=(tmp_path / "scratch",), platform="darwin")


def test_claude_settings_close_the_escape_hatches(layout):
    sandbox = profile.claude_settings(layout)["sandbox"]
    assert sandbox["enabled"] and sandbox["failIfUnavailable"]
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["autoAllowBashIfSandboxed"] is False


def test_only_the_gated_runtime_deploy_commands_run_outside_the_sandbox(layout):
    sandbox = profile.claude_settings(layout)["sandbox"]
    assert sandbox["excludedCommands"] == ["poolhouse runtime ensure", "poolhouse runtime rollback", "poolhouse runtime restart-host"]
    assert str(layout.state / "runtimes") in sandbox["filesystem"]["denyWrite"]


def test_every_worktree_is_writable_and_its_git_config_and_hooks_are_not(layout, tmp_path):
    files = profile.claude_settings(layout)["sandbox"]["filesystem"]
    for checkout in (layout.primary, layout.worktrees[0]):
        assert str(checkout) in files["allowWrite"]
        for name in ("scripts/hooks", ".claude", ".git/config", ".git/hooks"):
            assert str(checkout / name) in files["denyWrite"]
    shared = layout.primary / ".git"
    assert str(shared) in files["allowWrite"]
    assert str(shared / "config") in files["denyWrite"]
    assert str(shared / "hooks") in files["denyWrite"]


def test_the_node_client_folder_holding_every_session_token_is_closed_to_reads_and_writes(layout):
    clients = str(layout.state / "node" / "client")
    for files in (profile.claude_settings(layout)["sandbox"]["filesystem"], profile.srt_settings(layout)["filesystem"]):
        assert clients in files["denyRead"] and clients in files["denyWrite"]
    assert profile.claude_settings(layout)["sandbox"]["filesystem"]["allowRead"] == []
    for path in (profile.claude_settings(layout)["sandbox"]["filesystem"]["allowWrite"] + profile.allow_read(layout)):
        assert not path.startswith(clients), "no allowed path reaches a token or a .parent file"
    assert f"Write(/{clients}/**)" in profile.claude_settings(layout)["permissions"]["deny"]
    assert not any("workspace/tokens" in path for path in profile.deny_read(layout) + profile.deny_write(layout))


def test_the_person_key_custody_is_closed_to_reads_and_writes_and_to_every_file_tool(layout):
    custody = str(layout.state / "node" / "person")
    settings = profile.claude_settings(layout)
    for files in (settings["sandbox"]["filesystem"], profile.srt_settings(layout)["filesystem"]):
        assert custody in files["denyRead"] and custody in files["denyWrite"]
    for tool in ("Read", "Edit", "Write"):
        assert f"{tool}(/{custody}/**)" in settings["permissions"]["deny"], tool


def test_the_file_tools_are_refused_every_path_the_sandbox_denies_to_reads(layout):
    settings = profile.claude_settings(layout)
    for path in profile.deny_read(layout):
        assert f"Read(/{path}/**)" in settings["permissions"]["deny"], path
    assert f"Read(/{layout.state / 'node' / 'client'}/**)" in settings["permissions"]["deny"]


def test_the_credentials_of_the_machine_are_denied_to_reads(layout):
    files = profile.claude_settings(layout)["sandbox"]["filesystem"]
    for name in (".ssh", ".aws", ".config/gh"):
        assert str(layout.home / name) in files["denyRead"]


def test_runtimes_transcripts_and_persistence_paths_are_not_writable(layout):
    denied = profile.claude_settings(layout)["sandbox"]["filesystem"]["denyWrite"]
    for path in (layout.state / "runtimes", layout.home / ".claude", layout.home / "Library/LaunchAgents",
                 layout.home / ".zshrc", layout.home / ".pyenv/versions"):
        assert str(path) in denied


def test_linux_denies_systemd_user_directories_instead_of_launch_agents(layout):
    linux = profile.Layout(layout.home, layout.state, layout.primary, platform="linux")
    denied = profile.claude_settings(linux)["sandbox"]["filesystem"]["denyWrite"]
    assert str(linux.home / ".config/systemd") in denied
    assert str(linux.home / "Library/LaunchAgents") not in denied
    assert "allowLocalBinding" not in profile.claude_settings(linux)["sandbox"]["network"]


def test_network_allows_only_the_listed_hosts(layout):
    network = profile.claude_settings(layout)["sandbox"]["network"]
    assert network["allowedDomains"] == list(profile.HOSTS)
    assert "127.0.0.1:8770" in set(network["allowedDomains"])
    assert {"pypi.org", "*.hf.co"} <= set(network["allowedDomains"])
    assert "example.com" not in set(network["allowedDomains"])


def test_scrubbed_environment_names_messaging_and_search_path_variables(layout):
    deny = profile.claude_settings(layout)["sandbox"]["credentials"]["envVars"]["deny"]
    for name in ("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "POOLHOUSE_HOME",
                 "PYTHONPATH", "GIT_CONFIG_GLOBAL", "SSH_AUTH_SOCK"):
        assert name in deny


def test_file_tools_are_refused_the_hook_directory(layout):
    deny = profile.claude_settings(layout)["permissions"]["deny"]
    assert f"Edit(/{layout.primary}/scripts/hooks/**)" in deny
    assert f"Write(/{layout.home}/.claude/**)" in deny


def test_codex_fragment_parses_and_lists_the_checkouts(layout):
    parsed = tomllib.loads(profile.codex_toml(layout))
    assert parsed["sandbox_mode"] == "workspace-write"
    roots = parsed["sandbox_workspace_write"]["writable_roots"]
    assert str(layout.primary) in roots and str(layout.worktrees[0]) in roots
    assert parsed["sandbox_workspace_write"]["network_access"] is False


def test_srt_settings_carry_the_same_filesystem_lists(layout):
    srt = profile.srt_settings(layout)
    claude = profile.claude_settings(layout)["sandbox"]["filesystem"]
    assert srt["filesystem"]["denyWrite"] == claude["denyWrite"]
    assert {"api.anthropic.com"} <= set(srt["network"]["allowedDomains"])


def test_prepare_stages_files_with_matching_digests(layout):
    target = stage.prepare(layout, "device-1", now=1000.0)
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["device_id"] == "device-1"
    assert manifest["expires"] == 1000.0 + stage.LIFETIME
    for item in manifest["files"]:
        assert stage.digest((target / item["name"]).read_bytes()) == item["sha256"]
    assert {item["destination"] for item in manifest["files"]} >= {
        "/Library/Application Support/ClaudeCode/managed-settings.json"}


def test_install_command_is_one_sudo_line_carrying_the_digests(layout):
    stage.prepare(layout, "device-1")
    command = stage.install_command(layout.state)
    assert command.startswith("sudo sh -c ") and "\n" not in command
    for item in stage.load(layout.state)["files"]:
        assert item["sha256"] in command
    assert "install -m 644" in command


def test_installed_reports_absent_match_and_differs(layout, tmp_path, monkeypatch):
    destination = tmp_path / "managed-settings.json"
    monkeypatch.setitem(stage.DESTINATIONS["darwin"], "claude-managed-settings.json", str(destination))
    target = stage.prepare(layout, "device-1")
    assert (str(destination), "absent") in stage.installed(layout.state)
    destination.write_bytes((target / "claude-managed-settings.json").read_bytes())
    assert (str(destination), "match") in stage.installed(layout.state)
    destination.write_text("{}")
    assert (str(destination), "differs") in stage.installed(layout.state)


def test_staged_files_expire(layout):
    stage.prepare(layout, "device-1", now=1000.0)
    assert not stage.expired(layout.state, now=1000.0 + stage.LIFETIME - 1)
    assert stage.expired(layout.state, now=1000.0 + stage.LIFETIME + 1)
    assert not stage.expired(layout.state / "elsewhere")


def test_command_line_prepares_and_reports_without_touching_destinations(tmp_path):
    base = [sys.executable, str(ROOT / "scripts" / "agent-sandbox"),
            "--home", str(tmp_path / "home"), "--state", str(tmp_path / "state")]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    (tmp_path / "home").mkdir()
    prepared = subprocess.run([*base, "prepare"], capture_output=True, text=True, env=env, check=False,
                              cwd=ROOT)
    assert prepared.returncode == 0, prepared.stderr
    assert "sudo sh -c" in prepared.stdout
    reported = subprocess.run([*base, "status"], capture_output=True, text=True, env=env, check=False,
                              cwd=ROOT)
    assert reported.returncode == 0
    assert "managed-settings.json" in reported.stdout
    refused = subprocess.run([*base, "install"], capture_output=True, text=True, env=env, check=False,
                             cwd=ROOT)
    assert refused.returncode != 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_a_write_probe_passes_only_where_the_write_is_refused(tmp_path):
    shut = tmp_path / "shut"
    shut.mkdir()
    shut.chmod(0o500)
    try:
        assert probe._denied_write("shut", shut / "x").status == probe.PASS
        assert probe._denied_write("open", tmp_path / "x").status == probe.FAIL
    finally:
        shut.chmod(0o700)
    assert probe._denied_write("missing", tmp_path / "none" / "x").status == probe.SKIP
    assert not (tmp_path / "x").exists()


def test_a_read_probe_fails_when_the_file_reads(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("x")
    assert probe._denied_read("token", secret).status == probe.FAIL
    assert probe._denied_read("token", tmp_path / "none").status == probe.SKIP


def test_connect_state_tells_refused_from_open():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert probe.connect_state("127.0.0.1", port) == "open"
    assert probe.connect_state("127.0.0.1", port) == "refused"


def test_environment_probe_fails_for_a_scrubbed_variable_that_is_set():
    results = {item.name: item.status for item in probe.environment_probes({"PYTHONPATH": "/x"})}
    assert results["env PYTHONPATH absent"] == probe.FAIL
    assert results["env CLAUDE_CODE_MESSAGING_SOCKET absent"] == probe.PASS


def test_git_probe_pushes_to_a_local_bare_repository():
    assert probe.git_probe().status == probe.PASS


def test_inside_needs_a_refusal_and_no_open_line():
    passed, failed = probe.Result("a", probe.PASS), probe.Result("b", probe.FAIL)
    assert probe.inside([passed])
    assert not probe.inside([passed, failed])
    assert not probe.inside([probe.Result("c", probe.SKIP)])
    assert probe.render([passed]).startswith("PASS  a")


def test_the_install_command_installs_the_staged_files_when_run_without_sudo(layout, tmp_path, monkeypatch):
    places = {name: str(tmp_path / "root" / name) for name in stage.DESTINATIONS["darwin"]}
    monkeypatch.setitem(stage.DESTINATIONS, "darwin", places)
    stage.prepare(layout, "device-1")
    argv = shlex.split(stage.install_command(layout.state))[1:]
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert all(state == "match" for _, state in stage.installed(layout.state))


def covered(path, roots):
    path = Path(os.path.normpath(path))
    return any(path == Path(root) or Path(root) in path.parents for root in roots)


def test_the_sandbox_writes_its_own_stores_and_nothing_of_the_owners(layout):
    import gaterun

    roots = profile.write_roots(layout)
    cache = layout.home / ".cache"
    for needed in ("test-reuse-sandbox/ab12/entries/x.json", "test-reuse-sandbox/ab12/jobs/j/log", "test-cargo-sandbox/targets/k/debug/x",
                   "test-cargo-sandbox/workspaces/k/app/poolhouse-node/src/main.rs", "test-cargo-sandbox/test-node/k/poolhouse-node",
                   "dev-test-slots/mutex.lock"):
        assert covered(cache / needed, roots), needed
    gate = gaterun.steps_folder(cache / "test-reuse-sandbox", "ab12")
    for hostile in ("test-reuse/ab12/entries/forged.json", "test-reuse/ab12/chain.jsonl", "test-reuse/ab12/latest/" + "0" * 64,
                    "test-reuse", "test-cargo/workspaces/k/app/poolhouse-node/src/main.rs", "test-cargo/targets/k/debug/poolhouse-node",
                    "test-cargo/test-node/k/poolhouse-node", "test-gate/ab12/step.json", "x", "test-reuse-evil/x",
                    "test-reuse-sandbox/../test-reuse/ab12/chain.jsonl", "test-cargo-sandbox/../test-cargo/targets/k/x",
                    "test-reuse-sandbox/../.ssh/id_ed25519"):
        assert not covered(cache / hostile, roots), hostile
    assert not covered(gate / "step.json", roots) and not covered(layout.home / ".cache", roots)
    assert not covered(layout.home / ".config" / "gh" / "hosts.yml", roots)


def test_a_sandboxed_scripts_test_is_pointed_at_folders_the_sandbox_may_write(layout, monkeypatch):
    import testnode

    from poolhouse.activity import reuse

    account = testnode.account()
    monkeypatch.setenv("HOME", str(account))
    for name in (testnode.CARGO_ENV, "DEV_TEST_REUSE_DIR", "DEV_TEST_SLOTS_DIR"):
        monkeypatch.delenv(name, raising=False)
    real = profile.Layout(home=account, state=account / ".poolhouse", primary=layout.primary, platform="darwin")
    roots = profile.write_roots(real)
    owner = {"store": reuse.reuse_base(), "cargo": testnode.cargo_root(), "node": testnode.node_folder()}
    assert owner["store"] == account / ".cache" / "test-reuse" and owner["cargo"] == account / ".cache" / "test-cargo"
    owners = [str(account / ".cache" / "test-reuse"), str(account / ".cache" / "test-cargo"), str(account / ".cache" / "test-gate")]
    for kind, folder in owner.items():
        assert not covered(folder, roots) and not covered(folder / "k" / "x", roots), (kind, folder)
    for variables in (profile.claude_settings(real)["env"], tomllib.loads(profile.codex_toml(real))["shell_environment_policy"]["set"]):
        assert set(profile.store_environment(real)) <= set(variables)
        for name, value in variables.items():
            monkeypatch.setenv(name, value)
        for kind, folder in {"store": reuse.reuse_base(), "cargo": testnode.cargo_root(), "node": testnode.node_folder()}.items():
            assert covered(folder, roots) and folder != owner[kind], (kind, folder)
            assert not covered(folder, owners), (kind, folder)


def test_the_node_binary_is_built_in_the_folder_the_cargo_variable_names(monkeypatch, tmp_path):
    import testnode

    monkeypatch.setenv(testnode.CARGO_ENV, str(tmp_path / "named"))
    assert testnode.node_folder() == tmp_path / "named" / testnode.FOLDER
    assert testnode.node_folder(tmp_path / "given") == tmp_path / "given" / testnode.FOLDER



def test_no_write_root_reaches_above_the_cache_folders_it_names(layout):
    cache = layout.home / ".cache"
    above = [root for root in profile.write_roots(layout) if Path(root) in (cache, layout.home, layout.home.parent)]
    assert above == []
