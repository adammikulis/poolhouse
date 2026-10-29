"""Native source keys must preserve file boundaries."""

import errno
import hashlib
import os

import pytest
import testnode


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids newline in a file name")
def test_distinct_file_sets_cannot_share_a_key_via_newlines(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    first = b"first synthetic source"
    second = b"second synthetic source"
    disguised = "foo " + hashlib.sha256(first).hexdigest() + "\nbar"
    (one / disguised).write_bytes(second)
    (two / "foo").write_bytes(first)
    (two / "bar").write_bytes(second)
    assert testnode.key_of(one, [disguised], "rustc fixture") != testnode.key_of(two, ["foo", "bar"], "rustc fixture")


def test_key_has_stable_order_and_distinct_toolchains(tmp_path):
    (tmp_path / "one").write_bytes(b"one")
    (tmp_path / "two").write_bytes(b"two")
    first = testnode.key_of(tmp_path, ["one", "two"], "rustc one")
    assert first == testnode.key_of(tmp_path, ["two", "one"], "rustc one")
    assert first != testnode.key_of(tmp_path, ["one", "two"], "rustc two")
    with pytest.raises(testnode.BuildError, match="duplicate"):
        testnode.key_of(tmp_path, ["one", "one"], "rustc one")


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable mode")
def test_executable_mode_changes_the_key(tmp_path):
    path = tmp_path / "source"
    path.write_bytes(b"source")
    path.chmod(0o600)
    before = testnode.key_of(tmp_path, ["source"], "rustc fixture")
    path.chmod(0o700)
    assert before != testnode.key_of(tmp_path, ["source"], "rustc fixture")


@pytest.mark.skipif(os.name == "nt", reason="POSIX undecodable filenames")
def test_non_utf8_and_control_characters_are_hashed_as_filesystem_bytes(tmp_path):
    raw = os.fsencode(tmp_path) + b"/name-\xff\t"
    try:
        fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        if error.errno in (errno.EILSEQ, errno.EINVAL):
            pytest.skip("filesystem does not support undecodable names")
        raise
    try:
        os.write(fd, b"source")
    finally:
        os.close(fd)
    name = os.fsdecode(b"name-\xff\t")
    assert testnode.key_of(tmp_path, [name], "rustc fixture") != testnode.key_of(tmp_path, [], "rustc fixture")


@pytest.mark.skipif(os.name == "nt", reason="Windows forbids control characters in file names")
def test_tab_in_a_filename_does_not_change_file_boundaries(tmp_path):
    path = tmp_path / "name\tpart"
    path.write_bytes(b"source")
    assert testnode.key_of(tmp_path, [path.name], "rustc fixture") != testnode.key_of(tmp_path, [], "rustc fixture")


def test_old_unframed_keys_are_not_reused(tmp_path):
    (tmp_path / "source").write_bytes(b"source")
    digest = hashlib.sha256(b"source").hexdigest()
    legacy = hashlib.sha256(f"source {digest}\nrustc fixture".encode()).hexdigest()[:testnode.KEY_LENGTH]
    assert testnode.key_of(tmp_path, ["source"], "rustc fixture") != legacy


@pytest.mark.parametrize("name", ["", "/absolute", "../escape", "nested/../escape", "nested//file", "bad\0name"])
def test_invalid_relative_source_names_are_rejected(tmp_path, name):
    with pytest.raises(testnode.BuildError, match="Invalid"):
        testnode.key_of(tmp_path, [name], "rustc fixture")


def test_toolchain_and_each_salt_have_distinct_boundaries(tmp_path):
    (tmp_path / "source").write_bytes(b"source")
    files = ["source"]
    assert testnode.key_of(tmp_path, files, "rustc\nsalt") != testnode.key_of(tmp_path, files, "rustc", salts=("salt",))
    assert testnode.key_of(tmp_path, files, "rustc", salts=("one\ntwo",)) != testnode.key_of(
        tmp_path, files, "rustc", salts=("one", "two"))
