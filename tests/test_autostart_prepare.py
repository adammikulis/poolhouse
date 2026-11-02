"""`prepare` stages unit files and a manifest and installs nothing; the manifest is strict about what it holds."""

from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path

import pytest
from autostart_support import environment, launchers, staged, write_launcher

from poolhouse import home, runtime_identity
from poolhouse.files import sha256_file
from poolhouse.fleet import autostart, autostart_manifest as manifest_mod
from poolhouse.fleet.autostart_manifest import ManifestError, launcher_problem, parse
from poolhouse.fleet.autostart_prepare import PrepareError, Spec, prepare


@pytest.fixture
def user(tmp_path, monkeypatch):
    return environment(tmp_path, monkeypatch)


@pytest.fixture
def poolhouse():
    """A Mac that has its Poolhouse executable (a file stands in: units only name it)."""
    path = runtime_identity.executable_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    return path


def test_prepare_stages_files_and_installs_nothing(tmp_path, user):
    done = staged(tmp_path, platform="darwin", agent={"agent": "claude-code"})
    folder = done.path.parent
    assert folder.is_relative_to(home.state("autostart", "staging"))
    assert not (user / "Library").exists()
    raw = json.loads(done.path.read_text())
    assert raw["version"] == 1
    assert raw["preparer"] == {"agent": "claude-code"}
    assert raw["expires"] - raw["created"] == 24 * 3600
    assert raw["platform"] == "darwin" and raw["device"] == home.device_id()
    for role in raw["roles"]:
        for unit in role["units"]:
            assert sha256_file(folder / unit["name"]) == unit["sha256"]
            assert unit["destination"] == str(user / "Library" / "LaunchAgents" / unit["name"])
        assert Path(role["argv"][0]).is_absolute()
        assert Path(role["argv"][0]).is_relative_to(home.state("bin"))
        assert Path(role["workdir"]).is_relative_to(home.state())
        assert set(role["environment"]) <= set(manifest_mod.ENV_ALLOWED)
    assert oct(folder.stat().st_mode & 0o777) == "0o700"
    assert done.command.endswith(f"install --manifest {done.path}")
    assert done.command.count("\n") == 0


def test_the_manifest_carries_no_secret_from_the_environment(tmp_path, user, monkeypatch):
    for name in ("HF_TOKEN", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "POOLHOUSE_TOKEN"):
        monkeypatch.setenv(name, f"secret-{name}")
    done = staged(tmp_path, platform="linux")
    for path in done.path.parent.iterdir():
        text = path.read_text()
        assert "secret-" not in text, path.name


def test_each_platform_renders_a_restart_policy_and_the_argv_list(tmp_path, user, poolhouse):
    mac = staged(tmp_path, platform="darwin").manifest
    pool = next(r for r in mac.roles if r.role == "pool-daemon")
    plist = plistlib.loads((staged_dir(mac) / pool.units[0].name).read_bytes())
    assert plist["ProgramArguments"] == runtime_identity.launch_argv(list(pool.argv))
    assert plist["ProgramArguments"][0] == str(runtime_identity.executable_path()) and plist["ProgramArguments"][1:3] == ["-I", "-S"]
    assert plist["ProgramArguments"][3:] == list(pool.argv), "macOS names the unit after the program it runs, which is Poolhouse"
    assert plist["KeepAlive"] == {"SuccessfulExit": False} and plist["ThrottleInterval"] == 30
    assert plist["RunAtLoad"] is True and "UserName" not in plist
    ensure = next(r for r in mac.roles if r.role == "runtime-ensure")
    timed = plistlib.loads((staged_dir(mac) / ensure.units[0].name).read_bytes())
    assert timed["StartInterval"] == 3600 and timed["RunAtLoad"] is True and "KeepAlive" not in timed

    nix = staged(tmp_path, platform="linux").manifest
    units = {u.name: (staged_dir(nix) / u.name).read_text() for r in nix.roles for u in r.units}
    service = units["poolhouse-traind.service"]
    assert "Restart=on-failure" in service and "RestartSec=30" in service
    assert "StartLimitBurst=5" in service and "NoNewPrivileges=yes" in service and "User=" not in service
    assert "OnUnitActiveSec=3600" in units["poolhouse-runtime-ensure.timer"]
    assert "Type=oneshot" in units["poolhouse-runtime-ensure.service"]

    win = staged(tmp_path, platform="win32").manifest
    xml = (staged_dir(win) / "com.poolhouse.traind.xml").read_bytes().decode("utf-16")
    for tag in ("<LogonTrigger>", "<RestartOnFailure>", "<Exec>", "<RunLevel>LeastPrivilege</RunLevel>"):
        assert tag in xml


