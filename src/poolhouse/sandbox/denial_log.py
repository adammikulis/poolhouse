"""What Seatbelt refused, read from one ``log stream`` started before the first confined command.

The kernel's refusal reports carry the tag of the profile that refused and are not kept for a
later ``log show``, so the stream runs from the first confined command to the end of the process.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from collections import OrderedDict, deque

from poolhouse.platform import start_grouped

__all__ = ["DenialLog", "parse", "shared"]

logger = logging.getLogger("poolhouse.sandbox")

LOG = "/usr/bin/log"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PROBE_PROGRAM = "/usr/bin/true"
PREFIX = "poolhouse-sandbox:"
PROBE = "probe~"
PREDICATE = f'eventMessage CONTAINS "{PREFIX}"'

START_SECONDS = 5.0
SETTLE_SECONDS = 5.0
RETRY_SECONDS = 60.0
KEEP_TAGS = 256
KEEP_PER_TAG = 1000

DENIAL = re.compile(r"Sandbox: (?P<proc>[^(]+)\((?P<pid>\d+)\) deny\(\d+\) (?P<op>[\w*-]+)\s*(?P<target>.*)",
                    re.DOTALL)
NOISE = re.compile(
    r"\.CFUserTextEncoding$|/\.?GlobalPreferences[^/]*\.plist$|^com\.apple\.SystemConfiguration\.configd$"
    r"|^/Users/[^/]+$|^com\.apple\.analyticsd$|^com\.apple\.system\.opendirectoryd\.membership$"
    r"|^net\.routetable\.|^/private/etc/localtime$|^/Library/Preferences/com\.apple\.networkd\.plist$"
    r"|^/Library/Preferences/Logging/com\.apple\.diagnosticd\.filter\.plist$|^com\.apple\.diagnosticd$")
"""Targets every confined process probes at start-up and survives being refused."""


def parse(line: str) -> tuple[str, dict[str, str]] | None:
    """``(tag, refusal)`` for one ndjson event of the stream; None for any line that is not a refusal.

    The profile puts its tag on the last line of the message, so a target holding a newline
    cannot name the tag of another run.
    """
    if not line.startswith("{"):
        return None
    try:
        message = json.loads(line).get("eventMessage")
    except (ValueError, AttributeError):
        return None
    if not isinstance(message, str):
        return None
    body, _, last = message.rpartition("\n")
    found = DENIAL.fullmatch(body)
    if found is None or not last.startswith(PREFIX):
        return None
    return last[len(PREFIX):], {"process": found["proc"].strip(), "operation": found["op"],
                                "target": found["target"].strip()}


class DenialLog:
    """A ``log stream`` of every refusal tagged by a poolhouse profile, and the refusals it carried per tag."""

    def __init__(self) -> None:
        self._start_lock = threading.Lock()
        self._changed = threading.Condition()
        self._entries: OrderedDict[str, list[dict[str, str]]] = OrderedDict()
        self._probes: dict[str, bool] = {}
        self._said: deque[str] = deque(maxlen=3)
        self._proc: subprocess.Popen[str] | None = None
        self._owner = 0
        self._failed_at: float | None = None
        atexit.register(self.close)

    def running(self) -> bool:
        """Whether this process started the stream and it is alive."""
        return self._proc is not None and self._owner == os.getpid() and self._proc.poll() is None

    def start(self) -> bool:
        """Start the stream unless it runs; True once it carries refusals."""
        with self._start_lock:
            if self.running():
                return True
            if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_SECONDS:
                return False
            try:
                self._launch()
            except OSError as exc:
                return self._fail(f"could not start {LOG}: {exc}")
            if not self._probe("ready", START_SECONDS):
                said = "; ".join(self._said)
                self.close()
                return self._fail(f"the system log did not carry a test refusal ({said or 'it said nothing'})")
            self._failed_at = None
            return True

    def settle(self) -> bool:
        """Wait until every refusal logged before this call has arrived; False when there is no stream."""
        return self.start() and self._probe("settle", SETTLE_SECONDS)

    def denials(self, tag: str) -> list[dict[str, str]]:
        """The refusals the kernel logged for the run tagged ``tag``, without the start-up noise."""
        if not self.settle():
            return []
        with self._changed:
            return [dict(entry) for entry in self._entries.get(tag, ())]

    def close(self) -> None:
        """Stop the stream this process started."""
        proc, self._proc = self._proc, None
        if proc is None or self._owner != os.getpid():
            return
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        with contextlib.suppress(OSError):
            proc.stdout.close()

    def _launch(self) -> None:
        self.close()
        with self._changed:
            self._entries.clear()
        self._proc = start_grouped(
            [LOG, "stream", "--style", "ndjson", "--predicate", PREDICATE],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._owner = os.getpid()
        threading.Thread(target=self._read, args=(self._proc,), daemon=True, name="denial-log").start()

    def _fail(self, reason: str) -> bool:
        self._failed_at = time.monotonic()
        logger.warning("sandbox refusals cannot be reported: %s", reason)
        return False

    def _read(self, proc: subprocess.Popen[str]) -> None:
        with contextlib.suppress(ValueError, OSError):
            for line in proc.stdout:
                found = parse(line)
                if found is not None:
                    self._add(*found)
                elif not line.startswith("{") and line.strip():
                    self._said.append(line.strip()[:200])

    def _add(self, tag: str, entry: dict[str, str]) -> None:
        with self._changed:
            if tag.startswith(PROBE):
                if tag in self._probes:
                    self._probes[tag] = True
                    self._changed.notify_all()
                return
            bucket = self._entries.setdefault(tag, [])
            self._entries.move_to_end(tag)
            if not NOISE.search(entry["target"]) and len(bucket) < KEEP_PER_TAG:
                bucket.append(entry)
            while len(self._entries) > KEEP_TAGS:
                self._entries.popitem(last=False)
            self._changed.notify_all()

    def _probe(self, kind: str, seconds: float) -> bool:
        """Have the kernel refuse a profile tagged with a fresh token, again until the stream carries it."""
        token = f"{PROBE}{kind}-{secrets.token_hex(4)}"
        profile = f'(version 1)(deny default (with message "{PREFIX}{token}"))'
        deadline = time.monotonic() + seconds
        with self._changed:
            self._probes[token] = False
        try:
            while time.monotonic() < deadline and self.running():
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    subprocess.run([SANDBOX_EXEC, "-p", profile, "--", PROBE_PROGRAM], stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=10, check=False)
                with self._changed:
                    if self._changed.wait_for(lambda: self._probes[token], 0.25):
                        return True
            return False
        finally:
            with self._changed:
                del self._probes[token]


_SHARED = DenialLog()


def shared() -> DenialLog:
    """The stream of this process."""
    return _SHARED
