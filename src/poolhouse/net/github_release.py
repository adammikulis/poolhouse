"""Fixed-origin GitHub App and prerelease requests."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from poolhouse import httpguard

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"
REPOSITORY = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
PATH = re.compile(rf"(?:/app/installations/[1-9][0-9]*/access_tokens|/repos/{REPOSITORY}/(?:installation|commits/[0-9a-f]{{40}}|git/ref/(?:heads|tags)/[A-Za-z0-9_.%/-]+|releases(?:/tags/[A-Za-z0-9_.%-]+|/[1-9][0-9]*(?:/assets)?)?))")


class Failed(RuntimeError):
    """A fixed GitHub operation was refused or failed."""


class Gone(Failed):
    """The requested GitHub object does not exist."""


def _fetch(url: str, method: str, body: bytes | None, headers: dict[str, str]) -> Any:
    def exact(destination: str) -> None:
        if destination != url:
            raise Failed("GitHub publication cannot redirect")

    limits = httpguard.Limits(max_bytes=8 << 20, timeout=30, deadline_s=120,
                              vet=exact, follow_redirects=False)
    reply = httpguard.fetch(url, method=method, data=body, headers=headers, limits=limits)
    if reply.status == 404:
        raise Gone("GitHub object not found")
    if not 200 <= reply.status < 300:
        raise Failed(f"GitHub answered {reply.status}")
    try:
        return json.loads(reply.body) if reply.body else {}
    except (ValueError, UnicodeError):
        raise Failed("GitHub returned an invalid response") from None


def request(method: str, path: str, *, bearer: str = "", payload: dict | None = None,
            hint: str = "") -> Any:
    """Perform one supported operation at the fixed GitHub API origin."""
    if method not in {"GET", "POST"} or not PATH.fullmatch(path) or '..' in path or '//' in path:
        raise Failed("unsupported GitHub publication operation")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
    return _fetch(API + path, method, json.dumps(payload).encode() if payload is not None else None, headers)


def upload(repo: str, release_id: int, asset: tuple[str, bytes], bearer: str) -> Any:
    """Upload pinned asset bytes to one release at GitHub's fixed upload origin."""
    name, body = asset
    if (not re.fullmatch(REPOSITORY, repo) or type(release_id) is not int or release_id <= 0
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or len(body) > 512 << 20):
        raise Failed("invalid GitHub release asset")
    url = f"{UPLOAD}/repos/{repo}/releases/{release_id}/assets?name={urllib.parse.quote(name)}"
    return _fetch(url, "POST", body, {"Authorization": f"Bearer {bearer}",
                                     "Content-Type": "application/octet-stream"})
