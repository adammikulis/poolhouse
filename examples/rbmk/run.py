"""Command line for the RBMK experiment.

Results go to a local run store under the poolhouse state directory, never into the repository.

    python -m examples.rbmk.run batch --count 300 --seed 1 --memory none history   # varied poisoned starts, resumable
    python -m examples.rbmk.run record poisoned_a                                    # named scenarios, real model
    python -m examples.rbmk.run live poisoned_a                                      # the same on separate clocks (asyncio)
    python -m examples.rbmk.run replay FILE                                          # no model; checks the digest
    python -m examples.rbmk.run export FILE --out OUT                                # replay into the full run document
    python -m examples.rbmk.run baseline historic_1986 --out DIR                     # scripted crew or naive rule
    python -m examples.rbmk.run summary                                              # end states by model and memory
    python -m examples.rbmk.run selftest poisoned_a                                  # one real decision
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

from poolhouse import home

from . import recorder, scenarios, starts, whatif
from .decider.model import LiveAsker, ModelStopped
from .decider.policy import CrewScript, ModelDecider, NaiveDecider
from .display import (
    export as display,
    text,
)
from .runner import Result, Runner, RunOptions


def store() -> Path:
    """The local run store: under the poolhouse state directory, outside the repository."""
    return home.state("rbmk", "runs")


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(recorder.dumps(doc) + "\n")


def clocks(scn: scenarios.Scenario, res: Result) -> dict[str, Any]:
    """The clocks of a run, as data."""
    return {"plant_substep_s": {"slow": 10.0, "fast": 0.5}, "tick_s": {"slow": scn.slow_s, "fast": scenarios.FAST_TICK_S},
            "decision": "back to back: the next request starts when the previous answer has been applied",
            "speed": {"fast_regime": res.speed, "quiet_regime": scn.quiet_speed}}


def backend(asker: LiveAsker, warm_s: float) -> dict[str, Any]:
    """Where the answers came from."""
    return {"mode": "recorded", "model": asker.model, "recorded_at": datetime.date.today().isoformat(),
            "source": "live run of poolhouse.decide, backend pointer", "warm_up_s": round(warm_s, 1)}


def rods_of(scn: scenarios.Scenario) -> list[dict[str, Any]]:
    """The rod map for a document."""
    return [{k: row[k] for k in ("id", "kind", "x", "y", "w")} for row in scn.build_core().rods.table()]


def _where(args: argparse.Namespace, model: str, memory: str) -> Path:
    return Path(args.out) if args.out else store() / model / memory


def _run_one(scn: scenarios.Scenario, asker: LiveAsker, warm: float, memory: str) -> tuple[Result, dict[str, Any]]:
    res = Runner(scn, ModelDecider(asker, scn.seed), RunOptions(memory=memory)).run()
    return res, recorder.recording_of(res, scn, backend(asker, warm), clocks(scn, res))


def _session(args: argparse.Namespace, work: Any) -> int:
    """Hold the GPU, load the model once, run ``work(asker, warm)``; any failure stops loudly with a nonzero exit."""
    from poolhouse.serve import gpu
    from poolhouse.serve.broker import BrokerError

    try:
        asker = LiveAsker(timeout=args.timeout)
        with gpu.hold("RBMK experiment", wait_s=args.wait):
            work(asker, asker.warm())
    except ModelStopped as exc:
        print(f"RUN STOPPED, nothing was applied after this point: {exc}", file=sys.stderr)
        return 2
    except (BrokerError, OSError) as exc:
        print(f"RUN STOPPED: the GPU hold or the broker refused: {exc}", file=sys.stderr)
        return 2
    return 0


def record(args: argparse.Namespace) -> int:
    """Run named scenarios with the real decision model and write one recording per scenario."""
    def work(asker: LiveAsker, warm: float) -> None:
        for key in args.scenarios:
            res, rec = _run_one(scenarios.scenario(key), asker, warm, args.memory)
            _write(_where(args, rec["meta"]["backend"]["model"], args.memory) / f"{key}.json", rec)
            print(f"{key}: {text.outcome(res)['state']}, {len(res.decisions)} decisions", flush=True)

    return _session(args, work)


def batch(args: argparse.Namespace) -> int:
    """Run varied poisoned starts (`starts.draw`) for each memory variant; a start already in the store is skipped."""
    def work(asker: LiveAsker, warm: float) -> None:
        for k in range(args.first, args.first + args.count):
            scn = starts.draw(args.seed, k)
            for memory in args.memory:
                path = _where(args, asker.model, memory) / f"{scn.key}.json"
                if path.exists():
                    continue
                res, rec = _run_one(scn, asker, warm, memory)
                _write(path, rec)
                print(f"{scn.key} [{memory}]: {text.outcome(res)['state']}, {len(res.decisions)} decisions", flush=True)

    return _session(args, work)


def live_cmd(args: argparse.Namespace) -> int:
    """Run one scenario live: the plant on its own clock, each decision an async request applied at its real latency."""
    import asyncio

    from poolhouse.serve import gpu
    from poolhouse.serve.broker import BrokerError

    from .live import COMPUTE, LiveRunner

    scn = scenarios.scenario(args.scenario)
    try:
        asker = LiveAsker(timeout=args.timeout)
        runner = Runner(scn, ModelDecider(asker, scn.seed), RunOptions(speed=args.speed, memory=args.memory))
        live = LiveRunner(runner, speed=args.speed, timeout=args.timeout)
        with gpu.hold("RBMK experiment live", wait_s=args.wait):
            warm = asker.warm()
            res = asyncio.run(live.run())
    except ModelStopped as exc:
        print(f"RUN STOPPED, nothing was applied after this point: {exc}", file=sys.stderr)
        if COMPUTE.dead:
            print("a model call from this run is still running; this process exits now so that compute is released", file=sys.stderr)
            sys.stderr.flush()
            os._exit(2)
        return 2
    except (BrokerError, OSError) as exc:
        print(f"RUN STOPPED: the GPU hold or the broker refused: {exc}", file=sys.stderr)
        return 2
    _write(_where(args, asker.model, args.memory) / f"{scn.key}.json", recorder.recording_of(res, scn, backend(asker, warm), clocks(scn, res)))
    print(f"{scn.key}: {text.outcome(res)['state']}, {len(res.decisions)} decisions, speed {args.speed}")
    return 0


def _scenario_of(rec: dict[str, Any]) -> scenarios.Scenario:
    key = rec["meta"]["scenario"]
    if key.startswith("start_"):
        _, seed, k = key.split("_")
        return starts.draw(int(seed), int(k))
    return scenarios.scenario(key)


def replay_cmd(args: argparse.Namespace) -> int:
    """Replay a recording with no model and check that it reproduces the run it was made from."""
    rec = json.loads(Path(args.file).read_text())
    try:
        res = recorder.replay(rec, _scenario_of(rec))
    except recorder.ReplayMismatch as exc:
        print(f"replay of {args.file}: DIFFERENT: {exc}")
        return 1
    print(f"replay of {args.file}: identical ({text.outcome(res)['state']})")
    return 0


def export_cmd(args: argparse.Namespace) -> int:
    """Replay a recording into the full run document, with what-if branches, for an interface."""
    rec = json.loads(Path(args.file).read_text())
    scn = _scenario_of(rec)
    res = recorder.replay(rec, scn)
    _write(Path(args.out), display.export(res, scn, rec["meta"], rods_of(scn), whatif.whatifs(res, scn)))
    return 0


def baseline(args: argparse.Namespace) -> int:
    """Run the scripted crew (history scenarios) or the naive rule; no model is involved."""
    scn = scenarios.scenario(args.scenario)
    decider = CrewScript() if scn.crew else NaiveDecider()
    res = Runner(scn, decider).run()
    meta = {"backend": {"mode": "none", "model": None, "recorded_at": None, "source": f"{decider.kind} controller, no decision model"},
            "clocks": clocks(scn, res)}
    _write(Path(args.out or store() / "baselines") / f"{scn.key}.{decider.kind}.json", display.export(res, scn, meta, rods_of(scn)))
    print(f"{scn.key} ({decider.kind}): {text.outcome(res)['state']}")
    return 0


def summary(args: argparse.Namespace) -> int:
    """The measured end states of the recordings in the run store, by model and memory variant."""
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for path in sorted(Path(args.dir or store()).rglob("*.json")):
        rec = json.loads(path.read_text())
        if rec.get("schema") != recorder.RECORDING:
            continue
        state = text.outcome(recorder.replay(rec, _scenario_of(rec)))["state"]
        row = counts.setdefault((rec["meta"]["backend"]["model"], rec["meta"]["memory"]), {})
        row[state] = row.get(state, 0) + 1
    print("| model | memory | runs | damaged | margin below 15, no damage | margin at least 15, no damage |\n|---|---|---|---|---|---|")
    for (model, memory), row in sorted(counts.items()):
        print(f"| {model} | {memory} | {sum(row.values())} | {row.get('damaged', 0)} | {row.get('margin_below_15', 0)} | "
              f"{row.get('margin_at_least_15', 0)} |")
    return 0


def selftest(args: argparse.Namespace) -> int:
    """Ask the real model one full hierarchical decision about a scenario's first view and print every probability."""
    from poolhouse.serve import gpu

    try:
        asker = LiveAsker()
        runner = Runner(scenarios.scenario(args.scenario), ModelDecider(asker, 0))
        with gpu.hold("RBMK experiment selftest", wait_s=args.wait):
            asker.warm()
            prop = runner.decider.propose(0, runner.sensors.view(runner.plant))
    except ModelStopped as exc:
        print(f"SELFTEST FAILED: {exc}", file=sys.stderr)
        return 2
    for name, a in prop.answers.items():
        print(f"{name}: {a['choice']} { {k: round(v, 3) for k, v in a['scores'].items()} } tokens={a.get('tokens')}")
    print(f"model {asker.model}; chose {prop.cls} {prop.target} step {prop.step}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn: Any, *flags: tuple[str, dict[str, Any]], positional: str | None = None, many: bool = False) -> None:
        p = sub.add_parser(name)
        if positional:
            p.add_argument(positional, nargs="+" if many else None)
        for flag, kw in flags:
            p.add_argument(flag, **kw)
        p.set_defaults(fn=fn)

    wait = ("--wait", {"type": float, "default": 0.0, "help": "seconds to wait for the GPU claim; 0 refuses at once"})
    timeout = ("--timeout", {"type": float, "default": 120.0, "help": "seconds a model call may take before the run halts"})
    memory = ("--memory", {"default": "none", "choices": ("none", "history")})
    out = ("--out", {"default": None, "help": "directory; the default is the run store under the poolhouse state directory"})
    add("record", record, out, wait, timeout, memory, positional="scenarios", many=True)
    add("batch", batch, out, wait, timeout, ("--count", {"type": int, "required": True}), ("--seed", {"type": int, "default": 1}),
        ("--first", {"type": int, "default": 0}), ("--memory", {"nargs": "+", "default": ["none"], "choices": ("none", "history")}))
    add("live", live_cmd, out, wait, timeout, memory, ("--speed", {"type": float, "default": 60.0}), positional="scenario")
    add("replay", replay_cmd, positional="file")
    add("export", export_cmd, ("--out", {"required": True}), positional="file")
    add("baseline", baseline, out, positional="scenario")
    add("summary", summary, ("--dir", {"default": None}))
    add("selftest", selftest, wait, positional="scenario")
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
