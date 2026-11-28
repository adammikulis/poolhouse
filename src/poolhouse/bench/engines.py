"""``python -m poolhouse.bench engines``: serving engines and their settings measured on one model, interleaved.

A campaign file names the model files and the arms (engine, build, files, drafting). ``plan`` says which files are here;
``tune`` finds each arm's drafter and draft length; ``run`` leases every arm in turn through the broker, round after
round with the order turned, holds one session (`engine_workload.session`) on it, and keeps one JSON line per request
as it is answered; ``report`` reads those lines. A request during which another process used the GPU is kept, marked
contended and left out of every figure.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

from poolhouse import bench, home, lock
from poolhouse.bench import (
    codeitems,
    engine_workload as workload,
    gpuwatch,
    plan_files,
    quiet,
    serve as bench_serve,
    system,
)
from poolhouse.bench.holding import Watching, machine_memory, process_tree, serving_pids
from poolhouse.bench.keep import save
from poolhouse.bench.underway import ended, remember
from poolhouse.client import Client
from poolhouse.client.settings import Request, Transport
from poolhouse.http import ServerError
from poolhouse.log import say, warn
from poolhouse.serve import binary as binaries
from poolhouse.serve.serving import Config, Serving

__all__ = ["Arm", "add_arguments", "arms_of", "best_draft", "figures", "run"]

KIND = "engines"
ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / "docs" / "experiments" / "engines-qwen3.8-flash.json"
#: Seconds one request may take: a cold read of the largest level at the slowest rate seen (125 tok/s) is under this.
REQUEST_S = 3600.0
TUNE_WRITES = workload.WRITES[:3]


@dataclass(frozen=True)
class Arm:
    """One configuration compared: the engine, its build, the model and draft head files, and the drafting settings."""

    name: str
    engine: str
    model: str
    head: str = ""
    build: str = "current"
    spec_type: str = "draft-mtp"
    draft_n_max: int = 4
    draft_p_min: float | None = None
    extra_args: tuple[str, ...] = ()
    kv: str = "q8_0"
    tune: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Loaded:
    """One drafter load being tuned: where it answers, its processes, its arm, its setting and the depths asked."""

    url: str
    pids: Sequence[int]
    arm: str
    setting: Mapping[str, Any]
    depths: Sequence[int]


class Deadline(RuntimeError):
    """The campaign's hard end has passed; the session stops where it is."""


def arms_of(config: Mapping[str, Any], device: str, only: str = "") -> list[Arm]:
    """The arms of ``device`` in the campaign, narrowed to ``only`` (names, comma separated, order kept)."""
    held = {}
    for row in config["devices"][device]["arms"]:
        held[row["name"]] = Arm(**{**row, "extra_args": tuple(row.get("extra_args", ()))})
    if not only:
        return list(held.values())
    wanted = [name.strip() for name in only.split(",") if name.strip()]
    absent = [name for name in wanted if name not in held]
    if absent:
        raise ValueError(f"no arm {', '.join(absent)} for {device}; there are {', '.join(held)}")
    return [held[name] for name in wanted]


def binary_of(build: str) -> str:
    """The llama-server an arm names: "current", a named build, or a path."""
    if build == "current":
        return str(binaries.require_binary())
    named = dict(binaries.named_builds())
    if build in named:
        return str(named[build])
    if Path(build).is_file():
        return build
    raise ValueError(f"no llama.cpp build {build!r}; named builds here: {', '.join(named) or 'none'}")


def llama_config(arm: Arm, files: Mapping[str, Path], context: int, port: int) -> Config:
    """The lease of one llama.cpp arm: one slot, the whole context, its drafting."""
    config = Config(serving=Serving(model=str(files[arm.model])))
    drafting = ({"draft": str(files[arm.head]), "spec_type": arm.spec_type, "draft_n_max": arm.draft_n_max,
                 "draft_p_min": arm.draft_p_min} if arm.head else {"draft": "", "spec_type": "", "mtp": False})
    return config.over(slots=1, slot_context=context, port=port, cache_type=arm.kv, kv_unified=True,
                       extra_args=arm.extra_args, **drafting)


