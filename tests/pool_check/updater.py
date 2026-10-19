"""Run on a Linux device: make this device follow a local origin that has a newer commit, then run the real updater once.

    python updater.py --state DIR --binary PATH --listen ADDR --sync-ms N --target-id ID --target-seq N

The git side is real: a bare origin and a checkout of its development branch in the device's own folder, one commit behind. The
updater's own `fast_forward` fetches and merges it. Only the build is replaced (nothing is compiled here): the replacement selects the
new commit and moves the node onto the pool's newest build identity, which is what `runtime ensure`'s node swap does. Prints the
updater's result as JSON.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import wire

from poolhouse import build_update, node_health, runtime, runtime_store
from poolhouse.build_update import Steps

BRANCH = "dev"


def git(*argv: str, cwd: Path) -> str:
    done = subprocess.run(["git", "-c", "user.name=pool-check", "-c", "user.email=pool-check@example.invalid", *argv], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def make_repos(root: Path) -> tuple[Path, str, str]:
    """A bare origin with two commits on BRANCH and a checkout with only the first; the checkout, the old and the new commit."""
    origin, checkout, other = root / "origin.git", root / "checkout", root / "other"
    root.mkdir(parents=True, exist_ok=True)
    git("init", "--bare", "-b", BRANCH, str(origin), cwd=root)
    git("clone", str(origin), str(other), cwd=root)
    git("checkout", "-B", BRANCH, cwd=other)
    (other / "build.txt").write_text("1\n")
    git("add", "build.txt", cwd=other)
    git("commit", "-m", "the build this device runs", cwd=other)
    git("push", "origin", BRANCH, cwd=other)
    old = git("rev-parse", "HEAD", cwd=other)
    git("clone", "-b", BRANCH, str(origin), str(checkout), cwd=root)
    (other / "build.txt").write_text("2\n")
    git("commit", "-am", "the pool's newer build", cwd=other)
    git("push", "origin", BRANCH, cwd=other)
    return checkout, old, git("rev-parse", "HEAD", cwd=other)


def wait_for_pool_builds(state: Path, seconds: float = 60.0, ask: Callable[..., dict] = node_health.call) -> dict:
    """Wait until the node on ``state`` knows a member on a newer build; its `pool_status`."""
    end, seen = time.monotonic() + seconds, "no node answered"
    while time.monotonic() < end:
        try:
            if build_update.newest(pool := ask(state, "pool_status")) is not None:
                return pool
            seen = "the node knows of no member on a newer build yet"
        except (OSError, ValueError) as exc:
            seen = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
    raise RuntimeError(f"the node knows of no member on a newer build: {seen}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--binary", required=True)
    ap.add_argument("--listen", required=True)
    ap.add_argument("--sync-ms", required=True)
    ap.add_argument("--target-id", required=True)
    ap.add_argument("--target-seq", required=True)
    args = ap.parse_args(argv)
    state = Path(args.state)
    checkout, old, new = make_repos(state.parent / "update")
    runtime_store.write_state({"checkout": str(checkout)})
    runtime.directory().mkdir(parents=True, exist_ok=True)
    selected = runtime.directory() / "selected.json"
    selected.write_text(json.dumps({"commit": old}))
    wait_for_pool_builds(state)

    def ensure(_checkout: Path) -> tuple[int, str]:
        selected.write_text(json.dumps({"commit": new}))
        wire.node_launch.stop_node(state)
        wire.start(state, Path(args.binary), ["--listen", args.listen, "--sync-ms", args.sync_ms, "--build-id", args.target_id, "--build-seq", args.target_seq])
        return 0, f"node moved onto {args.target_id[:12]}"

    steps = Steps(fetch=build_update.fast_forward, ensure=ensure, rollback=lambda commit: (0, ""), release=lambda: {"ok": False, "error": "release path not used"},
                  selected=lambda: json.loads(selected.read_text())["commit"])
    result = build_update.update_once(state, steps=steps)
    sys.stdout.write(json.dumps({"action": result.action, "detail": result.detail, "head": git("rev-parse", "HEAD", cwd=checkout), "new": new}) + "\n")
    return 0 if result.action == "updated" and git("rev-parse", "HEAD", cwd=checkout) == new else 1


if __name__ == "__main__":
    raise SystemExit(main())
