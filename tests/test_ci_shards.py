"""The CI test job's shards: balanced from recorded times, covering every test file once, run with a timeout."""

from __future__ import annotations

import json
import re
from pathlib import Path

import ci_shards
import pytest
import test_release_workflows as workflows
import testhistory

REPO = Path(__file__).resolve().parents[1]
CASE = '<testcase classname="{name}" name="test_x" time="{seconds}"></testcase>'


def tree(root: Path, files: dict[str, float | None]) -> Path:
    """Test files under ``root/tests``; the durations file records those whose time is a number."""
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_x():\n    pass\n", encoding="utf-8")
    recorded = {name: seconds for name, seconds in files.items() if seconds is not None}
    (root / ci_shards.DURATIONS).write_text(json.dumps({"files": recorded}), encoding="utf-8")
    return root


def junit(path: Path, rows: dict[str, float]) -> Path:
    path.write_text("<testsuites><testsuite>"
                    + "".join(CASE.format(name=name, seconds=seconds) for name, seconds in rows.items())
                    + "</testsuite></testsuites>", encoding="utf-8")
    return path


def test_every_test_file_is_in_exactly_one_shard(tmp_path):
    names = {f"tests/test_{c}.py": float(i) for i, c in enumerate("abcdefghij")}
    names["tests/pool_check/test_rig.py"] = 3.0
    root = tree(tmp_path, names)
    placed = [name for group in ci_shards.shard_files(root, 4) for name in group]
    assert sorted(placed) == sorted(names)


def test_the_shards_carry_equal_recorded_time(tmp_path):
    root = tree(tmp_path, {"tests/test_a.py": 100, "tests/test_b.py": 60, "tests/test_c.py": 50,
                           "tests/test_d.py": 40, "tests/test_e.py": 30})
    totals = [sum(json.loads((root / ci_shards.DURATIONS).read_text())["files"][n] for n in group)
              for group in ci_shards.shard_files(root, 2)]
    assert totals == [140, 140]


def test_a_file_with_no_record_takes_the_median_of_the_recorded_ones(tmp_path):
    seconds = ci_shards.weights(["tests/test_a.py", "tests/test_b.py", "tests/test_new.py"],
                                {"tests/test_a.py": 10.0, "tests/test_b.py": 30.0})
    assert seconds["tests/test_new.py"] == 20.0
    assert ci_shards.weights(["tests/test_new.py"], {}) == {"tests/test_new.py": ci_shards.DEFAULT_S}


def test_the_same_tree_gives_the_same_shards_whatever_order_it_is_listed(tmp_path):
    root = tree(tmp_path, {f"tests/test_{c}.py": 5.0 for c in "abcdef"})
    first = ci_shards.shard_files(root, 3)
    assert ci_shards.shard_files(root, 3) == first
    shuffled = ci_shards.assign(dict(reversed(list(ci_shards.weights(
        ci_shards.collected_files(root), ci_shards.load(root / ci_shards.DURATIONS)).items()))), 3)
    assert shuffled == first


def test_list_prints_the_files_of_the_shard_asked_for(tmp_path, capsys):
    root = tree(tmp_path, {"tests/test_a.py": 9, "tests/test_b.py": 1})
    assert ci_shards.main(["list", "--shards", "2", "--index", "1"], root) == 0
    assert capsys.readouterr().out.split() == ["tests/test_a.py"]
    assert ci_shards.main(["list", "--shards", "2", "--index", "2"], root) == 0
    assert capsys.readouterr().out.split() == ["tests/test_b.py"]


@pytest.mark.parametrize("index", ["0", "3"])
def test_a_shard_outside_the_count_is_refused(tmp_path, index):
    root = tree(tmp_path, {"tests/test_a.py": 1})
    with pytest.raises(SystemExit) as refused:
        ci_shards.main(["list", "--shards", "2", "--index", index], root)
    assert refused.value.code == 2


def test_the_summary_names_each_shard_and_the_longest_file(tmp_path, capsys):
    root = tree(tmp_path, {"tests/test_a.py": 90, "tests/test_b.py": 10})
    assert ci_shards.main(["summary", "--shards", "2"], root) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["shard 1: 1 files, 90 s", "shard 2: 1 files, 10 s", "longest file: tests/test_a.py 90 s"]


def test_record_reads_the_per_test_history_into_the_durations_file(tmp_path):
    root = tree(tmp_path / "work", {"tests/test_a.py": None, "tests/test_b.py": None})
    store = tmp_path / "history.json"
    testhistory.merge(store, root, {"tests/test_a.py::test_one": (2.0, 1.0), "tests/test_a.py::test_two": (0.5, 8.0),
                                    "tests/test_b.py::test_one": (4.0, 1.0), "tests/test_gone.py::test_one": (9.0, 9.0)})
    assert ci_shards.main(["record", "--history", str(store)], root) == 0
    assert ci_shards.load(root / ci_shards.DURATIONS) == {"tests/test_a.py": 4.0, "tests/test_b.py": 4.0}


