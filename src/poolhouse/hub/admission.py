"""Whether the files of a Hub repository may be fetched: a known publisher, a name that does not say the
model was altered, and bytes that match what was pulled before. Anything else waits for a person."""

from __future__ import annotations

from poolhouse import sentinel
from poolhouse.hub import publishers, remote
from poolhouse.hub.remote import Ref, RemoteError, RemoteFile
from poolhouse.requests.approvals import approved, ask
from poolhouse.requests.model import shown
from poolhouse.sentinel.events import Severity
from poolhouse.sentinel.findings import HIGH, finding

__all__ = ["DigestChanged", "NotApproved", "admit", "approved", "ask"]

CLOSING = "Models can hide behaviour that tests do not show. Use it anyway?"


class NotApproved(RemoteError):
    """A person has not approved this repository at this commit."""


class DigestChanged(RemoteError):
    """The Hub lists other bytes for a file at a commit than it did when the file was pulled."""


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


def admit(ref: Ref, files: list[RemoteFile]) -> None:
    """Return when ``files`` of ``ref`` (resolved to a commit) may be fetched. `NotApproved` after
    asking a person when the publisher is not known, a name says the model was altered, or a file
    differs from the one pulled before; `DigestChanged` when the Hub contradicts itself."""
    if len(ref.commit) != 40:
        raise ValueError(f"{ref.text} must be resolved to a commit before it is fetched")
    publisher = publishers.namespace(ref.repo)
    host = remote.endpoint_host()
    official = publishers.official(host)
    why = []
    if not official:
        why.append(f"This model is from {publisher} on {host}, a Hub Poolhouse does not know.")
    elif publisher not in publishers.TRUSTED:
        why.append(f"This model is from {publisher}, who Poolhouse does not know.")
    elif not remote.on_branch(ref.repo, ref.commit):
        why.append(f"This file is not from {publisher}'s own branch.")
    if publishers.marker(ref.repo, *(f.path for f in files)):
        why.append("Its name says it was changed to answer what the original would refuse.")
    if moved := _changed(ref, files):
        why.append(f"{shown(moved[0], 'label')} is not the file Poolhouse pulled from {ref.repo} before.")
    if not why:
        return
    fields = {"publisher": publisher, "repo": ref.repo, "commit": ref.commit, "host": host}
    subject = f"{ref.repo}@{ref.commit[:12]}"
    sentence = " ".join((*why, f"It is {ref.repo} at {ref.commit[:12]}.", CLOSING))
    if approved("model_publisher", subject, sentence, **fields):
        return
    raise NotApproved(ask("model_publisher", subject, sentence, **fields))
