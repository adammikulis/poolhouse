"""Package the shared governance verifier for the Android app."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def build(android: Path, workers: int) -> None:
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
    ndks = sorted((sdk / "ndk").iterdir(), key=lambda p: tuple(int(n) for n in p.name.split(".")))
    if not ndks:
        raise RuntimeError("The Android SDK needs its native development kit to build pool verification.")
    toolchain = ndks[-1] / "toolchains/llvm/prebuilt" / ("darwin-x86_64" if sys.platform == "darwin" else "linux-x86_64") / "bin"
    compiler = toolchain / "aarch64-linux-android30-clang"
    target = android / "build/native-target"
    environment = {**os.environ, "CARGO_TARGET_DIR": str(target), "CARGO_BUILD_JOBS": str(workers),
                   "CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER": str(compiler), "CC_aarch64_linux_android": str(compiler),
                   "AR_aarch64_linux_android": str(toolchain / "llvm-ar")}
    subprocess.run(["cargo", "build", "--locked", "--release", "-p", "poolhouse-phone-governance", "--target", "aarch64-linux-android"],
                   cwd=android.parent, env=environment, check=True)
    destination = android / "build/native-jni/arm64-v8a"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target / "aarch64-linux-android/release/libpoolhouse_phone_governance.so", destination / "libpoolhouse_phone_governance.so")
