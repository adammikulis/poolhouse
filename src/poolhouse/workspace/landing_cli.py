"""Commands that review a commit and name the devices whose entries count."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from poolhouse import runtime_git
from poolhouse.command import flag
from poolhouse.workspace import landing

__all__ = ["TABLE"]


def commit(value: str, cwd: Path) -> str:
    """Resolve a literal commit abbreviation; full IDs remain usable for remote advice."""
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", value):
        raise ValueError("a review names a commit ID of 4 to 40 hexadecimal characters")
    value = value.lower()
    if len(value) == 40:
        return value
    try:
        found = runtime_git.git(cwd, "rev-parse", "--disambiguate=" + value, check=False).splitlines()
        if len(found) == 1 and runtime_git.git(cwd, "cat-file", "-t", found[0], check=False) == "commit":
            return found[0]
    except (OSError, subprocess.SubprocessError):
        pass
    raise ValueError("the short commit ID does not name one unambiguous commit in this repository")


def argument(value: str) -> str:
    """Normalize before connecting, with fixed parser errors that do not echo untrusted input."""
    try:
        return commit(value, Path.cwd())
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def current_source(cwd: Path, sha: str) -> bool:
    """Whether this is the actual primary checkout at the reviewed immutable commit."""
    try:
        found = runtime_git.git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir", check=False).splitlines()
        return (len(found) == 2 and Path(found[0]).resolve() == Path(found[1]).resolve().parent
                and Path(found[1]).name == ".git"
                and runtime_git.git(cwd, "rev-parse", "--verify", "HEAD^{commit}", check=False) == sha)
    except (OSError, subprocess.SubprocessError):
        return False


def eligibility(s, branch: str, sha: str) -> str:
    """A contextual explanation, never an approval or a replacement for the runtime check."""
    try:
        reason = landing.standing(s, landing.accepts(s, sha, branch))
    except OSError:
        return "Review recorded; deployment review eligibility is unavailable. The runtime will check it before updating."
    if not reason:
        return ""
    if reason == "an independent reviewer rejected it":
        return "Review recorded; an independent reviewer rejected this commit. Your own review cannot approve your deployment."
    return "Review recorded; your own review cannot approve your deployment. Another independent review of this commit is needed."


def review(args, s):
    """Record a full immutable commit and explain current-source deployment eligibility."""
    cwd = Path.cwd()
    sha = commit(args.sha, cwd)
    note = eligibility(s, args.branch, sha) if current_source(cwd, sha) else ""
    result = landing.review(s, args.branch, sha, args.verdict)
    return {**result, **({"note": note} if note else {})}


TABLE = [
    ("land-review",
     "review the exact commit of a branch: an accept is advice, a reject stops a push of that commit",
     [flag("branch"), flag("sha", type=argument,
          help="the full commit ID or an unambiguous local abbreviation"),
      flag("--verdict", choices=("accept", "reject"), default="accept")],
     review),
    ("land-trust", "count the tasks and grants of another device of the pool (its certificate fingerprint; a person "
                  "or a lead with the grant), or stop counting them with --revoke",
     [flag("device", help="the device's fingerprint, from `poolhouse-members ls --json`"),
      flag("--revoke", action="store_true", help="stop counting this device's entries")],
     lambda a, s: landing.trust(s, a.device, not a.revoke)),
]
