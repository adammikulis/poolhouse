"""Only a linked phone's signature makes a message the person's, checked when it is delivered."""

from __future__ import annotations

import dataclasses
import json
import time

import pytest
from phone_kit import Key, linked

from poolhouse import phone_mailbox
from poolhouse.phone_mailbox import Refused, Store

MAIN = "claude-0f3a"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "phones")


def _planted(store, target, signed, ident=None):
    """Write a queued message the way another program writing the folder would."""
    import hashlib

    ident = ident or "m-" + hashlib.sha256(signed.bytes()).hexdigest()[:20]
    folder = store.root / "mail" / target
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ident}.json").write_text(json.dumps({"version": 1, **dataclasses.asdict(signed)}))


def _dropped(store, session=MAIN):
    return [e["text"] for e in store.events(session) if e["type"] == "dropped"]


def test_a_signed_message_is_delivered_once_with_its_phone(store):
    key = linked(store)
    ident = store.send(MAIN, key.message(MAIN, "run the tests"))
    assert store.waiting(MAIN) == 1
    taken = store.claim(MAIN)
    assert [(m.id, m.text, m.phone.name) for m in taken] == [(ident, "run the tests", "Pixel")]
    assert store.claim(MAIN) == []
    assert [e["type"] for e in store.events(MAIN)] == ["message", "delivered"]


def test_a_message_signed_by_another_key_is_never_delivered(store):
    key = linked(store)
    stranger = Key()
    stranger.phone = key.phone
    with pytest.raises(Refused, match="signature"):
        store.send(MAIN, stranger.message(MAIN, "hi"))
    _planted(store, MAIN, stranger.message(MAIN, "do something"))
    assert store.claim(MAIN) == []
    assert "signature" in _dropped(store)[0]


def test_changed_words_a_moved_message_and_an_old_one_are_dropped(store):
    key = linked(store)
    good = key.message(MAIN, "stop the job")
    _planted(store, MAIN, dataclasses.replace(good, body=json.dumps({"text": "delete everything"})))
    assert store.claim(MAIN) == []
    _planted(store, "claude-other", good)
    assert store.claim("claude-other") == []
    assert "not for a message to this session" in _dropped(store, "claude-other")[0]
    _planted(store, MAIN, key.message(MAIN, "yesterday", at=time.time() - 2 * 86400))
    assert store.claim(MAIN) == []
    assert any("more than a day" in each for each in _dropped(store))


def test_a_signature_for_another_request_is_not_a_message(store):
    key = linked(store)
    with pytest.raises(Refused, match="not for a message"):
        store.send(MAIN, key.signed("POST", f"/phone/v1/sessions/{MAIN}/stop", {"text": "x"}))


def test_a_delivered_message_copied_back_is_not_delivered_again(store):
    key = linked(store)
    signed = key.message(MAIN, "once only")
    store.send(MAIN, signed)
    assert len(store.claim(MAIN)) == 1
    _planted(store, MAIN, signed)
    assert store.claim(MAIN) == []
    with pytest.raises(Refused, match="already delivered"):
        store.send(MAIN, signed)


def test_removing_a_phone_voids_what_it_queued(store):
    key, kept = linked(store), linked(store, "Tablet")
    store.send(MAIN, key.message(MAIN, "from the lost phone"))
    assert store.unlink(key.phone)
    assert store.claim(MAIN) == []
    assert "not linked" in _dropped(store)[0]
    assert list(store.phones()) == [kept.phone]


def test_limits_on_phones_text_waiting_and_session_names(store):
    key = linked(store)
    with pytest.raises(Refused, match="characters"):
        store.send(MAIN, key.message(MAIN, "x" * (phone_mailbox.MOST_TEXT + 1)))
    for bad in ("claude-../../etc", "../x", "claude-", "other-1"):
        with pytest.raises(Refused, match="session id"):
            store.send(bad, key.message(bad, "hi"))
    for number in range(phone_mailbox.MOST_WAITING):
        store.send(MAIN, key.message(MAIN, f"message {number}"))
    with pytest.raises(Refused, match="too many"):
        store.send(MAIN, key.message(MAIN, "one more"))
    for number in range(phone_mailbox.MOST_PHONES - 1):
        linked(store, f"p{number}")
    with pytest.raises(Refused, match="remove one"):
        linked(store, "ninth")


def test_a_key_that_is_not_p256_links_nothing(store):
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    other = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.DER,
                                                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    for bad in (base64.b64encode(other).decode(), "not base64!", "A" * 400):
        with pytest.raises(Refused, match="P-256"):
            store.link("x", bad)
    assert store.phones() == {}


def test_pool_use_is_off_until_it_is_switched_on(store):
    key = linked(store)
    assert store.phones()[key.phone].pool_use is False
    store.change(key.phone, pool_use=True)
    assert store.phones()[key.phone].pool_use is True


def test_only_the_asked_phone_signature_answers_an_open_question(store):
    key = linked(store)
    question = store.ask(MAIN, [{"question": "Which?", "options": [{"label": "A"}]}], 60)
    stranger = Key()
    stranger.phone = key.phone
    with pytest.raises(Refused, match="signature"):
        store.answer(MAIN, question, stranger.answer(MAIN, question, {"Which?": "A"}))
    with pytest.raises(Refused, match="not for an answer"):
        store.answer(MAIN, question, key.message(MAIN, "A"))
    assert store.answered(MAIN, question) is None
    store.answer(MAIN, question, key.answer(MAIN, question, {"Which?": "A"}))
    assert store.answered(MAIN, question)[0] == {"Which?": "A"}
    store.close_question(MAIN, question, "phone")
    with pytest.raises(Refused, match="not open"):
        store.answer(MAIN, question, key.answer(MAIN, question, {"Which?": "A"}))


def test_the_audit_names_who_and_never_the_words(store):
    key = linked(store)
    store.send(MAIN, key.message(MAIN, "a very private sentence"))
    lines = (store.root / "audit.jsonl").read_text()
    assert key.phone in lines and MAIN in lines and "private sentence" not in lines


def test_the_folder_is_the_one_agents_are_kept_out_of():
    from poolhouse import protectedwrites

    assert Store().root.parts[-2:] == ("person", "phones")
    assert protectedwrites.SECRETS.search("/home/x/.poolhouse/person/phones/phones.json")
