"""What githubstatus.com reports, for scripts/status and scripts/ci_watch.py.

$POOLHOUSE_GITHUB_STATUS_URL replaces the address of the summary. A page that does not answer is a ``Summary`` with an error, never an
exception.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import srcpath  # noqa: F401

from poolhouse import http

URL = "https://www.githubstatus.com/api/v2/summary.json"
TIMEOUT_S = 3.0
COMPONENT = "actions"


@dataclass(frozen=True)
class Summary:
    """The status description, the names of the open incidents, the open ones that concern Actions and the reason the page was not read."""

    description: str = ""
    incidents: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    error: str = ""

    def line(self) -> str:
        """`github reports: <status description>; <incident names>`, or one plain line when the page did not answer."""
        if self.error:
            return f"github reports: {self.error}"
        return "; ".join(["github reports: " + (self.description or "no status description"), *self.incidents])


def concerns_actions(incident: dict) -> bool:
    """Whether ``incident`` names Actions or lists the Actions component."""
    parts = [str(incident.get("name", ""))] + [str(c.get("name", "")) for c in incident.get("components") or [] if isinstance(c, dict)]
    return any(COMPONENT in p.lower() for p in parts)


def read(data: object) -> Summary:
    """The ``Summary`` of a decoded summary.json."""
    if not isinstance(data, dict):
        raise TypeError("not a summary")
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    open_ones = [i for i in data.get("incidents") or [] if isinstance(i, dict) and i.get("name")]
    actions = [str(i["name"]) for i in open_ones if concerns_actions(i)]
    for part in data.get("components") or []:
        if isinstance(part, dict) and COMPONENT in str(part.get("name", "")).lower() and part.get("status") not in (None, "operational"):
            actions.append(f"Actions {str(part['status']).replace('_', ' ')}")
    return Summary(str(status.get("description", "")), tuple(dict.fromkeys(str(i["name"]) for i in open_ones)), tuple(dict.fromkeys(actions)))


def fetch(timeout: float = TIMEOUT_S) -> Summary:
    """The current summary; one with an ``error`` when the page cannot be read in ``timeout`` seconds."""
    try:
        return read(http.request_json(os.environ.get("POOLHOUSE_GITHUB_STATUS_URL") or URL, options=http.Options(timeout=timeout)))
    except (http.ServerError, ValueError, TypeError, KeyError, OSError) as why:
        return Summary(error=f"githubstatus.com did not answer ({type(why).__name__})")
