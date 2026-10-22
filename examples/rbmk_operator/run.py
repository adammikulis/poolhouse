"""Command line for the RBMK operator example.

    python examples/rbmk_operator/run.py record poisoned_a --out DIR   # real model, under a GPU hold
    python examples/rbmk_operator/run.py live SCENARIO --out DIR --speed 60   # async: plant, decider and speed knob on separate clocks
    python examples/rbmk_operator/run.py replay FILE                   # no model; checks the file replays identically
    python examples/rbmk_operator/run.py baseline historic_1986 --out DIR   # scripted crew or naive rule, no model
    python examples/rbmk_operator/run.py summary DIR                   # recoveries and failures table
    python examples/rbmk_operator/run.py selftest                      # one real decision, real model
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rbmk_operator import bundle, scenarios
from rbmk_operator.loop import Result, Runner
from rbmk_operator.operator_model import (
    ABSTAIN_BELOW,
    CrewOperator,
    LiveAsker,
    ModelOperator,
    ModelStopped,
    NaiveOperator,
)


def _write(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.dumps(doc) + "\n")


def _backend(asker: LiveAsker) -> dict[str, Any]:
    return {"mode": "recorded", "model": asker.model, "recorded_at": datetime.date.today().isoformat(),
            "abstain_below": asker.abstain_below, "source": "live run of poolhouse.decide, backend pointer"}


def record(args: argparse.Namespace) -> int:
    """Run scenarios with the real decision model and write one JSON per scenario; stop loudly on any failure."""
    from poolhouse.serve import gpu
    from poolhouse.serve.broker import BrokerError

    try:
        asker = LiveAsker(abstain_below=args.abstain_below)
        with gpu.hold("RBMK operator example", wait_s=args.wait):
            for key in args.scenarios:
                scn = scenarios.scenario(key)
                print(f"{key}: asking {asker.model or 'the decision model'} ...", flush=True)
                res = _run_live(scn, asker, Path(args.out))
                doc = bundle.export(res, scn, _backend(asker), with_whatif=False)
                _write(Path(args.out) / f"{key}.json", bundle.recording_of(doc))
                print(f"{key}: {doc['outcome']['verdict']} ({doc['outcome']['why']}), {len(res.decisions)} rows")
    except ModelStopped as exc:
        print(f"RUN STOPPED, no action was taken after this point: {exc}", file=sys.stderr)
        return 2
    except (BrokerError, OSError) as exc:
        print(f"RUN STOPPED: the GPU hold or the broker refused: {exc}", file=sys.stderr)
        return 2
    return 0


def _run_live(scn: scenarios.Scenario, asker: LiveAsker, out: Path) -> Result:
    runner = Runner(scn, ModelOperator(asker))
    try:
        return runner.run()
    except ModelStopped as exc:
        doc = bundle.export(runner.res, scn, {"mode": "failed", "model": asker.model or None,
                                              "recorded_at": datetime.date.today().isoformat(),
                                              "abstain_below": asker.abstain_below, "source": "live run"}, False)
        doc["meta"]["error"] = str(exc)
        _write(out / f"{scn.key}.failed.json", doc)
        raise


def live_cmd(args: argparse.Namespace) -> int:
    """Run one scenario live: the plant on its own clock, each decision an async request, the answer applied at its real latency."""
    import asyncio

    from poolhouse.serve import gpu
    from poolhouse.serve.broker import BrokerError
    from rbmk_operator.live import COMPUTE, LiveRunner

    scn = scenarios.scenario(args.scenario)
    try:
        asker = LiveAsker(abstain_below=args.abstain_below, timeout=args.timeout)
        live = LiveRunner(Runner(scn, ModelOperator(asker)), speed=args.speed, timeout=args.timeout)
        with gpu.hold("RBMK operator live", wait_s=args.wait):
            res = asyncio.run(live.run())
    except ModelStopped as exc:
        print(f"RUN STOPPED, no action was taken after this point: {exc}", file=sys.stderr)
        if COMPUTE.dead:
            print("a model call from this run is still running; this process exits now so that compute is released", file=sys.stderr)
            sys.stderr.flush()
            os._exit(2)
        return 2
    except (BrokerError, OSError) as exc:
        print(f"RUN STOPPED: the GPU hold or the broker refused: {exc}", file=sys.stderr)
        return 2
    doc = bundle.export(res, scn, _backend(asker), with_whatif=False)
    _write(Path(args.out) / f"{scn.key}.json", bundle.recording_of(doc))
    print(f"{scn.key}: {doc['outcome']['verdict']} ({doc['outcome']['why']}), {len(res.decisions)} rows, speed {args.speed}")
    return 0


def replay_cmd(args: argparse.Namespace) -> int:
    """Replay a recording with no model and check that it reproduces the run it was made from."""
    rec = json.loads(Path(args.file).read_text())
    scn = scenarios.scenario(rec["meta"]["scenario"]["key"])
    doc = bundle.export(bundle.replay(rec, scn), scn, rec["meta"]["backend"], with_whatif=False)
    same = bundle.digest(doc) == rec["digest"]
    print(f"replay of {args.file}: {'identical' if same else 'DIFFERENT'} ({doc['outcome']['verdict']})")
    return 0 if same else 1


def export_cmd(args: argparse.Namespace) -> int:
    """Replay a recording into the full run document, with what-if branches, for the interface."""
    rec = json.loads(Path(args.file).read_text())
    scn = scenarios.scenario(rec["meta"]["scenario"]["key"])
    doc = bundle.export(bundle.replay(rec, scn), scn, rec["meta"]["backend"])
    if bundle.digest(doc) != rec["digest"]:
        print("EXPORT REFUSED: the replay differs from the recording", file=sys.stderr)
        return 1
    _write(Path(args.out), doc)
    return 0


def baseline(args: argparse.Namespace) -> int:
    """Run the scripted crew (history scenarios) or the naive rule; no model is involved."""
    scn = scenarios.scenario(args.scenario)
    op = CrewOperator() if scn.crew else NaiveOperator()
    res = Runner(scn, op).run()
    doc = bundle.export(res, scn, {"mode": "none", "model": None, "recorded_at": None, "abstain_below": None,
                                   "source": f"{op.kind} controller, no decision model"})
    _write(Path(args.out) / f"{scn.key}.{op.kind}.json", doc)
    print(f"{scn.key} ({op.kind}): {doc['outcome']['verdict']} - {doc['outcome']['why']}")
    return 0


def summary(args: argparse.Namespace) -> int:
    """A table of recoveries and failures over the recordings in a directory."""
    rows = []
    for path in sorted(Path(args.dir).glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("schema") != bundle.RECORDING:
            continue
        meta, out = doc["meta"], doc["outcome"]
        rows.append((meta["scenario"]["key"], out["verdict"], out["why"], out["peak_MW"], out["decisions"], out["abstained"],
                     meta["backend"]["model"], meta["backend"]["recorded_at"]))
    print("| scenario | verdict | why | peak MW | rows | abstained | model | recorded |\n|---|---|---|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(str(x) for x in r) + " |")
    ok = sum(1 for r in rows if r[1] == "recovered")
    print(f"\n{ok} recovered, {len(rows) - ok} failed, of {len(rows)} runs")
    return 0


def calibrate(args: argparse.Namespace) -> int:
    """Abstain-floor calibration on the recordings: for each floor, the abstain rate and how often a kept action was followed by worse."""
    samples = []
    for path in sorted(Path(args.dir).glob("*.json")):
        rec = json.loads(path.read_text())
        if rec.get("schema") != bundle.RECORDING or rec["meta"]["backend"]["mode"] != "recorded":
            continue
        scn = scenarios.scenario(rec["meta"]["scenario"]["key"])
        doc = bundle.export(bundle.replay(rec, scn), scn, rec["meta"]["backend"], with_whatif=False)
        for d in doc["decisions"]:
            cls = d["answers"].get("class")
            if d["kind"] == "decision" and cls and "outcome" in d:
                samples.append((max(cls["scores"].values()), d["outcome"]["verdict"] == "worse", d["applied"] is not None
                              and d["applied"]["cls"] not in ("hold", "orm_print")))
    print(f"{len(samples)} class decisions over the recordings\n| floor | abstain rate | worse after a kept action | worse after an abstained one |\n|---|---|---|---|")
    for floor in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        kept = [w for top, w, _ in samples if top >= floor]
        held = [w for top, w, _ in samples if top < floor]
        def rate(xs):
            return f"{100 * sum(xs) / len(xs):.0f}% of {len(xs)}" if xs else "none"
        print(f"| {floor:.2f} | {100 * len(held) / len(samples):.0f}% | {rate(kept)} | {rate(held)} |")
    return 0


def index_cmd(args: argparse.Namespace) -> int:
    """Write recordings/index.json and rename each recording to <scenario>-<model-id>-<date>.json."""
    out = []
    for path in sorted(Path(args.dir).glob("*.json")):
        rec = json.loads(path.read_text())
        if rec.get("schema") != bundle.RECORDING:
            continue
        meta, key = rec["meta"], rec["meta"]["scenario"]["key"]
        name = f"{key}-{meta['backend']['model']}-{meta['backend']['recorded_at']}.json"
        if path.name != name:
            path.rename(path.with_name(name))
        out.append({"file": name, "scenario": key, "title": meta["scenario"]["title"], "group": meta["scenario"]["group"],
                    "model": meta["backend"]["model"], "recorded_at": meta["backend"]["recorded_at"],
                    "abstain_below": meta["backend"]["abstain_below"], "verdict": rec["outcome"]["verdict"],
                    "why": rec["outcome"]["why"], "peak_MW": rec["outcome"]["peak_MW"]})
    _write(Path(args.dir) / "index.json", {"schema": "rbmk-operator-index/1", "recordings": out})
    print(f"{len(out)} recordings indexed")
    return 0


def selftest(args: argparse.Namespace) -> int:
    """Ask the real model one full hierarchical decision about a poisoned start and print every probability."""
    from poolhouse.serve import gpu

    try:
        asker = LiveAsker()
        runner = Runner(scenarios.scenario(args.scenario), ModelOperator(asker))
        with gpu.hold("RBMK operator selftest", wait_s=0.0):
            prop = runner.operator.propose(0, runner.plant)
    except ModelStopped as exc:
        print(f"SELFTEST FAILED: {exc}", file=sys.stderr)
        return 2
    for name, a in prop.answers.items():
        print(f"{name}: {a['choice']} { {k: round(v, 3) for k, v in a['scores'].items()} } tokens={a.get('tokens')}")
    print(f"model {asker.model}; proposed {prop.cls} {prop.target} step {prop.step} ({prop.source})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record")
    p.add_argument("scenarios", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--abstain-below", type=float, default=ABSTAIN_BELOW)
    p.add_argument("--wait", type=float, default=0.0, help="seconds to wait for the GPU claim; 0 refuses at once if it is held")
    p.set_defaults(fn=record)
    p = sub.add_parser("live")
    p.add_argument("scenario")
    p.add_argument("--out", required=True)
    p.add_argument("--speed", type=float, default=60.0, help="simulated seconds per wall second")
    p.add_argument("--timeout", type=float, default=120.0, help="seconds a model call may take before the run halts")
    p.add_argument("--abstain-below", type=float, default=ABSTAIN_BELOW)
    p.add_argument("--wait", type=float, default=0.0)
    p.set_defaults(fn=live_cmd)
    p = sub.add_parser("replay")
    p.add_argument("file")
    p.set_defaults(fn=replay_cmd)
    p = sub.add_parser("export")
    p.add_argument("file")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=export_cmd)
    p = sub.add_parser("baseline")
    p.add_argument("scenario")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=baseline)
    p = sub.add_parser("summary")
    p.add_argument("dir")
    p.set_defaults(fn=summary)
    p = sub.add_parser("calibrate")
    p.add_argument("dir")
    p.set_defaults(fn=calibrate)
    p = sub.add_parser("index")
    p.add_argument("dir")
    p.set_defaults(fn=index_cmd)
    p = sub.add_parser("selftest")
    p.add_argument("scenario", nargs="?", default="poisoned_a")
    p.set_defaults(fn=selftest)
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
