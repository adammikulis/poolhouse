"""A pool is founded for a person: the node drafts the charter, the person's key signs it, the node takes it back (docs/pools-and-serving.md, 2 and 3).

Nothing here signs for anyone. The signature that makes the pool true is the person's admin key, put to the person where only the
person can answer: the operating system's own prompt on this computer (the key is this computer's, `computer_key`: the node makes it, the
person's yes at the prompt enrols it and hands it to the person's session once), or a phone's card (docs/phone-approval.md,
docs/design-phone-free-pools.md). `Admin` is that key when it is a phone's or another device's. How a key counts, the node says: the draft
of the charter carries the claim the node derives for it, and the node takes no pool whose charter lists no key that counts. Every refusal
is a `Refusal`: one sentence an agent passes on, what is needed and where the person taps or answers.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from poolhouse import errors, keystore
from poolhouse.board import approval, computer_key, credentials, grants, phone, presence
from poolhouse.board.client import Client, Denied, NodeError
from poolhouse.board.session import Session
from poolhouse.board.terminal import terminal_id
from poolhouse.fleet.onboard.signing import KeyStoreError

__all__ = ["Admin", "Founded", "NoComputerKey", "Refusal", "Terms", "found", "found_here", "found_on_computer", "govern", "pool_session", "with_card", "yes_for"]

POOL_GRANT_LASTS = "10m"
CARD_WAIT_S = 120.0
POLICIES = ("open", "secure")
NOT_COUNTED = ("That key is a typed yes, which counts for nothing on a computer that can ask you at its own prompt: found the pool without naming "
               "a key, and this computer makes its own.")
FOR_THE_PERSON = ("This process cannot ask you at the computer's own prompt: run the command in a terminal of your own. An agent that can ask "
                  "is not refused; it only cannot answer for you.")


class Refusal(errors.Denied):
    """Founding could not go on; ``say`` is one sentence for the person, ``needs`` what is missing (`approval`, `device`, `time`, `none`),
    ``where`` where the person taps or answers, and ``then`` what the agent does next (`wait`, `retry`, `ask-owner`, `stop`)."""

    def __init__(self, say: str, *, needs: str = "none", where: str = "", then: str = "ask-owner") -> None:
        super().__init__(say)
        self.say, self.needs, self.where, self.then = say, needs, where, then


class NoComputerKey(Refusal):
    """No key of this computer governs the pool: a phone does, and its card is the way."""


@dataclass(frozen=True, slots=True)
class Admin:
    """A key that is not this computer's and governs the pool (a phone's): it signs each act itself, so the person is asked by whatever stands
    behind ``key``, the phone's card for the grant."""

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
        # only the person's own session puts the question to the person
        grants.enrol_first(session.client, pool_session(session.client, person_session=True).token, admin.key, admin.about, voice)
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


def _draft(session: Session, key: dict[str, Any], name: str, policy: str) -> dict[str, Any]:
    """The node's draft of the charter of the key ``key`` names (a key someone holds, or ``computer`` for the key the node makes)."""
    try:
        return dict(session.client.call("pool_draft_found", "", session.token, title=name, policy=policy, project="", keys=[key]))
    except NodeError as err:
        raise _refused(err, needs="none") from None


def _card_of(draft: dict[str, Any]) -> str:
    """The id of the card the node drafted, or the refusal that says why there is none."""
    card = draft.get("card")
    if not isinstance(card, str):
        raise Refusal(str(draft.get("card_why") or "This change cannot be confirmed at the computer's prompt."), needs="none", then="stop")
    return card


def _charter(key: grants.Signs, draft: dict[str, Any]) -> dict[str, Any]:
    """The node's draft of the charter with the admin key's acceptance filled in, signed outside the node."""
    charter = draft["charter"]
    for wanted in draft["accept"]:
        listed = next(k for k in charter["admins"] if k["key"] == wanted["key"])
        listed["acceptance"] = key.sign_bytes(bytes.fromhex(wanted["sign_bytes"])).hex()
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


def _submit(session: Session, charter: dict[str, Any]) -> Founded:
    try:
        made = session.client.call("pool_found", "", session.token, charter=charter)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    return Founded(str(made["pool"]), str(made["name"]), str(made["policy"]), int(made["epoch"]))


