"""Test shards under a hostile member: another device's shard, a pool that is not in dev mode, the disk, the settings and the text a device sends back.

Each attack is made against a real daemon over the pool's TLS (`dev_pool_kit`), as a member with a certificate of its own would make it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from dev_pool_kit import (  # noqa: F401  (a fixture)
    Device,
    device,
    real_tree,
    sender,
    serve_pools,
    this_python_runs_shards,
)
from testfarm_tree import make_tree

from poolhouse import http
from poolhouse.fleet import shard_store, tls
from poolhouse.fleet.remote import Member, MemberError
from poolhouse.testfarm import dev, report
from poolhouse.testfarm.client import ShardError

pytestmark = pytest.mark.redteam


@pytest.fixture(autouse=True)
def pins_are_forgotten(this_python_runs_shards):  # noqa: F811
    yield
    http._PINNED.clear()


def as_member(one: Device, who: tls.Identity | None = None) -> Member:
    """The client of ``one``, presenting the certificate ``who`` (this machine's own unless said) from now on."""
    http.pin(f"127.0.0.1:{one.httpd.server_port}", tls.pinned_context(one.ident.beacon, who=who))
    return one.member


def another_member(one: Device, tmp_path: Path, name: str = "other") -> tls.Identity:
    """A device with a certificate of its own that the pool lists."""
    stranger = tls.identity(tmp_path / f"identity-{name}", name)
    one.roster.enrol("lab", stranger.beacon, name, "test")
    return stranger


def until(seconds: float, check) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


def test_a_member_cannot_replace_read_or_stop_the_shard_of_another_while_it_waits_for_a_slot(tmp_path):
    pids = tmp_path / "pids"
    with device(tmp_path) as one:
        owner = sender(one)
        holder = owner.send(one.fingerprint, make_tree(tmp_path / "hang-tree", pids), "all", ["tests/test_hang.py"], 600)
        assert until(60, pids.exists)
        queued = owner.send(one.fingerprint, real_tree(tmp_path / "tree"), "all", ["tests/test_pass.py"], 120)
        assert one.member.shard_state(queued)["state"] == "queued", "the one slot is held, so the shard waits"
        attacker = as_member(one, another_member(one, tmp_path))
        local = tmp_path / "evil.tgz"
        local.write_bytes(b"x")
        header = {"id": queued, "tree_sha256": "0" * 64, "tier": "all", "files": [], "timeout_s": 60}
        for call in (
            lambda: attacker.push(local, f"shards/{queued}/tree.tgz"),
            lambda: attacker.pull(f"shards/{queued}/spec.json", tmp_path / "read"),
            lambda: attacker.shard_upload(queued, b"evil"),
            lambda: attacker.shard_start(header),
            lambda: attacker.shard_state(queued),
            lambda: attacker.shard_cancel(queued),
            lambda: attacker.log(JOB + queued),
            lambda: attacker.stop(JOB + queued),
        ):
            with pytest.raises(MemberError, match=r"40[34]"):
                call()
        as_member(one)
        owner.cancel(one.fingerprint, holder)
        result = owner.wait(one.fingerprint, queued, timeout_s=120, poll_s=0.2)
        assert result["exit"] == 0 and result["files"]["tests/test_pass.py"]["passed"] == 2, "it ran the tree its owner sent"
        assert one.member.shard_state(queued)["job"]["by"].startswith(dev.fingerprint_of(tls.local().beacon))


JOB = "shard-"


def test_a_member_of_a_production_pool_only_cannot_send_to_a_device_that_also_holds_a_pool_in_dev_mode(tmp_path):
    one = serve_pools(tmp_path, "box", {"lab": ("dev", False), "corp": ("prod", True)})
    try:
        corp = one.clients["corp"]
        caps = corp.shard_caps()
        assert caps["accepts"] is False and caps["mode"] == "prod" and "not in dev mode" in caps["reason"]
        with pytest.raises(MemberError, match=r"403.*takes no tests"):
            corp.shard_upload("ab" * 16, b"x")
        with pytest.raises(MemberError, match=r"403.*takes no tests"):
            corp.shard_start({"id": "ab" * 16, "tree_sha256": "0" * 64, "tier": "all", "files": [], "timeout_s": 60})
        with pytest.raises(MemberError, match=r"403.*not a member of that pool"):
            one.clients["lab"].shard_caps()
        assert one.runner.snapshot() == [] and not (one.root / "files" / "shards").exists()
    finally:
        one.stop()


def test_a_tree_larger_than_a_shard_may_carry_or_sent_out_of_order_is_refused(tmp_path):
    with device(tmp_path) as one:
        id_ = "ab" * 16

        def put(offset: int, length: int, total: int):
            span = {"Content-Range": f"bytes {offset}-{offset + length - 1}/{total}"}
            return one.member._request("PUT", f"/shards/v1/{id_}/tree", data=b"x" * length, options=http.Options(headers=span, timeout=30))

        with pytest.raises(MemberError, match=r"400.*Content-Range says how many"):
            put(0, 1, shard_store.MOST_PACKED + 1)
        with pytest.raises(MemberError, match=r"416.*holds 0 bytes"):
            put(5, 1, 10)
        assert put(0, 5, 10)[0] == 200
        with pytest.raises(MemberError, match=r"416.*holds 5 bytes"):
            put(0, 5, 10)
        with pytest.raises(MemberError, match=r"400.*Content-Range disagrees"):
            put(5, 9, 10)
        assert put(5, 5, 10)[0] == 200 and (one.root / "files" / "shards" / id_ / "tree.tgz").stat().st_size == 10
        with pytest.raises(MemberError, match=r"409.*already here"):
            put(0, 5, 10)


def test_a_device_holds_a_bounded_number_of_shards_and_drops_what_nobody_finished(tmp_path, monkeypatch):
    with device(tmp_path) as one:
        def begin(index: int):
            span = {"Content-Range": "bytes 0-0/10"}
            return one.member._request("PUT", f"/shards/v1/{index:032x}/tree", data=b"x", options=http.Options(headers=span, timeout=30))

        for index in range(shard_store.MOST_OWNED):
            assert begin(index)[0] == 200
        with pytest.raises(MemberError, match=r"429.*holds 4 of your shards"):
            begin(99)
        monkeypatch.setattr(shard_store, "IDLE_S", 0.0)
        assert begin(100)[0] == 200, "what was never finished is dropped to make room"
        assert [p.name for p in shard_store.folders(one.root / "files")] == [f"{100:032x}"]
        monkeypatch.setattr(shard_store, "IDLE_S", 600.0)
        monkeypatch.setattr(shard_store, "MOST_KEPT", 2)
        files = one.root / "files"
        live = {f"{n:032x}" for n in (100, 101)}
        assert shard_store.claim(shard_store.folder_of(files, f"{101:032x}"), "someone")
        assert "holds 2 shards already" in shard_store.room(files, "another", live, live), "shards that are running are never dropped to make room"
        assert shard_store.room(files, "another", set(), set()) == "", "finished ones are"


def test_settings_that_cannot_be_read_turn_tests_from_other_devices_off(tmp_path):
    with device(tmp_path) as one:
        assert sender(one).capability(one.fingerprint)["accepts"] is True
        (one.root / "settings.json").write_text("{ this is not json", encoding="utf-8")
        caps = sender(one).capability(one.fingerprint)
        assert caps["accepts"] is False and "turned off" in caps["reason"]
        (one.root / "settings.json").write_text("{}", encoding="utf-8")
        assert sender(one).capability(one.fingerprint)["accepts"] is True


def test_text_a_device_sends_back_cannot_move_the_cursor_or_end_a_line(tmp_path):
    (tmp_path / "evil").mkdir()
    tree = real_tree(tmp_path / "evil" / "tree")
    (tree / "tests" / "test_broken.py").write_text("def test_wrong():\n    print('\\x1b]0;owned\\x07\\x1b[2J')\n    assert False, 'bad \\x1b[31mred'\n", encoding="utf-8")
    lines: list[str] = []
    with device(tmp_path) as one:
        shards = sender(one, lines)
        shard = shards.send(one.fingerprint, tree, "all", ["tests/test_broken.py"], 120)
        result = shards.wait(one.fingerprint, shard, timeout_s=120, poll_s=0.2)
        assert result["exit"] == 1
    assert lines and not any("\x1b" in line or "\x07" in line for line in lines)
    shown = report.show(result, "box")
    assert not any("\x1b" in line for line in shown)
    assert report.plain("a\x1b[2Jb\nc\td") == "a?[2Jb?c\td"
    posted = report.board_line({"name": "bo\nx", "fingerprint": "f" * 64}, "all", 1, result, "tree")
    assert "\n" not in posted and posted.startswith("test-result device=bo?x ")


def test_a_run_whose_device_was_never_listed_says_so(tmp_path):
    with device(tmp_path) as one:
        shards = dev.DevShards({}, say=lambda text: None)
        with pytest.raises(ShardError, match=r"is not a member of this device's pool in dev mode"):
            shards.capability(one.fingerprint)
