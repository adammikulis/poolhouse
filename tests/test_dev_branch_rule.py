"""`scripts/dev-branch-rule` can only add protection to the development branch.

PLUMBING ONLY: GitHub's replies are CANNED here; the script's own choices are what is tested.
"""

from __future__ import annotations

import copy
import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev-branch-rule"
REPO = "someone/project"


def load():
    loader = SourceFileLoader("dev_branch_rule", str(SCRIPT))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def ruleset(**changed):
    return {"id": 7, "name": "development branch", "target": "branch", "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["refs/heads/0.9dev"], "exclude": []}},
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}], **changed}


@pytest.fixture
def github(tmp_path, monkeypatch):
    """The module with a CANNED GitHub holding ``held``; ``writes`` lists every call that would change it."""
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "0.9dev")
    module = load()
    held, writes = [ruleset()], []

    def gh(*args, body=None):
        if args[0] == "repo":
            return {"nameWithOwner": REPO, "defaultBranchRef": {"name": "main"}}
        if "-X" in args:
            writes.append((args[2], args[3], copy.deepcopy(body)))
            held[0] = {**held[0], **copy.deepcopy(body)}
            return {}
        if args[1] == f"repos/{REPO}/rulesets":
            return [{"id": r["id"]} for r in held]
        return copy.deepcopy(next(r for r in held if args[1].endswith(f"/{r['id']}")))

    monkeypatch.setattr(module, "gh", gh)
    module.held, module.writes = held, writes
    return module


def test_without_apply_nothing_is_changed(github, capsys):
    assert github.main([]) == 0
    assert github.writes == [] and "nothing changed" in capsys.readouterr().out


def test_apply_adds_the_pull_request_and_checks_and_keeps_what_was_there(github, tmp_path):
    assert github.main(["--apply"]) == 0
    (method, path, body), = github.writes
    assert (method, path) == ("PUT", f"repos/{REPO}/rulesets/7")
    rules = {rule["type"]: rule.get("parameters") for rule in body["rules"]}
    assert set(rules) == {"deletion", "non_fast_forward", "pull_request", "required_status_checks"}
    assert rules["pull_request"]["required_approving_review_count"] == 0
    assert [c["context"] for c in rules["required_status_checks"]["required_status_checks"]] == list(github.CHECKS)
    assert body["bypass_actors"] == [github.ADMINS] and body["conditions"] == ruleset()["conditions"]
    saved, = (tmp_path / "state" / "github-protection").iterdir()
    assert json.loads(saved.read_text()) == ruleset()


def test_a_second_apply_changes_nothing(github):
    assert github.main(["--apply"]) == 0 and github.main(["--apply"]) == 0
    assert len(github.writes) == 1


def test_checks_and_parameters_already_required_stay(github):
    strict = {"type": "pull_request", "parameters": {"required_approving_review_count": 2}}
    checks = {"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": True, "required_status_checks": [{"context": "other"}]}}
    github.held[0] = ruleset(rules=[strict, checks])
    assert github.main(["--apply"]) == 0
    body = github.writes[0][2]
    assert body["rules"][0] == strict and body["bypass_actors"] == [], "a rule in force gains no bypass"
    kept = body["rules"][1]["parameters"]
    assert kept["strict_required_status_checks_policy"] is True
    assert [c["context"] for c in kept["required_status_checks"]] == ["other", *github.CHECKS]


@pytest.mark.parametrize("held", [
    ruleset(conditions={"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}),
    ruleset(conditions={"ref_name": {"include": ["refs/heads/0.9dev", "refs/heads/main"], "exclude": []}}),
    ruleset(conditions={"ref_name": {"include": ["refs/heads/0.9dev"], "exclude": ["refs/heads/0.9dev"]}}),
    ruleset(target="tag"),
    ruleset(enforcement="disabled"),
    ruleset(bypass_actors=[{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]),
])
def test_a_ruleset_that_is_not_only_the_active_development_one_is_left_alone(github, held):
    github.held[0] = held
    assert github.main(["--apply"]) == 1
    assert github.writes == []


@pytest.mark.parametrize("branch", ["main", "master", "release"])
def test_a_branch_that_is_no_development_branch_is_never_touched(github, monkeypatch, branch):
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", branch)
    monkeypatch.setattr(github, "development_branch", lambda _root: branch)
    github.held[0] = ruleset(conditions={"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}})
    assert github.main(["--apply"]) == 1
    assert github.writes == []


@pytest.mark.parametrize("words", [["--branch", "main"], ["--repo", "other/one"], ["--app"], ["main"]])
def test_it_takes_nothing_from_its_caller_but_apply(github, words):
    with pytest.raises(SystemExit):
        github.main(words)
    assert github.writes == []
