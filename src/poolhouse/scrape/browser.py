"""A browser that remembers who you are.

Anything worth reading is usually behind a login, and a login worth having survives a reboot.
So the browser runs against a profile directory on disk rather than a fresh sandbox: sign in
once, by hand, and every run after that is already signed in.

That also sets the etiquette. A profile is a real session — the account looks online while the
browser is open, and reading a site fast enough to notice is how a session stops working. Runs
are paced and can be held to hours when somebody is meant to be awake.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import random
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from poolhouse import home, provision, safelaunch
from poolhouse.lock import only_one
from poolhouse.log import warn
from poolhouse.net import browserguard

FETCH_S = 1800.0
"""Seconds the browser's download, and a wait for another process's, may take."""


class BrowserUnavailable(RuntimeError):
    """Playwright or a browser is not here and could not be installed."""


@dataclass
class Window:
    """How the browser presents itself."""

    profile: Path
    width: int = 1440
    height: int = 900
    headless: bool = True
    channel: str = "chrome"
    timeout_ms: int = 60_000
    position: tuple[int, int] | None = None
    guarded: bool = False
    """Only public addresses may be requested and nothing is downloaded: for pages a model chose."""
    own_pages: bool = False
    """Nothing but the project's own pages is opened, so Chromium may run without its sandbox, which cannot start inside
    another one. A guarded window keeps its sandbox whatever this says."""

    def args(self) -> list[str]:
        """The Chromium flags this window wants."""
        at = self.position if self.position is not None else placed()
        return [f"--window-position={at[0]},{at[1]}"] if at else []



def placed() -> tuple[int, int] | None:
    """Where a headed window opens, from ``POOLHOUSE_WINDOW_POSITION`` as ``X,Y``."""
    said = os.environ.get("POOLHOUSE_WINDOW_POSITION", "").strip()
    if not said:
        return None
    try:
        x, y = (int(part) for part in said.split(",", 1))
    except ValueError:
        return None
    return x, y


MISSING = ("Executable doesn't exist", "is not found at")
REDIRECT = re.compile(r"(?:npm_(?:package_)?config_)?playwright_(?:\w+_)?download_host|node_tls_reject_unauthorized",
                      re.IGNORECASE)
"""Environment settings that would fetch the browser from somewhere else, or without checking who answers, under each
name Playwright reads them by."""
"""What Playwright says when the browser asked for is not on this computer."""
EXECUTABLE = ("from playwright.sync_api import sync_playwright\n"
              "with sync_playwright() as p:\n print(p.chromium.executable_path)")


def bundled_chromium(run: Any = subprocess.run) -> str:
    """The Chromium of this Playwright release kept under the state root (``tools/browsers``), downloaded by Playwright's own
    installer the first time; one process downloads and the others use it. Which Chromium that is comes from the revision
    table inside the installed Playwright release, itself held to the project's hashed lock; Poolhouse holds no digest of the
    browser's own files. Playwright's settings for another download host, and for skipping certificate checks, are not
    passed on. Raises `BrowserUnavailable` with why not."""
    kept = home.state("tools", "browsers")
    environment = {name: value for name, value in os.environ.items() if not REDIRECT.fullmatch(name)}
    environment["PLAYWRIGHT_BROWSERS_PATH"] = str(kept)

    def playwright(*words: str, **how: Any) -> Any:
        """This interpreter on ``words``, started from an empty directory it takes nothing from."""
        with safelaunch.empty_directory() as nowhere:
            return run([safelaunch.absolute(sys.executable), "-P", *words], env=environment, cwd=nowhere, **how)

    def executable() -> str:
        said = playwright("-c", EXECUTABLE, capture_output=True, text=True, timeout=60)
        found = said.stdout.strip() if said.returncode == 0 else ""
        return found if found and Path(found).is_file() else ""

    try:
        if found := executable():
            return found
        if refused := provision.download_refusal():
            raise BrowserUnavailable(f"no browser is on this computer, and none was downloaded: {refused}")
        kept.mkdir(parents=True, exist_ok=True)
        with only_one(kept / "fetch.lock", wait=True, timeout=FETCH_S, announce=warn, note="downloading Chromium"):
            if found := executable():
                return found
            warn("a browser is needed for the first time: downloading Playwright's Chromium once")
            # its progress goes to standard error, so a program reading this one's output is not fed it
            done = playwright("-m", "playwright", "install", "chromium", timeout=FETCH_S, stdout=2)
            if done.returncode:
                raise BrowserUnavailable(f"Playwright's installer exited {done.returncode}; its output is above")
        if found := executable():
            return found
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserUnavailable(f"no browser is on this computer, and none could be downloaded: {exc}") from exc
    raise BrowserUnavailable("Playwright's installer finished and left no Chromium")


def _launch_errors() -> tuple[type[BaseException], ...]:
    """What a browser launch raises: Playwright's own error where Playwright is here, and RuntimeError."""
    try:
        return (importlib.import_module("playwright.sync_api").Error, RuntimeError)
    except ImportError:
        return (RuntimeError,)


