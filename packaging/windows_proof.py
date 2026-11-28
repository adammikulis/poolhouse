"""Installs the Windows setup this checkout built and uses it as a person would, on a throwaway Windows machine.

    python packaging/windows_proof.py dist/bundle

It installs silently, reads the firewall rules, starts the app, completes first run and opens every screen, founds a
pool on the Pool screen, joins a second device to it with the code shown, installs over the running copy and checks the
pool is still there, then uninstalls. It changes the machine it runs on (programs, firewall rules, the user's state
folder), so it refuses to run outside CI. No model is downloaded or loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from smoke import every_screen, first_run

ROOT = Path(__file__).resolve().parent.parent
STARTS_S = 180.0
HOME = Path.home()
LOGS = ("traind.log", "node.log", "supervisor.log", "restart.log")


def step(what: str) -> None:
    print(f"\n== {what}", flush=True)


def run(argv: list[str], *, timeout: float = 600.0) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode:
        raise SystemExit(f"{argv} exited {done.returncode}\n{done.stdout}\n{done.stderr}")
    return done.stdout


def health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as answer:   # fixed loopback http
            return json.loads(answer.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None


def answers(port: int, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTS_S
    while time.monotonic() < deadline:
        if health(port) is not None:
            return
        if process.poll() is not None:
            raise SystemExit(f"{process.args} exited {process.returncode} before its daemon answered on {port}")
        time.sleep(1)
    raise SystemExit(f"no daemon answered on {port} within {STARTS_S:.0f}s")


def install(setup: Path, folder: Path) -> Path:
    """Run the setup silently; the app's own program in the folder it installs to."""
    run([str(setup), "/S"])
    apps = [one for one in folder.glob("*.exe") if one.name not in {"uninstall.exe", "poolhouse-headless.exe"}]
    if len(apps) != 1 or not (folder / "poolhouse-headless.exe").is_file():
        raise SystemExit(f"{folder} does not hold the app and its daemon: {[p.name for p in folder.glob('*')]}")
    return apps[0]


def firewall() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from poolhouse.lan import RULES

    for name, protocol, port in RULES:
        said = run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}", "verbose"])
        wanted = {"Profiles:": "Private", "Protocol:": protocol, "LocalPort:": str(port)}
        for field, value in wanted.items():
            found = [line.split(":", 1)[1].strip() for line in said.splitlines() if line.startswith(field)]
            if found != [value]:
                raise SystemExit(f"the rule {name!r} has {field} {found}, not {value}\n{said}")
        print(f"rule {name}: {protocol} {port}, private only")


def signed_in(page: Page, port: int, root: Path) -> None:
    """Open the page the way the app's window does: with a one-use ticket from the daemon's own launch secret."""
    record = json.loads((root / "launch" / "secret.json").read_text(encoding="utf-8"))
    answer = page.request.post(f"http://127.0.0.1:{port}/ui/launch/ticket", data="{}",
                               headers={"X-Poolhouse-UI": "1", "X-Poolhouse-Launch": record["secret"],
                                        "Content-Type": "application/json"})
    page.goto(f"http://127.0.0.1:{port}/ui/?launch_ticket={answer.json()['ticket']}")


def browser_page(browser, errors: list[str]) -> Page:
    page = browser.new_context(viewport={"width": 1400, "height": 950}).new_page()
    page.set_default_timeout(60_000)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 and r.status != 501 else None)
    return page


def to_pool(page: Page) -> None:
    page.evaluate("() => { location.hash = 'pool'; }")
    page.wait_for_selector("#pool:not([hidden])")


def done_or_why(page: Page, form: str, done: str) -> None:
    """Wait for the form's own success or failure, and fail with what the page said."""
    page.wait_for_selector(f"{form} {done}, {form} .err")
    if page.locator(f"{form} .err").count():
        raise SystemExit(f"{form} failed: {page.locator(f'{form} .err').first.inner_text()}")


