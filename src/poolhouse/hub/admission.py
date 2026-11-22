"""What a pull of a Hub repository checks and notes before it fetches, and who it believes asked for it.

Nobody is asked. A pull that a person or the app asked for (`PERSON`) fetches from any publisher; what is unusual about
it (a publisher Poolhouse does not know, a Hub that is not the official one, a revision that is not the publisher's own
branch, a name that says the model was altered) is logged and recorded in the pin of each file kept. A pull an agent
started (`AGENT`) of something unusual is refused at once with the reason, unless the model is already on this
machine or its owner or repository is a line of `allow_file`, a text file a person edits. Whoever asked, a file the
Hub lists with other bytes than the same commit had when it was pulled is refused, and the file already kept stays
as it is."""

from __future__ import annotations

import enum
import logging
from pathlib import Path

from poolhouse import home, person, sentinel
from poolhouse.hub import publishers, remote
from poolhouse.hub.remote import Ref, RemoteError, RemoteFile
from poolhouse.requests.model import shown
from poolhouse.sentinel.events import Event, Severity

__all__ = ["AGENT", "PERSON", "DigestChanged", "NotAllowed", "Trust", "admit", "allow_file", "allowed", "checked",
           "notes"]

logger = logging.getLogger(__name__)


class NotAllowed(RemoteError):
    """An agent's pull of something unusual that is neither on this machine nor in the allow list."""


class DigestChanged(RemoteError):
    """The Hub lists other bytes for a file at a commit than the file kept from that commit has."""


class Trust(enum.Enum):
    """Who asked for a pull, which decides what it may fetch: ``PERSON`` (a person's command or a click in the app) or
    ``AGENT`` (an agent). ``None`` where a pull takes one means the process says: an agent marker in its
    environment (`poolhouse.person.marked`) makes it an agent's."""

    PERSON = "person"
    AGENT = "agent"


PERSON = Trust.PERSON
AGENT = Trust.AGENT

ALLOW_NAME = "allowed-publishers.txt"


def allow_file() -> Path:
    """The text file whose lines (``owner`` or ``owner/repo``; ``#`` starts a comment) name what an agent may pull
    although it is unusual. A person writes it; nothing here does."""
    return home.state("models", ALLOW_NAME)


def allowed(repo: str) -> bool:
    """Whether ``repo`` or its owner is a line of `allow_file`."""
    try:
        lines = allow_file().read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    listed = {line.split("#", 1)[0].strip() for line in lines}
    return repo in listed or publishers.namespace(repo) in listed


def checked(trust: object) -> Trust:
    """``trust`` itself when it is a `Trust`; an agent's when it is None and the process carries an agent marker, else a
    person's; `TypeError` for anything else, such as a word."""
    if trust is None:
        return AGENT if person.marked() else PERSON
    if not isinstance(trust, Trust):
        raise TypeError(f"trust is a poolhouse.hub.Trust (PERSON or AGENT) or None, not {trust!r}")
    return trust


def _unusual(ref: Ref, files: list[RemoteFile], check=None) -> list[str]:
    """The sentences that say what is unusual about ``files`` of ``ref``: who published them and where, and what their
    names say."""
    publisher = publishers.namespace(ref.repo)
    host = remote.endpoint_host()
    said: list[str] = []
    if not publishers.official(host):
        said.append(f"This model is from {publisher} on {host}, a Hub Poolhouse does not know.")
    elif not publishers.trusted(ref.repo):
        said.append(f"This model is from {publisher}, who Poolhouse does not know.")
    elif not remote.on_branch(ref.repo, ref.commit, check=check):
        said.append(f"This file is not from {publisher}'s own branch.")
    if publishers.marker(ref.repo, *(f.path for f in files)):
        said.append("Its name says it was changed to answer what the original would refuse.")
    return said


def _kept(ref: Ref, one: RemoteFile) -> str:
    """The sha256 of a file of this commit still on this machine that is not the one ``one`` lists, or ''."""
    for pin in sentinel.default().manifest.pins_for(f"{ref.repo}/{one.path}"):
        if pin.commit == ref.commit and pin.sha256 != one.sha256 and Path(pin.path).exists():
            return pin.sha256
    return ""


def _same_commit(ref: Ref, files: list[RemoteFile]) -> None:
    """`DigestChanged` for a file the Hub lists with other bytes than the file kept from the same commit has. Nothing is
    held or quarantined: the file kept stays as it is, and removing it lets the next pull take the Hub's bytes."""
    node = sentinel.default()
    if node.mode == sentinel.Mode.OFF:
        return
    for one in (f for f in files if f.sha256 and _kept(ref, f)):
        node.bus.emit(Event("model.hub_digest_changed", Severity.WARNING, "hub", f"model:{ref.repo}/{one.path}",
                            {"repo": ref.repo, "path": one.path, "commit": ref.commit, "listed": one.sha256,
                             "kept": _kept(ref, one)}, node.clock()))
        raise DigestChanged(
            f"{shown(one.path, 'label')} of {ref.repo} at {ref.commit[:12]} is listed with other bytes than the file "
            f"kept from that commit, which cannot change. It was not downloaded and the file you have is unchanged. "
            f"To take the Hub's bytes instead, remove that file from the cache and pull again")


def notes(repo: str, commit: str, path: str, note: str = "") -> dict[str, str]:
    """What the pin of the file ``path`` of ``repo`` at ``commit`` records of its pull: the publisher, the commit, the
    Hub, the file, and ``note`` (the words `admit` returned) when there are any."""
    return {"publisher": publishers.namespace(repo), "commit": commit, "host": remote.endpoint_host(),
            "source_ref": f"{repo}/{path}", **({"note": note} if note else {})}


def admit(ref: Ref, files: list[RemoteFile], trust: object = None, *, check=None) -> str:
    """Return when ``files`` of ``ref`` (resolved to a commit) may be fetched for whoever ``trust`` says asked: the
    words of what is unusual about them (the pin of the files keeps them), empty when nothing is. `DigestChanged` for
    other bytes than the same commit had; `NotAllowed` for an agent's pull of what is unusual that is not in the allow
    list."""
    trust = checked(trust)
    if len(ref.commit) != 40:
        raise ValueError(f"{ref.text} must be resolved to a commit before it is fetched")
    _same_commit(ref, files)
    said = _unusual(ref, files, check)
    if not said:
        return ""
    words = " ".join(said)
    if trust is AGENT and not allowed(ref.repo):
        raise NotAllowed(f"Not fetched for an agent: {words} It is {ref.repo} at {ref.commit[:12]}. A pull a person "
                         f"asks for in a terminal or the app fetches it; for agents to pull it, put "
                         f"{publishers.namespace(ref.repo)} or {ref.repo} on a line of {allow_file()}.")
    logger.info("%s at %s is pulled for %s: %s", ref.repo, ref.commit[:12],
                "an agent" if trust is AGENT else "a person", words)
    return words
