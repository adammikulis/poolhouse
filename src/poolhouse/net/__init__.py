"""Everything from the internet comes in through here.

    from poolhouse import net

    page = net.get("https://example.org/page")
    done = net.download(url, dest, net.Want(sha256=digest, require_digest=True))

`net.pipeline` is the only module that opens a connection to a host outside this machine;
`net.download` stages, checks, scans and promotes a file; `net.untrusted` fences text a model
reads from the web.
"""

from __future__ import annotations

from poolhouse.httpguard import Fetched
from poolhouse.net.download import (
    Blocked,
    ChecksumMismatch,
    Hooks,
    NoDigest,
    Truncated,
    Want,
    download,
)
from poolhouse.net.pipeline import ASK, Ask, Pipeline, Reply, default, library, use
from poolhouse.net.policy import Distrusted, NeedsApproval, Policy
from poolhouse.net.provenance import Provenance, recent
from poolhouse.net.scan import Outcome, Scanner, ScanPolicy, ScanResult, scan_file

__all__ = ["Ask", "Blocked", "ChecksumMismatch", "Distrusted", "Hooks", "NeedsApproval", "NoDigest", "Outcome", "Pipeline",
           "Policy", "Provenance", "Reply", "ScanPolicy", "ScanResult", "Scanner", "Truncated",
           "Want", "default", "download", "get", "library", "recent", "scan_file", "use"]


def get(url: str, ask: Ask = ASK, *, pipeline: Pipeline | None = None) -> Fetched:
    """``url``'s answer through ``pipeline``, else `default`; see `Pipeline.get`."""
    return (pipeline or default()).get(url, ask)
