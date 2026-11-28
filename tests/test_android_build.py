"""app/android/build.py: it names exactly what a computer lacks, never installs an SDK package or accepts a licence, and runs only
a Gradle download that matches the pinned checksum."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("android_build", ROOT / "app" / "android" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load()


def sdk_at(root: Path, *places: str) -> Path:
    for place in places:
        (root / place).mkdir(parents=True)
    return root


def test_a_missing_sdk_package_or_licence_is_named_with_the_command_a_person_runs(tmp_path):
    with pytest.raises(build.Missing, match="No Android SDK"):
        build.find_sdk([tmp_path / "nowhere"])
    sdk = sdk_at(tmp_path / "sdk", "platforms/android-36", "licenses", "build-tools/36.0.0")
    with pytest.raises(build.Missing) as missing:
        build.find_sdk([sdk])
    assert f'sdkmanager "platforms;android-{build.COMPILE_SDK}.0"`' in str(missing.value)
    (sdk / "platforms" / f"android-{build.COMPILE_SDK}.0").mkdir()
    with pytest.raises(build.Missing, match="sdkmanager --licenses"):
        build.find_sdk([sdk])
    (sdk / "licenses" / "android-sdk-license").write_text("accepted by a person\n", encoding="utf-8")
    assert build.find_sdk([tmp_path / "nowhere", sdk]) == sdk
    assert not any(sdk.rglob("package.xml")), "the build writes nothing into the SDK"


def test_the_sdk_named_by_the_environment_is_the_one_used(tmp_path):
    assert build.sdk_candidates({"ANDROID_HOME": "/opt/sdk"}, tmp_path) == [Path("/opt/sdk")]
    assert build.sdk_candidates({}, tmp_path)[0] == tmp_path / "Library" / "Android" / "sdk"


def test_a_java_version_is_read_from_both_forms():
    assert build.java_major('openjdk version "25.0.2" 2026-01-20') == 25
    assert build.java_major('java version "1.8.0_402"') == 8
    assert build.java_major("no java here") == 0


def test_a_download_that_does_not_match_the_pinned_checksum_is_removed(tmp_path):
    archive = tmp_path / "gradle.zip"
    archive.write_bytes(b"not gradle")
    with pytest.raises(build.Missing, match="not the pinned"):
        build.checked(archive, build.GRADLE_SHA256)
    assert not archive.exists()
    archive.write_bytes(b"gradle")
    assert build.checked(archive, hashlib.sha256(b"gradle").hexdigest()) == archive


def test_the_build_keeps_its_caches_and_debug_key_in_the_state_folder_unless_moved(tmp_path):
    env = build.environment({"ANDROID_SDK_ROOT": "/old"}, tmp_path / "sdk", tmp_path / "jdk", tmp_path / "state")
    assert env["GRADLE_USER_HOME"] == str(tmp_path / "state" / "gradle-home")
    assert env["ANDROID_USER_HOME"] == str(tmp_path / "state" / "android-home")
    assert env["ANDROID_HOME"] == str(tmp_path / "sdk") and "ANDROID_SDK_ROOT" not in env
    moved = build.environment({"GRADLE_USER_HOME": str(tmp_path / "mine")}, tmp_path / "sdk", tmp_path / "jdk", tmp_path / "state")
    assert moved["GRADLE_USER_HOME"] == str(tmp_path / "mine")
