"""Opens the app in a headless Chromium of its own and takes screenshots of named camera views of the room.

    python -m examples.rbmk.room.shoot --out DIR --recordings DIR --recording NAME.json --row 30 overview az5 rodpanel orm decision

``--recording`` opens that file of the recordings folder as the run the room shows (a recording of a real model run);
``2d-<view>`` names a 2D view (2d-plan, 2d-front, 2d-dials ...); ``--drawer NAME`` opens one of the tool drawers (run, log, charts, core, readouts, whatif, history, dist, compare) first.
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..web import server
from ..web.hub import Hub

GL_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"]


@contextmanager
def serving(folder: Path, wait_s: float = 0.0) -> Iterator[str]:
    """Serve the app on a free local port with ``folder`` as its recordings folder; yields the origin."""
    srv = server.serve(Hub(folder, wait_s), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def open_page(pw, origin: str, recording: str | None = None, width: int = 1280, height: int = 800, touch: bool = False):
    """Launch headless Chromium, open the app, optionally open a recording and wait until the room is built; returns (browser, page, errors)."""
    browser = pw.chromium.launch(headless=True, args=GL_ARGS)
    ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{origin}/")
    page.wait_for_function("window.__room && window.__room.ready && window.RB.state.info", timeout=60000)
    if recording:
        open_recording(page, recording)
    return browser, page, errors


def open_recording(page, name: str) -> None:
    """Open a recording through the app's own recorded-run path and wait until the room shows it."""
    page.evaluate("(n) => { window.RB.setMode('recorded'); return window.RB.openRecording(n); }", name)
    page.wait_for_function("window.__room.feed.status === 'ok'", timeout=30000)


def view(page, name: str, row: int | None = None) -> None:
    """Show a view and let a few frames render: a 3D camera bookmark, or ``2d-<view>`` for a 2D view; ``row`` moves the playhead first."""
    if row is not None:
        page.evaluate("(r) => { window.__room.feed.playing = false; window.__room.feed.setIndex(r); }", row)
    if name.startswith("2d-"):
        page.evaluate("(n) => window.__room.setMode('2d', n)", name[3:])
        page.wait_for_function("(n) => window.__room.ready && window.__room2d.shown === n", arg=name[3:], timeout=30000)
        page.wait_for_function("!window.__room2d.loop.running", timeout=10000)
        return
    page.evaluate("() => window.__room.setMode('3d')")
    page.wait_for_function("window.__room.ready && window.__room.state.ready && window.__room.mode === '3d'", timeout=60000)
    page.evaluate("(n) => window.__room.goTo(n)", name)
    page.wait_for_timeout(700)


def main(argv: list[str]) -> int:
    """Take one screenshot per named view."""
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("views", nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--recordings", type=Path, required=True)
    ap.add_argument("--recording")
    ap.add_argument("--drawer")
    ap.add_argument("--row", type=int)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--touch", action="store_true")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    with serving(args.recordings) as origin, sync_playwright() as pw:
        browser, page, errors = open_page(pw, origin, args.recording, args.width, args.height, args.touch)
        page.evaluate("(d) => (d ? window.RB.openDrawer(d) : window.RB.closeDrawer())", args.drawer)
        for name in args.views:
            view(page, name, args.row)
            path = args.out / f"{name}{'-' + str(args.row) if args.row is not None else ''}.png"
            page.screenshot(path=str(path))
            sys.stdout.write(f"{path}\n")
        browser.close()
    for e in errors:
        sys.stdout.write(f"console error: {e}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