@contextlib.contextmanager
def llama_up(args: Any, arm: Arm, files: Mapping[str, Path]) -> Iterator[tuple[str, dict[str, Any]]]:
    """``(url, record)`` for ``arm`` leased from the broker and released on the way out."""
    binary = binary_of(arm.build)
    config = llama_config(arm, files, args.context, args.serve_port)
    with bench_serve.up(config, binary=binary, serve_timeout=1800.0) as (server, held):
        held.pop("baseline", None)
        loaded = held.pop("loaded", None)
        version = binaries.manifest_of(binary)
        yield server.base_url, {**held, "loaded_s": loaded, "llama_cpp": {
            key: version.get(key) for key in ("commit", "version", "ref", "repo", "source", "built_at")}}


#: How each engine named in a campaign is leased.
ENGINES: dict[str, Callable[..., Any]] = {"llama.cpp": llama_up}


def flags_of(pids: Sequence[int]) -> list[str]:
    """The served process's command line with the value after any key or token flag left out."""
    for process in process_tree(pids):
        words: list[str] = []
        with contextlib.suppress(psutil.Error, OSError):      # a process that will not say adds no flags
            words = [str(w) for w in process.cmdline()]
        if "--port" in words:
            out, hide = [], False
            for word in words:
                out.append("<left out>" if hide else word)
                hide = word.startswith("--") and ("key" in word or word.endswith("token"))
            return out
    return []


def asker(url: str) -> Callable[..., Any]:
    """``ask(messages, turn, on_piece, tools=None)``: one streamed greedy request on ``url``."""
    def ask(messages: list[dict[str, Any]], turn: workload.Turn, on_piece: Callable[..., None],
            tools: list[dict[str, Any]] | None = None, depth: int | None = None) -> Any:
        client = Client(url, request=Request(temperature=0.0, n_predict=turn.limit, spec_draft_max=depth),
                        transport=Transport(timeout=REQUEST_S))
        return client.chat(messages, tools=tools or (workload.TOOLS if turn.tools else None), think=turn.think,
                           on_delta=on_piece, timeout=REQUEST_S)
    return ask


def campaign_dir(config: Mapping[str, Any]) -> Path:
    path = bench.home_dir() / "engines" / str(config["campaign"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def frozen(config: Mapping[str, Any], levels: Sequence[int]) -> str:
    """The text sessions up to levels are cut from, written once per campaign so an edit of the checkout during a
    campaign changes nothing it sends."""
    chars = int((max(levels) + 1) * float(config["chars_per_token"]))
    path = campaign_dir(config) / f"corpus-{chars}.txt"
    if not path.is_file():
        longest = int((max(config["levels"]) + 1) * float(config["chars_per_token"]))
        whole = campaign_dir(config) / f"corpus-{longest}.txt"
        text = whole.read_text(encoding="utf-8") if whole.is_file() else workload.corpus(ROOT, longest)
        whole.write_text(text, encoding="utf-8")
        path.write_text(text[:chars], encoding="utf-8")
    return path.read_text(encoding="utf-8")


def appended(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, default=str) + "\n")


def lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(one) for one in path.read_text(encoding="utf-8").splitlines() if one.strip()]


def moment(clock: str, now: datetime | None = None) -> float | None:
    """The next time the clock reads ``HH:MM``, as seconds since the epoch; None for ""."""
    if not clock:
        return None
    now = now or datetime.now()
    hour, minute = (int(part) for part in clock.split(":"))
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (at if at > now else at + timedelta(days=1)).timestamp()


def settled(minutes: float) -> list[str]:
    """Waits up to ``minutes`` for a quiet machine; what was still busy afterwards."""
    until = time.monotonic() + minutes * 60
    while True:
        found = quiet.look()
        if found.ok:
            return []
        if time.monotonic() >= until:
            return list(found.reasons)
        warn(f"waiting for a quiet machine: {found.reasons[0]}")
        time.sleep(30)


@contextlib.contextmanager
def held_arm(args: Any, arm: Arm, files: Mapping[str, Path]) -> Iterator[tuple[str, list[int], dict[str, Any]]]:
    """``(url, pids, record)`` for an arm that is up, with the memory it held written into the record on the way out."""
    before = machine_memory()
    with ENGINES[arm.engine](args, arm, files) as (url, record):
        watched = Watching(url, baseline=before)
        pids = [int(getattr(p, "pid", 0)) for p in process_tree(serving_pids(url))]
        record.update({"engine": arm.engine, "arm": arm.name, "flags": flags_of(pids),
                       "files": {key: plan_files.identity(files[key]) for key in (arm.model, arm.head) if key}})
        try:
            yield url, pids, record
        finally:
            record["memory"] = watched.stop()


