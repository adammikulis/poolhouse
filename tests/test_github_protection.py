"""scripts/github-protection: drift found from canned `gh api` replies, and no call that writes."""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.github_support import GOOD, REPO, SCRIPT, codes, serve

pytest_plugins = ["github_support"]
pytestmark = pytest.mark.usefixtures("device")


def run(gp, capsys, *flags):
    code = gp.main(["--check", "--repo", REPO, *flags])
    return code, capsys.readouterr().out


def anchors():
    """The anchors GitHub gives the headings of docs/github-protection.md."""
    text = (SCRIPT.parent.parent / "docs" / "github-protection.md").read_text(encoding="utf-8")
    return {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
            for h in re.findall(r"^#{2,3} (.+)$", text, re.M)}


SECTIONS = ("rulesets", "codeowners", "workflow-rules", "agent-identity", "who-merges",
            "actions-settings", "code-security-settings", "the-release-environment",
            "main-branch-ruleset", "development-branch-ruleset", "release-tag-ruleset", "push-credential")


@pytest.mark.parametrize("section", SECTIONS)
def test_every_section_the_script_points_at_is_a_heading(section):
    assert f'"{section}"' in SCRIPT.read_text(encoding="utf-8")
    assert section in anchors()


def changed(mutate):
    replies = copy.deepcopy(GOOD)
    mutate(replies)
    return replies


def test_the_script_contains_no_call_that_writes():
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"-X\s*(POST|PUT|PATCH|DELETE)", text)
    for forbidden in ("gh secret", "gh workflow run", "--method", "--input", "--field", "--raw-field"):
        assert forbidden not in text, forbidden
    assert not re.search(r"""["']-[fF]["']""", text)


def test_a_repository_that_matches_the_target_has_no_findings(gp, tmp_path, monkeypatch, capsys):
    log = serve(tmp_path, monkeypatch, GOOD)
    code, out = run(gp, capsys)
    assert (code, out.strip().splitlines()[-1]) == (0, "0 findings"), out
    calls = log.read_text().splitlines()
    assert calls and all(re.fullmatch(r"GET \S+", line) for line in calls), calls


def test_every_call_is_a_plain_get(gp, tmp_path, monkeypatch, capsys):
    log = serve(tmp_path, monkeypatch, GOOD)
    run(gp, capsys, "--agent")
    for line in log.read_text().splitlines():
        words = line.split()
        assert words[0] == "GET" and len(words) == 2, line


def test_the_repository_comes_from_gh_when_none_is_named(gp, tmp_path, monkeypatch, capsys):
    serve(tmp_path, monkeypatch, GOOD)
    assert gp.main(["--check"]) == 0
    assert capsys.readouterr().out.strip().endswith("0 findings")


def test_local_mode_makes_no_request(gp, tmp_path, monkeypatch, capsys):
    log = serve(tmp_path, monkeypatch, {})
    assert gp.main(["--check", "--local"]) == 0
    assert "(local only)" in capsys.readouterr().out
    assert log.read_text() == ""