def _checked(session: Session, policy: str) -> Founded | None:
    """The pool this device is in already, if it is in one; else None. Raises `Refusal` for a policy that is neither."""
    if (done := _already(session.client.call("pool_status", "", session.token))) is not None:
        return done
    if policy not in POLICIES:
        raise Refusal("A pool lets devices join by themselves (open) or with a code (secure); say which.", needs="none")
    return None


def found(session: Session, admin: Admin, name: str, policy: str = "secure", terms: Terms | None = None) -> Founded:
    """Found the pool ``name`` of ``policy`` on this device, governed by a key that is not this computer's (a phone's): the charter the key
    accepts names this device as the pool's first member, so accepting it is the whole of founding.

    A device that is already in a founded pool answers with that pool and changes nothing. ``terms`` says how the person is waited for.
    Raises `Refusal`: a key that counts for nothing, a no, a question nobody answered.
    """
    terms = terms or Terms()
    if (done := _checked(session, policy)) is not None:
        return done
    draft = _draft(session, {"key": admin.key.public.hex(), "person": admin.person, "label": admin.label}, name, policy)
    if not draft["counting"]:
        raise Refusal(NOT_COUNTED, needs="none")
    _allowed(session, admin, name, terms.wait_s, terms.voice)
    return _submit(session, _charter(admin.key, draft))


def found_on_computer(session: Session, name: str, policy: str = "secure", voice: presence.Voice = presence.SILENT, *, ks: keystore.Keystore | None = None) -> Founded:
    """Found the pool ``name`` with a key this computer makes: the node drafts the charter with a key it makes for the card, the person's one
    yes at the computer's own prompt enrols that key, is its ticket and hands its secret to this session once, the key signs the charter's
    acceptance under the ticket, and the node takes the pool. Where the computer can raise no prompt of its own the pool says it is approved
    by a typed yes, and the question is put on the person's terminal."""
    if (done := _checked(session, policy)) is not None:
        return done
    keys = computer_key.PoolKeys(session.client.state, session.client, session.token, ks=ks)
    draft = _draft(session, {"computer": True, "person": "owner", "label": "this computer's key", "sealed": keys.sealed()}, name, policy)
    if not draft["counting"]:
        raise Refusal(NOT_COUNTED, needs="none")
    try:
        said = presence.ask_card(session.client, session.token, "pool_key", presence.Card(_card_of(draft)), voice)
    except Denied as err:
        raise Refusal(str(err).removeprefix("denied: "), needs="approval", where="the prompt this computer shows", then="stop") from None
    try:
        key = keys.adopt(bytes.fromhex(str(said["seed"])))
    except (KeyStoreError, OSError, ValueError, KeyError) as err:
        raise Refusal(f"This computer could not keep the key it made for the pool: {err}", needs="device", where="this computer's keystore", then="stop") from None
    key.ticket = str(said["secret"])
    try:
        return _submit(session, _charter(key, draft))
    except Refusal:
        key.forget()
        raise


def yes_for(session: Session, op: str, args: dict[str, Any], voice: presence.Voice = presence.SILENT, *, ks: keystore.Keystore | None = None) -> dict[str, Any]:
    """The person's yes at the computer's own prompt for the operation ``op`` (`add_device` or `join_pool`) with ``args``, as the params the call then
    carries: the ticket, and for a window the act this computer's key signs under that same ticket, which lets this device admit what proves the window's
    code (so a pool approved by a typed yes, or a Mac whose 30 days have lapsed, still adds a device). The person's own session holds no grant; it
    opens a window or joins a pool with this yes for exactly that."""
    try:
        drawn = session.client.call("op_draft", "", session.token, op=op, args=args)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    try:
        ticket = str(presence.ask_card(session.client, session.token, "card", presence.Card(_card_of(drawn)), voice)["secret"])
    except Denied as err:
        raise Refusal(str(err), needs="approval", where="the prompt this computer shows", then="stop") from None
    params: dict[str, Any] = {"ticket": ticket}
    window = drawn.get("window")
    if isinstance(window, dict) and (key := _pool_key(session, ks)) is not None:
        key.ticket = ticket
        act = dict(window["act"])
        act["signatures"] = [{"kind": "admin", "key": key.public.hex(), "sig": key.sign_bytes(bytes.fromhex(window["sign_bytes"])).hex()}]
        params["window"] = act
    return params


