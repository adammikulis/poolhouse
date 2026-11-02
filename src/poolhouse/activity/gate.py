"""Test results as evidence: a run is recorded against the hash of the tree it ran on, so
"the full tier is green on tree X" is something the tool wrote, not something said."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import psutil

from poolhouse import gitstate
from poolhouse.activity import writer
from poolhouse.activity.schema import Entry, Said, build
from poolhouse.files import write_json

__all__ = ["Run", "evidence", "read_counts", "record_run", "tree_hash"]


def _plain_index(root: Path) -> bool:
    """Whether the checkout's index has no entry that hides an edit from git."""
    try:
        return not gitstate.hidden(root)
    except RuntimeError:
        return False


def _seed_index(root: Path, index: Path) -> None:
    """Copy the checkout's index, timestamps kept, to ``index`` so ``git add -A`` rehashes only the files that changed."""
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-path", "index"],
                          capture_output=True, text=True, check=False)
    if done.returncode == 0 and _plain_index(root):
        with contextlib.suppress(OSError):
            shutil.copy2(done.stdout.strip(), index)


def tree_hash(root: Path) -> str:
    """The git tree hash of everything in ``root`` that is not ignored, working tree
    included, computed in a throwaway copy of the index; empty when ``root`` is not a git checkout."""
    with tempfile.TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        _seed_index(root, index)
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        for step in (["add", "-A"], ["write-tree"]):
            done = subprocess.run(["git", "-C", str(root), *step], env=env, capture_output=True,
                                  text=True, check=False)
            if done.returncode:
                return ""
        return done.stdout.strip()


def read_counts(junit: Path) -> dict[str, int]:
    try:
        node = ET.parse(junit).getroot()  # noqa: S314 - the file pytest just wrote
    except ET.ParseError as error:
        raise ValueError("invalid junit document") from error
    suites = [node] if node.tag == "testsuite" else list(node) if node.tag == "testsuites" else []
    if not suites or any(suite.tag != "testsuite" or suite.find("testsuite") is not None for suite in suites):
        raise ValueError("missing junit test counts")
    result = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        attrs = suite.attrib
        if "tests" not in attrs:
            raise ValueError("missing junit test counts")
        total, bad = int(attrs["tests"]), int(attrs.get("failures", 0))
        errors, skipped = int(attrs.get("errors", 0)), int(attrs.get("skipped", 0))
        if min(total, bad, errors, skipped) < 0 or bad + errors + skipped > total:
            raise ValueError("invalid junit test counts")
        cases = suite.findall("testcase")
        seen = {"failure": 0, "error": 0, "skipped": 0}
        for case in cases:
            outcomes = [child.tag for child in case if child.tag in seen]
            if len(outcomes) > 1:
                raise ValueError("ambiguous junit testcase outcome")
            if outcomes:
                seen[outcomes[0]] += 1
        if len(cases) != total or (seen["failure"], seen["error"], seen["skipped"]) != (bad, errors, skipped):
            raise ValueError("junit testcase outcomes do not match suite counts")
        result["passed"] += total - bad - errors - skipped
        result["failed"] += bad
        result["errors"] += errors
        result["skipped"] += skipped
    return result


@dataclass(frozen=True, slots=True)
class Run:
    """One pytest run: where, which tier, the command, the junit file it wrote, how it ended."""

    root: Path
    tier: str
    command: list[str]
    junit: Path
    exit_code: int
    seconds: float
    tree: str = ""


def record_run(run: Run, artifact: Path | None = None, *, destination=None, boundary: dict | None = None) -> bool:
    """Record one pytest run: tree, tier, a hash of the command, counts and duration."""
    root, tier, exit_code = run.root, run.tier, run.exit_code
    try:
        counts = read_counts(run.junit)
    except (OSError, ET.ParseError, ValueError):
        counts = {}
    tree = run.tree or tree_hash(root)
    words = [w for w in run.command if not w.startswith("--junitxml") and not w.startswith("-n")]
    digest = hashlib.sha256(" ".join(words).encode()).hexdigest()[:16]
    clean = bool(counts) and sum(counts.values()) > 0 and not counts["failed"] and not counts["errors"]
    outcome = ("pass" if counts["passed"] else "skipped") if exit_code == 0 and clean else "fail"
    said: Said = {"subject": f"tree:{tree[:12]}", "outcome": outcome,
                  "refs": {"tree": tree, "command": digest, "tier": tier},
                  "meta": {**counts, "seconds": round(run.seconds, 1), "exit": exit_code}}
    if artifact is not None:
        payload = build("test.result", ts=time.time(), actor="system",
                        session=f"test-{os.getpid()}", **said)
        payload["run"] = {"requested_command": list(run.command), "runner_interpreter": sys.executable,
                          "executed_interpreter": run.command[0] if run.command else "",
                          "root": str(root.resolve()), "tree_after": tree_hash(root)}
        payload["process"] = {"pid": os.getpid(),
                              "started": psutil.Process().create_time()}
        if boundary is not None:
            payload["boundary"] = boundary
        if destination is None:
            write_json(artifact, payload)
        else:
            destination.write(json.dumps(payload, sort_keys=True).encode() + b"\n")
        return True
    return writer.record("test.result", actor="system", **said)


def evidence(tree: str, tier: str = "") -> Entry | None:
    """The latest passing record for ``tree`` (and ``tier``), else None."""
    found = None
    for e in writer.log().entries():
        if isinstance(e, Entry) and e.kind == "test.result" and e.refs.get("tree") == tree \
                and e.outcome == "pass" and tier in ("", e.refs.get("tier")):
            found = e
    return found
