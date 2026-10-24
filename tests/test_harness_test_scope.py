"""Routine scoped testing and inventory use the existing reversible policy."""
import pytest

from poolhouse import coding_policy
from poolhouse.harnesspolicy import decide, workspace_authority
from poolhouse.interventions import Call


def test_queue_is_reversible_only_in_authorized_isolated_worktree(tmp_path):
    root = tmp_path / "worktree"
    (root / "scripts").mkdir(parents=True)
    (root / ".git").write_text("gitdir: elsewhere")
    (root / "scripts/test").write_text("# maintained runner")
    call = {"command": "python scripts/test fast tests/test_example.py -n 1"}
    assert decide(coding_policy.Policy(coding_policy.ASK_DANGEROUS), Call("Bash", call), roots=[str(root)]).action == "allow"
    assert decide(coding_policy.Policy(coding_policy.READ_ONLY), Call("Bash", call), roots=[str(root)]).action == "deny"
    assert decide(coding_policy.Policy(coding_policy.ASK_DANGEROUS), Call("Bash", {"command": "python /outside/scripts/test fast"}), roots=[str(root)]).action == "ask"
    (root / "scripts/test").unlink()
    (root / "scripts/test").symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_text("other program")
    assert decide(coding_policy.Policy(coding_policy.ASK_DANGEROUS), Call("Bash", call), roots=[str(root)]).action == "ask"


def test_inventory_and_policy_source_reads_do_not_request_mutation_authority():
    assert decide(coding_policy.Policy(coding_policy.READ_ONLY), Call("Bash", {"command": "pyenv versions 2>/dev/null"})).action == "allow"
    assert decide(coding_policy.Policy(coding_policy.ASK_DANGEROUS), Call("Bash", {"command": "pyenv exec arbitrary-program"})).action == "ask"
    assert decide(coding_policy.Policy(coding_policy.READ_ONLY), Call("Bash", {"command": "git log -- src/poolhouse/sentinel/policy.py"})).action == "allow"
    assert decide(coding_policy.Policy(coding_policy.ASK_DANGEROUS), Call("Bash", {"command": "poolhouse-security mode off"})).action != "allow"


@pytest.mark.redteam
@pytest.mark.parametrize("command", [
    "pytest tests/test_one.py", "python -m pytest tests/test_one.py",
    "python3.13 -m pytest", "uv run pytest", "uv run --project . pytest",
    "poetry run pytest", "python -m unittest", "tox", "nox", "pytest --collect-only",
])
def test_consumer_test_commands_follow_execution_permissions_without_a_poolhouse_broker(command, tmp_path):
    decision = decide(coding_policy.Policy(coding_policy.ASK_DANGEROUS),
                      Call("Bash", {"command": command}), roots=[str(tmp_path)])
    assert decision.action != "deny", decision.reason
    assert "scripts/test" not in decision.reason


@pytest.mark.redteam
@pytest.mark.parametrize("command", ["pytest tests/test_one.py", "python -m unittest"])
def test_consumer_tests_still_require_write_permission(command):
    assert decide(coding_policy.Policy(coding_policy.READ_ONLY),
                  Call("Bash", {"command": command})).action == "deny"


@pytest.mark.redteam
@pytest.mark.parametrize("line", [
    "poolhouse-workspace --help", "poolhouse-workspace status", "poolhouse-workspace inbox",
    "poolhouse-workspace inbox --agent own", "poolhouse-workspace inbox --agent=own",
])
def test_workspace_default_identity_selection_needs_no_manual_selector_or_board(line):
    assert workspace_authority(line, "own") is None


@pytest.mark.redteam
@pytest.mark.parametrize("line", [
    "poolhouse-workspace inbox --agent other", "poolhouse-workspace inbox --agent=other",
    "poolhouse-workspace inbox --agent", "poolhouse-workspace inbox --agent=",
    "poolhouse-workspace inbox --agent own --agent other",
    "env POOLHOUSE_WORKSPACE_AGENT=other poolhouse-workspace inbox --agent other",
    "poolhouse-workspace inbox --token-file /tmp/other",
    "poolhouse-workspace inbox --token-file=/tmp/other",
])
def test_workspace_explicit_credential_selection_cannot_change_the_launcher_actor(line):
    assert workspace_authority(line, "own").action == "deny"
