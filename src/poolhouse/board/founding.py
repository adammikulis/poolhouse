"""A pool is founded for a person: the node drafts the charter, the person's key signs it, the node takes it back (docs/pools-and-serving.md, 2 and 3).

Nothing here signs for anyone. The signature that makes the pool true is the person's admin key, put to the person where only the
person can answer: the operating system's own prompt on this computer (the key is this computer's, `computer_key`), or a phone's card
(docs/phone-approval.md, docs/design-phone-free-pools.md). `Admin` is that key. How it counts, the node says: the draft of the charter
carries the claim the node derives for it, and the node takes no pool whose charter lists no key that counts. Every refusal is a `Refusal`:
one sentence an agent passes on, what is needed and where the person taps or answers.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from poolhouse import errors, person
from poolhouse.board import approval, computer_key, grants, phone, presence
from poolhouse.board.client import Client, Denied, NodeError
from poolhouse.board.session import Session
from poolhouse.board.terminal import terminal_id

__all__ = ["Admin", "Founded", "Refusal", "Terms", "default_admin", "found", "found_here", "govern", "pool_session", "with_card"]

POOL_GRANT_LASTS = "10m"
CARD_WAIT_S = 120.0
POLICIES = ("open", "secure")
NOT_COUNTED = ("This computer cannot ask you at its own prompt from here, so a key it made would count for nothing in a pool: run Poolhouse "
               "installed on your own account, or approve from your phone.")
FOR_THE_PERSON = ("The computer's own prompt is for you to answer: run `poolhouse-pool found NAME` in a terminal of your own; an agent cannot "
                  "answer it for you.")


class Refusal(errors.Denied):
    """Founding could not go on; ``say`` is one sentence for the person, ``needs`` what is missing (`approval`, `device`, `time`, `none`),
    ``where`` where the person taps or answers, and ``then`` what the agent does next (`wait`, `retry`, `ask-owner`, `stop`)."""

    def __init__(self, say: str, *, needs: str = "none", where: str = "", then: str = "ask-owner") -> None:
        super().__init__(say)
        self.say, self.needs, self.where, self.then = say, needs, where, then


@dataclass(frozen=True, slots=True)
class Admin:
    """The person's key that governs the pool: it signs each act itself, so the person is asked by whatever stands behind ``key``: the
    computer's prompt for a `ComputerKey`, which has an ``approve``, the phone's card for the grant of any other."""

    key: grants.Signs
    person: str = "owner"
    label: str = "the owner's key"
    about: str = "the owner's key"


@dataclass(frozen=True, slots=True)
class Founded:
    """What founding did: the pool's id, name, policy and epoch, and whether it was already there."""

    pool: str
    name: str
    policy: str
    epoch: int
    already: bool = False

    def say(self) -> str:
        """The sentence for the person."""
        word = "is already" if self.already else "is now"
        return f"This device {word} in the pool {self.name} ({self.policy})."


def _held(session: Session) -> list[str]:
    return [str(k["fingerprint"]) for k in session.client.call("person_keys")["keys"]]


def _refused(err: Exception, *, needs: str = "approval", where: str = "") -> Refusal:
    """The node's refusal as one sentence: the words after its code."""
    said = str(err)
    while said.split(": ", 1)[0] in ("denied", "invalid", "quota") and ": " in said:
        said = said.split(": ", 1)[1]
    return Refusal(said, needs=needs, where=where, then="stop" if needs == "approval" else "ask-owner")


def _enrolled(session: Session, admin: Admin, voice: presence.Voice) -> None:
    """The admin key is a key this device's node holds: the first key after the person says yes to the node's own question."""
    held = _held(session)
    if grants.fingerprint(admin.key.public) in held:
        return
    if held:
        raise Refusal("This device already trusts a different key of yours; add this one from your phone, or use that one.", needs="device",
                      where="the Poolhouse app on your phone")
    try:
        grants.enrol_first(session.client, session.token, admin.key, admin.about, voice)
    except NodeError as err:
        raise _refused(err, where="the prompt this computer shows") from None


def _live(session: Session, name: str) -> bool:
    return any(g.action == "pool_create" and g.status == "live" and g.mine and g.scope["resource"] in ("*", name) for g in grants.listing(session).grants)


def _card(session: Session, ask: approval.Ask, note: str, wait_s: float, voice: presence.Voice) -> None:
    """Ask the person's phone for the grant ``ask`` once and wait for the tap."""
    made = approval.request(session, approval.wire([ask], note=note))
    if made.code:
        voice.say(f"Your phone will ask which number I show you: {made.code}.")
    if made.wait(wait_s) != "approved":
        waiting = made.state in (approval.PENDING, approval.HELD)
        raise Refusal(made.message(), needs="approval" if waiting else "none", where="your phone", then="wait" if waiting else "stop")


