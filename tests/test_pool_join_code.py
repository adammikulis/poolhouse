"""Adding a computer to a pool with a code, against real nodes: founding from the page and the command, showing an address with the code, joining at
whatever a person types, the names people gave their computers, what a member says when asked to add a device, and the sync that follows
with no one asking for it."""

from __future__ import annotations

import json

import pytest
from computers_kit import BOARD, computers
from test_fleet_ui import Serving
from testfarm_kit import until

import poolhouse as ph
from poolhouse import pool_cli
from poolhouse.api import pool
from poolhouse.board import approval
from poolhouse.board.client import Client
from poolhouse.fleet.service_pool import Pool
from poolhouse.fleet.ui import UI

pytest_plugins = ["node_kit"]  # the `node_binary` fixture
pytestmark = pytest.mark.slow

SYNC_WAIT_S = 75
"""More than three of the node's own rounds."""


@pytest.fixture
def alone(node_binary, monkeypatch):
    with computers(node_binary, monkeypatch, "127.0.0.1") as (a,):
        yield a.use()


@pytest.fixture
def two(node_binary, monkeypatch):
    with computers(node_binary, monkeypatch, "127.0.0.1", "127.0.0.1") as both:
        yield both


def one_pool(a, b) -> None:
    """``a`` makes a pool and ``b`` joins it with the code, as two people at two screens would."""
    a.use()
    ph.pool.found("home")
    made = ph.pool.add_device()
    b.use()
    ph.pool.join("127.0.0.1", made["code"], port=a.port)


# -- founding ----------------------------------------------------------------------------
def test_the_page_founds_a_pool(alone, tmp_path):
    serving = Serving(tmp_path)
    try:
        _, _, headers = serving.ticket_session()
        cookie = headers["Set-Cookie"].split(";")[0]
        status, answer, _ = serving.call("/ui/pool/found", method="POST", body={"name": "home", "policy": "secure"}, cookie=cookie)
    finally:
        serving.close()
    assert status == 200, answer
    assert answer["already"] is False and "now in the pool home" in answer["say"]
    assert answer["pool"]["governance"] == "governed" and answer["pool"]["name"] == "home"
    assert alone.status()["governance"]["name"] == "home"


