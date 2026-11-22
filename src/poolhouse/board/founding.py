"""A pool is founded on this device at once: the node drafts the charter, this computer's own key signs it, the node takes it back (docs/pools-and-serving.md, 2 and 3).

Nobody is asked and nothing waits. The signature that makes the pool true is this computer's own admin key (`computer_key`: the node makes it,
enrols it and hands it to the local user's session once, and it is kept in the keystore). A phone is optional (docs/phone-approval.md,
docs/design-phone-free-pools.md): `Admin` is a phone's or another device's key when one governs the pool. How a key counts, the node says: the
draft of the charter carries the claim the node derives for it. Every refusal is a `Refusal`: one sentence an agent passes on and what is missing.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from poolhouse import errors, keystore
from poolhouse.board import (
    approval,
    autoapprove,
    computer_key,
    credentials,
    grants,
    phone,
    presence,
)
from poolhouse.board.client import Client, Denied, NodeError
from poolhouse.board.session import Session
from poolhouse.board.terminal import terminal_id
from poolhouse.fleet.onboard.signing import KeyStoreError

__all__ = ["Admin", "Founded", "NoComputerKey", "Refusal", "Terms", "asking_session", "found", "found_here", "found_on_computer", "govern", "pool_session", "with_card", "yes_for"]

POOL_GRANT_LASTS = "10m"
POLICIES = ("open", "secure")
NOT_COUNTED = ("That key counts for nothing in this pool: found the pool without naming a key, and this computer makes its own.")
NOT_THE_DEVICE = "The node did not accept this process as this device's own session: the node must run as the same operating-system user."


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
    """The admin key is a key this device's node holds: the first key, enrolled at once."""
    held = _held(session)
    if grants.fingerprint(admin.key.public) in held:
        return
    if held:
        raise Refusal("This device already trusts a different key of yours; add this one from your phone, or use that one.", needs="device",
                      where="the Poolhouse app on your phone")
    try:
        # only the device's own session makes the first key
        grants.enrol_first(session.client, pool_session(session.client, person_session=True).token, admin.key, admin.about, voice)
    except NodeError as err:
        raise _refused(err) from None


def _live(session: Session, name: str) -> bool:
    return any(g.action == "pool_create" and g.status == "live" and g.mine and g.scope["resource"] in ("*", name) for g in grants.listing(session).grants)


def _card(session: Session, ask: approval.Ask, note: str) -> None:
    """Get the grant ``ask`` at once: this device's key signs it when policy allows, and a refusal says why. Nobody is waited for."""
    try:
        made = autoapprove.request(session, [ask], note=note)
    except Denied as err:
        raise Refusal(str(err).removeprefix("denied: "), needs="none", then="stop") from None
    if made.state != "approved":
        raise Refusal(made.message(), needs="none", then="stop")


def with_card[T](session: Session, send: Callable[[], T]) -> T:
    """``send()``; when the node says a grant is missing, get exactly that grant at once by policy and send again."""
    try:
        return send()
    except NodeError as err:
        if not err.grant:
            raise
        ask = approval.Ask(str(err.grant["action"]), str(err.grant["resource"]), lasts=POOL_GRANT_LASTS, uses=1)
        _card(session, ask, f"the node asked for {ask.action}")
        return send()


def _phone_linked(session: Session) -> bool:
    return any(p["state"] != "removed" for p in phone.phones(session))


