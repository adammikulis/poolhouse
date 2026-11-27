"""The daily check for new Unsloth uploads, against a local Hub and a Hub cache in ``tmp_path``. Nothing is downloaded by it."""

import json
import struct

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401 - fixtures

from poolhouse import hub
from poolhouse.fleet import model_uploads
from poolhouse.fleet.models import Models
from poolhouse.hub import uploads
from poolhouse.testing.fakehub import fake_hub

REPO, OTHER = "unsloth/thing-GGUF", "maker/other-GGUF"
HELD = ["thing-Q3.gguf", "MTP/mtp-thing-Q4_0.gguf"]
DAY = 86400


def gguf(size: int, mark: bytes = b"\0") -> bytes:
    return b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + mark * size


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    """A local Hub whose files `HELD` and one other publisher's model are in the cache."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "cache"))
    listed = {name: gguf(100 + at) for at, name in enumerate([*HELD, "thing-Q8.gguf", "mmproj-F16.gguf"])}
    with fake_hub({REPO: listed, OTHER: {"other-Q4.gguf": gguf(50)}}) as server:
        server.point(monkeypatch)
        for name in HELD:
            hub.fetch(f"hf:{REPO}/{name}")
        hub.fetch(f"hf:{OTHER}/other-Q4.gguf")
        server.hub_seen.clear()
        server.cdn_seen.clear()
        yield server


def replace(server, name: str, body: bytes) -> None:
    server.repos[REPO][name] = body
    server.moved[REPO] = server.moved.get(REPO, 0) + 1


def test_each_distinct_change_is_posted_once_and_the_first_listing_counts_as_seen(shelf):
    posted: list[str] = []
    replace(shelf, "thing-Q3.gguf", gguf(100, b"\1"))
    uploads.check(posted.append, now=DAY)
    assert posted == [f"{REPO}: replaced upstream: thing-Q3.gguf (same size, other content)"]
    uploads.check(posted.append, now=2 * DAY)
    assert len(posted) == 1, "what was listed first is seen, and a change already posted is not posted again"

    replace(shelf, "thing-Q9.gguf", gguf(200))
    replace(shelf, "mmproj-F16.gguf", gguf(300))
    replace(shelf, "MTP/mtp-thing-Q4_0.gguf", gguf(400))
    uploads.check(posted.append, now=3 * DAY)
    uploads.check(posted.append, now=4 * DAY)
    assert sorted(line.split(": ", 1)[1].split(" (")[0] for line in posted[1:]) == [
        "new or changed companion: mmproj-F16.gguf", "new upload: thing-Q9.gguf", "replaced upstream: MTP/mtp-thing-Q4_0.gguf"]

    asked = [(method, path, auth) for method, path, _range, auth in shelf.hub_seen]
    assert asked and all(path.startswith(f"/api/models/{REPO}/tree/") and not auth for _method, path, auth in asked)
    assert shelf.cdn_seen == [], "nothing is downloaded"

    rows = {row["repo"]: row for row in Models([hub.hub_cache()], hub.hub_cache()).library()}
    assert len(rows[REPO]["updates"]) == 4 and rows[OTHER]["updates"] == []
    hub.fetch(f"hf:{REPO}/thing-Q3.gguf")
    assert not any("thing-Q3" in change["text"] for change in uploads.current()), "a file fetched since is no longer reported"
    assert (hub.located("thing-Q3.gguf")).read_bytes() == gguf(100, b"\1")


def test_off_asks_nothing_and_a_mirror_is_never_asked(shelf, monkeypatch, capsys):
    replace(shelf, "thing-Q3.gguf", gguf(500))
    assert hub.main(["updates", "--off"]) == 0
    assert uploads.check(pytest.fail) == [] and shelf.hub_seen == [] and uploads.current() == []
    capsys.readouterr()
    assert hub.main(["updates", "--on", "--now"]) == 0
    assert "replaced upstream: thing-Q3.gguf" in capsys.readouterr().out and len(shelf.hub_seen) == 1
    monkeypatch.setattr("poolhouse.hub.publishers.HOSTS", frozenset({"huggingface.co"}))
    assert uploads.check(pytest.fail) == [] and len(shelf.hub_seen) == 1


def test_the_model_list_shows_what_was_found(shelf, capsys):
    from poolhouse.serve import models_cli

    replace(shelf, "thing-Q9.gguf", gguf(200))
    uploads.check(now=DAY)
    replace(shelf, "thing-Q10.gguf", gguf(210))
    uploads.check(now=2 * DAY)
    capsys.readouterr()
    assert models_cli.run(["list"]) == 0
    assert f"{REPO}: new upload: thing-Q10.gguf" in capsys.readouterr().out


def test_the_check_runs_once_a_day_and_a_notice_that_failed_is_offered_again(shelf):
    replace(shelf, "thing-Q3.gguf", gguf(500))

    def refused(_text):
        raise LookupError("no workspace to post to")

    uploads.watch(refused, first_after_s=0, look_s=0.01, rounds=3).join(timeout=20)
    assert len(shelf.hub_seen) == 1
    posted: list[str] = []
    uploads.check(posted.append)
    assert len(posted) == 1
    kept = json.loads((uploads.home.state("hub", "uploads.json")).read_text(encoding="utf-8"))
    assert len(kept["posted"]) == 1


def test_a_notice_is_an_announcement_on_the_workspace_board(workspace_node):  # noqa: F811
    reader = workspace_node.member()
    post = model_uploads.board_notice(lambda: [{"id": workspace_node.board, "available": True, "preferred": True}],
                                      workspace_node.client)
    post(f"{REPO}: new upload: thing-Q9.gguf (224B)")
    said = [entry.text for entry in workspace_node.session(reader).read(channel="#announcements", kind="message").entries]
    assert f"{REPO}: new upload: thing-Q9.gguf (224B)" in said
    with pytest.raises(LookupError):
        model_uploads.board_notice(lambda: [], workspace_node.client)("nowhere to say it")
