"""app/android/build.py: it names exactly what a computer lacks, never installs an SDK package or accepts a licence, and runs only
a Gradle download that matches the pinned checksum."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
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


def test_the_build_keeps_its_caches_and_debug_key_in_its_folder_unless_moved(tmp_path):
    env = build.environment({"ANDROID_SDK_ROOT": "/old"}, tmp_path / "sdk", tmp_path / "jdk", tmp_path / "folder")
    assert env["GRADLE_USER_HOME"] == str(tmp_path / "folder" / "gradle-home")
    assert env["ANDROID_USER_HOME"] == str(tmp_path / "folder" / "android-home")
    assert env["ANDROID_HOME"] == str(tmp_path / "sdk") and "ANDROID_SDK_ROOT" not in env
    moved = build.environment({"GRADLE_USER_HOME": str(tmp_path / "mine")}, tmp_path / "sdk", tmp_path / "jdk", tmp_path / "folder")
    assert moved["GRADLE_USER_HOME"] == str(tmp_path / "mine")


def test_a_build_with_nothing_said_keeps_its_files_in_the_cache_folder_and_never_in_the_state_folder(tmp_path, monkeypatch):
    """The run of `main` itself, stopped where it would fetch Gradle: the folder it would fill is outside every state folder."""
    user, state = tmp_path / "user", tmp_path / "user" / ".poolhouse"
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("POOLHOUSE_HOME", str(state))
    for name in ("XDG_CACHE_HOME", "LOCALAPPDATA", "GRADLE_USER_HOME", "ANDROID_USER_HOME"):
        monkeypatch.delenv(name, raising=False)
    stores: list[Path] = []

    def stop_here(env, store):
        stores.append(store)
        raise build.Missing("stop here")

    monkeypatch.setattr(build, "find_sdk", lambda candidates: tmp_path / "sdk")
    monkeypatch.setattr(build, "find_java", lambda env: tmp_path / "jdk")
    monkeypatch.setattr(build, "find_gradle", stop_here)
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert build.main(["debug"]) == 3
    assert stores == [(user / ".cache" / "poolhouse-android" / "gradle").resolve()]
    assert not stores[0].is_relative_to(state.resolve()) and not state.exists()


def test_a_folder_inside_the_state_folder_is_refused_however_it_is_named(tmp_path):
    state = tmp_path / ".poolhouse"
    assert build.build_home(None, {"XDG_CACHE_HOME": str(tmp_path / "xdg")}, tmp_path, [state]) == (tmp_path / "xdg" / "poolhouse-android").resolve()
    assert build.default_home({"LOCALAPPDATA": str(tmp_path / "local")}, tmp_path) == tmp_path / "local" / "poolhouse-android"
    for asked, env in ((state / "android", {}), (None, {"XDG_CACHE_HOME": str(state)}),
                       (None, {"GRADLE_USER_HOME": str(state / "gradle")}), (tmp_path / "ok", {"ANDROID_USER_HOME": str(state)})):
        with pytest.raises(build.Missing, match="state folder"):
            build.build_home(asked, env, tmp_path, [state])