def staged_dir(manifest) -> Path:
    return home.state("autostart", "staging", manifest.id)


def test_unattended_ensure_takes_only_its_own_arguments(tmp_path, user):
    done = staged(tmp_path, roles=("runtime-ensure",), platform="linux")
    role = done.manifest.roles[0]
    assert role.argv[1:] == ("runtime", "ensure") and Path(role.argv[0]).name == "poolhouse"
    assert not {"POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_WORKSPACE_TOKEN"} & set(role.environment)


def test_arguments_with_spaces_quotes_and_percent_stay_single_argv_elements(tmp_path, user, poolhouse):
    odd = home.state("bin dir", "it's \"q\" $x %h")
    launchers(odd)
    done = prepare(Spec(("pool-daemon",), launchers=odd, platform="linux", report="a b;c"))
    argv = done.manifest.roles[0].argv
    assert argv[0] == str(odd / "poolhouse-traind") and argv[-1] == "--report=a b;c"
    text = (staged_dir(done.manifest) / "poolhouse-traind.service").read_text()
    line = next(row for row in text.splitlines() if row.startswith("ExecStart="))
    assert "%%h" in line and "$$x" in line and '\\"q\\"' in line and "%h" not in line.replace("%%h", "")
    plist = plistlib.loads((staged_dir(prepare(Spec(("pool-daemon",), launchers=odd, platform="darwin")).manifest)
                            / "com.poolhouse.traind.plist").read_bytes())
    assert plist["ProgramArguments"][3] == str(odd / "poolhouse-traind")


@pytest.mark.parametrize("bad", [{"labels": ("a b",)}, {"labels": ("x;rm -rf",)},
                                  {"report": "two\nlines"}, {"slots": 0}, {"slots": 99}])
def test_prepare_refuses_option_values_that_are_not_plain(tmp_path, user, bad):
    with pytest.raises(PrepareError):
        prepare(Spec(("pool-daemon",), launchers=launchers(home.state("bin")), platform="linux", **bad))


def test_a_path_with_a_newline_cannot_be_prepared(tmp_path, user):
    odd = home.state("bin", "line\nbreak")
    launchers(odd)
    with pytest.raises(ValueError):
        prepare(Spec(("pool-daemon",), launchers=odd, platform="linux"))
    assert not home.state("autostart", "staging").exists()


def test_the_exec_target_must_be_a_launcher_the_runtime_tooling_wrote(tmp_path, user):
    bin_dir = home.state("bin")
    launchers(bin_dir)
    target = bin_dir / "poolhouse-traind"
    assert launcher_problem(target) == ""
    target.write_text("#!/bin/sh\nexec python -m poolhouse.cli.daemon\n")
    assert "not a runtime launcher" in launcher_problem(target)
    write_launcher(bin_dir, "poolhouse-traind", mode=0o777)
    assert "writable by others" in launcher_problem(target)
    target.unlink()
    assert "missing" in launcher_problem(target)
    target.symlink_to(bin_dir / "poolhouse-runtime")
    assert "not a regular file" in launcher_problem(target)
    with pytest.raises(PrepareError):
        prepare(Spec(("pool-daemon",), launchers=bin_dir, platform="linux"))


def test_a_launcher_inside_a_checkout_or_outside_the_runtime_directory_is_refused(tmp_path, user):
    checkout = home.state("work")
    (checkout / ".git").mkdir(parents=True)
    path = write_launcher(checkout / "bin", "poolhouse-traind")
    assert "source checkout" in launcher_problem(path)
    odd = write_launcher(home.state("bin"), "poolhouse-traind")
    odd.write_text(odd.read_text().replace(str(home.state("runtimes")), "/opt/elsewhere"))
    assert "outside" in launcher_problem(odd)


def test_the_launcher_the_runtime_tooling_writes_is_accepted(tmp_path, user):
    from poolhouse import runtime

    path = home.state("bin") / "poolhouse"
    path.parent.mkdir(parents=True)
    path.write_text(runtime.LAUNCHER.format(root=str(runtime.directory()), python="/x/bin/python",
                                            arguments=repr(["-m", "poolhouse.cli"]), name="poolhouse", follow_selection=False))
    path.chmod(0o700)
    assert launcher_problem(path) == ""


