"""The daemon simulated in the browser (tests/web_assets.py) answers in the shapes the real daemon answers, and marks them made up."""

import pytest
from browser_expect import expect
from launch_support import signed
from service_kit import node_binary, studio_node  # noqa: F401  (fixtures)
from test_fleet_ui import Serving
from test_studio_canvas_browser import SPECS, studio_browser  # noqa: F401 (fixture)
from web_assets import SimulatedDaemon, shell_answers

from poolhouse.fleet import tls
from poolhouse.studio.catalog import node_catalog
from poolhouse.testing.canned import CANNED_HEADER, CANNED_HEADER_VALUE

pytestmark = pytest.mark.slow


@pytest.fixture
def real_daemon(tmp_path, studio_node):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.root = tmp_path / "traind"
    served.ui.settings.setup_done = True
    identity = tls.identity(tmp_path / "tls-studio", "studio")
    served.ui.studio_identity = lambda: identity
    studio_node.bind(studio_node.a, identity, f"https://127.0.0.1:{served.port}")
    signed(served)   # a founded pool answers Studio only for a signed-in window
    try:
        yield served
    finally:
        served.close()
        if hasattr(served.ui, "studio_streams"):
            served.ui.studio_streams.close()
        if hasattr(served.ui, "studio"):
            served.ui.studio.close()
            served.ui.studio.store.close()


def assert_within(made_up, real, where):
    """Every key of a made-up mapping is one the real answer has, with a value of the same kind, as far down as both go."""
    assert set(made_up) <= set(real), f"{where}: the daemon answers no {sorted(set(made_up) - set(real))}"
    for key, value in made_up.items():
        other = real[key]
        if value is None or other is None:
            continue
        number = (int, float)
        assert (isinstance(value, number) and isinstance(other, number)) or type(value) is type(other), (
            f"{where}.{key}: {type(value).__name__} here, {type(other).__name__} from the daemon")
        if isinstance(value, dict):
            assert_within(value, other, f"{where}.{key}")


def test_every_simulated_shell_answer_has_the_shape_of_the_real_one(real_daemon):
    for path, made_up in shell_answers().items():
        status, real, _ = real_daemon.call(path)
        assert status == 200, (path, real)
        assert set(made_up) == set(real), path
        assert_within(made_up, real, path)


def test_the_made_up_studio_records_have_the_shapes_the_real_routes_answer(real_daemon):
    catalogue = {key for node in node_catalog() for key in node}
    for spec in SPECS:
        assert set(spec) <= catalogue, spec["type"]
    graph = {"schema_version": 1, "nodes": [{"id": "ask", "type": "prompt", "version": 1, "parameters": {"template": "Hello"},
                                             "bindings": {}}], "edges": [], "groups": {}}
    status, saved, _ = real_daemon.call("/ui/studio/workflows", method="POST", body={"title": "CANNED check", "graph": graph})
    assert status in (200, 201), saved
    assert {"id", "revision", "title", "graph"} <= set(saved["workflow"])
    status, submitted, _ = real_daemon.call("/ui/studio/runs", method="POST", body={
        "workflow_id": saved["workflow"]["id"], "revision": saved["workflow"]["revision"], "idempotency_key": "a" * 32})
    assert status in (200, 201, 202), submitted
    assert {"id", "workflow_id", "revision", "state", "nodes"} <= set(submitted["run"])


def test_a_simulated_page_opens_studio_with_every_answer_marked_and_no_server(studio_browser):  # noqa: F811
    daemon = SimulatedDaemon()
    with studio_browser() as page:
        errors, answers = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: answers.append(response)
                if "json" in response.headers.get("content-type", "") else None)
        daemon.attach(page)
        page.goto(daemon.url)
        expect(page.locator("studio-view > section")).to_be_visible()
        page.wait_for_load_state("networkidle")
        asked = {path for _method, path in daemon.requests}
        assert {"/ui/setup", "/ui/session", "/ui/settings", "/ui/studio/nodes"} <= asked
        assert daemon.unanswered == []
        ours = [response for response in answers if not response.url.endswith((".json", ".js"))]
        assert ours and all(response.headers.get(CANNED_HEADER.lower()) == CANNED_HEADER_VALUE for response in ours)
        assert not errors
