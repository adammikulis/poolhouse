"""Descriptor metadata remains syscall evidence and fails closed for reuse."""
from __future__ import annotations

import contextlib
import inspect
import json
import os

import pytest
import testreuse_facts as facts
import testreuse_key as keys
import testreuse_plugin as plugin
import testreuse_run as run
import testreuse_store as storage
from test_testreuse import FILE, attempt, project as project_fixture

pytestmark = pytest.mark.slow
project = project_fixture
FSTAT = inspect.unwrap(os.fstat)


@contextlib.contextmanager
def observing(monkeypatch, root):
    with monkeypatch.context() as patch:
        patch.setattr(plugin, "ROOT", root.resolve())
        name = "descriptor-stat-probe"
        plugin.FILES.pop(name, None)
        plugin.CURRENT.append(name)
        try:
            yield plugin._entry(name)
        finally:
            plugin.CURRENT.pop()
            plugin.FILES.pop(name, None)


@pytest.fixture
def opened(project):
    path = project / "input.dat"
    path.write_bytes(b"content")
    modified = path.stat().st_mtime_ns
    os.utime(path, ns=(modified + 3_600_000_000_000, modified))
    fd = os.open(path, os.O_RDONLY)
    try:
        yield path, fd
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def test_full_native_metadata_and_keyword_semantics(project, opened, monkeypatch):
    path, fd = opened
    expected = FSTAT(fd)
    with observing(monkeypatch, project) as seen:
        actual = plugin._probe_fstat(FSTAT)(fd=fd)
        assert facts.stat_fields(actual) == facts.stat_fields(expected)
        assert seen["fd_stats"] == {"input.dat": facts.stat_fields(expected)}
        assert {"st_ino", "st_dev", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
                "st_atime_ns", "st_mtime_ns", "st_ctime_ns"} <= seen["fd_stats"]["input.dat"].keys()
        assert seen["reads"] == seen["stats"] == {"input.dat"}
        assert not seen["unobserved"]
        assert path.is_file()


@pytest.mark.parametrize("change", ["mode", "content", "replace"])
def test_real_descriptor_manifest_invalidates_metadata_and_content(project, opened, monkeypatch, change):
    path, fd = opened
    with observing(monkeypatch, project) as seen:
        plugin._probe_fstat(FSTAT)(fd)
        manifest = keys.build_manifest(project, FILE, seen, keys.Closures(project))
    assert not manifest["unobserved"]
    assert not keys.manifest_holds(project, manifest, keys.Closures(project))
    if change == "mode":
        path.chmod(0o600 if path.stat().st_mode & 0o077 else 0o644)
    elif change == "content":
        path.write_bytes(b"changed content")
    else:
        other = project / "replacement.dat"
        other.write_bytes(path.read_bytes())
        other.replace(path)
    assert keys.manifest_holds(project, manifest, keys.Closures(project))


@pytest.mark.parametrize("attack", ["closed", "reused", "replace", "symlink", "unresolved", "error", "decode"])
def test_descriptor_races_preserve_original_result_and_refuse_cache(project, opened, monkeypatch, attack):
    path, fd = opened
    other = project / "other.dat"
    other.write_bytes(b"other")
    original = FSTAT(fd)
    def resolve(descriptor):
        assert descriptor == fd
        if attack == "closed":
            os.close(fd)
        elif attack == "reused":
            second = os.open(other, os.O_RDONLY)
            try:
                os.dup2(second, fd)
            finally:
                os.close(second)
        elif attack == "replace":
            other.replace(path)
        elif attack == "symlink":
            path.unlink()
            path.symlink_to(other)
        elif attack == "unresolved":
            return ""
        elif attack == "error":
            raise OSError("descriptor lookup unavailable")
        elif attack == "decode":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid filename byte")
        return str(path)
    with observing(monkeypatch, project) as seen:
        monkeypatch.setattr(plugin, "_fd_path", resolve)
        actual = plugin._probe_fstat(FSTAT)(fd)
        assert facts.stat_fields(actual) == facts.stat_fields(original)
        assert seen["unobserved"]
        manifest = keys.build_manifest(project, FILE, seen, keys.Closures(project))
        assert manifest["unobserved"] and keys.manifest_holds(project, manifest, keys.Closures(project))
        assert plugin.BUSY == []


def test_deleted_pipe_and_external_descriptors_never_bind(project, opened, tmp_path, monkeypatch):
    path, fd = opened
    path.unlink()
    read_end, write_end = os.pipe()
    external = tmp_path / "external.dat"
    external.write_bytes(b"outside")
    external_fd = os.open(external, os.O_RDONLY)
    try:
        for descriptor in (fd, read_end, external_fd):
            with observing(monkeypatch, project) as seen:
                assert plugin._probe_fstat(FSTAT)(descriptor) == FSTAT(descriptor)
                assert seen["unobserved"] and not seen["fd_stats"]
    finally:
        os.close(read_end)
        os.close(write_end)
        os.close(external_fd)


def test_no_dev_fd_path_or_replacement_syscall(project, opened, monkeypatch):
    _, fd = opened
    actual_stat = plugin._STAT
    paths = []
    def checked(path):
        paths.append(str(path))
        assert not str(path).startswith("/dev/fd/")
        return actual_stat(path)
    with observing(monkeypatch, project) as seen:
        monkeypatch.setattr(plugin, "_STAT", checked)
        assert plugin._probe_fstat(FSTAT)(fd) == FSTAT(fd)
        assert paths and not seen["unobserved"]


