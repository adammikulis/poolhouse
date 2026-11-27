"""Coding items graded by their own tests: HumanEval and MBPP files, the question asked, and the sandboxed run.

A reply passes when its code, followed by the item's tests, runs to the end inside the deny-default sandbox with no
network. The items are files the caller names; their hash is kept with every result.
"""

from __future__ import annotations

import gzip
import json
import re
import secrets
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import home
from poolhouse.files import sha256_file
from poolhouse.testfarm.core.spawn import run_confined

__all__ = ["Item", "code_in", "digest", "grade", "load"]

TIMEOUT_S = 20.0
_FENCE = re.compile(r"```[\w+-]*[^\S\n]*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Item:
    """One question, and what is put before and after the reply's code to test it."""

    id: str
    ask: str
    before: str
    after: str


def _item(row: Mapping[str, Any]) -> Item:
    if "entry_point" in row:          # HumanEval: the prompt is the signature, `check` holds the asserts
        entry, prompt = str(row["entry_point"]), str(row["prompt"])
        ask = ("Complete this Python function. Reply with one code block holding the whole function and any "
               f"imports it needs.\n\n```python\n{prompt}```")
        return Item(str(row["task_id"]), ask, prompt + "\n", f"\n{row['test']}\ncheck({entry})\n")
    if "test_list" in row:            # MBPP: a sentence, and asserts that name the function
        said = str(row.get("text") or row.get("prompt") or "")
        tests = [str(t) for t in row["test_list"]]
        setup = "\n".join([*map(str, row.get("test_imports") or ()), str(row.get("test_setup_code") or "")])
        ask = f"{said}\nReply with one Python code block. It must pass this test:\n{tests[0]}"
        return Item(f"MBPP/{row['task_id']}", ask, "", "\n" + setup + "\n" + "\n".join(tests) + "\n")
    raise ValueError(f"not a HumanEval or MBPP item: it has {', '.join(sorted(row)) or 'no fields'}")


def load(paths: Sequence[str | Path]) -> list[Item]:
    """Every item of ``paths`` in order: JSON lines or a JSON list, gzipped or not. A repeated id is refused."""
    out: list[Item] = []
    for path in map(home.expand, paths):
        raw = path.read_bytes()
        text = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode("utf-8")
        rows = json.loads(text) if text.lstrip().startswith("[") else [
            json.loads(line) for line in text.splitlines() if line.strip()]
        out += [_item(row) for row in rows]
    if len({one.id for one in out}) != len(out):
        raise ValueError("two items share an id; name each file once")
    return out


def digest(paths: Sequence[str | Path]) -> str:
    """The hashes of the item files, so two results over different items are never paired."""
    return "+".join(sha256_file(home.expand(p))[:16] for p in paths)


def code_in(reply: str) -> str:
    """The last fenced block of ``reply``, else all of it."""
    found = _FENCE.findall(reply or "")
    return found[-1] if found else (reply or "")


def grade(item: Item, reply: str, *, timeout: float = TIMEOUT_S) -> bool:
    """Whether the code of ``reply`` passes the item's tests, run confined. The program prints a fresh mark after
    the tests, so code that exits early with status 0 does not pass."""
    mark = secrets.token_hex(12)
    program = f"{item.before}{code_in(reply)}\n{item.after}\nprint({mark!r})\n"
    with tempfile.TemporaryDirectory(prefix="poolhouse-item-") as made:
        Path(made, "check.py").write_text(program, encoding="utf-8")
        done = run_confined([str(Path(sys.executable).resolve()), "-I", "-B", "check.py"], Path(made), timeout=timeout)
    return done.returncode == 0 and not done.timed_out and mark.encode() in done.stdout
