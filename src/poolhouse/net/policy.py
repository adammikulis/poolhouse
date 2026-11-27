"""Which hosts a fetch may reach. The standard policy fetches any https URL the caller names; a program that passes an
allow-list is held to it. The built-in names and recorded approvals say which hosts the policy vouches for."""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from poolhouse import home
from poolhouse.httpguard import Refused

__all__ = ["ALLOWED", "ANYWHERE", "ENV", "Approval", "NeedsApproval", "Policy", "approvals_path", "default", "host_of",
           "mirror_netloc"]

ALLOWED: tuple[str, ...] = (
    "huggingface.co", "*.huggingface.co", "hf.co", "*.hf.co",
    "github.com", "api.github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "raw.githubusercontent.com",
    "codeload.github.com", "nominatim.openstreetmap.org",
)
"""Hosts the standard policy names: the model hub, GitHub's release hosts and the geocoder. The
host of ``$HF_ENDPOINT`` and those in ``$POOLHOUSE_NET_ALLOW_HOSTS`` count as listed too."""

ANYWHERE: tuple[str, ...] = ("*",)
"""An allow-list that lists every host; only `pipeline.library` builds a policy from it."""

ENV = "POOLHOUSE_NET_ALLOW_HOSTS"

class NeedsApproval(Refused):
    """A host the policy does not admit for this URL; ``host`` is what to approve."""

    def __init__(self, host: str, purpose: str, *, any_https: bool = False) -> None:
        why = (f"{purpose} is not made over plain http to {host}: use its https address (any https URL is fetched)"
               if any_https else
               f"{host} is not on the allow-list for {purpose}. A program lists it in the `allowed` argument "
               f"(`net.Policy(allowed=(..., {host!r}))` in a `net.Pipeline`)")
        super().__init__(f"{why}; `poolhouse-security approve-host {host}` approves it (decided by the sentinel.policy gate)")
        self.host, self.purpose = host, purpose


@dataclass(frozen=True, slots=True)
class Approval:
    """One approved host: who approved it, when, why, and when it lapses (0 for never)."""

    host: str
    at: float
    by: str
    note: str = ""
    until: float = 0.0


def host_of(url: str) -> str:
    """The lower-case IDNA host name of ``url``; '' when it has none."""
    try:
        name = urllib.parse.urlsplit(url.strip()).hostname or ""
        return name.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return ""


def approvals_path() -> Path:
    """The append-only record of approved hosts."""
    return home.state("net", "approvals.jsonl")


def mirror_netloc() -> str:
    """``host`` or ``host:port`` of ``$HF_ENDPOINT`` as the person wrote it; '' when unset. With
    an explicit port only that port is meant, so a redirect to another one is checked."""
    named = host_of(os.environ.get("HF_ENDPOINT", ""))
    try:
        port = urllib.parse.urlsplit(os.environ.get("HF_ENDPOINT", "").strip()).port
    except ValueError:
        port = None
    return f"{named}:{port}" if named and port else named


class Policy:
    """Allow-list plus recorded approvals; an allow-list entry may hold ``*`` wildcards. Without ``allowed`` the
    policy names `ALLOWED` and also admits any https URL; with it, only what the list and the approvals name."""

    def __init__(self, allowed: Iterable[str] | None = None, *, path: Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.any_https = allowed is None
        self.allowed = tuple(p.lower() for p in (ALLOWED if allowed is None else allowed))
        self.path = path
        self.clock = clock
        self._lock = threading.Lock()

    def _file(self) -> Path:
        return self.path or approvals_path()

    def _extra(self) -> tuple[str, ...]:
        named = tuple(p.strip().lower() for p in os.environ.get(ENV, "").split(",") if p.strip())
        mirror = mirror_netloc()
        return (*named, mirror) if mirror else named

    def approvals(self) -> list[Approval]:
        """Every approval on record, oldest first."""
        out: list[Approval] = []
        try:
            lines = self._file().read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            try:
                row = json.loads(line)
                out.append(Approval(str(row["host"]), float(row["at"]), str(row["by"]),
                                    str(row.get("note", "")), float(row.get("until", 0.0))))
            except (ValueError, KeyError, TypeError):
                continue
        return out

    def approve(self, host: str, *, by: str, note: str = "", for_s: float = 0.0) -> Approval:
        """Record that ``by`` approved ``host`` (one exact name), for ``for_s`` seconds or for good."""
        name = host.strip().lower()
        if not name or any(ch in name for ch in "*/ "):
            raise ValueError("approve one exact host name")
        now = self.clock()
        row = Approval(name, now, by, note, now + for_s if for_s else 0.0)
        where = self._file()
        with self._lock:
            where.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with where.open("a", encoding="utf-8") as out:
                out.write(json.dumps(asdict(row), sort_keys=True) + "\n")
        return row

    def listed(self, host: str) -> bool:
        """Whether ``host`` is on the built-in or environment allow-list."""
        return any(fnmatch.fnmatchcase(host, p) for p in (*self.allowed, *self._extra()))

    def lists_every_host(self) -> bool:
        """Whether the allow-list (with the environment's hosts) lists a host nobody named: it lists one that cannot exist."""
        return self.listed("unlisted.invalid")

    def approved(self, host: str) -> bool:
        """Whether a person approved ``host`` and the approval has not lapsed."""
        now = self.clock()
        return any(a.host == host and (not a.until or a.until > now) for a in self.approvals())

    def named(self, url: str) -> bool:
        """Whether the host of ``url`` is listed or approved."""
        host = host_of(url)
        if not host:
            return False
        netloc = urllib.parse.urlsplit(url.strip()).netloc.lower().rsplit("@", 1)[-1]
        return self.listed(host) or self.listed(netloc) or self.approved(host)

    def vouches(self, url: str) -> bool:
        """Whether the policy names the host of ``url`` and not every host: what arrived from there may be trusted as
        brought in by Poolhouse."""
        return not self.lists_every_host() and self.named(url)

    def admit(self, url: str, purpose: str = "fetch") -> str:
        """The host of ``url`` when it may be reached; `NeedsApproval` otherwise."""
        host = host_of(url)
        if not host:
            raise Refused("that URL has no host")
        if self.named(url) or (self.any_https and urllib.parse.urlsplit(url.strip()).scheme == "https"):
            return host
        raise NeedsApproval(host, purpose, any_https=self.any_https)


_DEFAULT = Policy()


def default() -> Policy:
    """The policy every fetch uses unless it is handed another."""
    return _DEFAULT
