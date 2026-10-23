"""Runtime operations derive their live native caller without identity exports or a caller cwd."""

import os

import pytest
from test_runtime_unmerged import cli_args, reviewed as _reviewed

from poolhouse import (
    authority,
    node_launch,
    person,
    runtime_cli,
    runtime_deploy,
    runtime_source,
    runtime_trust,
    runtime_unmerged,
)
from poolhouse.activity import writer

pytest_plugins = ["node_kit"]
reviewed = _reviewed


@pytest.fixture
def native(reviewed, monkeypatch):
    project, node, _, _, _, _ = reviewed
    for name in (*person.AGENT_MARKERS, authority.AGENT_ENV, authority.SUBAGENT_ENV, "POOLHOUSE_WORKSPACE_TOKEN",
                 "POOLHOUSE_SESSION_ID", "POOLHOUSE_SESSION_HARNESS"):
        monkeypatch.delenv(name, raising=False)
    actor = node.member("runtime-native-root", harness="codex")
    monkeypatch.setenv("CODEX_THREAD_ID", actor.session)
    return project, node, actor, reviewed


def test_native_reviewed_plan_and_install_claim_actor_need_no_exports(native):
    project, _node, actor, original = native
    args = cli_args(original, "--ref", original[4][:12])
    args.review_branch = ""
    plan = runtime_cli.plan_from(args)
    assert not os.environ.get(authority.AGENT_ENV) and not os.environ.get("POOLHOUSE_AGENT")
    assert plan.agent == plan.allow_unmerged.actor == actor.name
    assert plan.commit == plan.allow_unmerged.commit == original[4]
    held = runtime_deploy.acting(plan)
    assert held.name == actor.name and held.client.autostart is False
    assert held.board == plan.allow_unmerged.board
    runtime_source.admit(project.root, plan.commit, allow_unmerged=plan.allow_unmerged)


def test_reviewed_native_operation_is_bound_to_primary_even_outside_it(native, monkeypatch, tmp_path):
    project, _, actor, original = native
    monkeypatch.chdir(tmp_path)
    granted = runtime_unmerged.approve(project.root, original[4])
    runtime_unmerged.validate(granted, project.root, original[4])
    assert granted.actor == actor.name


def test_explicit_plan_name_cannot_replace_the_actual_native_actor(native):
    project, node, _, original = native
    other = node.member("another-root", harness="codex")
    plan = runtime_deploy.Plan(project.root, original[4], None, agent=other.name)
    assert runtime_deploy.acting(plan) is None


def test_native_install_actor_is_derived_for_a_direct_plan(native):
    project, _, actor, original = native
    held = runtime_deploy.acting(runtime_deploy.Plan(project.root, original[4], None))
    assert held is not None and held.name == actor.name


def test_a_supplied_install_session_cannot_replace_the_native_caller(native):
    project, node, actor, original = native
    other = node.member("other-install-owner", harness="codex")
    plan = runtime_deploy.Plan(project.root, original[4], None, agent=actor.name)
    with pytest.raises(runtime_source.DeployError, match="caller changed"):
        runtime_deploy.build_and_switch(plan, builder=lambda *_args: pytest.fail("built as another session"),
                                        who=node.session(other))



def test_unresolved_supplied_session_cannot_start_a_node(native, monkeypatch):
    project, node, actor, original = native
    supplied = node.session(actor)
    supplied.named = ""
    monkeypatch.setattr(supplied.client, "call", lambda *_args, **_kwargs: pytest.fail("used supplied client"))
    plan = runtime_deploy.Plan(project.root, original[4], None, agent=actor.name)
    with pytest.raises(runtime_source.DeployError, match="caller changed"):
        runtime_deploy.build_and_switch(plan, builder=lambda *_args: pytest.fail("built without corroboration"), who=supplied)


