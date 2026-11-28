"""Attacks on the routes that show and remove stray copies of Poolhouse: who may ask, and what a request can make them run."""

from __future__ import annotations

import pytest
from launch_support import signed
from test_fleet_ui import Serving
from test_stray_copies import (  # noqa: F401  (fixtures: a real environment holding a development link)
    PTH,
    made,
    stray,
)

pytestmark = [pytest.mark.redteam]


@pytest.fixture
def window(stray, tmp_path):  # noqa: F811
    server = signed(Serving(tmp_path))
    try:
        yield server, stray[0]
    finally:
        server.close()


def own(server) -> dict[str, str]:
    return {"Origin": f"http://127.0.0.1:{server.port}", "Sec-Fetch-Site": "same-origin"}


@pytest.mark.parametrize("extra", [{"Authorization": "Bearer x"}, {"X-Poolhouse-Agent": "claude-1"}, {"X-Poolhouse-Token": "x"},
                                   {"Sec-Fetch-Site": "same-site"}, {"Origin": "null"}, {"Host": "evil.invalid"}])
def test_nothing_but_the_persons_own_window_reads_or_removes(window, extra):
    server, packages = window
    headers = {**own(server), **extra}
    assert server.call("/ui/stray-copies", headers=headers)[0] in (401, 403)
    assert server.call("/ui/stray-copies/remove", method="POST", body={"id": "x"}, headers=headers)[0] in (401, 403)
    assert (packages / PTH).exists()


@pytest.mark.parametrize("body", [{}, {"id": 1}, {"id": "x", "command": ["rm", "-rf", "/"]}, {"command": "id"}, ["x"],
                                  {"id": "../../etc"}, {"id": "; touch pwned"}, {"id": "x" * 5000}])
def test_a_request_names_only_a_copy_the_server_found_and_nothing_it_runs(window, body):
    server, packages = window
    status = server.call("/ui/stray-copies/remove", method="POST", body=body, headers=own(server))[0]
    assert status in (400, 409), status
    assert (packages / PTH).exists()
    assert server.call("/ui/stray-copies", headers=own(server))[0] == 200, "the handler still answers"


def test_a_removal_cannot_be_asked_by_a_get(window):
    server, packages = window
    assert server.call("/ui/stray-copies/remove", headers=own(server))[0] == 403
    assert (packages / PTH).exists()
