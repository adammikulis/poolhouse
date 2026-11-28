"""Who `poolhouse.home` hands the account's own state and cache roots to: the installed software and the repository's declared entry
points, never a test run, a process one started, or Poolhouse run from a checkout, whatever folder the asking file is in.

Every program here runs in a real process of its own with a scratch folder standing for the account (a ``sitecustomize`` points the
password database and ``HOME`` at it), so nothing of the real account is opened whichever way the code answers.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import home

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

STAND_IN = """
import os, sys, types
account = {account!r}
if os.name == "posix":
    import pwd
    asked = pwd.getpwuid
    pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_dir=account, pw_name=asked(uid).pw_name)
"""

PROBE = """
from poolhouse import home

def ask(name, call):
    try:
        print(name, "RESOLVED", call())
    except Exception as why:
        print(name, type(why).__name__, why)

ask("home", home.home)
ask("state", lambda: home.state("authority.json"))
ask("cache", lambda: home.cache("logs"))
ask("moved", lambda: home.moved("fit.json"))
"""
ASKED = ("home", "state", "cache", "moved")


class Account:
    """A scratch folder standing for the account, and processes that see it as theirs."""

    def __init__(self, base: Path) -> None:
        self.base = base.resolve()
        self.folder = self.base / "account"
        self.folder.mkdir()
        self.hook = self.base / "hook"
        self.hook.mkdir()
        (self.hook / "sitecustomize.py").write_text(STAND_IN.format(account=str(self.folder)))

    def env(self, *path: Path, **more: str) -> dict[str, str]:
        """An environment holding nothing of this test run: only the way to the code and the scratch account."""
        kept = {name: os.environ[name] for name in ("SYSTEMROOT", "PATH") if name in os.environ}
        return {**kept, "HOME": str(self.folder), "USERPROFILE": str(self.folder), "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(str(p) for p in (self.hook, *path)), **more}

    def run(self, env: dict[str, str], *words: str) -> str:
        done = subprocess.run([sys.executable, *words], env=env, cwd=self.base, capture_output=True, text=True, timeout=120, check=False)
        assert not done.returncode, done.stdout + done.stderr
        return done.stdout

    def script(self, at: Path, text: str) -> str:
        at.parent.mkdir(parents=True, exist_ok=True)
        at.write_text(text)
        return str(at)


@pytest.fixture
def account(tmp_path):
    return Account(tmp_path)


def copied(into: Path) -> Path:
    """A copy of the package under ``into``, as an install or another checkout holds it."""
    shutil.copytree(SRC / "poolhouse", into / "poolhouse", ignore=shutil.ignore_patterns("__pycache__"))
    return into


def answers(said: str) -> dict[str, str]:
    return {line.split()[0]: line.split()[1] for line in said.splitlines() if line.split() and line.split()[0] in ASKED}


def refused(said: str) -> bool:
    return answers(said) == dict.fromkeys(ASKED, "RealRootRefused")


def resolved(said: str) -> bool:
    return answers(said) == dict.fromkeys(ASKED, "RESOLVED")


def test_a_plain_script_on_the_checkout_is_refused_the_accounts_roots_and_given_scratch_ones(account):
    probe = account.script(account.base / "scratch" / "probe.py", PROBE)
    said = account.run(account.env(SRC), probe)
    assert refused(said), said
    scratch = account.env(SRC, POOLHOUSE_HOME=str(account.base / "s"), POOLHOUSE_CACHE=str(account.base / "c"))
    assert resolved(account.run(scratch, probe))


@pytest.mark.parametrize("spelling", ["named", "above", "override", "link"])
def test_no_spelling_of_the_accounts_root_gets_past(account, spelling):
    real = account.folder / ".poolhouse"
    real.mkdir()
    link = account.base / "link"
    link.symlink_to(real, target_is_directory=True)
    named, ask = {"named": ({"POOLHOUSE_HOME": str(real)}, 'home.state("x")'),
                  "above": ({"POOLHOUSE_HOME": str(account.folder)}, 'home.state(".poolhouse", "x")'),
                  "override": ({"POOLHOUSE_HOME": str(account.base / "s"), "POOLHOUSE_JOBS_HOME": str(real / "jobs")}, 'home.state("jobs")'),
                  "link": ({"POOLHOUSE_HOME": str(link)}, 'home.state("x")')}[spelling]
    probe = account.script(account.base / "probe.py", f"from poolhouse import home\ntry:\n    print({ask})\n"
                                                      "except home.RealRootRefused:\n    print('REFUSED')\n")
    assert account.run(account.env(SRC, **named), probe).strip() == "REFUSED"


def test_a_test_file_outside_tests_with_no_conftest_is_refused_and_so_is_a_child_that_lost_every_variable(account):
    child = account.script(account.base / "elsewhere" / "child.py", PROBE)
    probe = account.script(account.base / "elsewhere" / "test_probe.py", f"""
