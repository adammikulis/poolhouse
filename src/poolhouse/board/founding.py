"""An agent founds a pool for a person: the node drafts the charter, the person's key signs it, the node takes it back (docs/pools-and-serving.md, 2 and 3).

Nothing here signs for anyone. The signature that makes the pool true is the person's admin key, put to the person where only the
person can answer: the operating system's own prompt for the first key, or the phone's card for a grant (docs/phone-approval.md).
`Admin` is that key and how strong it is, and a key that is not strong is refused before the person is asked anything, since
the node takes no pool whose charter lists no strong key. Every refusal is a `Refusal`: one sentence an agent passes on, what is needed
and where the person taps.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import errors
from poolhouse.board import approval, grants, person_key, phone, presence
from poolhouse.board.client import Client, NodeError
from poolhouse.board.session import Session
from poolhouse.board.terminal import terminal_id

__all__ = ["Admin", "Founded", "Refusal", "Terms", "default_admin", "found", "found_here", "pool_session", "with_card"]

POOL_GRANT_LASTS = "10m"
CARD_WAIT_S = 120.0
POLICIES = ("open", "secure")
STRONG = "strong"
WEAK = "weak"
FIRST_KEY_STRENGTH = WEAK
"""What the key the first-key flow makes can honestly claim: it is kept in the account's keystore, which any process of the account can use,
so it is `weak` (docs/pools-and-serving.md, 1.3) until a presence prompt stands in front of each of its signatures."""
NEEDS_PHONE = "A pool needs a key that asks for you each time it signs, which is your phone; link your phone in the Poolhouse app and tell me again."


class Refusal(errors.Denied):
    """Founding could not go on; ``say`` is one sentence for the person, ``needs`` what is missing (`approval`, `device`, `time`, `none`),
    ``where`` where the person taps or answers, and ``then`` what the agent does next (`wait`, `retry`, `ask-owner`, `stop`)."""

    def __init__(self, say: str, *, needs: str = "none", where: str = "", then: str = "ask-owner") -> None:
        super().__init__(say)
        self.say, self.needs, self.where, self.then = say, needs, where, then


@dataclass(frozen=True, slots=True)
class Admin:
    """The person's key that governs the pool: it signs each act itself, so the person is asked by whatever stands behind ``key``."""

    key: grants.Signs
    strength: str
    """`strong` when every signature needs a presence this process cannot supply, `weak` when any process of the account can sign."""
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


def _charter(session: Session, admin: Admin, name: str, policy: str) -> dict[str, Any]:
    """The node's draft of the charter with the admin key's acceptance filled in, signed outside the node."""
    key = {"key": admin.key.public.hex(), "alg": "ed25519", "person": admin.person, "strength": admin.strength, "label": admin.label}
    draft = session.client.call("pool_draft_found", "", session.token, title=name, policy=policy, project="", keys=[key])
    charter = draft["charter"]
    for wanted in draft["accept"]:
        listed = next(k for k in charter["admins"] if k["key"] == wanted["key"])
        listed["acceptance"] = admin.key.sign_bytes(bytes.fromhex(wanted["sign_bytes"])).hex()
    return dict(charter)


def _admit_this_device(session: Session, admin: Admin, cert: str) -> None:
    """The admin key admits this device, the pool's first member: the node drafts the act, the key signs it, the node takes it back."""
    drafted = session.client.call("pool_draft", "", session.token, kind="admit", body={"cert": cert, "name": "", "how": "found", "via": ""})
    act = dict(drafted["act"])
    sig = admin.key.sign_bytes(bytes.fromhex(drafted["sign_bytes"])).hex()
    listed = [*act["signatures"], {"kind": "admin", "key": admin.key.public.hex(), "alg": "ed25519", "sig": sig}]
    act["signatures"] = sorted(listed, key=lambda s: (s["kind"], s["key"]))
    session.client.call("pool_submit", "", session.token, act=act)


def _already(status: dict[str, Any]) -> Founded | None:
    gov = status.get("governance", {})
    if gov.get("mode") != "governed":
        return None
    return Founded(str(status["pool"]), str(gov["name"]), str(status["policy"]), int(gov["epoch"]), already=True)


@dataclass(frozen=True, slots=True)
class Terms:
    """How founding waits on the person: ``voice`` says where they answer (the terminal the node asks on and the words told to them), ``wait_s``
    how long the phone's card is waited for, and ``cert_hex`` is this device's certificate when its state directory is not where this runs."""

    voice: presence.Voice = presence.SILENT
    wait_s: float = CARD_WAIT_S
    cert_hex: str = ""


def found(session: Session, admin: Admin, name: str, policy: str = "secure", terms: Terms | None = None) -> Founded:
    """Found the pool ``name`` of ``policy`` on this device, governed by the person's ``admin`` key; the device is its first member.

    A device that is already in a founded pool answers with that pool and changes nothing. ``terms`` says how the person is waited for.
    Raises `Refusal`: a key that is not strong, a no, a question nobody answered.
    """
    terms = terms or Terms()
    status = session.client.call("pool_status", "", session.token)
    if (done := _already(status)) is not None:
        return done
    if policy not in POLICIES:
        raise Refusal("A pool lets devices join by themselves (open) or with a code (secure); say which.", needs="none")
    if admin.strength != STRONG:
        raise Refusal(NEEDS_PHONE, needs="device", where="the Poolhouse app on your phone")
    _allowed(session, admin, name, terms.wait_s, terms.voice)
    try:
        charter = _charter(session, admin, name, policy)
        made = session.client.call("pool_found", "", session.token, charter=charter)
        _admit_this_device(session, admin, terms.cert_hex or (Path(session.client.state) / "device.cert").read_bytes().hex())
    except NodeError as err:
        raise _refused(err, needs="none") from None
    return Founded(str(made["pool"]), str(made["name"]), str(made["policy"]), int(made["epoch"]))


def default_admin(session: Session) -> Admin:
    """The admin key this device can offer a pool today: the person's key kept beside the node, as strong as it honestly is.

    A linked phone is the strong key, but it signs approvals of grants and no charter yet, so with one linked the sentence says so.
    """
    if _phone_linked(session):
        raise Refusal("Your phone is linked, but it cannot sign a new pool's charter yet; update the Poolhouse app, then tell me again.",
                      needs="device", where="the Poolhouse app on your phone")
    return Admin(person_key.keys(session.client.state), FIRST_KEY_STRENGTH)


def pool_session(client: Client) -> Session:
    """A session of the pool board of this device (the one that may change the pool), as the node knows the caller."""
    got = client.call("register", "pool", "", model="poolhouse-api", harness="poolhouse-api", session=f"python-api-{terminal_id()}")
    return Session(client, "pool", got["token"], got["name"])


def device_name() -> str:
    """A name for a pool that nobody named: this computer's, in the letters a pool name may hold."""
    host = "".join(c if c.isalnum() or c in "-_" else " " for c in socket.gethostname().split(".")[0]).strip()
    return host[:40] or "home"


def found_here(client: Client, name: str | None, policy: str, *, admin: Admin | None = None, voice: presence.Voice = presence.SILENT) -> Founded:
    """What an agent does when its person says "make a pool": found it on the device ``client`` reaches, with ``admin`` or the key the device can offer."""
    session = pool_session(client)
    return found(session, admin or default_admin(session), name or device_name(), policy, Terms(voice=voice))
