"""``poolhouse-serve warm``: record which ranges of a model a run left in the page cache, and
read them ahead."""

from __future__ import annotations

import argparse

from poolhouse.command import flag
from poolhouse.log import say, warn
from poolhouse.serve import warm
from poolhouse.serve.plan import GIB
from poolhouse.units import human_bytes

__all__ = ["OPTIONS", "cmd_warm"]

OPTIONS = [
    flag("model", help="path to the first .gguf file of the model"),
    flag("--record", action="store_true",
         help="ask the OS which pages of the expert and n-gram tensors are in the page cache "
              "now -- run it right after the server has answered some questions -- and merge "
              "them into the manifest beside the model"),
    flag("--prefault", action="store_true",
         help="ask the OS to read the manifest's ranges ahead now"),
    flag("--budget", type=float, default=16.0, metavar="GB",
         help="the most bytes --prefault asks for (default: %(default)s)"),
]


def _show(model: str) -> int:
    found = warm.read(model)
    if found is None:
        say(f"no warm manifest for {model} ({warm.path_for(model)})")
        return 0
    held = sum(int(t["resident"]) for t in found.tensors.values())  # type: ignore[call-overload]
    say(f"{warm.path_for(model)}: {found.runs} recording(s), last {found.recorded_at}, "
        f"{len(found.tensors)} tensors, {human_bytes(held)} recorded resident")
    return 0


def cmd_warm(args: argparse.Namespace) -> int:
    """``poolhouse-serve warm MODEL`` -- show, record or read ahead a model's warm manifest."""
    try:
        if args.record:
            manifest = warm.record(args.model)
            say(f"wrote {warm.write(args.model, manifest)}")
        if args.prefault:
            found = warm.read(args.model)
            if found is None:
                warn("no warm manifest to read ahead; run with --record first")
                return 1
            ranges = warm.hot_ranges(args.model, found, int(args.budget * GIB))
            say(f"asked the OS to read {human_bytes(warm.prefault_ranges(ranges))} ahead")
        if not (args.record or args.prefault):
            return _show(args.model)
    except (OSError, ValueError) as exc:
        warn(f"error: {exc}")
        return 2
    return 0

