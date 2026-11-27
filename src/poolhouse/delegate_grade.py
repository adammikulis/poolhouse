"""The grading record of delegated work: what each run was asked and how it came out, kept in the delegate's own folder (`delegate_store.graded`)
with one list of entries, the self-check's rounds and the grades others give. A grade is another session's judgement: never the run's own session or
model, unverified without a check, and the only thing a success rate counts.

A run also keeps where it started, for a replay: the commit of its project and, under ``start`` in its folder, the files that differed from that
commit (credential names aside).
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from poolhouse import delegate_candidate, delegate_review, delegate_store as store, secretpaths

__all__ = ["VERDICTS", "commit_of", "ended", "entry", "kind_of", "lines", "opened", "record", "self_grade", "snapshot", "started", "summary"]

VERDICTS = ("correct", "partly", "wrong", "unusable")
KIND = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
START_BYTES = 32 << 20
"""The most bytes of files that differ from the starting commit a run keeps for a replay."""
PUBLICATION = ("applied", "refused", "conflicts", "withheld", "patches", "runs_later", "refused_writes")
TOKENS = ("turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "model_seconds")


def kind_of(asked: str, edit: bool) -> str:
    """The task kind a run is recorded under: the lead's word, else ``edit`` or ``read``."""
    if asked and not KIND.fullmatch(asked):
        raise ValueError("a task kind is one short word: a-z, 0-9 and -, up to 32 characters")
    return asked or ("edit" if edit else "read")


def commit_of(source: str) -> str:
    """The commit the git project at ``source`` is at; empty for none."""
    if not source:
        return ""
    try:
        return delegate_candidate.git(source, "rev-parse", "--verify", "HEAD").decode().strip()
    except (OSError, ValueError):
        return ""


