"""LIST: its statuses are the runner's, and it runs confined whoever wrote the project."""
from __future__ import annotations

import json
import socket
import sys

import pytest
from testfarm_project import SHELL_CONFIG, project, shell_project, with_list

import poolhouse as ph
from poolhouse.testfarm import cli
from poolhouse.testfarm.core import project as core
from poolhouse.testfarm.model import exitcodes


def check(root, capsys) -> tuple[int, str, str]:
    status = cli.COMMAND.run(["check", "--root", str(root)])
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_a_list_that_fails_or_prints_nothing_is_status_4(tmp_path, capsys):
    quick = SHELL_CONFIG.replace("timeout = 30", "timeout = 1")
    banner = quick.replace("[list]", "[list]\nstarts = 'tests/'")
    cases = [("echo tests/a.sh; echo tests/b.sh; echo boom >&2; exit 3", quick, "LIST exited with status 3: boom"),
             ("exit 0", quick, "LIST printed no unit"), ("exit 7", quick, "LIST exited with status 7 and printed nothing on its error output"),
             ("echo tests/a.sh; kill -9 $$", quick, "LIST exited with status"),
             ("echo '== banner =='; echo 3 passed", banner, "LIST printed no unit (2 lines were ignored"),
             ("echo tests/a.sh; sleep 30", quick, "LIST ran past its timeout of 1 s"), ("exec nothing-here-at-all", quick, "LIST exited with status")]
    for index, (script, config, sentence) in enumerate(cases):
        status, out, err = check(project(tmp_path / str(index), config, {"tests/list.sh": script}), capsys)
        assert status == exitcodes.REFUSED == 4, (script, out, err)
        assert sentence in err, (script, err)
        assert "units:" not in out


def test_the_error_line_of_a_failing_list_is_plain_text(tmp_path, capsys):
    root = with_list(tmp_path, "printf '\\033[31mred\\033[0m \\033]0;title\\007done\\n' >&2; exit 2")
    status, _, err = check(root, capsys)
    assert status == 4 and "\x1b" not in err and "LIST exited with status 2: red done" in err


def test_a_list_may_print_nothing_when_the_project_says_so(tmp_path, capsys):
    root = with_list(tmp_path, "exit 0", "")
    (root / "testfarm.toml").write_text(SHELL_CONFIG.replace("[list]", "[list]\nallow_empty = true"))
    status, out, _ = check(root, capsys)
    assert status == 0 and "units: 0 from 0 ids" in out


def test_a_missing_file_is_status_4_with_the_way_to_make_one_and_no_units(tmp_path, capsys):
    root = project(tmp_path, None, {"tests/t_a.sh": "exit 0\n"})
    status, out, err = check(root, capsys)
    assert status == 4 and "poolhouse-test init" in err and "units" not in out


def test_check_prints_the_units_labels_steps_and_the_split_mode(tmp_path, capsys):
    config = SHELL_CONFIG + '\n[label.slow]\nmatch = ["tests/t_render.sh"]\n\n[deps]\nglobs = { "tests/*" = ["tests/*"] }\n'
    root = shell_project(tmp_path, config)
    status, out, _ = check(root, capsys)
    assert status == 0, out
    assert "units: 2 from 2 ids" in out and "labels: slow 1" in out and "deps: 2 of 2 units declare their files" in out
    assert "split: by name" in out and "steps: none" in out and "testfarm.toml: ok (schema 1, sha256 " in out and "project: widgets" in out


def test_a_list_runs_confined_whoever_wrote_it(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("CANARY-READ")
    script = (f"cat {outside} > /dev/null 2>&1 && echo read/yes || echo read/no\n"
              "echo planted > tests/planted.txt 2> /dev/null && echo write/yes || echo write/no\n"
              "echo planted > \"$HOME/planted.txt\" 2> /dev/null && echo scratch/writable || echo scratch/denied\n")
    root = with_list(tmp_path / "p", script)
    assert [unit.name for unit in ph.test.units(root)] == ["read/no", "scratch/writable", "write/no"]
    assert not (root / "tests" / "planted.txt").exists()


def test_a_list_has_no_network_and_an_allowlist_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("WIDGETS_SECRET", "hunter2")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(5)
        script = ("import os, socket, sys\n"
                  f"try:\n    socket.create_connection(('127.0.0.1', {server.getsockname()[1]}), 2)\n    print('net/connected')\n"
                  "except OSError:\n    print('net/denied')\n"
                  "print('env/' + ('leaked' if 'WIDGETS_SECRET' in os.environ or 'CLAUDECODE' in os.environ else 'clean'))\n"
                  "print('home/' + ('scratch' if 'poolhouse-testfarm-' in os.environ['HOME'] else 'real'))\n")
        root = project(tmp_path, SHELL_CONFIG.replace('run = ["sh", "tests/list.sh"]', f"run = [{json.dumps(sys.executable)}, \"tests/list.py\"]"),
                       {"tests/list.py": script})
        names = [unit.name for unit in ph.test.units(root)]
    assert names == ["env/clean", "home/scratch", "net/denied"]


def test_a_list_that_loops_is_ended_at_its_timeout_not_waited_for(tmp_path):
    root = with_list(tmp_path, "while :; do :; done")
    (root / "testfarm.toml").write_text(SHELL_CONFIG.replace("timeout = 30", "timeout = 1"))
    with pytest.raises(ph.Error, match=r"ran past its timeout of 1 s"):
        ph.test.units(root)


def test_a_project_with_only_margins_and_no_list_timeout_is_not_run_unguarded(tmp_path):
    config = SHELL_CONFIG.replace("timeout = 30\n", "").replace("timeout = 60\n", "") + "\n[schedule]\nsilence_margin = 3\ntotal_margin = 4\n"
    root = project(tmp_path, config, {"tests/list.sh": "echo tests/a.sh\n"})
    assert ph.test.config(root).list.timeout is None
    with pytest.raises(ph.Error, match=r"\[list\] timeout is not set"):
        ph.test.units(root)
    assert core.CONFIG_NAME == "testfarm.toml"
