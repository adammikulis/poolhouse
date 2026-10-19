"""What Seatbelt refused, read from one ``log stream`` started before the first confined command.

The kernel's refusal reports carry the tag of the profile that refused and are not kept for a
later ``log show``, so the stream runs from the first confined command to the end of the process.
The kernel cuts a report longer than about a thousand bytes at its end, which removes the tag; the
stream also carries every kernel report that long. A cut report names the process that was refused, so
it makes unreadable the run that process belongs to (`process_tree`). A cut report of a process that no
run follows makes unreadable every run that started a process of its own (it may be one that ended
before it was followed) and every run whose processes were not followed from its start. ``denials`` is
None whenever the stream cannot say what a run was refused: it never started, did not answer, ended or
restarted after the run was watched, never watched the run, or a report of the run was cut off or
could not be read.
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
from dataclasses import dataclass, field

from poolhouse.platform import start_grouped
from poolhouse.sandbox import process_tree
from poolhouse.sandbox.backend import CUT_REFUSAL_LOG, CUT_UNTRACED_LOG, UNPARSED_REFUSAL_LOG

__all__ = ["DenialLog", "parse", "shared"]

logger = logging.getLogger("poolhouse.sandbox")

LOG = "/usr/bin/log"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PROBE_PROGRAM = "/usr/bin/true"
PREFIX = "poolhouse-sandbox:"
PROBE = "probe~"
CUT_CHARS = 200  # the stream's character floor for a kernel report; a report of multi-byte characters has fewer characters than bytes
CUT_BYTES = 900  # a kernel report this long may have been cut: the kernel's limit is about 1010 bytes
CUT_MARK = "<\u2026>"  # what the kernel puts at the end of a report it cut
LONG_PAD = "x" * 300
PREDICATE = (f'eventMessage CONTAINS "{PREFIX}" OR '
             f'(senderImagePath ENDSWITH "/Sandbox" AND eventMessage MATCHES "(?s).{{{CUT_CHARS},}}")')
KERNEL = "/kernel"
SANDBOX_KEXT = "/Sandbox.kext/Contents/MacOS/Sandbox"
NOT_STREAMED = -1

START_SECONDS = 5.0
SETTLE_SECONDS = 5.0
RETRY_SECONDS = 60.0
KEEP_TAGS = 256
KEEP_PER_TAG = 1000

HEAD = r"Sandbox: (?P<proc>[^\n]+?)\((?P<pid>\d+)\) deny\(\d+\) "
DENIAL = re.compile(HEAD + r"(?P<op>[\w*-]+)\s*(?P<target>.*)", re.DOTALL)
DUPLICATE = re.compile(r"\d+ duplicate reports? for (?=Sandbox: )")
"""The kernel's note that it left out repeats of a refusal it had reported already."""
REPORT_START = re.compile(r"(?:\d+ duplicate reports? for )?Sandbox: ")
PID_BEFORE_DENY = re.compile(r"(?=\((\d+)\) deny\()")
PROBE_PROCESS = "sandbox-exec"
NOISE = re.compile(
    r"\.CFUserTextEncoding$|/\.?GlobalPreferences[^/]*\.plist$|^com\.apple\.SystemConfiguration\.configd$"
    r"|^/Users/[^/]+$|^com\.apple\.analyticsd$|^com\.apple\.system\.opendirectoryd\.membership$"
    r"|^net\.routetable\.|^/private/etc/localtime$|^/Library/Preferences/com\.apple\.networkd\.plist$"
    r"|^/Library/Preferences/Logging/com\.apple\.diagnosticd\.filter\.plist$|^com\.apple\.diagnosticd$")
"""Targets every confined process probes at start-up and survives being refused."""


def kernel_message(line: str) -> str | None:
    """The message of one ndjson event of the stream when the kernel's Sandbox extension sent it, else None.

    Only an event the kernel's Sandbox extension sent counts, so another process's log line cannot
    pose as a refusal.
    """
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
        message = data.get("eventMessage")
        sender = data.get("senderImagePath")
        process = data.get("processImagePath")
    except (ValueError, AttributeError):
        return None
    if not isinstance(message, str) or process != KERNEL or not (isinstance(sender, str) and sender.endswith(SANDBOX_KEXT)):
        return None
    return message