CASES = {
    "bypass actor on main": (
        lambda r: r[f"repos/{REPO}/rulesets/1"]["bypass_actors"].append(
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}),
        "ruleset.main.bypass"),
    "no pull request rule": (
        lambda r: r[f"repos/{REPO}/rulesets/1"]["rules"].pop(2), "ruleset.main.rule"),
    "gates not required": (
        lambda r: r[f"repos/{REPO}/rulesets/1"]["rules"][3]["parameters"][
            "required_status_checks"].pop(0), "ruleset.main.checks"),
    "dev ruleset blocks pushes": (
        lambda r: r[f"repos/{REPO}/rulesets/2"]["rules"].append({"type": "update"}),
        "ruleset.dev.extra"),
    "tag ruleset missing": (
        lambda r: r[f"repos/{REPO}/rulesets"].pop(), "ruleset.tags.missing"),
    "ruleset only evaluating": (
        lambda r: r[f"repos/{REPO}/rulesets/3"].update(enforcement="evaluate"),
        "ruleset.tags.enforcement"),
    "no reviewer": (
        lambda r: r[f"repos/{REPO}/environments/release"].update(protection_rules=[]),
        "env.reviewer"),
    "admins bypass the environment": (
        lambda r: r[f"repos/{REPO}/environments/release"].update(can_admins_bypass=True),
        "env.admin-bypass"),
    "environment open to every branch": (
        lambda r: r[f"repos/{REPO}/environments/release"].update(
            deployment_branch_policy={"custom_branch_policies": False}), "env.deployment"),
    "environment allows another branch": (
        lambda r: r[f"repos/{REPO}/environments/release/deployment-branch-policies"][
            "branch_policies"].append({"name": "v*", "type": "tag"}), "env.deployment"),
    "key held as a repository secret": (
        lambda r: r[f"repos/{REPO}/actions/secrets"]["secrets"].append(
            {"name": "RELEASE_SIGNING_KEY"}), "secret.repository"),
    "key missing from the environment": (
        lambda r: r[f"repos/{REPO}/environments/release/secrets"].update(secrets=[]),
        "secret.environment"),
    "token can write": (
        lambda r: r[f"repos/{REPO}/actions/permissions/workflow"].update(
            default_workflow_permissions="write"), "actions.default-token"),
    "every action allowed": (
        lambda r: r[f"repos/{REPO}/actions/permissions"].update(allowed_actions="all"),
        "actions.allowed"),
    "first-time contributors only": (
        lambda r: r[f"repos/{REPO}/actions/permissions/fork-pr-contributor-approval"].update(
            approval_policy="first_time_contributors"), "actions.fork-approval"),
    "push protection off": (
        lambda r: r[f"repos/{REPO}"]["security_and_analysis"].update(
            secret_scanning_push_protection={"status": "disabled"}),
        "security.secret_scanning_push_protection"),
    "auto-merge on": (
        lambda r: r[f"repos/{REPO}"].update(allow_auto_merge=True), "repo.auto-merge"),
}


@pytest.mark.parametrize("what", CASES)
def test_drift_is_reported_with_its_fix(gp, tmp_path, monkeypatch, capsys, what):
    mutate, code = CASES[what]
    serve(tmp_path, monkeypatch, changed(mutate))
    status, out = run(gp, capsys)
    assert status == 1
    assert code in codes(out), out
    line = next(ln for ln in out.splitlines() if ln.startswith(code))
    assert re.search(r"-> docs/github-protection\.md#[a-z0-9-]+$", line), line
    assert line.rsplit("#", 1)[1] in anchors()


def test_an_unreadable_endpoint_is_a_finding(gp, tmp_path, monkeypatch, capsys):
    replies = changed(lambda r: r.pop(f"repos/{REPO}/environments/release"))
    serve(tmp_path, monkeypatch, replies)
    status, out = run(gp, capsys)
    assert status == 1 and "env.read" in codes(out)


def test_an_agent_run_fails_on_owner_or_admin_credentials(gp, tmp_path, monkeypatch, capsys):
    def owner(r):
        r["user"] = {"login": "ownerlogin"}
        r[f"repos/{REPO}"]["permissions"]["admin"] = True

    serve(tmp_path, monkeypatch, changed(owner))
    assert run(gp, capsys)[0] == 0
    status, out = run(gp, capsys, "--agent")
    assert status == 1 and {"identity.owner", "identity.admin"} <= codes(out)


def test_the_owner_review_is_required_only_when_asked(gp, tmp_path, monkeypatch, capsys):
    serve(tmp_path, monkeypatch, GOOD)
    assert run(gp, capsys)[0] == 0
    status, out = run(gp, capsys, "--require-owner-review")
    assert status == 1 and "ruleset.main.pull-request" in codes(out)


