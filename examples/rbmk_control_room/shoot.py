"""Opens the viewer in a headless Chromium of its own and takes screenshots of named camera views.

    python examples/rbmk_control_room/shoot.py --out /tmp/shots --feed ./feed/replay.json --row 30 overview az5 rodpanel orm decision
"""

from __future__ import annotations

import argparse
import functools
import http.server
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = "/examples/rbmk_control_room/viewer/index.html"
GL_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"]


class Quiet(http.server.SimpleHTTPRequestHandler):
    """Static handler that logs nothing."""

    def log_message(self, *args: object) -> None:
        return None


@contextmanager
def serving() -> Iterator[str]:
    """Serve the repository root on a free local port; yields the origin."""
    handler = functools.partial(Quiet, directory=str(ROOT))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def open_page(pw, origin: str, feed: str, width: int = 1280, height: int = 800, touch: bool = False):
    """Launch headless Chromium, open the viewer and wait until the room is built; returns (browser, page, errors)."""
    browser = pw.chromium.launch(headless=True, args=GL_ARGS)
    ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{origin}{PAGE}?feed={feed}")
    page.wait_for_function("window.__room && window.__room.state && window.__room.state.ready", timeout=60000)
    return browser, page, errors


def view(page, name: str, row: int | None = None) -> None:
    """Move the camera to a bookmark (and the replay to a row) and let a few frames render."""
    if row is not None:
        page.evaluate("(r) => { window.__room.feed.playing = false; window.__room.feed.setIndex(r); }", row)
    page.evaluate("(n) => window.__room.goTo(n)", name)
    page.wait_for_timeout(700)


def main(argv: list[str]) -> int:
    """Take one screenshot per named view."""
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("views", nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--feed", default="./feed/replay.json")
    ap.add_argument("--row", type=int)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    ap.add_argument("--touch", action="store_true")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    with serving() as origin, sync_playwright() as pw:
        browser, page, errors = open_page(pw, origin, args.feed, args.width, args.height, args.touch)
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
