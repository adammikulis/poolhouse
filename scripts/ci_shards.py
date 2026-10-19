#!/usr/bin/env python3
"""Split the test files into shards of about equal recorded time for the CI jobs, and record the times.

    ci_shards.py list --shards N --index I    the test files of shard I (1 to N), one per line
    ci_shards.py summary --shards N           each shard's file count and seconds, and the longest file
    ci_shards.py record --history [FILE]      write tests/ci-durations.json from the per-test history
    ci_shards.py record --junit FILE...       write it from the junit files of a CI run

A file with no recorded time takes the median of the recorded ones. The shards are the same on
every machine for one tree and one durations file.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import testhistory

ROOT = Path(__file__).resolve().parents[1]
DURATIONS = "tests/ci-durations.json"
DEFAULT_S = 10.0
CASE = re.compile(r'<testcase\b[^>]*?\bclassname="([^"]*)"[^>]*?\btime="([^"]+)"')


def collected_files(root: Path = ROOT) -> list[str]:
    """Every test file pytest collects from ``tests/``, sorted, as repository-relative paths."""
    return sorted(p.relative_to(root).as_posix() for p in (root / "tests").rglob("test_*.py"))


def load(path: Path) -> dict[str, float]:
    """The recorded seconds per file; empty when the file is missing or unreadable."""
    try:
        files = json.loads(path.read_text(encoding="utf-8"))["files"]
        return {name: float(seconds) for name, seconds in files.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def weights(files: Sequence[str], recorded: dict[str, float]) -> dict[str, float]:
    """Seconds for each of ``files``: the recorded value, else the median of the recorded ones."""
    known = [recorded[name] for name in files if name in recorded]
    fallback = statistics.median(known) if known else DEFAULT_S
    return {name: recorded.get(name, fallback) for name in files}


def assign(seconds: dict[str, float], shards: int) -> list[list[str]]:
    """Longest file first, each into the shard with the least time so far."""
    totals = [0.0] * shards
    out: list[list[str]] = [[] for _ in range(shards)]
    for name in sorted(seconds, key=lambda n: (-seconds[n], n)):
        emptiest = min(range(shards), key=lambda i: (totals[i], i))
        totals[emptiest] += seconds[name]
        out[emptiest].append(name)
    return [sorted(group) for group in out]


def shard_files(root: Path, shards: int) -> list[list[str]]:
    """The shards of this tree, balanced by the committed durations."""
    files = collected_files(root)
    return assign(weights(files, load(root / DURATIONS)), shards)


def from_history(history: Path, root: Path) -> dict[str, float]:
    """Seconds per test file, summed from the per-test history, for the files that still exist."""
    by_file: dict[str, float] = defaultdict(float)
    for node, entry in testhistory.load(history).items():
        by_file[node.split("::", 1)[0]] += testhistory.seconds(entry)
    return {name: seconds for name, seconds in by_file.items() if (root / name).is_file()}


def module_file(classname: str, root: Path) -> str | None:
    """The test file a junit ``classname`` (``tests.pool_check.test_x.TestY``) names."""
    parts = classname.split(".")
    for length in range(len(parts), 0, -1):
        candidate = "/".join(parts[:length]) + ".py"
        if (root / candidate).is_file():
            return candidate
    return None


def from_junit(paths: Sequence[Path], root: Path) -> dict[str, float]:
    """Seconds per test file, the mean over the junit files that ran it."""
    runs: dict[str, list[float]] = defaultdict(list)
    for path in paths:
        sums: dict[str, float] = defaultdict(float)
        for classname, taken in CASE.findall(path.read_text(encoding="utf-8")):
            name = module_file(classname, root)
            if name:
                sums[name] += float(taken)
        for name, seconds in sums.items():
            runs[name].append(seconds)
    return {name: statistics.fmean(values) for name, values in runs.items()}


def write(path: Path, seconds: dict[str, float]) -> None:
    """The durations file: one file per line, sorted, to a tenth of a second."""
    rows = {name: round(value, 1) for name, value in sorted(seconds.items())}
    path.write_text(json.dumps({"files": rows}, indent=1) + "\n", encoding="utf-8")


def summary(root: Path, shards: int) -> str:
    """Each shard's file count and seconds, and the longest file."""
    files = collected_files(root)
    seconds = weights(files, load(root / DURATIONS))
    lines = [f"shard {i}: {len(group)} files, {sum(seconds[n] for n in group):.0f} s"
             for i, group in enumerate(shard_files(root, shards), 1)]
    longest = max(files, key=lambda n: seconds[n], default="")
    return "\n".join([*lines, f"longest file: {longest} {seconds.get(longest, 0.0):.0f} s"])


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("list", "summary"):
        one = sub.add_parser(name)
        one.add_argument("--shards", type=int, required=True)
        if name == "list":
            one.add_argument("--index", type=int, required=True, help="1 to --shards")
    record = sub.add_parser("record")
    source = record.add_mutually_exclusive_group(required=True)
    source.add_argument("--history", nargs="?", const="", metavar="FILE",
                        help="the per-test history; this repository's when no file is named")
    source.add_argument("--junit", nargs="+", type=Path, metavar="FILE")
    args = ap.parse_args(argv)
    if args.command == "record":
        if args.history is None:
            found = from_junit(args.junit, root)
        else:
            found = from_history(Path(args.history) if args.history else testhistory.history_path(root), root)
        write(root / DURATIONS, found)
        print(f"{len(found)} files recorded in {DURATIONS}")
        return 0
    if args.shards < 1 or (args.command == "list" and not 1 <= args.index <= args.shards):
        ap.error("--index runs from 1 to --shards")
    if args.command == "summary":
        print(summary(root, args.shards))
    else:
        print("\n".join(shard_files(root, args.shards)[args.index - 1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