def test_any_repository_secret_is_a_finding(gp, tmp_path, monkeypatch, capsys):
    replies = changed(lambda r: r[f"repos/{REPO}/actions/secrets"]["secrets"].append(
        {"name": "OTHER_TOKEN"}))
    serve(tmp_path, monkeypatch, replies)
    status, out = run(gp, capsys)
    assert status == 1 and "OTHER_TOKEN" in out


def test_tag_creation_is_blocked_only_when_asked(gp, tmp_path, monkeypatch, capsys):
    replies = changed(lambda r: r[f"repos/{REPO}/rulesets/3"]["rules"].pop())
    serve(tmp_path, monkeypatch, replies)
    assert run(gp, capsys)[0] == 0
    status, out = run(gp, capsys, "--strict-tags")
    assert status == 1 and "ruleset.tags.rule" in codes(out)


def test_signed_commits_are_required_only_when_asked(gp, tmp_path, monkeypatch, capsys):
    serve(tmp_path, monkeypatch, GOOD)
    assert "ruleset.main.rule" in codes(run(gp, capsys, "--require-signatures")[1])


def test_codeowners_rules_cover_the_paths_that_decide_what_the_gates_accept(gp):
    rules = gp.owners_rules((SCRIPT.parent.parent / ".github" / "CODEOWNERS").read_text())
    for path in ("scripts/hooks/pre-push", "scripts/gates/ruff.py", "budgets.json",
                 "src/poolhouse/fleet/signing.py", "src/poolhouse/guard/x.py",
                 "src/poolhouse/workspace/person_session.py", ".github/workflows/ci.yml",
                 "scripts/land_run.py", "scripts/test", "pyproject.toml", ".claude/settings.json"):
        rule, owners = gp.owners_of(path, rules)
        assert rule != "*" and owners, path
    assert gp.owners_of("src/poolhouse/serve/x.py", rules)[0] == "*"


def test_codeowners_coverage_names_each_path_left_to_the_default_rule(gp, tmp_path, monkeypatch):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("* @someone\n/scripts/hooks/ @someone\n")
    monkeypatch.setattr(gp, "ROOT", tmp_path)
    found = gp.check_codeowners(["scripts/hooks/a", "scripts/gates/b", "budgets.json"])
    uncovered = [f for f in found if f.startswith("codeowners.uncovered")]
    assert any("scripts/gates/b" in f for f in uncovered)
    assert any("budgets.json" in f for f in uncovered)
    assert not any("scripts/hooks/a" in f for f in uncovered)


def test_a_missing_codeowners_file_is_a_finding(gp, tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "ROOT", tmp_path)
    assert gp.check_codeowners([])[0].startswith("codeowners.file")


def test_this_repository_covers_every_protected_path(gp):
    assert gp.check_codeowners() == []


BAD_WORKFLOW = """\
on:
  pull_request_target:
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: write
    steps:
      - uses: actions/checkout@v4
      - run: echo ${{ secrets.SIGNING }}
  call:
    uses: ./.github/workflows/other.yml
    secrets: inherit
"""


def test_workflow_rules_name_each_unsafe_construct(gp, tmp_path, monkeypatch):
    (tmp_path / "bad.yml").write_text(BAD_WORKFLOW)
    monkeypatch.setattr(gp, "WORKFLOWS", tmp_path)
    found = {line.split()[0] for line in gp.check_workflows()}
    assert {"workflow.unpinned", "workflow.pr-target", "workflow.top-write", "workflow.id-token",
            "workflow.pr-write", "workflow.secret-scope", "workflow.secrets-inherit",
            "workflow.tag-guard", "workflow.base-gate"} <= found


def test_this_repository_workflows_follow_the_rules(gp):
    assert gp.check_workflows() == []


