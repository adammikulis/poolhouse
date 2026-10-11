"""Derived files (quantized drafters) are kept in the hub cache under a ``poolhouse-derived`` repository."""

from __future__ import annotations

import errno
import hashlib
import json
import os

import pytest

from poolhouse import hub
from poolhouse.hub import derived, hfstore

COMMIT = "9" * 40
BODY = bytes(range(256)) * 32
RECIPE = "mlx-q4-g64"


@pytest.fixture(autouse=True)
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    return tmp_path / "hub"


def snapshot_of(cache, repo, commit=COMMIT, file=""):
    path = cache / f"models--{repo.replace('/', '--')}" / "snapshots" / commit
    return path / file if file else path


def source(file="model.safetensors"):
    return derived.Source("maker/drafter", COMMIT, file)


def make(body=BODY):
    def produce(path):
        path.write_bytes(body)
    return produce


def test_a_snapshot_path_is_a_source_by_repository_commit_and_file(cache):
    folder = snapshot_of(cache, "maker/drafter")
    assert derived.source_of(folder) == derived.Source("maker/drafter", COMMIT, "")
    assert derived.source_of(folder / "MTP" / "m.gguf") == derived.Source("maker/drafter", COMMIT, "MTP/m.gguf")


def test_a_folder_outside_the_cache_is_a_source_that_changes_when_its_files_do(tmp_path):
    folder = tmp_path / "my drafter"
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(BODY)
    first = derived.source_of(folder)
    assert first.repo == "local/my-drafter" and len(first.revision) == 40
    assert derived.source_of(folder) == first
    (folder / "model.safetensors").write_bytes(BODY + b"x")
    assert derived.source_of(folder) != first


def test_the_repository_id_and_revision_name_the_source_and_the_recipe():
    one = derived.repo_id(source(), RECIPE)
    assert one == "poolhouse-derived/maker.drafter.mlx-q4-g64" and "--" not in one
    assert derived.repo_id(derived.Source("local/a b", COMMIT), RECIPE) == "poolhouse-derived/local.a-b.mlx-q4-g64"
    revision = derived.revision_of(source(), RECIPE)
    assert hfstore.COMMIT.fullmatch(revision) and revision == derived.revision_of(source(), RECIPE)
    assert len({revision, derived.revision_of(source(), "mlx-q8-g64"),
                derived.revision_of(source("other.safetensors"), RECIPE),
                derived.revision_of(derived.Source("maker/drafter", "8" * 40, "model.safetensors"), RECIPE)}) == 4


def test_a_saved_file_is_in_the_hub_layout_with_its_provenance(cache):
    got = derived.save(source(), RECIPE, make())
    repo = derived.repo_id(source(), RECIPE)
    revision = derived.revision_of(source(), RECIPE)
    root = cache / "models--poolhouse-derived--maker.drafter.mlx-q4-g64"
    blob = root / "blobs" / hashlib.sha256(BODY).hexdigest()
    assert got == snapshot_of(cache, repo, revision, derived.FILE)
    assert got.is_symlink() and got.readlink().as_posix() == f"../../blobs/{blob.name}"
    assert blob.read_bytes() == BODY and (root / "refs" / "main").read_text() == revision
    record = json.loads((got.parent / derived.RECORD).read_text())
    assert record["source"] == {"repo": "maker/drafter", "revision": COMMIT, "file": "model.safetensors"}
    assert (record["recipe"], record["size"], record["sha256"]) == (RECIPE, len(BODY), blob.name)
    assert not (root / ".staging").exists() or not list((root / ".staging").iterdir())


def test_a_saved_file_is_found_again_and_not_produced_twice(cache):
    assert derived.lookup(source(), RECIPE) is None
    got = derived.save(source(), RECIPE, make())
    assert derived.lookup(source(), RECIPE) == got
    assert derived.lookup(source(), "mlx-q8-g64") is None
    assert derived.lookup(source("other"), RECIPE) is None


def test_a_record_that_names_another_source_or_a_cut_file_is_not_found(cache):
    got = derived.save(source(), RECIPE, make())
    record = got.parent / derived.RECORD
    held = json.loads(record.read_text())
    for change in ({"recipe": "other"}, {"source": {"repo": "x/y", "revision": COMMIT, "file": ""}},
                   {"size": len(BODY) + 1}, {"format": "other/1"}):
        blob = record.resolve()
        blob.write_text(json.dumps({**held, **change}))
        assert derived.lookup(source(), RECIPE) is None
    record.resolve().write_text("not json")
    assert derived.lookup(source(), RECIPE) is None


def test_a_producer_that_fails_leaves_nothing(cache):
    def broken(path):
        path.write_bytes(BODY[:10])
        raise RuntimeError("out of memory")

    with pytest.raises(RuntimeError):
        derived.save(source(), RECIPE, broken)
    assert derived.lookup(source(), RECIPE) is None
    assert not list(cache.rglob("*.safetensors")) and not list(cache.rglob("weights.safetensors"))


def test_a_placed_file_moves_in_by_link_and_its_old_name_goes(cache, tmp_path):
    old = tmp_path / "spec" / "dflash" / f"{COMMIT}-4bit.safetensors"
    old.parent.mkdir(parents=True)
    old.write_bytes(BODY)
    inode = old.stat().st_ino
    got = derived.place(source(), RECIPE, old, origin=f"adopted from {old}")
    assert not old.exists() and got.resolve().stat().st_ino == inode
    record = json.loads((got.parent / derived.RECORD).read_text())
    assert record["origin"] == f"adopted from {old}"
    assert derived.lookup(source(), RECIPE) == got


def test_a_placed_file_that_is_already_in_the_cache_is_not_stored_twice(cache, tmp_path):
    first = derived.save(source(), RECIPE, make())
    twin = tmp_path / "twin.safetensors"
    twin.write_bytes(BODY)
    derived.place(derived.Source("maker/drafter", COMMIT, "again"), RECIPE, twin)
    assert not twin.exists()
    blobs = first.parent.parent.parent.joinpath("blobs")
    assert [b.stat().st_size for b in blobs.iterdir()].count(len(BODY)) == 1


def test_a_file_on_another_volume_is_refused_and_kept(cache, tmp_path, monkeypatch):
    old = tmp_path / "old.safetensors"
    old.write_bytes(BODY)

    def across(*_args, **_kw):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", across)
    with pytest.raises(OSError, match="another volume"):
        derived.place(source(), RECIPE, old)
    assert old.read_bytes() == BODY and derived.lookup(source(), RECIPE) is None


def test_a_derived_repository_is_not_listed_as_a_model(cache):
    derived.save(source(), RECIPE, make())
    assert [m.name for m in hub.discover(companions=True)] == []