def test_original_exception_identity_and_monkeypatch_restore(project, monkeypatch):
    original = os.fstat
    error = OSError(9, "original descriptor failure")
    calls = []
    def failing(fd):
        calls.append(fd)
        raise error
    with observing(monkeypatch, project) as seen:
        with monkeypatch.context() as patch:
            patch.setattr(os, "fstat", plugin._probe_fstat(failing))
            with pytest.raises(OSError) as caught:
                os.fstat(fd=-1)
            assert caught.value is error and calls == [-1]
            assert seen["unobserved"] == {"fstat failed"}
        assert os.fstat is original and plugin.BUSY == []


def test_probe_busy_and_repeated_metadata_changes_are_uncacheable(project, opened, monkeypatch):
    path, fd = opened
    with observing(monkeypatch, project) as seen:
        plugin.BUSY.append(True)
        try:
            assert plugin._probe_fstat(FSTAT)(fd) == FSTAT(fd)
        finally:
            plugin.BUSY.pop()
        assert seen["unobserved"]
    with observing(monkeypatch, project) as seen:
        wrapped = plugin._probe_fstat(FSTAT)
        wrapped(fd)
        path.chmod(0o600 if path.stat().st_mode & 0o077 else 0o644)
        wrapped(fd)
        assert seen["fd_stats"]["input.dat"] is None and seen["unobserved"]


def test_shared_and_worker_merges_retain_descriptor_conflicts(project, opened, tmp_path, monkeypatch):
    _, fd = opened
    with observing(monkeypatch, project) as seen:
        plugin._probe_fstat(FSTAT)(fd)
        row = {key: sorted(value) if isinstance(value, set) else value for key, value in seen.items()}
    first, second = dict(row), dict(row)
    second["fd_stats"] = {"input.dat": {**row["fd_stats"]["input.dat"], "st_mode": 0}}
    first["unobserved"] = ["shared descriptor uncertainty"]
    folder = tmp_path / "records"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps({"meta": {}, "files": {"*": first, FILE: second}}))
    merged, problem = run.recorded(folder)
    assert not problem
    assert merged[FILE]["fd_stats"]["input.dat"] is None
    assert merged[FILE]["unobserved"] == {"shared descriptor uncertainty"}
    manifest = keys.build_manifest(project, FILE, merged[FILE], keys.Closures(project))
    assert manifest["unobserved"] == "conflicting fstat metadata"


@pytest.mark.parametrize("anonymous", [False, "pipe", "temporary"])
def test_real_pytest_descriptor_result_and_cache_eligibility(project, tmp_path, anonymous):
    (project / "data.bin").write_bytes(b"data")
    (project / "data.bin").chmod(0o644)
    modified = (project / "data.bin").stat().st_mtime_ns
    os.utime(project / "data.bin", ns=(modified + 3_600_000_000_000, modified))
    body = '''import os

def test_descriptor():
    fd = os.open("data.bin", os.O_RDONLY)
    try:
        assert os.fstat(fd=fd).st_size == 4
    finally:
        os.close(fd)
'''
    if anonymous == "pipe":
        body += '''    reader, writer = os.pipe()
    try:
        os.fstat(reader)
    finally:
        os.close(reader)
        os.close(writer)
'''
    elif anonymous == "temporary":
        body += '''    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        directory = os.open(folder, os.O_RDONLY)
        try:
            os.fstat(directory)
        finally:
            os.close(directory)
'''
    (project / FILE).write_text(body)
    store = storage.Store(tmp_path / "cache")
    first, launches = attempt(project, store)
    assert first.status == 0 and launches
    assert bool(first.outcomes[0].stored) is (not bool(anonymous))
    second, launches = attempt(project, store)
    assert second.status == 0
    assert bool(launches) is bool(anonymous)
    if not anonymous:
        (project / "data.bin").chmod(0o600)
        third, launches = attempt(project, store)
        assert third.status == 0 and launches
    else:
        assert "fstat" in first.outcomes[0].detail


def test_indirect_descriptor_is_evaluated_only_by_original_syscall(project, opened, monkeypatch):
    _, fd = opened
    calls = []
    class Indirect:
        def __index__(self):
            calls.append(True)
            return fd
    with observing(monkeypatch, project) as seen:
        assert plugin._probe_fstat(FSTAT)(Indirect()) == FSTAT(fd)
        assert calls == [True] and seen["unobserved"]


def test_metadata_is_rechecked_after_content_hashing(project, opened, monkeypatch):
    path, fd = opened
    with observing(monkeypatch, project) as seen:
        plugin._probe_fstat(FSTAT)(fd)
        manifest = keys.build_manifest(project, FILE, seen, keys.Closures(project))
    original = keys.file_sha
    def hash_and_change_atime(target):
        value = original(target)
        if target == path:
            before = target.stat()
            os.utime(target, ns=(before.st_atime_ns + 1_000_000_000, before.st_mtime_ns))
        return value
    monkeypatch.setattr(keys, "file_sha", hash_and_change_atime)
    assert keys.manifest_holds(project, manifest, keys.Closures(project)) == "descriptor metadata changed: input.dat"


def test_actual_byte_filename_descriptor_is_recorded(project, monkeypatch):
    name = b"records-\n\xe2\x98\x83.bin"
    raw = os.fsencode(project) + b"/" + name
    fd = os.open(raw, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, b"data")
        with observing(monkeypatch, project) as seen:
            native = FSTAT(fd)
            assert plugin._probe_fstat(FSTAT)(fd) == native
            assert seen["fd_stats"] == {os.fsdecode(name): facts.stat_fields(native)}
            assert not seen["unobserved"]
    finally:
        os.close(fd)