def snapshot(source: str, into: Path) -> dict[str, Any]:
    """Keep what the project at ``source`` holds that its commit does not: the changed and new files copied under ``into``, the deleted names, and the
    names left out (a credential, or more than `START_BYTES`)."""
    try:
        top = Path(os.fsdecode(delegate_candidate.git(source, "rev-parse", "--show-toplevel").rstrip(b"\r\n")))
        commit = delegate_candidate.git(top, "rev-parse", "--verify", "HEAD").decode().strip()
        rows = delegate_candidate.git(top, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames").split(b"\0")
        changed, links, deleted, left, size = [], {}, [], [], 0
        for name in (os.fsdecode(row[3:]) for row in rows if len(row) > 3):
            path = top / name
            if secretpaths.name_hit(name):
                left.append(name)
            elif path.is_symlink():
                links[name] = str(path.readlink())
            elif path.is_file():
                size += path.stat().st_size
                if size > START_BYTES:
                    left.append(name)
                    continue
                (into / name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(path, into / name)
                changed.append(name)
            elif path.exists():
                left.append(name)
            else:
                deleted.append(name)
    except (OSError, ValueError) as why:
        return {"kept": False, "why": str(why)}
    return {"kept": True, "commit": commit, "root": str(top), "files": str(into) if changed else "", "changed": changed, "links": links,
            "deleted": deleted, "left_out": left}


def _asked(rec: store.Delegate) -> dict[str, Any]:
    return {"name": rec.name, "lead": rec.lead, "board": rec.board, "task": rec.task, "kind": rec.kind or ("edit" if rec.edit else "read"),
            "label": rec.label, "project": rec.source, "edit": rec.edit, "permissions": rec.permissions, "sandboxed": rec.sandboxed,
            "max_turns": rec.max_turns, "review_rounds": rec.review_rounds, "model": rec.model, "commit": rec.commit, "started": rec.started}


def opened(rec: store.Delegate) -> None:
    """The record of a run that is about to start."""
    store.keep(rec.name, run=_asked(rec))


def started(rec: store.Delegate) -> None:
    """What the run starts from, taken before any model works on it."""
    seen = rec.workdir if rec.branch else rec.source   # a worktree of its own holds the commit and nothing more
    state = snapshot(seen, store.folder(rec.name) / "start") if seen else {"kept": False, "why": "it was given no folder"}
    store.keep(rec.name, run={**_asked(rec), "start": state})


def ended(name: str, state: str, row: dict[str, Any]) -> None:
    """How the run ended: its times, tokens, publication and what the self-check came to."""
    store.keep(name, run={
        "state": state, "ended": time.time(), "seconds": row.get("seconds"), "tokens": {key: row[key] for key in TOKENS if key in row},
        "publication": {key: row[key] for key in PUBLICATION if key in row}, "review": row.get("review"), "error": row.get("error", ""),
        "tree": row.get("tree"),
        "detail": row.get("detail", "")})


def record(name: str) -> dict[str, Any]:
    """The grading record of ``name``; for a run made before runs had one, its ``run`` is read from what it left. ValueError for no such run."""
    rec = store.load(name)
    held = store.graded(name)
    if rec is None and not held["run"]:
        raise ValueError(f"no delegate {name}; `poolhouse-delegate status` lists them")
    if rec is not None:
        row = store.result(name)
        left = {} if not row else {"state": row.get("state"), "seconds": row.get("seconds"),
                                   "tokens": {key: row[key] for key in TOKENS if key in row},
                                   "publication": {key: row[key] for key in PUBLICATION if key in row}}
        held["run"] = {**_asked(rec), **left, **held["run"]}
    return held


def _family(model: str) -> str:
    """A model's name as two spellings of one model compare: the file's name without its suffix and the number of a split part."""
    return re.sub(r"-\d{5}-of-\d{5}$", "", Path(model).name.casefold().removesuffix(".gguf"))


def self_grade(run: dict[str, Any], graders: Collection[str], agents: Sequence[Any]) -> str:
    """Why none of ``graders`` (the session grading, and the one its command runs in) may grade ``run``: one of them is the run's own session or one
    it started, or runs on the run's own model. Empty when they may. ``agents`` are the sessions of the run's board."""
    name, rows = str(run.get("name")), {each.name: each for each in agents}
    served = run.get("served") or {}
    own = {_family(each) for each in (run.get("model"), served.get("file"), served.get("alias")) if each}
    # the board's row of the run's session is not asked: until a run records the model it was served, it may hold the launcher's
    for grader in graders:
        seen, each = set(), grader
        while each and each not in seen:
            if each == name or each.startswith(f"{name}."):
                return f"{grader} is the run's own session or one it started: a run never grades itself"
            seen.add(each)
            each = getattr(rows.get(each), "parent", "")
        model = getattr(rows.get(grader), "model", "")
        if model and _family(model) in own:
            return f"{grader} runs on {model}, the run's own model, which never grades its own work"
    return ""


def entry(grader: tuple[str, str], verdict: str, critique: str, checks: Sequence[str], survived: int | None) -> dict[str, Any]:
    """The grade of ``grader`` (a session and its model) as an entry of the record; each check is what was run, and after ``=>`` what it showed.
    Without a check it is unverified."""
    if verdict not in VERDICTS:
        raise ValueError(f"a grade is one of {', '.join(VERDICTS)}")
    if not critique.strip():
        raise ValueError("give the critique as text: --critique TEXT")
    if survived is not None and not 0 <= survived <= 100:
        raise ValueError("--survived is the percent of the change that survived, 0 to 100")
    made = [{"what": what.strip(), "result": result.strip()} for what, _, result in (each.partition("=>") for each in checks) if what.strip()]
    return {"kind": "grade", "grader": grader[0], "model": grader[1], "at": time.time(), "round": 0, "verdict": verdict, "critique": critique.strip(),
            "findings": [], "checks": made, "verified": bool(made), "survived": survived, "seconds": None, "tokens": None}


def summary(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs by model and task kind (warming runs aside): how they ended, what the self-check accepted, the checked grades of others (the latest of
    each run), the runs with only unchecked ones, the success rate (correct of checked grades, None with none) and the mean time."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for held in records:
        run, grades = held["run"], held["grades"]
        if run.get("kind") == "warm":
            continue
        key = (Path(str(run.get("model") or "")).name, str(run.get("kind") or ""))
        row = groups.setdefault(key, {"model": key[0], "kind": key[1], "runs": 0, "states": {}, "self_checked": 0, "self_accepted": 0,
                                      "graded": 0, **dict.fromkeys(VERDICTS, 0), "unverified": 0, "times": [], "input_tokens": 0,
                                      "output_tokens": 0})
        row["runs"] += 1
        state = str(run.get("state") or "unknown")
        row["states"][state] = row["states"].get(state, 0) + 1
        rounds = [each for each in grades if each.get("kind") == delegate_review.SELF_CHECK]
        row["self_checked"] += bool(rounds)
        row["self_accepted"] += any(each.get("verdict") == "accept" for each in rounds)
        others = [each for each in grades if each.get("kind") == "grade"]
        checked = [each for each in others if each.get("verified")]
        if checked:
            row["graded"] += 1
            row[checked[-1]["verdict"]] += 1
        row["unverified"] += bool(others) and not checked
        if run.get("seconds") is not None:
            row["times"].append(float(run["seconds"]))
        for name in ("input_tokens", "output_tokens"):
            row[name] += int((run.get("tokens") or {}).get(name) or 0)
    found = []
    for _key, row in sorted(groups.items()):
        times = row.pop("times")
        found.append({**row, "success": row["correct"] / row["graded"] if row["graded"] else None,
                      "mean_seconds": round(sum(times) / len(times), 1) if times else None})
    return found


def lines(rows: Sequence[dict[str, Any]]) -> list[str]:
    """`summary` as one line a model and kind; the self-check is named as such and is no part of the success rate."""
    said = []
    for row in rows:
        states = ", ".join(f"{state} {count}" for state, count in sorted(row["states"].items()))
        verdicts = ", ".join(f"{each} {row[each]}" for each in VERDICTS)
        success = "no checked grade" if row["success"] is None else f"success {row['success']:.0%}"
        mean = "no time" if row["mean_seconds"] is None else f"mean {row['mean_seconds']}s"
        said.append(f"{row['model']}  {row['kind']}  {row['runs']} run(s): {states}; self-check accepted {row['self_accepted']} of "
                    f"{row['self_checked']}; checked grades by others {row['graded']} ({verdicts}), {success}; unchecked only {row['unverified']}; "
                    f"{mean}; {row['input_tokens']} read, {row['output_tokens']} written")
    return said