def test_the_command_founds_a_pool(alone, capsys):
    assert pool_cli.main(["found", "home", "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["already"] is False and answer["pool"]["name"] == "home" and alone.status()["governance"]["name"] == "home"


# -- after joining ---------------------------------------------------------------------
@pytest.mark.timeout(180)
def test_the_computers_of_a_pool_sync_with_nobody_asking(two):
    a, b = two
    one_pool(a, b)
    writer, written = a.session("writer")
    writer.call("post", BOARD, written, kind="message", fields={"type": "note", "to": "#general", "body": "written on the first computer", "subject": ""})
    reader, read = b.session("reader")

    def arrived():
        entries = reader.call("read", BOARD, read, kind="message")["entries"]
        return any(e["fields"].get("body") == "written on the first computer" for e in entries)

    until("the entry to reach the other computer", SYNC_WAIT_S, arrived)
    for one in (a, b):
        until("each computer to record that it synced with the other", SYNC_WAIT_S,
              lambda one=one: all(m["last_sync_ms"] > 0 for m in one.status()["members"] if not m["self"]))


def test_a_pool_lists_each_computer_by_the_name_typed_on_it(two):
    a, b = two
    for computer, typed in ((a, "the desk computer"), (b, "the travel laptop")):
        UI(pool=Pool(computer.state)).set_name(typed)  # what the first screen of setup does with the name
    assert a.names() == {"the desk computer"}
    one_pool(a, b)
    assert a.names() == b.names() == {"the desk computer", "the travel laptop"}


def test_the_code_comes_with_an_address_the_other_computer_can_type(two):
    a, b = two
    a.use()
    ph.pool.found("home")
    made = ph.pool.add_device()
    assert made["addresses"] == [f"127.0.0.1:{a.port}"] and made["policy"] == "secure"
    b.use()
    ph.pool.join(made["addresses"][0], made["code"])
    assert ph.pool.status().name == "home"


@pytest.mark.parametrize(("listens", "typed"), [("[::1]", "::1"), ("127.0.0.1", "localhost")],
                         ids=["an IPv6 address", "a name that resolves to IPv6 first"])
def test_a_computer_joins_at_what_was_typed(node_binary, monkeypatch, listens, typed):
    with computers(node_binary, monkeypatch, listens, "127.0.0.1") as (a, b):
        a.use()
        ph.pool.found("home")
        made = ph.pool.add_device()
        b.use()
        ph.pool.join(typed, made["code"], port=a.port)
        assert ph.pool.status().name == "home"


def test_a_member_says_which_computer_adds_devices(two):
    a, b = two
    UI(pool=Pool(a.state)).set_name("the desk computer")
    one_pool(a, b)
    b.use()
    with pytest.raises(ph.pool.Refusal) as refused:
        ph.pool.add_device()
    said = str(refused.value)
    assert "the desk computer" in said and "the computer that made it" in said and "Add a device" in said, said
    assert "OK" not in said and "denied" not in said, said


def test_an_open_pool_is_joined_by_the_page_with_the_fingerprint_and_no_code(two, tmp_path):
    a, b = two
    a.use()
    ph.pool.found("home", "open")
    made = ph.pool.add_device()
    assert made["policy"] == "open"
    b.use()
    serving = Serving(tmp_path)
    try:
        _, _, headers = serving.ticket_session()
        cookie = headers["Set-Cookie"].split(";")[0]
        status, answer, _ = serving.call("/ui/pool/join", method="POST", cookie=cookie,
                                         body={"host": made["addresses"][0], "fingerprint": made["fingerprint"]})
    finally:
        serving.close()
    assert status == 200, answer
    assert ph.pool.status().name == "home"


# -- names in what a key signs -----------------------------------------------------------
@pytest.mark.parametrize(("typed", "listed"), [("büro", "büro"), ("büro\u2019s rechner", "büros rechner"), ("書斎", "書斎")],
                         ids=["an accented letter", "a curly apostrophe", "no Latin letter"])
def test_a_request_is_made_on_a_computer_with_any_typed_name(alone, typed, listed):
    UI(pool=Pool(alone.state)).set_name(typed)
    ph.pool.found("home")
    assert alone.names() == {listed}, "the pool shows the name as typed"
    client = Client(alone.state)
    asker = client.call("register", BOARD, "", model="claude-sonnet-5-5", harness="claude-code", session="asker")["token"]
    made = client.call("approval_request", BOARD, asker, **approval.wire([approval.Ask("pool_leave", uses=1)]))
    assert made["state"] and made["text"].isascii(), made


# -- who may change the pool through the page --------------------------------------------
CHANGES = [("POST", "/ui/pool/found", {"name": "theirs", "policy": "open"}), ("POST", "/ui/pool/join", {"host": "127.0.0.1", "code": "123456"}),
           ("POST", "/ui/pool/add-device", {}), ("POST", "/ui/pool/remove-device", {"device": "other"}), ("DELETE", "/ui/pool", None)]


@pytest.mark.parametrize(("method", "path", "body"), CHANGES, ids=[f"{method} {path}" for method, path, _ in CHANGES])
def test_only_a_signed_in_window_changes_the_pool_even_before_there_is_one(alone, tmp_path, method, path, body):
    serving = Serving(tmp_path)
    try:
        status, answer, _ = serving.call(path, method=method, body=body)
    finally:
        serving.close()
    assert status == 403 and "its own window" in answer["error"], (status, answer)
    assert alone.status()["governance"]["mode"] == "unfounded"


@pytest.mark.parametrize("name", ["rebound.local", "intranet"])
def test_a_name_somebody_points_at_this_computer_reaches_nothing_without_a_session(alone, tmp_path, name):
    serving = Serving(tmp_path)
    try:
        where = f"{name}:{serving.port}"
        for method, path in (("GET", "/ui/pool"), ("GET", "/ui/settings"), ("POST", "/ui/settings"), ("POST", "/ui/setup/prefs")):
            status, answer, _ = serving.call(path, method=method, body={} if method == "POST" else None, headers={"Host": where})
            assert status == 403, (method, path, status, answer)
        for plain in (f"127.0.0.1:{serving.port}", f"localhost:{serving.port}"):
            assert serving.call("/ui/pool", headers={"Host": plain})[0] == 200
    finally:
        serving.close()


# -- what a person types -----------------------------------------------------------------
@pytest.mark.parametrize(("typed", "dialed"), [
    ("192.168.1.20", ("192.168.1.20", 7447)),
    (" 192.168.1.20:7500 ", ("192.168.1.20", 7500)),
    ("studio.local/", ("studio.local", 7447)),
    ("fd00::1234", ("fd00::1234", 7447)),
    ("[fd00::1234]:7500", ("fd00::1234", 7500)),
    ("https://192.168.1.20:8770/ui/#pool", ("192.168.1.20", 7447)),  # the page's port is not the pool's
    ("http://[fd00::1234]:8770", ("fd00::1234", 7447)),
])
def test_what_a_person_types_or_pastes_is_read_as_an_address(typed, dialed):
    assert pool._typed_address(typed, pool.DEFAULT_PORT) == dialed


@pytest.mark.parametrize(("typed", "port", "said"), [
    ("ftp://192.168.1.20", 7447, "not a device's address"),
    ("the studio mac", 7447, "not a device's address"),
    ("192.168.1.20:port", 7447, "not a device's address"),
    ("192.168.1.20:70000", 7447, "not a device's address"),
    ("192.168.1.20/ui", 7447, "not a device's address"),
    ("[fd00::1234", 7447, "not a device's address"),
    ("https://", 7447, "not a device's address"),
    ("fe80::1%en0", 7447, "with a % in it"),
    ("192.168.1.20:7500", 7600, "give one of them"),
])
def test_what_is_not_an_address_is_refused_in_one_plain_sentence(typed, port, said):
    with pytest.raises(ph.Error, match=said) as refused:
        pool.join(typed, "123456", port=port)
    assert len(str(refused.value)) <= 160


def test_a_node_on_every_interface_shows_the_lan_address_first(monkeypatch):
    import socket
    from types import SimpleNamespace as Row

    held = {"lo0": [Row(family=socket.AF_INET, address="127.0.0.1")],
            "bridge": [Row(family=socket.AF_INET, address="192.168.64.1")],
            "en0": [Row(family=socket.AF_INET6, address="fd00::1"), Row(family=socket.AF_INET, address="192.168.1.20"),
                    Row(family=socket.AF_INET, address="169.254.3.4")],
            "down": [Row(family=socket.AF_INET, address="10.9.9.9")]}
    monkeypatch.setattr(pool.psutil, "net_if_addrs", lambda: held)
    monkeypatch.setattr(pool.psutil, "net_if_stats", lambda: {name: Row(isup=name != "down") for name in held})
    monkeypatch.setattr(pool, "primary_ip", lambda: "192.168.1.20")
    assert pool._shown_addresses("0.0.0.0:7447") == ["192.168.1.20", "192.168.64.1"]
    assert pool._shown_addresses("0.0.0.0:7500") == ["192.168.1.20:7500", "192.168.64.1:7500"]
    assert pool._shown_addresses("[::1]:7500") == ["[::1]:7500"]
    assert pool._shown_addresses("") == []
