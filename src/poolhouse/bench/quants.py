"""``python -m poolhouse.bench quants``: named quantisations of one model compared on this machine.

``plan`` and ``report`` load nothing. ``build-tool`` compiles llama.cpp's scoring tool. ``fidelity`` writes each
file's log probabilities over a fixed text on Metal and on the CPU and compares them with a reference file's and with
each other; ``tasks`` grades the same coding items on each file; ``speed`` repeats the speed grid, file after file.
Every figure is printed with how many blocks, items or runs it rests on and its 95% interval, and two builds whose
difference has an interval holding zero are said not to be told apart. Runs go into the bench's store.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import shutil
import statistics
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from poolhouse import bench, gate, home, lock
from poolhouse.bench import (
    codeitems,
    logits,
    ops,
    quiet,
    serve as bench_serve,
    speed as bench_speed,
)
from poolhouse.bench.intervals import difference, t_interval
from poolhouse.bench.keep import save
from poolhouse.bench.underway import ended, remember
from poolhouse.client.tokens import CHARS_PER_TOKEN
from poolhouse.files import sha256_file
from poolhouse.http import ServerError
from poolhouse.hub.local import hub_cache
from poolhouse.log import say, warn
from poolhouse.sentinel.canary import wilson
from poolhouse.serve import gpu, prefault
from poolhouse.serve.broker import BrokerError

__all__ = ["Comparison", "Quant", "add_arguments", "items_needed", "load_comparison", "run"]

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "docs/experiments/quants-qwen3.8.json"
FIDELITY, TASKS, SPEED = "quants-fidelity", "quants-task", "quants-speed"
#: The fixed text by domain: checkout files taken in this order, then by pattern, cut to the length asked for.
CORPUS = {"code": (("src/poolhouse/bench/score.py", "src/poolhouse/bench/speed.py", "src/poolhouse/bench/keep.py",
                    "src/poolhouse/lock.py"), "src/poolhouse/**/*.py"),
          "prose": (("docs/agent-policy.md", "docs/serving.md"), "docs/*.md")}
CORPUS_CHARS = 80_000
VOCAB_GUESS = 248_320
#: Characters of text kept per token of context, more than code or prose take, so the text is never short.
CHARS_PER_CONTEXT_TOKEN = 5
#: Items kept at a time, so a run that stops keeps what it graded.
BATCH = 25
Z95 = 1.959964
Z80 = 0.841621


@dataclass(frozen=True)
class Quant:
    """One build of the model: the shards a repository publishes for it, or one local file."""

    name: str
    repo: str = ""
    shards: tuple[Mapping[str, Any], ...] = ()
    local: str = ""
    cache: str = ""
    """The K and V (and draft) cache type this build is run with; empty for the part's own."""

    def found(self) -> tuple[Path | None, str]:
        """``(first shard, state)``: ``present`` when every shard is here and is the published file, ``stale``
        when a file of that name here is another revision, ``missing`` otherwise."""
        if self.local:
            path = home.expand(self.local)
            return (path, "present") if path.is_file() else (None, "missing")
        state = "missing"
        base = hub_cache() / f"models--{self.repo.replace('/', '--')}" / "snapshots"
        for snapshot in sorted(base.glob("*")):
            paths = [snapshot / str(s["path"]) for s in self.shards]
            if not all(p.is_file() for p in paths):
                continue
            if all(_is_published(p, s) for p, s in zip(paths, self.shards, strict=True)):
                return paths[0], "present"
            state = "stale"
        return None, state

    @property
    def bytes(self) -> int:
        """The size of every shard together, 0 for a local file that is not here."""
        if self.local:
            path = home.expand(self.local)
            return path.stat().st_size if path.is_file() else 0
        return sum(int(s["bytes"]) for s in self.shards)

    def missing(self) -> dict[str, int]:
        """The blobs of this build not in the cache, by hash, with their sizes."""
        blobs = hub_cache() / f"models--{self.repo.replace('/', '--')}" / "blobs"
        return {str(s["sha256"]): int(s["bytes"]) for s in self.shards
                if not any(blobs.glob(f"{s['sha256']}*[!e]"))}


def _is_published(path: Path, shard: Mapping[str, Any]) -> bool:
    """Whether ``path`` is the published shard: by the hash its cache blob is named for, else by its size."""
    blob = Path(os.path.realpath(path)).name
    if len(blob) == 64:
        return blob.startswith(str(shard["sha256"]))
    return path.stat().st_size == int(shard["bytes"])


@dataclass(frozen=True)
class Comparison:
    """A reference build, the builds compared with it, and which IQ build is reported against which K build."""

    id: str
    reference: str
    quants: Mapping[str, Quant]
    versus: tuple[tuple[str, str], ...] = ()
    rates: Mapping[str, float] = field(default_factory=dict)
    note: str = ""

    def chosen(self, only: str = "", *, reference: bool = False) -> list[str]:
        """The names to measure, in the config's order. ``only`` narrows the builds compared; the reference is
        among them when ``reference`` is asked for, whatever ``only`` names."""
        wanted = [w for w in only.split(",") if w]
        unknown = sorted(set(wanted) - set(self.quants))
        if unknown:
            raise ValueError(f"{self.id} has no build named {', '.join(unknown)}; it has {', '.join(self.quants)}")
        return [n for n in self.quants
                if (reference if n == self.reference else not wanted or n in wanted)]


def cache_comparison(plan: Comparison, name: str, caches: str) -> Comparison:
    """One file of ``plan`` under each cache type of ``caches``, the first being the reference."""
    types = [c for c in caches.split(",") if c]
    if name not in plan.quants or len(types) < 2:
        raise ValueError(f"--cache-of takes one of {', '.join(plan.quants)}, and --caches at least two types")
    builds = {f"{name}@{c}": replace(plan.quants[name], name=f"{name}@{c}", cache=c) for c in types}
    first, *others = builds
    note = (f"One file, {name}, under cache types {', '.join(types)}; the reference is {first}. A draft head's cache "
            f"takes the same type; scoring a text uses no draft head.")
    return Comparison(f"{plan.id}+cache-{name}", first, builds, tuple((other, first) for other in others),
                      plan.rates, note)


