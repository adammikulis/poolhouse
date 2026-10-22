"""Measures what the 2D views and the 3D room cost a browser: process CPU time over a replay, and the overlay's own frame cost.

    python examples/rbmk_control_room/measure.py --seconds 10 [--angle metal]

Each page replays the committed feed at 5 rows/s in a headless Chromium of its own; the browser's per-process CPU time
(browser, renderer, GPU) is read through the DevTools protocol before and after. ``--angle metal`` asks Chromium for the
machine's GPU instead of the software renderer, so the GPU process time is the GPU work the page caused.
"""

from __future__ import annotations

import argparse
import json
import sys

import shoot

PAGES = {"3d": "index.html", "2d-front": "room2d.html#front", "2d-rods": "room2d.html#rodpanel", "2d-plan": "room2d.html#plan"}
ROOT = "/examples/rbmk_control_room/viewer/"


def cpu_by_type(cdp) -> dict[str, float]:
    """Total CPU seconds of each Chromium process type."""
    out: dict[str, float] = {}
    for p in cdp.send("SystemInfo.getProcessInfo")["processInfo"]:
        out[p["type"]] = out.get(p["type"], 0.0) + p["cpuTime"]
    return out


def press(page, feed: str, playing: bool) -> None:
    """Click Play or Pause so the page is in the wanted state."""
    if page.evaluate(f"{feed}.playing") != playing:
        page.click("#playpause")


def window(page, cdp, feed: str, seconds: float, playing: bool) -> dict[str, float]:
    """CPU percent per process type over ``seconds`` with the replay playing (restarted when it ends) or paused."""
    page.select_option("#speed", "5")
    press(page, feed, playing)
    page.wait_for_timeout(1500)
    before = cpu_by_type(cdp)
    for _ in range(int(seconds)):
        page.wait_for_timeout(1000)
        if playing and not page.evaluate(f"{feed}.playing"):
            page.evaluate(f"() => {feed}.setIndex(0)")
            press(page, feed, True)
    after = cpu_by_type(cdp)
    return {k: round(100 * (after.get(k, 0.0) - before.get(k, 0.0)) / seconds, 2) for k in after if k != "network.mojom.NetworkService"}


def run(pw, origin: str, name: str, seconds: float, angle: str) -> dict:
    """Open one page and report CPU percent per process type while a replay plays and while it is paused."""
    args = [f"--use-angle={angle}", "--ignore-gpu-blocklist", "--enable-webgl"] if angle else shoot.GL_ARGS
    browser = pw.chromium.launch(headless=True, args=args)
    page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
    page.goto(f"{origin}{ROOT}{PAGES[name]}")
    ready = "window.__room && window.__room.state && window.__room.state.ready" if name == "3d" else "window.__room2d && window.__room2d.ready"
    page.wait_for_function(ready, timeout=90000)
    feed = "window.__room.feed" if name == "3d" else "window.__room2d.feed"
    cdp = browser.new_browser_cdp_session()
    renderer = page.evaluate("() => { const c = document.createElement('canvas').getContext('webgl2'); const e = c && c.getExtension('WEBGL_debug_renderer_info'); return e ? c.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'none'; }")
    result = {"page": name, "seconds": seconds, "gl": renderer, "replay_cpu_percent": window(page, cdp, feed, seconds, True),
              "paused_cpu_percent": window(page, cdp, feed, seconds, False)}
    if name != "3d":
        result["overlay"] = page.evaluate("() => ({ frames: window.__room2d.stats.frames, mean_ms: window.__room2d.stats.ms / Math.max(1, window.__room2d.stats.frames), bench: window.__room2d.bench(120) })")
    browser.close()
    return result


def main(argv: list[str]) -> int:
    """Measure every page and print one JSON line each."""
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--angle", default="", help="ANGLE backend for the browser's GL, for example metal")
    ap.add_argument("pages", nargs="*", default=list(PAGES))
    args = ap.parse_args(argv)
    with shoot.serving() as origin, sync_playwright() as pw:
        for name in args.pages:
            sys.stdout.write(json.dumps(run(pw, origin, name, args.seconds, args.angle)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
