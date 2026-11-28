"""Running one step of a job: each is one of Poolhouse's own commands as a process of its own, or a model removal.

`Run` is what a step is given: where its output goes, why it must stop (a cancel, the job's time limit), and the job's folder.
A step answers ``("ok" or "failed", why)``. A stop ends the processes of that step alone.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import delegate_record, paths, schedule_record as record
from poolhouse.hub import removal
from poolhouse.platform import start_process
from poolhouse.schedule import code
from poolhouse.serve.process import kill_process_tree

__all__ = ["Run", "bench", "delegate", "receipt", "remove", "unusable"]

LOOK_S = 1.0
STARTED = re.compile(r"^(?:started (\S+) \(pid|(\S+) is already running that task)", re.MULTILINE)


@dataclass
class Run:
    """What the steps of one job are run with."""

    job: dict[str, Any]
    log: Any
    stop: Callable[[], str]
    """Why the step in hand must end now, "" while it may go on."""
    child: Callable[[int], None] = lambda _pid: None
    """Told the process of the step in flight, 0 when there is none, so a job's next process can find one left running."""
    look_s: float = LOOK_S

    def say(self, line: str) -> None:
        with contextlib.suppress(OSError):  # a full disk: the line is lost and the step in flight goes on being watched
            self.log.write(f"{time.strftime('%H:%M:%S')} {line}\n".encode())
            self.log.flush()

    @property
    def folder(self) -> Path:
        return record.folder(self.job["id"])

    def command(self, argv: list[str], *, cwd: Path, env: dict[str, str]) -> tuple[int, str]:
        """Run ``argv`` with its output in the job's log: ``(exit, why it was stopped)``."""
        child = start_process(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT)
        why = ""
        try:
            self.child(child.pid)
            while child.poll() is None:
                if why := self.stop():
                    break
                time.sleep(self.look_s)
        finally:
            if child.poll() is None:
                kill_process_tree(child.pid, grace_s=60.0)
            code_ = child.wait()
            self.child(0)
        return code_, why


def _ended(code_: int, why: str) -> tuple[str, str]:
    if why:
        return "failed", why
    return ("ok", "") if code_ == 0 else ("failed", f"exit {code_}")


def bench(run: Run, step: dict[str, Any]) -> tuple[str, str]:
    """``python -m poolhouse.bench`` with the interpreter, the copy of the code and the input files fixed when it was scheduled."""
    if why := code.changed(run.job.get("pins") or {}):
        return "failed", why
    held = run.job.get("code") or {}
    copy = run.folder / "code" if held.get("copied") else None
    argv = [held["python"], "-P", "-B", "-m", "poolhouse.bench", *step["argv"]]
    run.say(f"python -m poolhouse.bench {' '.join(step['argv'])}")
    kept = receipt(run, step)
    kept.unlink(missing_ok=True)
    env = {**code.environment(copy), paths.BENCH_RECEIPT_ENV: str(kept)}
    return _ended(*run.command(argv, cwd=run.folder / "work", env=env))


def receipt(run: Run, step: dict[str, Any]) -> Path:
    """Where the bench writes a line for each run it keeps during ``step`` (`poolhouse.bench.keep.save`)."""
    return run.folder / f"kept-{step['n']}.jsonl"


def unusable(run: Run, step: dict[str, Any]) -> str:
    """Why what a measuring ``step`` kept is nothing to act on, "" when each thing it measured has a usable row. An exit of 0
    says the command ran, not that it measured: a repeat that found the machine busy is kept and left out of the report."""
    usable: dict[str, int] = {}
    try:
        for line in receipt(run, step).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            usable[str(row["label"])] = usable.get(str(row["label"]), 0) + int(row["usable"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError) as why:
        return f"left a receipt of its results that cannot be read: {why}"
    if not usable:
        return "kept no result"
    if empty := sorted(label for label, count in usable.items() if not count):
        return f"kept nothing usable for {', '.join(empty)}"
    return ""


def _own(module: str, *words: str) -> list[str]:
    """This installed Poolhouse's ``module``, run as this process runs, with no folder it starts in on its path."""
    return [sys.executable, "-P", "-B", *(["-I"] if sys.flags.isolated else []), "-m", module, *words]


def start_words(asked: dict[str, Any], model: str, label: str) -> list[str]:
    """The words of ``poolhouse-delegate`` that start the step's task on ``model``. Nothing wider than ``--edit`` is passed."""
    words = ["start", f"--task={asked['task']}", f"--model={model}", f"--name={label}"]
    words += [f"--project={asked['project']}"] if asked["project"] else []
    words += ["--edit"] if asked["edit"] else []
    words += [f"--kind={asked['kind']}"] if asked["kind"] else []
    words += [f"--max-turns={asked['max_turns']}"] if asked["max_turns"] else []
    return words + (["--no-review"] if asked["no_review"] else [])


def _one_delegate(run: Run, asked: dict[str, Any], model: str, label: str) -> tuple[str, str]:
    where = Path(asked["project"] or run.job["by"].get("cwd") or run.folder / "work")
    if not where.is_dir():
        return "failed", f"the folder {where} is gone"
    run.say(f"delegate to {model}")
    begin = run.log.tell()
    code_, why = run.command(_own("poolhouse.delegate", *start_words(asked, model, label)), cwd=where, env=dict(os.environ))
    if code_ or why:
        return _ended(code_, why)
    with Path(run.log.name).open("rb") as said:
        said.seek(begin)
        found = STARTED.search(said.read().decode("utf-8", "replace"))
    if found is None:
        return "failed", "the delegate did not say what it started"
    name = found[1] or found[2]
    until = time.monotonic() + asked["hours"] * 3600
    while delegate_record.state_of(name)[0] in delegate_record.LIVE:
        why = run.stop() or ("" if time.monotonic() < until else f"it ran past its {asked['hours']:g} hours")
        if why:
            run.command(_own("poolhouse.delegate", "stop", name), cwd=where, env=dict(os.environ))
            return "failed", f"{name}: {why}"
        time.sleep(run.look_s)
    seen = delegate_record.view(name)
    state, detail = delegate_record.state_of(name)
    spent = seen.get("spent") or {}
    said = (f"{name} on {model}: {state}, {seen.get('seconds', 0):.0f}s, {seen.get('turns', 0)} turn(s), "
            f"{spent.get('output_tokens', 0)} tokens written; {seen.get('tests_said') or 'no test run'}")
    run.say(said + (f"; {detail}" if detail else ""))
    return ("ok", said) if state == "done" else ("failed", said)


def delegate(run: Run, step: dict[str, Any]) -> tuple[str, str]:
    """The step's task to a delegate on each of its models, one after the other, so the two never compute at once; what each came
    to is in the log and in the step's result, to compare. `poolhouse-delegate record NAME` has the rest."""
    asked = step["delegate"]
    outcomes = [_one_delegate(run, asked, model, f"{asked['name']}-{run.job['id'][-4:]}-{step['n']}-{index}")
                for index, model in enumerate(asked["models"], start=1)]
    why = " | ".join(why for _state, why in outcomes)
    return ("ok" if all(state == "ok" for state, _why in outcomes) else "failed"), why


def remove(run: Run, step: dict[str, Any]) -> tuple[str, str]:
    """``poolhouse-models remove`` of the step's model."""
    try:
        done = removal.remove(step["ref"])
    except (OSError, ValueError) as why:
        run.say(f"remove-model {step['ref']}: {why}")
        return "failed", str(why)
    said = done.said()
    run.say(said)
    return "ok", said