def drafted_arm(arm: Arm, chosen: Mapping[str, Any]) -> Arm:
    """``arm`` with the drafting ``tune`` chose for it, when it chose any."""
    got = chosen.get(arm.name)
    if not got:
        return arm
    return replace(arm, **{k: got[k] for k in ("head", "spec_type", "draft_n_max", "draft_p_min") if k in got})


def checks(args: Any, url: str, keep: Callable[[dict[str, Any]], None]) -> None:
    """The tool-calling and coding items, once each."""
    ask = asker(url)
    for row in workload.tool_checks(ask, json.loads(Path(args.tools).read_text(encoding="utf-8"))):
        keep(row)
    for item in codeitems.load([args.code]):
        reply = ask([{"role": "user", "content": item.ask}], workload.Turn("code-check", 0, 1024), lambda *_: None)
        keep({"kind": "code-check", "item": item.id, "correct": codeitems.grade(item, reply.content or ""),
              "text": reply.content or ""})


def measure(args: Any, config: Mapping[str, Any], arm: Arm, turn: int) -> None:
    """One session of ``arm`` in round ``turn``: leased, asked, released, kept."""
    files = plan_files.required(config, [k for k in (arm.model, arm.head) if k])
    hard = args.hard
    where = campaign_dir(config)
    levels = tuple(int(n) for n in args.levels.split(",")) if args.levels else tuple(config["levels"])
    plan = workload.Plan(levels=levels, agent_levels=tuple(n for n in config["agent_levels"] if n in levels),
                         generate=int(config["generate"]), chars_per_token=float(config["chars_per_token"]))
    session = f"{arm.name}-r{turn}-{int(time.time())}"
    rows: list[dict[str, Any]] = []
    text = frozen(config, levels)
    busy = settled(args.wait)
    system.note_start()
    with held_arm(args, arm, files) as (url, pids, record):
        def keep(row: dict[str, Any]) -> None:
            row = {"arm": arm.name, "round": turn, "session": session, "at": time.time(), **row}
            rows.append(row)
            appended(where / "turns.jsonl", row)
            if row.get("prompt_ms") and row.get("predicted_ms"):
                say(f"  {row['kind']} @{row.get('level')}: read {row.get('prompt_n')} tok "
                    f"{1000 * row['prompt_n'] / row['prompt_ms']:.0f}/s, wrote {row.get('predicted_n')} "
                    f"{1000 * row['predicted_n'] / row['predicted_ms']:.1f}/s"
                    + (" CONTENDED" if row.get("contended") else "")
                    + ("" if "correct" not in row else f" correct={row['correct']}"))
            if hard and time.time() > hard:
                raise Deadline(f"past the hard end during {session}")

        say(f"\n{session}: {' '.join(record['flags'])[:600]}")
        record.update({"busy_before": busy, "levels": list(levels), "round": turn, "session": session,
                       "checkout": plan_files.commit(ROOT), "corpus": plan_files.digest_of(text)})
        try:
            workload.session(asker(url), plan, workload.Material(
                ROOT, keep, lambda: gpuwatch.watch(pids, platform=sys.platform), f"r{turn}", text))
            if turn == args.first_round and not args.no_checks:
                checks(args, url, keep)
            record["completed"] = True
        except (ServerError, Deadline) as exc:
            record["completed"], record["stopped"] = False, f"{type(exc).__name__}: {exc}"[:400]
            warn(f"{session} stopped: {record['stopped']}")
    record["system"] = system.record()
    appended(where / "sessions.jsonl", record)
    if rows:
        save(args.kept, rows, kind=KIND, label=f"{config['campaign']}:{arm.name}", server=record)


@contextlib.contextmanager
def measuring(words: Sequence[str]) -> Iterator[None]:
    """The bench's measuring lock, queued for, with the campaign named where ``status`` reads it. SIGTERM ends the
    run as an exception, so a leased server is released on the way out."""
    def stopped(signum: int, frame: Any) -> None:
        raise SystemExit(128 + signum)

    with lock.only_one(bench.home_dir() / "measuring.lock", wait=True, announce=warn):
        previous = signal.signal(signal.SIGTERM, stopped)
        remember(list(words), pid=os.getpid())
        try:
            yield
        finally:
            ended()
            signal.signal(signal.SIGTERM, previous)


