"""app/android/phone_check.py runs on a person's own phone: it reads, touches and photographs nothing but the Poolhouse app.

The phone here is canned plumbing: a stand-in for `adb` that answers with fixed text and records what it was asked.
"""

from __future__ import annotations

import importlib.util
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("android_phone_check", ROOT / "app" / "android" / "phone_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = load()

OURS = "app.poolhouse.phone/app.poolhouse.phone.MainActivity"


def window(focus: str = OURS, shade: str | None = "false", locked: bool = False) -> str:
    """Canned `dumpsys window` text with the lines the script reads."""
    text = f"  mCurrentFocus=Window{{1a u0 {focus}}}\n  isKeyguardShowing={'true' if locked else 'false'}\n"
    if shade is not None:
        text += f"  Window #4 Window{{2b u0 NotificationShade}}:\n    mViewVisibility=0x4\n    isVisible={shade}\n  Window #5 Window{{3c u0 StatusBar}}:\n    isVisible=true\n"
    return text


CLEAN = window()
NOT_ALONE = {
    "another app": window("com.example.bank/com.example.bank.Main"),
    "the lock screen": window(locked=True),
    "the shade or a lock window focused": window("NotificationShade", shade="true"),
    "a notification over the app": window(shade="true"),
    "no way to tell": window(shade=None),
}
TOUCHING = ("input ", "screencap")


class CannedPhone:
    """Answers `adb` with canned text; `windows` are given in turn and the last one stays."""

    def __init__(self, *windows: str, screen: tuple[str, ...] = (), raw: bytes = b""):
        self.windows, self.screen, self.raw = list(windows) or [CLEAN], list(screen), raw
        self.asked: list[str] = []

    def __call__(self, command, **_):
        assert command[1:3] == ["-s", "canned-serial"], "every request names the one phone"
        said = " ".join(command[3:])
        self.asked.append(said)
        out = b""
        if said == "shell dumpsys window":
            out = (self.windows.pop(0) if len(self.windows) > 1 else self.windows[0]).encode()
        elif said == "exec-out screencap":
            out = self.raw
        elif said.startswith("exec-out uiautomator"):
            words = self.screen.pop(0) if len(self.screen) > 1 else (self.screen[0] if self.screen else "")
            nodes = "".join(f'<node text="{word}" package="app.poolhouse.phone" bounds="[0,{i * 100}][100,{i * 100 + 50}]" />'
                            for i, word in enumerate(words.split("|")))
            other = '<node text="canned private text" package="com.example.bank" bounds="[0,0][1,1]" />'
            out = f"<?xml version='1.0'?><hierarchy>{nodes}{other}</hierarchy>dumped".encode()
        elif said.startswith("shell pm list packages"):
            out = b"package:app.poolhouse.phone\n"
        elif said.startswith("shell pidof"):
            out = b"4242\n"
        elif said.startswith("logcat"):
            out = b"canned log line\n"
        return subprocess.CompletedProcess(command, 0, out, b"")


def phone(canned: CannedPhone):
    return check.Phone("canned-serial", Path("adb"), run=canned)


def raw(width: int, height: int) -> bytes:
    return struct.pack("<IIII", width, height, 1, 0) + bytes(width * height * 4)


@pytest.mark.parametrize("why", sorted(NOT_ALONE))
def test_nothing_is_typed_tapped_or_photographed_unless_the_app_is_alone_on_the_screen(tmp_path, why):
    canned = CannedPhone(NOT_ALONE[why], raw=raw(4, 100))
    subject = phone(canned)
    assert not subject.tap(check.Node("Send", "", 0, 0, 10, 10))
    assert not subject.back()
    assert not subject.type_text("canned")
    assert not subject.screenshot(tmp_path / "picture.png")
    assert not (tmp_path / "picture.png").exists()
    assert not [said for said in canned.asked if any(word in said for word in TOUCHING)], canned.asked


@pytest.mark.parametrize("why", ["another app", "the lock screen", "the shade or a lock window focused"])
def test_the_screen_is_not_read_when_the_app_is_not_in_front(why):
    canned = CannedPhone(NOT_ALONE[why], screen=("Chats",))
    assert phone(canned).nodes() is None
    assert not [said for said in canned.asked if "uiautomator" in said]


def test_only_the_apps_own_text_is_read_from_the_screen():
    assert [node.words for node in phone(CannedPhone(screen=("Chats|Pool",))).nodes()] == ["Chats", "Pool"]


def test_a_picture_is_dropped_when_something_appears_while_it_is_taken(tmp_path):
    canned = CannedPhone(CLEAN, NOT_ALONE["a notification over the app"], raw=raw(4, 100))
    assert not phone(canned).screenshot(tmp_path / "picture.png")
    assert "exec-out screencap" in canned.asked and not (tmp_path / "picture.png").exists()


def test_a_picture_is_saved_without_the_status_bar(tmp_path):
    assert phone(CannedPhone(raw=raw(4, 100))).screenshot(tmp_path / "picture.png")
    data = (tmp_path / "picture.png").read_bytes()
    assert data.startswith(b"\x89PNG") and struct.unpack(">II", data[16:24]) == (4, 94)


#: Everything a run may ask of the phone.
ALLOWED = (
    r"install -r \S+", r"uninstall app\.poolhouse\.phone", r"shell am start -n app\.poolhouse\.phone/\.MainActivity",
    r"shell dumpsys window", r"shell pm list packages --user 0 app\.poolhouse", r"shell pidof app\.poolhouse\.phone",
    r"logcat -d --pid=4242", r"exec-out uiautomator dump /dev/tty", r"exec-out screencap", r"shell input tap \d+ \d+",
    r"shell input keyevent KEYCODE_BACK", r"shell input text .+",
)


def test_a_whole_run_asks_the_phone_only_about_the_app(tmp_path):
    home = "Chats|Phone models|Model on this phone|Pool|Work|Inbox"
    downloaded = "Use them|Gemma 4 E2B|In use|Ask the model on this phone|Send|391"
    # The screens in the order the steps read them: each read takes the next, and the last stays.
    canned = CannedPhone(screen=(f"{home}|{downloaded}",), raw=raw(4, 100))
    clock = iter(range(0, 100_000))
    run = check.Check(phone(canned), tmp_path / "app.apk", tmp_path / "out", pause=lambda _: None, clock=lambda: next(clock), say=lambda _: None)
    assert run.run(list(check.STEPS)), (tmp_path / "out" / "report.txt").read_text(encoding="utf-8")
    strange = [said for said in canned.asked if not any(re.fullmatch(pattern, said) for pattern in ALLOWED)]
    assert not strange, strange
    assert "uninstall app.poolhouse.phone" in canned.asked


class ModelLeft(CannedPhone):
    """After installing again the model is still on the phone, and is offered once the folder has been opened to the app."""

    def __call__(self, command, **named):
        done = super().__call__(command, **named)
        if self.asked[-1] == "shell input tap 50 325":
            self.screen = ["Chats|Phone models|Inbox|Gemma 4 E2B|Use"]
        return done


def checking(canned: CannedPhone, out: Path):
    clock = iter(range(0, 100_000))
    return check.Check(phone(canned), out.parent / "app.apk", out, patience=100, pause=lambda _: None, clock=lambda: next(clock), say=lambda _: None)


def test_a_model_left_from_before_is_reused_through_the_phones_own_question(tmp_path):
    canned = ModelLeft(screen=("Chats|Phone models|Inbox|Use them|Gemma 4 E2B|On the phone from before",), raw=raw(4, 100))
    assert checking(canned, tmp_path / "out").run(["reinstall"])
    report = (tmp_path / "out" / "report.txt").read_text(encoding="utf-8")
    assert 'tap "Use this folder", then "Allow"' in report and "nothing downloaded" in report


def test_installing_again_fails_when_the_model_would_have_to_be_downloaded(tmp_path):
    # The other model's button is no answer for this one.
    canned = CannedPhone(screen=("Chats|Phone models|Inbox|Gemma 4 E2B|Download 2.6 GB|Gemma 4 E4B|Use",), raw=raw(4, 100))
    assert not checking(canned, tmp_path / "out").run(["reinstall"])


def test_a_person_is_asked_to_unlock_once_and_nothing_is_done_for_them(tmp_path):
    canned = CannedPhone(NOT_ALONE["the lock screen"])
    clock = iter(range(0, 100_000, 10))
    said: list[str] = []
    run = check.Check(phone(canned), tmp_path / "app.apk", tmp_path / "out", patience=100, pause=lambda _: None, clock=lambda: next(clock), say=said.append)
    assert not run.run(["models"])
    assert len([line for line in said if line.startswith("WAITING")]) == 1
    assert not [asked for asked in canned.asked if any(word in asked for word in (*TOUCHING, "uiautomator"))]


def test_another_apps_package_is_never_installed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check, "apk_package", lambda apk, sdk: "com.example.other")
    monkeypatch.setattr(check, "Check", lambda *_: pytest.fail("a check of the phone was started"))
    assert check.main(["--serial", "canned-serial", "--apk", str(tmp_path / "other.apk"), "--out", str(tmp_path / "out")]) == 1
    assert "nothing was installed" in capsys.readouterr().err