def trailing_tag(message: str) -> str | None:
    """The tag on the last line of ``message``. The profile puts its tag there, so a target holding
    a newline cannot name the tag of another run."""
    last = message.rpartition("\n")[2]
    return last[len(PREFIX):] if last.startswith(PREFIX) else None


def refusal(message: str) -> dict[str, str] | None:
    """The process, operation and target a kernel message reports, or None when it reports no refusal."""
    found = DENIAL.fullmatch(message.rpartition("\n")[0])
    if found is None:
        return None
    return {"process": found["proc"].strip(), "operation": found["op"], "target": found["target"].strip()}


def parse(line: str) -> tuple[str, dict[str, str]] | None:
    """``(tag, refusal)`` for one ndjson event of the stream; None for any line that is not a tagged refusal."""
    message = kernel_message(line)
    if message is None:
        return None
    tag, entry = trailing_tag(message), refusal(message)
    return None if tag is None or entry is None else (tag, entry)


def repeated(message: str) -> dict[str, str] | None:
    """The refusal the kernel's note of repeats names, or None when ``message`` is no such note or names none."""
    note = DUPLICATE.match(message)
    return None if note is None else refusal(message[note.end():])


def reported_pids(message: str) -> tuple[int, ...]:
    """The pids a kernel Sandbox message may name as the process that was refused, in order: the
    ``(pid) deny(`` of each place that reads so. A process name or a path can hold the same text, so the
    pid of the process that was refused is one of several then, and never missing."""
    if REPORT_START.match(message) is None:
        return ()
    return tuple(int(pid) for pid in PID_BEFORE_DENY.findall(message))


def may_be_cut(message: str) -> bool:
    """Whether the kernel may have cut the end of ``message`` off: it is as long as the kernel's limit lets one be, or says so."""
    return message.endswith(CUT_MARK) or len(message.encode("utf-8", "surrogatepass")) >= CUT_BYTES


@dataclass(slots=True)
class _Run:
    """One watched run: the stream it was watched on, what that stream carried for it, the processes
    it started, the processes a cut report was heard for while it ran (and whether one named no process),
    whether a report of its own could not be read, and whether it has ended (then what the cut reports it
    heard say of it is kept in ``cut_reason`` and its processes are no longer followed)."""

    generation: int
    entries: list[dict[str, str]] = field(default_factory=list)
    tree: process_tree.Tree | None = None
    cut_own: bool = False
    cut_pids: set[int] = field(default_factory=set)
    cut_flood: bool = False
    cut_blind: bool = False
    cut_reason: str = ""
    unparsed: bool = False
    ended: bool = False


