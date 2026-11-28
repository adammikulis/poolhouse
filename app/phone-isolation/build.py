"""Build the isolation prototype's APK with the SDK's and NDK's own tools (no Gradle).

    python app/phone-isolation/build.py --out DIR [--engine DIR] [--guest DIR]

``--out`` is outside the checkout. Without ``--engine`` and ``--guest`` the APK holds the probe alone; with them it
also carries the virtual machine engine (engine.py) and the guest's kernel and first disk (guest.py), every one of
them under ``lib/arm64-v8a`` with a ``lib*.so`` name, which is what makes Android unpack it into the one app folder a
process may execute from. The APK is debuggable and signed with a key made once in ``--out``: it is a prototype.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ABI = "arm64-v8a"
API = 33


class BuildError(RuntimeError):
    """A build that cannot go on; the message says why."""


def newest(folder: Path) -> Path:
    """The entry of ``folder`` with the highest version number in its name."""
    def version(path: Path) -> list[int]:
        return [int(part) for part in path.name.removeprefix("android-").split(".") if part.isdigit()]

    found = sorted(folder.glob("*"), key=version)
    if not found:
        raise BuildError(f"{folder} holds nothing")
    return found[-1]


def packaged(engine: Path | None, guest: Path | None) -> list[Path]:
    """The extra files an APK carries beside the compiled ones. Each must be named so that Android unpacks it."""
    found = []
    for folder in (engine, guest):
        if folder is not None:
            found.extend(sorted(path for path in folder.iterdir() if path.is_file()))
    for path in found:
        if not (path.name.startswith("lib") and path.name.endswith(".so")):
            raise BuildError(f"{path.name} would stay inside the APK: a file there is named lib*.so")
    return found


def run(*command: object) -> None:
    subprocess.run([str(word) for word in command], check=True)


def compile_native(sdk: Path, into: Path) -> list[Path]:
    """The probe program and the library that starts programs, for the phone's processor."""
    toolchain = newest(sdk / "ndk") / "toolchains" / "llvm" / "prebuilt"
    clang = newest(toolchain) / "bin" / f"aarch64-linux-android{API}-clang"
    into.mkdir(parents=True, exist_ok=True)
    common = ("-O2", "-Wall", "-Wextra", "-Werror", "-Wl,-z,max-page-size=16384")
    run(clang, *common, "-fPIE", "-pie", "-o", into / "libprobe.so", HERE / "native" / "probe.c")
    run(clang, *common, "-shared", "-fPIC", "-o", into / "libspawn.so", HERE / "native" / "spawn.c")
    return [into / "libprobe.so", into / "libspawn.so"]


def build(out: Path, sdk: Path, engine: Path | None = None, guest: Path | None = None) -> Path:
    """The signed APK, built under ``out``."""
    extra = packaged(engine, guest)
    build_tools = newest(sdk / "build-tools")
    platform = newest(sdk / "platforms") / "android.jar"
    work = out / "apk"
    shutil.rmtree(work, ignore_errors=True)
    (work / "classes").mkdir(parents=True)
    unsigned = work / "unsigned.apk"
    run(build_tools / "aapt2", "link", "-o", unsigned, "-I", platform, "--manifest", HERE / "AndroidManifest.xml")
    run("javac", "--release", "17", "-Xlint:-options", "-classpath", platform, "-d", work / "classes",
        *sorted((HERE / "src").rglob("*.java")))
    run(build_tools / "d8", "--min-api", str(API), "--lib", platform, "--output", work,
        *sorted((work / "classes").rglob("*.class")))
    libraries = compile_native(sdk, work / "native") + extra
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED, compresslevel=6) as apk:
        apk.write(work / "classes.dex", "classes.dex")
        for path in libraries:
            apk.write(path, f"lib/{ABI}/{path.name}")
    aligned, signed = work / "aligned.apk", work / "isolation.apk"
    run(build_tools / "zipalign", "-f", "4", unsigned, aligned)
    key = out / "debug.keystore"
    if not key.is_file():
        run("keytool", "-genkeypair", "-keystore", key, "-storepass", "isolation", "-keypass", "isolation",
            "-alias", "isolation", "-keyalg", "RSA", "-keysize", "2048", "-validity", "30", "-dname", "CN=isolation")
    run(build_tools / "apksigner", "sign", "--ks", key, "--ks-key-alias", "isolation", "--ks-pass",
        "pass:isolation", "--out", signed, aligned)
    return signed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="a folder outside the checkout")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("ANDROID_HOME") or Path.home() / "Library/Android/sdk"))
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--guest", type=Path)
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(ROOT):
        parser.error("--out is a folder outside the checkout")
    try:
        sys.stdout.write(f"{build(args.out.resolve(), args.sdk, args.engine, args.guest)}\n")
    except (BuildError, subprocess.CalledProcessError, OSError) as exc:
        sys.stderr.write(f"phone isolation build: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
