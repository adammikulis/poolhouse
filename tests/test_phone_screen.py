"""The computer's side of linked phones: the QR code, the Phones routes that only the person's session may use, the command that an
agent is refused, and which projects are connected on linking."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import threading

import pytest
from phone_kit import Key, linked

from poolhouse.caller import AGENT_MARKERS
from poolhouse.cli import phone as command
from poolhouse.fleet.launch_secret import LaunchSecret
from poolhouse.fleet.phone import PhoneService, qr
from poolhouse.fleet.phone.link import code_key, message
from poolhouse.fleet.ui import serve_page
from poolhouse.http import Options, ServerError, request_json
from poolhouse.phone_mailbox import Store

LINK = ("poolhouse://link?v=1&pin=" + "0123456789abcdef" * 20)[:213]
CHECKED = "98da3c99dcef"
"""The first hex digits of the SHA-256 of the modules of `LINK`'s symbol."""


def test_the_qr_code_is_a_symbol_of_the_right_size_with_its_finders():
    rows = qr.matrix(LINK)
    assert len(rows) == 57 and all(len(row) == 57 for row in rows)   # version 10 holds 213 bytes at level M
    finder = [[max(abs(x - 3), abs(y - 3)) != 2 for x in range(7)] for y in range(7)]
    for left, top in ((0, 0), (50, 0), (0, 50)):
        assert [row[left:left + 7] for row in rows[top:top + 7]] == finder
    assert [rows[6][x] for x in range(8, 49)] == [x % 2 == 0 for x in range(8, 49)]
    picture = qr.svg(LINK)
    assert picture.startswith("<svg") and 'viewBox="0 0 65 65"' in picture
    with pytest.raises(ValueError, match="too long"):
        qr.matrix("x" * 700)


def test_the_qr_code_is_the_checked_symbol():
    """The digest of a symbol that OpenCV decoded back to its text and that equals segno's module for module."""
    drawn = "".join("1" if dark else "0" for row in qr.matrix(LINK) for dark in row)
    assert hashlib.sha256(drawn.encode()).hexdigest().startswith(CHECKED)


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    server = serve_page(name="Studio")
    handler = server.RequestHandlerClass
    handler.do_DELETE = handler.do_GET   # the daemon's handler routes DELETE; this page-only server names two methods
    ui = handler.ui
    ui.phones = PhoneService(ui, None, store=Store(tmp_path / "phones"), port=0, addresses=lambda: ["127.0.0.1"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield ui, f"http://127.0.0.1:{server.server_port}"
    ui.phones.close()
    server.shutdown()
    server.server_close()


def _call(base, method, path, payload=None, session=None):
    headers = {"X-Poolhouse-UI": "1", **({"Cookie": session} if session else {})}
    return request_json(base + path, payload=payload, method=method, options=Options(headers=headers, timeout=20))


def _person(ui):
    session = ui.sessions.open("ticket", "launch-ticket")
    return ui.sessions.cookie_header(session).split(";")[0]


def test_phones_are_managed_only_with_the_persons_own_session(page):
    ui, base = page
    for method, path in (("GET", "/ui/phones"), ("POST", "/ui/phones/offer")):
        with pytest.raises(ServerError, match="403"):
            _call(base, method, path, {} if method == "POST" else None)
    assert ui.phones.listening["addresses"] == []
    cookie = _person(ui)
    offer = _call(base, "POST", "/ui/phones/offer", {}, cookie)
    assert offer["code"] and offer["qr_svg"].startswith("<svg")
    held = _call(base, "GET", "/ui/phones", session=cookie)
    assert held["offer"]["code"] == offer["code"] and held["listening"]["addresses"] == ["127.0.0.1"] and held["phones"] == []
    assert _call(base, "DELETE", "/ui/phones/offer", session=cookie)["offer"] is None


def test_the_pages_one_connection_survives_a_code_request_and_a_refusal(page):
    """The window reuses its connection, so a body a route leaves unread would garble the request after it."""
    ui, base = page
    link = http.client.HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[-1]), timeout=20)
    try:
        for cookie, expected in ((_person(ui), 200), ("", 403)):
            for method, body in (("POST", b"{}"), ("GET", None)):
                link.request(method, "/ui/phones/offer" if body else "/ui/phones", body=body,
                             headers={"X-Poolhouse-UI": "1", "Cookie": cookie})
                answer = link.getresponse()
                answer.read()
                assert answer.status == expected, (method, expected)
    finally:
        link.close()


