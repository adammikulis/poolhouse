"""Repository GitHub App records and scoped installation tokens."""

from __future__ import annotations

import base64
import dataclasses
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import jwt

from poolhouse.net.github_release import Failed, request as api

ITEM = "POOLHOUSE_AGENT_APP"
RESET = f"remove it with `poolhouse credentials unset {ITEM} --keychain` and run scripts/agent-identity setup again"
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
RANK = {"read": 1, "write": 2, "admin": 3}
TOKEN_LIFE_S = 3700

@dataclass
class Identity:
    """The App agents authenticate as. ``key`` is its private key and is never shown."""

    repo: str
    app_id: int
    client_id: str
    slug: str
    key: str = field(repr=False)
    installation_id: int | None = None

def encode(who: Identity) -> str:
    return base64.b64encode(json.dumps(dataclasses.asdict(who), sort_keys=True).encode()).decode()

def decode(text: str) -> Identity:
    """The identity a stored record holds; ``Failed`` for anything else."""
    damaged = Failed(f"the stored identity is damaged; {RESET}")
    try:
        who = Identity(**json.loads(base64.b64decode(text, validate=True)))
    except (ValueError, TypeError):
        raise damaged from None
    sound = (type(who.app_id) is int and isinstance(who.repo, str) and REPO.fullmatch(who.repo)
             and isinstance(who.slug, str) and SLUG.fullmatch(who.slug) and isinstance(who.client_id, str)
             and isinstance(who.key, str) and who.key.startswith("-----BEGIN")
             and (who.installation_id is None or type(who.installation_id) is int))
    if not sound:
        raise damaged
    return who

def app_jwt(who: Identity) -> str:
    """A ten minute JWT that signs in as the App."""
    now = int(time.time())
    try:
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": who.client_id or str(who.app_id)},
                          who.key, algorithm="RS256")
    except (jwt.PyJWTError, ValueError, TypeError):
        raise Failed(f"the stored key cannot sign; {RESET}") from None

def within(granted: object, allowed: dict[str, str]) -> bool:
    """Whether ``granted`` holds only permissions of ``allowed``, none at a higher level."""
    return isinstance(granted, dict) and all(
        name in allowed and isinstance(level, str) and RANK.get(level, 99) <= RANK[allowed[name]]
        for name, level in granted.items())

def lifetime_ok(text: object) -> bool:
    """Whether ``text`` is a time within the hour ahead."""
    try:
        ends = datetime.fromisoformat(str(text))
        return ends.tzinfo is not None and 0 < (ends - datetime.now(UTC)).total_seconds() <= TOKEN_LIFE_S
    except ValueError:
        return False

def wrong_token(reply: object, permissions: dict[str, str], only: str | None) -> str:
    """Why a minted token is not the one asked for; empty when it is."""
    if not isinstance(reply, dict) or not isinstance(reply.get("token"), str) or not reply["token"]:
        return "GitHub's answer holds no token"
    if not within(reply.get("permissions"), permissions):
        return "the token carries permissions that were not asked for"
    if only is not None:
        rows = reply.get("repositories")
        names = [str(r.get("full_name", "")).lower() for r in rows if isinstance(r, dict)] if isinstance(
            rows, list) else []
        if names != [only.lower()]:
            return f"the token is for {', '.join(names) or 'no repository'}, not {only}"
    return "" if lifetime_ok(reply.get("expires_at")) else "the token does not last one hour"

def mint(who: Identity, permissions: dict[str, str], only: str | None) -> dict:
    """An installation token for ``permissions`` (and the one repository ``only``), checked against what was asked."""
    body: dict[str, Any] = {"permissions": permissions}
    if only:
        body["repositories"] = [only.split("/")[1]]
    reply = api("POST", f"/app/installations/{who.installation_id}/access_tokens", bearer=app_jwt(who),
                payload=body, hint="run `scripts/agent-identity check --online` to see what is wrong")
    if problem := wrong_token(reply, permissions, only):
        raise Failed(f"{problem}; no token was given out. Run `scripts/agent-identity check --online`, "
                     f"or {RESET}")
    return reply
