"""A stray copy of Poolhouse is shown, in the health check and in this computer's window, and only a person's click removes it.

The interpreter holding the copy is a real virtual environment with its own pip; the copy is a development-linked install written the
way pip records one, so the removal the click runs is pip's own.
"""

from __future__ import annotations

import json
import os
import venv

import pytest
from launch_support import signed
from test_fleet_ui import Serving

PTH = "_editable_impl_poolhouse.pth"


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    prefix = tmp_path_factory.mktemp("stray") / "python"
    venv.EnvBuilder(with_pip=True).create(prefix)
    return prefix


@pytest.fixture
def stray(made, tmp_path, monkeypatch):
    """The environment holding a development-linked Poolhouse (and one other package), the only interpreter this machine shows."""
    from poolhouse import devinstall

    packages = next(made.glob("Lib/site-packages" if os.name == "nt" else "lib/python3*/site-packages"))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    info = packages / "poolhouse-0.0.dist-info"
    info.mkdir(exist_ok=True)
    (packages / PTH).write_text(str(checkout / "src") + "\n")
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: poolhouse\nVersion: 0.0\n")
    (info / "direct_url.json").write_text(json.dumps({"dir_info": {"editable": True}, "url": checkout.as_uri()}))
    (info / "RECORD").write_text("".join(f"{name},,\n" for name in (
        PTH, f"{info.name}/METADATA", f"{info.name}/direct_url.json", f"{info.name}/RECORD")))
    (packages / "kept_beside.py").write_text("")
    program = made / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setattr(devinstall, "interpreters", lambda environ=None: [(program, packages)])
    return packages, program, checkout


def test_the_click_removes_only_poolhouse_and_only_from_that_python(stray):
    from poolhouse import devinstall

    packages, program, checkout = stray
    (found,) = devinstall.copies()
    assert found.linked and found.python == str(program) and found.source == str(checkout)
    assert devinstall.remove("no-such-copy") and (packages / PTH).exists(), "an id no fresh look finds removes nothing"
    assert devinstall.remove(found.id) == ""
    assert not (packages / PTH).exists() and not list(packages.glob("poolhouse-*"))
    assert (packages / "kept_beside.py").exists() and (packages / "pip").is_dir()
    assert devinstall.copies() == [] and devinstall.findings() == []


@pytest.mark.slow
def test_the_window_names_the_copy_and_its_python_and_the_button_removes_it(stray, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    packages, program, checkout = stray
    server = signed(Serving(tmp_path))
    server.ui.settings.setup_done = True
    try:
        status, body, _ = server.call("/ui/launch-ticket", method="POST")
        assert status == 200, body
        with playwright.sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.port}/ui/?launch_ticket={body['ticket']}#settings")
            notice = page.locator("stray-copies")
            playwright.expect(notice).to_contain_text(f"{program} runs Poolhouse straight from {checkout}")
            page.set_viewport_size({"width": 390, "height": 800})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "long paths wrap on a phone-sized window"
            assert (packages / PTH).exists(), "showing it changes nothing"
            notice.get_by_role("button", name="Remove this copy").click()
            playwright.expect(notice).to_be_empty()
            assert not (packages / PTH).exists()
            browser.close()
    finally:
        server.close()


def test_this_computers_signed_in_window_sees_the_copy_and_removes_it_and_nothing_else_may(stray, tmp_path):
    packages, program, checkout = stray
    server = signed(Serving(tmp_path))
    own = {"Origin": f"http://127.0.0.1:{server.port}", "Sec-Fetch-Site": "same-origin"}
    try:
        status, shown, _ = server.call("/ui/stray-copies", headers=own)
        assert status == 200 and str(program) in shown["copies"][0]["said"] and str(checkout) in shown["copies"][0]["said"]
        wanted = {"id": shown["copies"][0]["id"]}

        def ask(**more):
            return server.call("/ui/stray-copies/remove", method="POST", body=wanted, **{"headers": own, **more})[0]

        assert ask(headers={"Origin": "https://evil.invalid", "Sec-Fetch-Site": "cross-site"}) == 403
        assert ask(cookie="") in (401, 403) and ask(ui_header=False) == 403
        assert (packages / PTH).exists()
        status, after, _ = server.call("/ui/stray-copies/remove", method="POST", body=wanted, headers=own)
        assert (status, after["copies"], after["error"]) == (200, [], "")
        assert not (packages / PTH).exists()
    finally:
        server.close()
