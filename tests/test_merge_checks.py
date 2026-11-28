"""The parse check and the data-file check of the pull-request `gates` job: its steps, run as the workflow writes them over a real
repository laid out as the runner lays it out, fail the job for what they are there to stop."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
JOB = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]["gates"]
OVERLAY, PARSES, NO_DATA = "the base commit's gate over this tree", "the changed Python parses", "no data or weight file"
CHECKERS = ("scripts/sealed_git.py", "scripts/sealed_git_parse.py", "scripts/hooks/no-data-files", "scripts/hooks/renames.py")

def bash(test):
    return pytest.mark.skipif(os.name == "nt", reason="the workflow's steps are run by bash")(test)


def step(name: str) -> dict:
    [found] = [each for each in JOB["steps"] if each.get("name") == name]
    return found


class Runner:
    """A pull request checked out as the job has it: the head in the working folder and the base commit's files under `trusted`."""

    def __init__(self, folder: Path, base_checkers: tuple[str, ...] = CHECKERS) -> None:
        self.work = folder / "work"
        self.work.mkdir()
        self.git("init", "-q", "-b", "dev")
        for name in ("scripts/budgets", "scripts/gates/_util.py", *base_checkers):
            self.write(name, (ROOT / name).read_bytes())
        self.base = self.commit("chore: base")

    def git(self, *words: str) -> str:
        done = subprocess.run(["git", "-C", str(self.work), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               "-c", "commit.gpgsign=false", *words], check=True, capture_output=True, text=True)
        return done.stdout.strip()

    def write(self, name: str, content: bytes | str) -> None:
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def job(self, *names: str, base: str = "") -> dict[str, subprocess.CompletedProcess]:
        """The overlay step and then ``names``, each run by `bash -e` as the runner runs it."""
        trusted = self.work / "trusted"
        if not trusted.exists():
            self.git("worktree", "add", "-q", "--detach", str(trusted), self.base)
        done = {}
        for name in (OVERLAY, *names):
            script = step(name)["run"].replace("python ", f"{sys.executable} ")
            done[name] = subprocess.run(["bash", "-e", "-c", script], cwd=self.work, capture_output=True, text=True, check=False,
                                        env={**os.environ, "BASE": base or self.base})
        assert done[OVERLAY].returncode == 0, done[OVERLAY].stderr
        return done


@pytest.mark.parametrize("name", [PARSES, NO_DATA])
def test_the_check_runs_on_a_pull_request_after_the_base_commits_checkers_and_its_failure_fails_the_job(name):
    names = [each.get("name") for each in JOB["steps"]]
    assert names.index(OVERLAY) < names.index(name)
    assert step(name)["if"] == "github.event_name == 'pull_request'"
    assert step(name)["env"] == {"BASE": "${{ github.event.pull_request.base.sha }}"}
    assert "continue-on-error" not in step(name) and "continue-on-error" not in JOB and "if" not in JOB
    assert "||" not in step(name)["run"]


@bash
def test_a_change_that_parses_and_holds_no_data_passes_both(tmp_path):
    runner = Runner(tmp_path)
    runner.write("src/ok.py", "VALUE = 1\n")
    runner.write("docs/note.md", "text\n")
    runner.commit("feat: ordinary")
    done = runner.job(PARSES, NO_DATA)
    assert [done[name].returncode for name in (PARSES, NO_DATA)] == [0, 0], [each.stderr for each in done.values()]


@bash
@pytest.mark.parametrize("name", ["src/broken.py", "tests/test_café.py"])
def test_python_that_does_not_parse_fails_the_job_naming_the_file(tmp_path, name):
    runner = Runner(tmp_path)
    runner.write(name, "def (:\n")
    runner.commit("feat: broken")
    done = runner.job(PARSES)[PARSES]
    assert done.returncode == 1 and f"{name}:1" in done.stderr, done.stderr


@bash
def test_a_file_that_stops_parsing_in_a_later_commit_of_the_pull_request_fails_the_job(tmp_path):
    runner = Runner(tmp_path)
    runner.write("src/later.py", "VALUE = 1\n")
    runner.commit("feat: fine")
    runner.write("src/later.py", "VALUE = (\n")
    runner.commit("feat: broken")
    assert runner.job(PARSES)[PARSES].returncode == 1


@bash
@pytest.mark.parametrize("name,content", [("docs/rows.csv", "a,b\n"), ("model.gguf", "GGUF"), ("data/notes.txt", "x\n"),
                                          ("docs/large.txt", "x" * ((1 << 20) + 1))],
                         ids=["extension", "weights", "directory", "size"])
def test_a_data_or_weight_file_fails_the_job_naming_it(tmp_path, name, content):
    runner = Runner(tmp_path)
    runner.write(name, content)
    runner.commit("feat: data")
    done = runner.job(NO_DATA)[NO_DATA]
    assert done.returncode == 1 and name in done.stderr, done.stderr


@bash
@pytest.mark.parametrize("name", [PARSES, NO_DATA])
def test_a_base_commit_the_checkout_does_not_hold_fails_the_job(tmp_path, name):
    runner = Runner(tmp_path)
    runner.write("src/broken.py", "def (:\n")
    runner.write("docs/rows.csv", "a,b\n")
    runner.commit("feat: both")
    assert runner.job(name, base="1" * 40)[name].returncode != 0


@bash
def test_a_pull_request_that_empties_the_checkers_is_judged_by_the_base_commits(tmp_path):
    runner = Runner(tmp_path)
    for checker in ("scripts/sealed_git_parse.py", "scripts/hooks/no-data-files"):
        runner.write(checker, "raise SystemExit(0)\n")
    runner.write("src/broken.py", "def (:\n")
    runner.write("docs/rows.csv", "a,b\n")
    runner.commit("feat: gutted")
    done = runner.job(PARSES, NO_DATA)
    assert done[PARSES].returncode == 1 and done[NO_DATA].returncode == 1, [each.stderr for each in done.values()]


@bash
def test_a_base_commit_without_the_parse_check_leaves_the_pull_requests_own_to_run(tmp_path):
    runner = Runner(tmp_path, tuple(name for name in CHECKERS if name != "scripts/sealed_git_parse.py"))
    shutil.copy(ROOT / "scripts" / "sealed_git_parse.py", runner.work / "scripts" / "sealed_git_parse.py")
    runner.write("src/broken.py", "def (:\n")
    runner.commit("feat: the first")
    done = runner.job(PARSES)[PARSES]
    assert done.returncode == 1 and "src/broken.py" in done.stderr, done.stderr


def test_the_parse_check_starts_no_git_of_its_own():
    tree = ast.parse((ROOT / "scripts" / "sealed_git_parse.py").read_text(encoding="utf-8"))
    assert [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == "git"] == []
