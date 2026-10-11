"""Hand-edited and planted files reach ``hub.adopt`` and ``hub.derived``: nothing crashes, nothing is moved or removed."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from poolhouse.hub import adopt, derived, hfstore

pytestmark = pytest.mark.redteam

REV = "a" * 40
BODY = struct.pack("<Q", 2) + b"{}" + b"x" * 4
HOSTILE_RECORDS = [
    b"[]", b"null", b'"text"', b"{" * 5000, b'{"sha256": 7, "expected_sha256": 7, "size": "9", "outcome": "kept"}',
    b'{"sha256": "x", "expected_sha256": "x", "size": 10, "outcome": "kept", "headers": []}',
    b'{"sha256": "../../x", "expected_sha256": "../../x", "size": 10, "outcome": "kept", "headers": {"etag": "../../x"}}',
    json.dumps({"sha256": "0" * 64, "expected_sha256": "0" * 64, "size": 10, "outcome": "kept",
                "headers": {"etag": 'W/"' + "g" * 40 + '"'}}).encode(),
]
HOSTILE_INDEXES = [b"[]", b"null", b'{"weight_map": []}', b'{"weight_map": {"t": ["a"]}}', b"{" * 5000,
                   b'{"weight_map": {"../../etc/passwd": "model.safetensors"}}']


@pytest.fixture(autouse=True)
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    return tmp_path / "hub"


def folder_with(tmp_path, files: dict[str, bytes]) -> adopt.Folder:
    for rel, body in files.items():
        (tmp_path / "old" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "old" / rel).write_bytes(body)
    return adopt.Folder("maker/thing", REV, tmp_path / "old")


@pytest.mark.parametrize("record", HOSTILE_RECORDS)
def test_a_hand_edited_download_record_vouches_for_nothing_and_moves_nothing(tmp_path, cache, record):
    folder = folder_with(tmp_path, {"model.safetensors": BODY})
    (tmp_path / "old" / "model.safetensors.provenance.json").write_bytes(record)
    rows = adopt.take(folder, None, apply=True)
    assert [row.action for row in rows] == ["blocked"]
    assert (tmp_path / "old" / "model.safetensors").read_bytes() == BODY and not cache.exists()


@pytest.mark.parametrize("index", HOSTILE_INDEXES)
def test_a_hostile_index_is_not_read_into_a_crash_or_a_path(tmp_path, cache, index):
    folder = folder_with(tmp_path, {"model.safetensors": BODY, "model.safetensors.index.json": index})
    rows = {row.rel: row for row in adopt.take(folder, None, apply=False)}
    assert rows["model.safetensors"].action in ("link", "blocked")
    assert not cache.exists()


def test_a_planted_link_in_the_folder_is_never_followed_or_removed_as_a_file(tmp_path, cache):
    secret = tmp_path / "secret.json"
    secret.write_text("secret")
    folder = folder_with(tmp_path, {"model.safetensors": BODY})
    (tmp_path / "old" / "config.json").symlink_to(secret)
    rows = {row.rel: row for row in adopt.take(folder, None, apply=True)}
    assert rows["config.json"].action == "blocked" and secret.read_text() == "secret"
    assert (tmp_path / "old" / "config.json").is_symlink()


def test_a_planted_link_in_the_cache_that_points_elsewhere_blocks_the_removal(tmp_path, cache):
    folder = folder_with(tmp_path, {"config.json": b'{"a": 1}'})
    decoy = tmp_path / "decoy.json"
    decoy.write_text('{"a": 2}')
    target = hfstore.repo_root("maker/thing") / "snapshots" / REV / "config.json"
    target.parent.mkdir(parents=True)
    target.symlink_to(decoy)
    rows = adopt.take(folder, {"config.json": adopt.remote.RemoteFile("config.json", 8, "", hfstore.git_blob_sha1(
        tmp_path / "old" / "config.json", 8))}, apply=True)
    assert rows[0].action == "blocked" and (tmp_path / "old" / "config.json").exists()
    assert decoy.read_text() == '{"a": 2}'


@pytest.mark.parametrize("record", [b"[]", b"null", b'{"source": []}', b'{"format": "poolhouse-derived/1", "source": 5}',
                                    b'{"format": "poolhouse-derived/1", "recipe": "r", "size": "9"}', b"{" * 5000])
def test_a_planted_provenance_in_a_derived_repository_is_not_believed(tmp_path, cache, record):
    source = derived.Source("maker/drafter", REV, "")
    kept = derived.save(source, "r", lambda stage: stage.write_bytes(BODY))
    kept.parent.joinpath(derived.RECORD).resolve().write_bytes(record)
    assert derived.lookup(source, "r") is None


def test_a_derived_source_name_cannot_climb_out_of_the_cache(tmp_path, cache):
    hostile = derived.Source("local/../../../escape", REV, "")
    kept = derived.save(hostile, "r", lambda stage: stage.write_bytes(BODY))
    assert Path(kept).resolve().is_relative_to(cache.resolve())