def test_prepare_needs_the_launcher_directory_named(tmp_path, user):
    with pytest.raises(PrepareError, match="--launchers"):
        prepare(Spec(("pool-daemon",), platform="linux"))


def test_a_relative_launcher_directory_is_refused(tmp_path, user):
    assert "absolute" in launcher_problem(Path("poolhouse-traind"))


@pytest.mark.parametrize("roles", [(), ("landing",), ("pool-daemon", "bogus")])
def test_there_is_no_role_for_landing_or_anything_unnamed(tmp_path, user, roles):
    with pytest.raises(PrepareError):
        prepare(Spec(roles, launchers=launchers(home.state("bin")), platform="linux"))


def test_system_scope_is_not_offered_on_windows(tmp_path, user):
    with pytest.raises(PrepareError):
        prepare(Spec(("pool-daemon",), scope="system", launchers=launchers(home.state("bin")), platform="win32"))


def test_the_parser_refuses_what_the_preparer_never_writes(tmp_path, user):
    raw = staged(tmp_path, platform="linux").manifest.to_dict()
    for change in ({"version": 2}, {"id": "x"}, {"expires": raw["created"] + 90000}, {"scope": "root"},
                   {"roles": []}, {"platform": "plan9"}):
        with pytest.raises(ManifestError):
            parse({**raw, **change})
    role = raw["roles"][0]
    for change in ({"role": "landing"}, {"environment": {"HF_TOKEN": "x"}}, {"argv": "poolhouse-traind"},
                   {"units": []}):
        with pytest.raises(ManifestError):
            parse({**raw, "roles": [{**role, **change}]})
    assert parse(raw).id == raw["id"]


def test_the_daemon_port_in_the_health_probe_is_the_daemon_default():
    from poolhouse.fleet import daemon

    assert manifest_mod.DAEMON_PORT == daemon.DEFAULT_PORT


def test_a_mac_unit_is_not_prepared_while_the_installed_mac_has_no_signed_executable(tmp_path, user, monkeypatch):
    staged(tmp_path, platform="darwin")  # a machine that is not the installed software renders the unit and asks nothing
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    with pytest.raises(PrepareError, match="no signed executable yet"):
        staged(tmp_path, platform="darwin")
    assert staged(tmp_path, platform="linux").manifest.platform == "linux", "no other platform asks for it"


def test_the_login_plist_of_the_older_install_path_also_starts_the_launcher_under_poolhouse(tmp_path, user, monkeypatch, poolhouse):
    done = type("Done", (), {"returncode": 0, "stderr": ""})()
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: done)
    launcher = write_launcher(home.state("bin"), "poolhouse-traind")
    result = autostart._mac_install("login", [str(launcher), "--slots", "2"], tmp_path / "logs")
    plist = plistlib.loads(result.path.read_bytes())
    assert plist["ProgramArguments"] == [str(runtime_identity.executable_path()), "-I", "-S", str(launcher), "--slots", "2"]
    python_form = autostart._mac_install("login", [sys.executable, "-m", "poolhouse.cli.daemon"], tmp_path / "logs")
    assert plistlib.loads(python_form.path.read_bytes())["ProgramArguments"][0] == sys.executable, "the runtime's own python is already Poolhouse"


def test_the_older_install_path_installs_nothing_on_an_installed_mac_that_has_no_signed_executable(tmp_path, user, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart.wsl_startup, "guest", lambda: False)
    monkeypatch.setattr(autostart, "uninstall", lambda: [])
    monkeypatch.setattr(autostart, "_runs", lambda argv: True)
    monkeypatch.setattr(autostart, "_mac_install", lambda *a: pytest.fail("a unit that cannot start was installed"))
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    result = autostart.install("login", log_dir=tmp_path / "logs")
    assert not result.installed and "no signed executable yet" in result.note


def test_a_mac_with_no_poolhouse_executable_keeps_starting_its_units_as_it_always_did(tmp_path, user):
    mac = staged(tmp_path, roles=("pool-daemon",), platform="darwin").manifest
    plist = plistlib.loads((staged_dir(mac) / mac.roles[0].units[0].name).read_bytes())
    assert plist["ProgramArguments"] == list(mac.roles[0].argv), "a unit is never pointed at an executable nothing built"
