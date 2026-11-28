"""The launcher's side of a delegate's record (`poolhouse.delegate_record`): it takes in what the harness prints, what the box's hook asks and what
the launcher sees, as each arrives, and keeps what the run is doing now in its status.

Everything it is given from a box is data. It decides nothing about the run: a record that cannot be written is said in the run's log and the run
goes on.
"""

from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from poolhouse import delegate_record, delegate_store as store, delegate_testrun
from poolhouse.log import say
from poolhouse.redact.secrets import env_secrets

__all__ = ["Pass", "Recorder", "of"]

SHELL = ("shell", "bash")
FILES = ("file_path", "path", "filePath")
ABOUT = ("pattern", "question", "text")
"""The argument that says what a call is about, of a tool that has no command and no file."""


def _never_ends_a_run(method: Callable[..., None]) -> Callable[..., None]:
    """``method``, with a failure to record said in the log and nothing raised: what is recorded is a box's data and a disk can be full."""
    @functools.wraps(method)
    def recorded(*args: Any, **more: Any) -> None:
        try:
            method(*args, **more)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as why:
            say(f"this step is missing from the run's record: {type(why).__name__}: {why}")

    return recorded


class Recorder:
    """What the runner of delegate ``name`` writes in its record. ``hide`` names the run's own tokens, which are replaced wherever they appear."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.spent: dict[str, float] = {}
        self._lock = threading.RLock()

    def hide(self, *values: str) -> None:
        delegate_record.hide(self.name, *values)

    @_never_ends_a_run
    def add(self, kind: str, **fields: Any) -> None:
        """One event of the record; anything but a notice to the lead is also when the run was last heard."""
        delegate_record.append(self.name, kind, **fields)
        if kind != "notice":
            store.note(self.name, heard=time.time())

    def clean(self, value: object, limit: int = delegate_record.SHORT) -> str:
        return delegate_record.cleaned(value, limit, delegate_record.hidden(self.name))

    def spend(self, **more: float) -> None:
        with self._lock:
            for key, value in more.items():
                self.spent[key] = round(self.spent.get(key, 0) + value, 2)
            store.note(self.name, spent=dict(self.spent))

    def of_pass(self, ask: Any, asking: Callable[[], bool] = lambda: False) -> Pass:
        """The recorder of one session of the model, the pass ``ask`` (`delegate_review.Pass`); ``asking`` says whether a question of it waits for
        the lead."""
        return Pass(self, ask, asking)

    def ended(self, ask: Any, ran: Any, seconds: float) -> None:
        """The attempt ``ask`` is over, with what it came to (`delegate_review.Ran`)."""
        used = ran.answer.spent
        self.add("attempt_end", phase=ask.phase, round=ask.round, attempt=ask.attempt, seconds=round(seconds, 2), turns=used.turns,
                 input_tokens=used.input_tokens, output_tokens=used.output_tokens, cache_read_tokens=used.cache_read_tokens,
                 why=ran.answer.why if ran.answer.is_error else "", interrupted=ran.broke)


class Pass:
    """One attempt as it runs. `row` takes each line the harness prints, `asked` each tool call the hook was asked about, `beat` each look the
    launcher takes at whether a request is with the model server."""

    def __init__(self, recorder: Recorder, ask: Any, asking: Callable[[], bool]) -> None:
        self.recorder, self.ask, self.asking = recorder, ask, asking
        self.turn = 0
        self._open: dict[str, tuple[str, str, float]] = {}
        """The calls the hook let through that have no result yet, by id: the tool, what it is about and when it was asked."""
        self._busy, self._looked, self._model_s = False, time.monotonic(), 0.0
        self._doing: tuple[str, str] | None = None
        self._lock = threading.RLock()

    @_never_ends_a_run
    def boxed(self, box: Any) -> None:
        """The confinement of this attempt is proved: its tokens are never written, and what its preflight found is recorded."""
        self.recorder.hide(*env_secrets(box.env), *(str(getattr(each, "token", "")) for each in box.made.services))
        lacks, untested = str(box.made.lacks), str(box.untested)
        self.recorder.add("preflight", ok=not untested and "The preflight " not in lacks, untested=untested, lacks=lacks, phase=self.ask.phase)

    @_never_ends_a_run
    def started(self) -> None:
        """The harness is about to be started: an attempt, and with a number above 0 a restart of one that was broken off."""
        self.recorder.add("attempt", phase=self.ask.phase, round=self.ask.round, attempt=self.ask.attempt)
        with self._lock:
            self._looked = time.monotonic()
            self._show()

    def after(self, answered: Callable[[str, str, Mapping[str, Any], bool], None]) -> Callable[[str, str, Mapping[str, Any], bool], None]:
        """``answered``, and then `asked`: what a hook calls with each tool call it answered."""
        def both(call: str, name: str, arguments: Mapping[str, Any], denied: bool) -> None:
            answered(call, name, arguments, denied)
            self.asked(call, name, arguments, denied)

        return both

    @_never_ends_a_run
    def asked(self, call: str, name: str, arguments: Mapping[str, Any], denied: bool) -> None:
        tool = _tool(name)
        told = _told(tool, arguments)
        self.recorder.add("call", tool=tool, denied=denied, **told)
        with self._lock:
            if not denied:
                what = told.get("command") or told.get("file") or told.get("about") or ""
                shown = " ".join(self.recorder.clean(what, 160).split())
                self._open[call] = (tool, f"{tool} `{shown}`" if shown else tool, time.time())
            self._show()

    @_never_ends_a_run
    def row(self, row: Mapping[str, Any]) -> None:
        """One event the harness printed (``run --format json``)."""
        kind = row.get("type")
        part = row.get("part") if isinstance(row.get("part"), dict) else {}
        if kind == "step_start":
            self.turn += 1
            self._model_s = 0.0
            self.recorder.add("step", turn=self.turn, phase=self.ask.phase)
        elif kind == "text" and isinstance(part.get("text"), str):
            self.recorder.add("text", text=part["text"])
        elif kind == "tool_use":
            self._ran(part)
        elif kind == "step_finish":
            self._finished(part)
        elif kind == "error":
            told = row.get("error") if isinstance(row.get("error"), dict) else {}
            self.recorder.add("error", why=f"{told.get('type') or 'unknown'}: {told.get('message') or 'no message'}")

    @_never_ends_a_run
    def beat(self, with_model: bool) -> None:
        with self._lock:
            now = time.monotonic()
            self._model_s += now - self._looked if with_model or self._busy else 0.0
            self._busy, self._looked = with_model, now
            self._show()

    def _ran(self, part: Mapping[str, Any]) -> None:
        """A tool call that has ended, completed or in an error."""
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        arguments = state.get("input") if isinstance(state.get("input"), dict) else {}
        tool = _tool(str(part.get("tool") or ""))
        call = str(part.get("id") or part.get("callID") or "")
        with self._lock:
            if call not in self._open:
                call = next((each for each, held in self._open.items() if held[0] == tool), call)
            began = self._open.pop(call, None)
        exit_status = _exit(state)
        output = "\n".join(str(state[key]) for key in ("output", "error") if state.get(key))
        told = _told(tool, arguments)
        self.recorder.add("result", tool=tool, status=str(state.get("status") or ""), exit=exit_status, seconds=_seconds(state, began), output=output,
                         **told)
        command = str(arguments.get("command") or "")
        if tool in SHELL and (test := delegate_testrun.read(command, output, exit_status)):
            test = {**test, "setup": self.recorder.clean(test["setup"]), "command": self.recorder.clean(command, 300), "phase": self.ask.phase}
            self.recorder.add("test", **test)
            store.note(self.recorder.name, tests={**test, "at": time.time()})
        with self._lock:
            self._show()

    def _finished(self, part: Mapping[str, Any]) -> None:
        """A step is over: what it spent, how fast it was written and how much of its prompt the server reused."""
        used = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        cache = used.get("cache") if isinstance(used.get("cache"), dict) else {}
        read, wrote, reused, kept = (int(each or 0) for each in (used.get("input"), used.get("output"), cache.get("read"), cache.get("write")))
        wrote += int(used.get("reasoning") or 0)
        prompt, seconds = read + reused + kept, round(self._model_s, 2)
        self.recorder.add("usage", turn=self.turn, reason=str(part.get("reason") or ""), input_tokens=read, output_tokens=wrote,
                         cache_read_tokens=reused, reuse_percent=round(100 * reused / prompt) if prompt else None, model_seconds=seconds,
                         tokens_per_second=round(wrote / seconds, 1) if wrote and seconds >= 0.5 else None)
        self.recorder.spend(input_tokens=read, output_tokens=wrote, cache_read_tokens=reused, model_seconds=seconds)

    def _show(self) -> None:
        """Keep what the run is doing now in its status, with the lock held, when it has changed."""
        oldest = next(iter(self._open.values()), None)
        if self.asking():
            doing, what, since = "lead", "", None
        elif oldest:
            doing, what, since = "tool", oldest[1], oldest[2]
        else:
            doing, what, since = ("model" if self._busy else "idle"), "", None
        if (doing, what) != self._doing:
            self._doing = (doing, what)
            store.note(self.recorder.name, now={"doing": doing, "what": what, "since": since or time.time()})


def _tool(name: str) -> str:
    """One name for a tool, whether the hook's (``Bash``) or the harness's (``shell``)."""
    lowered = name.lower()
    return "shell" if lowered in SHELL else lowered.removeprefix("mcp__").replace("__", "_")


def _told(tool: str, arguments: Mapping[str, Any]) -> dict[str, str]:
    """What a call is about: its ``command``, the ``file`` it names, or what else it was given (``about``)."""
    if tool in SHELL:
        return {"command": str(arguments.get("command") or "")}
    told = {}
    if file := next((str(arguments[key]) for key in FILES if isinstance(arguments.get(key), str)), ""):
        told["file"] = file
    if about := next((str(arguments[key]) for key in ABOUT if isinstance(arguments.get(key), str)), ""):
        told["about"] = about
    return told


def _exit(state: Mapping[str, Any]) -> int | None:
    """The exit status OpenCode reports of a command, in its tool result's metadata."""
    found: Any = state.get("metadata")
    for _ in range(2):
        if not isinstance(found, dict):
            return None
        if isinstance(found.get("exit"), int):
            return found["exit"]
        found = found.get("metadata")
    return None


def _seconds(state: Mapping[str, Any], began: tuple[str, str, float] | None) -> float | None:
    times = state.get("time") if isinstance(state.get("time"), dict) else {}
    start, end = times.get("start"), times.get("end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return round((end - start) / 1000, 2)
    return round(time.time() - began[2], 2) if began else None


_OPEN: dict[str, Recorder] = {}
_OPENING = threading.Lock()


def of(name: str) -> Recorder:
    """The recorder of delegate ``name`` in this process: one, so that what one part of the runner hides is hidden in all of it."""
    key = str(delegate_record.folder(name))
    with _OPENING:
        if key not in _OPEN:
            _OPEN[key] = Recorder(name)
        return _OPEN[key]
