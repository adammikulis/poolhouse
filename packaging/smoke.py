"""Drives a running bundle's page in headless Chromium: first run, every tab."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

SCREENS = ["chat", "pool", "fit", "models", "settings"]


def launch_url(page: Page, base: str) -> str:
    """The page URL carrying a one-use sign-in ticket.

    A page opened by hand is refused ("Open from its own window"); the daemon's own window and
    the owner's terminal ask for a ticket with the secret the daemon wrote under its root.
    """
    record = json.loads((Path.home() / ".poolhouse" / "traind" / "launch" / "secret.json")
                        .read_text(encoding="utf-8"))
    answer = page.request.post(
        f"{base}/ui/launch/ticket", data="{}",
        headers={"X-Poolhouse-UI": "1", "X-Poolhouse-Launch": record["secret"],
                 "Content-Type": "application/json"})
    ticket = answer.json()["ticket"]
    return f"{base}/ui/?launch_ticket={ticket}"


def press(page: Page, label: str) -> None:
    """Presses the wizard button with exactly this label."""
    page.click(f"#first-run button:text-is('{label}')")


def first_run(page: Page, name: str) -> None:
    """Complete setup through explicit model deferral; smoke never starts compute."""
    page.get_by_role("heading", name="Welcome to Poolhouse", exact=True).wait_for()
    page.fill("#setup-device-name", name)
    press(page, "Continue")
    page.get_by_role("heading", name="Make this device ready", exact=True).wait_for()
    press(page, "Set up models later")
    page.wait_for_selector("#app:not([hidden])")


def every_screen(page: Page) -> None:
    """Goes to each screen by its route and waits for it to show."""
    for route in SCREENS:
        page.evaluate("route => { location.hash = route; }", route)
        page.wait_for_selector(f"#{route}:not([hidden])")
        page.wait_for_load_state("networkidle")


def main() -> int:
    """Returns 0 when every screen drew with no error in the page."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base", help="the daemon, e.g. http://127.0.0.1:8899")
    ap.add_argument("--screenshot", help="where to save the last screen")
    args = ap.parse_args()

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        page.set_default_timeout(20_000)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}")
                if m.type == "error" and "Failed to load resource" not in m.text else None)
        # 501 is the daemon saying a feature is not in this build, which the page draws
        page.on("response", lambda r: errors.append(f"{r.status} {r.url}")
                if r.status >= 400 and r.status != 501 else None)
        try:
            page.goto(launch_url(page, args.base))
            first_run(page, "ci-runner")
            every_screen(page)
        finally:
            if args.screenshot:
                page.screenshot(path=args.screenshot, full_page=True)
            browser.close()
    for line in errors:
        print(line, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