def with_card[T](session: Session, send: Callable[[], T], *, wait_s: float = CARD_WAIT_S, voice: presence.Voice = presence.SILENT) -> T:
    """``send()``; when the node says a grant is missing and a phone is linked, ask the phone for exactly that grant and send again after the tap."""
    try:
        return send()
    except NodeError as err:
        if not err.grant or not _phone_linked(session):
            raise
        ask = approval.Ask(str(err.grant["action"]), str(err.grant["resource"]), lasts=POOL_GRANT_LASTS, uses=1)
        _card(session, ask, f"the node asked for {ask.action}", wait_s, voice)
        return send()


def _phone_linked(session: Session) -> bool:
    return any(p["state"] != "removed" for p in phone.phones(session))


def _allowed(session: Session, admin: Admin, name: str, wait_s: float, voice: presence.Voice) -> None:
    """The session holds the grant to make this pool: from the phone's card when a phone is linked, else signed by the admin key."""
    if _live(session, name):
        return
    if _phone_linked(session):
        _card(session, approval.Ask("pool_create", name, lasts=POOL_GRANT_LASTS, uses=1), f"making the pool {name}", wait_s, voice)
        return
    _enrolled(session, admin, voice)
    want = grants.Want("pool_create", {"kind": "session", "name": session.name}, name, grants.listing(session).device, 600_000, 1)
    try:
        grants.issue(session, admin.key, want)
    except (NodeError, RuntimeError) as err:
        raise _refused(err, where="the question this computer asks you") from None


def _draft(session: Session, admin: Admin, name: str, policy: str) -> dict[str, Any]:
    """The node's draft of the charter of the admin key: the level of the key is what the node derives."""
    key = {"key": admin.key.public.hex(), "person": admin.person, "label": admin.label}
    return dict(session.client.call("pool_draft_found", "", session.token, title=name, policy=policy, project="", keys=[key]))


def _approved(session: Session, key: computer_key.ComputerKey, draft: dict[str, Any], terms: Terms) -> None:
    """The person says yes at this computer's own prompt for the card the node drafted: that yes enrols the key and is its ticket."""
    card = draft.get("card")
    if not isinstance(card, str):
        raise Refusal(str(draft.get("card_why") or "This change cannot be confirmed at the computer's prompt."), needs="none", then="stop")
    enrolled = grants.fingerprint(key.public) in _held(session)
    try:
        key.approve(card, enrolled=enrolled, voice=terms.voice)
    except Denied as err:
        if not enrolled:
            key.forget()
        raise Refusal(str(err), needs="approval", where="the prompt this computer shows", then="stop") from None


def _charter(admin: Admin, draft: dict[str, Any]) -> dict[str, Any]:
    """The node's draft of the charter with the admin key's acceptance filled in, signed outside the node."""
    charter = draft["charter"]
    for wanted in draft["accept"]:
        listed = next(k for k in charter["admins"] if k["key"] == wanted["key"])
        listed["acceptance"] = admin.key.sign_bytes(bytes.fromhex(wanted["sign_bytes"])).hex()
    return dict(charter)


def _already(status: dict[str, Any]) -> Founded | None:
    gov = status.get("governance", {})
    if gov.get("mode") != "governed":
        return None
    return Founded(str(status["pool"]), str(gov["name"]), str(status["policy"]), int(gov["epoch"]), already=True)


@dataclass(frozen=True, slots=True)
class Terms:
    """How founding waits on the person: ``voice`` says where they answer (the terminal the node asks on and the words told to them) and
    ``wait_s`` how long the phone's card is waited for."""

    voice: presence.Voice = presence.SILENT
    wait_s: float = CARD_WAIT_S


