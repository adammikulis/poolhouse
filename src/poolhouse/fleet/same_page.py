"""The checks a page route makes before it answers: the request comes from this page, on this host, as a read or a post."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from poolhouse.graph.guard import refusal

__all__ = ["MAX_BODY", "SAFE", "Reply", "checked", "reply"]

MAX_BODY = 512 * 1024
SAFE = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'", "Referrer-Policy": "no-referrer"}
Reply = tuple[int, dict[str, str], bytes]


def reply(status: int, body: Any) -> Reply:
    """A JSON answer, or a 502 when it is over the size a page takes."""
    blob = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode()
    if len(blob) > MAX_BODY:
        status, blob = 502, b'{"error":"the answer is over the size limit; ask for fewer"}'
    return status, {**SAFE, "Content-Type": "application/json; charset=utf-8"}, blob


def checked(method: str, headers: Mapping[str, str], port: int, writes: bool = False) -> Reply | None:
    """A refusal when the request is not a read (or, with ``writes``, a post) from this page; else None."""
    if method != "GET" and not (writes and method == "POST"):
        _, headers, blob = reply(405, {"error": "this route only reads"})
        return 405, {**headers, "Allow": "GET"}, blob
    if method == "POST" and (headers.get("origin") is None or headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        return reply(403, {"error": "a post comes from this page"})
    found = refusal(method, dict(headers), port, schemes=("http", "https"))
    if found:
        return reply(found[0], {"error": found[1]})
    site = headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return reply(403, {"error": "a request from another site"})
    origin = headers.get("origin")
    if origin is not None and origin not in {f"http://{headers.get('host', '')}", f"https://{headers.get('host', '')}"}:
        return reply(403, {"error": "a request from another origin"})
    return None
