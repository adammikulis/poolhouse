"""``poolhouse <word>`` runs ``poolhouse-<word>``: the ``git foo`` -> ``git-foo`` pattern."""

from __future__ import annotations

import importlib
import sys
import tomllib
import types
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from poolhouse import cli

declarations = importlib.import_module("poolhouse.cli.registry")

REPO = Path(__file__).resolve().parent.parent


def _fake_target(monkeypatch, name: str, calls: list, *, returns: int = 0,
                 about: str = "Counts quinces.") -> str:
    """A module on ``sys.modules`` whose ``main`` records its args; returns its ``module:attr``."""
    module = types.ModuleType(name)

    def main(argv=None):
        argv = list(argv or [])
        if argv == ["--help"]:
            print(f"usage: poolhouse-{name} [-h]\n\n{about}\n\noptions:\n  -h, --help")
            raise SystemExit(0)
        calls.append((name, argv))
        return returns

    module.main = main
    monkeypatch.setitem(sys.modules, name, module)
    return f"{name}:main"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Three invented declarations in the package's installed metadata."""
    calls: list = []
    points = [
        EntryPoint("poolhouse-quince", _fake_target(monkeypatch, "quince", calls, returns=3),
                   "console_scripts"),
        EntryPoint("poolhouse-medlar", _fake_target(monkeypatch, "medlar", calls, about="Trains."),
                   "console_scripts"),
        EntryPoint("poolhouse-medlar-run",
                   _fake_target(monkeypatch, "medlar_run", calls, about="Runs one."),
                   "console_scripts"),
        EntryPoint("poolhouse", "poolhouse.cli:main", "console_scripts"),
        EntryPoint("unrelated-tool", "quince:main", "console_scripts"),
    ]
    monkeypatch.setattr(declarations, "distribution", lambda name: types.SimpleNamespace(entry_points=points))
    monkeypatch.setattr(cli, "PYPROJECT", tmp_path / "absent.toml")
    return calls


def test_a_word_runs_the_hyphenated_command_in_process(fake):
    assert cli.main(["quince", "--ripe", "2"]) == 3
    assert fake == [("quince", ["--ripe", "2"])]


def test_words_join_with_hyphens_longest_match_first(fake):
    assert cli.main(["medlar", "run", "--steps", "1"]) == 0
    assert cli.main(["medlar", "--dry-run"]) == 0
    assert fake == [("medlar_run", ["--steps", "1"]), ("medlar", ["--dry-run"])]


def test_the_targets_help_is_returned_unchanged(fake, capsys):
    with pytest.raises(SystemExit) as left:
        cli.main(["quince", "--help"])
    assert left.value.code == 0
    assert capsys.readouterr().out.startswith("usage: poolhouse-quince")


def test_an_unknown_word_exits_2_naming_the_nearest(fake, capsys):
    assert cli.main(["quinc"]) == 2
    err = capsys.readouterr().err
    assert "quinc" in err and "quince" in err
    assert fake == []


def test_list_prints_every_command_with_the_first_line_of_its_help(fake, capsys):
    assert cli.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "quince" in out and "Installed command" in out
    assert "medlar run" in out
    assert "unrelated" not in out
    assert fake == []


def test_help_lists_the_subcommands(fake, capsys):
    with pytest.raises(SystemExit) as left:
        cli.main(["--help"])
    assert left.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    for word in ("quince", "medlar", "medlar run"):
        assert word in out


def test_bare_and_its_flags_reach_the_app(fake, monkeypatch):
    seen: list = []
    from poolhouse.cli.daemon import main as daemon_main

    def app(argv, **services):
        assert services["daemon_main"] is daemon_main
        return seen.append(argv) or 0

    monkeypatch.setattr("poolhouse.fleet.launch.main", app)
    assert cli.main([]) == 0
    assert cli.main(["--port", "8771", "--no-browser"]) == 0
    assert seen == [[], ["--port", "8771", "--no-browser"]]
    assert fake == []


def test_an_uninstalled_checkout_dispatches_from_its_pyproject(monkeypatch, tmp_path):
    calls: list = []
    target = _fake_target(monkeypatch, "quince", calls)
    (tmp_path / "pyproject.toml").write_text(
        f'[project.scripts]\npoolhouse = "poolhouse.cli:main"\npoolhouse-quince = "{target}"\n')
    monkeypatch.setattr(declarations, "distribution", lambda name: types.SimpleNamespace(entry_points=[]))
    monkeypatch.setattr(cli, "PYPROJECT", tmp_path / "pyproject.toml")
    assert cli.main(["quince", "x"]) == 0
    assert calls == [("quince", ["x"])]


