"""Runs one scenario of the RBMK operator demo and writes the control room's feed JSON.

    python examples/rbmk_control_room/export_feed.py --scenario pump_trip --operator model \
        --live examples/rbmk_control_room/viewer/feed/live.json --replay examples/rbmk_control_room/viewer/feed/replay.json

``--live`` is rewritten after every decision while the run goes on (meta.mode "live"); ``--replay`` is written once when
the run ends (meta.mode "replay"). The model operator asks the released decision checkpoint under a GPU hold at the
Broker, and the run stops loudly when the model cannot answer; there is no substitute.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.rbmk_operator.loop import Result, Runner  # noqa: E402
from examples.rbmk_operator.operator_model import CrewOperator, ModelOperator, NaiveOperator  # noqa: E402
from examples.rbmk_operator.scenarios import scenario  # noqa: E402


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def feed_of(res: Result, mode: str, runner: Runner, args: argparse.Namespace, model: str) -> dict[str, Any]:
    """The feed document: the run's public rows plus a meta header."""
    doc = res.public()
    doc["decisions"] = [{k: v for k, v in d.items() if k not in ("calls", "state_text")} for d in doc["decisions"]]
    doc["meta"] = {
        "mode": mode, "model": model, "operator": args.operator, "scenario": args.scenario,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "examples/rbmk_operator", "format": 1,
        "rods": [{"id": r.id, "kind": r.kind} for r in runner.plant.core.rods.rods],
    }
    return doc


def build_operator(kind: str, pointer: str) -> tuple[Any, Any]:
    """The operator and the asker behind it (None unless it is the model)."""
    if kind == "naive":
        return NaiveOperator(), None
    if kind == "crew":
        return CrewOperator(), None
    from examples.rbmk_operator.operator_model import LiveAsker

    asker = LiveAsker(pointer)
    return ModelOperator(asker), asker


def parse(argv: list[str]) -> argparse.Namespace:
    """The command line."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="pump_trip")
    ap.add_argument("--operator", choices=("model", "naive", "crew"), default="model")
    ap.add_argument("--pointer", default="", help="a registered decider name (default: the released Strands checkpoint)")
    ap.add_argument("--live", type=Path, help="progressive feed file, meta.mode live")
    ap.add_argument("--replay", type=Path, help="final feed file, meta.mode replay")
    ap.add_argument("--wait", type=float, default=600.0, help="seconds to wait for the GPU hold")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run the scenario and write the feed files."""
    args = parse(argv)
    if not (args.live or args.replay):
        sys.stderr.write("give --live and/or --replay\n")
        return 2
    operator, asker = build_operator(args.operator, args.pointer)
    holder: contextlib.AbstractContextManager[Any] = contextlib.nullcontext()
    if asker is not None:
        from poolhouse.serve.gpu import hold

        holder = hold("rbmk control room feed", wait_s=args.wait)
    box: dict[str, Runner] = {}

    def progress(res: Result) -> None:
        if args.live:
            write_atomic(args.live, feed_of(res, "live", box["runner"], args, asker.model if asker else ""))

    with holder:
        runner = Runner(scenario(args.scenario), operator, progress)
        box["runner"] = runner
        res = runner.run()
    model = asker.model if asker else ""
    if args.live:
        write_atomic(args.live, feed_of(res, "live", runner, args, model))
    if args.replay:
        write_atomic(args.replay, feed_of(res, "replay", runner, args, model))
    sys.stdout.write(f"{res.scenario}: {len(res.trace)} rows, {len(res.decisions)} decisions, model {model or 'none'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
