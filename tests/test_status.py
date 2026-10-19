"""scripts/status against real broker run records held by real processes and a fake gh on PATH."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "status"
FAKE_GH = r'''#!__PYTHON__
import json, os, pathlib, sys
base = pathlib.Path(os.environ["FAKE_GH_DIR"])
args = sys.argv[1:]
scenario = json.loads((base / "scenario.json").read_text())
if scenario.get("error"):
    sys.stderr.write(scenario["error"] + "\n")
    sys.exit(1)
if args[:2] == ["run", "list"]:
    print(json.dumps(scenario["runs"]))
elif args[:2] == ["run", "view"]:
    print(json.dumps({"jobs": scenario["jobs"]}))
else:
    sys.exit(2)
'''
HOLDER = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import testqueue
age, est, state, owner = float(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
record = testqueue.RunRecord(Path(sys.argv[2]), sys.argv[7], "interactive", None if est == "none" else float(est), 1)
now = time.time()
record.update(state=state, owner=owner, granted=1 if state == "running" else 0, enqueued=now - age,
              started=now - age if state == "running" else None)
print("ready", flush=True)
time.sleep(120)
'''


def stamp(age_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_s))


class Rig:
    """A broker folder, a bin folder with git and a fake gh, and the processes that hold run records."""

    def __init__(self, tmp_path: Path) -> None:
        self.base = tmp_path
        self.slots = tmp_path / "slots"
        self.slots.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        (self.bin / "git").symlink_to(shutil.which("git") or "git")
        self.holders: list[subprocess.Popen] = []
        self.status_url = "http://127.0.0.1:1/summary.json"
        self.gh_scenario(runs=[], jobs=[])

    def install_gh(self) -> None:
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.replace("__PYTHON__", sys.executable), encoding="utf-8")
        gh.chmod(0o755)

    def gh_scenario(self, **data) -> None:
        (self.base / "scenario.json").write_text(json.dumps(data), encoding="utf-8")

    def gh_run(self, age_s: float, status: str = "in_progress", number: int = 4242, workflow: str = "ci", sha: str = "a") -> dict:
        return {"databaseId": number, "status": status, "conclusion": "", "createdAt": stamp(age_s), "headSha": sha * 40,
                "workflowName": workflow, "url": f"https://github.invalid/runs/{number}"}

    def run_record(self, state: str, age_s: float, est: str = "100", owner: str = "claude-abc123", label: str = "pytest") -> None:
        """Start a process that holds a broker run record in ``state`` that began ``age_s`` ago."""
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT / "scripts"), str(self.slots), str(age_s), est, state, owner, label],
                                  stdout=subprocess.PIPE, text=True)
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        self.holders.append(holder)

    def status(self, *extra: str) -> tuple[int, list[str]]:
        env = {"PATH": str(self.bin), "DEV_TEST_SLOTS_DIR": str(self.slots), "FAKE_GH_DIR": str(self.base),
               "HOME": str(self.base), "POOLHOUSE_DEV_BRANCH": "0.3dev", "POOLHOUSE_GITHUB_STATUS_URL": self.status_url}
        done = subprocess.run([sys.executable, str(SCRIPT), *extra], cwd=ROOT, env=env, capture_output=True, text=True,
                              check=False, timeout=120)
        return done.returncode, (done.stdout + done.stderr).splitlines()

    def close(self) -> None:
        for holder in self.holders:
            holder.kill()
            holder.wait()


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    made.install_gh()
    yield made
    made.close()


def test_an_idle_machine_with_a_finished_run_is_ok(rig):
    rig.gh_scenario(runs=[{**rig.gh_run(3000, "completed"), "conclusion": "success"}], jobs=[])
    code, lines = rig.status()
    assert code == 0 and lines[0] == "ok", lines
    assert any(ln.startswith("broker") for ln in lines) and any(ln.startswith("github") for ln in lines)


def test_a_run_inside_its_estimate_is_listed_with_owner_estimate_and_age_and_is_ok(rig):
    rig.run_record("running", 30, est="600", owner="claude-abc123", label="tests/test_alpha.py")
    code, lines = rig.status()
    assert code == 0 and lines[0] == "ok", lines
    (row,) = [ln for ln in lines if "tests/test_alpha.py" in ln]
    assert "running" in row and "claude-abc123" in row and "est 600s" in row and "age 30s" in row, row


def test_a_local_run_over_its_estimate_by_five_minutes_is_stuck(rig):
    rig.run_record("running", 100 + 320, est="100", owner="claude-abc123", label="tests/test_slow.py")
    code, lines = rig.status()
    assert code == 0 and lines[0].startswith("STUCK:") and "tests/test_slow.py" in lines[0], lines


def test_a_local_run_without_an_estimate_is_stuck_after_twenty_minutes(rig):
    rig.run_record("running", 21 * 60, est="none", label="pytest")
    assert rig.status()[1][0].startswith("STUCK:")


def test_a_run_with_a_long_estimate_is_judged_by_its_estimate(rig):
    rig.run_record("running", 40 * 60, est=str(3 * 3600), label="full suite")
    assert rig.status()[1][0] == "ok"


def test_a_queued_run_over_five_minutes_with_nothing_running_is_stuck(rig):
    rig.run_record("queued", 6 * 60, label="tests/test_waiting.py")
    lines = rig.status()[1]
    assert lines[0].startswith("STUCK:") and "queued" in lines[0], lines


def test_a_queued_run_behind_a_running_one_is_ok(rig):
    rig.run_record("running", 30, est="600", label="tests/test_first.py")
    rig.run_record("queued", 6 * 60, label="tests/test_waiting.py")
    assert rig.status()[1][0] == "ok"


def test_a_github_run_over_twenty_five_minutes_is_stuck_and_its_unfinished_jobs_are_listed(rig):
    jobs = [{"name": "shard 1 on Python 3.13", "status": "completed", "conclusion": "success", "startedAt": stamp(1700)},
            {"name": "shard 3 on Python 3.13", "status": "in_progress", "conclusion": "", "startedAt": stamp(1600)}]
    rig.gh_scenario(runs=[rig.gh_run(26 * 60)], jobs=jobs)
    code, lines = rig.status()
    assert code == 0 and lines[0].startswith("STUCK:") and "4242" in lines[0], lines
    text = "\n".join(lines)
    assert "shard 3 on Python 3.13" in text and "in_progress" in text, text
    assert "1 job finished" in text, text


def test_a_github_run_inside_twenty_five_minutes_is_ok_and_shows_its_jobs(rig):
    jobs = [{"name": "gates", "status": "in_progress", "conclusion": "", "startedAt": stamp(120)}]
    rig.gh_scenario(runs=[rig.gh_run(5 * 60)], jobs=jobs)
    lines = rig.status()[1]
    assert lines[0] == "ok" and any("gates" in ln and "in_progress" in ln for ln in lines), lines


def test_a_queued_github_run_over_five_minutes_with_no_later_run_started_is_stuck(rig):
    rig.gh_scenario(runs=[rig.gh_run(13 * 60, "queued", 1, "pool-check", "b")], jobs=[])
    code, lines = rig.status()
    assert code == 0 and lines[0] == "STUCK: github: pool-check bbbbbbb queued 13 min. github reports: githubstatus.com did not answer (ServerUnreachable)", lines


def serving(summary: dict) -> HTTPServer:
    """A local server that answers every request with ``summary`` as JSON."""
    body = json.dumps(summary).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_an_open_actions_incident_makes_a_stuck_github_run_information(rig):
    rig.gh_scenario(runs=[rig.gh_run(18 * 60, "queued", 1, "ci", "b")], jobs=[])
    server = serving({"status": {"description": "Partial System Outage"}, "components": [],
                      "incidents": [{"name": "Delayed Actions runs", "components": [{"name": "Actions"}]}]})
    rig.status_url = f"http://127.0.0.1:{server.server_port}/summary.json"
    try:
        code, lines = rig.status()
    finally:
        server.shutdown()
    assert code == 0 and lines[0].startswith("ok (github degraded: Delayed Actions runs"), lines


def test_a_queued_github_run_inside_five_minutes_is_ok(rig):
    rig.gh_scenario(runs=[rig.gh_run(4 * 60, "queued", 1)], jobs=[])
    assert rig.status()[1][0] == "ok"


def test_a_queued_github_run_behind_a_started_run_of_the_same_workflow_says_so_and_is_ok_until_twenty_five_minutes(rig):
    rig.gh_scenario(runs=[rig.gh_run(10 * 60, "queued", 2), rig.gh_run(20 * 60, "in_progress", 1)], jobs=[])
    lines = rig.status()[1]
    assert lines[0] == "ok", lines
    assert any("queued" in ln and "waiting behind run 1" in ln for ln in lines), lines
    rig.gh_scenario(runs=[rig.gh_run(26 * 60, "queued", 2), rig.gh_run(40 * 60, "in_progress", 1)], jobs=[])
    assert rig.status()[1][0].startswith("STUCK: github:"), "a run past 25 minutes is flagged whatever it waits behind"


def test_a_queued_github_run_is_judged_against_runs_of_its_own_workflow(rig):
    rig.gh_scenario(runs=[rig.gh_run(10 * 60, "queued", 2, "pool-check"), rig.gh_run(20 * 60, "in_progress", 1, "ci")], jobs=[])
    assert rig.status()[1][0].startswith("STUCK: github: pool-check")


def test_a_queued_github_run_is_not_flagged_when_a_later_run_of_its_workflow_has_started(rig):
    rig.gh_scenario(runs=[rig.gh_run(2 * 60, "in_progress", 2), rig.gh_run(10 * 60, "queued", 1, sha="c")], jobs=[])
    assert rig.status()[1][0] == "ok"


def test_every_queued_github_run_is_judged_not_only_the_first_few_listed(rig):
    runs = [rig.gh_run(60 + n, "in_progress", 100 + n, f"w{n}") for n in range(6)] + [rig.gh_run(9 * 60, "queued", 1, "late")]
    rig.gh_scenario(runs=runs, jobs=[])
    assert rig.status()[1][0].startswith("STUCK: github: late"), "the queued run is the seventh active row"


@pytest.mark.parametrize("said", ["To get started with GitHub CLI, please run:  gh auth login", "HTTP 403: Actions has been disabled"])
def test_a_gh_that_cannot_answer_is_one_line_and_not_an_error(rig, said):
    rig.gh_scenario(error=said)
    code, lines = rig.status()
    assert code == 0 and lines[0] == "ok", lines
    assert len([ln for ln in lines if ln.startswith("github")]) == 1 and said in "\n".join(lines)


def test_a_missing_gh_is_one_line_and_not_an_error(rig):
    (rig.bin / "gh").unlink()
    code, lines = rig.status()
    assert code == 0 and lines[0] == "ok", lines
    assert [ln for ln in lines if ln.startswith("github")] == ["github: gh is not installed"]


def test_the_verdict_option_prints_only_the_first_line(rig):
    rig.run_record("running", 100 + 320, est="100", label="tests/test_slow.py")
    code, lines = rig.status("--verdict")
    assert code == 0 and len(lines) == 1 and lines[0].startswith("STUCK:"), lines
