"""Checks the installed Poolhouse app on a connected phone, and touches nothing else on that phone.

    python3 app/android/phone_check.py --serial SERIAL --apk app.apk --out FOLDER [step ...]

Steps: ``install``, ``home``, ``models``, ``download``, ``chat``, ``reinstall``, ``crashes``. With none named: install, home, models,
crashes. ``download`` and ``chat`` need the model on the phone; ``reinstall`` removes the app and installs it again to see that the
model stayed.

The phone holds a person's private data, so :class:`Phone` has no way to do more than this:

- install, remove, open and read the log of the Poolhouse app's own debug build;
- read the screen's text, tap, type and press back only while that app is the focused window;
- take a screenshot only while it is focused and nothing of the system (lock screen, notification, shade) is drawn over it, checked
  before and after, with the status bar cut off.

It never wakes or unlocks the phone and changes no setting: where a person has to unlock, allow or tap, it says so once and waits.
"""

from __future__ import annotations

import argparse
import re
import shlex
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

PACKAGE = "app.poolhouse.phone"
ACTIVITY = f"{PACKAGE}/.MainActivity"
#: The share of the screen's height cut from the top of a screenshot: the status bar shows other apps' notification icons.
STATUS_BAR = 0.06
#: Seconds the app may be out of front, as while a screen opens, before the person is asked to bring it back.
GRACE = 15
QUESTION = "What is 17 times 23? Answer with the number only."
ANSWER = "391"
MODEL = "Gemma 4 E2B"
#: How every model's title begins: a model's own button lies between its title and the next one.
MODELS = "Gemma 4 "
STEPS = ("install", "home", "models", "download", "chat", "reinstall", "crashes")
DEFAULT_STEPS = ("install", "home", "models", "crashes")


class Failed(Exception):
    """A check that did not hold."""


@dataclass(frozen=True)
class Node:
    text: str
    description: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def centre(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    @property
    def words(self) -> str:
        return self.text or self.description


def focused_package(window: str) -> str:
    """The package of the focused window in ``dumpsys window``; empty for a system window such as the lock screen or the shade."""
    found = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/", window)
    return found.group(1) if found else ""


def covered(window: str) -> bool:
    """Whether the system may be drawing over the app: locked, the shade or a heads-up notification shown, or not known."""
    if "isKeyguardShowing=true" in window or "mDreamingLockscreen=true" in window:
        return True
    shade = re.search(r"Window\{\S+ \S+ NotificationShade\}:(.*?)(?=\n\s*Window #|\Z)", window, re.S)
    return shade is None or "isVisible=false" not in shade.group(1)


def png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\0" + rgba[row * width * 4:(row + 1) * width * 4] for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")


def without_status_bar(raw: bytes) -> bytes:
    """``screencap``'s raw picture as a PNG with its top rows left out."""
    if len(raw) < 16:
        raise Failed("The phone gave no picture.")
    width, height = struct.unpack_from("<II", raw)
    size = width * height * 4
    # The header is 12 bytes before Android 9 and 16 since; the pixels are the rest.
    pixels = raw[len(raw) - size:]
    if width <= 0 or height <= 0 or len(raw) - size not in (12, 16):
        raise Failed("The phone's picture is not in the expected form.")
    cut = int(height * STATUS_BAR)
    return png(width, height - cut, pixels[cut * width * 4:])


