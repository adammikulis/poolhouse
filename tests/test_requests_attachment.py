"""Exact action approvals attach atomically to the existing encrypted request inbox."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse import requests
from poolhouse.requests import store as request_store

KEY = bytes(range(32))
CAPABILITY = "b" * 64
BINDING = "a" * 64


def action(**changes):
    return replace(requests.Ask("tool_call_destructive", "Delete generated cache", "Selected project only",
        ("allow-once", "deny"), origin=requests.Origin(agent="coding-agent", project="selected-project", session="coding-session"),
        key=BINDING, extra=(("action_digest", BINDING), ("request_binding", BINDING))), **changes)


@pytest.fixture
def inbox(tmp_path):
    return requests.Inbox(tmp_path / "requests", key=lambda: KEY)


def attached(inbox, ask=None, **options):
    return requests.raise_request(ask or action(), inbox=inbox, cancellation_secret=CAPABILITY, **options)


def test_repeated_pending_action_retains_id_expiry_and_cancellation_capability(inbox):
    first = attached(inbox)
    second = attached(inbox)
    assert second.id == first.id and second.request.expires == first.request.expires
    assert len(inbox.list()) == 1
    assert second.withdraw()
    assert first.outcome().state == "cancelled"


@pytest.mark.parametrize("choice,state", [("allow-once", "approved"), ("deny", "denied")])
def test_terminal_answers_are_reused_without_a_second_prompt(inbox, choice, state):
    first = attached(inbox)
    inbox.answer(first.id, choice, first.fingerprint, "ui")
    second = attached(inbox)
    assert second.id == first.id and second.outcome().state == state
    assert not inbox.pending()
    assert len(inbox.list()) == 1


def test_expired_action_remains_expired_on_reconnect(tmp_path):
    now = [1000.0]
    held = requests.Inbox(tmp_path / "requests", key=lambda: KEY, clock=lambda: now[0])
    first = attached(held, action(ttl=2))
    now[0] += 3
    second = attached(held)
    assert second.id == first.id and second.outcome().state == "expired"
    assert second.request.expires == first.request.expires


def test_cancelled_action_does_not_reprompt(inbox):
    first = attached(inbox)
    assert first.withdraw()
    assert attached(inbox).outcome().state == "cancelled"
    assert len(inbox.list()) == 1


def test_restart_can_attach_and_cancel_only_with_original_capability(inbox):
    first = attached(inbox)
    restarted = requests.Inbox(inbox.directory, key=lambda: KEY)
    wrong = requests.raise_request(action(), inbox=restarted, cancellation_secret="c" * 64, create=False)
    assert wrong.outcome().state == "denied" and not wrong.withdraw()
    assert inbox.get(first.id).state == "pending"
    resumed = attached(restarted, create=False)
    assert resumed.id == first.id and resumed.withdraw()


def test_attachment_succeeds_at_pending_quota(inbox, monkeypatch):
    first = attached(inbox)
    monkeypatch.setattr(request_store, "MAX_PENDING", 1)
    assert attached(inbox).id == first.id
    unrelated = attached(inbox, action(key="d" * 64, extra=(("action_digest", "d" * 64), ("request_binding", "d" * 64))))
    assert unrelated.outcome().state == "denied"
    assert len(inbox.list()) == 1


@pytest.mark.parametrize("changed", [{"subject": "Different operation"}, {"reason": "Different reason"},
    {"choices": ("deny",)}, {"extra": (("action_digest", BINDING), ("request_binding", BINDING), ("other", "changed"))},
    {"origin": requests.Origin(agent="another-agent")}, {"kind": "tool_call"}])
def test_same_binding_cannot_change_words_origin_kind_or_extra(inbox, changed):
    first = attached(inbox)
    refused = attached(inbox, action(**changed))
    assert refused.outcome().state == "denied"
    assert inbox.get(first.id).state == "pending"
    assert len(inbox.list()) == 1


def test_missing_prior_history_stays_interrupted(inbox):
    first = attached(inbox)
    assert first.withdraw()
    with inbox._edit() as rows:
        rows.clear()
    missing = attached(inbox, create=False)
    assert missing.outcome().state == "denied"
    assert not inbox.list()


@pytest.mark.parametrize("choice", [None, "allow-once", "deny"])
@pytest.mark.parametrize("changed", [{}, {"origin": requests.Origin(agent="forged-agent")}, {"kind": "tool_call"}])
def test_generic_request_cannot_supersede_a_bound_approval(inbox, choice, changed):
    first = attached(inbox)
    if choice:
        inbox.answer(first.id, choice, first.fingerprint, "ui")
    original = inbox.get(first.id)
    forged = requests.raise_request(action(subject="Replace the original operation", extra=(), **changed), inbox=inbox)
    assert forged.outcome().state == "denied"
    assert inbox.get(first.id) == original
    assert len(inbox.list()) == 1


def test_generic_request_cannot_reserve_an_attachment_marker(inbox):
    refused = requests.raise_request(action(), inbox=inbox)
    assert refused.outcome().state == "denied" and not inbox.list()


def test_unbound_preexisting_key_cannot_be_promoted_to_an_approval(inbox):
    original = requests.raise_request(action(extra=()), inbox=inbox)
    refused = attached(inbox)
    assert refused.outcome().state == "denied"
    assert inbox.get(original.id).state == "pending" and len(inbox.list()) == 1


def test_distinct_bound_actions_have_independent_requests(inbox):
    first = attached(inbox)
    second = attached(inbox, action(key="d" * 64, extra=(("action_digest", "e" * 64), ("request_binding", "d" * 64))))
    assert first.id != second.id
    assert first.withdraw() and second.outcome().state == "pending"


def test_stopped_waiter_never_returns_a_preexisting_approval(inbox):
    handle = attached(inbox)
    approved = inbox.answer(handle.id, "allow-once", handle.fingerprint, "ui")
    assert handle.wait(stop=lambda: True).state == "cancelled"
    assert inbox.get(handle.id) == approved
    assert attached(inbox).id == handle.id and len(inbox.list()) == 1


def test_approval_racing_cancellation_does_not_escape_stopped_waiter(inbox):
    handle = attached(inbox)
    def stop():
        inbox.answer(handle.id, "allow-once", handle.fingerprint, "ui")
        return True
    assert handle.wait(stop=stop).state == "cancelled"
    assert inbox.get(handle.id).state == "approved"


def test_failed_current_authority_probe_never_returns_approval(inbox):
    handle = attached(inbox)
    inbox.answer(handle.id, "allow-once", handle.fingerprint, "ui")
    def stop():
        raise RuntimeError("current authority unavailable")
    with pytest.raises(RuntimeError, match="authority unavailable"):
        handle.wait(stop=stop)
    assert inbox.get(handle.id).state == "approved"


@pytest.mark.parametrize("key", ["", "short", "A" * 64, "g" * 64])
def test_attached_action_requires_full_canonical_digest(inbox, key):
    with pytest.raises(ValueError, match="full action digest"):
        attached(inbox, action(key=key))


def test_real_concurrent_processes_create_exactly_one_encrypted_request(tmp_path):
    root = tmp_path / "requests"
    source = str(Path(__file__).resolve().parents[1] / "src")
    script = (f"import sys;sys.path.insert(0,{source!r})\n"
              "from poolhouse import requests\n"
              "from pathlib import Path\n"
              "held=requests.Inbox(Path(sys.argv[1]),key=lambda:bytes(range(32)))\n"
              f"ask=requests.Ask('tool_call_destructive','Delete generated cache','Selected project only',('allow-once','deny'),origin=requests.Origin(agent='coding-agent',project='selected-project',session='coding-session'),key={BINDING!r},extra=(('action_digest',{BINDING!r}),('request_binding',{BINDING!r})))\n"
              f"print(requests.raise_request(ask,inbox=held,cancellation_secret={CAPABILITY!r}).id)\n")
    children = [subprocess.Popen([sys.executable, "-I", "-c", script, str(root)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for _ in range(4)]
    results = [child.communicate(timeout=30) for child in children]
    assert all(child.returncode == 0 for child in children), results
    assert len({stdout.strip() for stdout, _ in results}) == 1
    held = requests.Inbox(root, key=lambda: KEY)
    assert len(held.pending()) == len(held.list()) == 1
    assert BINDING.encode() not in held.path.read_bytes()
    assert json.loads(json.dumps(held.list()[0].to_json()))["state"] == "pending"
