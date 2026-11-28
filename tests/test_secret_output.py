"""A failing test or build prints no credential and, unless its command line asks, no environment; it still says what failed and
shows the build output.

Every value here is a canary made for the run, and an inner run is given only the few variables named in ``PASSED``, so a failure of
these tests shows nothing the machine holds.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import native_cargo  # noqa: F401  (the inner run imports it, so a change to it runs this file again)
import pytest
import testnode
import testscope
import testsecrets
from test_activity_gate import (  # noqa: F401  (a fixture, and the runner loaded as a module)
    repo,
    script,
)

from poolhouse import caller

ROOT = Path(__file__).resolve().parents[1]
PASSED = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "POOLHOUSE_HOME", "POOLHOUSE_CACHE", "PYTHONUSERBASE",
          "DEV_TEST_SLOTS_DIR")
CREDENTIAL = "AAA_CANARY_MESSAGING_TOKEN"
SETTING = "AAB_CANARY_SETTING"
"""A variable nothing marks as a credential. Both names sort first, so pytest's shortened repr of an environment would show them."""

FAILING = '''
import os
import subprocess
import sys

from native_cargo import NodeUnits


def test_a_native_build_fails(tmp_path):
    compiler = "import sys; print('error[E0425]: not found in the staged crate', file=sys.stderr); raise SystemExit(101)"
    NodeUnits.__new__(NodeUnits).build_output([sys.executable, "-c", compiler], tmp_path, dict(os.environ))


def test_a_subprocess_error():
    subprocess.run([sys.executable, "-c", "raise SystemExit(3)"], env=dict(os.environ), check=True)


def test_an_assertion_about_the_environment():
    assert "AAA_NEVER_SET" in os.environ


def test_output_captured_from_a_child():
    subprocess.run([sys.executable, "-c", "import os; print('child holds', os.environ.get('AAA_CANARY_MESSAGING_TOKEN'))"], check=True)
    raise AssertionError("after the child")
'''

LISTENER = '''
import pytest


@pytest.mark.lan_network
def test_marked_for_the_network_and_touching_nothing():
    pass
'''


def inner_run(folder: Path, source: str, given: dict[str, str], *asked: str) -> subprocess.CompletedProcess:
    """Run ``source`` as a test file under this repository's configuration and ``conftest.py``, with ``given`` beside ``PASSED``."""
    (folder / "test_inner.py").write_text(source, encoding="utf-8")
    environment = {name: os.environ[name] for name in PASSED if name in os.environ}
    environment.update(given, PYTHONPATH=os.pathsep.join(str(ROOT / name) for name in ("src", "scripts", "tests")))
    return subprocess.run([sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"), "--rootdir", str(folder),
                           "--confcutdir", str(folder), "-p", "conftest", "-p", "no:cacheprovider", "-p", "no:langsmith_plugin",
                           "-n", "0", *asked, str(folder / "test_inner.py")],
                          cwd=folder, env=environment, capture_output=True, text=True, timeout=100, check=False)


def canaries() -> tuple[str, str]:
    return f"canary-{secrets.token_hex(12)}", f"canary-{secrets.token_hex(12)}"


@pytest.mark.parametrize(("asked", "arguments_shown"), [([], False), (["-vv", "--tb=long", "--showlocals", "--full-trace"], True)],
                         ids=["default", "everything"])
def test_a_failing_run_shows_what_failed_no_credential_and_the_environment_only_when_asked(tmp_path, asked, arguments_shown):
    credential, setting = canaries()
    done = inner_run(tmp_path, FAILING, {CREDENTIAL: credential, SETTING: setting}, *asked)
    output = done.stdout + done.stderr
    assert done.returncode == 1 and "4 failed" in output, output
    assert credential not in output, "the run showed the credential:\n" + output
    assert (setting in output) is arguments_shown, f"arguments shown should be {arguments_shown}:\n{output}"
    for shown in ("test_a_native_build_fails", "exited 101", "error[E0425]: not found in the staged crate", "CalledProcessError",
                  "returned non-zero exit status 3", "AAA_NEVER_SET", "child holds None", "after the child"):
        assert shown in output, output