def _context(play: Any, profile: Path, window: Window) -> Any:
    """The persistent context of ``window``: the browser it names, else the bundled Chromium where that one is not here."""
    options = {"headless": window.headless, "args": window.args(), "accept_downloads": not window.guarded,
               "viewport": {"width": window.width, "height": window.height},
               "chromium_sandbox": window.guarded or not window.own_pages,
               **({"service_workers": "block"} if window.guarded else {})}   # a service worker's requests pass no route
    try:
        return play.chromium.launch_persistent_context(str(profile), channel=window.channel, **options)
    except _launch_errors() as exc:
        if not any(said in str(exc) for said in MISSING):
            raise
    return play.chromium.launch_persistent_context(str(profile), executable_path=bundled_chromium(), **options)


@contextlib.contextmanager
def _paged(play: Any, window: Window) -> Iterator[Any]:
    """A page on that Playwright, over a profile directory that persists."""
    profile = Path(window.profile).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    context = _context(play, profile, window)
    if window.guarded:
        browserguard.install(context)
    context.set_default_timeout(window.timeout_ms)
    # a guarded window never uses a page that was open before the guard was installed: that one's document lacks it
    older = list(context.pages) if window.guarded else []
    page = context.new_page() if older or not context.pages else context.pages[0]
    for one in older:
        one.close()
    try:
        yield page
    finally:
        with contextlib.suppress(Exception):
            context.close()


@contextlib.contextmanager
def browser(window: Window, play: Any = None) -> Iterator[Any]:
    """A page, on a profile that persists. Closed when the block ends.

    ``play`` is a Playwright already open in this thread; Playwright's sync API allows
    only one per thread, so a caller holding one hands it over instead of opening a
    second. Without one, a Playwright is opened for the block.
    """
    if play is not None:
        with _paged(play, window) as page:
            yield page
        return
    try:
        sync_playwright = provision.module("playwright.sync_api", "scrape").sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on what is installed
        raise BrowserUnavailable(str(exc)) from exc
    with sync_playwright() as opened, _paged(opened, window) as page:
        yield page


def sign_in(window: Window, url: str, *, done: str, patience_s: float = 600.0) -> bool:
    """Open a window and wait while the site's own sign-in page is completed in it.

    Headed on purpose: the site asks for its own login, and doing it invisibly is how an
    hour goes missing. Returns whether ``done`` — a selector that only exists once signed in —
    turned up before patience ran out. Nothing is read from a terminal, so ``done`` is required
    and an empty one is refused at once.
    """
    if not done:
        raise ValueError("sign_in needs `done`, the selector that only exists once signed in; nothing is read from a terminal")
    headed = Window(**{**window.__dict__, "headless": False})
    with browser(headed) as page:
        page.goto(url, wait_until="domcontentloaded")
        deadline = time.monotonic() + patience_s
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                if page.query_selector(done):
                    return True
            time.sleep(1.0)
    return False


def signed_in(page: Any, *, done: str) -> bool:
    """Whether the page shows the thing only a signed-in session shows."""
    with contextlib.suppress(Exception):
        return page.query_selector(done) is not None
    return False


def within_hours(first: int, last: int, *, now: datetime | None = None) -> bool:
    """Whether the clock is inside the hours a run is allowed to be visible.

    A scraper on a real account is a person appearing online. Reading a workspace at four in
    the morning is not invisible; it is conspicuous.
    """
    hour = (now or datetime.now()).hour
    return first <= hour <= last if first <= last else (hour >= first or hour <= last)


def pace(least_s: float = 0.0, most_s: float = 0.0) -> float:
    """Wait a while, unevenly. Returns the seconds spent.

    Evenly spaced requests are a signature. The spread matters more than the length.
    """
    if most_s <= 0:
        return 0.0
    spent = random.uniform(max(0.0, least_s), most_s)  # noqa: S311 - a sample, not a secret
    time.sleep(spent)
    return spent