def _allowed(session: Session, admin: Admin, name: str, voice: presence.Voice) -> None:
    """The session holds the grant to make this pool: from policy when a phone is linked, else signed by the admin key."""
    if _live(session, name):
        return
    if _phone_linked(session):
        _card(session, approval.Ask("pool_create", name, lasts=POOL_GRANT_LASTS, uses=1), f"making the pool {name}")
        return
    _enrolled(session, admin, voice)
    want = grants.Want("pool_create", {"kind": "session", "name": session.name}, name, grants.listing(session).device, 600_000, 1)
    try:
        grants.issue(session, admin.key, want)
    except (NodeError, RuntimeError) as err:
        raise _refused(err) from None


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
    """How founding tells the user what it does: ``voice`` is where the words go. Nothing waits."""

    voice: presence.Voice = presence.SILENT


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

    A device that is already in a founded pool answers with that pool and changes nothing. ``terms`` says where the words go.
    Raises `Refusal`: a key that counts for nothing, a no, a question nobody answered.
    """
    terms = terms or Terms()
    if (done := _checked(session, policy)) is not None:
        return done
    draft = _draft(session, {"key": admin.key.public.hex(), "person": admin.person, "label": admin.label}, name, policy)
    if not draft["counting"]:
        raise Refusal(NOT_COUNTED, needs="none")
    _allowed(session, admin, name, terms.voice)
    return _submit(session, _charter(admin.key, draft))


def found_on_computer(session: Session, name: str, policy: str = "secure", voice: presence.Voice = presence.SILENT, *, ks: keystore.Keystore | None = None) -> Founded:
    """Found the pool ``name`` with a key this computer makes: the node drafts the charter with a key it makes for the card, enrols that key at
    once, makes its ticket and hands its secret to this session once, the key signs the charter's acceptance under the ticket, and the node
    takes the pool. Nobody is asked."""
    if (done := _checked(session, policy)) is not None:
        return done
    keys = computer_key.PoolKeys(session.client.state, session.client, session.token, ks=ks)
    draft = _draft(session, {"computer": True, "person": "owner", "label": "this computer's key", "sealed": keys.sealed()}, name, policy)
    try:
        said = presence.ask_card(session.client, session.token, "pool_key", presence.Card(_card_of(draft)), voice)
    except Denied as err:
        raise Refusal(str(err).removeprefix("denied: "), needs="none", then="stop") from None
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
    """The ticket for the operation ``op`` (`add_device` or `join_pool`) with ``args``, made at once, as the params the call then carries: the
    ticket, and for a window the act this computer's key signs under that same ticket, which lets this device admit what proves the window's
    code. The device's own session holds no grant; it opens a window or joins a pool with this ticket for exactly that."""
    try:
        drawn = session.client.call("op_draft", "", session.token, op=op, args=args)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    try:
        ticket = str(presence.ask_card(session.client, session.token, "card", presence.Card(_card_of(drawn)), voice)["secret"])
    except Denied as err:
        raise Refusal(str(err), needs="none", then="stop") from None
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
    """A session of the pool board of this device (the one that may change the pool), as the node knows the caller. The device's own
    session (``person_session``) is registered with the secret the node keeps for the local user (`credentials.person_secret`), and it is the
    one the node lets make the first key and the first phone."""
    native = f"python-api-{terminal_id()}"
    if person_session:
        try:
            got = client.call("register_person", "pool", "", session=native, proof=credentials.person_secret(client.state))
        except Denied:
            raise Refusal(NOT_THE_DEVICE, needs="device", then="stop") from None
    else:
        got = client.call("register", "pool", "", model="poolhouse-api", harness="poolhouse-api", session=native)
    return Session(client, "pool", got["token"], got["name"])


def asking_session(client: Client) -> Session:
    """The session that asks the node for a ticket: the device's own when this process can prove the local user's secret, else the
    agent's own session of the pool board, which the node lets ask for a pool's key or a change."""
    try:
        return pool_session(client, person_session=True)
    except Refusal:
        return pool_session(client)


def device_name() -> str:
    """A name for a pool that nobody named: this computer's, in the letters a pool name may hold."""
    host = "".join(c if c.isascii() and (c.isalnum() or c in "-.") else " " for c in socket.gethostname().split(".")[0]).strip().lstrip("-. ")
    return host[:40] or "home"


def found_here(client: Client, name: str | None, policy: str, *, admin: Admin | None = None, voice: presence.Voice = presence.SILENT) -> Founded:
    """What an agent does when asked to "make a pool": found it on the device ``client`` reaches, with ``admin`` (a phone's key) or, by
    default, a key this computer makes. Nobody is asked: the node confirms at once for the local user's session (`pool_session`)."""
    if admin is not None:
        return found(pool_session(client), admin, name or device_name(), policy, Terms(voice=voice))
    return found_on_computer(asking_session(client), name or device_name(), policy, voice)


def govern(client: Client, kind: str, body: dict[str, Any], *, voice: presence.Voice = presence.SILENT, ks: keystore.Keystore | None = None) -> dict[str, Any]:
    """Have this computer's key make the act of ``kind`` the node drafts from ``body`` (a removal, a new device): the node confirms that exact act
    at once, the key signs it, and the pool takes it. The node's reply (`status`, `act`)."""
    try:
        status = client.call("pool_status")
    except NodeError as err:
        raise _refused(err, needs="none") from None
    listed = {str(k["fingerprint"]) for k in status["governance"].get("admins", []) if k["role"] == "admin"}
    key = computer_key.PoolKeys(client.state, client, "", ks=ks).find(listed)
    if key is None:
        raise NoComputerKey("No key of this computer governs this pool, so it cannot make that change here.", needs="device", where="the computer that founded the pool", then="stop")
    session = asking_session(client)
    key.use(session.token)
    try:
        draft = session.client.call("pool_draft", "", session.token, kind=kind, body=body)
    except NodeError as err:
        raise _refused(err, needs="none") from None
    try:
        key.approve(_card_of(draft), voice=voice)
    except Denied as err:
        raise Refusal(str(err), needs="none", then="stop") from None
    act = dict(draft["act"])
    act["signatures"] = [{"kind": "admin", "key": key.public.hex(), "sig": key.sign_bytes(bytes.fromhex(draft["sign_bytes"])).hex()}]
    try:
        return dict(session.client.call("pool_submit", "", session.token, act=act))
    except NodeError as err:
        raise _refused(err, needs="none") from None
