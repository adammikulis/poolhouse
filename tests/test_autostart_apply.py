"""`install` and `rollback` against real files in a temporary account home, with the service manager recorded."""

from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

import pytest
from autostart_support import Recorder, environment, seams, staged

from poolhouse import authority, caller, home
from poolhouse.caller import AgentRefused
from poolhouse.fleet import autostart_ledger
from poolhouse.fleet.autostart_apply import install, rollback


@pytest.fixture
def user(tmp_path, monkeypatch):
    return environment(tmp_path, monkeypatch)


def unit_dir(user: Path) -> Path:
    return user / ".config" / "systemd" / "user"


def tree(root: Path) -> list[str]:
    """Every path under ``root`` except the audit log, which a refusal appends to."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.name != "authority-audit.jsonl")


def run_install(done, recorder=None, **more):
    recorder = recorder or Recorder()
    return install(done.path, seams=seams(recorder, **more)), recorder


def test_install_places_the_staged_bytes_loads_them_and_records_it(tmp_path, user):
    done = staged(tmp_path)
    outcome, recorder = run_install(done)
    assert outcome.ok, outcome.lines
    for role in done.manifest.roles:
        for unit in role.units:
            placed = unit_dir(user) / unit.name
            assert placed.read_bytes() == (done.path.parent / unit.name).read_bytes()
            assert oct(placed.stat().st_mode & 0o777) == "0o644"
    assert ["systemctl", "--user", "enable", "--now", "poolhouse-traind.service"] in recorder.ran
    assert ["systemctl", "--user", "enable", "--now", "poolhouse-runtime-ensure.timer"] in recorder.ran
    ledger = autostart_ledger.read()
    assert ledger["version"] == 1 and done.manifest.id in ledger["used"]
    assert set(ledger["installed"]) == {"pool-daemon", "runtime-ensure"}
    assert "rollback" in outcome.rollback and sys.executable in outcome.rollback
    row = [r for r in authority.audit_rows() if r["event"] == "autostart.install"][-1]
    assert row["who"].startswith("user ") and row["manifest"] == done.manifest.id
    assert {u["sha256"] for u in row["units"]} == {u.sha256 for r in done.manifest.roles for u in r.units}


def test_install_backs_up_what_it_replaces_and_rollback_restores_it(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    old = unit_dir(user) / "poolhouse-traind.service"
    old.parent.mkdir(parents=True)
    old.write_text("[Service]\nExecStart=/old\n")
    outcome, recorder = run_install(done)
    assert outcome.ok
    backup = home.state("autostart", "backups", done.manifest.id)
    assert (backup / "poolhouse-traind.service").read_text() == "[Service]\nExecStart=/old\n"
    assert json.loads((backup / "index.json").read_text())["version"] == 1
    assert old.read_bytes() != b"[Service]\nExecStart=/old\n"
    undone = rollback(seams=seams(recorder))
    assert undone.ok, undone.lines
    assert old.read_text() == "[Service]\nExecStart=/old\n"
    assert autostart_ledger.read()["installed"] == {}
    assert [r for r in authority.audit_rows() if r["event"] == "autostart.rollback"]


def test_rollback_of_a_first_install_removes_the_files_and_unloads(tmp_path, user):
    done = staged(tmp_path)
    _, recorder = run_install(done)
    undone = rollback(seams=seams(recorder))
    assert undone.ok
    assert not any(unit_dir(user).glob("poolhouse-*"))
    assert ["systemctl", "--user", "disable", "--now", "poolhouse-traind.service"] in recorder.ran
    assert recorder.alive is False


def test_a_failed_load_puts_the_previous_unit_back_and_spends_nothing(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    old = unit_dir(user) / "poolhouse-traind.service"
    old.parent.mkdir(parents=True)
    old.write_text("old\n")
    outcome, _ = run_install(done, Recorder(fail_load=True))
    assert not outcome.ok and "load refused" in outcome.lines[0]
    assert old.read_text() == "old\n"
    assert autostart_ledger.read()["used"] == [] and autostart_ledger.read()["installed"] == {}


def test_a_unit_that_does_not_answer_its_health_probe_is_undone(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    outcome, _ = run_install(done, probe=lambda health: False)
    assert not outcome.ok and "health probe" in outcome.lines[0]
    assert not (unit_dir(user) / "poolhouse-traind.service").exists()
    assert autostart_ledger.read()["installed"] == {}


def test_an_expired_manifest_is_refused_and_nothing_is_written(tmp_path, user):
    done = staged(tmp_path)
    outcome, recorder = run_install(done, now=done.manifest.expires + 1)
    assert not outcome.ok and "expired" in outcome.lines[0]
    assert not unit_dir(user).exists() and recorder.ran == []


def test_a_manifest_prepared_on_another_device_is_refused(tmp_path, user):
    done = staged(tmp_path, device="d" * 64)
    outcome, _ = run_install(done)
    assert not outcome.ok and "another device" in " ".join(outcome.lines)


def test_replaying_an_installed_manifest_is_refused(tmp_path, user):
    done = staged(tmp_path)
    assert run_install(done)[0].ok
    again, _ = run_install(done)
    assert not again.ok and "already installed" in again.lines[0]


def test_changing_a_staged_unit_between_prepare_and_install_is_refused(tmp_path, user):
    done = staged(tmp_path)
    target = done.path.parent / "poolhouse-traind.service"
    target.write_bytes(target.read_bytes() + b"ExecStartPost=/bin/sh -c evil\n")
    outcome, recorder = run_install(done)
    assert not outcome.ok and "changed after it was prepared" in " ".join(outcome.lines)
    assert not unit_dir(user).exists() and recorder.ran == []


def test_a_unit_and_manifest_edited_together_still_differ_from_what_prepare_renders(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    target = done.path.parent / "poolhouse-traind.service"
    forged = target.read_bytes() + b"ExecStartPost=/bin/sh -c evil\n"
    target.write_bytes(forged)
    import hashlib

    raw = json.loads(done.path.read_text())
    raw["roles"][0]["units"][0]["sha256"] = hashlib.sha256(forged).hexdigest()
    done.path.write_text(json.dumps(raw))
    outcome, _ = run_install(done)
    assert not outcome.ok and "does not match the manifest" in " ".join(outcome.lines)


def test_a_consistently_forged_environment_is_refused(tmp_path, user):
    import hashlib
    from dataclasses import replace

    from poolhouse.fleet.autostart_manifest import parse
    from poolhouse.fleet.autostart_units import render

    done = staged(tmp_path, roles=("pool-daemon",))
    raw = json.loads(done.path.read_text())
    raw["roles"][0]["environment"]["PATH"] = "/tmp/evil"
    forged = render(replace(parse(raw).roles[0], units=()), "linux", "user", getpass.getuser())["service"]
    (done.path.parent / "poolhouse-traind.service").write_bytes(forged)
    raw["roles"][0]["units"][0]["sha256"] = hashlib.sha256(forged).hexdigest()
    done.path.write_text(json.dumps(raw))
    outcome, recorder = run_install(done)
    assert not outcome.ok and "environment is not the allowed one" in " ".join(outcome.lines)
    assert recorder.ran == []


def test_an_agent_is_refused_before_a_manifest_is_even_opened(tmp_path, user):
    with pytest.raises(AgentRefused):
        install(tmp_path / "missing.json", seams=seams(Recorder(), env={"CLAUDECODE": "1"}))


def edit(done, change):
    raw = json.loads(done.path.read_text())
    change(raw)
    done.path.write_text(json.dumps(raw))


@pytest.mark.parametrize("destination", [
    "{user}/.config/systemd/user/../../../.bashrc",
    "{user}/.config/systemd/user/../poolhouse-traind.service",
    "/etc/systemd/system/poolhouse-traind.service",
    "{user}/.config/systemd/user/other.service",
])
def test_a_destination_outside_the_allowed_set_is_refused(tmp_path, user, destination):
    done = staged(tmp_path, roles=("pool-daemon",))
    edit(done, lambda raw: raw["roles"][0]["units"][0].update(destination=destination.format(user=user)))
    outcome, recorder = run_install(done)
    assert not outcome.ok and recorder.ran == []
    assert not (user / ".bashrc").exists()


def test_a_symlinked_unit_directory_is_refused(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (user / ".config" / "systemd").mkdir(parents=True)
    (user / ".config" / "systemd" / "user").symlink_to(elsewhere)
    outcome, _ = run_install(done)
    assert not outcome.ok and "symbolic link" in " ".join(outcome.lines)
    assert list(elsewhere.iterdir()) == []


def test_a_symlinked_destination_file_is_refused(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    victim = tmp_path / "victim"
    victim.write_text("keep")
    unit_dir(user).mkdir(parents=True)
    (unit_dir(user) / "poolhouse-traind.service").symlink_to(victim)
    outcome, _ = run_install(done)
    assert not outcome.ok and victim.read_text() == "keep"


@pytest.mark.parametrize("change", [
    lambda raw: raw["roles"][0]["argv"].__setitem__(0, "/bin/sh"),
    lambda raw: raw["roles"][0]["argv"].extend(["-c", "evil"]),
    lambda raw: raw["roles"][0]["environment"].update(PATH="/tmp/evil"),
    lambda raw: raw["roles"][0].update(workdir="/tmp"),
    lambda raw: raw.update(platform="darwin"),
])
def test_a_manifest_edited_to_run_something_else_is_refused(tmp_path, user, change):
    done = staged(tmp_path, roles=("pool-daemon",))
    edit(done, change)
    outcome, recorder = run_install(done)
    assert not outcome.ok and recorder.ran == []
    assert not unit_dir(user).exists()


def test_a_missing_or_replaced_exec_target_is_refused_at_install(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    Path(done.manifest.roles[0].argv[0]).write_text("#!/bin/sh\nexit 0\n")
    outcome, _ = run_install(done)
    assert not outcome.ok and "not a runtime launcher" in " ".join(outcome.lines)


def test_a_staging_directory_others_can_write_is_refused(tmp_path, user):
    done = staged(tmp_path, roles=("pool-daemon",))
    done.path.parent.chmod(0o777)
    outcome, _ = run_install(done)
    assert not outcome.ok and "not private" in " ".join(outcome.lines)


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_NONINTERACTIVE", "POOLHOUSE_AGENT"])
def test_an_agent_is_refused_install_and_rollback_at_once_before_anything_is_read(tmp_path, user, marker):
    done = staged(tmp_path)
    before = tree(tmp_path)
    recorder = Recorder()
    agent = seams(recorder, env={marker: "1"})
    with pytest.raises(AgentRefused, match=r"autostart\.install"):
        install(done.path, seams=agent)
    with pytest.raises(AgentRefused, match="refused to an agent"):
        rollback(seams=agent)
    assert recorder.ran == [] and not unit_dir(user).exists()
    assert tree(tmp_path) == before
    refused = [r for r in authority.audit_rows() if r["event"] == "refused"]
    assert [r["action"] for r in refused] == ["install autostart units", "roll back autostart units"]
    assert all(r["who"].startswith(f"agent {marker}") for r in refused)


def test_a_process_no_agent_started_installs_and_rolls_back_with_no_terminal_and_nothing_asked(tmp_path, user, monkeypatch):
    def asked(*_args, **_kwargs):
        raise AssertionError("nothing may prompt")

    monkeypatch.setattr("builtins.input", asked)
    assert not caller.marked() and not caller.is_terminal(sys.stdin) and not caller.is_terminal(sys.stdout)
    done = staged(tmp_path)
    outcome, recorder = run_install(done)
    assert outcome.ok, outcome.lines
    assert rollback(seams=seams(recorder)).ok
    ran = [r for r in authority.audit_rows() if r["event"] == "ran"]
    assert [r["action"] for r in ran] == ["install autostart units", "roll back autostart units"]
    assert all(r["who"].startswith("user ") for r in ran)


def test_an_agent_installs_while_the_gate_is_delegated_and_the_audit_names_it(tmp_path, user, monkeypatch):
    monkeypatch.delenv(authority.FLOOR_ENV)
    authority.set_state(["autostart.install"], authority.DELEGATED, by="test")
    monkeypatch.setenv("CLAUDECODE", "1")
    outcome, _ = run_install(staged(tmp_path))
    assert outcome.ok, outcome.lines
    used = [r for r in authority.audit_rows() if r["event"] == "authority.use" and r["gate"] == "autostart.install"]
    assert len(used) == 1 and used[0]["who"].startswith("agent CLAUDECODE")
    row = [r for r in authority.audit_rows() if r["event"] == "autostart.install"][-1]
    assert row["who"].startswith("agent CLAUDECODE")


def test_a_system_manifest_needs_the_flag_and_only_then(tmp_path, user):
    if sys.platform != "darwin":
        pytest.skip("the system directories are checked as they are on macOS")
    done = staged(tmp_path, platform="darwin", scope="system")
    plain = install(done.path, seams=seams(Recorder(), "darwin", "system"))
    assert not plain.ok and "--system" in " ".join(plain.lines)
    user_scope = staged(tmp_path, platform="darwin")
    flagged = install(user_scope.path, system=True, seams=seams(Recorder(), "darwin"))
    assert not flagged.ok and "--system" in " ".join(flagged.lines)


def test_a_system_install_is_refused_at_once_with_the_reason_and_places_nothing(tmp_path, user):
    """Placing a unit in a system directory needs administrator rights, which nothing in Poolhouse asks for."""
    if sys.platform != "darwin":
        pytest.skip("the system directories are checked as they are on macOS")
    from poolhouse.fleet.autostart_backends import NEEDS_ROOT

    done = staged(tmp_path, roles=("pool-daemon",), platform="darwin", scope="system")
    recorder = Recorder()
    outcome = install(done.path, system=True, seams=seams(recorder, "darwin", "system"))
    assert not outcome.ok and NEEDS_ROOT in " ".join(outcome.lines)
    assert recorder.ran == []
    assert not Path("/Library/LaunchDaemons/com.poolhouse.traind.plist").exists()


def test_the_unit_runs_as_the_user_even_when_installed_system_wide(tmp_path, user):
    import plistlib

    done = staged(tmp_path, roles=("pool-daemon",), platform="darwin", scope="system")
    body = plistlib.loads((done.path.parent / "com.poolhouse.traind.plist").read_bytes())
    import getpass

    assert body["UserName"] == getpass.getuser() != "root"


def test_a_concurrent_install_is_refused_not_interleaved(tmp_path, user):
    from poolhouse.lock import only_one

    done = staged(tmp_path)
    with only_one(home.state("autostart", "apply.lock"), wait=False):
        outcome, _ = run_install(done)
    assert not outcome.ok and "another" in outcome.lines[0]

