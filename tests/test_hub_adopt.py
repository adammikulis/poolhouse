"""``hub.adopt``: a folder of a repository's files is linked into the hub cache, and only what is vouched for."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from poolhouse import files, http
from poolhouse.hub import adopt, hfstore
from poolhouse.testing.fakehub import fake_hub

HEAD = json.dumps({"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}).encode()
WEIGHTS = struct.pack("<Q", len(HEAD)) + HEAD + bytes(range(8))
CONFIG = b'{"model_type": "tiny"}'
README = b"a small model"
REPO = "maker/thing"
REPOS = {REPO: {"config.json": CONFIG, "model.safetensors": WEIGHTS, "README.md": README,
                "lora/adapter.json": CONFIG}}
REV = "e" * 40


def sha1(body: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(body) + body, usedforsecurity=False).hexdigest()


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(http, "check", lambda url: url)
    with fake_hub(REPOS) as hub_:
        hub_.plain.update({f"{REPO}/config.json", f"{REPO}/README.md"})
        hub_.point(monkeypatch)
        yield hub_


def make_folder(tmp_path, body=None, *, keep=False, name="old") -> adopt.Folder:
    folder = adopt.Folder(REPO, REV, tmp_path / name, keep)
    for rel, data in (body or REPOS[REPO]).items():
        (folder.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder.path / rel).write_bytes(data)
    return folder


def root_of(tmp_path) -> Path:
    return tmp_path / "hub" / "models--maker--thing"


def actions(rows) -> dict[str, str]:
    return {row.rel: row.action for row in rows}


def lay_out_like_hf(tmp_path, rel, body, *, name=None) -> Path:
    """What the cache's own tooling leaves for one file at the folder's revision."""
    blob = root_of(tmp_path) / "blobs" / (name or hashlib.sha256(body).hexdigest())
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(body)
    hfstore.link(root_of(tmp_path) / "snapshots" / REV / rel, blob)
    return blob


def test_a_plan_vouches_for_each_file_and_changes_nothing(server, tmp_path):
    folder = make_folder(tmp_path)
    rows = adopt.take(folder, adopt.listing(folder)[0], apply=False)
    assert actions(rows) == {"config.json": "link", "model.safetensors": "link", "README.md": "link",
                             "lora/adapter.json": "link"}
    assert {row.how for row in rows} == {"the Hub's listing"}
    assert all((folder.path / row.rel).read_bytes() for row in rows) and not root_of(tmp_path).exists()


def test_a_run_links_each_file_by_its_digest_and_removes_the_old_names(server, tmp_path):
    folder = make_folder(tmp_path)
    inode = (folder.path / "model.safetensors").stat().st_ino
    rows = adopt.take(folder, adopt.listing(folder)[0], apply=True)
    root = root_of(tmp_path)
    assert {row.action for row in rows} == {"link"} and not folder.path.exists()
    weights = root / "blobs" / hashlib.sha256(WEIGHTS).hexdigest()
    assert weights.stat().st_ino == inode and weights.read_bytes() == WEIGHTS
    assert (root / "blobs" / sha1(CONFIG)).read_bytes() == CONFIG
    link = root / "snapshots" / REV / "model.safetensors"
    assert link.is_symlink() and link.readlink().as_posix() == f"../../blobs/{weights.name}"


def test_a_folder_that_keeps_its_names_shares_the_bytes_and_is_not_read_again(server, tmp_path, monkeypatch):
    folder = make_folder(tmp_path, keep=True)
    rows = adopt.take(folder, adopt.listing(folder)[0], apply=True)
    assert {row.action for row in rows} == {"link"}
    assert (folder.path / "model.safetensors").stat().st_nlink == 2
    monkeypatch.setattr(files, "sha256_file", lambda *a, **k: pytest.fail("read the bytes again"))
    again = adopt.take(folder, adopt.listing(folder)[0], apply=True)
    assert {row.action for row in again} == {"adopted"}


@pytest.mark.parametrize("keep, action", [(True, "derived"), (False, "blocked")])
def test_a_file_that_is_not_the_hubs_stays_and_is_never_linked(server, tmp_path, keep, action):
    folder = make_folder(tmp_path, {**REPOS[REPO], "config.json": b'{"model_type": "tinz"}',
                                    "ple-store.json": b"{}"}, keep=keep)
    rows = {row.rel: row for row in adopt.take(folder, adopt.listing(folder)[0], apply=True)}
    assert rows["config.json"].action == rows["ple-store.json"].action == action
    assert "differs from the digest" in rows["config.json"].why and "is not a file of" in rows["ple-store.json"].why
    assert (folder.path / "config.json").exists() and (folder.path / "ple-store.json").exists()
    assert not (root_of(tmp_path) / "snapshots" / REV / "config.json").exists()


def test_a_file_of_the_wrong_size_is_refused_before_it_is_read(server, tmp_path, monkeypatch):
    folder = make_folder(tmp_path, {**REPOS[REPO], "model.safetensors": WEIGHTS[:-1]})
    real = files.sha256_file

    def hashed(path, **kw):
        assert Path(path).name != "model.safetensors", "hashed a file of the wrong size"
        return real(path, **kw)

    monkeypatch.setattr(files, "sha256_file", hashed)
    rows = {row.rel: row for row in adopt.take(folder, adopt.listing(folder)[0], apply=True)}
    assert rows["model.safetensors"].action == "blocked" and "the Hub lists" in rows["model.safetensors"].why
    assert (folder.path / "model.safetensors").read_bytes() == WEIGHTS[:-1]


def test_a_file_the_cache_holds_is_a_duplicate_only_when_every_byte_is_the_same(server, tmp_path):
    folder = make_folder(tmp_path)
    kept = lay_out_like_hf(tmp_path, "model.safetensors", WEIGHTS)
    inode = kept.stat().st_ino
    rows = adopt.take(folder, adopt.listing(folder)[0], apply=True)
    assert actions(rows)["model.safetensors"] == "have" and kept.stat().st_ino == inode
    assert not (folder.path / "model.safetensors").exists()


def test_a_cache_file_with_other_bytes_blocks_the_removal_of_the_old_name(server, tmp_path):
    folder = make_folder(tmp_path)
    other = bytearray(WEIGHTS)
    other[-1] ^= 1
    cached = lay_out_like_hf(tmp_path, "model.safetensors", bytes(other), name="b" * 64)
    rows = {row.rel: row for row in adopt.take(folder, adopt.listing(folder)[0], apply=True)}
    assert rows["model.safetensors"].action == "blocked" and "other bytes" in rows["model.safetensors"].why
    assert (folder.path / "model.safetensors").read_bytes() == WEIGHTS and cached.read_bytes() == bytes(other)


def test_a_blob_of_that_name_with_other_bytes_blocks_it_too(server, tmp_path):
    folder = make_folder(tmp_path)
    blob = root_of(tmp_path) / "blobs" / hashlib.sha256(WEIGHTS).hexdigest()
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"x" * len(WEIGHTS))
    rows = {row.rel: row for row in adopt.take(folder, adopt.listing(folder)[0], apply=True)}
    assert rows["model.safetensors"].action == "blocked" and (folder.path / "model.safetensors").exists()


def test_a_run_cut_off_after_linking_is_finished_without_reading_the_bytes_again(server, tmp_path, monkeypatch):
    folder = make_folder(tmp_path)
    blob = root_of(tmp_path) / "blobs" / hashlib.sha256(WEIGHTS).hexdigest()
    blob.parent.mkdir(parents=True)
    os.link(folder.path / "model.safetensors", blob)
    hfstore.link(root_of(tmp_path) / "snapshots" / REV / "model.safetensors", blob)
    monkeypatch.setattr(hfstore, "same_bytes", lambda *a, **k: pytest.fail("compared the bytes again"))
    rows = adopt.take(folder, adopt.listing(folder)[0], apply=True)
    assert actions(rows)["model.safetensors"] == "have" and not (folder.path / "model.safetensors").exists()


def record(body: bytes, *, etag: str = "", kept: bool = True, **more) -> dict:
    digest = hashlib.sha256(body).hexdigest()
    return {"sha256": digest, "expected_sha256": digest, "size": len(body), "outcome": "kept" if kept else "held",
            "headers": {"etag": f'"{etag}"'} if etag else {}, **more}


def write_record(path: Path, held: dict) -> None:
    path.with_name(path.name + ".provenance.json").write_text(json.dumps(held))


def test_a_download_record_vouches_when_the_hub_does_not_answer(server, tmp_path):
    folder = make_folder(tmp_path)
    write_record(folder.path / "model.safetensors", record(WEIGHTS))
    write_record(folder.path / "config.json", record(CONFIG, etag=sha1(CONFIG)))
    rows = {row.rel: row for row in adopt.take(folder, None, apply=True)}
    assert rows["model.safetensors"].action == rows["config.json"].action == "link"
    assert rows["config.json"].how == "its download record"
    root = root_of(tmp_path)
    assert (root / "blobs" / sha1(CONFIG)).read_bytes() == CONFIG
    assert (root / "blobs" / f"{sha1(CONFIG)}.provenance.json").is_file()
    assert (root / "blobs" / f"{hashlib.sha256(WEIGHTS).hexdigest()}.provenance.json").is_file()
    assert rows["README.md"].action == "blocked" and "without the Hub" in rows["README.md"].why


@pytest.mark.parametrize("change", [{"outcome": "held"}, {"size": 1}, {"expected_sha256": "0" * 64},
                                    {"sha256": "0" * 64, "expected_sha256": "0" * 64}])
def test_a_record_that_does_not_match_the_file_vouches_for_nothing(server, tmp_path, change):
    folder = make_folder(tmp_path)
    write_record(folder.path / "model.safetensors", {**record(WEIGHTS), **change})
    rows = {row.rel: row for row in adopt.take(folder, None, apply=True)}
    assert rows["model.safetensors"].action == "blocked"
    assert (folder.path / "model.safetensors").read_bytes() == WEIGHTS


def test_an_object_id_in_a_record_that_the_bytes_do_not_have_vouches_for_nothing(server, tmp_path):
    folder = make_folder(tmp_path)
    write_record(folder.path / "config.json", record(CONFIG, etag="a" * 40))
    rows = {row.rel: row for row in adopt.take(folder, None, apply=True)}
    assert rows["config.json"].action == "blocked" and "object id" in rows["config.json"].why


def test_a_weights_file_with_no_record_is_vouched_for_by_its_structure(server, tmp_path):
    folder = make_folder(tmp_path)
    rows = {row.rel: row for row in adopt.take(folder, None, apply=True)}
    assert rows["model.safetensors"].action == "link" and "structure" in rows["model.safetensors"].how
    assert (root_of(tmp_path) / "blobs" / hashlib.sha256(WEIGHTS).hexdigest()).read_bytes() == WEIGHTS


def test_a_cut_weights_file_is_not_vouched_for_by_its_structure(server, tmp_path):
    folder = make_folder(tmp_path, {**REPOS[REPO], "model.safetensors": WEIGHTS[:-3]})
    rows = {row.rel: row for row in adopt.take(folder, None, apply=True)}
    assert rows["model.safetensors"].action == "blocked" and "outside the data" in rows["model.safetensors"].why
    assert (folder.path / "model.safetensors").exists() and not root_of(tmp_path).exists()


def test_a_shard_that_lacks_a_tensor_its_index_maps_to_it_is_not_vouched_for(server, tmp_path):
    index = json.dumps({"weight_map": {"w": "model.safetensors", "gone": "model.safetensors"}}).encode()
    folder = make_folder(tmp_path, {"model.safetensors": WEIGHTS, "model.safetensors.index.json": index})
    rows = {row.rel: row for row in adopt.take(folder, None, apply=False)}
    assert rows["model.safetensors"].action == "blocked" and "lacks 1 tensors" in rows["model.safetensors"].why


def test_a_half_written_file_and_a_link_are_never_moved(server, tmp_path):
    folder = make_folder(tmp_path, {**REPOS[REPO], "later.safetensors.part": b"x"})
    (folder.path / "alias.json").symlink_to(folder.path / "config.json")
    rows = {row.rel: row for row in adopt.take(folder, adopt.listing(folder)[0], apply=True)}
    assert rows["later.safetensors.part"].action == rows["alias.json"].action == "blocked"
    assert (folder.path / "later.safetensors.part").exists() and (folder.path / "alias.json").is_symlink()


def test_another_volume_is_blocked_in_the_plan_and_in_the_run(server, tmp_path, monkeypatch):
    folder = make_folder(tmp_path)
    monkeypatch.setattr(hfstore, "volume", lambda _path: Path("/dev"))
    rows = adopt.take(folder, adopt.listing(folder)[0], apply=False)
    assert {row.action for row in rows} == {"blocked"} and "another volume" in rows[0].why


def test_a_link_that_fails_across_volumes_leaves_the_file_where_it_is(server, tmp_path, monkeypatch):
    folder = make_folder(tmp_path)

    def across(*_args, **_kw):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", across)
    rows = adopt.take(folder, adopt.listing(folder)[0], apply=True)
    assert {row.action for row in rows} == {"blocked"} and "another volume" in rows[0].why
    assert (folder.path / "model.safetensors").read_bytes() == WEIGHTS


def test_a_folder_with_a_file_left_in_it_is_kept_and_an_emptied_one_goes(server, tmp_path):
    folder = make_folder(tmp_path, {**REPOS[REPO], "odd.txt": b"x"})
    adopt.take(folder, adopt.listing(folder)[0], apply=True)
    assert [p.name for p in folder.path.rglob("*") if p.is_file()] == ["odd.txt"]
    assert not (folder.path / "lora").exists()
    again = make_folder(tmp_path, {**REPOS[REPO], "lora/extra.json": b"x"}, name="again")
    adopt.take(again, adopt.listing(again)[0], apply=True)
    assert [p.name for p in again.path.rglob("*") if p.is_file()] == ["extra.json"]


def test_the_hub_that_does_not_answer_is_named(server, tmp_path, monkeypatch):
    folder = make_folder(tmp_path)
    assert adopt.listing(folder)[0] is not None
    monkeypatch.setenv("HF_ENDPOINT", "http://127.0.0.1:9")
    got, why = adopt.listing(folder)
    assert got is None and "maker/thing could not be read" in why
