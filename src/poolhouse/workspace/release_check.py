"""The automatic rule for an agent that merges into a protected branch: allowed when the commit it would publish has a standing
independent land-review accept on the board and no reject, and the checkout is clean; refused at once with the reason otherwise.

Nothing waits for a person. A process with no agent marker is not asked at all (`poolhouse.caller`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from poolhouse import board, caller
from poolhouse.workspace import landing

__all__ = ["refusal"]

TIMEOUT_S = 15


def _git(where: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", where, *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def refusal(cwd: str, environ: dict[str, str] | None = None, commit: str = "") -> str:
    """Why an agent may not merge into a protected branch from ``cwd``, or an empty string when it may. ``commit`` is the commit a push
    publishes (the pre-push hook names it); without it, the checkout's HEAD is."""
    if not caller.marked(environ):
        return ""
    where = cwd or str(Path.cwd())
    head = _git(where, "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}")
    if not head:
        return "git cannot name the commit that would be published"
    if _git(where, "status", "--porcelain"):
        return f"the checkout has uncommitted changes, so {head[:7]} is not what would be published"
    try:
        client = board.client.Client(autostart=False)
        session = board.session.connect(cwd=Path(where), client=client)
        found = landing.accepts(session, head)
        why = landing.standing(session, found)
    except (board.client.NodeError, OSError, ValueError, KeyError, TypeError) as exc:
        return f"no land-review for {head[:7]} could be read ({type(exc).__name__}); a reviewer's accept is what releases it"
    return f"{head[:7]}: {why}" if why else ""