def campaign(args: Any, config: Mapping[str, Any], arms: Sequence[Arm]) -> int:
    """``run``: every arm once per round, the order turned each round."""
    plan_files.required(config, [k for arm in arms for k in (arm.model, arm.head) if k])
    chosen_path = campaign_dir(config) / f"chosen-{args.device}.json"
    chosen = json.loads(chosen_path.read_text(encoding="utf-8")) if chosen_path.is_file() else {}
    stop, args.hard = moment(args.until), moment(args.hard_until)
    with measuring(["engines", "run", "--device", args.device]):
        for turn in range(args.first_round, args.first_round + args.repeats):
            shift = turn % len(arms)
            for arm in [*arms[shift:], *arms[:shift]]:
                arm = drafted_arm(arm, chosen)
                if stop and time.time() >= stop:
                    say(f"no new session after {args.until}; stopping")
                    return 0
                try:
                    measure(args, config, arm, turn)
                except (RuntimeError, OSError, ValueError) as exc:      # an arm that cannot load; the others go on
                    warn(f"{arm.name} round {turn} failed: {type(exc).__name__}: {exc}")
                    appended(campaign_dir(config) / "sessions.jsonl", {
                        "arm": arm.name, "round": turn, "completed": False,
                        "stopped": f"{type(exc).__name__}: {exc}"[:1500]})
    return report(args, config)


