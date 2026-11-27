"""The one device setup command: its decisions per platform, and that what it runs on the host is fixed argv."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from poolhouse import device_host, device_setup, node_binary, node_join, pool_cli
from poolhouse.api import pool

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text, mirrored", [
    ("", False),
    ("[wsl2]\nmemory=8GB\n", False),
    ("[wsl2]\nnetworkingMode=NAT\n", False),
    ("[wsl2]\nmemory=8GB\n[experimental]\nnetworkingMode=mirrored\n", False),
    ("[WSL2]\n networkingmode = Mirrored \n", True),
])
def test_the_wslconfig_is_read_for_mirrored_networking_under_wsl2_only(text, mirrored):
    assert device_setup.is_mirrored(text) is mirrored


def test_the_node_comes_from_the_runtime_then_a_binary_in_the_repository_then_cargo_then_rust(tmp_path):
    runtime_prefix = tmp_path / "rt"
    repo = tmp_path / "repo"
    assert device_setup.node_source(repo, selected=None, cargo=None) == device_setup.Source("missing")
    assert device_setup.node_source(repo, selected=None, cargo="/bin/cargo") == device_setup.Source("build")
    built = repo / "app" / "target" / "release" / node_binary.name()
    built.parent.mkdir(parents=True)
    built.write_text("x", encoding="utf-8")
    assert device_setup.node_source(repo, selected=None, cargo=None) == device_setup.Source("found", str(built))
    chosen = node_binary.location(runtime_prefix)
    chosen.parent.mkdir(parents=True)
    chosen.write_text("x", encoding="utf-8")
    assert device_setup.node_source(repo, selected=runtime_prefix, cargo=None).kind == "runtime"


def needs(**over):
    base = {"kind": "mac", "mode": "mirrored", "mirrored_set": False, "firewall_missing": False, "source": device_setup.Source("found", "/n")}
    return device_setup.Needs(**{**base, **over})


def test_the_plan_lists_only_what_will_change_and_always_the_node_and_the_check():
    nothing = device_setup.plan(needs())
    assert len(nothing) == 2 and "/n" in nothing[0] and "join check" in nothing[1]
    windows = "\n".join(device_setup.plan(needs(kind="windows", firewall_missing=True, source=device_setup.Source("missing"))))
    assert "firewall" not in windows and "inbound allow rules" in windows and "Rust" in windows
    assert "wslconfig" not in windows
    assert str(node_join.DEFAULT_PORT) in windows and str(node_join.DEFAULT_BEACON_PORT) in windows


def test_wsl_in_nat_mode_restarts_only_where_the_person_already_set_mirrored_networking():
    ready = needs(kind="wsl", mode="nat", mirrored_set=True)
    steps = device_setup.plan(ready)
    assert ready.restart_wsl and "restart WSL" in steps[-1] and "join check" not in "\n".join(steps)
    lacking = needs(kind="wsl", mode="nat")
    steps = device_setup.plan(lacking)
    assert not lacking.restart_wsl and steps[-1] == f"stop: {device_setup.WSL_FIX}"
    assert "never writes" in steps[-1] and "networkingMode=mirrored" in steps[-1]
    assert not needs(kind="wsl").restart_wsl and not needs(kind="windows", mode="nat").restart_wsl
    assert ".wslconfig" not in "\n".join(device_setup.plan(needs(kind="mac", mode="nat")))


@pytest.mark.parametrize("platform, version, kind", [
    ("win32", "", "windows"), ("darwin", "", "mac"), ("linux", "Linux 5.15 microsoft-standard-WSL2", "wsl"), ("linux", "Linux 6.1 generic", "linux")])
def test_the_kind_of_machine_comes_from_the_platform_and_the_kernel(platform, version, kind):
    assert device_host.host_kind(platform, version) == kind


def test_the_firewall_script_covers_both_ports_and_the_vm():
    script = device_host.rules_script()
    assert f"-LocalPort {node_join.DEFAULT_PORT}" in script and f"-LocalPort {node_join.DEFAULT_BEACON_PORT}" in script
    assert device_host.HYPERV_ID in script and "-Profile Private" in script


def test_a_dry_run_lists_the_changes_and_makes_none(monkeypatch, capsys):
    monkeypatch.setattr(device_setup, "survey", lambda repo: needs(kind="windows", firewall_missing=True))
    monkeypatch.setattr(device_host, "add_firewall", lambda: pytest.fail("a dry run changed the machine"))
    assert device_setup.main(["--dry-run"]) == 0
    assert "inbound allow rules" in capsys.readouterr().out


def test_no_flag_is_needed_to_make_the_changes_and_nothing_is_asked(monkeypatch, capsys):
    monkeypatch.setattr(device_setup, "survey", lambda repo: needs(kind="windows", firewall_missing=True))
    added = []
    monkeypatch.setattr(device_host, "add_firewall", lambda: added.append(1))
    monkeypatch.setattr(device_host, "firewall_missing", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": pytest.fail("nothing is asked"))
    assert device_setup.main([]) == 1
    assert added == [1], "the change was made with no yes given"
    with pytest.raises(SystemExit):
        device_setup.main(["--yes"])


def test_wsl_that_lacks_mirroring_is_told_what_to_add_and_the_users_wslconfig_is_not_written(monkeypatch, tmp_path, capsys):
    config = tmp_path / ".wslconfig"
    config.write_text("[wsl2]\nmemory=8GB\n", encoding="utf-8")
    monkeypatch.setattr(device_host, "current_kind", lambda: "wsl")
    monkeypatch.setattr(device_host, "firewall_missing", lambda: False)
    monkeypatch.setattr(device_host, "add_firewall", lambda: pytest.fail("no rule was missing"))
    monkeypatch.setattr(device_host, "wslconfig_path", lambda kind: config)
    monkeypatch.setattr(device_setup.node_join_check, "route_address", lambda: "172.20.144.1")
    monkeypatch.setattr(device_setup.node_join_check, "wsl_mode", lambda address: "nat")
    monkeypatch.setattr(device_setup.runtime, "selection_prefix", lambda: None)
    monkeypatch.setattr(device_host, "restart_wsl", lambda: pytest.fail("restarted WSL with nothing changed"))
    monkeypatch.setattr(device_setup, "binary_of", lambda *a: pytest.fail("started the node on a device that is not ready"))
    assert device_setup.main([]) == 1
    assert config.read_text(encoding="utf-8") == "[wsl2]\nmemory=8GB\n" and list(tmp_path.iterdir()) == [config]
    said = capsys.readouterr().err
    assert "NOT READY" in said and device_setup.WSL_FIX in said


def test_wsl_with_mirroring_set_but_still_nat_restarts_before_anything_else(monkeypatch):
    calls = []
    monkeypatch.setattr(device_setup, "survey", lambda repo: needs(kind="wsl", mode="nat", mirrored_set=True))
    monkeypatch.setattr(device_host, "restart_wsl", lambda: calls.append("restart"))
    monkeypatch.setattr(device_setup, "binary_of", lambda *a: pytest.fail("started the node before the restart"))
    assert device_setup.main([]) == 0
    assert calls == ["restart"]


def test_a_device_without_rust_is_told_to_install_it_and_nothing_installs_a_toolchain(monkeypatch, tmp_path):
    calls = _recorded(monkeypatch)
    with pytest.raises(node_join.PoolError, match="never installs it"):
        device_setup.binary_of(needs(source=device_setup.Source("missing")), tmp_path)
    assert calls == []


def test_a_firewall_rule_that_could_not_be_added_is_not_ready_with_the_reason(monkeypatch, capsys):
    monkeypatch.setattr(device_setup, "survey", lambda repo: needs(kind="windows", firewall_missing=True))
    monkeypatch.setattr(device_host, "add_firewall", lambda: None)
    monkeypatch.setattr(device_host, "firewall_missing", lambda: True)
    assert device_setup.main([]) == 1
    said = capsys.readouterr().err
    assert "NOT READY" in said and device_host.NOT_ELEVATED in said


def test_joining_without_a_code_is_refused_before_the_node_is_asked(monkeypatch, capsys):
    monkeypatch.delenv(pool_cli.JOIN_CODE_ENV, raising=False)
    monkeypatch.setattr(pool.founding, "ticket_for", lambda *a, **k: pytest.fail("the node was asked"))
    assert pool_cli.main(["join", "--host", "192.0.2.1"]) == 1
    assert "give the code" in capsys.readouterr().out


def _recorded(monkeypatch, stdout=""):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(device_host.subprocess, "run", run)
    return calls


def test_a_hostile_windows_profile_path_reaches_wslpath_as_one_argument_and_no_command_goes_through_a_shell(monkeypatch):
    hostile = "C:\\Users\\x'; rm -rf ~; echo '$(id)"
    calls = _recorded(monkeypatch, stdout=hostile)
    device_host.wslconfig_path("wsl")
    assert calls[1][0] == ["wslpath", "-u", hostile]
    for argv, kwargs in calls:
        assert isinstance(argv, list) and not kwargs.get("shell")


def test_every_command_the_host_runs_is_a_fixed_argv_with_no_input_from_outside(monkeypatch):
    calls = _recorded(monkeypatch)
    device_host.restart_wsl()
    device_host.firewall_missing()
    assert calls[0][0] == ["wsl.exe", "--shutdown"]
    assert all(isinstance(argv, list) and not kwargs.get("shell") for argv, kwargs in calls)


def test_the_firewall_rules_are_added_where_the_shell_is_an_administrators_and_refused_at_once_with_the_reason_where_it_is_not(monkeypatch):
    calls = _recorded(monkeypatch, stdout="False")
    with pytest.raises(PermissionError, match="administrator's PowerShell"):
        device_host.add_firewall()
    assert len(calls) == 1 and "IsInRole" in calls[0][0][-1], "only the elevation was asked: no rule was added and no prompt was raised"
    calls = _recorded(monkeypatch, stdout="True")
    device_host.add_firewall()
    assert "New-NetFirewallRule" in calls[-1][0][-1] and not any("RunAs" in part for argv, _ in calls for part in argv)
