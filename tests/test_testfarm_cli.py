"""poolhouse-test init, check and list as a person runs them."""
from __future__ import annotations

import json
import subprocess
import sys

from testfarm_project import SHELL_CONFIG, project, shell_project

from poolhouse.testfarm import cli
from poolhouse.testfarm.core import project as project_core


def run(*args, capsys):
    status = cli.COMMAND.run(list(args))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_init_writes_a_file_to_fill_in_and_never_overwrites_one(tmp_path, capsys):
    root = project(tmp_path, None)
    status, out, _ = run("init", "--root", str(root), capsys=capsys)
    written = (root / "testfarm.toml").read_text()
    assert status == 0 and "schema = 1" in written and "poolhouse-test check" in out
    (root / "testfarm.toml").write_text("schema = 1\n# mine\n")
    status, _, err = run("init", "--root", str(root), capsys=capsys)
    assert status == 4 and "is left as it is" in err and (root / "testfarm.toml").read_text() == "schema = 1\n# mine\n"


def test_the_file_init_writes_is_valid_and_check_names_what_is_missing_from_the_project(tmp_path, capsys):
    root = project(tmp_path, None)
    run("init", "--root", str(root), capsys=capsys)
    status, out, err = run("check", "--root", str(root), capsys=capsys)
    assert status == 4 and "LIST exited with status" in err and "units" not in out


def test_list_prints_units_narrowed_by_selectors_and_as_json(tmp_path, capsys):
    root = shell_project(tmp_path, SHELL_CONFIG + '\n[set.first]\nids = ["tests/t_parse.sh"]\n', tests=("parse", "render", "emit"))
    _, out, _ = run("list", "--root", str(root), capsys=capsys)
    assert out.splitlines() == ["tests/t_emit.sh", "tests/t_parse.sh", "tests/t_render.sh"]
    _, out, _ = run("list", "--root", str(root), "@first", capsys=capsys)
    assert out.splitlines() == ["tests/t_parse.sh"]
    _, out, _ = run("list", "--root", str(root), "tests/t_r*", "--json", capsys=capsys)
    assert [unit["name"] for unit in json.loads(out)] == ["tests/t_render.sh"]
    status, _, err = run("list", "--root", str(root), "nothing", capsys=capsys)
    assert status == 4 and "matches no unit" in err


def test_the_command_runs_as_a_module_and_its_help_names_the_three_commands(tmp_path):
    root = shell_project(tmp_path)
    help_text = subprocess.run([sys.executable, "-m", "poolhouse.testfarm.cli", "--help"], capture_output=True, text=True, timeout=120)
    assert help_text.returncode == 0 and all(word in help_text.stdout for word in ("init", "check", "list"))
    done = subprocess.run([sys.executable, "-m", "poolhouse.testfarm.cli", "check", "--root", str(root)], capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0 and "units: 2 from 2 ids" in done.stdout, done.stderr


def test_the_root_is_the_first_directory_up_with_a_file_or_a_git_entry_and_the_walk_stops_there(tmp_path, capsys):
    root = shell_project(tmp_path)
    deep = root / "tests" / "nested"
    deep.mkdir()
    assert project_core.find_root(deep) == root.resolve()
    status, out, _ = run("check", "--root", str(deep), capsys=capsys)
    assert status == 0 and "units: 2 from 2 ids" in out
    repo = tmp_path / "other"
    (repo / ".git").mkdir(parents=True)
    below = repo / "app" / "src"
    below.mkdir(parents=True)
    assert project_core.find_root(below) == repo.resolve()
    status, _, err = run("check", "--root", str(below), capsys=capsys)
    assert status == 4 and "poolhouse-test init" in err
    (repo / "testfarm.toml").write_text("schema = 1\n")
    assert project_core.find_root(below) == repo.resolve()
    (repo / "app" / ".git").mkdir()
    assert project_core.find_root(below) == (repo / "app").resolve(), "the walk stops at the nearer .git and never climbs into the parent"
