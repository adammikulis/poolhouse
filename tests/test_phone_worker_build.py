"""The phone worker's build: what its manifest lets reach it, and that only the pinned programs go into the APK.

The APK itself needs the Android SDK and is built by `app/phone-worker/build.py`; these checks need neither.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree

ROOT = Path(__file__).resolve().parents[1]
ANDROID = "{http://schemas.android.com/apk/res/android}"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("phone_worker_build", ROOT / "app" / "phone-worker" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(build, release: bool) -> Any:
    return ElementTree.fromstring(build.manifest(release))


def services(found: Any) -> dict[str, tuple[str, str]]:
    return {s.get(ANDROID + "name"): (s.get(ANDROID + "exported"), s.get(ANDROID + "permission"))
            for s in found.iter("service")}


def test_only_an_app_signed_with_the_same_key_reaches_a_release_and_the_shell_only_a_debug_build(build):
    release, debug = manifest(build, True), manifest(build, False)
    assert release.get("package") == "app.poolhouse.worker" and debug.get("package") == "app.poolhouse.worker.nodetest"
    assert services(release) == {"app.poolhouse.worker.WorkerService": ("true", "app.poolhouse.worker.CONTROL")}
    assert services(debug) == {"app.poolhouse.worker.WorkerService": ("true", "app.poolhouse.worker.nodetest.CONTROL"),
                               "app.poolhouse.worker.DebugWorkerService": ("true", "android.permission.DUMP")}
    for found in (release, debug):
        (declared,) = found.findall("permission")
        assert declared.get(ANDROID + "protectionLevel") == "signature"
        for part in ("receiver", "provider", "activity"):
            assert not list(found.iter(part)), "nothing else is a way in"
    assert release.find("application").get(ANDROID + "debuggable") == "false"
    assert "DebugWorkerService.java" not in [p.name for p in build.sources(True)]
    assert "DebugWorkerService.java" in [p.name for p in build.sources(False)]


def test_the_app_asks_for_no_storage_keeps_no_backup_and_may_run_what_it_built(build):
    found = manifest(build, False)
    asked = {p.get(ANDROID + "name") for p in found.iter("uses-permission")}
    assert asked == {f"android.permission.{name}" for name in ("INTERNET", "ACCESS_NETWORK_STATE", "FOREGROUND_SERVICE", "WAKE_LOCK")}
    assert found.find("application").get(ANDROID + "allowBackup") == "false"
    assert found.find("uses-sdk").get(ANDROID + "targetSdkVersion") == "28", "29 and later may not execute the app's own files"


def package(files: dict[str, bytes]) -> bytes:
    """A Debian package (`ar` with a `data.tar.xz`) holding ``files`` under the Termux prefix."""
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w:xz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(f"./data/data/com.termux/files/usr/{name}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    parts = [b"!<arch>\n"]
    for name, content in (("debian-binary", b"2.0\n"), ("data.tar.xz", inner.getvalue())):
        parts.append(f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(content):<10}`\n".encode() + content + b"\n" * (len(content) % 2))
    return b"".join(parts)


def test_a_program_goes_in_only_from_the_pinned_package(build):
    data = package({"bin/proot": b"program", "bin/other": b"not wanted"})
    pinned = hashlib.sha256(data).hexdigest()
    assert build.package_files(data, pinned, {"bin/proot": "bin/proot"}) == {"bin/proot": b"program"}
    changed = package({"bin/proot": b"another program"})
    with pytest.raises(build.BuildError, match="pinned SHA-256"):
        build.package_files(changed, pinned, {"bin/proot": "bin/proot"})
    for path, digest, _ in build.PACKAGES:
        assert path.endswith("_aarch64.deb") and len(digest) == 64


def test_the_guest_scripts_the_app_starts_are_the_ones_the_build_packs(build):
    java = "".join(path.read_text(encoding="utf-8") for path in build.sources(False))
    for name in ("setup.sh", "node.sh"):
        assert f"/opt/worker/{name}" in java and name in build.GUEST
    assert sorted(p.name for p in (ROOT / "app" / "phone-worker" / "guest").iterdir()) == sorted(build.GUEST)