def test_the_screen_lists_switches_and_removes_a_phone(page):
    ui, base = page
    phone = linked(ui.phones.store).phone
    cookie = _person(ui)
    [row] = _call(base, "GET", "/ui/phones", session=cookie)["phones"]
    assert (row["id"], row["pool_use"], row["offering"]) == (phone, False, False)
    switched = _call(base, "POST", f"/ui/phones/{phone}/pool-use", {"enabled": True}, cookie)
    assert switched["phones"][0]["pool_use"] is True
    assert _call(base, "DELETE", f"/ui/phones/{phone}", session=cookie)["phones"] == []


def test_connecting_a_project_writes_its_hooks(page, tmp_path):
    ui, base = page
    project = tmp_path / "project"
    project.mkdir()
    got = _call(base, "POST", "/ui/phones/projects", {"path": str(project), "connected": True}, _person(ui))
    assert [p["connected"] for p in got["projects"]] == [True]
    assert "phone hook listen" in (project / ".claude" / "settings.local.json").read_text()


def test_linking_connects_the_projects_claude_ran_in_and_respects_a_no(page, tmp_path, monkeypatch):
    ui, _base = page
    work, turned_off = tmp_path / "work", tmp_path / "off"
    for folder in (work, turned_off, work / ".claude" / "worktrees" / "agent-1"):
        folder.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "claude" / "projects" / "-p"
    root.mkdir(parents=True)
    for number, folder in enumerate((work / ".claude" / "worktrees" / "agent-1", turned_off)):
        line = {"type": "user", "cwd": str(folder), "message": {"role": "user", "content": "hi"}}
        (root / f"00000000-0000-0000-0000-00000000000{number}.jsonl").write_text(json.dumps(line) + "\n")
    ui.phones.store.project(str(turned_off.resolve()), False)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "elsewhere"))
    assert ui.phones.connect_known() == [str(work.resolve())]
    assert not (turned_off / ".claude").exists()
    (work / ".claude" / "settings.local.json").unlink()
    offer = ui.phones.offer()
    key = Key()
    said = message("poolhouse/phone-link/v1", ui.phones.identity().fingerprint, key.public, "Pixel")
    proof = base64.b64encode(hmac.new(code_key(offer["code"], ui.phones.identity().fingerprint), said, hashlib.sha256).digest()).decode()
    ui.phones.linking().link("192.168.1.9", "Pixel", key.public, proof, key.sign(said))
    for thread in threading.enumerate():
        if thread.name == "phone-connect":
            thread.join(20)
    assert "phone hook listen" in (work / ".claude" / "settings.local.json").read_text()


def test_the_command_signs_in_to_the_daemon_and_does_what_the_screen_does(page, tmp_path, monkeypatch, capsys):
    ui, base = page
    ui.launch = LaunchSecret(tmp_path / "daemon", int(base.rsplit(":", 1)[-1]))
    monkeypatch.setattr("poolhouse.fleet.runtime_paths.default_root", lambda: tmp_path / "daemon")
    for marker in AGENT_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    assert command.main(["link"]) == 0
    shown = capsys.readouterr().out
    assert ui.phones.linking().open().shown in shown and "127.0.0.1:" in shown
    phone = linked(ui.phones.store).phone
    assert command.main(["pool-use", phone, "on"]) == 0
    assert command.main(["list"]) == 0
    assert f"{phone}  Pixel  offline  in the pool" in capsys.readouterr().out
    assert command.main(["remove", phone]) == 0
    assert ui.phones.store.phones() == {}


def test_a_command_an_agent_runs_cannot_link_remove_or_switch(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    for args in (["link"], ["remove", "ph-000000000000"], ["pool-use", "ph-000000000000", "on"]):
        assert command.main(args) == 2, args
    assert "refused to an agent" in capsys.readouterr().err


def test_the_terminal_code_draws_the_same_symbol():
    drawn = command.terminal_qr("poolhouse://link?v=1").splitlines()
    size = len(qr.matrix("poolhouse://link?v=1"))
    assert len(drawn) == (size + 5) // 2 and all(len(line) == size + 4 for line in drawn)
