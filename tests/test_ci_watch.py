"""scripts/ci_watch.py against a fake gh on PATH, a real git repository and a real node.

The fake gh answers `run list`, `run watch` and `run view` from a scenario file and logs its calls.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import ci_watch
import pytest
from land_support import DEV, Project

pytest_plugins = ["node_kit"]

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_watch.py"
FAKE_GH = r'''#!__PYTHON__
import json, os, pathlib, sys, time
base = pathlib.Path(os.environ["FAKE_GH_DIR"])
args = sys.argv[1:]
with open(base / "calls.log", "a") as f:
    f.write(" ".join(args) + "\n")
scenario = json.loads((base / "scenario.json").read_text())
if args[:2] == ["run", "list"]:
    if scenario.get("list_error"):
        sys.stderr.write(scenario["list_error"] + "\n")
        sys.exit(1)
    print(json.dumps(scenario["runs"]))
elif args[:2] == ["run", "watch"]:
    time.sleep(scenario.get("watch_sleep", 0))
    print("watching")
elif args[:2] == ["run", "view"] and "--log-failed" in args:
    print(scenario["log"])
elif args[:2] == ["run", "view"]:
    print(json.dumps(scenario["view"]))
else:
    sys.stderr.write("fake gh: unexpected " + " ".join(args) + "\n")
    sys.exit(2)
'''
LOG = "\n".join([
    "gates\tchecks\t2026-10-19T10:00:00.0000000Z all fine",
    "test (ubuntu-latest, 3.13)\ttests\t2026-10-19T10:01:00.0000000Z =========== short test summary info ===========",
    "test (ubuntu-latest, 3.13)\ttests\t2026-10-19T10:01:00.1000000Z FAILED tests/test_alpha.py::test_one - AssertionError: boom",
    "test (ubuntu-latest, 3.13)\ttests\t2026-10-19T10:01:00.2000000Z ERROR tests/test_beta.py::test_two - RuntimeError",
    "test (ubuntu-latest, 3.13)\ttests\t2026-10-19T10:01:00.3000000Z FAILED tests/test_alpha.py::test_one - AssertionError: boom",
    "test (ubuntu-latest, 3.13)\ttests\t2026-10-19T10:01:00.4000000Z 2 failed, 1998 passed in 600.00s",
])
JOBS = [{"name": "gates", "conclusion": "success"}, {"name": "test (ubuntu-latest, 3.13)", "conclusion": "failure"}]
FAST = ("--wait-s", "2", "--poll-s", "1")


class Rig:
    """A git project, a bin folder with git and a fake gh, and a real node with the session that pushes."""

    def __init__(self, tmp_path: Path, node) -> None:
        self.node = node
        self.base = tmp_path / "rig"
        self.base.mkdir()
        (self.base / "git").mkdir()
        self.proj = Project(self.base / "git")
        self.pusher = node.member("pusher")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        (self.bin / "git").symlink_to(shutil.which("git") or "git")
        self.state = Path(self.proj.root / ".git" / "ci-watch")
        self.status_url = "http://127.0.0.1:1/summary.json"
        self.scenario(runs=[])

    def install_gh(self) -> None:
        """Put the fake gh on this rig's PATH."""
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.replace("__PYTHON__", sys.executable), encoding="utf-8")
        gh.chmod(0o755)

    def scenario(self, **data) -> None:
        """Replace what the fake gh answers."""
        (self.base / "scenario.json").write_text(json.dumps(data), encoding="utf-8")

    def commit(self, name: str) -> str:
        """A new commit on the development branch; its id."""
        return self.proj.advance(f"src/poolhouse/{name}.py", f"VALUE = '{name}'\n")

    def run_for(self, sha: str, conclusion: str, created: str, status: str = "completed") -> dict:
        """The `run list` row of the push of ``sha``."""
        return {"databaseId": int(sha[:6], 16), "status": status, "conclusion": conclusion, "createdAt": created,
                "headSha": sha, "event": "push", "url": f"https://github.invalid/runs/{sha[:6]}"}

    def answer(self, sha: str, conclusion: str, created: str, status: str = "completed") -> None:
        """The fake gh reports one `ci` run of ``sha`` that ended with ``conclusion``."""
        row = self.run_for(sha, conclusion, created, status)
        done = {**row, "status": "completed", "jobs": JOBS if conclusion == "failure" else JOBS[:1]}
        self.scenario(runs=[row], view=done, log=LOG)

    def going(self, sha: str, jobs: list[dict], age_s: float = 0.0, watch_sleep: float = 5.0) -> None:
        """The fake gh reports a run of ``sha`` that began ``age_s`` ago and never ends; its `run watch` hangs ``watch_sleep`` seconds."""
        began = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_s))
        row = self.run_for(sha, "", began, "in_progress")
        view = {**row, "jobs": [{"startedAt": began, **job} for job in jobs]}
        self.scenario(runs=[row], view=view, log="", watch_sleep=watch_sleep)

    def env(self, who=None) -> dict[str, str]:
        """The environment of a watcher: this node, the pusher (or nobody) and a PATH of this rig's bin folder alone."""
        return self.node.env(who, {"PATH": str(self.bin), "FAKE_GH_DIR": str(self.base),
                                   "POOLHOUSE_GITHUB_STATUS_URL": self.status_url})

    def watch(self, sha: str, *extra: str, who="pusher") -> tuple[int, str]:
        """Run ``scripts/ci_watch.py`` for ``sha`` as the pusher (by default); the exit status and the output."""
        member = self.pusher if who == "pusher" else who
        done = subprocess.run([sys.executable, str(SCRIPT), sha, "--dev", DEV, *FAST, *extra], cwd=self.proj.root,
                              env=self.env(member), capture_output=True, text=True, check=False, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def calls(self) -> list[str]:
        """The gh calls so far."""
        log = self.base / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def tasks(self) -> list[str]:
        """The bodies of the ci task messages the pusher received."""
        found = self.node.session(self.pusher).read_all(kind="message")
        return [e.text for e in found if str(e.fields.get("subject", "")).startswith("ci red")]

    def stuck(self) -> list[str]:
        """The bodies of the stuck-run messages the pusher received."""
        found = self.node.session(self.pusher).read_all(kind="message")
        return [e.text for e in found if str(e.fields.get("subject", "")).startswith("ci stuck")]

    def blocked(self) -> list[str]:
        """The `blocked` announcements."""
        found = self.node.session(self.pusher).read_all(channel="#announcements")
        return [e.text for e in found if e.fields.get("type") == "blocked"]


@pytest.fixture
def rig(tmp_path, workspace_node):
    return Rig(tmp_path, workspace_node)


def test_a_green_run_posts_nothing(rig):
    rig.install_gh()
    sha = rig.commit("green")
    rig.answer(sha, "success", "2026-10-19T10:00:00Z")
    code, out = rig.watch(sha)
    assert code == 0 and f"ci green for {sha[:12]}" in out, out
    assert rig.tasks() == [] and rig.blocked() == []
    assert not any("--log-failed" in c for c in rig.calls())


def test_a_red_run_posts_one_message_naming_the_job_and_the_failing_tests(rig):
    rig.install_gh()
    sha = rig.commit("red")
    rig.answer(sha, "failure", "2026-10-19T10:00:00Z")
    code, out = rig.watch(sha)
    assert code == 0 and f"ci red for {sha[:12]}" in out, out
    (body,) = rig.tasks()
    assert sha[:12] in body and "test (ubuntu-latest, 3.13)" in body, body
    assert "tests/test_alpha.py::test_one, tests/test_beta.py::test_two" in body, body
    assert body.count("test_one") == 1
    assert rig.blocked() == []


def test_a_run_still_going_is_watched_until_it_ends(rig):
    rig.install_gh()
    sha = rig.commit("going")
    rig.answer(sha, "failure", "2026-10-19T10:00:00Z", status="in_progress")
    code, out = rig.watch(sha)
    assert code == 0, out
    assert any(c.startswith("run watch") for c in rig.calls()), rig.calls()
    assert len(rig.tasks()) == 1


def test_a_missing_gh_is_one_plain_line_and_exit_zero(rig):
    sha = rig.commit("nogh")
    code, out = rig.watch(sha)
    assert code == 0 and "gh is not installed" in out, out
    assert len(out.strip().splitlines()) == 1 and rig.tasks() == []


@pytest.mark.parametrize("said", ["To get started with GitHub CLI, please run:  gh auth login",
                                  "HTTP 403: Actions has been disabled for this repository"])
def test_a_gh_that_cannot_answer_is_one_plain_line_and_exit_zero(rig, said):
    rig.install_gh()
    sha = rig.commit("noauth")
    rig.scenario(list_error=said)
    code, out = rig.watch(sha)
    assert code == 0 and said in out, out
    assert len(out.strip().splitlines()) == 1 and rig.tasks() == []


def test_a_push_that_starts_no_run_is_one_plain_line_and_exit_zero(rig):
    rig.install_gh()
    sha = rig.commit("quiet")
    rig.scenario(runs=[])
    code, out = rig.watch(sha)
    assert code == 0 and "no `ci` run" in out, out
    assert rig.tasks() == []


def test_a_second_call_for_the_same_commit_gives_the_first_result_and_posts_nothing(rig):
    rig.install_gh()
    sha = rig.commit("twice")
    rig.answer(sha, "failure", "2026-10-19T10:00:00Z")
    first = rig.watch(sha)
    calls = rig.calls()
    second = rig.watch(sha)
    assert first == second and first[0] == 0, (first, second)
    assert rig.calls() == calls, "the second call asked GitHub again"
    assert len(rig.tasks()) == 1


def test_a_red_run_the_board_did_not_take_is_told_by_the_next_call(rig):
    rig.install_gh()
    sha = rig.commit("untold")
    rig.answer(sha, "failure", "2026-10-19T10:00:00Z")
    code, out = rig.watch(sha, who=None)
    assert code == 1 and "the board did not take the message" in out, out
    assert rig.tasks() == []
    code, out = rig.watch(sha)
    assert code == 0 and len(rig.tasks()) == 1, out
    assert rig.watch(sha)[0] == 0 and len(rig.tasks()) == 1


def test_a_failure_that_stays_red_for_three_pushes_is_announced_as_blocked_once(rig):
    rig.install_gh()
    shas = [rig.commit(f"streak{n}") for n in range(4)]
    for n, sha in enumerate(shas[:3]):
        rig.answer(sha, "failure", f"2026-10-19T10:0{n}:00Z")
        assert rig.watch(sha)[0] == 0
        assert len(rig.blocked()) == (1 if n == 2 else 0), n
    assert len(rig.tasks()) == 3
    assert "tests/test_alpha.py::test_one" in rig.blocked()[0] and shas[2][:12] in rig.blocked()[0]
    rig.answer(shas[3], "failure", "2026-10-19T10:03:00Z")
    assert rig.watch(shas[3])[0] == 0
    assert len(rig.blocked()) == 1 and len(rig.tasks()) == 4


def test_a_green_run_or_a_cancelled_one_decides_what_counts_as_the_streak(rig):
    rig.install_gh()
    shas = [rig.commit(f"mixed{n}") for n in range(5)]
    for n, (sha, result) in enumerate(zip(shas, ["failure", "success", "failure", "cancelled", "failure"], strict=True)):
        rig.answer(sha, result, f"2026-10-19T10:0{n}:00Z")
        assert rig.watch(sha)[0] == 0
    assert len(rig.tasks()) == 3
    assert rig.blocked() == [], "a green run between the failures ended the streak at two"


def test_the_watch_outlasts_the_longest_job_of_the_workflow():
    longest = max(int(m) for m in re.findall(r"timeout-minutes: (\d+)", (ROOT / ".github/workflows/ci.yml").read_text()))
    assert longest * 60 <= ci_watch.RUN_TIMEOUT_S


SHARDS = [{"name": "shard 1 on Python 3.13", "status": "completed", "conclusion": "success"},
          {"name": "shard 3 on Python 3.13", "status": "in_progress", "conclusion": ""},
          {"name": "shard 4 on Python 3.13", "status": "queued", "conclusion": ""}]


def test_a_run_still_going_at_the_first_mark_names_the_jobs_not_finished_and_their_age(rig):
    rig.install_gh()
    sha = rig.commit("stuck1")
    rig.going(sha, SHARDS, age_s=0)
    code, out = rig.watch(sha, "--warn-s", "1", "--block-s", "600")
    assert code == 0, out
    (body,) = rig.stuck()
    assert sha[:12] in body and "shard 3 on Python 3.13" in body and "shard 4 on Python 3.13" in body, body
    assert "shard 1 on Python 3.13" not in body and "in_progress" in body and "queued" in body, body
    assert rig.blocked() == []


def test_a_run_still_going_at_the_second_mark_is_also_announced_as_blocked(rig):
    rig.install_gh()
    sha = rig.commit("stuck2")
    rig.going(sha, SHARDS, age_s=0, watch_sleep=6)
    rig.watch(sha, "--warn-s", "1", "--block-s", "3")
    assert len(rig.stuck()) == 2
    (line,) = rig.blocked()
    assert sha[:12] in line and "shard 3 on Python 3.13" in line and len(line) <= 200, line


def test_an_open_actions_incident_leaves_out_the_blocked_announcement(rig):
    summary = {"status": {"description": "Partial System Outage"}, "components": [],
               "incidents": [{"name": "Delayed Actions runs", "components": [{"name": "Actions"}]}]}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(summary).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rig.status_url = f"http://127.0.0.1:{server.server_port}/summary.json"
    try:
        rig.install_gh()
        sha = rig.commit("stuck-incident")
        rig.going(sha, SHARDS, age_s=0, watch_sleep=6)
        rig.watch(sha, "--warn-s", "1", "--block-s", "3")
    finally:
        server.shutdown()
    assert len(rig.stuck()) == 2 and rig.blocked() == []


def test_a_stuck_run_is_told_once_per_commit_and_mark(rig):
    rig.install_gh()
    sha = rig.commit("stuck3")
    rig.going(sha, SHARDS, age_s=0, watch_sleep=6)
    rig.watch(sha, "--warn-s", "1", "--block-s", "3")
    rig.watch(sha, "--warn-s", "1", "--block-s", "3")
    assert len(rig.stuck()) == 2 and len(rig.blocked()) == 1


def test_a_run_with_no_job_started_says_it_is_waiting_for_the_one_before(rig):
    rig.install_gh()
    sha = rig.commit("stuck4")
    rig.going(sha, [], age_s=0)
    rig.watch(sha, "--warn-s", "1", "--block-s", "600")
    (body,) = rig.stuck()
    assert "no job has started" in body, body


def test_a_run_that_ends_before_the_first_mark_posts_no_stuck_message(rig):
    rig.install_gh()
    sha = rig.commit("quick")
    rig.answer(sha, "success", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    code, out = rig.watch(sha, "--warn-s", "600", "--block-s", "1200")
    assert code == 0 and rig.stuck() == [] and rig.blocked() == [], out


def test_the_marks_are_twenty_five_and_thirty_five_minutes():
    assert (ci_watch.WARN_S, ci_watch.BLOCK_S) == (25 * 60, 35 * 60)


def load_pushed():
    """The pushed hook as a module."""
    loader = importlib.machinery.SourceFileLoader("pushed_hook", str(ROOT / "scripts/hooks/pushed"))
    spec = importlib.util.spec_from_loader("pushed_hook", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def wait_for_record(state: Path, sha: str, seconds: float = 60.0) -> dict:
    """The record the detached watcher writes for ``sha``."""
    end = time.monotonic() + seconds
    path = state / f"{sha}.json"
    while time.monotonic() < end:
        if path.exists():
            return json.loads(path.read_text())
        time.sleep(0.1)
    pytest.fail(f"no record for {sha[:12]} in {state}: {sorted(p.name for p in state.glob('*'))}")


def test_a_push_of_the_development_branch_starts_a_detached_watcher(rig, monkeypatch):
    rig.install_gh()
    sha = rig.commit("hook")
    rig.answer(sha, "success", "2026-10-19T10:00:00Z")
    monkeypatch.setattr("poolhouse.lan.on_the_persons_machine", lambda: True)
    for name, value in rig.env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(rig.proj.root)
    hook = load_pushed()
    assert hook.main(DEV, [f"refs/heads/{DEV} {sha} refs/heads/{DEV} {'0' * 40}"]) == 0
    record = wait_for_record(rig.state, sha)
    assert record["outcome"] == "green" and record["sha"] == sha


def test_a_push_of_another_branch_starts_no_watcher(rig, monkeypatch):
    rig.install_gh()
    sha = rig.commit("other")
    monkeypatch.setattr("poolhouse.lan.on_the_persons_machine", lambda: True)
    monkeypatch.chdir(rig.proj.root)
    assert load_pushed().main(DEV, [f"refs/heads/work {sha} refs/heads/work {'0' * 40}"]) == 0
    assert not rig.state.exists() or not list(rig.state.glob("*.log"))


def test_the_watcher_starts_only_on_the_persons_machine(rig, monkeypatch):
    rig.install_gh()
    sha = rig.commit("gated")
    monkeypatch.setenv("PATH", str(rig.bin))
    assert ci_watch.start(sha, DEV, rig.proj.root) is False, "a test run is not the person's machine"
    assert not rig.state.exists() or not list(rig.state.glob("*.log"))


def test_the_watcher_says_so_at_the_push_when_gh_is_not_installed(rig, monkeypatch, capsys):
    sha = rig.commit("nogh-start")
    monkeypatch.setattr("poolhouse.lan.on_the_persons_machine", lambda: True)
    monkeypatch.setenv("PATH", str(rig.bin))
    assert ci_watch.start(sha, DEV, rig.proj.root) is False
    assert "gh is not installed" in capsys.readouterr().err


def test_a_watched_environment_carries_no_git_redirects(rig, monkeypatch):
    rig.install_gh()
    sha = rig.commit("env")
    rig.answer(sha, "success", "2026-10-19T10:00:00Z")
    monkeypatch.setattr("poolhouse.lan.on_the_persons_machine", lambda: True)
    for name, value in rig.env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GIT_DIR", str(rig.base / "nowhere"))
    assert ci_watch.start(sha, DEV, rig.proj.root) is True
    assert wait_for_record(rig.state, sha)["outcome"] == "green"