class Phone:
    def __init__(self, serial: str, adb: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        if not serial:
            raise Failed("Name the phone with --serial.")
        self.serial, self.adb, self.run = serial, adb, run

    def _adb(self, *args: str, timeout: float = 60) -> bytes:
        done = self.run([str(self.adb), "-s", self.serial, *args], capture_output=True, timeout=timeout, check=False)
        if done.returncode != 0:
            raise Failed(f"adb {args[0]} failed: {done.stderr.decode(errors='replace').strip()[:300]}")
        return done.stdout

    def _shell(self, command: str) -> str:
        return self._adb("shell", command).decode(errors="replace")

    def packages(self) -> list[str]:
        """The installed packages whose name begins like the app's; no other package is listed."""
        listed = self._shell("pm list packages --user 0 app.poolhouse")
        return sorted(line.removeprefix("package:").strip() for line in listed.splitlines() if line.startswith("package:app.poolhouse"))

    def install(self, apk: Path) -> None:
        self._adb("install", "-r", str(apk), timeout=600)

    def uninstall(self) -> None:
        self._adb("uninstall", PACKAGE)

    def launch(self) -> None:
        self._shell(f"am start -n {ACTIVITY}")

    def _window(self) -> str:
        return self._shell("dumpsys window")

    def in_front(self) -> bool:
        window = self._window()
        return focused_package(window) == PACKAGE and "isKeyguardShowing=true" not in window

    def uncovered(self) -> bool:
        window = self._window()
        return focused_package(window) == PACKAGE and not covered(window)

    def nodes(self) -> list[Node] | None:
        """The app's own text on the screen: None when it is not in front, empty when it is but cannot be read just now."""
        if not self.in_front():
            return None
        said = self._adb("exec-out", "uiautomator", "dump", "/dev/tty").decode(errors="replace")
        start, end = said.find("<?xml"), said.rfind("</hierarchy>")
        if start < 0 or end < 0:
            return []
        found = []
        for element in ElementTree.fromstring(said[start:end + len("</hierarchy>")]).iter("node"):
            if element.get("package") != PACKAGE:
                continue
            left, top, right, bottom = map(int, re.findall(r"-?\d+", element.get("bounds", "[0,0][0,0]")))
            found.append(Node(element.get("text", ""), element.get("content-desc", ""), left, top, right, bottom))
        return found

    def tap(self, node: Node) -> bool:
        if not self.uncovered():
            return False
        x, y = node.centre
        self._shell(f"input tap {x} {y}")
        return True

    def back(self) -> bool:
        if not self.uncovered():
            return False
        self._shell("input keyevent KEYCODE_BACK")
        return True

    def type_text(self, text: str) -> bool:
        if not self.uncovered():
            return False
        self._shell(f"input text {shlex.quote(text.replace(' ', '%s'))}")
        return True

    def screenshot(self, path: Path) -> bool:
        if not self.uncovered():
            return False
        raw = self._adb("exec-out", "screencap")
        if not self.uncovered():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(without_status_bar(raw))
        return True

    def pid(self) -> str:
        done = self.run([str(self.adb), "-s", self.serial, "shell", f"pidof {PACKAGE}"], capture_output=True, timeout=60, check=False)
        return done.stdout.decode().strip().split(" ")[0] if done.returncode == 0 else ""

    def log(self, pid: str) -> str:
        if not pid.isdigit():
            raise Failed("No process of the app to read the log of.")
        return self._adb("logcat", "-d", f"--pid={pid}").decode(errors="replace")


def pick(nodes: list[Node], words: str) -> Node | None:
    """The node saying exactly ``words`` (the lowest one: a tab sits under a title of the same name), else one containing them."""
    exact = [n for n in nodes if n.words == words]
    return max(exact, key=lambda n: n.top) if exact else next((n for n in nodes if words in n.words), None)


def printed(line: str) -> None:
    """A line of the report, shown as it happens even where the output is piped."""
    print(line, flush=True)


class Check:
    def __init__(self, phone: Phone, apk: Path, out: Path, patience: float = 900, pause: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, say: Callable[[str], None] | None = None):
        self.phone, self.apk, self.out, self.patience = phone, apk, out, patience
        self.pause, self.clock, self.say = pause, clock, say or printed
        self.pids: set[str] = set()
        self.away: float | None = None
        self.told = False

    def report(self, line: str) -> None:
        self.say(line)
        self.out.mkdir(parents=True, exist_ok=True)
        with (self.out / "report.txt").open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def wait(self, what: str, found: Callable[[list[Node]], Node | bool | None], patience: float | None = None) -> Node | bool:
        """Reads the app's screen until ``found`` answers; says once when the person has to bring the app back."""
        deadline = self.clock() + (self.patience if patience is None else patience)
        while True:
            pid = self.phone.pid()
            if pid:
                self.pids.add(pid)
            nodes = self.phone.nodes()
            if nodes is None:
                self.away = self.clock() if self.away is None else self.away
                if not self.told and self.clock() - self.away >= GRACE:
                    self.report("WAITING: unlock the phone and open Poolhouse (answer anything it asks).")
                    self.told = True
            else:
                self.away, self.told = None, False
                answer = found(nodes)
                if answer:
                    return answer
            if self.clock() >= deadline:
                raise Failed(f"Not seen in time: {what}")
            self.pause(2)

    def see(self, words: str, patience: float | None = None) -> Node:
        return self.wait(words, lambda nodes: pick(nodes, words), patience)

    def press(self, words: str) -> None:
        for _ in range(5):
            if self.phone.tap(self.see(words)):
                return
            self.pause(2)
        raise Failed(f"Something stays over the app; not tapped: {words}")

    def picture(self, name: str) -> None:
        if self.phone.screenshot(self.out / f"{name}.png"):
            self.report(f"picture: {name}.png")
        else:
            self.report(f"picture skipped ({name}): the app was not alone on the screen")

    def home(self) -> None:
        """Back to the first screen, by the back key only."""
        for _ in range(6):
            nodes = self.wait("the app", lambda found: found or None)
            tab = pick(nodes, "Chats")
            if tab and any(n.words == "Inbox" for n in nodes):
                self.phone.tap(tab)
                return
            self.phone.back()
            self.pause(1)
        raise Failed("The first screen was not reached.")

    # The steps.

    def install(self) -> None:
        before = self.phone.packages()
        self.phone.install(self.apk)
        after = self.phone.packages()
        if PACKAGE not in after:
            raise Failed("The app is not installed after installing it.")
        others = [name for name in after if name != PACKAGE]
        self.report(f"installed {PACKAGE}; before: {before or 'none'}; other Poolhouse packages: {others or 'none'}")
        if others:
            raise Failed(f"The phone has more than the one app: {others}")

    def start(self) -> None:
        self.phone.launch()
        self.wait("the app", lambda nodes: nodes or None)

    def check_home(self) -> None:
        self.start()
        self.home()
        self.see("Chats")
        nodes = self.wait("the first screen", lambda found: found or None)
        if any("Scan the code" in n.words for n in nodes):
            raise Failed("The app opened on the linking screen.")
        self.picture("01-chats")
        for number, tab in enumerate(("Pool", "Work", "Inbox"), start=2):
            self.press(tab)
            self.pause(1)
            self.picture(f"{number:02d}-{tab.lower()}")
        self.home()

    def models(self) -> None:
        self.start()
        self.home()
        self.press("Phone models")
        self.see(MODEL)
        self.picture("05-models")

    def button_under(self, title: str, labels: tuple[str, ...]) -> Callable[[list[Node]], Node | None]:
        def found(nodes: list[Node]) -> Node | None:
            top = next((n.top for n in nodes if n.words == title or n.words.startswith(title)), None)
            if top is None:
                return None
            end = min((n.top for n in nodes if n.top > top and n.words.startswith(MODELS)), default=float("inf"))
            below = sorted((n for n in nodes if top < n.top < end and n.words.startswith(labels)), key=lambda n: n.top)
            return below[0] if below else None
        return found

    def reuse(self) -> bool:
        """Taps the app's offer of the models an earlier install left, if it makes one; the system's own question is the person's."""
        nodes = self.wait("the models", lambda found: found if pick(found, MODEL) else None)
        if not any(n.words == "Use them" for n in nodes):
            return False
        self.press("Use them")
        self.report('ON THE PHONE: tap "Use this folder", then "Allow".')
        return True

    def download(self) -> None:
        self.models()
        if self.reuse():
            self.wait("the copied model", self.button_under(MODEL, ("Use", "In use")), patience=1800)
        button = self.wait("the model's button", self.button_under(MODEL, ("Download ", "Continue download", "Use", "In use")))
        if button.words.startswith(("Download ", "Continue download")):
            if not self.phone.tap(button):
                raise Failed("The download button could not be tapped.")
            self.pause(3)
            nodes = self.wait("the download", lambda found: found or None)
            if any("network is metered" in n.words for n in nodes):
                self.press("Cancel")
                raise Failed("The network is metered: nothing was downloaded. Connect the phone to Wi-Fi.")
            self.wait("the finished download", self.button_under(MODEL, ("Use", "In use")), patience=3600)
        button = self.wait("the downloaded model", self.button_under(MODEL, ("Use", "In use")))
        if button.words == "Use":
            self.phone.tap(button)
        self.picture("06-downloaded")

    def chat(self) -> None:
        self.start()
        self.home()
        self.press("Model on this phone")
        self.press("Ask the model on this phone")
        if not self.phone.type_text(QUESTION):
            raise Failed("The question could not be typed.")
        self.press("Send")
        started = self.clock()
        # The line counting the context's tokens can hold any number, so it is not an answer.
        self.wait("the model's answer", lambda nodes: any(ANSWER in n.words and "tokens" not in n.words for n in nodes), patience=600)
        self.report(f"the model on the phone answered {ANSWER} within {self.clock() - started:.0f} s")
        self.picture("07-chat")

    def reinstall(self) -> None:
        self.phone.uninstall()
        self.phone.install(self.apk)
        self.report("removed the app (its link and settings with it) and installed it again")
        self.models()
        if not self.reuse():
            raise Failed("The app offers no model left from before: it would have to be downloaded again.")
        self.wait("the model still on the phone", self.button_under(MODEL, ("Use", "In use")), patience=1800)
        self.report(f"{MODEL} was still on the phone: nothing downloaded")
        self.picture("08-after-reinstall")

    def crashes(self) -> None:
        pid = self.phone.pid()
        if pid:
            self.pids.add(pid)
        if not self.pids:
            raise Failed("The app was never seen running.")
        for pid in sorted(self.pids):
            log = self.phone.log(pid)
            (self.out / f"log-{pid}.txt").write_text(log, encoding="utf-8")
            if "FATAL EXCEPTION" in log or "Fatal signal" in log:
                raise Failed(f"The app crashed (see log-{pid}.txt).")
        self.report(f"no crash in the app's log ({len(self.pids)} process(es))")

    def run(self, steps: list[str]) -> bool:
        ok = True
        for step in steps:
            action = self.check_home if step == "home" else getattr(self, step)
            try:
                action()
                self.report(f"PASS {step}")
            except Failed as failure:
                self.report(f"FAIL {step}: {failure}")
                ok = False
                if step == "install":
                    break
        return ok


def apk_package(apk: Path, sdk: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    tools = sorted((sdk / "build-tools").glob("*/aapt2*"))
    if not tools:
        raise Failed(f"No aapt2 under {sdk / 'build-tools'}.")
    done = run([str(tools[-1]), "dump", "packagename", str(apk)], capture_output=True, timeout=120, check=False)
    return done.stdout.decode().strip()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("steps", nargs="*", help=f"of {', '.join(STEPS)}")
    parser.add_argument("--serial", required=True, help="the phone, as `adb devices` names it")
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="where the report, pictures and the app's log go")
    parser.add_argument("--sdk", type=Path, default=Path.home() / "Library/Android/sdk")
    args = parser.parse_args(argv)
    unknown = [step for step in args.steps if step not in STEPS]
    if unknown:
        parser.error(f"no step {', '.join(unknown)}; the steps are {', '.join(STEPS)}")
    try:
        found = apk_package(args.apk, args.sdk)
        if found != PACKAGE:
            raise Failed(f"{args.apk} is {found or 'not a readable APK'}, not {PACKAGE}: nothing was installed.")
        adb = args.sdk / "platform-tools" / ("adb.exe" if sys.platform == "win32" else "adb")
        check = Check(Phone(args.serial, adb), args.apk, args.out)
        return 0 if check.run(args.steps or list(DEFAULT_STEPS)) else 1
    except Failed as failure:
        print(f"FAIL: {failure}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