class DenialLog:
    """A ``log stream`` of every refusal tagged by a poolhouse profile, and the refusals it carried per watched run."""

    def __init__(self) -> None:
        self._start_lock = threading.Lock()
        self._changed = threading.Condition()
        self._runs: OrderedDict[str, _Run] = OrderedDict()
        self._probes: dict[str, bool] = {}
        self._said: deque[str] = deque(maxlen=3)
        self._proc: subprocess.Popen[str] | None = None
        self._owner = 0
        self._generation = 0
        self._failed_at: float | None = None
        self._exit_hook = False

    def running(self) -> bool:
        """Whether this process started the stream and it is alive."""
        return self._proc is not None and self._owner == os.getpid() and self._proc.poll() is None

    def start(self) -> bool:
        """Start the stream unless it runs; True once it carries refusals."""
        return self._ensure() is not None

    def watch(self, tag: str) -> bool:
        """Start the stream unless it runs and register the run tagged ``tag`` on it; True when it can be read back."""
        generation = self._ensure()
        with self._changed:
            self._runs[tag] = _Run(NOT_STREAMED if generation is None else generation)
            self._trim()
        return generation is not None

    def track(self, tag: str, pid: int) -> None:
        """Follow the processes of the run tagged ``tag``, which started as ``pid``, so that a cut report is charged to the run it belongs to."""
        tree = process_tree.follow(pid)
        with self._changed:
            run = self._runs.get(tag)
            if run is not None and not run.ended:
                run.tree = tree
                return
        process_tree.release(tree)

    def finish(self, tag: str) -> None:
        """Mark the run tagged ``tag`` as ended and stop following its processes; its refusals stay readable until the cap needs the room."""
        process_tree.flush()
        tree = None
        with self._changed:
            run = self._runs.get(tag)
            if run is not None and not run.ended:
                run.cut_reason = self._cut_reason(run)
                run.ended = True
                tree, run.tree = run.tree, None
                run.cut_pids.clear()
                if tree is not None:
                    self._explain(tree)
            self._trim()
        if tree is not None:
            process_tree.release(tree)

    def settle(self) -> bool:
        """Wait until every refusal logged before this call has arrived; False when there is no stream."""
        if not self.start():
            return False
        if self._probe("settle", SETTLE_SECONDS):
            return True
        logger.warning("sandbox refusals cannot be reported: the system log stream did not answer in %s s", SETTLE_SECONDS)
        return False

    def denials(self, tag: str) -> list[dict[str, str]] | None:
        """The refusals the kernel logged for the run tagged ``tag``, without the start-up noise; None when they cannot be known."""
        with self._changed:
            run = self._runs.get(tag)
        if run is None or run.generation == NOT_STREAMED:
            logger.warning("sandbox refusals cannot be reported: no system log stream was running when the command started")
            return None
        if not self.settle():
            return None
        process_tree.flush()
        with self._changed:
            if self._generation != run.generation or not self.running():
                logger.warning("sandbox refusals cannot be reported: the system log stream restarted or ended after the command started")
                return None
            if why := self._why(run):
                logger.warning("sandbox refusals cannot be reported: %s", why)
                return None
            return [dict(entry) for entry in run.entries]

    def why_unreadable(self, tag: str) -> str:
        """Why the refusals of the run tagged ``tag`` cannot be known from the reports heard for it, or an empty string."""
        process_tree.flush()
        with self._changed:
            run = self._runs.get(tag)
            return "" if run is None else self._why(run)

    def _why(self, run: _Run) -> str:
        """Why ``run``'s refusals cannot be known from the reports heard, or an empty string. Call it holding the lock."""
        return UNPARSED_REFUSAL_LOG if run.unparsed else run.cut_reason if run.ended else self._cut_reason(run)

    def _cut_reason(self, run: _Run) -> str:
        """Why the cut reports heard so far make ``run`` unreadable, or an empty string. Call it holding the lock.
        A report of a process that no run follows is not the run's only when the run started nothing but
        the process it was started as: a process of its own that ended at once may be the one it names."""
        tree = run.tree
        if run.cut_own or (tree is not None and tree.holds_any(run.cut_pids)):
            return CUT_REFUSAL_LOG
        if run.cut_blind:
            return CUT_UNTRACED_LOG
        if tree is not None and tree.sealed:
            return ""
        if run.cut_flood or any(not self._followed(pid) for pid in run.cut_pids):
            return CUT_UNTRACED_LOG
        return ""

    def _followed(self, pid: int) -> bool:
        """Whether ``pid`` is a process of a run still going that this stream watches."""
        return any(not run.ended and run.tree is not None and pid in run.tree for run in self._runs.values())

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

    def _ensure(self) -> int | None:
        """The generation of the running stream, starting one if needed; None when there is none."""
        with self._start_lock:
            if self.running():
                return self._generation
            if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_SECONDS:
                return None
            try:
                self._launch()
            except OSError as exc:
                self._fail(f"could not start {LOG}: {exc}")
                return None
            for kind, what in (("ready", "a test refusal"), ("cut", "a long test refusal")):
                if not self._probe(kind, START_SECONDS):
                    said = "; ".join(self._said)
                    self.close()
                    self._fail(f"the system log did not carry {what} ({said or 'it said nothing'})")
                    return None
            self._failed_at = None
            return self._generation

    def _launch(self) -> None:
        self.close()
        self._generation += 1
        self._proc = start_grouped(
            [LOG, "stream", "--style", "ndjson", "--predicate", PREDICATE],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._owner = os.getpid()
        if not self._exit_hook:
            self._exit_hook = True
            atexit.register(self.close)
        threading.Thread(target=self._read, args=(self._proc,), daemon=True, name="denial-log").start()

    def _fail(self, reason: str) -> None:
        self._failed_at = time.monotonic()
        logger.warning("sandbox refusals cannot be reported: %s", reason)

    def _read(self, proc: subprocess.Popen[str]) -> None:
        with contextlib.suppress(ValueError, OSError):
            for line in proc.stdout:
                message = kernel_message(line)
                if message is not None:
                    self._hear(message)
                elif not line.startswith("{") and line.strip():
                    self._said.append(line.strip()[:200])

    def _hear(self, message: str) -> None:
        """Record what one kernel message says about a run, or about a probe. A message that no probe
        or watched run owns and that may have been cut off is charged to the runs still going: to the one
        whose process it names, and to those whose processes were not followed from their start."""
        tag, entry = trailing_tag(message), refusal(message)
        with self._changed:
            run = None if tag is None else self._runs.get(tag)
            if answered := self._answered(message, entry):
                self._probes.update(dict.fromkeys(answered, True))
            elif run is not None:
                self._take(run, message, entry)
            elif may_be_cut(message):
                self._cut(reported_pids(message))
            self._changed.notify_all()

    def _answered(self, message: str, entry: dict[str, str] | None) -> list[str]:
        """The probes in flight that ``message`` answers: the report of the probe program's refusal that ends
        in the probe's token. Call it holding the lock."""
        if entry is None or entry["process"] != PROBE_PROCESS or entry["target"] != PROBE_PROGRAM:
            return []
        last = message.rpartition("\n")[2]
        return [token for token in self._probes if last in (PREFIX + token, token + LONG_PAD)]

    @staticmethod
    def _take(run: _Run, message: str, entry: dict[str, str] | None) -> None:
        """Keep what a message that carries ``run``'s tag reported; one that is neither a refusal nor a
        note of repeats of a refusal the run was reported makes the run unreadable."""
        if entry is None:
            noted = repeated(message)
            known = noted is not None and (NOISE.search(noted["target"]) or noted in run.entries or len(run.entries) >= KEEP_PER_TAG)
            run.unparsed = run.unparsed or not known
        elif not NOISE.search(entry["target"]) and len(run.entries) < KEEP_PER_TAG:
            run.entries.append(entry)

    def _cut(self, pids: tuple[int, ...]) -> None:
        """Remember that a report naming the processes ``pids`` was cut off, for every run still going."""
        for run in self._runs.values():
            if run.ended:
                continue
            if not pids:
                run.cut_blind = True
            elif run.tree is not None and any(pid in run.tree for pid in pids):
                run.cut_own = True
            elif len(run.cut_pids) + len(pids) <= KEEP_PER_TAG:
                run.cut_pids.update(pids)
            else:
                run.cut_flood = True

    def _explain(self, tree: process_tree.Tree) -> None:
        """A run has ended: the cut reports of its processes are not the other runs' to answer for. Call it holding the lock."""
        for run in self._runs.values():
            run.cut_pids = {pid for pid in run.cut_pids if pid not in tree}

    def _trim(self) -> None:
        """Drop the oldest ended runs while there are more than ``KEEP_TAGS``; a run still going is never dropped."""
        for tag in [tag for tag, run in self._runs.items() if run.ended]:
            if len(self._runs) <= KEEP_TAGS:
                break
            del self._runs[tag]

    def _probe(self, kind: str, seconds: float) -> bool:
        """Have the kernel refuse a profile tagged with a fresh token, again until the stream carries it.
        The ``cut`` kind puts the token in a message long enough for the stream's second clause to carry it."""
        token = f"{PROBE}{kind}-{secrets.token_hex(8)}"
        said = f"{token}{LONG_PAD}" if kind == "cut" else f"{PREFIX}{token}"
        profile = f'(version 1)(deny default (with message "{said}"))'
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