def expires_in(**delta):
    """The token-expiration header GitHub sends for a credential that ends ``delta`` from now."""
    when = (datetime.now(UTC) + timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {"Github-Authentication-Token-Expiration": when}


def credential(push=True, admin=False):
    def apply(replies):
        replies[f"repos/{REPO}"]["permissions"].update(push=push, admin=admin)
    return apply


def app_token(replies):
    """An installation token: it reads the installation and cannot read the account behind `user`."""
    replies["installation/repositories"] = {"total_count": 1, "repositories": []}
    replies.pop("user")


def served(tmp_path, monkeypatch, mutate, header=None, **kwargs):
    headers = {f"repos/{REPO}": header} if header else None
    return serve(tmp_path, monkeypatch, changed(mutate), headers=headers, **kwargs)


def test_a_device_that_is_not_the_lander_holds_no_push_credential_per_the_check(
        gp, tmp_path, monkeypatch, capsys):
    (tmp_path / "member").mkdir()
    (tmp_path / "lander").mkdir()
    served(tmp_path / "member", monkeypatch, credential(push=True))
    status, out = run(gp, capsys, "--agent")
    assert status == 1 and "credential.push" in codes(out), out
    line = next(ln for ln in out.splitlines() if ln.startswith("credential.push"))
    assert line.rsplit("#", 1)[1] == "push-credential" and "push-credential" in anchors()
    served(tmp_path / "lander", monkeypatch, credential(push=True), expires_in(minutes=50))
    assert "credential.push" not in codes(run(gp, capsys, "--agent", "--lander")[1])


@pytest.mark.parametrize("held", ({"push": True}, {"maintain": True}, {"admin": True}))
def test_any_right_to_write_is_a_push_credential_on_a_device_that_is_not_the_lander(
        gp, tmp_path, monkeypatch, capsys, held):
    served(tmp_path, monkeypatch, lambda r: r[f"repos/{REPO}"]["permissions"].update(
        {"push": False, "admin": False, **held}))
    assert "credential.push" in codes(run(gp, capsys, "--agent")[1])


def test_a_read_only_credential_leaves_a_device_that_is_not_the_lander_clean(
        gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, credential(push=False))
    status, out = run(gp, capsys, "--agent")
    assert (status, out.strip().splitlines()[-1]) == (0, "0 findings"), out


def test_a_gh_failure_that_is_not_signing_out_clears_nothing(gp, tmp_path, monkeypatch, capsys):
    log = serve(tmp_path, monkeypatch, {})
    status, out = run(gp, capsys, "--agent")
    assert status == 1 and {"repo.read", "ruleset.read"} <= codes(out), out
    assert log.read_text().splitlines()[0] == "GET rate_limit"


def test_a_reply_with_null_permissions_is_a_finding(gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, lambda r: r[f"repos/{REPO}"].update(permissions=None))
    assert "credential.unknown" in codes(run(gp, capsys, "--agent")[1])


def test_a_reply_that_does_not_say_what_the_credentials_may_do_is_a_finding(
        gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, lambda r: r[f"repos/{REPO}"].pop("permissions"))
    assert "credential.unknown" in codes(run(gp, capsys, "--agent")[1])


def test_the_lander_flag_means_nothing_without_the_agent_flag(gp, capsys):
    with pytest.raises(SystemExit) as stop:
        gp.main(["--check", "--repo", REPO, "--lander"])
    assert stop.value.code == 2 and "--agent" in capsys.readouterr().err


def test_the_lander_credential_must_expire_within_the_hour(gp, tmp_path, monkeypatch, capsys):
    for name in ("app", "year", "never"):
        (tmp_path / name).mkdir()

    served(tmp_path / "app", monkeypatch, app_token, expires_in(minutes=50))
    status, out = run(gp, capsys, "--agent", "--lander")
    assert (status, out.strip().splitlines()[-1]) == (0, "0 findings"), out
    served(tmp_path / "year", monkeypatch, lambda r: None, expires_in(days=300))
    status, out = run(gp, capsys, "--agent", "--lander")
    assert status == 1 and "credential.long-lived" in codes(out), out
    served(tmp_path / "never", monkeypatch, lambda r: None, {"X-Oauth-Scopes": "repo, workflow"})
    status, out = run(gp, capsys, "--agent", "--lander")
    assert status == 1 and "credential.no-expiry" in codes(out), out
    for line in out.splitlines():
        if line.startswith("credential."):
            assert line.rsplit("#", 1)[1] == "push-credential"


def test_an_installation_token_that_reports_a_long_life_is_refused(gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, app_token, expires_in(hours=3))
    assert "credential.long-lived" in codes(run(gp, capsys, "--agent", "--lander")[1])


def test_an_installation_token_with_no_expiry_header_is_not_accepted_on_the_lander(
        gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, app_token)
    status, out = run(gp, capsys, "--agent", "--lander")
    assert status == 1 and "credential.no-expiry" in codes(out), out


def test_an_installation_token_with_a_malformed_expiry_header_is_not_accepted_on_the_lander(
        gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, app_token, {"Github-Authentication-Token-Expiration": "soon"})
    status, out = run(gp, capsys, "--agent", "--lander")
    assert status == 1 and "credential.no-expiry" in codes(out), out


def test_an_expiry_in_the_past_is_a_finding_on_the_lander(gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, app_token, expires_in(minutes=-5))
    status, out = run(gp, capsys, "--agent", "--lander")
    assert status == 1 and "credential.expired" in codes(out), out


def test_a_device_that_is_not_the_lander_is_not_asked_for_an_expiry(gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, credential(push=False))
    assert run(gp, capsys, "--agent")[0] == 0


def test_an_expiry_that_cannot_be_read_counts_as_none(gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, lambda r: None, {"Github-Authentication-Token-Expiration": "soon"})
    assert "credential.no-expiry" in codes(run(gp, capsys, "--agent", "--lander")[1])


def test_the_owner_check_still_runs_on_an_app_token_without_reading_the_account(
        gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, app_token, expires_in(minutes=10))
    status, out = run(gp, capsys, "--agent", "--lander")
    assert "identity.read" not in codes(out) and status == 0, out
    (tmp_path / "admin").mkdir()
    served(tmp_path / "admin", monkeypatch, lambda r: (app_token(r), credential(admin=True)(r)))
    assert "identity.admin" in codes(run(gp, capsys, "--agent", "--lander")[1])


@pytest.mark.parametrize("rule", ("deletion", "non_fast_forward"))
def test_the_development_branch_ruleset_blocks_deletion_and_force_push_per_the_check(
        gp, tmp_path, monkeypatch, capsys, rule):
    served(tmp_path, monkeypatch, lambda r: r[f"repos/{REPO}/rulesets/2"].update(rules=[
        x for x in r[f"repos/{REPO}/rulesets/2"]["rules"] if x["type"] != rule]))
    status, out = run(gp, capsys)
    assert status == 1 and "ruleset.dev.rule" in codes(out), out
    assert f"dev lacks the {rule} rule" in out


def test_a_development_branch_with_no_ruleset_is_a_finding(gp, tmp_path, monkeypatch, capsys):
    served(tmp_path, monkeypatch, lambda r: r[f"repos/{REPO}/rulesets"].pop(1))
    assert "ruleset.dev.missing" in codes(run(gp, capsys)[1])


def test_the_containment_row_names_tests_that_exist():
    docs = SCRIPT.parent.parent / "docs"
    row = next(ln for ln in (docs / "compromise-containment.md").read_text(encoding="utf-8").splitlines()
               if ln.startswith("| C13 |"))
    me = Path(__file__).read_text(encoding="utf-8")
    names = re.findall(r"`([a-z0-9_]{20,})`", row)
    assert len(names) == 3
    for name in names:
        assert f"def test_{name}(" in me, name


def test_the_lander_flag_says_in_its_help_that_it_is_declared_and_not_detected(gp, capsys):
    with pytest.raises(SystemExit) as stop:
        gp.main(["--help"])
    assert stop.value.code == 0
    assert "declared, not detected" in " ".join(capsys.readouterr().out.split())
