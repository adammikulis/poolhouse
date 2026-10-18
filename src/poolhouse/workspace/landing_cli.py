"""Commands that review a commit before it lands and name the devices whose entries count."""

from __future__ import annotations

from poolhouse.command import flag
from poolhouse.workspace import landing

__all__ = ["TABLE"]

TABLE = [
    ("land-review",
     "independently review the exact commit of a branch before `scripts/land` lands it (a session that held the "
     "branch's claim cannot)",
     [flag("branch"), flag("sha", help="the full SHA you reviewed; must be the tip `scripts/land --dry-run` prints"),
      flag("--verdict", choices=("accept", "reject"), default="accept")],
     lambda a, s: landing.review(s, a.branch, a.sha, a.verdict)),
    ("land-trust", "count the tasks and grants of another device of the pool (its certificate fingerprint; a person "
                  "or a lead with the grant), or stop counting them with --revoke",
     [flag("device", help="the device's fingerprint, from `poolhouse-members ls --json`"),
      flag("--revoke", action="store_true", help="stop counting this device's entries")],
     lambda a, s: landing.trust(s, a.device, not a.revoke)),
]