import subprocess, sys
{PROBE}
def test_probe():
    bare = {{"PATH": "/usr/bin:/bin", "HOME": {str(account.folder)!r}, "PYTHONPATH": {os.pathsep.join((str(account.hook), str(SRC)))!r}}}
    print("CHILD")
    print(subprocess.run([sys.executable, {child!r}], env=bare, capture_output=True, text=True).stdout)
""")
    (account.base / "elsewhere" / "pytest.ini").write_text("[pytest]\n")
    said = account.run(account.env(SRC), "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", "-c",
                       str(account.base / "elsewhere" / "pytest.ini"), probe)
    own, spawned = said.split("CHILD")
    assert refused(own), said
    assert refused(spawned), said


def test_the_installed_copy_reaches_the_accounts_roots_unless_a_test_started_it(account):
    installed = copied(account.base / "venv" / "site-packages")
    probe = account.script(account.base / "probe.py", PROBE)
    assert resolved(account.run(account.env(installed), probe))
    assert refused(account.run(account.env(installed, POOLHOUSE_TEST_PROCESS="1"), probe))


def test_a_test_run_hands_the_mark_to_the_processes_it_starts():
    mark = os.environ.get("POOLHOUSE_TEST_PROCESS")   # a local, so a failure prints the mark and not the environment
    assert mark == "1"


def test_only_a_declared_program_of_the_checkout_run_as_itself_gets_the_accounts_roots(account):
    checkout = account.base / "checkout"
    copied(checkout / "src")
    declared = f"import sys\nsys.path.insert(0, {str(checkout / 'src')!r})\nPOOLHOUSE_ENTRY_POINT = 'a probe of the grant'\n{PROBE}"
    program = account.script(checkout / "scripts" / "tool.py", declared)
    clean = account.env()
    assert resolved(account.run(clean, program))
    assert refused(account.run({**clean, "POOLHOUSE_TEST_PROCESS": "1"}, program)), "a test started it"
    assert refused(account.run({**clean, "PYTEST_CURRENT_TEST": "x"}, program)), "a test started it"
    homeless = {name: value for name, value in clean.items() if name not in ("HOME", "USERPROFILE")}
    if os.name == "posix":
        assert refused(account.run(homeless, program)), "its environment was thrown away"
    outside = account.script(account.base / "scratch" / "tool.py", declared)
    assert refused(account.run(clean, outside)), "the declaration counts only in the repository's own folders"
    importer = account.script(account.base / "importer.py", f"import runpy\nrunpy.run_path({program!r}, run_name='tool')\n")
    assert refused(account.run(clean, importer)), "importing a program grants nothing"
    undeclared = account.script(checkout / "scripts" / "other.py", declared.replace("POOLHOUSE_ENTRY_POINT", "SOMETHING_ELSE"))
    assert refused(account.run(clean, undeclared))


def test_a_development_linked_install_is_refused_like_a_checkout_however_it_is_started(account):
    checkout = account.base / "checkout"
    source = copied(checkout / "src")
    (source / "poolhouse" / "probe_here.py").write_text(PROBE)
    found = account.base / "found" / "poolhouse-0.0.dist-info"
    found.mkdir(parents=True)
    (found / "METADATA").write_text("Metadata-Version: 2.1\nName: poolhouse\nVersion: 0.0\n")
    (found / "direct_url.json").write_text(json.dumps({"dir_info": {"editable": True}, "url": checkout.as_uri()}))
    with (account.hook / "sitecustomize.py").open("a") as stand_in:
        stand_in.write(f"sys.path[:0] = [{str(source)!r}, {str(found.parent)!r}]\n")
    as_a_module = account.run(account.env(), "-m", "poolhouse.probe_here")
    assert refused(as_a_module), as_a_module
    probe = account.script(account.base / "probe.py", PROBE)
    assert refused(account.run(account.env(), probe))
    assert resolved(account.run(account.env(POOLHOUSE_HOME=str(account.base / "s"), POOLHOUSE_CACHE=str(account.base / "c")),
                                "-m", "poolhouse.probe_here")), "scratch folders still work"


@pytest.mark.parametrize("variable", ["POOLHOUSE_HOME", "POOLHOUSE_CACHE"])
def test_this_test_run_cannot_point_itself_at_the_accounts_roots(monkeypatch, variable):
    root = home.account_roots()[variable == "POOLHOUSE_CACHE"]
    monkeypatch.setenv(variable, str(root))
    ask = home.cache if variable == "POOLHOUSE_CACHE" else home.state
    with pytest.raises(home.RealRootRefused, match="test run"):
        ask("anything")


def test_a_real_model_test_may_name_downloaded_files_and_nothing_else():
    state, cache = home.account_roots()
    assert home.machine_asset("llama.cpp", "current", "llama-server") == state / "llama.cpp" / "current" / "llama-server"
    assert home.machine_asset("guard", "classifier") == cache / "guard" / "classifier"
    for parts in (("keystore",), ("node", "node.sock"), ("llama.cpp", "..", "keystore"), ("llama.cpp", "/etc"), ()):
        with pytest.raises(home.RealRootRefused):
            home.machine_asset(*parts)


def test_a_file_outside_tests_is_given_no_downloaded_file(account):
    probe = account.script(account.base / "probe.py", "from poolhouse import home\ntry:\n    print(home.machine_asset('tools'))\n"
                                                      "except home.RealRootRefused:\n    print('REFUSED')\n")
    assert account.run(account.env(SRC), probe).strip() == "REFUSED"


GRANTED = {
    "examples/rbmk/__main__.py", "examples/rbmk/run.py",
    "scripts/agent-identity", "scripts/agent-sandbox", "scripts/board_tally.py", "scripts/ci_watch.py",
    "scripts/experiments/destructive_eval.py", "scripts/experiments/iq_vs_kquant.py", "scripts/github-protection-apply",
    "scripts/guard-eval", "scripts/hooks/claude-harness-bind", "scripts/hooks/claude-lead-attention",
    "scripts/hooks/claude-session-end", "scripts/hooks/claude-session-start", "scripts/hooks/claude-subagent-start",
    "scripts/hooks/claude-tool-milestones", "scripts/hooks/harness-ancestry", "scripts/hooks/pushed", "scripts/lead-review",
    "scripts/pool-join-check", "scripts/release-key", "scripts/review-backlog", "scripts/test", "scripts/testslots.py",
    "src/poolhouse/bench/__main__.py", "src/poolhouse/device_setup.py", "src/poolhouse/hook_diagnostics.py",
    "src/poolhouse/runtime_cli.py", "src/poolhouse/workspace/release_check.py",
}
"""Every program that may use the account's roots from a checkout. Adding one is a decision made here, where a review sees it."""


def declares(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
        if home.ENTRY_POINT not in text:
            return False
        tree = ast.parse(text)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return False
    return any(isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == home.ENTRY_POINT for t in node.targets)
               for node in tree.body)


def test_the_programs_granted_the_accounts_roots_are_these_and_no_others():
    tracked = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=REPO, capture_output=True, text=True, check=True).stdout.split("\0")
    assert {name for name in tracked if name and declares(REPO / name)} == GRANTED


def test_listing_the_chat_tools_for_the_coverage_check_opens_nothing_of_the_account(account):
    probe = account.script(account.base / "probe.py", f"""
import sys
sys.path[:0] = [{str(REPO / "scripts")!r}, {str(SRC)!r}]
import redteam_coverage
found = {{}}
redteam_coverage.find_tools(found)
print("TOOLS", len(found) > 0)
""")
    assert account.run(account.env(SRC), probe).splitlines()[-1] == "TOOLS True"
    assert not (account.folder / ".poolhouse").exists()
