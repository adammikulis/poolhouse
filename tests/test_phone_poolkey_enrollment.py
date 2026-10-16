"""Compile literal Android sources and check enrollment binding without Android services."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHONE = ROOT / "app/android/src/main/java/app/poolhouse/phone"
JDK = Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home")
SDK = Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk")))
DEPENDENCIES = Path.home() / ".gradle/caches/modules-2/files-2.1"


def test_phone_enrollment_binding_and_literal_android_compile(tmp_path):
    environment = {**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    output = tmp_path / "protocol"
    output.mkdir()
    test = ROOT / "app/android/src/test/java/app/poolhouse/phone/PoolKeyEnrollmentTest.java"
    common = [str(JDK / "bin/javac"), "-J-XX:ActiveProcessorCount=1", "--release", "17", "-d", str(output)]
    compiled = subprocess.run([*common, str(PHONE / "Hex.java"), str(PHONE / "PoolKeyEnrollment.java"), str(test)],
                              capture_output=True, text=True, env=environment, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(JDK / "bin/java"), "-XX:ActiveProcessorCount=1", "-cp", str(output),
                             "app.poolhouse.phone.PoolKeyEnrollmentTest"],
                            capture_output=True, text=True, env=environment, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "66 enrollment binding checks passed" in result.stdout
    zxing = sorted((DEPENDENCIES / "com.google.zxing/core/3.5.3").glob("*/*.jar"))
    assert len(zxing) == 1, "Install the declared Android dependencies before compiling."
    platform = SDK / "platforms/android-36/android.jar"
    assert platform.is_file(), "Install the declared Android SDK before compiling."
    # Compile the shipped sources but never load Android Keystore or start an Activity.
    literal = subprocess.run([*common, "-cp", os.pathsep.join(map(str, [platform, zxing[0]])),
                              *map(str, sorted(PHONE.glob("*.java")))], capture_output=True, text=True,
                             env=environment, timeout=90)
    assert literal.returncode == 0, literal.stdout + literal.stderr
