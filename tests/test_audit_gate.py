"""The audit and red-team workflows must fail on findings, and accepted findings must expire.

GitHub Actions cannot run here, so this parses the workflow files and runs the gate script they call.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def steps(workflow: str, job: str) -> list[dict]:
    return yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"][job]["steps"]


def step_with(workflow: str, job: str, needle: str) -> dict:
    found = [s for s in steps(workflow, job) if needle in str(s.get("run", ""))]
    assert len(found) == 1, f"{workflow}/{job}: expected one step running {needle!r}"
    return found[0]


def gate_module():
    spec = importlib.util.spec_from_file_location("audit_gate", ROOT / "scripts" / "audit_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pip_audit_step_runs_the_gate_and_cannot_be_swallowed():
    step = step_with("audit.yml", "pip-audit", "pip-audit")
    assert "scripts/audit_gate.py" in step["run"]
    assert step.get("continue-on-error") in (None, False)
    last = [line for line in step["run"].splitlines() if line.strip()][-1]
    assert last.strip().startswith("python scripts/audit_gate.py") and "||" not in last


def not_run_notice() -> dict:
    """The step of the red-team workflow that says the attacks on a real model were not run."""
    found = [s for s in steps("redteam.yml", "redteam") if "NOT RUN" in str(s.get("name", ""))]
    assert len(found) == 1, "redteam.yml: one step must say the real-model attacks are not run on this runner"
    return found[0]


def test_the_redteam_workflow_runs_no_attack_on_a_runner_that_has_no_model_and_says_so_loudly():
    """A hosted runner has no GPU and no model, and nothing stands in for one: the attacks are not run there, and
    the last step of every run says what was not run, why, and the command that runs it on a machine with the model."""
    notice = not_run_notice()
    assert notice.get("continue-on-error") in (None, False) and "if" not in notice
    assert steps("redteam.yml", "redteam")[-1] == notice, "the notice is the last thing the run says"
    assert "::warning" in notice["run"] and "GITHUB_STEP_SUMMARY" in notice["run"]
    assert "no GPU" in notice["env"]["REASON"] and "nothing stands in" in notice["env"]["REASON"]
    runs = [s for s in steps("redteam.yml", "redteam") if "poolhouse.redteam run" in str(s.get("run", ""))]
    assert runs == [], "no step runs the attacks on this runner"
    tests = [s for s in steps("redteam.yml", "redteam") if "pytest tests/test_redteam_" in str(s.get("run", ""))]
    assert len(tests) == 1 and "--redteam" in tests[0]["run"] and "--slow" not in tests[0]["run"]


def test_the_real_model_red_team_tests_run_in_the_nightly_real_model_job():
    """What the redteam workflow does not run on a hosted runner has a place where it does run."""
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["real-model"]
    assert "schedule" in str(job["if"]) and "--real-model" in str(job["steps"])
    ran = " ".join(str(one.get("run", "")) for one in job["steps"])
    assert "tests/test_redteam_sentinel_model.py" in ran and "tests/test_redteam_lab_home.py::" in ran
    for name in ("tests/test_redteam_sentinel_model.py", "tests/test_redteam_lab_home.py"):
        module = (ROOT / name).read_text(encoding="utf-8")
        assert "pytest.mark.real_model" in module and "pytest.mark.slow" in module, name


def test_the_command_the_workflow_prints_reproduces_the_committed_real_model_baseline():
    command = shlex.split(not_run_notice()["env"]["COMMAND"])
    assert command[:4] == ["python", "-m", "poolhouse.redteam", "run"]
    given = dict(zip(command[4::2], command[5::2], strict=True))
    baseline = ROOT / given["--against"]
    assert baseline.is_file() and baseline.name.startswith("baseline-real-model-")
    meta = json.loads(baseline.read_text(encoding="utf-8"))["meta"]
    recorded = shlex.split(meta["command"])
    ran = dict(zip(recorded[4::2], recorded[5::2], strict=True))
    assert given["--model"] == ran["--model"] == meta["model"] and meta["model"].endswith(".gguf")
    assert given["--scenarios"] == ran["--scenarios"] == meta["scenarios"]
    assert {"extraction", "fleet", "sentinel"} <= set(given["--scenarios"].split(","))
    assert {"chat", "loop", "compaction", "isolation"}.isdisjoint(given["--scenarios"].split(","))
    assert "stub" not in meta["command"].lower() and "--success-tolerance" in command and "--ttd-tolerance" in command
    pull = not_run_notice()["env"]["PULL"]
    assert pull.startswith("poolhouse-models pull hf:") and pull.endswith(meta["model"])


def test_the_redteam_workflow_is_scheduled_and_pins_what_it_uses():
    doc = yaml.safe_load((WORKFLOWS / "redteam.yml").read_text(encoding="utf-8"))
    assert "cron" in doc[True]["schedule"][0]
    for job in doc["jobs"].values():
        for one in job["steps"]:
            if "uses" in one:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", one["uses"]), f"not pinned: {one['uses']}"


def test_every_workflow_is_still_scheduled():
    for name in ("audit.yml", "redteam.yml"):
        doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        assert "schedule" in (doc.get(True) or doc.get("on"))


def test_every_accepted_finding_has_a_reason_and_an_expiry_in_the_future():
    allow = gate_module().load_allow()
    for entry in allow:
        assert entry["reason"].strip()
        assert date.fromisoformat(entry["expires"]) > date.today(), f"{entry['id']} expired: renew or remove it"


REPORT = {"dependencies": [{"name": "Foo", "version": "1.0", "vulns": [{"id": "PYSEC-1"}]},
                           {"name": "bar", "version": "2.0", "vulns": []}]}


def test_the_gate_fails_on_a_vulnerability_nobody_accepted():
    assert gate_module().gate(REPORT, [], date.today()) == ["Foo 1.0: PYSEC-1"]


def test_the_gate_passes_a_finding_with_a_live_acceptance_and_fails_it_once_lapsed():
    entry = {"id": "PYSEC-1", "package": "foo", "reason": "r", "expires": (date.today() + timedelta(days=5)).isoformat()}
    gate = gate_module().gate
    assert gate(REPORT, [entry], date.today()) == []
    problems = gate(REPORT, [entry], date.today() + timedelta(days=6))
    assert problems and "expired" in problems[0]


def test_the_script_exits_one_on_a_finding_and_zero_on_a_clean_report(tmp_path):
    bad, good = tmp_path / "bad.json", tmp_path / "good.json"
    bad.write_text(json.dumps(REPORT), encoding="utf-8")
    good.write_text(json.dumps({"dependencies": []}), encoding="utf-8")
    run = lambda p: subprocess.run([sys.executable, str(ROOT / "scripts" / "audit_gate.py"), str(p)],  # noqa: E731
                                   capture_output=True, text=True, check=False)
    assert run(bad).returncode == 1 and "PYSEC-1" in run(bad).stdout
    assert run(good).returncode == 0
