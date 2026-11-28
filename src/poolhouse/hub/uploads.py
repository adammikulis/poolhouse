"""What Unsloth has uploaded for the models this machine holds, and whether a held file is still what its repository serves.

`difference` is the one comparison of a held file with a listed one; `poolhouse-models files` uses it too. `check` lists each
downloaded ``unsloth/*`` repository on the official Hub, sending no token, and keeps what changed: a held file the repository
replaced, a model file it added, and a draft head or projector it added or changed. It only lists: nothing is downloaded or
deleted, and a change only informs. The first listing of a repository is what counts as already seen.

The daemon makes the check once a day (`watch`), and serving a downloaded model makes the day's check where none ran
(`serving`). A server about to start with a draft head asks once whether that file was replaced (`replaced`), and
`serve.drafthead` fetches the newer one.

``<state>/hub/uploads.json`` is a flat file because it is one switch and a small cache that the next check can rebuild.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from poolhouse import files, home, hub
from poolhouse.hub import hfstore, publishers, remote
from poolhouse.hub.naming import _SHARD
from poolhouse.units import human_bytes

PUBLISHER = "unsloth"
EVERY_S = 24 * 3600
FIRST_AFTER_S = 300.0
LOOK_S = 3600.0
_LOG = logging.getLogger(__name__)


def difference(path: Path | None, held_size: int, size: int, digests: Callable[[], Iterable[str]]) -> str:
    """How a held file differs from the one served under its name, '' when nothing says it does: another size, or, where the
    Hub cache already names the content (`hfstore.held_digest`), another digest. No model file is hashed, and ``digests``
    (what the Hub lists for the name) is asked only when the cache names one to compare."""
    if held_size != size:
        return f"{held_size:,} bytes here, {size:,} served"
    held = hfstore.held_digest(path) if path is not None else ""
    if not held:
        return ""
    served = {digest for digest in digests() if len(digest) == len(held)}
    return "same size, other content" if served and held not in served else ""


def _state_file() -> Path:
    return home.state("hub", "uploads.json")


def _kept() -> dict[str, Any]:
    got = files.read_json(_state_file(), {})
    return got if isinstance(got, dict) else {}


def _keep(state: dict[str, Any]) -> None:
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    files.write_json(_state_file(), state)


def enabled() -> bool:
    return _kept().get("enabled", True) is not False


def switch(on: bool) -> None:
    """Turn the daily check on or off. Off also hides what earlier checks found."""
    _keep({**_kept(), "enabled": bool(on)})


def downloaded() -> dict[str, dict[str, Path]]:
    """The model files of this publisher in the Hub cache: ``{repository: {path in it: the current copy}}``."""
    out: dict[str, dict[str, Path]] = {}
    for path in hub.weight_paths():
        found = hfstore.identify(path)
        if found and found[0].split("/")[0].lower() == PUBLISHER and found[2]:
            out.setdefault(found[0], {}).setdefault(found[2], path)
    return out


def _differs(path: Path, size: int, digest: str) -> str:
    try:
        held_size = path.stat().st_size
    except OSError:
        return ""
    return difference(path, held_size, size, lambda: (digest,))


def _changes(repo: str, held: dict[str, Path], listed: dict[str, list], seen: dict[str, str]) -> list[dict[str, Any]]:
    """One change per build and kind, so the shards of one upload read as one."""
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for name, (size, digest) in sorted(listed.items()):
        if not name.lower().endswith(".gguf") and name not in held:
            continue
        companion = bool(hub.aside(name.rsplit("/", 1)[-1]))
        if name in held:
            kind, detail = "replaced", _differs(held[name], size, digest)
            if not detail:
                continue
        elif name not in seen:
            kind, detail = "companion" if companion else "new", human_bytes(size)
        elif companion and seen[name] != digest:
            kind, detail = "companion", human_bytes(size)
        else:
            continue
        groups.setdefault((kind, _SHARD.sub("", name)), []).append((name, detail))
    words = {"replaced": "replaced upstream", "new": "new upload", "companion": "new or changed companion"}
    out = []
    for (kind, build), rows in groups.items():
        name, detail = rows[0]
        more = f" and {len(rows) - 1} more shards" if len(rows) > 1 else ""
        named = {path: listed[path] for path, _detail in rows}
        out.append({"repo": repo, "kind": kind, "files": named,
                    "key": "|".join([repo, kind, build, *(digest for _size, digest in named.values())]),
                    "text": f"{repo}: {words[kind]}: {name}{more} ({detail})"})
    return out


def _asks(state: dict[str, Any]) -> bool:
    """Whether the Hub may be asked: the check is on and ``$HF_ENDPOINT`` points at the official Hub."""
    return state.get("enabled", True) is not False and urllib.parse.urlsplit(remote.endpoint()).hostname in publishers.HOSTS


def replaced(path: Path) -> bool:
    """Whether the repository the cached file ``path`` came from serves other bytes under its name now, asked now.
    False for another publisher's file, where the check is off, and where the Hub cannot be asked."""
    found = hfstore.identify(path)
    if not found or not found[2] or found[0].split("/")[0].lower() != PUBLISHER or not _asks(_kept()):
        return False
    try:
        one = next((one for one in remote.listing(found[0], auth="") if one.path == found[2]), None)
    except (OSError, RuntimeError, ValueError) as exc:
        _LOG.info("%s could not be asked whether %s changed: %s", found[0], found[2], exc)
        return False
    return one is not None and bool(_differs(path, one.size, one.sha256 or one.oid))