def found(session: Session, admin: Admin, name: str, policy: str = "secure", terms: Terms | None = None) -> Founded:
    """Found the pool ``name`` of ``policy`` on this device, governed by the person's ``admin`` key; the charter the key accepts names this
    device as the pool's first member, so accepting it is the whole of founding.

    A device that is already in a founded pool answers with that pool and changes nothing. ``terms`` says how the person is waited for.
    Raises `Refusal`: a key that counts for nothing, a no, a question nobody answered.
    """
    terms = terms or Terms()
    status = session.client.call("pool_status", "", session.token)
    if (done := _already(status)) is not None:
        return done
    if policy not in POLICIES:
        raise Refusal("A pool lets devices join by themselves (open) or with a code (secure); say which.", needs="none")
    try:
        draft = _draft(session, admin, name, policy)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    approve = isinstance(admin.key, computer_key.ComputerKey)
    if not draft["counting"]:
        if approve:
            admin.key.forget()
        raise Refusal(NOT_COUNTED, needs="device", where="your own account's Poolhouse, or the Poolhouse app on your phone")
    if approve:
        _approved(session, admin.key, draft, terms)
    else:
        _allowed(session, admin, name, terms.wait_s, terms.voice)
    try:
        charter = _charter(admin, draft)
        made = session.client.call("pool_found", "", session.token, charter=charter)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    return Founded(str(made["pool"]), str(made["name"]), str(made["policy"]), int(made["epoch"]))


def default_admin(session: Session) -> Admin:
    """The key this computer offers a new pool: a new key of its own, sealed under the keystore, that signs under the person's yes at the
    computer's prompt. Only the person's own session has one; a process an agent started is refused, and so is a computer with no keystore."""
    if person.marked():
        raise Refusal(FOR_THE_PERSON, needs="approval", where="a terminal of your own", then="ask-owner")
    try:
        key = computer_key.PoolKeys(session.client.state, session.client, session.token).make()
    except computer_key.NoKeystore as err:
        raise Refusal(str(err), needs="device", where="this computer's keystore", then="stop") from None
    return Admin(key, "owner", "this computer's key", "this computer's key for a pool")


def pool_session(client: Client, *, person_session: bool = False) -> Session:
    """A session of the pool board of this device (the one that may change the pool), as the node knows the caller. The person's own
    session (``person_session``) is the only one the node lets raise the computer's prompt."""
    native = f"python-api-{terminal_id()}"
    if person_session:
        got = client.call("register_person", "pool", "", session=native)
    else:
        got = client.call("register", "pool", "", model="poolhouse-api", harness="poolhouse-api", session=native)
    return Session(client, "pool", got["token"], got["name"])


def device_name() -> str:
    """A name for a pool that nobody named: this computer's, in the letters a pool name may hold."""
    host = "".join(c if c.isalnum() or c in "-_" else " " for c in socket.gethostname().split(".")[0]).strip()
    return host[:40] or "home"


def found_here(client: Client, name: str | None, policy: str, *, admin: Admin | None = None, voice: presence.Voice = presence.SILENT) -> Founded:
    """What an agent does when its person says "make a pool": found it on the device ``client`` reaches, with ``admin`` or the key the device can offer."""
    session = pool_session(client, person_session=admin is None)
    return found(session, admin or default_admin(session), name or device_name(), policy, Terms(voice=voice))


def govern(client: Client, kind: str, body: dict[str, Any], *, voice: presence.Voice = presence.SILENT) -> dict[str, Any]:
    """Have this computer's key make the act of ``kind`` the node drafts from ``body`` (a removal, a new device): the person says yes at the
    computer's prompt for exactly that act, the key signs it, and the pool takes it. The node's reply (`status`, `act`)."""
    if person.marked():
        raise Refusal(FOR_THE_PERSON.replace("found NAME", "remove DEVICE"), needs="approval", where="a terminal of your own", then="ask-owner")
    session = pool_session(client, person_session=True)
    try:
        draft = session.client.call("pool_draft", "", session.token, kind=kind, body=body)
        status = session.client.call("pool_status", "", session.token)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    card = draft.get("card")
    if not isinstance(card, str):
        raise Refusal(str(draft.get("card_why") or "This change cannot be confirmed at the computer's prompt."), needs="none", then="stop")
    listed = {str(k["fingerprint"]) for k in status["governance"]["admins"] if k["role"] == "admin"}
    key = computer_key.PoolKeys(client.state, client, session.token).find(listed)
    if key is None:
        raise Refusal("No key of this computer governs this pool, so it cannot make that change here.", needs="device", where="the computer that founded the pool", then="stop")
    try:
        key.approve(card, enrolled=True, voice=voice)
    except Denied as err:
        raise Refusal(str(err), needs="approval", where="the prompt this computer shows", then="stop") from None
    act = dict(draft["act"])
    act["signatures"] = [{"kind": "admin", "key": key.public.hex(), "sig": key.sign_bytes(bytes.fromhex(draft["sign_bytes"])).hex()}]
    try:
        return dict(session.client.call("pool_submit", "", session.token, act=act))
    except NodeError as err:
        raise _refused(err, needs="none") from None