def test_record_reads_the_junit_files_of_a_run_into_the_durations_file(tmp_path):
    root = tree(tmp_path / "work", {"tests/test_a.py": None, "tests/pool_check/test_rig.py": None})
    done = junit(tmp_path / "junit.xml", {"tests.test_a": 1.5, "tests.test_a.TestOne": 2.0,
                                          "tests.pool_check.test_rig.TestRig": 6.3, "tests.test_gone": 9.0})
    assert ci_shards.main(["record", "--junit", str(done)], root) == 0
    assert ci_shards.load(root / ci_shards.DURATIONS) == {"tests/test_a.py": 3.5, "tests/pool_check/test_rig.py": 6.3}


def test_a_file_that_two_junit_files_ran_takes_their_mean(tmp_path):
    root = tree(tmp_path / "work", {"tests/test_a.py": None})
    first = junit(tmp_path / "one.xml", {"tests.test_a": 10.0})
    second = junit(tmp_path / "two.xml", {"tests.test_a": 20.0})
    assert ci_shards.main(["record", "--junit", str(first), str(second)], root) == 0
    assert ci_shards.load(root / ci_shards.DURATIONS) == {"tests/test_a.py": 15.0}


def test_the_committed_durations_file_assigns_the_real_tree_once():
    recorded = ci_shards.load(REPO / ci_shards.DURATIONS)
    assert recorded and all(name.startswith("tests/") and seconds >= 0 for name, seconds in recorded.items())
    placed = [name for group in ci_shards.shard_files(REPO, 8) for name in group]
    assert sorted(placed) == ci_shards.collected_files(REPO)


def shards_job() -> dict:
    return workflows.CI["jobs"]["shards"]


def run_of(name: str) -> str:
    return next(step["run"] for step in shards_job()["steps"] if step.get("name") == name)


def test_the_workflow_runs_one_job_for_each_shard_the_script_is_asked_for():
    count = int(shards_job()["env"]["SHARDS"])
    assert shards_job()["strategy"]["matrix"]["shard"] == list(range(1, count + 1))
    script = run_of("tests")
    assert 'scripts/ci_shards.py list --shards "$SHARDS" --index ${{ matrix.shard }}' in script
    assert '"${files[@]}"' in script


def test_the_shards_run_every_test_file_through_the_slow_tier_with_a_timeout_and_a_junit_file():
    command = next(line for line in run_of("tests").splitlines() if "-m pytest" in line).split()
    assert {"--slow", "--timeout=120", "--junitxml=junit.xml"} <= set(command)


def test_each_shard_uploads_its_junit_file_however_the_tests_ended():
    uploads = [s for s in shards_job()["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert len(uploads) == 1 and uploads[0]["if"] == "always()"
    assert uploads[0]["with"]["path"] == "junit.xml"
    assert uploads[0]["with"]["name"] == "junit-${{ matrix.python }}-${{ matrix.shard }}"


def test_the_macos_run_has_the_same_timeout_and_uploads_its_junit_file():
    macos = workflows.CI["jobs"]["macos"]["steps"]
    command = next(s["run"] for s in macos if s.get("name") == "tests")
    assert "--timeout=120" in command and "--junitxml=junit.xml" in command
    assert any(s.get("if") == "always()" and str(s.get("uses", "")).startswith("actions/upload-artifact@")
               for s in macos)


def test_the_check_the_repository_requires_is_the_job_that_waits_for_every_shard():
    named = re.search(r"^CHECKS = \((.*)\)$", (REPO / "scripts" / "github-protection-apply").read_text(
        encoding="utf-8"), re.M).group(1)
    required = re.findall(r'"(test \([^"]*\))"', named)
    job = workflows.CI["jobs"]["test"]
    assert required == [job["name"]]
    assert job["needs"] == "shards"
    assert job["if"] == "always() && (github.event_name != 'push' || github.ref == 'refs/heads/main')"
    assert 'needs.shards.result }}" = "success"' in job["steps"][0]["run"]


def test_the_test_extra_and_the_install_lines_carry_the_timeout_plugin():
    extra = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"pytest-timeout>=\d', extra)
    assert "pytest-timeout" in run_of("install test dependencies").split()
    macos = workflows.CI["jobs"]["macos"]["steps"]
    assert "pytest-timeout" in next(s["run"] for s in macos if s.get("name") == "install test dependencies").split()


def test_a_test_marked_for_a_longer_timeout_names_it_in_seconds():
    marked = []
    for path in sorted((REPO / "tests").glob("test_*.py")):
        marked += re.findall(r"@pytest\.mark\.timeout\((\S*)\)", path.read_text(encoding="utf-8"))
    assert marked and all(re.fullmatch(r"\d+", seconds) for seconds in marked)
