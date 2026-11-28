"""What a scheduled job may run, read from its file. Three kinds of Poolhouse's own commands, and no shell.

A job file has the lines of a bench queue (`poolhouse.joblines`): one command each, ``#`` comments, ``set`` and ``${NAME}``
(expanded when the job is scheduled), and ``smoke:`` whose failure skips the ``then:`` under it. A line is

    [bench] SUBCOMMAND ...                    one ``python -m poolhouse.bench`` measurement or report
    delegate --model M --model N --task T     the same task to each model's delegate, one after the other
    remove-model hf:owner/repo/file.gguf      ``poolhouse-models remove``, run only when every step before it passed

A bench line of a queue file works unchanged. One that names a program or code to run, or that would leave the job's process, is
refused (`CODE_FLAGS`).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from poolhouse import joblines
from poolhouse.home import expand

__all__ = ["BENCH", "CODE_FLAGS", "INPUTS", "REF", "bench_rows", "judge", "parse"]

BENCH = ("run", "sweep", "concurrent", "drafts", "retrieval", "speed", "quants", "show", "report", "compare")
"""The bench subcommands a job may run: measurements and the reports on them."""
CODE_FLAGS = {"binary": "--binary", "tool": "--tool", "tool_arg": "--tool-arg", "client": "--client", "ask": "--ask",
              "detach": "--detach"}
"""The parsed name of every bench flag a job refuses: it names a program or a module to run, or detaches from the job."""
MEASURES = ("run", "sweep", "concurrent", "drafts", "retrieval", "speed")
QUANTS_MEASURES = ("speed", "fidelity", "tasks")
"""The bench subcommands, and the actions of ``quants``, that measure and keep what they measured: a model is removed after
one only when what it kept can be used."""
INPUTS = ("config", "corpus", "items", "model", "serve", "draft", "questions", "profile", "profiles")
"""The parsed names whose values may be files the step reads; each such file is fixed when the job is scheduled (`code.freeze`)."""
REF = re.compile(r"hf:[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*(?:/[A-Za-z0-9][\w.-]*)*\.gguf")
MODEL = re.compile(r"[A-Za-z0-9/~][^\x00-\x1f]*")
WORDS = 6
DELEGATE_HOURS = 4.0


class _Quiet(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise joblines.LineError(message)


def _delegate_parser() -> argparse.ArgumentParser:
    ap = _Quiet(prog="delegate", add_help=False, allow_abbrev=False)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--task", default="")
    ap.add_argument("--task-file", default="")
    ap.add_argument("--project", default="")
    ap.add_argument("--edit", action="store_true")
    ap.add_argument("--kind", default="")
    ap.add_argument("--name", default="scheduled")
    ap.add_argument("--max-turns", type=int, default=0)
    ap.add_argument("--no-review", action="store_true")
    ap.add_argument("--hours", type=float, default=DELEGATE_HOURS)
    return ap


def _delegate(argv: list[str], base: Path) -> dict[str, Any]:
    with contextlib.redirect_stderr(io.StringIO()):
        args = _delegate_parser().parse_args(argv)
    if bool(args.task) == bool(args.task_file):
        raise joblines.LineError("a delegate step has one task: --task TEXT or --task-file FILE")
    task = args.task
    if args.task_file:
        found = expand(args.task_file)
        try:
            task = (found if found.is_absolute() else base / found).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as why:
            raise joblines.LineError(f"the task file {args.task_file} cannot be read: {why}") from why
    if not task.strip():
        raise joblines.LineError("the delegate's task is empty")
    if bad := [model for model in args.model if not MODEL.fullmatch(model)]:
        raise joblines.LineError(f"{bad[0]!r} is not a model's name or file")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", args.name) or (args.kind and not re.fullmatch(r"[a-z-]{1,30}", args.kind)):
        raise joblines.LineError("--name and --kind are short words of small letters, digits and hyphens")
    if not 0 < args.hours <= 48:
        raise joblines.LineError("--hours is more than 0 and at most 48")
    project = ""
    if args.project:
        found = expand(args.project)
        found = found if found.is_absolute() else base / found
        if not found.is_dir():
            raise joblines.LineError(f"--project {args.project} is not a folder")
        project = str(found.resolve())
    return {"models": args.model, "task": task, "project": project, "edit": args.edit, "kind": args.kind, "name": args.name,
            "max_turns": args.max_turns, "no_review": args.no_review, "hours": args.hours}


def parse(text: str, *, base: Path) -> list[dict[str, Any]]:
    """The steps of a job file, in order; `joblines.LineError` naming the line of the first that may not be run. Bench lines are
    judged afterwards, by the parser of the code that runs them (`judge`). Relative paths are read from ``base``."""
    out: list[dict[str, Any]] = []
    for line in joblines.read_lines(text):
        kind, argv = "bench", line.argv
        if argv[0] in ("delegate", "remove-model"):
            kind, argv = argv[0], argv[1:]
        elif argv[0] == "bench":
            argv = argv[1:]
        said = " ".join(line.argv[:WORDS])
        step: dict[str, Any] = {"n": len(out) + 1, "line": line.number, "kind": kind, "role": line.kind,
                                "words": said if len(said) <= 72 else said[:69] + "..."}
        try:
            if kind == "bench":
                if not argv or argv[0] not in BENCH:
                    raise joblines.LineError(f"a job runs {', '.join(BENCH)} of the bench, `delegate` and `remove-model`, "
                                             f"and nothing else: not {' '.join(line.argv[:2])!r}")
                step["argv"] = argv
            elif kind == "delegate":
                step["delegate"] = _delegate(argv, base)
            else:
                if len(argv) != 1 or not REF.fullmatch(argv[0]) or ".." in argv[0]:
                    raise joblines.LineError("remove-model takes one hf:owner/repo/file.gguf")
                step["ref"] = argv[0]
        except joblines.LineError as why:
            text_ = str(why)
            raise joblines.LineError(text_ if text_.startswith("line ") else f"line {line.number}: {text_}") from None
        out.append(step)
    if not out:
        raise joblines.LineError("the job file names nothing to run")
    return out


def judge(step: dict[str, Any], read: dict[str, Any]) -> list[str]:
    """Refuse a bench step the parser or the rules of a job do not take, and say in ``step`` whether it measures (`MEASURES`);
    the values among its words that may name input files."""
    where = f"line {step['line']}"
    if error := read.get("error"):
        raise joblines.LineError(f"{where}: python -m poolhouse.bench {' '.join(step['argv'])}\n  {error}")
    values = read["values"]
    if used := [flag for name, flag in CODE_FLAGS.items() if values.get(name)]:
        raise joblines.LineError(f"{where}: a scheduled bench line takes no {', '.join(used)}: it names something to run, or "
                                 f"leaves the job, and a job runs Poolhouse's own commands alone")
    word = step["argv"][0]
    step["measures"] = word in MEASURES or (word == "quants" and values.get("action") in QUANTS_MEASURES)
    out: list[str] = []
    for name in INPUTS:
        value = values.get(name)
        for one in value if isinstance(value, list) else [value]:
            if isinstance(one, str) and one:
                out.append(one)
                if name == "model" and "=" in one:
                    out.append(one.partition("=")[2])
    return out


def bench_rows(steps: Sequence[dict[str, Any]], check: Callable[[list[list[str]]], list[dict[str, Any]]]) -> list[tuple[dict, list[str]]]:
    """Each bench step of ``steps`` with its input values, every one judged; ``check`` reads the lines (`code.check_bench`)."""
    found = [step for step in steps if step["kind"] == "bench"]
    if not found:
        return []
    return [(step, judge(step, read)) for step, read in zip(found, check([step["argv"] for step in found]), strict=True)]
