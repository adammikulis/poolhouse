"""Durable subscription delegation survives browser sessions, never revocation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from poolhouse.fleet import studio_epoch
from poolhouse.fleet.studio_authority import StudioAuthority
from poolhouse.studio.store import Conflict

POOL, DEVICE = "a" * 64, "b" * 64
PRINCIPAL = {"id": "owner-browser", "scope": "scope"}


def manifest():
    return {"workflow": {"id": "workflow", "revision": 1, "graph": {"nodes": [
        {"id": "record", "type": "stream.record", "parameters": {"sample": "{}"}}], "edges": []}},
        "source": {"id": "source", "revision": 1, "digest": "source-digest"},
        "destinations": [{"kind": "live"}], "resources": []}


@pytest.fixture
def setup(tmp_path):
    state = SimpleNamespace(context={"pool": POOL, "device": DEVICE, "scope": "scope", "active": True},
                            origin="launch-ticket", resources=True)
    authority = StudioAuthority(tmp_path, current=lambda: deepcopy(state.context), owner=lambda principal: state.origin,
                                resources=lambda grant, node, inputs: state.resources)
    yield authority, state, tmp_path
    authority.close()


def test_start_is_idempotent_and_manifest_immutable(setup):
    authority, _, _ = setup
    value = manifest()
    first = authority.mint(PRINCIPAL, "subscription", value, "start")
    assert authority.mint(PRINCIPAL, "subscription", value, "start") == first
    value["workflow"]["revision"] = 2
    with pytest.raises(Conflict):
        authority.mint(PRINCIPAL, "subscription", value, "start")
    assert authority.get(authority.principal(first))["manifest"]["workflow"]["revision"] == 1


@pytest.mark.parametrize("origin", ["token", "invite", "setup", "", True])
def test_weak_session_or_owner_json_cannot_mint(setup, origin):
    authority, state, _ = setup
    state.origin = origin
    with pytest.raises(PermissionError):
        authority.mint({**PRINCIPAL, "owner": True}, "subscription", manifest(), "start")


@pytest.mark.parametrize("origin", ["launch-ticket", "passphrase", "recovery-file"])
def test_actual_strong_origins_can_delegate(setup, origin):
    authority, state, _ = setup
    state.origin = origin
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    assert authority.authorize(authority.principal(grant), None, {})


def test_read_never_bootstraps_or_recreates_missing_epoch(setup):
    authority, _, root = setup
    assert not (root / studio_epoch.NAME).exists()
    assert not authority.authorize({"id": "forged", "scope": "scope", "grant_id": "0" * 32}, None, {})
    assert not (root / studio_epoch.NAME).exists()
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    (root / studio_epoch.NAME).unlink()
    assert not authority.authorize(authority.principal(grant), None, {})
    assert not (root / studio_epoch.NAME).exists()


def test_restart_and_browser_expiry_keep_service_identity(setup):
    authority, state, root = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    principal = authority.principal(grant)
    state.origin = ""  # Original browser is gone; service never uses its credentials.
    authority.close()
    restarted = StudioAuthority(root, current=lambda: deepcopy(state.context), owner=lambda principal: "",
                                resources=lambda grant, node, inputs: True)
    try:
        assert restarted.authorize(principal, grant["manifest"]["workflow"]["graph"]["nodes"][0], {})
        assert "owner" not in principal and "owner-browser" not in repr(grant)
    finally:
        restarted.close()
    # Fixture close is idempotent at the storage layer.


@pytest.mark.parametrize("field,value", [("pool", "c" * 64), ("device", "d" * 64), ("scope", "other"), ("active", False)])
def test_clone_wrong_device_pool_scope_or_membership_is_denied(setup, field, value):
    authority, state, _ = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    state.context[field] = value
    assert not authority.authorize(authority.principal(grant), None, {})


def test_epoch_rotation_revokes_and_never_replays_old_start(setup):
    authority, _, root = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    studio_epoch.invalidate(root, [POOL], "leave")
    assert not authority.authorize(authority.principal(grant), None, {})
    tombstone = (root / studio_epoch.NAME).read_bytes()
    with pytest.raises(PermissionError):
        authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    assert (root / studio_epoch.NAME).read_bytes() == tombstone
    fresh = authority.mint(PRINCIPAL, "subscription", manifest(), "new-explicit-start")
    assert fresh["epoch"] != grant["epoch"]
    assert authority.authorize(authority.principal(fresh), None, {})
    assert not authority.authorize(authority.principal(grant), None, {})


def test_grant_revoke_is_sticky_and_idempotent(setup):
    authority, _, _ = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    assert authority.revoke(PRINCIPAL, grant["id"]) == authority.revoke(PRINCIPAL, grant["id"])
    assert not authority.authorize(authority.principal(grant), None, {})
    with pytest.raises(PermissionError):
        authority.mint(PRINCIPAL, "subscription", manifest(), "start")


def test_expanded_resource_or_node_cannot_inherit_grant(setup):
    authority, state, _ = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    principal = authority.principal(grant)
    node = deepcopy(grant["manifest"]["workflow"]["graph"]["nodes"][0])
    node["parameters"]["sample"] = '{"injected":"configuration"}'
    assert not authority.authorize(principal, node, {})
    assert not authority.authorize({**principal, "owner": True}, None, {})
    state.resources = False
    assert not authority.authorize(principal, None, {})


def test_revocation_before_commit_creates_no_grant(setup):
    authority, _state, _ = setup
    calls = []
    def resources(grant, node, inputs):
        calls.append(True)
        return len(calls) == 1
    authority.resources = resources
    with pytest.raises(PermissionError):
        authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    assert authority.graph.query("MATCH (n:Node) WHERE n.kind='studio.authority.grant' RETURN n.id AS id") == []


@pytest.mark.parametrize("mutation", ["permission", "symlink", "hardlink", "corrupt", "unknown"])
def test_epoch_tamper_fails_closed(setup, mutation):
    authority, _, root = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    path = root / studio_epoch.NAME
    if mutation == "permission":
        path.chmod(0o644)
    elif mutation in {"symlink", "hardlink"}:
        held = root / "held"
        path.rename(held)
        if mutation == "symlink":
            path.symlink_to(held)
        else:
            path.hardlink_to(held)
    else:
        path.write_text("not-json" if mutation == "corrupt" else '{"version":2,"pools":{}}')
    assert not authority.authorize(authority.principal(grant), None, {})


def test_stopped_daemon_revocation_requires_no_database_open(setup):
    import os
    import subprocess
    import sys
    authority, _, root = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    code = "from pathlib import Path;import sys;from poolhouse.fleet.studio_epoch import invalidate;invalidate(Path(sys.argv[1]),[sys.argv[2]],'security-reset')"
    completed = subprocess.run([sys.executable, "-c", code, str(root), POOL], env=dict(os.environ),
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert not authority.authorize(authority.principal(grant), None, {})


def test_tombstone_is_inert_and_security_revoke_rotates_picker_epoch(setup):
    authority, _, root = setup
    studio_epoch.invalidate(root, [POOL], "leave")
    before = (root / studio_epoch.NAME).read_bytes()
    studio_epoch.invalidate(root, [POOL], "leave")
    assert (root / studio_epoch.NAME).read_bytes() != before
    with pytest.raises(PermissionError):
        studio_epoch.read(root, POOL)
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "explicit-start")
    assert authority.authorize(authority.principal(grant), None, {})


def test_leave_invalidates_before_membership_write(setup, monkeypatch):
    import hashlib

    from poolhouse.fleet import discovery
    authority, state, root = setup
    member = SimpleNamespace(group="example", key=b"key")
    state.context["pool"] = hashlib.sha256(member.key).hexdigest()
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    monkeypatch.setattr(discovery, "memberships", lambda path=None: [member])
    def write(rows, path):
        assert not authority.authorize(authority.principal(grant), None, {})
        raise OSError("test stops before writing membership")
    monkeypatch.setattr(discovery, "_write_memberships", write)
    with pytest.raises(OSError):
        discovery.leave("example", root / "pool.key")


def test_forget_revocation_failure_leaves_credentials_untouched(setup, monkeypatch):
    from poolhouse.fleet import recovery
    _, _, root = setup
    calls = []
    monkeypatch.setattr(recovery, "memberships", lambda path=None: [SimpleNamespace(group="example", key=b"key")])
    monkeypatch.setattr(recovery, "_held", lambda path: {"example": "wrapped-credential"})
    monkeypatch.setattr(recovery, "write_json", lambda *args: calls.append("write"))
    def refuse(*args):
        raise OSError("cannot persist revocation")
    monkeypatch.setattr(studio_epoch, "invalidate", refuse)
    recovery.forget("example", root / "pool.key")
    assert calls == []


def test_invitation_revokes_before_removing_credential(setup, monkeypatch):
    from poolhouse.fleet.invites import Invitations
    authority, state, root = setup
    import hashlib
    key = b"example-key"
    state.context["pool"] = hashlib.sha256(key).hexdigest()
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    invitations = Invitations(lambda: [SimpleNamespace(group="example", key=key)], lambda: ("", ""), authority_root=root)
    invitations.devices["credential"] = {"group": "example"}
    invitations.revoke("credential")
    assert not invitations.devices
    assert not authority.authorize(authority.principal(grant), None, {})
    before = (root / studio_epoch.NAME).read_bytes()
    invitations.revoke("credential")
    assert (root / studio_epoch.NAME).read_bytes() == before


def test_first_picker_only_creates_disabled_epoch_and_start_preserves_pending_selection(setup):
    authority, _, root = setup
    with pytest.raises(PermissionError):
        authority.selection_epoch(PRINCIPAL)
    assert not (root / studio_epoch.NAME).exists()
    selection = authority.selection_epoch(PRINCIPAL, create=True)
    with pytest.raises(PermissionError):
        studio_epoch.read(root, POOL)
    assert authority.graph.query("MATCH (n:Node) WHERE n.kind='studio.authority.grant' RETURN n.id AS id") == []
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    assert grant["epoch"] == selection == authority.selection_epoch(PRINCIPAL)
    studio_epoch.invalidate(root, [POOL], "security-reset")
    assert authority.selection_epoch(PRINCIPAL) != selection
    assert not authority.authorize(authority.principal(grant), None, {})


def test_second_revocation_invalidates_disabled_picker_without_reactivating_old_grant(setup):
    authority, _, root = setup
    grant = authority.mint(PRINCIPAL, "subscription", manifest(), "start")
    studio_epoch.invalidate(root, [POOL], "security-reset")
    selected = authority.selection_epoch(PRINCIPAL, create=True)
    studio_epoch.invalidate(root, [POOL], "invitation-revoked")
    assert authority.selection_epoch(PRINCIPAL) != selected
    renewed = authority.mint(PRINCIPAL, "subscription", manifest(), "renew")
    assert renewed["epoch"] != grant["epoch"]
    assert not authority.authorize(authority.principal(grant), None, {})


@pytest.mark.parametrize("reason", ["start", "source-selection", "arbitrary"])
def test_invalidation_cannot_select_activation_or_picker_operation(setup, reason):
    _, _, root = setup
    with pytest.raises(ValueError):
        studio_epoch.invalidate(root, [POOL], reason)
    assert not (root / studio_epoch.NAME).exists()
