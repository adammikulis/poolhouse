"""``/ui/room``: the page's route for sizing the wiring limit. It reads and never changes the limit.

A real daemon on a real socket; the machine's facts are the ones the hooks name.
"""

from __future__ import annotations

import platform

import pytest
from conftest import write_gguf
from test_fleet_ui import Serving
from test_wired import dense

from poolhouse.serve import wired


@pytest.fixture(autouse=True)
def an_isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(platform, "system", lambda: "Darwin")


@pytest.fixture
def room(tmp_path):
    s = Serving(tmp_path)
    s.ui.room_hooks = wired.Hooks(total=128 * 1024**3, system="Darwin",
                                  read=lambda: 98304, daemon=tmp_path / "LaunchDaemons" / "d.plist")
    try:
        yield s
    finally:
        s.close()


def test_the_page_carries_the_memory_panel_on_the_models_and_settings_screens(room):
    from poolhouse.fleet.page import render

    page = render()
    assert page.count("<wired-memory>") == 2 and "Device memory controls" in page and "GPU memory" in page
    assert 'customElements.define("wired-memory"' in page
    assert "Keep this after restart" not in page and "asks for your password" not in page
    assert room.call("/ui/")[0] == 200


@pytest.mark.parametrize("path", ["/ui/room/apply", "/ui/room/reset", "/ui/room/keep"])
def test_there_is_no_route_that_changes_the_limit(room, path):
    status, _, _ = room.call(path, method="POST", body={"mb": 65536})
    assert status == 404
    assert room.call("/ui/room")[1]["machine"]["live_mb"] == 98304


def test_a_model_name_with_shell_text_is_only_ever_a_lookup(room):
    status, body, _ = room.call("/ui/room?model=%24%28touch%20x%29%3B%20rm%20-rf%20~&ctx=8192")
    assert status == 400 and "not installed" in body["error"]


def test_a_path_that_is_not_an_installed_model_is_never_read(room, tmp_path):
    path = write_gguf(tmp_path / "m-Q4_K_M.gguf", dense())
    status, body, _ = room.call(f"/ui/room?model={path}&ctx=8192")
    assert status == 400 and "not installed" in body["error"]
    status, body, _ = room.call("/ui/room?model=/etc/hosts&ctx=8192")
    assert status == 400 and "not installed" in body["error"]


def test_the_plan_for_an_installed_model_comes_back_as_plain_numbers(room, tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_MODEL_PATHS", str(tmp_path))
    path = write_gguf(tmp_path / "m-Q4_K_M.gguf", dense())
    with path.open("ab") as f:
        f.truncate(2 * 1024**3)
    status, body, _ = room.call(f"/ui/room?model={path}&ctx=16384&kv=q8_0&mtp=1")
    assert status == 200
    plan = body["plan"]
    assert plan["context"] == 16384 and plan["kv"] == "q8_0" and plan["table"]
    assert {g["where"] for g in plan["grows"]} == {"host RAM"}
    status, _, _ = room.call(f"/ui/room?model={path}&kv=q5_0")
    assert status == 400
