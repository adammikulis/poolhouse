"""``python -m poolhouse.train decider`` and ``poolhouse-decide train``: the command line of
`poolhouse.train.decider`."""

from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import Sequence
from pathlib import Path

from poolhouse.command import Group, flag, option
from poolhouse.decide import dataset, metrics, registry
from poolhouse.decide.cases import Case
from poolhouse.decide.guards import guard_cases
from poolhouse.decide.pins import BASES, QWEN35_2B_BASE, STRANDS_V19, Checkpoint
from poolhouse.decide.types import DecideError
from poolhouse.home import expand
from poolhouse.log import say, warn
from poolhouse.train.decider import DECIDER_TARGETS, Settings, check_inputs, split_cases, train
from poolhouse.train.lora import Lora

__all__ = ["COMMANDS", "OPTIONS", "main", "run"]

def _base(value: str) -> Checkpoint | Path:
    if value in BASES:
        return BASES[value]
    path = expand(value)
    if not (path / "config.json").is_file():
        raise DecideError(f"{value!r} is neither a pinned base ({sorted(BASES)}) nor a "
                          "directory holding a model's config.json")
    return path


def _settings(args: Namespace) -> Settings:
    """The run's settings: the reference recipe, or `Settings.smoke` with ``--smoke``, and only
    what the person gave on top of it."""
    given = {k: v for k, v in {
        "steps": args.steps, "epochs": args.epochs, "batch_size": args.batch_size,
        "grad_accum": args.grad_accum, "lr": args.lr, "max_tokens": args.max_tokens}.items()
        if v is not None}
    if args.base:
        given["base"] = _base(args.base)
    if args.rank:
        given["lora"] = Lora(args.rank, 2 * args.rank, 0.05, DECIDER_TARGETS)
    make = Settings.smoke if args.smoke else Settings
    return make(
        name=args.name, init=STRANDS_V19 if args.init == "strands" else None, seed=args.seed,
        device=args.device, download=args.download, baseline=args.baseline,
        allow_worse=args.allow_worse, replace=args.replace, wait_s=args.wait,
        floor=args.floor, allow_repo=args.allow_in_repo, **given)


def _show(s: Settings, cases: Sequence[Case], eval_cases: Sequence[Case] | None, out: Path
          ) -> list[str]:
    found = dataset.check(cases, label="training data")
    say(f"{len(cases)} cases, labels {dict(sorted(found.labels.items()))}, kinds "
        f"{dict(sorted(found.kinds.items()))}")
    warnings = check_inputs(cases, eval_cases, s, out)
    fit, cal, test = split_cases(cases, eval_cases, s)
    say(f"splits: {len(fit)} train, {len(cal)} calibration, {len(test)} test"
        + (" (the evaluation file)" if eval_cases is not None else " (whole groups)"))
    return warnings


def run(args: Namespace) -> int:
    try:
        s = _settings(args)
        registry.check_name(s.name)
        cases = guard_cases() if args.data == "guards" else dataset.load(args.data)
        eval_cases = dataset.load(args.eval) if args.eval else None
        out = expand(args.out) if args.out else registry.models_dir() / s.name
        if args.dry_run:
            for w in _show(s, cases, eval_cases, out):
                warn(w)
            say(f"dry run: would write {out}; nothing was trained or registered")
            return 0
        got = train(cases, out, s, eval_cases=eval_cases)
    except (DecideError, ValueError, OSError) as exc:
        warn(f"error: {exc}")
        return 2
    m = got.metrics
    say(f"wrote {got.out} in {got.seconds:.0f}s: test accuracy {m['accuracy']:.3f}, "
        f"Brier {m['brier']:.3f}, ECE {m['ece']:.3f}, abstain rate {m['abstain_rate']:.3f} at "
        f"{m['floor']:g}, temperature {got.temperature:.3f}")
    if got.baseline:
        b = got.baseline
        say(f"baseline ({got.baseline_name}): accuracy {b['accuracy']:.3f}, "
            f"Brier {b['brier']:.3f}, ECE {b['ece']:.3f}")
    for w in got.warnings:
        warn(w)
    say(f"registered as {s.name!r}; score it with: poolhouse-decide eval FILE --decider {s.name}")
    say(json.dumps({"data_hash": got.data_hash}))
    return 0


OPTIONS = [
    flag("--data", required=True, help="a JSONL file of labelled cases, or `guards`"),
    flag("--name", required=True, help="the name the decider is registered under"),
    flag("--eval", default="", help="a JSONL file of held-out cases to score on; refused if "
                                    "it shares a case or group with --data"),
    flag("--base", default="",
         help=f"a pinned base ({', '.join(sorted(BASES))}) or a local model directory "
              f"(default {QWEN35_2B_BASE.name}; with --smoke the 0.8B)"),
    flag("--smoke", action="store_true",
         help="the pipeline-check settings (0.8B base, 60 steps of 4 cases) instead of the "
              "reference recipe's; for tests and a first look, not for a decider you rely on"),
    flag("--baseline", default="auto",
         help="what the result must not be worse than: auto, majority, strands, none or a "
              "registered decider"),
    flag("--allow-worse", action="store_true",
         help="register even if worse than the baseline on the test cases"),
    flag("--replace", action="store_true",
         help="replace a decider already registered under --name (it is kept as NAME.prev)"),
    flag("--floor", type=float, default=metrics.FLOOR,
         help="the confidence under which an answer counts as abstained, for the abstain rate"),
    flag("--wait", type=float, default=0.0,
         help="seconds to wait for another training run to release the GPU"),
    option("out", help="where to write it (default: under the state directory, never a git "
                       "work tree)"),
    flag("--allow-in-repo", action="store_true",
         help="allow --out inside a git work tree"),
    option("dry-run"),
    flag("--epochs", type=float, default=None,
         help="passes over the training cases (default 1, as the reference recipe)"),
    flag("--steps", type=int, default=None,
         help="optimizer steps, instead of --epochs"),
    flag("--batch-size", type=int, default=None,
         help="cases per forward pass (default 8): lower it to use less memory"),
    flag("--grad-accum", type=int, default=None,
         help="forward passes per optimizer step (default 4); --batch-size times this is the "
              "cases per step"),
    flag("--lr", type=float, default=None, help="the adapter's learning rate (default 1e-4)"),
    flag("--max-tokens", type=int, default=None,
         help="refuse training cases over this many tokens, to save memory (default: the "
              "decider's one limit, 4096)"),
    flag("--seed", type=int, default=0),
    flag("--rank", type=int, default=0, help="LoRA rank (default 16)"),
    flag("--init", choices=("strands",), default=None,
         help="continue from the released checkpoint instead of a fresh adapter"),
    flag("--device", default="auto"),
    flag("--download", action="store_true", help="download the pinned base files"),
]

COMMANDS = Group(
    "python -m poolhouse.train decider",
    "Fine-tune a pointer-head decision model from labelled cases: a LoRA on a small base "
    "model and a pointer head, options shuffled on every pass, a temperature per question "
    "kind fitted on cases held apart from training and scoring. Checks the data first, holds "
    "the GPU at the Broker, registers the result unless it is worse than its baseline. "
    "Writes a directory `PointerDecider` loads, with a model card that records the data "
    "hash, the splits and the metrics.",
    allow_abbrev=False, run=run, options=OPTIONS)


main = COMMANDS.run


if __name__ == "__main__":  # pragma: no cover - the entry point is `python -m poolhouse.train decider`
    raise SystemExit(main())