def load_comparison(args: Any) -> Comparison:
    """The comparison asked for: a set or local files, narrowed to one file's cache types by ``--cache-of``."""
    plan = _named_comparison(args)
    return cache_comparison(plan, args.cache_of, args.caches) if args.cache_of else plan


def _named_comparison(args: Any) -> Comparison:
    """The comparison ``--set`` names in the config, or the one ``--model NAME=PATH`` and ``--reference`` make."""
    if args.model:
        quants = {}
        for one in args.model:
            name, sep, path = one.partition("=")
            if not sep or not name or not path:
                raise ValueError(f"--model takes NAME=PATH, not {one!r}")
            quants[name] = Quant(name, local=path)
        if args.reference not in quants:
            raise ValueError("--reference names one of the --model names")
        versus = tuple(tuple(p.split(",", 1)) for p in args.versus)
        if any(len(p) != 2 or set(p) - set(quants) for p in versus):
            raise ValueError("--versus takes IQ,K: two of the --model names")
        return Comparison(args.set or "custom", args.reference, quants, versus)
    config = json.loads(home.expand(args.config).read_text(encoding="utf-8"))
    if args.set not in config["sets"]:
        raise ValueError(f"--set is one of {', '.join(config['sets'])}")
    one = config["sets"][args.set]
    quants = {name: Quant(name, one["repo"], tuple(shards)) for name, shards in one["quants"].items()}
    return Comparison(args.set, args.reference or one["reference"], quants,
                      tuple((a, b) for a, b in one["versus"]), one.get("rates") or {}, one.get("note", ""))


# -- statistics ---------------------------------------------------------------------------

def items_needed(points: float, discordant: float) -> int:
    """Paired items for 80% power to separate two builds ``points`` apart at 95%, when a share ``discordant`` of
    items is passed by one and failed by the other."""
    return math.ceil((Z95 + Z80) ** 2 * discordant / (points / 100.0) ** 2)


def half_width(items: int, discordant: float) -> float:
    """The half-width, in points, of the 95% interval of a paired difference over ``items``."""
    return 100.0 * Z95 * math.sqrt(discordant / items)


def _mean(values: Sequence[float]) -> str:
    got = difference(values, [0.0] * len(values))
    return "-" if got is None else f"{got[0]:.5g} [{got[1]:.5g}, {got[2]:.5g}]"


def _verdict(first: str, second: str, got: tuple[float, float, float] | None, words: tuple[str, str, str],
             floor: float | None = None) -> str:
    """The plain sentence about ``first`` minus ``second``; ``words`` are the unit and what to say when that is above
    and below zero. A difference no larger than the noise ``floor`` is not distinguishable, whatever its interval."""
    unit, above, below = words
    if got is None:
        return f"{first} and {second}: too few shared results for an interval"
    mean, low, high = got
    span = f"difference {mean:+.5g} [{low:+.5g}, {high:+.5g}] {unit}"
    measured = "no noise floor measured" if floor is None else f"noise floor {floor:.5g}"
    if low <= 0 <= high:
        return f"{first} and {second} cannot be told apart: {span}; {measured}"
    if floor is not None and abs(mean) <= floor:
        return f"{first} and {second} are not distinguishable here: {span}, inside the noise floor of {floor:.5g}"
    return f"{first} {above if mean > 0 else below} {second}: {span}; {measured}"


# -- the plan -----------------------------------------------------------------------------

def _floor_paths(args: Any) -> list[str]:
    """The other ways each file is scored to measure what the path alone changes."""
    return [path for path, left_out in (("cpu", args.no_cpu), ("single", args.no_single)) if not left_out]


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def estimates(args: Any, plan: Comparison) -> dict[str, float]:
    """Wall seconds each part is expected to take, from the config's token rates for this machine."""
    rate = {"load_s": 60.0, "prefill_tps": 200.0, "decode_tps": 20.0, "cpu_tps": 40.0, "prompt_tokens": 300.0,
            "answer_tokens": 350.0, **plan.rates}
    quants = len(plan.chosen(args.only))
    tokens = args.chunks * args.ctx
    metal = rate["load_s"] + tokens / rate["prefill_tps"]
    cpu = 0 if args.no_cpu else rate["load_s"] + tokens / rate["cpu_tps"]
    single = 0 if args.no_single else rate["load_s"] + tokens / rate["decode_tps"]
    compared = 60.0
    fidelity = len(CORPUS) * (quants + 1) * (metal + cpu + single + compared)
    item = rate["prompt_tokens"] / rate["prefill_tps"] + rate["answer_tokens"] / rate["decode_tps"]
    tasks = (quants + 1 + bool(args.floor)) * (rate["load_s"] + args.items_planned * item)
    cell = sum(int(p) / rate["prefill_tps"] + args.generate / rate["decode_tps"] for p in args.prompts.split(","))
    speed = args.repeats * (quants + 1) * (rate["load_s"] + cell + args.settle)
    return {"fidelity": fidelity, "tasks": tasks, "speed": speed}


