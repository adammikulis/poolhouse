"""A failing test or build prints no environment and no credential, and still says what failed and shows the build output.

Every value here is a canary made for the run, and the inner run is given only the few variables named in ``PASSED``, so a failure of
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

ROOT = Path(__file__).resolve().parents[1]
PASSED = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "POOLHOUSE_HOME", "POOLHOUSE_CACHE", "PYTHONUSERBASE",
          "DEV_TEST_SLOTS_DIR")
CREDENTIAL = "AAA_CANARY_MESSAGING_TOKEN"
SETTING = "AAB_CANARY_SETTING"
"""A variable no name marks as a credential. Both names sort first, so pytest's shortened repr of an environment would show them."""

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


@pytest.mark.parametrize("asked", [[], ["-vv", "--tb=long", "--showlocals", "--full-trace"]], ids=["default", "everything"])
def test_a_failing_run_shows_what_failed_and_neither_the_environment_nor_a_credential(tmp_path, asked):
    credential, setting = (f"canary-{secrets.token_hex(12)}" for _ in range(2))
    (tmp_path / "test_failing.py").write_text(FAILING, encoding="utf-8")
    environment = {name: os.environ[name] for name in PASSED if name in os.environ}
    environment.update({CREDENTIAL: credential, SETTING: setting,
                        "PYTHONPATH": os.pathsep.join(str(ROOT / name) for name in ("src", "scripts", "tests"))})
    done = subprocess.run([sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"), "--rootdir", str(tmp_path),
                           "--confcutdir", str(tmp_path), "-p", "conftest", "-p", "no:cacheprovider", "-p", "no:langsmith_plugin",
                           "-n", "0", *asked, str(tmp_path / "test_failing.py")],
                          cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=100, check=False)
    output = done.stdout + done.stderr
    assert done.returncode == 1 and "4 failed" in output, output
    assert credential not in output, "the run showed the credential:\n" + output
    assert setting not in output, "the run showed the environment:\n" + output
    for shown in ("test_a_native_build_fails", "exited 101", "error[E0425]: not found in the staged crate", "CalledProcessError",
                  "returned non-zero exit status 3", "AAA_NEVER_SET", "child holds None", "after the child"):
        assert shown in output, output


def test_a_failed_node_build_shows_the_compiler_output_and_its_compiler_holds_no_credential(tmp_path, monkeypatch):
    credential = f"canary-{secrets.token_hex(12)}"
    monkeypatch.setenv(CREDENTIAL, credential)
    (tmp_path / "build").write_text(f"import os, sys\nprint('compiler holds', os.environ.get('{CREDENTIAL}'), file=sys.stderr)\n"
                                    "raise SystemExit(101)\n", encoding="utf-8")
    with pytest.raises(testnode.BuildError) as failed:
        testnode.compile_node(tmp_path, tmp_path / "target", sys.executable)   # runs the file `build` of the folder as cargo's command
    assert credential not in str(failed.value), "the build's output showed the credential"
    assert "cargo build -p poolhouse-node failed" in str(failed.value) and "compiler holds None" in str(failed.value)
