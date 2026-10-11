"""Runs on a device the rig cannot start commands from the inside of (a container, a WSL distribution): the one process every command of the rig runs under.

    python tests/pool_check/driver.py          reads one JSON request per line on stdin, answers one JSON line each, ends at end of input

A node binds each session token to the processes that registered it (the lineage, docs/node.md "Identity") and lets a token spend a grant
only from a process that shares that lineage. A command started with `docker exec` or `wsl.exe` has no parent the node can read, so two
commands of the rig would never share one, and a session that registered in the first could spend nothing in the second. The rig starts this
process once per such device and sends every command through it: each command is its child, so they all share it, as the commands a person
types in one terminal share the shell.

A request is ``{"id": N, "argv": [...], "env": {...}, "timeout": SECONDS}``; the answer is ``{"id": N, "rc": CODE, "out": TEXT, "err": TEXT}``
(``rc`` is -1 and ``err`` says so when the command ran out of time).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def answer(request: dict) -> dict:
    """Run one request's command to the end (or its timeout) and what it printed."""
    try:
        done = subprocess.run(request["argv"], env={**os.environ, **request.get("env", {})}, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=float(request.get("timeout", 120)), check=False)
    except subprocess.TimeoutExpired as exc:
        return {"id": request["id"], "rc": -1, "out": _text(exc.stdout), "err": f"{_text(exc.stderr)}\n(no answer in {request.get('timeout')}s)"}
    except OSError as exc:
        return {"id": request["id"], "rc": 127, "out": "", "err": f"{type(exc).__name__}: {exc}"}
    return {"id": request["id"], "rc": done.returncode, "out": done.stdout, "err": done.stderr}


def _text(value: bytes | str | None) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def main() -> int:
    out = sys.stdout.buffer
    out.write(b'{"ready": true}\n')
    out.flush()
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        out.write(json.dumps(answer(json.loads(line))).encode() + b"\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