def show_plan(args: Any, plan: Comparison) -> int:
    """The files, what must be downloaded, the disk and time each part needs, and the commands. Loads nothing."""
    names = plan.chosen(args.only)
    say(f"{plan.id}: reference {plan.reference}; against it {', '.join(names)}")
    if plan.note:
        say(f"  {plan.note}")
    needed: dict[str, int] = {}
    for name in [plan.reference, *names]:
        quant = plan.quants[name]
        _, state = quant.found()
        new = {k: v for k, v in (quant.missing() if quant.repo else {}).items() if k not in needed}
        needed.update(new)
        say(f"  {name:<14} {quant.bytes / 1e9:7.2f} GB  {state:<8} downloads {sum(new.values()) / 1e9:7.2f} GB")
        if state != "present" and quant.repo:
            say(f"      poolhouse-models fetch hf:{quant.repo}/{quant.shards[0]['path']}")
    scored = args.chunks * (args.ctx - 1 - args.ctx // 2)
    one = scored * (2 * VOCAB_GUESS + 8)
    peak = one * (2 + len(_floor_paths(args)))
    free = shutil.disk_usage(bench.home_dir().parent if bench.home_dir().parent.exists() else Path.cwd()).free
    say(f"download: {sum(needed.values()) / 1e9:.1f} GB (a shard several builds share is counted once)")
    say(f"logits on disk: {one / 1e9:.1f} GB a file at a vocabulary of {VOCAB_GUESS} (an estimate), "
        f"{peak / 1e9:.1f} GB at once; free now {free / 1e9:.0f} GB")
    say(f"memory the scoring tool needs for logits beside the model and cache: "
        f"{(args.ctx - args.ctx // 2) * VOCAB_GUESS * 4 / 1e9:.1f} GB at a context of {args.ctx}")
    say(f"scored tokens: {scored} per domain, {scored * len(CORPUS)} per file and backend, "
        f"in blocks of {logits.BLOCK}")
    times = estimates(args, plan)
    say("estimated wall time, from token rates not yet measured by this command: "
        + ", ".join(f"{part} {_hours(s)}" for part, s in times.items()))
    say(f"tasks, {args.items_planned} shared items, if 10% of items differ: interval ±"
        f"{half_width(args.items_planned, 0.10):.1f} points; 80% power for 3 points needs "
        f"{items_needed(3, 0.10)} items, for 5 points {items_needed(5, 0.10)}")
    words = (f"--set {args.set}" + (f" --only {args.only}" if args.only else "")
             + (f" --cache-of {args.cache_of} --caches {args.caches}" if args.cache_of else ""))
    say("commands, from the checkout, one at a time on a quiet machine:")
    for line in ("build-tool", f"fidelity {words}",
                 f"tasks {words} --floor --items HumanEval.jsonl.gz --items mbpp.jsonl", f"speed {words}",
                 f"report {words}"):
        say(f"  PYTHONPATH=src python -m poolhouse.bench quants {line}")
    return 0


# -- measuring ----------------------------------------------------------------------------

@contextlib.contextmanager
def _measuring(args: Any) -> Iterator[None]:
    """The bench's measuring lock, with the run named where ``status`` reads it."""
    with lock.only_one(bench.home_dir() / "measuring.lock", wait=False):
        remember(["quants", args.action, "--set", args.set], pid=os.getpid())
        try:
            yield
        finally:
            ended()


def _require(plan: Comparison, names: Sequence[str]) -> dict[str, Path]:
    paths, absent = {}, []
    for name in names:
        path, state = plan.quants[name].found()
        if path is None:
            absent.append(f"{name} ({state})")
        else:
            paths[name] = path
    if absent:
        raise ValueError(f"not here as published: {', '.join(absent)}; "
                         f"'quants plan --set {plan.id}' says what to fetch")
    return paths


def _record(plan: Comparison, name: str, path: Path, **extra: Any) -> dict[str, Any]:
    """The part of a run's ``server`` record every part shares: the build compared and the file measured."""
    quant = plan.quants[name]
    build = {k: logits.active_build().get(k) for k in ("commit", "build", "version")}
    return {"set": plan.id, "quant": name, "file": str(path), "file_bytes": quant.bytes,
            "shards": [str(s["sha256"]) for s in quant.shards], "reference": plan.reference, "llama_cpp": build,
            **extra}


def corpus(args: Any, chars: int = CORPUS_CHARS) -> dict[str, Path]:
    """The fixed text by domain: ``--corpus`` files, else ``chars`` of each domain's checkout files, the named ones
    first, frozen under the bench's home the first time that length is asked for."""
    if args.corpus:
        return {home.expand(p).stem: home.expand(p).resolve() for p in args.corpus}
    out = {}
    for domain, (first, pattern) in CORPUS.items():
        path = bench.home_dir() / "quants" / "corpus" / f"{domain}-{chars}.txt"
        if not path.is_file():
            text = ""
            for source in dict.fromkeys([*(ROOT / s for s in first), *sorted(ROOT.glob(pattern))]):
                if len(text) >= chars:
                    break
                text += source.read_text(encoding="utf-8") + "\n"
            if len(text) < chars:
                raise ValueError(f"the checkout holds {len(text)} characters of {domain}, under the {chars} asked; "
                                 f"name longer files with --corpus")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text[:chars], encoding="utf-8")
        out[domain] = path
    return out


