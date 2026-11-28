"""Build what node mode needs from the node's own source, with the SDK's and NDK's own tools.

    python app/phone-node/build.py --out DIR [--test-apk]

writes ``DIR/jniLibs/arm64-v8a/libpoolhouse_node.so``, the node cross-built for Android, which the app that holds
node mode packs with this folder's manifest and classes. ``--test-apk`` also writes a small debuggable app,
``app.poolhouse.node.nodetest``, that only the `adb` shell can start: for an emulator, never for release.
``DIR`` is outside the checkout.
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
TARGET = "aarch64-linux-android"
OLDEST = 30
"""The oldest Android the node is built for."""
CURRENT = 37
TEST_PACKAGE = "app.poolhouse.node.nodetest"
LIBRARY = "libpoolhouse_node.so"
JOBS = 3


class BuildError(RuntimeError):
    """A build that cannot go on; the message says why."""


def toolchain(ndk: Path) -> dict[str, str]:
    """The environment that makes cargo build for Android with the NDK at ``ndk``."""
    found = sorted(ndk.glob("toolchains/llvm/prebuilt/*/bin"))
    if not found:
        raise BuildError(f"no NDK compiler under {ndk}")
    clang = str(found[0] / f"{TARGET}{OLDEST}-clang")
    return {"CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER": clang, "CC_aarch64_linux_android": clang,
            "AR_aarch64_linux_android": str(found[0] / "llvm-ar")}


def newest(folder: Path) -> Path:
    """The highest-numbered entry of ``folder`` (an SDK keeps one folder per version)."""
    found = sorted(folder.glob("*"), key=lambda p: [int(n) for n in p.name.removeprefix("android-").split(".") if n.isdigit()])
    if not found:
        raise BuildError(f"nothing is installed under {folder}")
    return found[-1]


def run(*command: object, env: dict[str, str] | None = None) -> None:
    subprocess.run([str(word) for word in command], check=True, env=env)


def library(out: Path, ndk: Path) -> Path:
    """The node, cross-built and stripped into ``out/jniLibs``."""
    target = out / "cargo"
    run("cargo", "build", "--locked", "--release", "-j", JOBS, "--target", TARGET, "-p", "poolhouse-node",
        "--manifest-path", ROOT / "app" / "Cargo.toml", env={**os.environ, **toolchain(ndk), "CARGO_TARGET_DIR": str(target)})
    made = out / "jniLibs" / "arm64-v8a" / LIBRARY
    made.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target / TARGET / "release" / "poolhouse-node", made)
    run(Path(toolchain(ndk)["AR_aarch64_linux_android"]).with_name("llvm-strip"), made)
    return made


def test_sources() -> list[Path]:
    """The test app's classes: the module's own and the relay that is in no other build."""
    return sorted([*(HERE / "src").rglob("*.java"), *(HERE / "test").rglob("*.java")])


def test_manifest() -> str:
    """The test app's manifest: the module's own, named and debuggable, with its service and the relay open to the
    shell alone."""
    text = (HERE / "AndroidManifest.xml").read_text(encoding="utf-8")
    shell_only = 'android:exported="true" android:permission="android.permission.DUMP"'
    for old, new in (
            ("<manifest ", f'<manifest package="{TEST_PACKAGE}" '),
            ("<application ", f'<uses-sdk android:minSdkVersion="{OLDEST}" android:targetSdkVersion="{CURRENT}" />\n'
                              '    <application android:debuggable="true" android:allowBackup="false" '),
            ('android:exported="false"', shell_only),
            ("</application>", f'<service android:name="app.poolhouse.node.TestRelay" {shell_only} />\n    </application>')):
        if text.count(old) != 1:
            raise BuildError(f"the manifest no longer holds exactly one {old!r}")
        text = text.replace(old, new)
    return text


def test_apk(out: Path, sdk: Path, made: Path) -> Path:
    """The test app holding ``made``, signed with a key made once in ``out``."""
    build_tools, platform = newest(sdk / "build-tools"), newest(sdk / "platforms") / "android.jar"
    work = out / "test-apk"
    shutil.rmtree(work, ignore_errors=True)
    (work / "classes").mkdir(parents=True)
    (work / "AndroidManifest.xml").write_text(test_manifest(), encoding="utf-8")
    unsigned, aligned, signed = work / "unsigned.apk", work / "aligned.apk", work / "poolhouse-node-test.apk"
    run(build_tools / "aapt2", "link", "-o", unsigned, "-I", platform, "--manifest", work / "AndroidManifest.xml")
    run("javac", "--release", "11", "-Xlint:-options", "-classpath", platform, "-d", work / "classes", *test_sources())
    run(build_tools / "d8", "--min-api", OLDEST, "--lib", platform, "--output", work, *sorted((work / "classes").rglob("*.class")))
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as apk:
        apk.write(work / "classes.dex", "classes.dex")
        apk.write(made, f"lib/arm64-v8a/{LIBRARY}")
    run(build_tools / "zipalign", "-f", "-P", "16", "4", unsigned, aligned)
    key = out / "debug.keystore"
    if not key.is_file():
        run("keytool", "-genkeypair", "-keystore", key, "-storepass", "nodetest", "-keypass", "nodetest", "-alias", "nodetest",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "30", "-dname", "CN=nodetest")
    run(build_tools / "apksigner", "sign", "--ks", key, "--ks-key-alias", "nodetest", "--ks-pass", "pass:nodetest", "--out", signed, aligned)
    return signed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="a folder outside the checkout")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("ANDROID_HOME") or Path.home() / "Library/Android/sdk"))
    parser.add_argument("--ndk", type=Path, help="default: the newest under the SDK")
    parser.add_argument("--test-apk", action="store_true")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.is_relative_to(ROOT):
        parser.error("--out is a folder outside the checkout")
    try:
        made = library(out, args.ndk or newest(args.sdk / "ndk"))
        sys.stdout.write(f"{test_apk(out, args.sdk, made) if args.test_apk else made}\n")
    except (BuildError, subprocess.CalledProcessError, OSError) as exc:
        sys.stderr.write(f"phone node build: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
