"""Measures what the room's 2D views and the 3D room cost a browser: process CPU time over a replay, and the overlay's own frame cost.

    python -m examples.rbmk.room.measure --recordings examples/rbmk/recordings --recording FILE --seconds 10 [--angle metal]

Each view plays the recording at 5 ticks/s in a headless Chromium of its own; the browser's per-process CPU time (browser,
renderer, GPU) is read through the DevTools protocol before and after. ``--angle metal`` asks Chromium for the machine's GPU
instead of the software renderer, so the GPU process time is the GPU work the view caused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import shoot

VIEWS = {"3d": "3d", "2d-front": "front", "2d-rods": "rodpanel", "2d-plan": "plan"}


def cpu_by_type(cdp) -> dict[str, float]:
    """Total CPU seconds of each Chromium process type."""
    out: dict[str, float] = {}
    for p in cdp.send("SystemInfo.getProcessInfo")["processInfo"]:
        out[p["type"]] = out.get(p["type"], 0.0) + p["cpuTime"]
    return out


def press(page, playing: bool) -> None:
    """Start or stop the replay."""
    page.evaluate("(p) => { window.__room.feed.playing = p; }", playing)


def window(page, cdp, seconds: float, playing: bool) -> dict[str, float]:
    """CPU percent per process type over ``seconds`` with the replay playing (restarted when it ends) or paused."""
    page.evaluate("() => { window.RB.state.speed = 5; }")
    press(page, playing)
    page.wait_for_timeout(1500)
    before = cpu_by_type(cdp)
    for _ in range(int(seconds)):
        page.wait_for_timeout(1000)
        if playing and not page.evaluate("window.__room.feed.playing"):
            page.evaluate("() => window.__room.feed.setIndex(0)")
            press(page, True)
    after = cpu_by_type(cdp)
    return {k: round(100 * (after.get(k, 0.0) - before.get(k, 0.0)) / seconds, 2) for k in after if k != "network.mojom.NetworkService"}


def run(pw, origin: str, name: str, recording: str, seconds: float, angle: str) -> dict:
    """Open the app on a recording in one view and report CPU percent per process type while it plays and while it is paused."""
    args = [f"--use-angle={angle}", "--ignore-gpu-blocklist", "--enable-webgl"] if angle else shoot.GL_ARGS
    browser = pw.chromium.launch(headless=True, args=args)
    page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
    page.goto(f"{origin}/" if name == "3d" else f"{origin}/#{VIEWS[name]}")
    page.wait_for_function("window.__room && window.__room.ready && window.RB.state.info", timeout=90000)
    shoot.open_recording(page, recording)
    cdp = browser.new_browser_cdp_session()
    renderer = page.evaluate("() => { const c = document.createElement('canvas').getContext('webgl2'); const e = c && c.getExtension('WEBGL_debug_renderer_info'); return e ? c.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'none'; }")
    result = {"view": name, "seconds": seconds, "gl": renderer, "replay_cpu_percent": window(page, cdp, seconds, True),
              "paused_cpu_percent": window(page, cdp, seconds, False)}
    if name != "3d":
        result["overlay"] = page.evaluate("() => ({ frames: window.__room2d.stats.frames, mean_ms: window.__room2d.stats.ms / Math.max(1, window.__room2d.stats.frames), bench: window.__room2d.bench(120) })")
    browser.close()
    return result


def main(argv: list[str]) -> int:
    """Measure every view and print one JSON line each."""
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--angle", default="", help="ANGLE backend for the browser's GL, for example metal")
    ap.add_argument("--recordings", type=Path, required=True)
    ap.add_argument("--recording", required=True)
    ap.add_argument("views", nargs="*", default=list(VIEWS))
    args = ap.parse_args(argv)
    with shoot.serving(args.recordings) as origin, sync_playwright() as pw:
        for name in args.views:
            sys.stdout.write(json.dumps(run(pw, origin, name, args.recording, args.seconds, args.angle)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
