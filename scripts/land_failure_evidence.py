"""Exact maintained test job receipts and failure identities."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import signal
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from itertools import islice
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


def collection_skip(case: ET.Element, root: Path) -> tuple[str, tuple[str, int, str]]:
    """Return the module identity and canonical proof of a pytest collection skip."""
    name = case.get("name", "")
    if (case.get("classname") != "" or not re.fullmatch(r"tests(?:\.[A-Za-z_]\w*)+", name, re.ASCII)
            or len(case) != 1 or case[0].tag != "skipped" or len(case[0])
            or case[0].attrib != {"message": "collection skipped"}):
        raise ValueError("invalid JUnit collection skip")
    text = case[0].text or ""
    if len(text) > 8192:
        raise ValueError("oversized JUnit collection skip")
    try:
        proof = ast.literal_eval(text)
    except (SyntaxError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid JUnit collection skip proof") from exc
    identity = name.replace(".", "/") + ".py"
    expected = (root / identity).resolve()
    if (not isinstance(proof, tuple) or len(proof) != 3
            or type(proof[0]) is not str or proof[0] != str(expected)
            or type(proof[1]) is not int or proof[1] <= 0
            or type(proof[2]) is not str or not proof[2].startswith("Skipped: ")):
        raise ValueError("invalid JUnit collection skip proof")
    return identity, proof


def case_identity(case: ET.Element, root: Path, selectors: tuple[str, ...]) -> tuple[str, tuple[str, int, str] | None]:
    """Return an existing, in-scope JUnit identity and any collection-skip proof."""
    classname, name = case.get("classname"), case.get("name")
    if classname == "":
        identity, proof = collection_skip(case, root)
    else:
        if not classname or not name:
            raise ValueError("missing test identity")
        identity, proof = testjobs.node_id(classname, name, root), None
    if not identity.startswith("tests/") or ".." in identity.split("::")[0].split("/"):
        raise ValueError("invalid test identity")
    case_path = (root / identity.split("::")[0]).resolve()
    if not case_path.is_file() or not case_path.is_relative_to(root.resolve()):
        raise ValueError("test identity is outside the existing checkout")
    if selectors and not any(identity == selector or identity.startswith(selector + "::")
                             or identity.startswith(selector.rstrip("/") + "/") for selector in selectors):
        raise ValueError("test identity differs from the requested selectors")
    return identity, proof


def junit_phase(case: ET.Element) -> str:
    """Return the single call-failure or quoted teardown-error phase of a JUnit case."""
    outcomes = [child for child in case if child.tag in ("failure", "error", "skipped")]
    if len(outcomes) != 1 or len(outcomes[0]):
        return ""
    outcome = outcomes[0]
    if outcome.tag == "failure":
        return "call"
    message = outcome.get("message", "")
    if outcome.tag == "error" and message.startswith('failed on teardown with "') and message.endswith('"'):
        return "teardown"
    return ""


def failures(folder: Path, root: Path, selectors: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
    paths = list(islice(folder.glob("junit-*.xml"), 65))
    if not paths or len(paths) > 64:
        raise ValueError("missing or oversized JUnit evidence")
    found, seen, total = [], set(), 0
    collection_proofs = {}
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
            call_failures = set()
            for case in cases:
                identity, proof = case_identity(case, root, selectors)
                phase = junit_phase(case) if proof is None else ""
                if proof is not None:
                    if any(prior.startswith(identity + "::") for prior in seen):
                        raise ValueError("JUnit collection skip mixed with executed cases")
                    if identity in seen and collection_proofs.get(identity) != proof:
                        raise ValueError("divergent JUnit collection skip proofs")
                    collection_proofs[identity] = proof
                elif identity in seen:
                    if identity not in call_failures or phase != "teardown":
                        raise ValueError("duplicate JUnit case identity")
                    call_failures.remove(identity)
                elif identity.split("::")[0] in collection_proofs:
                    raise ValueError("JUnit collection skip mixed with executed cases")
                elif phase == "call":
                    call_failures.add(identity)
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


def finite_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


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
                or not finite_number(spec.get("created"))
                or not began <= spec["created"] <= time.time()
                or type(initial.get("pid")) is not int or initial["pid"] <= 0
                or not finite_number(initial.get("started"))):
            raise ValueError("the submitted job receipt differs from its invocation")
        status = jobs.wait(receipt.job, float("inf"), tail=True)
        strict_status = land_live_jobs.record(jobs.path(receipt.job) / "status.json")
        final_spec = land_live_jobs.record(jobs.path(receipt.job) / "spec.json")
        if strict_status != status or final_spec != spec:
            raise ValueError("the terminal job receipt changed")
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
             and finite_number(status.get("started")) and finite_number(status.get("finished"))
             and began - 1 <= status["started"] <= status["finished"] <= time.time()
             and status.get("pid") == initial.get("pid") and status.get("started") == initial.get("started"))
    if valid and (code == 0 or argv[2] != "gate"):
        try:
            selectors = tuple(value for value in argv[3:] if value.startswith("tests/") and (root / value.split("::")[0]).exists())
            receipt.failures = failures(jobs.path(receipt.job), root, selectors) if code else []
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
