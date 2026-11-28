"""Loads src/poolhouse/worktreerules.py by file, for the hook scripts that sit beside this one."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent


NESTED = ("blocked: the tool input is nested deeper than the guard can read in bounded work; it is refused, not passed. "
          "Split the call into smaller ones.")
"""What a guard says when the event is nested too deeply for the JSON decoder (the rules refuse the less deep ones themselves)."""


SECONDS = 55.0
"""How long a guard may take to judge a call: past the 45 seconds of a release check, under the time a harness waits for a hook."""


def closed(decide: Callable[[], int], hook: str, seconds: float = SECONDS) -> int:
    """The exit status of ``decide``, and 2 (the call is blocked) for anything else that ends it: an error of any kind, or more
    than ``seconds``. A harness runs the call when its hook ends with any other status, so a guard that fails must say 2."""
    def refuse(why: str) -> None:
        text = (f"blocked: the guard could not judge this call ({why}), so it does not run. Split it into smaller, plainer "
                f"commands.\n\n(poolhouse scripts/hooks/{hook}. No switch opens this rule.)\n")
        with contextlib.suppress(OSError):  # a closed standard error changes nothing: the status still blocks the call
            os.write(2, text.encode("utf-8", "replace"))

    def stalled(*_) -> None:
        refuse(f"it took more than {seconds:g} seconds")
        os._exit(2)

    stop = _alarm(stalled, seconds)
    try:
        return decide()
    except BaseException as why:
        refuse(type(why).__name__)
        return 2
    finally:
        stop()


def _alarm(stalled: Callable[..., None], seconds: float) -> Callable[[], None]:
    """Call ``stalled`` after ``seconds`` unless the function returned here is called first."""
    if hasattr(signal, "setitimer"):  # a signal also reaches a long match, which would hold the interpreter from a thread
        signal.signal(signal.SIGALRM, stalled)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return lambda: signal.setitimer(signal.ITIMER_REAL, 0)
    timer = threading.Timer(seconds, stalled)
    timer.daemon = True
    timer.start()

    def stop() -> None:
        timer.cancel()
        timer.join()  # no thread is left to end while the interpreter does

    return stop


def event() -> object:
    """The call the harness sent on standard input, whatever the encoding of the terminal."""
    return json.loads(getattr(sys.stdin, "buffer", sys.stdin).read())


def guard_off() -> bool:
    """Whether POOLHOUSE_GUARD=off turns the soft rules off; the hooks run only for an agent's calls, so the hard
    rules stay in force."""
    return os.environ.get("POOLHOUSE_GUARD") == "off"


def load():
    """The worktreerules module of the checkout these hooks sit in, None when it lacks one."""
    where = HERE.parent.parent / "src" / "poolhouse" / "worktreerules.py"
    if not where.exists():
        return None
    spec = importlib.util.spec_from_file_location("_poolhouse_worktreerules", where)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