def test_node_loss_after_admission_does_not_start_a_node(native, monkeypatch):
    project, node, _, original = native
    current = authority.current_agent

    def stop_after_admission(**options):
        caller = current(**options)
        node.stop()
        return caller

    monkeypatch.setattr(authority, "current_agent", stop_after_admission)
    monkeypatch.setattr(node_launch, "ensure_node", lambda *_a, **_k: pytest.fail("started the daemon"))
    assert runtime_deploy.acting(runtime_deploy.Plan(project.root, original[4], None)) is None


@pytest.mark.parametrize("case", ["child", "retired", "unknown"])
def test_unavailable_native_authority_cannot_take_an_existing_review(native, monkeypatch, case):
    project, node, actor, original = native
    if case == "child":
        child = node.member("runtime-native-child", harness="codex", parent=actor)
        monkeypatch.setenv("CODEX_THREAD_ID", child.session)
    elif case == "retired":
        node.session(actor).retire()
    else:
        monkeypatch.setenv("CODEX_THREAD_ID", "unknown-native")
    with pytest.raises(runtime_source.DeployError):
        runtime_unmerged.approve(project.root, original[4])
    assert runtime_deploy.acting(runtime_deploy.Plan(project.root, original[4], None)) is None


@pytest.mark.parametrize("gate", ["runtime.deploy", "runtime.deploy-unmerged"])
def test_native_caller_cannot_turn_a_person_gate_into_delegation(native, gate):
    project, _, actor, original = native
    authority.set_state([gate], authority.PERSON, by=actor.name)
    with pytest.raises(runtime_source.DeployError):
        runtime_unmerged.approve(project.root, original[4])


@pytest.mark.parametrize("declared", ["child", "unknown"])
def test_an_asserted_name_without_markers_cannot_fall_back_to_anonymous_install(native, monkeypatch, declared):
    project, node, actor, original = native
    child = node.member("markerless-child", harness="codex", parent=actor)
    for name in (*person.AGENT_MARKERS, authority.AGENT_ENV, "POOLHOUSE_WORKSPACE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    plan = runtime_deploy.Plan(project.root, original[4], None, agent=child.name if declared == "child" else "unknown")
    monkeypatch.setattr(runtime_deploy, "prepare_root", lambda: pytest.fail("mutated runtime state"))
    with pytest.raises(runtime_source.DeployError, match="authenticated workspace"):
        runtime_deploy.ensure(plan, builder=lambda *_args: pytest.fail("built without authority"))


def test_outside_cwd_gate_and_audit_use_the_authenticated_source_caller(native, monkeypatch, tmp_path):
    project, _, actor, _ = native
    monkeypatch.chdir(tmp_path)
    assert runtime_trust.authorize("ensure", cwd=project.root) == authority.DELEGATED
    rows = []
    monkeypatch.setattr(writer, "record", lambda _kind, **row: not rows.append(row))
    monkeypatch.setenv(writer.ENV_ACTOR, "agent:forged-label")
    assert runtime_trust.audit("ensure", "a" * 40, "refused", agent="forged-label", source=str(project.root))
    assert rows[0]["actor"] == f"agent:{actor.name}"
    assert rows[0]["meta"]["agent"] == actor.name


def test_failed_native_authentication_never_borrows_an_audit_identity(native, monkeypatch):
    project, _, _, _ = native
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown-native")
    monkeypatch.setenv(writer.ENV_ACTOR, "agent:forged-label")
    rows = []
    monkeypatch.setattr(writer, "record", lambda _kind, **row: not rows.append(row))
    assert runtime_trust.audit("ensure", "a" * 40, "refused", agent="forged-label", source=str(project.root))
    assert rows[0]["actor"] == "agent:unavailable"
    assert rows[0]["meta"]["agent"] == "" and not rows[0]["meta"]["person"]


def test_readonly_runtime_plan_needs_no_workspace_registration(native, monkeypatch):
    project, _, _, _ = native
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown-native")
    args = runtime_cli.GROUP.parser().parse_args(["status", "--checkout", str(project.root)])
    plan = runtime_cli.plan_from(args, deploying=False)
    assert plan.checkout == project.root and plan.agent == ""