def best_draft(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """For each arm, the drafting whose clean requests wrote most tokens a second over all of them. Settings missing
    a request another setting of the arm answered are left out, so every one is compared on the same work."""
    held: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("contended") and row.get("predicted_ms"):
            held[(row["arm"], json.dumps(row["setting"], sort_keys=True))].append(row)
    asked: dict[str, set] = defaultdict(set)
    for (arm, _), got in held.items():
        asked[arm] |= {row["prompt"] for row in got}
    out: dict[str, dict[str, Any]] = {}
    for (arm, setting), got in held.items():
        by_prompt: dict[int, list[float]] = defaultdict(list)
        for row in got:
            by_prompt[row["prompt"]].append(row["predicted_ms"] / row["predicted_n"])
        if set(by_prompt) != asked[arm]:
            continue
        rate = 1000.0 / statistics.mean(statistics.median(v) for v in by_prompt.values())
        drafted = sum(row.get("draft_n") or 0 for row in got)
        taken = sum(row.get("draft_n_accepted") or 0 for row in got)
        if arm not in out or rate > out[arm]["decode_tps"]:
            out[arm] = {**json.loads(setting), "decode_tps": round(rate, 2),
                        "acceptance": round(taken / drafted, 3) if drafted else None}
    return out


def tune(args: Any, config: Mapping[str, Any], arms: Sequence[Arm]) -> int:
    """Each arm's candidate drafters loaded once each, every depth asked per request, the same three writes over one
    8k conversation; the fastest is written to the campaign's chosen file."""
    where = campaign_dir(config)
    text = frozen(config, (8192,))
    hard = moment(args.hard_until)
    with measuring(["engines", "tune", "--device", args.device]):
        for arm in arms:
            grid = arm.tune or {}
            for load in grid.get("loads", []):
                depths = [int(d) for d in load.get("depths", grid.get("depths", [arm.draft_n_max]))]
                trial = replace(arm, draft_n_max=max(depths), **{k: v for k, v in load.items() if k != "depths"})
                setting = {"head": trial.head, "spec_type": trial.spec_type, "draft_p_min": trial.draft_p_min,
                           "build": trial.build, "extra_args": list(trial.extra_args)}
                if hard and time.time() >= hard:
                    return 0
                try:
                    files = plan_files.required(config, [k for k in (trial.model, trial.head) if k])
                    settled(args.wait)
                    with held_arm(args, trial, files) as (url, pids, record):
                        say(f"\ntune {arm.name}: {setting}")
                        _tune_load(args, where, Loaded(url, pids, arm.name, setting, depths), text)
                    appended(where / "sessions.jsonl", {**record, "tune": setting, "completed": True})
                except (RuntimeError, OSError, ValueError) as exc:      # a drafter a build will not load is a result
                    warn(f"{arm.name} {setting} failed: {type(exc).__name__}: {exc}")
                    appended(where / "sessions.jsonl", {"arm": arm.name, "tune": setting, "completed": False,
                                                        "stopped": f"{type(exc).__name__}: {exc}"[:1500]})
            best = best_draft([r for r in lines(where / "tune.jsonl") if r["arm"] == arm.name])
            if arm.name in best:
                chosen_path = where / f"chosen-{args.device}.json"
                chosen = json.loads(chosen_path.read_text(encoding="utf-8")) if chosen_path.is_file() else {}
                got = best[arm.name]
                chosen[arm.name] = got
                chosen_path.write_text(json.dumps(chosen, indent=1), encoding="utf-8")
                say(f"chosen for {arm.name}: {got}")
    return 0


def _tune_load(args: Any, where: Path, loaded: Loaded, text: str) -> None:
    url, pids, arm, setting, depths = (loaded.url, loaded.pids, loaded.arm, loaded.setting, loaded.depths)
    ask = asker(url)
    base = [{"role": "system", "content": workload.SYSTEM}, {"role": "user", "content": f"{text}\n\nReply OK."},
            {"role": "assistant", "content": "OK"}]
    ask(base[:2], workload.Turn("warm", 8192, 4), lambda *_: None, depth=depths[0])
    for repeat in range(args.repeats):
        order = list(depths) if repeat % 2 == 0 else list(reversed(depths))
        for depth in order:
            for number, prompt in enumerate(TUNE_WRITES):
                span = gpuwatch.watch(pids, platform=sys.platform)
                reply = ask([*base, {"role": "user", "content": prompt}],
                            workload.Turn("tune", 8192, args.tune_tokens), lambda *_: None, depth=depth)
                watched = span.close()
                timings = (reply.raw or {}).get("timings") or {}
                row = {"arm": arm, "setting": {**setting, "draft_n_max": depth}, "repeat": repeat, "prompt": number,
                       **{k: timings.get(k) for k in ("prompt_n", "cache_n", "prompt_ms", "predicted_n",
                                                      "predicted_ms", "draft_n", "draft_n_accepted")}, **watched}
                appended(where / "tune.jsonl", row)
                if row.get("predicted_ms"):
                    accepted = row["draft_n_accepted"] / row["draft_n"] if row.get("draft_n") else 0.0
                    say(f"  depth {depth} prompt {number}: {1000 * row['predicted_n'] / row['predicted_ms']:.1f} "
                        f"tok/s, accepted {accepted:.0%}" + (" CONTENDED" if row.get("contended") else ""))


def spread(values: Sequence[float], digits: int = 1) -> str:
    """``median [low-high] n=N``, or ``-`` for no values."""
    if not values:
        return "-"
    return (f"{statistics.median(values):.{digits}f} [{min(values):.{digits}f}-{max(values):.{digits}f}] "
            f"n={len(values)}")


def figures(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, int], dict[str, list[float]]]:
    """``(arm, kind, level)`` to each figure's values over clean requests. ``cold_s`` and ``cold_tps`` are a session's
    reading up to a level summed over its read turns, given only when none of them was contended or missing."""
    out: dict[tuple[str, str, int], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    reads: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["kind"] == "read":
            reads[(row["arm"], row["session"])].append(row)
        if row.get("foreign_share") is not None:
            out[(row["arm"], row["kind"], row.get("level") or 0)]["foreign_share"].append(100 * row["foreign_share"])
        if row.get("contended"):
            out[(row["arm"], row["kind"], row.get("level") or 0)]["contended"].append(1.0)
            continue
        held = out[(row["arm"], row["kind"], row.get("level") or 0)]
        if row.get("prompt_ms") and row.get("prompt_n") is not None:
            held["reread"].append(float(row["prompt_n"]))
            if row["prompt_n"] >= 256:
                held["read_tps"].append(1000 * row["prompt_n"] / row["prompt_ms"])
        if row.get("predicted_ms") and (row.get("predicted_n") or 0) >= 32:
            held["write_tps"].append(1000 * row["predicted_n"] / row["predicted_ms"])
        if row.get("draft_n"):
            held["accepted"].append(100 * row["draft_n_accepted"] / row["draft_n"])
        if row.get("ttft_s") is not None:
            held["ttft_s"].append(float(row["ttft_s"]))
        if "correct" in row:
            held["correct"].append(float(bool(row["correct"])))
    for (arm, _), got in reads.items():
        seconds = tokens = 0.0
        for row in sorted(got, key=lambda r: r["level"]):
            if row.get("contended") or not row.get("prompt_ms"):
                break
            seconds += row["prompt_ms"] / 1000
            tokens += row["prompt_n"]
            out[(arm, "read", row["level"])]["cold_s"].append(seconds)
            out[(arm, "read", row["level"])]["cold_tps"].append(tokens / seconds)
    return out


def report(args: Any, config: Mapping[str, Any]) -> int:
    where = campaign_dir(config)
    rows = lines(where / "turns.jsonl")
    if not rows:
        say(f"nothing kept yet in {where}")
        return 0
    got = figures(rows)
    for (arm, kind, level), held in sorted(got.items()):
        say(f"{arm} {kind} @{level}")
        for name in ("read_tps", "cold_tps", "cold_s", "write_tps", "accepted", "reread", "ttft_s", "foreign_share"):
            if held.get(name):
                say(f"    {name}: {spread(held[name], 2 if name == 'ttft_s' else 1)}")
        if held.get("correct"):
            say(f"    correct: {int(sum(held['correct']))}/{len(held['correct'])}")
        if held.get("contended"):
            say(f"    left out as contended: {len(held['contended'])}")
    say(f"lines: {where}")
    return 0


def add_arguments(sub: Any) -> argparse.ArgumentParser:
    """The ``engines`` subcommand."""
    one = sub.add_parser("engines", allow_abbrev=False,
                         help="serving engines and settings on one model, interleaved: context ladder, agent turns, "
                              "recall, tool calls and code; takes the measuring lock itself")
    one.add_argument("action", choices=("plan", "tune", "run", "report"))
    one.add_argument("--config", default=str(CAMPAIGN), help="the campaign (default: %(default)s)")
    one.add_argument("--device", default="mac", help="which device's arms (default: %(default)s)")
    one.add_argument("--only", default="", metavar="ARM,ARM", help="these arms, in this order")
    one.add_argument("--repeats", type=int, default=3, help="rounds of every arm (default: %(default)s)")
    one.add_argument("--first-round", type=int, default=0, metavar="N", help="the first round's number")
    one.add_argument("--levels", default="", metavar="N,N", help="context levels in tokens (default: the campaign's)")
    one.add_argument("--context", type=int, default=262144, help="served context (default: %(default)s)")
    one.add_argument("--serve-port", type=int, default=8099)
    one.add_argument("--wait", type=float, default=5.0, metavar="MIN", help="minutes to wait for a quiet machine")
    one.add_argument("--until", default="", metavar="HH:MM", help="start no new session after this")
    one.add_argument("--hard-until", default="", metavar="HH:MM", help="end a session at this")
    one.add_argument("--tune-tokens", type=int, default=192, metavar="N", help="tokens a tuning write writes")
    one.add_argument("--no-checks", action="store_true", help="leave out the tool-calling and coding items")
    one.add_argument("--tools", default=str(CAMPAIGN.with_name("engines-tools.json")))
    one.add_argument("--code", default=str(CAMPAIGN.with_name("engines-code.json")))
    one.add_argument("--kept", default=str(bench.home_dir() / "runs.ladybug"))
    return one


def run(args: Any) -> int:
    """The ``engines`` subcommand after the parse."""
    try:
        config = json.loads(home.expand(args.config).read_text(encoding="utf-8"))
        arms = arms_of(config, args.device, args.only)
        if args.action == "plan":
            return plan_files.show(config, arms, shutil.disk_usage(home.user_home()).free)
        if args.action == "report":
            return report(args, config)
        unknown = sorted({arm.engine for arm in arms} - set(ENGINES))
        if unknown:
            raise ValueError(f"this checkout leases {', '.join(ENGINES)}, not {', '.join(unknown)}; name other arms "
                             "with --only")
        return tune(args, config, arms) if args.action == "tune" else campaign(args, config, arms)
    except ValueError as why:
        warn(f"error: {why}")
        return 2
