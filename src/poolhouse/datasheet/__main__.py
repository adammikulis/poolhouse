"""``python -m poolhouse.datasheet PART [MANUFACTURER]``: download a part's datasheet and print where it went."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from poolhouse.datasheet import NotFound, find
from poolhouse.log import say, warn

USAGE = "usage: python -m poolhouse.datasheet PART [MANUFACTURER]"


def lookup(words: Sequence[str]) -> int:
    """Print the datasheet found for ``PART`` as JSON and return 0; print why none was found and return 1."""
    if words[:1] in (["-h"], ["--help"]):
        say(USAGE)
        return 0
    if not 1 <= len(words) <= 2:
        warn(USAGE)
        return 2
    try:
        found = find(words[0], words[1] if len(words) > 1 else "")
    except NotFound as exc:
        warn(str(exc))
        return 1
    say(json.dumps(found.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(lookup(sys.argv[1:]))
