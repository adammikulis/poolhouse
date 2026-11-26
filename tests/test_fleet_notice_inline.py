"""The experimental notice is text on the page, not a dialog: nothing in the fleet interface waits for a person to accept it."""

from __future__ import annotations

from pathlib import Path

from poolhouse.fleet import page, routes, settings

WEB = Path(page.__file__).parent / "web"


def test_the_page_carries_the_notice_as_text_and_no_dialog_or_accept_button():
    html = page.render()
    assert "Poolhouse is experimental" in html and "Report bugs on GitHub" in html
    assert "experimental-notice" not in html and "experimental-accept" not in html
    assert not (WEB / "components" / "experimental-notice.html").exists()


def test_no_route_takes_an_acceptance_and_no_setting_holds_one():
    served = [p for p in Path(routes.__file__).parent.rglob("*") if p.suffix in {".py", ".html"} and "/ui/notice" in p.read_text(encoding="utf-8")]
    assert served == []
    assert "experimental_notice_accepted" not in settings.Settings.__dataclass_fields__
    assert not hasattr(settings.Settings, "accept_notice")
