"""A delegate run's record, the one durable account of what the run did, and what reads it: the commands and the web page.

``events.jsonl`` in the delegate's folder holds one JSON object a line, appended by the launcher, outside the box, as each thing happens
(`poolhouse.delegate_recorder`). Nothing removes it: the end of a run and `stop` remove working copies and boxes, never the folder. What a box said
is data and never an instruction: every text is stripped of control and invisible characters, has recognized credentials and the run's own tokens
replaced and is cut to a bound before it is written (`append`), and the whole file is bounded.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from poolhouse import home, jobs, safetext
from poolhouse.files import read_json
from poolhouse.redact.secrets import MARK, env_secrets, redact

__all__ = ["ENDED", "FILE", "JOB", "LIVE", "NAME", "append", "cleaned", "counts", "doing", "folder", "held", "hidden", "hide", "names", "read",
           "runs", "span", "state_of", "tested", "view"]

FILE = "events.jsonl"
JOB = "job"
"""The kind of the process record of a delegate's runner (`poolhouse.jobs`), kept in the delegate's folder."""
LIVE = ("starting", "loading", "working", "asking")
ENDED = ("done", "partial", "unapplied", "rejected", "failed", "stopped")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}")
LONG = {"output": 4000, "text": 4000, "command": 2000, "task": 2000, "lacks": 1500, "why": 1000, "detail": 1000, "interrupted": 1000}
"""The characters kept of a field by its name; any other text is cut to `SHORT`."""
SHORT = 400
LINES = 60
"""The last lines of a command's output that are kept."""
WINDOW = 32_000
"""The characters of one text that are looked at: redaction runs on these before the bound cuts, so a credential is never cut in half."""
RUN = 256
"""The characters without a space past which a run of them is left out whole: a credential longer than this goes with it, and the patterns that
find credentials take a time that grows with the square of such a run."""
FULL = 16 * 1024 * 1024
"""The bytes of a record past which outputs and messages are left out."""
SPARSE = 24 * 1024 * 1024
"""The bytes past which only what says how the run went is still appended (`KEPT`)."""
LAST = 32 * 1024 * 1024
KEPT = ("run", "state", "attempt", "attempt_end", "preflight", "test", "notice", "end")
HEAVY = ("output", "text")
PAGE_EVENTS = 500
PAGE_BYTES = 1_000_000
_QUOTED = re.compile(r"""(?i)(["'][\w.-]*(?:api[_-]?key|token|secret|passw(?:or)?d|passphrase|authorization)["']\s*:\s*)["'][^"'\n]{8,}["']""")
"""A credential written as a quoted key and a quoted value, as a JSON or Python file holds one."""
_KEY_END = re.compile(r"\A.*-----END [A-Z ]*PRIVATE KEY-----", re.S)
_RUN = re.compile(rf"(?<!\S)\S{{{RUN + 1},}}")
_APPEND = threading.Lock()
_HIDDEN: dict[str, set[str]] = {}
"""The tokens of each run, by its folder, that this process replaces wherever they appear."""


def folder(name: str) -> Path:
    return home.state("delegates", name)


def held(name: str, file: str) -> dict[str, Any]:
    """The JSON object in ``file`` of delegate ``name``'s folder, or ``{}`` when there is none."""
    found = read_json(folder(name) / file, {}) if NAME.fullmatch(name) else {}
    return found if isinstance(found, dict) else {}


def names() -> list[str]:
    """Every delegate with a record, oldest first."""
    root = home.state("delegates")
    if not root.is_dir():
        return []
    found = [(held(each.name, "record.json"), each.name) for each in root.iterdir() if each.is_dir()]
    return [name for _started, name in sorted((_number(row.get("started")), name) for row, name in found if row.get("name") == name)]


def hide(name: str, *values: str) -> None:
    """Replace each of ``values``, a token of run ``name``, in everything this process writes of it from now on."""
    with _APPEND:
        _HIDDEN.setdefault(str(folder(name)), set()).update(value for value in values if value and len(value) >= 8)