def check(post: Callable[[str], object] | None = None, *, now: float = 0.0) -> list[dict[str, Any]]:
    """List every downloaded repository once and return what differs. ``post`` is given each change's line the first time it is
    found; one it raises `OSError`, `RuntimeError` or `LookupError` for is offered again by the next check. Nothing is asked
    when the check is off or ``$HF_ENDPOINT`` points anywhere but the official Hub."""
    state = _kept()
    if not _asks(state):
        return []
    repos, found, errors = dict(state.get("repos") or {}), [], {}
    for repo, held in sorted(downloaded().items()):
        before = repos.get(repo) or {}
        try:
            listed = {one.path: [one.size, one.sha256 or one.oid] for one in remote.listing(repo, auth="")}
        except (OSError, RuntimeError, ValueError) as exc:
            errors[repo] = str(exc)[:300]
            found.extend(before.get("changes") or [])
            continue
        seen = before["seen"] if "seen" in before else {name: digest for name, (_size, digest) in listed.items()}
        changes = _changes(repo, held, listed, seen)
        repos[repo] = {"seen": seen, "changes": changes}
        found.extend(changes)
    posted = list(state.get("posted") or [])
    for change in found:
        if post is None or change["key"] in posted:
            continue
        try:
            post(change["text"])
        except (OSError, RuntimeError, LookupError) as exc:
            errors["notice"] = str(exc)[:300]
            continue
        posted.append(change["key"])
    _keep({"enabled": enabled(), "checked_at": now or time.time(), "repos": repos, "posted": posted, "errors": errors})
    return found


def current() -> list[dict[str, Any]]:
    """What the last check found that still holds: a replaced file not fetched since, an upload not downloaded since."""
    state = _kept()
    found = [change for row in (state.get("repos") or {}).values() for change in row.get("changes") or []]
    if state.get("enabled", True) is False or not found:
        return []
    here = downloaded()
    out = []
    for change in found:
        held = here.get(change["repo"])
        if held is None:
            continue
        if change["kind"] == "replaced":
            keep = any(name in held and _differs(held[name], *served) for name, served in change["files"].items())
        else:
            keep = not all(name in held for name in change["files"])
        if keep:
            out.append(change)
    return out


def due(now: float = 0.0) -> bool:
    return enabled() and (now or time.time()) - float(_kept().get("checked_at") or 0) >= EVERY_S


def serving(*served: str | Path, say: Callable[[str], object] = _LOG.warning) -> threading.Thread | None:
    """The day's `check`, made when a model is served from this publisher: a machine that runs no daemon never reaches
    `watch`. ``served`` are the files a server started with (weights, draft head); ``say`` is given each change found in
    their repositories. The check runs beside the server, which it neither delays nor fails, and the thread outlives a
    short command until the listing is done. None when no check is due or nothing served is this publisher's."""
    repos = {found[0] for path in served if path and (found := hfstore.identify(path))
             and found[0].split("/")[0].lower() == PUBLISHER}
    if not repos or not due():
        return None

    def once() -> None:
        try:
            for change in check():
                if change["repo"] in repos:
                    say(change["text"])
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):  # a file, the Hub or the kept cache
            _LOG.exception("The model upload check failed")

    thread = threading.Thread(target=once, name="model-uploads-serving")
    thread.start()
    return thread


def watch(post: Callable[[str], object] | None = None, *, first_after_s: float = FIRST_AFTER_S, look_s: float = LOOK_S,
          rounds: int = 0) -> threading.Thread:
    """`check` once a day for as long as this process runs; the time of the last one is kept, so restarts add no checks."""
    def loop() -> None:
        time.sleep(first_after_s)
        seen = 0
        while not rounds or seen < rounds:
            seen += 1
            try:
                if due():
                    check(post)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):  # a file, the Hub or the kept cache
                _LOG.exception("The model upload check failed")
            time.sleep(look_s)

    thread = threading.Thread(target=loop, daemon=True, name="model-uploads")
    thread.start()
    return thread