def fidelity(args: Any, plan: Comparison) -> int:
    """Each build's log probabilities on Metal compared block by block with the reference's, and each file, the
    reference too, compared with itself scored on the CPU and one token at a time: the noise floor. The files of log
    probabilities are removed once compared."""
    names = plan.chosen(args.only)
    paths = _require(plan, [plan.reference, *names])
    tool = home.expand(args.tool) if args.tool else logits.tool_path()
    if not tool.is_file():
        raise ValueError(f"no {logits.TOOL} at {tool}; 'python -m poolhouse.bench quants build-tool' compiles it")
    held = Path(args.logits_dir) if args.logits_dir else bench.home_dir() / "quants" / "logits"
    held.mkdir(parents=True, exist_ok=True)
    with _measuring(args), gpu.hold(f"bench quants fidelity {plan.id}"):
        busy = quiet.look()
        if busy.servers and not args.anyway:
            raise ValueError("\n".join(busy.refusal()))
        length = max(CORPUS_CHARS, args.ctx * max(args.chunks, 2) * CHARS_PER_CONTEXT_TOKEN)
        for domain, text in corpus(args, length).items():
            reference = held / f"{plan.id}-{domain}-reference.logits"
            made: list[Path] = [reference]

            def score(name: str, out: Path, path: str, text: Path = text, domain: str = domain) -> float:
                command = logits.argv(tool, paths[name], text, out, logits.Scoring(
                    args.ctx, args.chunks, cpu=path == "cpu", cache=plan.quants[name].cache or args.kv,
                    ubatch=1 if path == "single" else 512, extra=tuple(args.tool_arg)))
                say(f"  {name} on {path}, {domain}")
                return logits.run(command, model_files=[paths[name]], corpus=text, out=out)

            try:
                reference_seconds = score(plan.reference, reference, args.reference_backend)
                for name in [plan.reference, *names]:
                    own = name == plan.reference
                    if own and (args.reference_backend != "metal" or not _floor_paths(args)):
                        continue
                    base = reference if own else held / f"{plan.id}-{domain}-{name}-metal.logits"
                    others = {p: held / f"{plan.id}-{domain}-{name}-{p}.logits" for p in _floor_paths(args)}
                    made += [*others.values(), base]
                    seconds = {"metal": reference_seconds if own else score(name, base, "metal"),
                               **{p: score(name, out, p) for p, out in others.items()}}
                    against = {} if own else {"metal": base, **{p: f for p, f in others.items() if p == "cpu"}}
                    rows = [{"against": "reference", "backend": p, **one}
                            for p, out in against.items() for one in logits.blocks(logits.compare(reference, out))]
                    rows += [{"against": "self", "backend": p, **one}
                             for p, out in others.items() for one in logits.blocks(logits.compare(base, out))]
                    save(args.kept, rows, kind=FIDELITY, label=f"{plan.id}:{name}:{domain}", server=_record(
                        plan, name, paths[name], domain=domain, tool=str(tool), tool_sha256=sha256_file(tool),
                        tool_args=list(args.tool_arg), context=args.ctx, chunks=args.chunks,
                        cache_type=plan.quants[name].cache or args.kv,
                        reference_cache_type=plan.quants[plan.reference].cache or args.kv, draft="none",
                        template="none (raw text)", thinking="none (raw text)", corpus_sha256=sha256_file(text),
                        reference_backend=args.reference_backend, reference_file=str(paths[plan.reference]),
                        seconds=seconds))
                    if not args.keep_logits:
                        for one in [*others.values(), *([] if own else [base])]:
                            one.unlink(missing_ok=True)
            finally:
                if not args.keep_logits:
                    for one in made:
                        one.unlink(missing_ok=True)
    return report(args, plan, parts=("fidelity",))


def lease_config(args: Any, model: Path, *, context: int, parallel: int, cache: str = "") -> Any:
    """What ``bench speed --serve`` would lease for ``model``: no profile, thinking off, greedy, no draft head unless
    ``--draft`` names one, and ``cache`` (else ``--kv``) for every cache."""
    words = ["speed", "--serve", str(model), *([] if args.draft else ["--no-draft"]), "--no-profile",
             "--parallel", str(parallel), "--context", str(context), "--serve-port", str(args.serve_port),
             "--reasoning-budget", "0", "--serve-kv", cache or args.kv,
             *(["--binary", args.binary] if args.binary else [])]
    config = ops.swept(bench._parser().parse_args(words), str(model), None, context=context, head=args.draft)
    if getattr(args, "single", False):
        config = config.over(extra_args=("-ub", "1"))
    if cache:
        config = config.over(draft_cache_type=cache)
    return config.over(port=int(args.serve_port), timeout=float(args.timeout), n_predict=int(args.max_tokens),
                       temperature=0.0)


@contextlib.contextmanager
def served(args: Any, model: Path, *, context: int, parallel: int,
           cache: str = "") -> Iterator[tuple[Any, Any, dict[str, Any]]]:
    """``(client, server, held)`` for ``model`` leased as `lease_config` says."""
    config = lease_config(args, model, context=context, parallel=parallel, cache=cache)
    with bench_serve.up(config, binary=args.binary) as (server, held):
        held.pop("baseline", None)
        held.pop("loaded", None)
        yield config.client(server.base_url), server, held


