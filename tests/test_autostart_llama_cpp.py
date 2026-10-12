"""The optional daily `llama-cpp-ensure` unit: `prepare` stages it, a person installs it, an agent never does."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from autostart_support import Recorder, environment, seams, staged

from poolhouse import home
from poolhouse.fleet.autostart_apply import install
from poolhouse.fleet.autostart_manifest import ROLES, ManifestError, parse
from poolhouse.fleet.autostart_verify import Context, problems
from poolhouse.person import HumanRequired

DAY = 24 * 3600


@pytest.fixture
def user(tmp_path, monkeypatch):
    return environment(tmp_path, monkeypatch)


def staged_text(done, name: str) -> bytes:
    return (done.path.parent / name).read_bytes()


def test_the_role_runs_poolhouse_serve_llama_cpp_ensure_and_nothing_else(tmp_path, user):
    assert "llama-cpp-ensure" in ROLES
    done = staged(tmp_path, roles=("llama-cpp-ensure",), platform="linux")
    role = done.manifest.roles[0]
    assert Path(role.argv[0]).name == "poolhouse-serve" and role.argv[1:] == ("llama-cpp", "ensure")
    assert Path(role.argv[0]).is_relative_to(home.state("bin"))


def test_each_platform_schedules_it_daily(tmp_path, user):
    mac = staged(tmp_path, roles=("llama-cpp-ensure",), platform="darwin")
    plist = plistlib.loads(staged_text(mac, "com.poolhouse.llama-cpp-ensure.plist"))
    assert plist["StartInterval"] == DAY and "KeepAlive" not in plist
    nix = staged(tmp_path, roles=("llama-cpp-ensure",), platform="linux")
    assert b"OnUnitActiveSec=86400" in staged_text(nix, "poolhouse-llama-cpp-ensure.timer")
    assert b"Type=oneshot" in staged_text(nix, "poolhouse-llama-cpp-ensure.service")
    win = staged(tmp_path, roles=("llama-cpp-ensure",), platform="win32")
    assert "<Interval>PT24H</Interval>" in staged_text(win, "com.poolhouse.llama-cpp-ensure.xml").decode("utf-16")


def test_preparing_it_installs_nothing(tmp_path, user):
    before = sorted(p.relative_to(user).as_posix() for p in user.rglob("*"))
    staged(tmp_path, roles=("llama-cpp-ensure",), platform="darwin")
    assert sorted(p.relative_to(user).as_posix() for p in user.rglob("*")) == before
    assert not (user / "Library").exists()


def test_the_unit_can_sit_beside_the_other_two_roles(tmp_path, user):
    done = staged(tmp_path, roles=ROLES, platform="linux")
    assert [r.role for r in done.manifest.roles] == list(ROLES)
    assert parse(done.manifest.to_dict()).roles == done.manifest.roles


def test_a_manifest_whose_ensure_arguments_were_changed_does_not_parse_into_a_valid_install(tmp_path, user):
    done = staged(tmp_path, roles=("llama-cpp-ensure",), platform="linux")
    forged = done.manifest.to_dict()
    forged["roles"][0]["argv"][-1] = "update"
    loaded = parse(forged)
    found = problems(loaded, done.path.parent, Context(loaded.created + 1, loaded.device, "linux", False))
    assert any("takes only its own options" in line for line in found)


def test_an_agent_is_refused_the_install(tmp_path, user):
    done = staged(tmp_path, roles=("llama-cpp-ensure",), platform="linux")
    recorder = Recorder()
    with pytest.raises(HumanRequired):
        install(done.path, seams=seams(recorder, typed=lambda _p: done.manifest.id, env={"CLAUDECODE": "1"}))
    assert recorder.ran == [] and not (user / ".config" / "systemd").exists()


def test_a_person_installs_it_and_the_timer_is_enabled(tmp_path, user):
    done = staged(tmp_path, roles=("llama-cpp-ensure",), platform="linux")
    recorder = Recorder()
    outcome = install(done.path, seams=seams(recorder, typed=lambda _p: done.manifest.id))
    assert outcome.ok, outcome.lines
    assert ["systemctl", "--user", "enable", "--now", "poolhouse-llama-cpp-ensure.timer"] in recorder.ran


def test_one_role_twice_is_refused(tmp_path, user):
    done = staged(tmp_path, roles=("llama-cpp-ensure",), platform="linux")
    raw = done.manifest.to_dict()
    raw["roles"] = raw["roles"] * 2
    with pytest.raises(ManifestError):
        parse(raw)
