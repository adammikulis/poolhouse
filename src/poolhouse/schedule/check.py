"""Read bench lines through the parser of the code that will run them: ``poolhouse schedule check-lines``.

Run by `poolhouse.schedule.code` with a step's own interpreter and copy of the code. Standard input is a JSON list of argument
lists; standard output one JSON object for each, ``{"error": TEXT}`` or ``{"values": {...}}`` with what the parser made of it.
Nothing is measured and nothing is loaded.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from typing import Any

from poolhouse import optional

__all__ = ["answer", "read"]


def read(argv: list[str]) -> dict[str, Any]:
    """What the bench's parser makes of ``argv``."""
    parser = optional.module("poolhouse.bench.run")._parser()  # the one parser of a bench line
    said = io.StringIO()
    try:
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(said):
            args = parser.parse_args(argv)
    except SystemExit:
        return {"error": next((line.strip() for line in reversed(said.getvalue().splitlines()) if line.strip()), "refused")}
    return {"values": {name: value for name, value in vars(args).items()
                       if value is None or isinstance(value, (str, int, float, bool, list))}}


def answer() -> int:
    """Answer the lines on standard input, one object each, on the last line of standard output."""
    rows = [read([str(word) for word in argv]) for argv in json.load(sys.stdin)]
    sys.stdout.write("\n" + json.dumps(rows))
    return 0
