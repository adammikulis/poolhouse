"""A delegate's record as lines a terminal or a lead reads (``poolhouse-delegate follow``). What a box said is printed inside a fence, as data: a
line of the launcher's begins with the time, and each line of a message or of output begins with ``|``."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from poolhouse import delegate_record
from poolhouse.log import say
from poolhouse.workspace import nudge_fence

__all__ = ["follow", "lines"]

LOOK_S = 1.0
STILL_S = 60.0
"""Seconds with nothing new after which a followed run says again what it is doing."""
SHOWN = 20
"""The last lines of a command's output a line of the timeline is followed by."""


def _line(text: object) -> str:
    return " ".join(str(text or "").split())


def _quoted(text: object, most: int = SHOWN) -> list[str]:
    held = str(text or "").rstrip().split("\n")
    if held == [""]:
        return []
    cut = [f"({len(held) - most} earlier lines)"] if len(held) > most else []
    return [f"          | {each}" for each in (*cut, *held[-most:])]


def _about(event: Mapping[str, Any]) -> str:
    return _line(event.get("command") or event.get("file") or event.get("about"))


def _said(event: Mapping[str, Any]) -> tuple[str, list[str]]:
    """What one event says: its line, and the lines of data under it."""
    kind = event.get("kind")
    named = f"{event.get('phase')} pass{' ' + str(event['round']) if event.get('round') else ''}, attempt {event.get('attempt')}"
    if kind == "run":
        return f"run started for {event.get('lead')} on {event.get('model')}, {event.get('tier')}", _quoted(event.get("task"))
    if kind == "state":
        return f"state {event.get('state')}" + (f": {_line(event.get('detail'))}" if event.get("detail") else ""), []
    if kind == "model":
        return f"model leased: {event.get('file')} as {event.get('alias')}, window {event.get('window')}", []
    if kind == "preflight":
        return ("preflight passed" if event.get("ok") else "preflight FAILED"), _quoted(event.get("lacks") or event.get("untested"))
    if kind == "attempt":
        return (f"restart: {named}" if event.get("attempt") else f"started: {named}"), []
    if kind == "step":
        return f"step {event.get('turn')}", []
    if kind == "text":
        return "the model says", _quoted(event.get("text"), 80)
    if kind == "call":
        return f"{'DENIED ' if event.get('denied') else ''}call {event.get('tool')}: {_about(event)}", []
    if kind == "result":
        came = event.get("status")
        came = f"exit {event['exit']}" if event.get("exit") is not None else came
        took = f" after {delegate_record.span(event['seconds'])}" if isinstance(event.get("seconds"), (int, float)) else ""
        shown = event.get("tool") == "shell" or event.get("status") == "error"
        return f"done {event.get('tool')}: {_about(event)} -> {came}{took}", _quoted(event.get("output")) if shown else []
    if kind == "test":
        verdict = "passed" if event.get("ok") else "FAILED"
        return f"tests {verdict}: {delegate_record.counts(event)}", _quoted(event.get("setup"))
    if kind == "usage":
        reuse = f", {event['reuse_percent']}% of the prompt reused" if event.get("reuse_percent") is not None else ""
        speed = f", {event['tokens_per_second']} tokens/s" if event.get("tokens_per_second") is not None else ""
        return (f"step {event.get('turn')} ended ({event.get('reason')}): {event.get('input_tokens')} read, {event.get('cache_read_tokens')} from "
                f"cache, {event.get('output_tokens')} written in {event.get('model_seconds')}s on the model{speed}{reuse}"), []
    if kind == "error":
        return f"error: {_line(event.get('why'))}", []
    if kind == "attempt_end":
        took = delegate_record.span(float(event.get("seconds") or 0))
        why = event.get("interrupted") or event.get("why")
        came = f"interrupted: {_line(why)}" if event.get("interrupted") else f"error: {_line(why)}" if why else "finished"
        return f"ended: {named}, after {took}, {event.get('turns')} turn(s): {came}", []
    if kind == "notice":
        sent = "told the lead" if event.get("delivered") else "could not tell the lead"
        return f"{sent} ({event.get('about')})", _quoted(event.get("why"))
    if kind == "end":
        return f"run ended {event.get('state')} after {delegate_record.span(float(event.get('seconds') or 0))}", _quoted(event.get("detail"))
    return str(kind), []


def lines(event: Mapping[str, Any]) -> list[str]:
    """One event as its line, with the time, and the lines of data under it."""
    try:
        head, under = _said(event)
        at = time.strftime("%H:%M:%S", time.localtime(float(event.get("at") or 0)))
    except (TypeError, ValueError, KeyError, OverflowError, OSError):
        return ["an event of the record could not be shown"]
    return [f"{at}  {head}", *under]


def follow(name: str, *, once: bool = False, out: Callable[[str], None] = say) -> int:
    """Print the record of ``name`` from its start and, while it runs, each event as it is added (``once``: only what there is now)."""
    after, quiet = 0, time.monotonic()
    while True:
        live = delegate_record.state_of(name)[0] in delegate_record.LIVE   # before the read, so an end written meanwhile is read
        events, after, more = delegate_record.read(name, after)
        if events:
            quiet = time.monotonic()
            out(nudge_fence.fenced([nudge_fence.clean("\n".join(each for event in events for each in lines(event)))]))
        if more:
            continue
        if once or not live:
            break
        if time.monotonic() - quiet >= STILL_S:
            quiet = time.monotonic()
            out(nudge_fence.fenced([nudge_fence.clean(f"still: {delegate_record.view(name).get('doing')}")]))
        time.sleep(LOOK_S)
    seen = delegate_record.view(name)
    tail = "; ".join(each for each in (seen.get("doing"), seen.get("tests_said")) if each)
    out(nudge_fence.fenced([nudge_fence.clean(f"{name} {seen.get('state')}: {tail}")]))
    return 0