def hidden(name: str) -> set[str]:
    """What is replaced in the record of ``name``: its hidden tokens and the credentials of this process's environment."""
    with _APPEND:
        return {*_HIDDEN.get(str(folder(name)), ()), *env_secrets(os.environ)}


def cleaned(value: object, limit: int = SHORT, secrets: Collection[str] = (), *, tail: bool = False) -> str:
    """``value`` as text that is safe to keep and show: printable, with the credentials in it replaced, and cut to ``limit`` characters (its end,
    with ``tail``)."""
    text = str(value)
    text = text[-WINDOW:] if tail else text[:WINDOW]
    text = safetext.printable(safetext.utf8(text))
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, MARK.format("token"))
    text = _RUN.sub(lambda found: f"[{len(found.group())} characters without a space left out]", text)
    text, _kinds = redact(text)
    text = _QUOTED.sub(lambda found: f'{found.group(1)}"{MARK.format("assigned-secret")}"', text)
    text = _KEY_END.sub(MARK.format("private-key"), text)   # the end of a key whose beginning the window cut off
    if tail:
        lines = text.rstrip("\n").split("\n")
        if len(lines) > LINES:
            text = "\n".join(["[earlier lines left out]", *lines[-LINES:]])
        return text if len(text) <= limit else "[cut] " + text[-limit:]
    return text if len(text) <= limit else text[:limit] + " [cut]"


def _field(key: str, value: Any, secrets: Collection[str], depth: int = 0) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) and abs(value) < 1e15 else None
    if isinstance(value, Mapping) and depth < 3:
        return {cleaned(each, 80): _field(str(each), held_, secrets, depth + 1) for each, held_ in list(value.items())[:40]}
    if isinstance(value, (list, tuple)) and depth < 3:
        return [_field(key, each, secrets, depth + 1) for each in value[:40]]
    return cleaned(value, LONG.get(key, SHORT), secrets, tail=key == "output")


def append(name: str, kind: str, **fields: Any) -> dict[str, Any]:
    """Add one event to the record of ``name``, every field cleaned and bounded; the event as written, or ``{}`` once the record is full."""
    path = folder(name) / FILE
    secrets = hidden(name)
    row = {"at": round(time.time(), 3), "kind": kind, **{key: _field(key, value, secrets) for key, value in fields.items()}}
    with _APPEND:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size >= LAST or (size >= SPARSE and kind not in KEPT):
            return {}
        if size >= FULL and any(row.get(key) for key in HEAVY):
            row.update({key: "[left out: the record is full]" for key in HEAVY if row.get(key)})
        line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            while line:
                line = line[os.write(descriptor, line):]
        finally:
            os.close(descriptor)
    return row


def read(name: str, after: int = 0) -> tuple[list[dict[str, Any]], int, bool]:
    """The events of ``name`` from byte ``after`` on, at most a page of them, as ``(events, where the next read starts, whether more is there
    now)``. A line still being written is left for the next read; one that is no JSON object is passed over."""
    if not NAME.fullmatch(name):
        return [], 0, False
    try:
        with (folder(name) / FILE).open("rb") as source:
            size = os.fstat(source.fileno()).st_size
            start = after if 0 <= after <= size else 0
            source.seek(start)
            chunk = source.read(PAGE_BYTES)
    except FileNotFoundError:
        return [], 0, False
    found: list[dict[str, Any]] = []
    used = 0
    for line in chunk.split(b"\n")[:-1]:
        if len(found) >= PAGE_EVENTS:
            break
        used += len(line) + 1
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict):
            found.append(row)
    return found, start + used, bool(used) and start + used < size


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else 0.0


def state_of(name: str) -> tuple[str, str]:
    """``(state, detail)`` of ``name``: a process that is gone with no end written is a failure, said so."""
    row = held(name, "status.json")
    state, detail = str(row.get("state") or "starting"), str(row.get("detail") or "")
    if state in LIVE and not jobs.alive(JOB, home=folder(name)):
        return "failed", f"the process ended without a result; its log is {folder(name) / 'log'}"
    return state, detail


