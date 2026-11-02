"""Git source objects are checked against their content identities."""

import hashlib
import zlib

import pytest

from poolhouse.net import git


def test_git_blob_reader_rejects_a_header_that_lies_about_content(tmp_path):
    digest = "a" * 40
    git.run(["init", "--object-format=sha1"], cwd=tmp_path)
    obj = tmp_path / ".git" / "objects" / digest[:2] / digest[2:]
    obj.parent.mkdir()
    obj.write_bytes(zlib.compress(b"blob 1\0x"))
    assert git.run(["cat-file", "-t", digest], cwd=tmp_path).stdout.strip() == "blob"
    assert git.run(["cat-file", "-s", digest], cwd=tmp_path).stdout.strip() == "1"
    assert git.run(["cat-file", "-p", digest], cwd=tmp_path).stdout == "x"
    with pytest.raises(git.GitFailed):
        git.blobs([digest], cwd=tmp_path, limit=10, total=10)


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_git_blob_reader_accepts_only_the_repository_object_format(tmp_path, object_format):
    root = tmp_path / object_format
    root.mkdir()
    git.run(["init", f"--object-format={object_format}"], cwd=root)
    data = b"verified source\n"
    object_bytes = f"blob {len(data)}\0".encode() + data
    digest = (hashlib.sha1(object_bytes, usedforsecurity=False).hexdigest() if object_format == "sha1"
              else hashlib.sha256(object_bytes).hexdigest())
    (root / "source.py").write_bytes(data)
    git.run(["add", "source.py"], cwd=root)
    assert git.blobs([digest], cwd=root, limit=100, total=100) == {digest: data}
    wrong = "a" * (64 if object_format == "sha1" else 40)
    with pytest.raises(git.GitFailed, match="repository format"):
        git.blobs([wrong], cwd=root, limit=100, total=100)
