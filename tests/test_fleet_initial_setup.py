"""First-run device name and local browser admission over real sockets."""
from __future__ import annotations

import pytest
from launch_support import browser as browser_headers, sign_in
from service_kit import node_binary, studio_node  # noqa: F401  (fixtures)
from test_fleet_ui import Serving

from poolhouse.fleet.daemon import DaemonOptions, DaemonRuntime


@pytest.fixture
def device(tmp_path):
    served = Serving(tmp_path)
    try:
        yield served
    finally:
        served.close()


def initial(device, cookie=None, **extra):
    return device.call("/ui/setup/initial", method="POST", headers=browser_headers(device),
                       cookie=sign_in(device) if cookie is None else cookie,
                       body={"name": "quillhaven", **extra})


def test_a_name_is_kept_and_the_wizard_is_not_finished(device):
    status, state, headers = initial(device)
    assert status == 200
    assert state["name"] == "quillhaven"
    assert state["needs_setup"] is True
    assert "HttpOnly" in headers["Set-Cookie"]
    assert device.ui.name == "quillhaven"


@pytest.mark.parametrize("changes", [
    {"Origin": "http://foreign.invalid"}, {"Sec-Fetch-Site": "cross-site"},
    {"Authorization": "Bearer agent"}, {"X-Poolhouse-Token": "agent"},
    {"X-Poolhouse-Agent": "helper"}, {"Host": "[malformed"},
])
def test_initial_rejects_foreign_and_agent_requests(device, changes):
    headers = {**browser_headers(device), **changes}
    status, _, _ = device.call("/ui/setup/initial", method="POST", headers=headers,
                               cookie=sign_in(device), body={"name": "quillhaven"})
    assert status == 403
    assert device.ui.name == "studio"


def test_a_field_other_than_the_name_is_refused(device):
    cookie = sign_in(device)
    assert initial(device, cookie=cookie, mode="dev")[0] == 400
    assert initial(device, cookie=cookie, automatic="yes")[0] == 400
    assert device.ui.name == "studio"


def test_token_browser_session_cannot_mint_owner_session(device):
    token_session = device.ui.sessions.open("token", "token")
    cookie = device.ui.sessions.cookie_header(token_session).split(";")[0]
    status, _, _ = device.call("/ui/setup/initial", method="POST", headers=browser_headers(device),
                               cookie=cookie, body={"name": "quillhaven"})
    assert status == 403
    assert device.ui.name == "studio"


def test_updater_waits_for_downloads_and_background_installations():
    from types import SimpleNamespace

    runtime = DaemonRuntime(DaemonOptions())
    runtime.runner = SimpleNamespace(status=lambda: {"busy": False})
    runtime.downloads = SimpleNamespace(active=lambda: [])
    runtime.interface = None
    assert runtime.background_busy() is False
    runtime.downloads.active = lambda: [SimpleNamespace(state="getting")]
    assert runtime.background_busy() is True
    runtime.downloads.active = lambda: [SimpleNamespace(state="done")]
    assert runtime.background_busy() is False
    runtime.interface = SimpleNamespace(setup_jobs=SimpleNamespace(active=lambda: True))
    assert runtime.background_busy() is True
    runtime.interface.setup_jobs.active = lambda: False
    assert runtime.background_busy() is False


@pytest.mark.redteam
@pytest.mark.parametrize("changes", [
    {"Origin": "http://foreign.invalid"}, {"Sec-Fetch-Site": "cross-site"},
    {"Authorization": "Bearer agent"}, {"X-Poolhouse-Token": "agent"},
    {"X-Poolhouse-Agent": "helper"}, {"Host": "rebound.invalid"},
])
def test_owner_admission_refuses_foreign_and_agent_requests_even_with_a_launch_session(device, changes):
    status, _, headers = device.call("/ui/setup/initial", method="POST", cookie=sign_in(device),
                                     headers={**browser_headers(device), **changes},
                                     body={"name": "quillhaven"})
    assert status == 403
    assert "Set-Cookie" not in headers
    assert len(device.ui.sessions) == 1


@pytest.mark.redteam
def test_owner_admission_refuses_lan_and_missing_ui_header(device):
    from poolhouse.fleet.onboard.lan import primary_ip

    body = {"name": "quillhaven"}
    cookie = sign_in(device)
    for options in ({"host": primary_ip()}, {"ui_header": False}):
        status, _, headers = device.call("/ui/setup/initial", method="POST", cookie=cookie,
                                         headers=browser_headers(device), body=body, **options)
        assert status == 403
        assert "Set-Cookie" not in headers
    assert len(device.ui.sessions) == 1


@pytest.mark.redteam
@pytest.mark.parametrize("body", [
    {"name": "../escaped", "mode": "dev"},
    {"name": "quillhaven", "mode": "owner"},
    {"name": "quillhaven", "automatic": "yes"},
    {"name": "quillhaven", "root": "../escaped"},
    {"name": "x" * 5000},
    ["quillhaven", "dev"],
])
def test_initial_rejects_hostile_body_without_identity_changes(device, body):
    status, _, headers = device.call("/ui/setup/initial", method="POST", headers=browser_headers(device),
                                     cookie=sign_in(device), body=body)
    assert status == 400
    assert "Set-Cookie" not in headers
    assert len(device.ui.sessions) == 1
    assert device.ui.name == "studio"


@pytest.mark.redteam
def test_setup_jobs_refuses_untrusted_readers_and_ignores_external_paths(device, tmp_path, studio_node):  # noqa: F811
    from poolhouse.fleet.setup_jobs import Jobs

    device.ui.root = tmp_path / "traind"
    device.ui.setup_jobs = Jobs(device.ui.root)
    device.ui.gated = True               # the node of the device founded its pool
    device.ui.setup_finished()
    for expected, options in ((401, {}), (401, {"cookie": "poolhouse-session=forged"}),
                              (403, {"ui_header": False}), (401, {"headers": {"Host": "rebound.invalid"}})):
        status, _, _ = device.call("/ui/setup/jobs", **options)
        assert status == expected
    outside = tmp_path / "external.json"
    outside.write_text('{"secret":"external-job-store"}')
    session = device.ui.sessions.open("setup", "launch-ticket")
    cookie = device.ui.sessions.cookie_header(session).split(";")[0]
    status, body, _ = device.call("/ui/setup/jobs?root=../external.json&path=../external.json", cookie=cookie)
    assert status == 200
    assert body == {"jobs": []}
    assert outside.read_text() == '{"secret":"external-job-store"}'


@pytest.mark.parametrize("kind", ["missing", "forged", "expired", "uncredentialed"])
def test_initial_distinguishes_session_loss_from_forbidden_credentials(device, kind):
    from poolhouse.fleet.session import parse_cookie
    cookie = ""
    if kind == "forged":
        from poolhouse.fleet.session import COOKIE
        cookie = COOKIE + "=not-a-session"
    elif kind == "expired":
        cookie = sign_in(device)
        device.ui.sessions.get(parse_cookie(cookie)).expires_at = 0
    elif kind == "uncredentialed":
        held = device.ui.sessions.open("browser")
        cookie = device.ui.sessions.cookie_header(held).split(";")[0]
    status, response, headers = device.call('/ui/setup/initial', method='POST',
                                            headers=browser_headers(device), cookie=cookie,
                                            body={'name': 'quillhaven'})
    assert status == (403 if kind == "uncredentialed" else 401)
    assert response['error'] and 'run:' not in response['error']
    assert 'Set-Cookie' not in headers
    assert device.ui.name == "studio"