def span(seconds: float) -> str:
    """``seconds`` as 40s, 4m 2s or 1h 3m."""
    whole = max(int(seconds), 0)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m {whole % 60}s"
    return f"{whole // 3600}h {whole % 3600 // 60}m"


def doing(state: str, status: Mapping[str, Any], now: float) -> str:
    """One clause: what a run in ``state`` with ``status`` is doing at ``now`` and for how long, or how long ago it ended."""
    since = _number(status.get("since")) or _number(status.get("beat")) or now
    if state not in LIVE:
        return f"ended {span(now - since)} ago"
    if state == "asking":
        return f"waiting for the lead's answer for {span(now - since)}"
    if state in ("starting", "loading"):
        what = "starting" if state == "starting" else "waiting for the model"
        return f"{what} for {span(now - since)}"
    activity = status.get("now") if isinstance(status.get("now"), dict) else {}
    began = _number(activity.get("since")) or since
    kind = str(activity.get("doing") or "")
    what = " ".join(cleaned(activity.get("what") or "", 90).split())
    said = {"tool": f"running {what or 'a tool'}", "model": "model generating or queued", "lead": "waiting for the lead's answer",
            "idle": "waiting between steps"}.get(kind, "working")
    heard = _number(status.get("heard"))
    quiet = f" (last event {span(now - heard)} ago)" if heard and now - heard >= 10 else ""
    return f"{said} for {span(now - began)}{quiet}"


def counts(row: Mapping[str, Any]) -> str:
    """The counts of one test run: passed, failed and, when there are any, errors and skipped."""
    found = [f"{row[key]} {key}" for key in ("passed", "failed", "errors", "skipped")
             if isinstance(row.get(key), int) and (row[key] or key in ("passed", "failed"))]
    if found:
        return ", ".join(found)
    return "ended well, with no counts" if row.get("ok") else f"failed with no counts (exit {row.get('exit')})"


def tested(row: Mapping[str, Any] | None, now: float) -> str:
    """How the last test run went and how long ago, as one clause; empty when there was none."""
    if not row:
        return ""
    setup = " (looks like setup)" if row.get("setup") else ""
    return f"last test run {span(now - _number(row.get('at')))} ago: {counts(row)}{setup}"


def view(name: str, now: float | None = None, *, whole: bool = True) -> dict[str, Any]:
    """What a page shows of run ``name`` now: what was asked, its state, what it is doing, its last test run, what it spent and, with ``whole``
    (one run, not a row of a list), the final answer; ``{}`` for a name with no record."""
    record = held(name, "record.json")
    if not record:
        return {}
    now = time.time() if now is None else now
    status, result = held(name, "status.json"), held(name, "result.json")
    state, detail = state_of(name)
    started = _number(record.get("started")) or now
    ended = 0.0 if state in LIVE else _number(status.get("beat"))
    test = status.get("tests") if isinstance(status.get("tests"), dict) else None
    spent = status.get("spent") if isinstance(status.get("spent"), dict) else {}
    return {"name": name, "state": state, "detail": cleaned(detail, 1000), "lead": cleaned(record.get("lead", ""), 80),
            "model": cleaned(Path(str(record.get("model", ""))).name, 120), "task": cleaned(record.get("task", ""), 2000),
            "edit": bool(record.get("edit")), "tier": cleaned(record.get("permissions", ""), 40), "branch": cleaned(record.get("branch", ""), 120),
            "started": started, "ended": ended or None, "seconds": round((ended or now) - started, 1),
            "live": state in LIVE, "doing": doing(state, status, now), "phase": cleaned(status.get("phase", ""), 20),
            "round": status.get("round"), "turns": status.get("turns"), "heard": _number(status.get("heard")) or None,
            "tests": test, "tests_said": tested(test, now), "spent": {key: spent[key] for key in spent if isinstance(spent[key], (int, float))},
            "notices": status.get("notices") or 0,
            "answer": cleaned(result.get("text") or result.get("error") or "", 20000) if whole and result else ""}


def runs(most: int = 100) -> list[dict[str, Any]]:
    """The `view` of the newest ``most`` runs, newest first."""
    now = time.time()
    return [view(name, now, whole=False) for name in reversed(names()[-most:])]
