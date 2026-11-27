"""`scripts/dev-branch-rule` can only add protection to 0.3dev, by a ruleset of its own.

PLUMBING ONLY: GitHub's replies are CANNED here; the script's own choices are what is tested.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev-branch-rule"


def load():
    loader = SourceFileLoader("dev_branch_rule", str(SCRIPT))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def older():
    """The ruleset already in force on the branch, which nothing may change."""
    return {"id": 7, "name": "development branch: no deletion, no force push", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/0.3dev"], "exclude": []}}, "bypass_actors": [],
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]}


@pytest.fixture
def github(monkeypatch):
    """The module over a CANNED GitHub holding ``held``; ``writes`` lists every call that would change it."""
    module = load()
    held, writes = [older()], []

    def gh(method, path, body=None):
        if method == "GET" and path.startswith("rulesets?"):
            page = int(path.split("&page=")[1].split("&")[0])
            return [{"id": r["id"], "name": r["name"]} for r in held][(page - 1) * 100:page * 100]
        if method == "GET":
            return copy.deepcopy(next(r for r in held if path == f"rulesets/{r['id']}"))
        writes.append((method, path, copy.deepcopy(body)))
        if method == "POST":
            held.append({"id": 100 + len(held), **copy.deepcopy(body)})
        else:
            next(r for r in held if path == f"rulesets/{r['id']}").update(copy.deepcopy(body))
        return {}

    monkeypatch.setattr(module, "gh", gh)
    module.held, module.writes = held, writes
    return module


def test_without_apply_nothing_is_changed(github, capsys):
    assert github.main([]) == 0
    assert github.writes == [] and "nothing changed" in capsys.readouterr().out


def test_apply_creates_a_ruleset_of_its_own_and_leaves_the_one_in_force(github):
    assert github.main(["--apply"]) == 0
    (method, path, body), = github.writes
    assert (method, path) == ("POST", "rulesets")
    assert [rule["type"] for rule in body["rules"]] == ["pull_request", "required_status_checks"]
    assert [c["context"] for c in body["rules"][1]["parameters"]["required_status_checks"]] == list(github.CHECKS)
    assert body["conditions"] == {"ref_name": {"include": ["refs/heads/0.3dev"], "exclude": []}}
    assert body["bypass_actors"] == [github.ADMINS] and github.held[0] == older()


def test_a_second_apply_changes_nothing(github):
    assert github.main(["--apply"]) == 0 and github.main(["--apply"]) == 0
    assert len(github.writes) == 1


def test_a_missing_check_is_added_and_nothing_it_holds_changes(github):
    mine = {"id": 9, **github.wanted(None)}
    mine["bypass_actors"] = []
    mine["rules"][0]["parameters"]["required_approving_review_count"] = 2
    mine["rules"][1]["parameters"] = {"strict_required_status_checks_policy": True,
                                      "required_status_checks": [{"context": "other"}]}
    mine["rules"].append({"type": "required_signatures"})
    github.held.append(mine)
    assert github.main(["--apply"]) == 0
    (method, path, body), = github.writes
    assert (method, path) == ("PUT", "rulesets/9")
    assert body["bypass_actors"] == [], "a bypass taken away stays away"
    assert body["rules"][0]["parameters"]["required_approving_review_count"] == 2
    assert body["rules"][2] == {"type": "required_signatures"}
    status = body["rules"][1]["parameters"]
    assert status["strict_required_status_checks_policy"] is True
    assert [c["context"] for c in status["required_status_checks"]] == ["other", *github.CHECKS]


@pytest.mark.parametrize("changed", [
    {"conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}},
    {"conditions": {"ref_name": {"include": ["refs/heads/*dev"], "exclude": []}}},
    {"conditions": {"ref_name": {"include": ["refs/heads/0.3dev", "refs/heads/main"], "exclude": []}}},
    {"target": "tag"},
    {"enforcement": "disabled"},
    {"bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]},
])
def test_a_ruleset_of_its_name_that_is_not_as_made_here_is_left_alone(github, changed):
    github.held.append({"id": 9, **github.wanted(None), **changed})
    assert github.main(["--apply"]) == 1
    assert github.writes == []


def test_two_rulesets_of_its_name_are_both_left_alone(github):
    github.held.extend({"id": each, **github.wanted(None)} for each in (9, 10))
    assert github.main(["--apply"]) == 1 and github.writes == []


def test_a_ruleset_past_the_first_page_is_found(github):
    github.held.extend({**older(), "id": 200 + each, "name": f"other {each}"} for each in range(100))
    github.held.append({"id": 9, **github.wanted(None)})
    assert github.main(["--apply"]) == 0 and github.writes == []


@pytest.mark.parametrize("words", [["--branch", "main"], ["--repo", "other/one"], ["--app"], ["main"]])
def test_it_takes_nothing_from_its_caller_but_apply(github, words):
    with pytest.raises(SystemExit):
        github.main(words)
    assert github.writes == []


def test_no_variable_of_the_caller_moves_the_repository_or_the_branch(monkeypatch):
    module, asked = load(), []
    for name, value in (("GH_REPO", "other/one"), ("GH_HOST", "example.invalid"), ("GIT_DIR", "/elsewhere/.git"),
                        ("POOLHOUSE_DEV_BRANCH", "main")):
        monkeypatch.setenv(name, value)

    def run(argv, **how):
        asked.append((argv, how))
        return subprocess.CompletedProcess(argv, 0, json.dumps([]), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([]) == 0
    (argv, how), = asked
    assert argv[:6] == ["gh", "api", "--hostname", "github.com", "-X", "GET"]
    assert argv[6].startswith(f"repositories/{module.REPOSITORY}/rulesets?")
    assert not {"GH_REPO", "GH_HOST", "GIT_DIR"} & set(how["env"]) and how["cwd"] == "/"
    assert module.CONDITIONS["ref_name"]["include"] == ["refs/heads/0.3dev"]


def test_it_runs_isolated_and_imports_only_the_standard_library():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env -S python3 -I\n")
    assert "from poolhouse" not in text and "import poolhouse" not in text and "sys.path" not in text


def test_started_without_isolation_it_ends_before_importing_anything_beside_it(tmp_path):
    (tmp_path / "copy.py").write_text("raise SystemExit('a file beside the script ran')\n", encoding="utf-8")
    beside = tmp_path / "dev-branch-rule"
    beside.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    done = subprocess.run([sys.executable, str(beside)], capture_output=True, text=True, check=False, timeout=30)
    assert done.returncode == 1 and "runs it isolated" in done.stderr and "beside the script ran" not in done.stderr