def _served_record(plan: Comparison, name: str, path: Path, held: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    """The lease's own record with the comparison's laid over it; ``draft`` names a head the lease served after all."""
    return {**held, **_record(plan, name, path, thinking="off", draft=held.get("draft_model") or "none", **extra)}


def _template(client: Any) -> str:
    """The hash of the chat template the server answers with, empty when it will not say."""
    try:
        said = str((client.props() or {}).get("chat_template") or "")
    except (ServerError, OSError, ValueError, AttributeError):      # a server with no /props records none
        return ""
    return hashlib.sha256(said.encode()).hexdigest()[:16] if said else ""


def _kept_runs(args: Any, plan: Comparison, kind: str) -> list[dict[str, Any]]:
    if not home.expand(args.kept).exists():
        return []
    return [r for r in bench.runs(args.kept)
            if r.get("kind") == kind and (r.get("server") or {}).get("set") == plan.id]


def tasks(args: Any, plan: Comparison) -> int:
    """The same items asked of each build, greedy and unthinking, each graded by its own tests. Items already
    kept under the same settings are not asked again."""
    if not args.items:
        raise ValueError("tasks needs --items FILE: HumanEval.jsonl.gz and mbpp.jsonl, repeatable")
    items = codeitems.load(args.items)
    items = items[:args.limit] if args.limit else items
    names = plan.chosen(args.only, reference=True)
    paths = _require(plan, names)
    before, context = "", args.context
    if args.long_context:
        length = int(args.long_context * CHARS_PER_TOKEN)
        texts = corpus(args, max(CORPUS_CHARS, length)).values()
        before = "".join(p.read_text(encoding="utf-8")[:length // len(texts)] for p in texts)
        before = f"Reference files, for background only:\n{before}\n\nThe task:\n"
        context = max(context, ((args.long_context * 5 // 4 + args.max_tokens + 1023) // 1024) * 1024)
    settings = {"items": codeitems.digest(args.items), "max_tokens": args.max_tokens, "context": context,
                "parallel": args.parallel, "kv": args.kv, "draft": args.draft or "none", "thinking": "off",
                "temperature": 0.0, "binary": args.binary,
                "before": hashlib.sha256(before.encode()).hexdigest()[:16] if before else ""}
    with _measuring(args):
        floor = [(plan.reference, "single")] if args.floor else []
        for name, route in [*((n, "batched") for n in names), *floor]:
            done = {row["item"] for r in _kept_runs(args, plan, TASKS)
                    if r["server"]["quant"] == name and r["server"].get("route") == route
                    and r["server"].get("settings") == settings for row in r["rows"]}
            todo = [one for one in items if one.id not in done]
            if not todo:
                say(f"{name} ({route}): all {len(items)} items are kept already")
                continue
            say(f"{name} ({route}): {len(todo)} item(s)")
            cache = plan.quants[name].cache
            lease_args = argparse.Namespace(**{**vars(args), "single": route == "single"})
            with served(lease_args, paths[name], context=context * args.parallel, parallel=args.parallel,
                        cache=cache) as (client, _lease, held):
                record = _served_record(plan, name, paths[name], held, settings=settings, template=_template(client),
                                        cache_type=cache or args.kv, route=route)

                def ask(one: codeitems.Item, client: Any = client) -> dict[str, Any]:
                    began = time.time()
                    with gate.parallel("bench quants tasks"):
                        reply = client.chat([{"role": "user", "content": before + one.ask}], think=False)
                    return {"item": one.id, "reply": reply.content or "", "truncated": reply.truncated,
                            "prompt_tokens": ((reply.raw or {}).get("usage") or {}).get("prompt_tokens"),
                            "seconds": round(time.time() - began, 3)}

                with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                    for start in range(0, len(todo), BATCH):
                        part = todo[start:start + BATCH]
                        rows = list(pool.map(ask, part))
                        for one, row in zip(part, rows, strict=True):
                            row["passed"] = codeitems.grade(one, row["reply"])
                        save(args.kept, rows, kind=TASKS, label=f"{plan.id}:{name}", server=dict(record))
                        say(f"  {start + len(part)}/{len(todo)}: {sum(r['passed'] for r in rows)}/{len(rows)} passed")
    return report(args, plan, parts=("tasks",))


def residency(path: Path, quant: Quant) -> float | None:
    """The share of the build's bytes in the page cache now, None where that cannot be asked."""
    files = [path.parent / Path(str(s["path"])).name for s in quant.shards] or [path]
    try:
        held = sum(b - a for f in files for a, b in prefault.resident_runs(f, 0, f.stat().st_size))
        return held / sum(f.stat().st_size for f in files)
    except (OSError, prefault.Unsupported):
        return None


def speed(args: Any, plan: Comparison) -> int:
    """The speed grid of one stream on each build, ``--repeats`` times, the order turned each time so drift lands
    on every build alike. A repeat that found the machine busy is kept and marked contended."""
    names = plan.chosen(args.only, reference=True)
    paths = _require(plan, names)
    sizes = [int(p) for p in args.prompts.split(",")]
    context = ((max(sizes) + args.generate + 512 + 1023) // 1024) * 1024
    args.max_tokens = args.generate
    with _measuring(args):
        before = quiet.look()
        if not before.ok and not args.anyway:
            raise ValueError("\n".join(before.refusal()))
        for repeat in range(args.repeats):
            turn = repeat % len(names)
            for name in [*names[turn:], *names[:turn]]:
                time.sleep(args.settle)
                found = quiet.look()
                cached = residency(paths[name], plan.quants[name])
                say(f"repeat {repeat + 1}/{args.repeats} {name}: "
                    + ("quiet" if found.ok else "CONTENDED")
                    + (f", {cached:.0%} in the page cache" if cached is not None else ""))
                cache = plan.quants[name].cache
                with served(args, paths[name], context=context, parallel=1, cache=cache) as (client, lease, held):
                    template = _template(client)
                    cells = bench_speed.grid(client, [(size, 1) for size in sizes], generate=args.generate, log=say)
                    own = getattr(lease, "port", args.serve_port)
                    others = [s for s in quiet.look().servers if s.get("port") != own]
                reasons = [*found.reasons, *(f"another server on port {s['port']}" for s in others)]
                rows = [{"repeat": repeat, "contended": bool(reasons), "page_cache_before": cached, **cell}
                        for cell in cells]
                save(args.kept, rows, kind=SPEED, label=f"{plan.id}:{name}", server=_served_record(
                    plan, name, paths[name], held, template=template, context=context, cache_type=cache or args.kv,
                    kv_flag=args.kv, draft_arg=args.draft, binary_arg=args.binary, generate=args.generate, contention=reasons))
    return report(args, plan, parts=("speed",))


# -- the report ---------------------------------------------------------------------------

def _same(run_: Mapping[str, Any], keys: Sequence[str]) -> str:
    server = run_["server"]
    return json.dumps([server.get(k) for k in keys], sort_keys=True, default=str)


def _latest(runs: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> list[Mapping[str, Any]]:
    """The runs made under the same settings as the newest one; the others are counted out loud."""
    if not runs:
        return []
    newest = max(runs, key=lambda r: str(r.get("at") or ""))
    kept = [r for r in runs if _same(r, keys) == _same(newest, keys)]
    if len(kept) != len(runs):
        say(f"  {len(runs) - len(kept)} kept run(s) under other settings ({', '.join(keys)}) are left out")
    return kept


def _comparable(runs: list[dict[str, Any]]) -> list[Mapping[str, Any]]:
    """The fidelity runs that may be read together: one tool and one set of settings, and one text per domain."""
    same = _latest(runs, ("context", "chunks", "llama_cpp", "tool_sha256", "tool_args", "reference_file",
                          "reference_backend", "reference_cache_type"))
    return [one for domain in sorted({r["server"]["domain"] for r in same})
            for one in _latest([r for r in same if r["server"]["domain"] == domain], ("corpus_sha256",))]


def _say_comparison(plan: Comparison, key: tuple[str, str, str], rows: Sequence[Mapping[str, Any]]) -> None:
    """The figures of one build's blocks against the reference or against itself, and where the top tokens differ."""
    quant, against, backend = key
    if against == "reference":
        what = f"{backend} against {plan.reference}"
    else:
        what = "against itself, " + ("on the CPU" if backend == "cpu" else "one token at a time")
    tokens = sum(int(r["tokens"]) for r in rows)
    kl, agree, text = (_mean([float(r[name]) for r in rows]) for name in ("kl", "agree", "dlogp"))
    say(f"  {quant}, {what}: {tokens} tokens in {len(rows)} blocks; KL {kl} nats; same top token {agree}; "
        f"log probability of the text {text}")
    differ = sum(int(r["differ"]) for r in rows)
    overall = sum(float(r["margin"]) * int(r["tokens"]) for r in rows) / tokens
    near = f"{sum(float(r['differ_margin']) for r in rows) / differ:.4g}" if differ else "-"
    say(f"    near ties: top tokens differ on {differ} of {tokens}; logit gap between the first two tokens there "
        f"{near}, over all tokens {overall:.4g}")


def report_fidelity(args: Any, plan: Comparison) -> None:
    say(f"fidelity of {plan.id} against {plan.reference}")
    runs = _kept_runs(args, plan, FIDELITY)
    runs = _comparable(runs) if args.same_settings else runs
    if not runs:
        say("  nothing kept yet")
        return
    # (quant, against, backend) -> {(domain, chunk, block): row}; a later run of a block replaces an earlier one
    found: dict[tuple[str, str, str], dict[tuple[str, int, int], Mapping[str, Any]]] = {}
    for one in sorted(runs, key=lambda r: str(r.get("at") or "")):
        for row in one["rows"]:
            key = (one["server"]["quant"], row["against"], row["backend"])
            found.setdefault(key, {})[(one["server"]["domain"], row["chunk"], row["block"])] = row

    def column(key: tuple[str, str, str], name: str, shared: Sequence[tuple[str, int, int]]) -> list[float]:
        return [float(found[key][at][name]) for at in shared]

    def floor(builds: Sequence[str], figure: str, routes: Sequence[str] = ("cpu", "single")) -> float | None:
        """The most that ``figure`` moves when one of ``builds`` or the reference is only scored another way."""
        moved = [column(key, figure, sorted(found[key])) for key in
                 ((b, "self", route) for b in {*builds, plan.reference} for route in routes) if key in found]
        means = [statistics.fmean(values) for values in moved]
        return max((abs(m) if figure == "kl" else 1.0 - m for m in means), default=None)

    for key, rows in sorted(found.items()):
        _say_comparison(plan, key, list(rows.values()))
    say(f"noise floor of the top token: {floor([k[0] for k in found], 'agree')} of tokens differ at most between "
        f"two scorings of one file")

    for (quant, against, backend), rows in sorted(found.items()):
        last = max(at[2] for at in rows)
        if against != "reference" or last < 7:
            continue
        say(f"  {quant}, {backend}, along the scored half of the window:")
        for quarter in range(4):
            at = sorted(a for a in rows if a[2] * 4 // (last + 1) == quarter)
            low = quarter * (last + 1) // 4 * logits.BLOCK + runs[0]["server"]["context"] // 2
            say(f"    from token {low}, {len(at)} blocks: KL "
                f"{_mean(column((quant, against, backend), 'kl', at))} nats")

    def per_block(quant: str) -> dict[tuple[str, int, int], float]:
        """Metal's distance from the reference minus the CPU's, per block: the share Metal adds."""
        metal, cpu = found.get((quant, "reference", "metal"), {}), found.get((quant, "reference", "cpu"), {})
        return {a: float(metal[a]["kl"]) - float(cpu[a]["kl"]) for a in sorted(set(metal) & set(cpu))}

    say("backend: does Metal move a file further from the reference than the CPU does?")
    for quant in sorted({k[0] for k in found}):
        extra = list(per_block(quant).values())
        if extra:
            say("  " + _verdict(
                f"{quant} on Metal", "the same file on the CPU", difference(extra, [0.0] * len(extra)),
                ("nats of KL from the reference", "is further from the reference than",
                 "is closer to the reference than"), floor([quant], "kl", ("single",))))
    say("IQ against K, over the same blocks:")
    for iq, k in plan.versus:
        sizes = f"({plan.quants[iq].bytes / 1e9:.1f} GB against {plan.quants[k].bytes / 1e9:.1f} GB)"
        for backend in ("metal", "cpu"):
            a, b = (iq, "reference", backend), (k, "reference", backend)
            at = sorted(set(found.get(a, {})) & set(found.get(b, {})))
            if at:
                say(f"  [{backend}, {len(at)} blocks] " + _verdict(
                    iq, k, difference(column(a, "kl", at), column(b, "kl", at)),
                    ("nats of KL", "is further from the reference than", "is closer to the reference than"),
                    floor([iq, k], "kl")) + f" {sizes}")
                say(f"  [{backend}] top token: " + _verdict(
                    iq, k, difference(column(a, "agree", at), column(b, "agree", at)),
                    ("share of tokens", "agrees with the reference more often than",
                     "agrees with the reference less often than"), floor([iq, k], "agree")))
        left, right = per_block(iq), per_block(k)
        at = sorted(set(left) & set(right))
        if at:
            say("  [what Metal adds] " + _verdict(
                iq, k, difference([left[a] for a in at], [right[a] for a in at]),
                ("nats of KL", "loses more on Metal than", "loses less on Metal than"),
                floor([iq, k], "kl", ("single",))))


def report_tasks(args: Any, plan: Comparison) -> None:
    say(f"coding items of {plan.id}")
    runs = _latest(_kept_runs(args, plan, TASKS), ("settings", "llama_cpp"))
    passed: dict[str, dict[str, bool]] = {}
    again: dict[str, dict[str, bool]] = {}
    for one in sorted(runs, key=lambda r: str(r.get("at") or "")):
        into = again if one["server"].get("route") == "single" else passed
        into.setdefault(one["server"]["quant"], {}).update({r["item"]: bool(r["passed"]) for r in one["rows"]})
    if not passed:
        say("  nothing kept yet")
        return
    for name in sorted(passed):
        got, n = sum(passed[name].values()), len(passed[name])
        low, high = wilson(got, n, z=Z95)
        say(f"  {name}: {got}/{n} passed, {got / n:.1%} [{low:.1%}, {high:.1%}] (one greedy run)")
    floors = []
    for name, second in again.items():
        shared = sorted(set(passed.get(name, {})) & set(second))
        if shared:
            flipped = sum(passed[name][i] != second[i] for i in shared)
            floors.append(100.0 * flipped / len(shared))
            say(f"  noise floor: {name} asked again with its prompts decoded one token at a time: {flipped} of "
                f"{len(shared)} items change verdict ({floors[-1]:.2f} points)")
    floor = max(floors, default=None)
    against = [(name, plan.reference) for name in passed if name != plan.reference]
    for first, second in [*plan.versus, *against]:
        shared = sorted(set(passed.get(first, {})) & set(passed.get(second, {})))
        if len(shared) < 2:
            continue
        a = [100.0 * passed[first][i] for i in shared]
        b = [100.0 * passed[second][i] for i in shared]
        differ = sum(x != y for x, y in zip(a, b, strict=True))
        say(f"  [{len(shared)} shared items, {differ} differ] " + _verdict(
            first, second, difference(a, b), ("points", "passes more than", "passes fewer than"), floor))
        if not differ:
            say(f"    no item differs; under {300 / len(shared):.1f}% would differ at 95% (rule of three)")
        else:
            say(f"    to resolve 3 points at this rate of differing items: "
                f"{items_needed(3, differ / len(shared))} items")


def report_speed(args: Any, plan: Comparison) -> None:
    say(f"speed of {plan.id}")
    runs = _latest(_kept_runs(args, plan, SPEED),
                   ("context", "kv_flag", "draft_arg", "generate", "llama_cpp", "binary_arg"))
    # (quant, prompt size, figure) -> {(run time, repeat): value}; contended and failed repeats are only counted
    found: dict[tuple[str, int, str], dict[int, float]] = {}
    dropped: dict[str, int] = {}
    cached: dict[str, list[float]] = {}
    for one in sorted(runs, key=lambda r: str(r.get("at") or "")):
        name = one["server"]["quant"]
        for row in one["rows"]:
            if row.get("contended") or row.get("errors"):
                dropped[name] = dropped.get(name, 0) + 1
                continue
            if row.get("page_cache_before") is not None:
                cached.setdefault(name, []).append(float(row["page_cache_before"]))
            if row.get("draft_tokens"):
                row = {**row, "accepted": 100.0 * float(row.get("draft_taken") or 0) / float(row["draft_tokens"])}
            for figure in ("prefill_tps", "decode_tps", "accepted"):
                if row.get(figure) is not None:
                    found.setdefault((name, int(row["prompt_tokens"]), figure), {})[int(row["repeat"])] = row[figure]
    if not found:
        say("  nothing kept yet" if not dropped else f"  only contended or failed cells: {dropped}")
        return
    for (name, size, figure), values in sorted(found.items()):
        got = t_interval(list(values.values()))
        said = (f"{got[0]:.1f} [{got[1]:.1f}, {got[2]:.1f}]" if got
                else f"{statistics.fmean(values.values()):.1f} (one run, no interval)")
        unit = "% of drafted tokens" if figure == "accepted" else "tok/s"
        say(f"  {name}, {size} tokens, {figure.removesuffix('_tps')} {unit}: {said}, {len(values)} run(s)")
    for name, count in sorted(dropped.items()):
        say(f"  {name}: {count} contended or failed cell(s) left out")
    for name, shares in sorted(cached.items()):
        say(f"  {name}: in the page cache before loading, {min(shares):.0%} to {max(shares):.0%}")
    for iq, k in plan.versus:
        for name, size, figure in sorted(found):
            other = found.get((k, size, figure))
            if name != iq or not other:
                continue
            shared = sorted(set(found[(iq, size, figure)]) & set(other))
            got = t_interval([found[(iq, size, figure)][r] - other[r] for r in shared])
            say(f"  [{size} tokens, {figure.removesuffix('_tps')}, {len(shared)} shared repeats] " + _verdict(
                iq, k, got, ("% of drafted tokens", "has more accepted than", "has fewer accepted than")
                if figure == "accepted" else ("tok/s", "is faster than", "is slower than")))


def report(args: Any, plan: Comparison, *, parts: Sequence[str] = ("fidelity", "tasks", "speed")) -> int:
    """What is kept for the comparison, with counts, intervals and a sentence per pair."""
    say()
    if plan.note:
        say(f"note: {plan.note}")
    for part in parts:
        {"fidelity": report_fidelity, "tasks": report_tasks, "speed": report_speed}[part](args, plan)
        say()
    return 0


def add_arguments(sub: Any) -> argparse.ArgumentParser:
    """The ``quants`` subcommand's flags."""
    one = sub.add_parser("quants", allow_abbrev=False,
                         help="quantisations of one model compared here: Metal against the CPU, each against a "
                              "reference, graded coding items and repeated speed, with 95%% intervals; takes the "
                              "measuring lock itself")
    one.add_argument("action", choices=("plan", "build-tool", "fidelity", "tasks", "speed", "report"))
    one.add_argument("--set", default="", metavar="NAME", help="a comparison in the config")
    one.add_argument("--config", default=str(CONFIG), help="the comparisons (default: %(default)s)")
    one.add_argument("--only", default="", metavar="NAME,NAME", help="measure only these builds")
    one.add_argument("--model", action="append", default=[], metavar="NAME=PATH",
                     help="a local GGUF instead of a config set; repeatable")
    one.add_argument("--reference", default="", metavar="NAME", help="the build the others are compared with")
    one.add_argument("--versus", action="append", default=[], metavar="IQ,K",
                     help="with --model, an IQ build reported against a K build; repeatable")
    one.add_argument("--kept", default=bench.STORE, type=bench.in_home,
                     help="where the runs are kept (default: %(default)s)")
    one.add_argument("--anyway", action="store_true", help="measure on a busy machine")
    one.add_argument("--cache-of", default="", metavar="NAME",
                     help="compare cache types on this one build instead of the builds")
    one.add_argument("--caches", default="f16,q8_0", metavar="TYPE,TYPE",
                     help="with --cache-of; the first is the reference (default: %(default)s)")
    group = one.add_argument_group("fidelity")
    group.add_argument("--tool", default="", metavar="PATH", help=f"the {logits.TOOL} to run")
    group.add_argument("--tool-arg", action="append", default=[], metavar="WORD",
                       help="one more word for the tool, such as a tensor placement; repeatable")
    group.add_argument("--ctx", type=int, default=1024, metavar="N",
                       help="context of one chunk; its second half is scored (default: %(default)s)")
    group.add_argument("--chunks", type=int, default=8, metavar="N", help="chunks per domain (default: %(default)s)")
    group.add_argument("--corpus", action="append", default=[], metavar="FILE",
                       help="a text file instead of the frozen code and prose; repeatable")
    group.add_argument("--no-cpu", action="store_true", help="leave out the CPU-only runs")
    group.add_argument("--no-single", action="store_true", help="leave out the one-token-at-a-time runs")
    group.add_argument("--reference-backend", choices=("metal", "cpu"), default="metal")
    group.add_argument("--logits-dir", default="", metavar="DIR", help="where the log probabilities are written")
    group.add_argument("--keep-logits", action="store_true", help="do not remove them once compared")
    group.add_argument("--mix-settings", dest="same_settings", action="store_false",
                       help="report runs made under different settings together")
    group = one.add_argument_group("tasks")
    group.add_argument("--items", action="append", default=[], metavar="FILE",
                       help="HumanEval or MBPP items, JSON lines, gzipped or not; repeatable")
    group.add_argument("--limit", type=int, default=0, metavar="N", help="only the first N items")
    group.add_argument("--items-planned", type=int, default=1138, metavar="N",
                       help="items the plan estimates for (default: HumanEval's 164 and MBPP's 974)")
    group.add_argument("--max-tokens", type=int, default=1024, metavar="N")
    group.add_argument("--context", type=int, default=8192, metavar="N", help="context per slot")
    group.add_argument("--parallel", type=int, default=1, metavar="N",
                       help="items asked at once; above 1 greedy replies can vary with what shares the batch")
    group.add_argument("--floor", action="store_true",
                       help="ask the reference again with prompts decoded one token at a time, to measure the noise")
    group.add_argument("--long-context", type=int, default=0, metavar="TOKENS",
                       help="put about this much fixed text before every item")
    group = one.add_argument_group("speed")
    group.add_argument("--repeats", type=int, default=5, metavar="N")
    group.add_argument("--prompts", default="512,16384", metavar="N,N", help="short and long prompts, in tokens")
    group.add_argument("--generate", type=int, default=256, metavar="N")
    group.add_argument("--settle", type=float, default=20.0, metavar="SECONDS", help="waited before each load")
    group = one.add_argument_group("serving")
    group.add_argument("--kv", default="q8_0", metavar="TYPE",
                       help="the K and V cache type of every part, scoring included (default: %(default)s)")
    group.add_argument("--draft", default="", metavar="PATH", help="a draft head, off unless named")
    group.add_argument("--serve-port", type=int, default=8099)
    group.add_argument("--binary", default="", metavar="PATH", help="the llama.cpp server build to lease")
    group.add_argument("--timeout", type=float, default=600.0, metavar="SECONDS", help="for one request")
    return one


def run(args: Any) -> int:
    """The ``quants`` subcommand after the parse."""
    try:
        if args.action == "build-tool":
            with lock.only_one(bench.home_dir() / "measuring.lock", wait=False):
                say(str(logits.build_tool(say)))
            return 0
        plan = load_comparison(args)
        return {"plan": show_plan, "fidelity": fidelity, "tasks": tasks, "speed": speed,
                "report": report}[args.action](args, plan)
    except lock.Busy as why:
        warn(f"error: {why}. Another measurement is running; wait for it.")
        return 3
    except (ValueError, OSError, BrokerError, logits.ToolFailed) as why:
        warn(f"error: {why}")
        return 2
