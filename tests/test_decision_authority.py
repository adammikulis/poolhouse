"""Only the fixed owning issuer may return a resident model source."""
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from poolhouse import home
from poolhouse.fleet import tls
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.membership import Roster
from poolhouse.serve import decision_authority as authority


def source():
    return {"name": "fixed-model", "base_dir": "/owned/base", "tokenizer_dir": "/owned/tokenizer",
            "head": "/owned/head.safetensors", "lora_dir": None, "pointer_dim": 128,
            "temperature": 1.0, "dtype": "bfloat16", "details": None}


def model():
    return {"source": source(), "identity": "a" * 64, "memory_bytes": 1_000_000, "calibration": None}


@pytest.fixture
def enrolled():
    identity = tls.identity(tls.device_directory(), "test-device")
    home.state("machine-id").write_text("test-machine")
    member = mint_pool("test-pool", home.state("pool.key"))
    Roster(member.key).enrol(identity.beacon, "test-device", "self")
    owned = SimpleNamespace(issuer={"endpoint": "https://127.0.0.1:7443", "certificate": identity.beacon,
                            "machine": "test-machine", "pool": hashlib.sha256(member.key).hexdigest(),
                            "runtime": "b" * 64, "issuer_instance": "c" * 64})
    return owned, identity, member


def test_existing_enrolled_identity_is_read_only_and_revocation_is_current(enrolled, monkeypatch):
    owned, expected, member = enrolled
    monkeypatch.setattr(tls, "local", lambda: pytest.fail("must never create a TLS identity"))
    actual, key = authority.existing(owned)
    assert actual == expected and key == member.key
    assert authority.existing(owned) == (actual, key)
    Roster(member.key).revoke(expected.fingerprint, "self")
    with pytest.raises(PermissionError, match="enrolled"):
        authority.existing(owned)


@pytest.mark.parametrize("mutation", ["missing", "symlink", "machine", "certificate", "expiry", "pool"])
def test_existing_identity_never_creates_adopts_or_falls_back(enrolled, mutation, tmp_path, monkeypatch):
    owned, identity, _member = enrolled
    monkeypatch.setattr(tls, "identity", lambda *_args, **_kwargs: pytest.fail("must not mint identity"))
    if mutation == "missing":
        identity.keyfile.unlink()
    elif mutation == "symlink":
        moved = tmp_path / "moved.key"
        identity.keyfile.rename(moved)
        identity.keyfile.symlink_to(moved)
    elif mutation == "machine":
        home.state("machine-id").write_text("another-machine")
    elif mutation == "certificate":
        owned.issuer["certificate"] = "another-certificate"
    elif mutation == "expiry":
        path = tls.device_directory() / "cert.json"
        value = json.loads(path.read_text())
        value["not_after"] = time.time() - 1
        path.write_text(json.dumps(value))
    else:
        owned.issuer["pool"] = "f" * 64
    with pytest.raises((PermissionError, OSError, tls.TlsUnavailable)):
        authority.existing(owned)


@pytest.mark.parametrize("extra", [{"module": "untrusted"}, {"url": "https://example.invalid"}])
def test_issuer_model_cannot_choose_code_or_endpoint(extra):
    with pytest.raises(ValueError):
        authority.model_reply({**model(), "source": {**source(), **extra}})


def test_fixed_reply_requires_typed_positive_finite_settings():
    assert authority.model_reply(model()).source == source()
    for field, value in [("head", "relative/file"), ("temperature", float("nan")), ("pointer_dim", True)]:
        with pytest.raises(ValueError):
            authority.model_reply({**model(), "source": {**source(), field: value}})


@pytest.fixture
def authorized(enrolled, monkeypatch):
    owned, _identity, _member = enrolled
    calls = []
    owned.instance = "f" * 64
    owned.sign = lambda body: calls.append(body) or {**body, "proof": "valid-proof"}
    state = {"reply_change": {}, "model_change": {}, "denied": False}
    class Transport:
        def __init__(self, member, current, *, identity):
            assert identity.der
            assert member.base_url == owned.issuer["endpoint"]
            self.current = current
        def request(self, action, body):
            assert action == "decision-authorize"
            self.current()
            return {"authorized": not state["denied"], "permit_id": body["permit_id"],
                    "request_id": body["request_id"], "record_digest": body["record_digest"],
                    "model_digest": body["model_digest"], "runtime": body["runtime_digest"],
                    "stage": body["stage"], "model": {**model(), **state["model_change"]}, **state["reply_change"]}
    monkeypatch.setattr(authority, "MemberExecution", Transport)
    value = {"permit_id": "permit", "request_id": "request", "model_digest": "e" * 64,
             "record": {"question": "safe", "state": "data", "options": {"a": "first", "b": "second"}}}
    return authority.DecisionAuthorization(owned, value, {"pid": 81, "started": 1.0}), calls, state, owned


def test_every_stage_rechecks_current_enrollment_and_echoed_identity(authorized):
    authorization, calls, _state, owned = authorized
    for stage in ("queue", "load", "compute", "result", "result"):
        assert authorization(stage).identity == "a" * 64
    assert {call["stage"] for call in calls} == {"queue", "load", "compute", "result"}
    assert all(call["model_digest"] == "e" * 64 for call in calls)
    home.state("machine-id").write_text("replaced-machine")
    with pytest.raises(PermissionError):
        authorization("compute")
    owned.sign = lambda body: (_ for _ in ()).throw(PermissionError("issuer ended"))
    with pytest.raises(PermissionError, match="ended"):
        authorization("result")


@pytest.mark.parametrize("key,value", [("stage", "other"), ("request_id", "other"),
                                      ("record_digest", "f" * 64), ("model_digest", "f" * 64),
                                      ("runtime", "f" * 64), ("authorized", False)])
def test_substituted_or_denied_reply_never_yields_model(authorized, key, value):
    authorization, _calls, state, _owned = authorized
    state["reply_change"] = {key: value}
    with pytest.raises(PermissionError):
        authorization("compute")