def found_and_join(browser, errors: list[str], temporary: Path, headless: Path) -> None:
    step("first run and every screen, in the installed app's daemon")
    first = browser_page(browser, errors)
    signed_in(first, 8770, HOME / ".poolhouse" / "traind")
    first_run(first, "windows-first")
    every_screen(first)
    first.screenshot(path=str(ROOT / "dist" / "windows-first-run.png"), full_page=True)

    step("founding a pool on the Pool screen")
    to_pool(first)
    first.fill("#pool-found-name", "proof pool")
    first.click("#pool-found-form button")
    done_or_why(first, "#pool-found-form", ".ok")
    to_pool(first)
    first.click("#pool-add-form button")
    done_or_why(first, "#pool-add-form", "#pool-join-code")
    code = first.inner_text("#pool-join-code").strip()
    first.screenshot(path=str(ROOT / "dist" / "windows-founded.png"), full_page=True)

    step("a second device joins with that code")
    home = temporary / "second"
    environment = {**os.environ, "POOLHOUSE_HOME": str(home)}
    other = subprocess.Popen([str(headless), "--port", "8771", "--root", str(home / "traind"), "--no-browser"], env=environment)
    answers(8771, other)
    second = browser_page(browser, errors)
    signed_in(second, 8771, home / "traind")
    first_run(second, "windows-second")
    to_pool(second)
    second.fill("#pool-join-host", "127.0.0.1")
    second.fill("#pool-join-code-in", code)
    second.click("#pool-join-form button")
    done_or_why(second, "#pool-join-form", ".ok")
    to_pool(first)
    first.reload()
    to_pool(first)
    first.wait_for_selector("#pool >> text=windows-second")
    first.screenshot(path=str(ROOT / "dist" / "windows-joined.png"), full_page=True)


def uninstall(folder: Path, app: Path) -> None:
    step("uninstall")
    subprocess.run(["taskkill", "/F", "/T", "/IM", app.name], capture_output=True, check=False)
    run([str(folder / "uninstall.exe"), "/S"])
    deadline = time.monotonic() + 120
    while (folder / "poolhouse-headless.exe").exists() and time.monotonic() < deadline:
        time.sleep(1)
    if (folder / "poolhouse-headless.exe").exists() or not (HOME / ".poolhouse").is_dir():
        raise SystemExit("uninstalling left the daemon, or removed the user's state")


def logs(temporary: Path) -> None:
    for folder in (HOME / ".poolhouse", temporary):
        for found in (path for name in LOGS for path in folder.rglob(name)):
            print(f"\n---- {found}\n{found.read_text(encoding='utf-8', errors='replace')[-6000:]}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    args = ap.parse_args()
    if sys.platform != "win32" or os.environ.get("CI") != "true":
        raise SystemExit("this installs programs and firewall rules: it runs only on a CI Windows machine")
    setup, = args.bundle.resolve().glob("*-setup.exe")
    folder = Path(os.environ["LOCALAPPDATA"]) / "Poolhouse"
    temporary = Path(tempfile.mkdtemp(prefix="poolhouse-proof-"))
    errors: list[str] = []
    try:
        step(f"silent install of {setup.name}")
        app = install(setup, folder)
        step("the firewall rules the installer added")
        firewall()
        step("the app starts and its daemon answers")
        window = subprocess.Popen([str(app)])
        answers(8770, window)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            found_and_join(browser, errors, temporary, folder / "poolhouse-headless.exe")

            step("installing over the running app keeps the pool")
            app = install(setup, folder)
            subprocess.run(["taskkill", "/F", "/T", "/IM", app.name], capture_output=True, check=False)
            window = subprocess.Popen([str(app)])
            answers(8770, window)
            again = browser_page(browser, errors)
            signed_in(again, 8770, HOME / ".poolhouse" / "traind")
            to_pool(again)
            again.wait_for_selector("#pool >> text=proof pool")
            again.wait_for_selector("#pool >> text=windows-second")
            again.screenshot(path=str(ROOT / "dist" / "windows-installed-over.png"), full_page=True)
            browser.close()

        uninstall(folder, app)
        if errors:
            raise SystemExit("the pages reported errors:\n" + "\n".join(errors))
        print("\nthe Windows app installed, ran, founded and joined a pool, was installed over and uninstalled")
        return 0
    except BaseException:
        for line in errors:
            print(line, flush=True)
        with suppress(OSError):
            logs(temporary)
        raise
    finally:
        for name in ("poolhouse-app.exe", "Poolhouse.exe", "poolhouse-headless.exe", "poolhouse-node.exe"):
            subprocess.run(["taskkill", "/F", "/T", "/IM", name], capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