def _pool_key(session: Session, ks: keystore.Keystore | None) -> computer_key.ComputerKey | None:
    """The key of this computer that governs the pool this device is in, if there is one."""
    status = session.client.call("pool_status")
    listed = {str(k["fingerprint"]) for k in status["governance"].get("admins", []) if k["role"] == "admin"}
    key = computer_key.PoolKeys(session.client.state, session.client, session.token, ks=ks).find(listed)
    return key.use(session.token) if key is not None else None


def pool_session(client: Client, *, person_session: bool = False) -> Session:
    """A session of the pool board of this device (the one that may change the pool), as the node knows the caller. The person's own
    session (``person_session``) is registered with the secret the node keeps for the person, which the harness's read deny keeps from a process an agent
    started (a process outside that deny can read it: see `credentials.person_secret`), and it is the only one the node lets raise the computer's prompt."""
    native = f"python-api-{terminal_id()}"
    if person_session:
        try:
            got = client.call("register_person", "pool", "", session=native, proof=credentials.person_secret(client.state))
        except Denied:
            raise Refusal(FOR_THE_PERSON, needs="approval", where="a terminal of your own", then="ask-owner") from None
    else:
        got = client.call("register", "pool", "", model="poolhouse-api", harness="poolhouse-api", session=native)
    return Session(client, "pool", got["token"], got["name"])


def device_name() -> str:
    """A name for a pool that nobody named: this computer's, in the letters a pool name may hold."""
    host = "".join(c if c.isascii() and (c.isalnum() or c in "-.") else " " for c in socket.gethostname().split(".")[0]).strip().lstrip("-. ")
    return host[:40] or "home"


def found_here(client: Client, name: str | None, policy: str, *, admin: Admin | None = None, voice: presence.Voice = presence.SILENT) -> Founded:
    """What an agent does when its person says "make a pool": found it on the device ``client`` reaches, with ``admin`` (a phone's key) or, by
    default, a key this computer makes. Whoever runs this asks the person as the node lets it: the person's own session is registered by the
    secret the node keeps for the person (`pool_session`), which a process that cannot read it never has, and the answer is the person's at the
    computer's prompt, which no process can give for them."""
    if admin is not None:
        return found(pool_session(client), admin, name or device_name(), policy, Terms(voice=voice))
    return found_on_computer(pool_session(client, person_session=True), name or device_name(), policy, voice)


def govern(client: Client, kind: str, body: dict[str, Any], *, voice: presence.Voice = presence.SILENT, ks: keystore.Keystore | None = None) -> dict[str, Any]:
    """Have this computer's key make the act of ``kind`` the node drafts from ``body`` (a removal, a new device): the person says yes at the
    computer's prompt for exactly that act, the key signs it, and the pool takes it. The node's reply (`status`, `act`)."""
    try:
        status = client.call("pool_status")
    except NodeError as err:
        raise _refused(err, needs="none") from None
    listed = {str(k["fingerprint"]) for k in status["governance"].get("admins", []) if k["role"] == "admin"}
    key = computer_key.PoolKeys(client.state, client, "", ks=ks).find(listed)
    if key is None:
        raise NoComputerKey("No key of this computer governs this pool, so it cannot make that change here.", needs="device", where="the computer that founded the pool", then="stop")
    session = pool_session(client, person_session=True)
    key.use(session.token)
    try:
        draft = session.client.call("pool_draft", "", session.token, kind=kind, body=body)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    try:
        key.approve(_card_of(draft), voice=voice)
    except Denied as err:
        raise Refusal(str(err), needs="approval", where="the prompt this computer shows", then="stop") from None
    act = dict(draft["act"])
    act["signatures"] = [{"kind": "admin", "key": key.public.hex(), "sig": key.sign_bytes(bytes.fromhex(draft["sign_bytes"])).hex()}]
    try:
        return dict(session.client.call("pool_submit", "", session.token, act=act))
    except NodeError as err:
        raise _refused(err, needs="none") from None