def test_a_run_without_the_terminal_reporter_reports_its_failures_without_the_environment(tmp_path):
    credential, setting = canaries()
    report = tmp_path / "report.xml"
    done = inner_run(tmp_path, FAILING, {CREDENTIAL: credential, SETTING: setting}, "-p", "no:terminal", f"--junitxml={report}")
    assert done.returncode == 1, done.stdout + done.stderr
    written = report.read_text(encoding="utf-8")
    assert "exited 101" in written and credential not in written and setting not in written, written


@pytest.mark.parametrize("marker", ["CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID"])
def test_a_session_that_marks_an_agent_still_keeps_it_from_the_network_tests(tmp_path, marker):
    done = inner_run(tmp_path, LISTENER, {marker: "canary-session", "POOLHOUSE_LAN_TESTS": "1"}, "-rs")
    assert done.returncode == 0 and "1 skipped" in done.stdout and "an agent never runs" in done.stdout, done.stdout + done.stderr


def test_the_runner_starts_pytest_without_credentials_and_with_the_agent_markers(repo, monkeypatch, capsys):  # noqa: F811
    runner = script()
    runner.ROOT = repo
    started = {}

    def supervise(command, workers, *, env):
        started.update(env)
        return 1

    monkeypatch.setattr(runner.testslots_runner, "run_pytest", supervise)
    credential, _ = canaries()
    markers = dict.fromkeys((*caller.AGENT_MARKERS, *testscope.AGENT_MARKERS), "canary-session")
    given = {**{name: os.environ[name] for name in PASSED if name in os.environ}, **markers, CREDENTIAL: credential,
             "POOLHOUSE_WORKSPACE_TOKEN": credential}
    assert runner.launch(runner.pytest_command(1, "test_a.py"), given) == 1
    Path(capsys.readouterr().out.split("test: evidence ", 1)[1].strip()).unlink(missing_ok=True)
    assert credential not in started.values(), sorted(name for name, value in started.items() if value == credential)
    assert {name: started.get(name) for name in markers} == markers
    assert given[CREDENTIAL] == credential, "the caller's own environment is untouched"


def test_a_credential_is_known_by_its_name_or_by_the_password_in_its_address():
    held = {"X_GH_PAT": "canary", "X_SIGNING_KEY": "canary", "X_PASSPHRASE": "canary", "X_PWD": "canary",
            "X_DATABASE_URL": "postgres://user:canary@db.invalid/x", "X_BROKER_URI": "redis://:canary@broker.invalid",
            "X_SENTRY_DSN": "https://user:canary@errors.invalid/1"}
    plain = {"PWD": "/work", "OLDPWD": "/before", "X_PATH": "/tools", "GIT_CONFIG_KEY_0": "core.hooksPath",
             "X_SERVICE_URL": "http://host.invalid:8000/a@b", "X_REPOSITORY_URL": "ssh://git@host.invalid/x",
             "HTTP_PROXY": "http://proxy.invalid:3128", "NO_PROXY": "localhost,127.0.0.1"}
    proxy = {"HTTPS_PROXY": "http://user:canary@proxy.invalid:3128", "ALL_PROXY": "user:canary@proxy.invalid:3128"}
    assert testsecrets.build_environment({**held, **plain, **proxy}) == {**plain, **proxy}, "a build needs its proxy"
    assert testsecrets.without_credentials({**held, **plain, **proxy}) == plain, "a test run reaches no network, so it holds none"


def test_a_failed_node_build_shows_the_compiler_output_and_its_compiler_holds_no_credential(tmp_path, monkeypatch):
    credential, _ = canaries()
    monkeypatch.setenv(CREDENTIAL, credential)
    (tmp_path / "build").write_text(f"import os, sys\nprint('compiler holds', os.environ.get('{CREDENTIAL}'), file=sys.stderr)\n"
                                    "raise SystemExit(101)\n", encoding="utf-8")
    with pytest.raises(testnode.BuildError) as failed:
        testnode.compile_node(tmp_path, tmp_path / "target", sys.executable)   # runs the file `build` of the folder as cargo's command
    assert credential not in str(failed.value), "the build's output showed the credential"
    assert "cargo build -p poolhouse-node failed" in str(failed.value) and "compiler holds None" in str(failed.value)
