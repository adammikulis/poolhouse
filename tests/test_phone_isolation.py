"""The phone isolation prototype's builders (`app/phone-isolation`): what goes into the APK and the guest."""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import struct
import sys
import tarfile
from pathlib import Path

import pytest
from defusedxml import ElementTree

HERE = Path(__file__).resolve().parents[1] / "app" / "phone-isolation"
ANDROID = "{http://schemas.android.com/apk/res/android}"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"phone_isolation_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def elf(needed: list[str], own: str = "") -> bytes:
    """A 64-bit ELF file holding only a load segment, a dynamic section and its string table."""
    strings = b"\0"
    offsets = []
    for name in [*needed, *([own] if own else [])]:
        offsets.append(len(strings))
        strings += name.encode() + b"\0"
    entries = [(1, at) for at in offsets[:len(needed)]] + [(14, at) for at in offsets[len(needed):]]
    start = 64 + 2 * 56
    table = start + 16 * (len(entries) + 3)
    entries += [(5, 0x1000 + table), (10, len(strings)), (0, 0)]
    dynamic = b"".join(struct.pack("<qQ", *entry) for entry in entries)
    total = table + len(strings)
    header = b"\x7fELF\x02\x01\x01" + b"\0" * 9 + struct.pack("<HHIQQQIHHHHHH", 3, 183, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    load_segment = struct.pack("<IIQQQQQQ", 1, 5, 0, 0x1000, 0x1000, total, total, 4096)
    dynamic_segment = struct.pack("<IIQQQQQQ", 2, 6, start, 0x1000 + start, 0x1000 + start, len(dynamic), len(dynamic), 8)
    return header + load_segment + dynamic_segment + dynamic + strings


def test_the_runner_is_isolated_and_closed_to_other_apps():
    root = ElementTree.parse(HERE / "AndroidManifest.xml").getroot()
    (service,) = root.iter("service")
    assert service.get(f"{ANDROID}isolatedProcess") == "true"
    assert service.get(f"{ANDROID}exported") == "false"
    assert root.find("application").get(f"{ANDROID}extractNativeLibs") == "true"
    assert int(root.find("uses-sdk").get(f"{ANDROID}targetSdkVersion")) >= 36
    (activity,) = root.iter("activity")
    assert activity.get(f"{ANDROID}permission") == "android.permission.DUMP"


def test_a_file_android_would_leave_inside_the_apk_is_refused(tmp_path):
    build = load("build")
    (tmp_path / "libengine.so").write_bytes(b"x")
    assert [path.name for path in build.packaged(tmp_path, None)] == ["libengine.so"]
    (tmp_path / "libz.so.1").write_bytes(b"x")
    with pytest.raises(build.BuildError, match=r"lib\*\.so"):
        build.packaged(tmp_path, None)


def test_versioned_library_names_become_installable_without_growing():
    engine = load("engine")
    assert engine.installable("libz.so.1") == "libz_1.so"
    assert engine.installable("libfoo.so.1.2.3") == "libfoo_1_2_3.so"
    assert engine.installable("libplain.so") == "libplain.so"
    with pytest.raises(engine.EngineError):
        engine.installable("ld-linux.so.1")


def test_the_engine_takes_what_the_program_needs_and_renames_it_everywhere():
    engine = load("engine")
    files = {engine.PROGRAM: elf(["libfirst.so.2", "libc.so"]), "libfirst.so.2.0": elf(["libsecond.so"], "libfirst.so.2"),
             "libsecond.so": elf([]), "libunused.so": elf([])}
    found = engine.closure(files, {"libfirst.so.2": "libfirst.so.2.0"})
    assert sorted(found) == ["libfirst.so.2", "libsecond.so", engine.PROGRAM]
    made = engine.installed(found)
    assert sorted(made) == ["libfirst_2.so", "libqemu.so", "libsecond.so"]
    assert engine.needs(made["libqemu.so"]) == ["libfirst_2.so", "libc.so"]
    assert b"libfirst.so.2" not in made["libfirst_2.so"]
    assert len(made["libqemu.so"]) == len(files[engine.PROGRAM])
    with pytest.raises(engine.EngineError, match=r"no package holds libgone\.so"):
        engine.closure({engine.PROGRAM: elf(["libgone.so"])}, {})


def test_a_download_that_differs_from_its_pin_is_refused(tmp_path):
    engine, guest = load("engine"), load("guest")
    (tmp_path / "a.deb").write_bytes(b"changed")
    for module, error in ((engine, engine.EngineError), (guest, guest.GuestError)):
        with pytest.raises(error, match="pinned SHA-256"):
            module.fetched("https://example.invalid/a.deb", "0" * 64, tmp_path)


@pytest.mark.parametrize("name", ["engine.lock.json", "guest.lock.json"])
def test_every_download_is_pinned(name):
    held = json.loads((HERE / name).read_text(encoding="utf-8"))
    entries = [entry for part in held.values() if isinstance(part, list) for entry in part]
    assert entries
    for entry in entries:
        assert entry["url"].startswith("https://")
        assert len(entry["sha256"]) == 64 and int(entry["sha256"], 16) >= 0


def test_guest_packages_resolve_through_what_they_provide():
    guest = load("guest")
    index = "P:app\nV:1\nD:so:libx.so.1 helper>=2 !other\n\nP:xlib\nV:1\np:so:libx.so.1=1.0\n\nP:helper\nV:2\n"
    packages = guest.records(index, "main")
    assert [record["P"] for record in guest.resolve(packages, ("app",))] == ["app", "helper", "xlib"]
    with pytest.raises(guest.GuestError, match="no package provides"):
        guest.resolve(packages, ("missing",))


def test_modules_load_after_the_ones_they_need_and_built_in_ones_are_left_out():
    guest = load("guest")
    modules = {"ext4": b"\0depends=jbd2,mbcache\0", "jbd2": b"\0depends=\0", "mbcache": b"\0depends=\0"}
    assert guest.module_order(modules, ("virtio_blk", "ext4")) == ["jbd2", "mbcache", "ext4"]


def test_the_first_disk_archive_reads_back():
    guest = load("guest")
    data = guest.cpio([("init", 0o100755, b"#!/bin/sh\n", (0, 0)), ("dev/console", 0o020600, b"", (5, 1))])
    at, found = 0, {}
    while True:
        assert data[at:at + 6] == b"070701"
        fields = [int(data[at + 6 + 8 * i:at + 14 + 8 * i], 16) for i in range(13)]
        name = data[at + 110:at + 110 + fields[11] - 1].decode()
        start = (at + 110 + fields[11] + 3) & ~3
        if name == "TRAILER!!!":
            break
        found[name] = (fields[1], data[start:start + fields[6]], fields[9], fields[10])
        at = (start + fields[6] + 3) & ~3
    assert found == {"init": (0o100755, b"#!/bin/sh\n", 0, 0), "dev/console": (0o020600, b"", 5, 1)}


def test_the_system_disk_is_owned_by_root_and_keeps_extras_under_their_prefix(tmp_path):
    guest = load("guest")
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode="w") as archive:
        for name, data in ((".PKGINFO", b"x"), ("usr/bin/tool", b"tool")):
            member = tarfile.TarInfo(name)
            member.size, member.uid = len(data), 501
            archive.addfile(member, io.BytesIO(data))
    extra = tmp_path / "extra.tar"
    with tarfile.open(extra, "w") as archive:
        member = tarfile.TarInfo("source.c")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"c"))
    guest.system_disk([gzip.compress(package.getvalue())], [(extra, "/opt/bench")], tmp_path / "system.img")
    with tarfile.open(tmp_path / "system.img") as made:
        assert made.getnames() == ["usr/bin/tool", "opt/bench/source.c"]
        assert {member.uid for member in made} == {0}
