"""Exact maintained test job receipts and failure identities."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import land_live_jobs
import testjobs

from poolhouse.activity import reuse
from poolhouse.activity.gate import tree_hash
from poolhouse.board import session
from poolhouse.board.client import NodeError
from poolhouse.keystore import KeystoreError
from poolhouse.workspace import testruns


@dataclass
class Receipt:
    job: str = ""
    before: str = ""
    after: str = ""
    valid: bool = False
    complete: bool = False
    failures: list[tuple[str, str, str]] = field(default_factory=list)
    detail: str = ""


def read_xml(path: Path, limit: int) -> tuple[ET.Element, int]:
    descriptor = land_live_jobs.regular_fd(path, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("oversized JUnit evidence")
    text = raw.decode("utf-8")
    if "\0" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("invalid JUnit evidence")
    return ET.fromstring(text), len(raw)  # noqa: S314 - UTF-8, bounded, declarations refused


def failures(folder: Path, root: Path) -> list[tuple[str, str, str]]:
    paths = list(folder.glob("junit-*.xml"))
    if not paths or len(paths) > 64:
        raise ValueError("missing or oversized JUnit evidence")
    found, seen, total = [], set(), 0
    for path in paths:
        document, size = read_xml(path, 8 * 1024 * 1024 - total)
        total += size
        suites = [document] if document.tag == "testsuite" else list(document)
        if document.tag not in ("testsuite", "testsuites") or not suites:
            raise ValueError("invalid JUnit suites")
        for suite in suites:
            cases = suite.findall("testcase")
            if suite.tag != "testsuite" or int(suite.get("tests", "-1")) != len(cases):
                raise ValueError("incomplete JUnit cases")
            if any(child.tag not in ("testcase", "properties", "system-out", "system-err") for child in suite):
                raise ValueError("unknown JUnit suite content")
            counts = Counter()
            for case in cases:
                classname, name = case.get("classname"), case.get("name")
                if not classname or not name:
                    raise ValueError("missing test identity")
                identity = testjobs.node_id(classname, name, root)
                if not identity.startswith("tests/") or ".." in identity.split("::")[0].split("/"):
                    raise ValueError("invalid test identity")
                if identity in seen:
                    raise ValueError("duplicate JUnit case identity")
                seen.add(identity)
                if any(child.tag not in ("failure", "error", "skipped", "properties", "system-out", "system-err") for child in case):
                    raise ValueError("unknown JUnit case content")
                counts["skipped"] += len(case.findall("skipped"))
                for failure in case:
                    if failure.tag not in ("failure", "error"):
                        continue
                    counts[failure.tag] += 1
                    content = json.dumps([failure.get("type", ""), failure.get("message", ""), failure.text or ""])
                    content = content.replace(str(root.resolve()), "<checkout>")
                    found.append((identity, failure.tag, hashlib.sha256(content.encode()).hexdigest()))
            if counts["skipped"] != int(suite.get("skipped", "0")):
                raise ValueError("incomplete JUnit skipped counts")
            if counts["failure"] != int(suite.get("failures", "-1")) or counts["error"] != int(suite.get("errors", "-1")):
                raise ValueError("incomplete JUnit failure counts")
    return sorted(found)


def actor(root: Path) -> tuple[dict[str, str], bool]:
    assigned = bool(os.environ.get(session.AGENT_ENV) or os.environ.get(session.TOKEN_ENV))
    owner = {"id": "", "source": "none"}
    authenticated = False
    if assigned:
        try:
            who = session.connect(cwd=root).whoami()
            owner = {"id": who.name, "parent": who.parent, "source": "workspace-session"}
            authenticated = True
        except (NodeError, KeystoreError, OSError, ValueError):
            pass
    return owner, authenticated


def run(root: Path, argv: list[str]) -> tuple[int, str, Receipt]:
    receipt = Receipt(before=tree_hash(root))
    owner, authenticated = actor(root)
    jobs = testjobs.Jobs(reuse.reuse_base() / testruns.scope(root))
    began = time.time()
    receipt.job = jobs.submit(argv[2:], owner, root, root / "scripts/test", {"task": "", "exclusive": False})
    completed = False
    previous = signal.getsignal(signal.SIGTERM)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    main_thread = threading.current_thread() is threading.main_thread()
    if main_thread:
        signal.signal(signal.SIGTERM, interrupt)
    try:
        spec = land_live_jobs.record(jobs.path(receipt.job) / "spec.json")
        initial = land_live_jobs.record(jobs.path(receipt.job) / "status.json")
        if (spec.get("id") != receipt.job or spec.get("root") != str(root.resolve())
                or spec.get("argv") != argv[2:] or spec.get("owner", {}).get("id") != owner["id"]
                or not began <= spec.get("created", 0) <= time.time()):
            raise ValueError("the submitted job receipt differs from its invocation")
        status = jobs.wait(receipt.job, float("inf"), tail=True)
        completed = status.get("state") in testjobs.TERMINAL
    finally:
        if not completed:
            own_secret = jobs.read(receipt.job, "spec.json").get("secret", "") if not owner["id"] else ""
            with suppress(testjobs.Refused, OSError, ValueError):
                jobs.cancel(receipt.job, owner["id"], own_secret)
        if main_thread:
            signal.signal(signal.SIGTERM, previous)
    code = status.get("exit", 3)
    descriptor = land_live_jobs.regular_fd(jobs.path(receipt.job) / "log", os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as handle:
        handle.seek(max(0, os.fstat(handle.fileno()).st_size - 65536))
        text = handle.read(65536).decode("utf-8", errors="replace")
    receipt.after = tree_hash(root)
    valid = (receipt.before and receipt.before == receipt.after and status.get("state") == "done"
             and type(code) is int and code in (0, 1) and type(status.get("pid")) is int
             and status.get("started", 0) >= began - 1 and status.get("finished", 0) >= status.get("started", 0)
             and status.get("pid") == initial.get("pid") and status.get("started") == initial.get("started"))
    if valid:
        try:
            receipt.failures = failures(jobs.path(receipt.job), root) if code else []
            receipt.complete = not code or bool(receipt.failures)
            receipt.valid = bool(authenticated and receipt.complete)
        except (OSError, ValueError, TypeError, ET.ParseError):
            pass
    receipt.detail = "" if receipt.valid else "missing, invalid or changed-tree execution evidence"
    return int(code), text, receipt


def maintained(root: Path, argv: list[str]) -> bool:
    return (len(argv) >= 3 and argv[0] == sys.executable
            and (root / argv[1]).resolve() == (root / "scripts/test").resolve()
            and argv[2] in ("all", "gate"))
