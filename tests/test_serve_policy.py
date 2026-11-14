"""`LocalPolicy`: what a program with no pool and no sentinel trusts a llama-server binary on.

The binaries here are plain files (the policy hashes bytes and reads ``BUILD.json``; it runs nothing), and every
pin file is a real JSON file in the test's own state root.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from poolhouse.files import sha256_file
from poolhouse.serve import binary as binary_module, policy
from poolhouse.serve.binary import BinaryTampered, find_binary
from poolhouse.serve.llamacpp_trust import PoolPolicy
from poolhouse.serve.policy import BinaryRefused, LocalPolicy, PinFileUnreadable


def build(where: Path, content: bytes = b"server one", *, record: bool = True) -> Path:
    """A ``llama-server`` file in ``where``, with the ``BUILD.json`` a build writes beside it."""
    where.mkdir(parents=True, exist_ok=True)
    path = where / "llama-server"
    path.write_bytes(content)
    path.chmod(0o755)
    if record:
        (where / "BUILD.json").write_text(json.dumps({"sha256": sha256_file(path)}))
    return path


def pins(local: LocalPolicy) -> dict:
    return json.loads(local.file.read_text())


def test_a_binary_is_pinned_on_first_use_and_the_pin_file_is_plain_json(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    assert local.problem(server) == ""
    written = pins(local)
    assert written["schema_version"] == 1
    assert written["pins"][str(server)] == {"sha256": sha256_file(server), "source": "first-use", "held": False, "previous": ""}


def test_a_binary_that_matches_its_build_record_is_pinned_as_built(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a")
    assert local.problem(server) == ""
    assert pins(local)["pins"][str(server)]["source"] == "build"


def test_a_binary_that_is_not_the_one_build_json_records_is_refused_and_not_pinned(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a")
    server.write_bytes(b"a different binary")
    assert "BUILD.json records another digest" in local.problem(server)
    assert not local.file.exists()


def test_a_pinned_binary_that_is_replaced_is_refused_and_held_until_it_is_unpinned(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    assert local.problem(server) == ""
    server.write_bytes(b"swapped by someone")
    assert "does not match its pin" in local.problem(server)
    assert local.held(server)
    server.write_bytes(b"server one")   # the original bytes are back, and it is still held
    assert "is held" in local.problem(server)
    local.unpin(server)
    assert not local.held(server)
    assert local.problem(server) == ""


def test_a_rebuilt_binary_whose_build_record_vouches_for_it_is_pinned_again(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a")
    assert local.problem(server) == ""
    build(tmp_path / "a", b"server two")   # `poolhouse-serve build` again: new bytes, new BUILD.json
    assert local.problem(server) == ""
    assert pins(local)["pins"][str(server)]["sha256"] == sha256_file(server)


def test_a_pin_file_with_keys_this_version_does_not_know_still_reads(tmp_path):
    """A file written by a newer version: unknown top-level keys, unknown fields in an entry, an entry that
    is not an entry. The digest still decides, and what it does not understand is ignored, not an error."""
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    local.file.write_text(json.dumps({
        "schema_version": 9, "from_the_future": {"x": 1},
        "pins": {str(server): {"sha256": sha256_file(server), "source": "build", "host": "h:1", "signed": True},
                 "/nowhere": "not an entry", "/also": {"no": "digest"}}}))
    assert local.problem(server) == ""
    server.write_bytes(b"changed")
    assert "does not match its pin" in local.problem(server)


def test_a_pin_file_that_cannot_be_read_refuses_instead_of_forgetting_every_pin(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    local.file.write_text("{not json")
    assert "not a readable pin file" in local.problem(server)
    local.file.write_text(json.dumps({"pins": ["a list"]}))
    assert "not a readable pin file" in local.problem(server)


def test_a_missing_file_is_nothing_to_judge_and_the_environment_passes_through(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    assert local.problem(tmp_path / "absent") == ""
    env = {"PATH": "/bin", "HOME": "/h"}
    assert local.scrub_env(env) == env and local.scrub_env(env) is not env


def test_the_managed_build_is_judged_by_the_policy_it_is_given(tmp_path, monkeypatch):
    current = binary_module.managed_current()
    server = build(current)
    local = LocalPolicy()
    assert find_binary(policy=local) == server.resolve()
    server.write_bytes(b"swapped")
    with pytest.raises(BinaryTampered, match=r"BUILD\.json records another digest"):
        find_binary(policy=local)


def test_with_no_policy_given_the_pool_decides(monkeypatch):
    assert isinstance(policy.default(), PoolPolicy)


def test_a_rebuilt_binary_is_re_pinned_loudly_and_the_pin_it_replaced_is_kept(tmp_path, caplog):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a")
    local.problem(server)
    before = sha256_file(server)
    build(tmp_path / "a", b"server two")
    with caplog.at_level(logging.WARNING):
        assert local.problem(server) == ""
    assert "RE-PINNED" in caplog.text and before in caplog.text
    assert pins(local)["pins"][str(server)]["previous"] == before


def test_a_binary_with_no_build_record_is_pinned_loudly(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        LocalPolicy(tmp_path / "pins.json").problem(build(tmp_path / "a", record=False))
    assert "TRUST ON FIRST USE" in caplog.text


def test_a_pin_file_that_cannot_be_read_is_never_overwritten_by_a_pin_or_an_unpin(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    local.file.write_text("{not json")
    with pytest.raises(PinFileUnreadable):
        local.pin(server, "ab" * 32)
    with pytest.raises(PinFileUnreadable):
        local.unpin(server)
    assert local.file.read_text() == "{not json"


def test_a_held_entry_stays_held_when_its_digest_is_unreadable_and_digests_compare_without_case(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    local.file.write_text(json.dumps({"pins": {str(server): {"sha256": 12, "held": True}}}))
    assert local.held(server) and "is held" in local.problem(server)
    local.file.write_text(json.dumps({"pins": {str(server): {"sha256": sha256_file(server).upper()}}}))
    assert local.problem(server) == ""


def test_the_binary_is_hashed_again_the_instant_before_it_is_executed(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a")
    assert local.problem(server) == ""
    local.before_launch([str(server), "--port", "1"])
    server.write_bytes(b"swapped between the check and the exec")
    with pytest.raises(BinaryRefused, match=r"BUILD\.json records another digest"):
        local.before_launch([str(server)])


def test_in_strict_mode_every_route_to_the_binary_is_judged_and_by_default_only_current_is(tmp_path, monkeypatch):
    server = build(tmp_path / "elsewhere")
    server.write_bytes(b"not what BUILD.json says")
    monkeypatch.setenv("LLAMA_CPP_SERVER", str(server))
    assert find_binary() == server.resolve()    # the pool's default: an explicit route is the caller's own business
    with pytest.raises(BinaryTampered):
        find_binary(policy=LocalPolicy(tmp_path / "pins.json"), strict=True)
    assert find_binary(explicit=server, policy=LocalPolicy(tmp_path / "pins.json")) == server.resolve()
    with pytest.raises(BinaryTampered):
        find_binary(explicit=server, policy=LocalPolicy(tmp_path / "pins.json"), strict=True)