def test_a_checkout_beats_a_stale_installed_entry_point(monkeypatch, tmp_path):
    calls: list = []
    target = _fake_target(monkeypatch, "quince", calls)
    (tmp_path / "pyproject.toml").write_text(
        f'[project.scripts]\npoolhouse-quince = "{target}"\n')
    monkeypatch.setattr(declarations, "distribution", lambda name: types.SimpleNamespace(entry_points=[
        EntryPoint("poolhouse-quince", "poolhouse.gone.quince:main", "console_scripts")]))
    monkeypatch.setattr(cli, "PYPROJECT", tmp_path / "pyproject.toml")
    assert cli.commands()["quince"] == target
    assert cli.main(["quince", "x"]) == 0
    assert calls == [("quince", ["x"])]


def test_every_command_in_pyproject_resolves_to_a_main():
    table = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    words = cli.commands()
    for name, target in table.items():
        if not name.startswith(cli.PREFIX):
            continue
        word = name[len(cli.PREFIX):]
        assert word in words, f"{name} is not a subcommand of poolhouse"
        assert callable(cli.load(words[word])), f"{name} -> {target} has no main"
    assert "serve" in words and "chat" in words and "open" in words


def test_help_lists_every_command_and_hands_a_named_one_its_own_help(capsys):
    from poolhouse import cli

    assert cli.help_main([]) == 0
    out = capsys.readouterr().out
    assert "open" in out and "chat" in out and "usage: poolhouse help" in out and "asks itself" not in out
    code = cli.help_main(["open"])
    assert code == 0 and "usage: poolhouse open" in capsys.readouterr().out
    assert cli.main(["help", "chat"]) == 0
    assert "usage: poolhouse chat" in capsys.readouterr().out
    assert cli.help_main(["benc"]) == 2
    assert "not a command" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.help_main(["--help"])


def test_pool_is_the_public_device_group_command(monkeypatch):
    monkeypatch.setattr(cli, "PYPROJECT", REPO / "pyproject.toml")
    registered = cli.commands()
    assert registered["pool"] == "poolhouse.fleet.join:main"
    assert "fleet" not in registered


def test_runtime_is_listed_and_has_the_same_named_help(capsys):
    assert cli.help_main(["runtime"]) == 0
    assert "usage: poolhouse runtime" in capsys.readouterr().out
    assert cli.commands()["runtime"] == "poolhouse.runtime_cli:main"
    assert cli.main(["--list"]) == 0
    assert "runtime" in capsys.readouterr().out
    assert cli.help_main(["runtime", "status"]) == 0
    assert "usage: poolhouse runtime status" in capsys.readouterr().out


@pytest.mark.parametrize("args", (["--help"], ["--list"], ["--version"], ["help"]))
def test_root_information_never_loads_a_command(args, monkeypatch, capsys):
    def forbidden(target):
        if target == "poolhouse.cli:help_main":
            return cli.help_main
        pytest.fail(f"information loaded {target}")

    monkeypatch.setattr(cli, "load", forbidden)
    try:
        result = cli.main(args)
    except SystemExit as left:
        result = left.code
    assert result == 0
    assert capsys.readouterr().out


