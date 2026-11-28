"""``poolhouse-models remove``: one build's files leave a Hugging Face cache laid out in the test's own folder. Shared bytes and
anything outside the cache stay, and a build a running process names is refused. The files are small stand-in bytes, not models.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import activity
from poolhouse.hub import removal
from poolhouse.serve import models_cli

BF16 = "hf:owner/repo/BF16/m-BF16-00001-of-00002.gguf"


@pytest.fixture
def cache(tmp_path, monkeypatch) -> Path:
    if os.name != "posix":
        pytest.skip("the cache is laid out in links")
    top = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(top))
    repo = top / "models--owner--repo"
    (repo / "blobs").mkdir(parents=True)

    def put(commit: str, name: str, digest: str, size: int) -> None:
        blob = repo / "blobs" / digest
        if not blob.exists():
            blob.write_bytes(b"x" * size)
        link = repo / "snapshots" / commit / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(blob, link.parent))

    for commit in ("aaa", "bbb"):
        put(commit, "BF16/m-BF16-00001-of-00002.gguf", "d1", 100)
        put(commit, "BF16/m-BF16-00002-of-00002.gguf", "d2", 50)
    put("aaa", "m-Q4.gguf", "d3", 30)
    put("aaa", "m-copy.gguf", "d3", 30)
    return repo


def test_every_shard_of_the_build_goes_from_every_revision_and_the_other_builds_stay(cache, monkeypatch):
    written: list[tuple[str, str]] = []   # the suite keeps the activity log quiet outside its own tests, so the call is read
    monkeypatch.setattr(activity, "record", lambda kind, **said: written.append((kind, said["subject"])))
    seen = removal.remove(BF16, dry_run=True)
    assert (len(seen.files), seen.bytes) == (4, 150) and (cache / "blobs" / "d1").exists() and not written
    done = removal.remove(BF16)
    assert sorted(path.name for path in done.blobs) == ["d1", "d2"]
    assert sorted(path.name for path in (cache / "blobs").iterdir()) == ["d3"]
    assert not (cache / "snapshots" / "aaa" / "BF16").exists() and not (cache / "snapshots" / "bbb" / "BF16").exists()
    assert (cache / "snapshots" / "aaa" / "m-Q4.gguf").read_bytes() == b"x" * 30
    assert written == [("model.remove", BF16)]


def test_bytes_another_file_still_links_to_stay(cache):
    done = removal.remove("hf:owner/repo/m-Q4.gguf")
    assert done.blobs == () and done.bytes == 0 and (cache / "snapshots" / "aaa" / "m-copy.gguf").read_bytes() == b"x" * 30


def test_nothing_outside_the_cache_is_removed(cache, tmp_path):
    kept = tmp_path / "mine.gguf"
    kept.write_bytes(b"mine")
    (cache / "snapshots" / "aaa" / "linked.gguf").symlink_to(kept)
    removal.remove("hf:owner/repo/linked.gguf")
    assert kept.read_bytes() == b"mine" and not (cache / "snapshots" / "aaa" / "linked.gguf").is_symlink()


@pytest.mark.parametrize("ref", ["hf:owner/repo", "hf:owner/repo/missing.gguf", "hf:owner/repo/../../x.gguf",
                                 "hf:owner/repo:Q4", "hf:owner/other/m.gguf"])
def test_a_reference_that_names_no_file_of_the_cache_removes_nothing(cache, ref):
    with pytest.raises(ValueError):
        removal.remove(ref)
    assert sorted(path.name for path in (cache / "blobs").iterdir()) == ["d1", "d2", "d3"]


def test_a_build_a_running_process_names_stays(cache):
    served = cache / "snapshots" / "aaa" / "BF16" / "m-BF16-00001-of-00002.gguf"
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)", str(served)])
    try:
        with pytest.raises(ValueError, match=f"in use and stays: pid {child.pid}"):
            removal.remove(BF16)
    finally:
        child.kill()
        child.wait()
    assert served.read_bytes() == b"x" * 100


def test_the_models_command_says_what_it_would_remove_then_removes_it(cache, capsys):
    assert models_cli.run(["remove", BF16, "--dry-run", "--json"]) == 0
    [said] = json.loads(capsys.readouterr().out)
    assert said["bytes"] == 150 and not said["removed"] and (cache / "blobs" / "d1").exists()
    assert models_cli.run(["remove", BF16]) == 0
    assert "removed" in capsys.readouterr().out and not (cache / "blobs" / "d1").exists()
