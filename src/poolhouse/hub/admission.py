"""Whether the files of a Hub repository may be fetched, under the `Trust` the caller passes: a known publisher,
a name that does not say the model was altered, and bytes that match what was pulled before. What a `Trust`
does not allow waits for a person (`PERSON`, the default) or is refused naming the argument."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from poolhouse import sentinel
from poolhouse.hub import publishers, remote
from poolhouse.hub.remote import Ref, RemoteError, RemoteFile
from poolhouse.requests.approvals import approved, ask
from poolhouse.requests.model import shown
from poolhouse.sentinel.events import Severity
from poolhouse.sentinel.findings import HIGH, finding
from poolhouse.sentinel.integrity import Pin

__all__ = ["LIBRARY", "PERSON", "DigestChanged", "NotApproved", "Trust", "admit", "approved", "ask", "checked", "notes",
           "question", "vetted"]

CLOSING = "Models can hide behaviour that tests do not show. Use it anyway?"
ARGUMENT = "From a program, `ph.models.pull(repo, file, trust=...)` (`poolhouse.hub.Trust`) decides this itself."


class NotApproved(RemoteError):
    """The trust passed does not allow this repository at this commit."""


class DigestChanged(RemoteError):
    """The Hub lists other bytes for a file at a commit than it did when the file was pulled."""


@dataclass(frozen=True, slots=True)
class Trust:
    """What a pull may fetch without an answer. ``publishers`` are the owners fetched from freely (None and not
    ``shipped``: any owner; ``shipped`` adds the publishers Poolhouse ships with); ``official_only`` counts a Hub
    other than the official one as unknown; ``markers`` and ``moved`` check for a name that says the model was
    altered and for bytes unlike an earlier pull of another commit; ``ask`` puts what is not allowed to a person
    in the requests inbox, else the pull is refused and the message names this argument."""

    publishers: frozenset[str] | None = None
    shipped: bool = False
    official_only: bool = False
    markers: bool = False
    moved: bool = False
    ask: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.publishers, str):
            raise TypeError("publishers is a collection of owner names, not one string")
        if self.publishers is not None:
            object.__setattr__(self, "publishers", frozenset(self.publishers))

    def restricts(self) -> bool:
        """Whether some owners are not allowed without an answer."""
        return self.publishers is not None or self.shipped

    def allows(self, owner: str) -> bool:
        """Whether ``owner`` is fetched from without an answer."""
        return not self.restricts() or owner in (self.publishers or ()) or (self.shipped and owner in publishers.TRUSTED)


LIBRARY = Trust()
"""Any owner, any Hub, any name; the digest check stays, and the pin of what is kept is recorded."""

PERSON = Trust(shipped=True, official_only=True, markers=True, moved=True, ask=True)
"""The shipped publishers on the official Hub, and a person answers the rest; the default of every pull."""


def checked(trust: object) -> Trust:
    """``trust`` itself when it is a `Trust`; `TypeError` for anything else, such as a word."""
    if not isinstance(trust, Trust):
        raise TypeError(f"trust is a poolhouse.hub.Trust (PERSON, or Trust(...) to allow more), not {trust!r}")
    return trust


def _changed(ref: Ref, files: list[RemoteFile]) -> list[str]:
    """Paths of ``files`` whose digest differs from the one last pulled for them under another
    commit; a digest that differs at the commit pulled before quarantines and raises `DigestChanged`."""
    node = sentinel.default()
    if node.mode == sentinel.Mode.OFF:
        return []
    moved: list[str] = []
    for one in (f for f in files if f.sha256):
        priors = node.manifest.pins_for(f"{ref.repo}/{one.path}")
        key = f"hub:{ref.repo}/{one.path}@{ref.commit}"
        if node.store.blocked("model", key):
            raise DigestChanged(_held(one.path, ref))
        if node.store.state_of("model", key) == sentinel.State.RELEASED:
            continue
        if any(p.commit == ref.commit and p.sha256 != one.sha256 for p in priors):
            node.handle(finding("model.hub_digest_changed", Severity.CRITICAL, ("model", key), HIGH,
                                {"repo": ref.repo, "path": one.path, "commit": ref.commit,
                                 "listed": one.sha256}))
            if node.mode != sentinel.Mode.OBSERVE:
                raise DigestChanged(_held(one.path, ref))
        elif priors and priors[0].sha256 != one.sha256:
            moved.append(one.path)
    return moved


def _held(path: str, ref: Ref) -> str:
    return (f"{path} of {ref.repo} at {ref.commit[:12]} is not the file pulled before: the Hub lists "
            f"other bytes for a commit that cannot change. It was not downloaded and is quarantined "
            f"by sentinel; a person releases it with `poolhouse-security release`")


def _reasons(ref: Ref, files: list[RemoteFile], trust: Trust, moved: list[str]) -> list[tuple[str, str]]:
    """Why ``trust`` does not allow ``files`` of ``ref``: each reason as the sentence a person is asked and
    as the phrase a program reads."""
    publisher = publishers.namespace(ref.repo)
    host = remote.endpoint_host()
    official = publishers.official(host)
    why: list[tuple[str, str]] = []
    if trust.official_only and not official:
        why.append((f"This model is from {publisher} on {host}, a Hub Poolhouse does not know.",
                    f"{host} is not the official Hub"))
    elif not trust.allows(publisher):
        why.append((f"This model is from {publisher}, who Poolhouse does not know.",
                    f"{publisher} is not among the trusted publishers"))
    elif trust.restricts() and official and not remote.on_branch(ref.repo, ref.commit):
        why.append((f"This file is not from {publisher}'s own branch.", f"it is not from {publisher}'s own branch"))
    if trust.markers and publishers.marker(ref.repo, *(f.path for f in files)):
        why.append(("Its name says it was changed to answer what the original would refuse.",
                    "its name says it was changed to answer what the original would refuse"))
    if moved and trust.moved:
        why.append((f"{shown(moved[0], 'label')} is not the file Poolhouse pulled from {ref.repo} before.",
                    f"{moved[0]} differs from the file pulled before"))
    return why


def notes(repo: str, commit: str, path: str, unvetted: str = "") -> dict[str, str]:
    """What the pin of the file ``path`` of ``repo`` at ``commit`` records of its pull: the publisher, the commit, the Hub,
    the file, and ``unvetted`` (the words `admit` returned) when there are any."""
    return {"publisher": publishers.namespace(repo), "commit": commit, "host": remote.endpoint_host(),
            "source_ref": f"{repo}/{path}", **({"unvetted": unvetted} if unvetted else {})}


def question(repo: str, commit: str, reasons: str) -> tuple[str, str]:
    """The subject and the sentence of the request that puts ``reasons`` about ``repo`` at ``commit`` to a person."""
    return f"{repo}@{commit[:12]}", " ".join((reasons, f"It is {repo} at {commit[:12]}.", CLOSING))


def admit(ref: Ref, files: list[RemoteFile], trust: Trust = PERSON) -> str:
    """Return when ``files`` of ``ref`` (resolved to a commit) may be fetched under ``trust``: the words of
    what a person would have been asked and ``trust`` did not ask (the pin of the files keeps them, and serving
    them asks), empty when nothing is left. `NotApproved` for what ``trust`` does not allow, after asking a
    person when it asks; `DigestChanged` when the Hub contradicts itself."""
    trust = checked(trust)
    if len(ref.commit) != 40:
        raise ValueError(f"{ref.text} must be resolved to a commit before it is fetched")
    moved = _changed(ref, files)
    why = _reasons(ref, files, trust, moved)
    person = why if trust == PERSON else _reasons(ref, files, PERSON, moved)
    left = " ".join(words for words, code in person if (words, code) not in why)
    if not why:
        return left
    it = f"It is {ref.repo} at {ref.commit[:12]}."
    if not trust.ask:
        raise NotApproved(f"Not fetched: {'; '.join(code for _, code in why)}. {it} Pass another `trust=` "
                          f"(`Trust(publishers=...)` names the owners to fetch from).")
    subject, sentence = question(ref.repo, ref.commit, " ".join(words for words, _ in why))
    fields = {"publisher": publishers.namespace(ref.repo), "repo": ref.repo, "commit": ref.commit,
              "host": remote.endpoint_host()}
    if approved("model_publisher", subject, sentence, **fields):
        return left
    told = ask("model_publisher", subject, sentence, **fields)
    raise NotApproved(told.replace(sentence, f"{sentence} {ARGUMENT}", 1))


def _waiting(path: Path) -> list[Pin]:
    """The pins of the pulled files at ``path`` (a file, or a folder of them, links followed) that wait on a question."""
    held = {os.path.realpath(p.path): p for p in sentinel.default().manifest.pins().values() if p.unvetted}
    if not held:
        return []
    files = [path] if path.is_file() else [Path(root, name) for root, _, names in os.walk(path) for name in names]
    return list({held[real].path: held[real] for one in files if (real := os.path.realpath(one)) in held}.values())


def vetted(path: Path | str) -> None:
    """Return when no file at or under ``path`` waits on a question its pull did not ask (`Pin.unvetted`: a `Trust`
    let it through that `PERSON` would have asked about). `NotApproved` after asking a person until they approved the
    repository at that commit; one answer covers every file of it, and the pins no longer wait."""
    node = sentinel.default()
    if node.mode == sentinel.Mode.OFF:
        return
    for pin in _waiting(Path(path).expanduser()):
        repo = "/".join(pin.source_ref.split("/")[:2])
        subject, sentence = question(repo, pin.commit, pin.unvetted)
        fields = {"publisher": pin.publisher, "repo": repo, "commit": pin.commit, **({"host": pin.host} if pin.host else {})}
        if not approved("model_publisher", subject, sentence, **fields):
            raise NotApproved(ask("model_publisher", subject, sentence, **fields))
        node.manifest.vet(pin)
