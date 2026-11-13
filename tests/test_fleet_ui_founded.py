"""The web interface against a real node: it asks for a signed-in session once a person founded the pool, and acts on the pool through the node.

The node is a real process (`testfarm_kit`), the daemon and its interface are the shipped ones on a real socket, and the interface reads the
pool from the node it finds in its home, exactly as a person's machine does. Nothing here holds a key.
"""

from __future__ import annotations

import pytest
from test_fleet_ui import Serving
from testfarm_kit import devices, found, skip_unless_possible

from poolhouse import home
from poolhouse.fleet import service_pool
from poolhouse.fleet.ui import UI

pytest_plugins = ("testfarm_kit",)  # the `node_binary` fixture
pytestmark = pytest.mark.slow


def gated(serving):
    """The interface as a machine runs it: a founded pool asks for a session."""
    serving.ui.gated = True
    return serving


@pytest.fixture(scope="module")
def unfounded_node(node_binary):
    """One real node in a pool of one that no person founded, started once for the tests that only read it or are refused by it."""
    skip_unless_possible()
    with devices(node_binary, "a") as (a,):
        yield a


@pytest.fixture(scope="module")
def founded_node(node_binary):
    """One real node in a pool a person founded, started once for the tests that only read it."""
    skip_unless_possible()
    with devices(node_binary, "a") as (a,):
        found(a)
        yield a


@pytest.fixture
def alone(unfounded_node, monkeypatch):
    """A node in a pool of one that no person founded; this process's home is its root."""
    monkeypatch.setenv("POOLHOUSE_HOME", str(unfounded_node.root))
    return unfounded_node


@pytest.fixture
def governed(founded_node, monkeypatch):
    """A node in a pool a person founded; this process's home is its root."""
    monkeypatch.setenv("POOLHOUSE_HOME", str(founded_node.root))
    return founded_node


@pytest.fixture
def ui(tmp_path):
    serving = gated(Serving(tmp_path))
    try:
        yield serving
    finally:
        serving.close()


def test_the_interface_reads_the_node_it_finds_in_its_home(alone):
    assert home.state("node") == alone.state


def test_a_pool_nobody_founded_answers_this_machine_without_a_sign_in(alone, ui):
    state = ui.call("/ui/setup")[1]
    assert state["in_pool"] is False and state["needs_sign_in"] is False and state["group"] is None
    assert ui.call("/ui/settings")[0] == 200
    assert ui.call("/ui/pool")[1]["pool"]["governance"] == "unfounded"


def test_a_page_started_without_the_pool_actions_refuses_every_pool_route(alone, ui):
    """The daemon may not import the pool actions: the command that starts it hands them in, and a page without them says so."""
    ui.ui.pool_actions = None
    for method, path in (("GET", "/ui/pool"), ("POST", "/ui/pool/found"), ("POST", "/ui/pool/add-device"), ("DELETE", "/ui/pool")):
        status, body, _ = ui.call(path, method=method, body={"name": "home"} if method == "POST" else None)
        assert status == 501 and "pool actions" in body["error"], (method, path, status, body)
    assert ui.call("/ui/fleet")[0] == 200


def test_a_founded_pool_asks_for_a_session(governed, ui):
    state = ui.call("/ui/setup")[1]
    assert state["in_pool"] is True and state["needs_sign_in"] is True and state["group"]
    status, body, _ = ui.call("/ui/settings")
    assert status == 401 and "sign in" in body["error"]
    assert ui.call("/ui/pool")[0] == 401


def test_a_launch_ticket_opens_the_founded_pool_and_the_node_says_who_is_in_it(governed, ui):
    status, body, headers = ui.ticket_session()
    assert status == 200 and body["signed_in"]
    cookie = headers["Set-Cookie"].split(";")[0]
    assert ui.call("/ui/settings", cookie=cookie)[0] == 200
    pool = ui.call("/ui/pool", cookie=cookie)[1]["pool"]
    assert pool["governance"] == "governed" and pool["name"] == ui.call("/ui/setup")[1]["group"]
    assert [m["this_device"] for m in pool["members"] if m["active"]] == [True]


@pytest.mark.redteam
def test_a_node_that_does_not_answer_leaves_the_page_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "no-node-here"))
    serving = gated(Serving(tmp_path))
    try:
        assert serving.call("/ui/settings")[0] == 401
    finally:
        serving.close()


def test_the_pool_actions_refuse_what_names_nothing(alone, ui):
    _, _, headers = ui.ticket_session()
    cookie = headers["Set-Cookie"].split(";")[0]
    for path, body in (("/ui/pool/join", {"host": "127.0.0.1"}),
                       ("/ui/pool/join", {"host": "127.0.0.1", "code": "x", "fingerprint": "f" * 64}),
                       ("/ui/pool/found", {"name": "home", "policy": "wide-open"}),
                       ("/ui/pool/found", {"name": ""}),
                       ("/ui/pool/remove-device", {})):
        status, answer, _ = ui.call(path, method="POST", body=body, cookie=cookie)
        assert status == 400 and answer["error"], (path, body, answer)


def test_governance_follows_the_ui_bound_node_not_the_default(node_binary, monkeypatch):
    skip_unless_possible()
    with devices(node_binary, "a", "b") as (a, b):
        found(a)
        monkeypatch.setenv("POOLHOUSE_HOME", str(a.root))
        first = UI(pool=service_pool.Pool(a.state))
        second = UI(pool=service_pool.Pool(b.state))
        assert first.founded() is True
        assert second.founded() is False
        assert first.governance()["mode"] == "governed"
        assert second.governance()["mode"] == "unfounded"
        found(b)
        assert second.founded() is True, "the sign-in gate must see an external founding immediately"
