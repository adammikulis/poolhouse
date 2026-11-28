"""Node mode for the one Android app: what it adds to that app, and how its program is built from the node's source.

The program and the test APK need the Android SDK and NDK (`app/phone-node/build.py`); these checks need neither.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree

ROOT = Path(__file__).resolve().parents[1]
ANDROID = "{http://schemas.android.com/apk/res/android}"
NEEDED = ("INTERNET", "ACCESS_NETWORK_STATE", "FOREGROUND_SERVICE", "FOREGROUND_SERVICE_SPECIAL_USE", "WAKE_LOCK")


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("phone_node_build", ROOT / "app" / "phone-node" / "build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the import system's own recipe: a dataclass looks its module up while it is made
    spec.loader.exec_module(module)
    return module


def components(found: Any) -> list[Any]:
    return [part for kind in ("service", "receiver", "provider", "activity") for part in found.iter(kind)]


def test_node_mode_adds_no_way_in_to_the_app_that_holds_it(build):
    found = ElementTree.fromstring((ROOT / "app" / "phone-node" / "AndroidManifest.xml").read_text(encoding="utf-8"))
    assert {p.get(ANDROID + "name") for p in found.iter("uses-permission")} == {f"android.permission.{n}" for n in NEEDED}
    (service,) = components(found)
    assert (service.get(ANDROID + "exported"), service.get(ANDROID + "foregroundServiceType")) == ("false", "specialUse")
    assert found.find("application").get(ANDROID + "extractNativeLibs") == "true", "the node is run from the extracted library folder"
    assert found.find("uses-sdk") is None, "the app that holds it chooses the target version, and a current one works"


def test_the_test_apk_targets_a_current_android_and_only_the_shell_reaches_it(build):
    found = ElementTree.fromstring(build.test_manifest())
    assert found.get("package") == "app.poolhouse.node.nodetest"
    assert int(found.find("uses-sdk").get(ANDROID + "targetSdkVersion")) >= 36
    ways_in = {s.get(ANDROID + "name"): (s.get(ANDROID + "exported"), s.get(ANDROID + "permission")) for s in components(found)}
    assert ways_in == {f"app.poolhouse.node.{name}": ("true", "android.permission.DUMP") for name in ("NodeService", "TestRelay")}
    shipped = [path.name for path in (ROOT / "app" / "phone-node" / "src").rglob("*.java")]
    assert "TestRelay.java" not in shipped, "the relay to the node's socket is in the test app alone"
    assert "TestRelay.java" in [path.name for path in build.test_sources()]
    assert found.find("application").get(ANDROID + "debuggable") == "true"
    assert found.find("application").get(ANDROID + "allowBackup") == "false"


def test_the_node_is_cross_built_with_the_ndk_compiler_of_the_oldest_android_it_runs_on(build, tmp_path):
    folder = tmp_path / "toolchains" / "llvm" / "prebuilt" / "some-host" / "bin"
    folder.mkdir(parents=True)
    env = build.toolchain(tmp_path)
    clang = str(folder / f"aarch64-linux-android{build.OLDEST}-clang")
    assert env["CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER"] == env["CC_aarch64_linux_android"] == clang
    assert env["AR_aarch64_linux_android"] == str(folder / "llvm-ar")
    with pytest.raises(build.BuildError, match="no NDK"):
        build.toolchain(tmp_path / "nowhere")