def test_importing_the_umbrella_keeps_runtime_and_app_handlers_unloaded(tmp_path):
    import subprocess

    result = subprocess.run([sys.executable, "-c", "import sys; import poolhouse.cli; "
                             "assert not {'poolhouse.runtime_cli', 'poolhouse.cli.daemon', "
                             "'poolhouse.fleet.launch'} & sys.modules.keys()"],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_version_reports_package_version_without_starting_the_app(monkeypatch, capsys):
    monkeypatch.setattr(cli, "version", lambda package: "0.0.0.dev7")
    with pytest.raises(SystemExit) as left:
        cli.main(["--version"])
    assert left.value.code == 0
    assert capsys.readouterr().out.strip() == "Poolhouse 0.0.0.dev7"


def test_internal_visibility_does_not_change_routing_or_return_values(monkeypatch, tmp_path, capsys):
    calls = []
    target = _fake_target(monkeypatch, "holder", calls, returns=7)
    project = tmp_path / "pyproject.toml"
    project.write_text(f'[project.scripts]\npoolhouse-traind = "{target}"\n')
    monkeypatch.setattr(cli, "PYPROJECT", project)
    assert cli.main(["--list"]) == 0
    assert "traind" not in capsys.readouterr().out
    assert cli.main(["--list", "--all"]) == 0
    assert "traind" in capsys.readouterr().out
    assert cli.help_main(["--all"]) == 0
    assert "traind" in capsys.readouterr().out
    assert cli.main(["traind", "--root", "scratch"]) == 7
    assert calls == [("holder", ["--root", "scratch"])]


@pytest.mark.parametrize("targets", (("one:main", "one:main"), ("one:main", "two:main")))
def test_duplicate_declared_commands_are_refused(monkeypatch, tmp_path, targets):
    points = [EntryPoint("poolhouse-quince", target, "console_scripts") for target in targets]
    monkeypatch.setattr(declarations, "distribution", lambda name: types.SimpleNamespace(entry_points=points))
    with pytest.raises(ValueError, match="duplicate"):
        declarations.registry(tmp_path / "absent")


def test_installed_registry_reads_only_the_poolhouse_distribution(monkeypatch, tmp_path):
    asked = []

    def owned(name):
        asked.append(name)
        return types.SimpleNamespace(entry_points=[EntryPoint("poolhouse-quince", "quince:main", "console_scripts")])

    monkeypatch.setattr(declarations, "distribution", owned)
    assert declarations.registry(tmp_path / "absent")["quince"].target == "quince:main"
    assert asked == ["poolhouse"]


def test_runtime_cannot_be_overridden_by_a_console_declaration(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project.scripts]\npoolhouse-runtime = "quince:main"\n')
    with pytest.raises(ValueError, match="reserved"):
        declarations.registry(project)


def test_group_usage_is_canonical_through_nested_commands():
    from poolhouse.command import Group

    group = Group("poolhouse-test-devices", "List devices.")
    group.add("show", lambda args: 0, help="Show one.")
    parser = group.parser()
    assert parser.prog == "poolhouse test devices"
    assert parser.parse_args(["show"]).cmd == "show"
    assert "poolhouse test devices" in parser.format_usage()
    assert Group("poolhouse.device_setup", "Set up.").parser().prog == "poolhouse device setup"


def test_unknown_routes_never_load_a_handler(monkeypatch, capsys):
    def forbidden(target):
        pytest.fail(f"unknown route loaded {target}")

    monkeypatch.setattr(cli, "load", forbidden)
    assert cli.main(["unlisted-internal-command"]) == 2
    assert "not a command" in capsys.readouterr().err


def test_all_without_listing_never_launches_the_app(monkeypatch):
    def forbidden(target):
        pytest.fail(f"unexpected handler {target}")

    monkeypatch.setattr(cli, "load", forbidden)
    with pytest.raises(SystemExit) as left:
        cli.main(["--all"])
    assert left.value.code == 2


def test_open_is_a_primary_command_and_help_never_requests_a_ticket(monkeypatch, capsys):
    from poolhouse.fleet import launch_open, members

    def forbidden():
        raise AssertionError("help or rejected old command requested a ticket")

    monkeypatch.setattr(launch_open, "ticket_url", forbidden)
    assert cli.commands()["open"] == "poolhouse.fleet.launch_open:main"
    assert cli.help_main(["open"]) == 0
    assert "poolhouse open" in capsys.readouterr().out
    with pytest.raises(SystemExit) as refused:
        members.main(["open", "--print"])
    assert refused.value.code == 2


def test_primary_open_print_is_denied_before_ticket_or_browser_work(monkeypatch):
    from poolhouse.fleet import launch_open
    from poolhouse.person import HumanRequired

    def denied(reason):
        raise HumanRequired(reason)

    def forbidden(*args):
        raise AssertionError("a denied caller reached ticket or browser work")

    monkeypatch.setattr(launch_open, "require_person", denied)
    monkeypatch.setattr(launch_open, "ticket_url", forbidden)
    monkeypatch.setattr(launch_open.webbrowser, "open", forbidden)
    assert cli.main(["open", "--print"]) == 2
