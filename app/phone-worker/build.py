"""Build the phone worker APK with the SDK's own tools (no Gradle, no downloaded plugin).

    python app/phone-worker/build.py --out DIR [--release --keystore FILE --alias NAME]

The debug build is `app.poolhouse.worker.nodetest`, signed with a key made once in ``DIR``; it alone carries the
service a computer with `adb` access may start. ``DIR`` is outside the checkout. proot and its two libraries are
fetched once from the Termux package archive, each pinned by SHA-256 (GPL-2.0, LGPL-3.0 and BSD-3-Clause programs
the APK carries and runs as separate processes).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = "app.poolhouse.worker"
DEBUG_SUFFIX = ".nodetest"
ARCHIVE = "https://packages.termux.dev/apt/termux-main/pool/main/"
PREFIX = "./data/data/com.termux/files/usr/"
PACKAGES = (
    ("p/proot/proot_5.1.107.102_aarch64.deb", "10a394b585c2fc2b2c8502544f4e5f1be581dc8bfff79e8ba62f16207423e030",
     {"bin/proot": "bin/proot", "libexec/proot/loader": "bin/loader"}),
    ("libt/libtalloc/libtalloc_2.5.0_aarch64.deb", "556591f43bb773ad8777e1a29522640866a55f95dab71914418b94a8c58ad5a7",
     {"lib/libtalloc.so.2.5.0": "lib/libtalloc.so.2"}),
    ("liba/libandroid-shmem/libandroid-shmem_0.7_aarch64.deb", "0da3a24d558b93c92bcf8d611e0826a99ff96e396b148e6cdf33b47c47c57ff6",
     {"lib/libandroid-shmem.so": "lib/libandroid-shmem.so"}),
)
GUEST = ("env.sh", "node.py", "node.sh", "setup.sh")
DEBUG_SERVICE = ('<service android:name="app.poolhouse.worker.DebugWorkerService" android:exported="true"\n'
                 '            android:permission="android.permission.DUMP" />')


class BuildError(RuntimeError):
    """A build that cannot go on; the message says why."""


def manifest(release: bool) -> str:
    """The manifest of a release or a debug build: only the debug one is debuggable and lets the shell drive it."""
    package = PACKAGE if release else PACKAGE + DEBUG_SUFFIX
    text = (HERE / "AndroidManifest.xml").read_text(encoding="utf-8")
    for name, value in (("package", package), ("debuggable", "false" if release else "true"),
                        ("debugService", "" if release else DEBUG_SERVICE)):
        text = text.replace("${" + name + "}", value)
    return text


def sources(release: bool) -> list[Path]:
    """The Java files of a build; a release leaves the debug service's class out."""
    found = sorted((HERE / "src").rglob("*.java"))
    return [path for path in found if not (release and path.name == "DebugWorkerService.java")]


def ar_member(data: bytes, wanted: str) -> bytes:
    """The member of a Debian package (an `ar` archive) whose name starts with ``wanted``."""
    if not data.startswith(b"!<arch>\n"):
        raise BuildError("a package is not an ar archive")
    at = 8
    while at + 60 <= len(data):
        name = data[at:at + 16].decode("ascii").strip().rstrip("/")
        size = int(data[at + 48:at + 58])
        if name.startswith(wanted):
            return data[at + 60:at + 60 + size]
        at += 60 + size + size % 2
    raise BuildError(f"a package holds no {wanted}")


def package_files(data: bytes, digest: str, wanted: dict[str, str]) -> dict[str, bytes]:
    """The ``wanted`` files of the package ``data`` once it matches ``digest``, under their asset names."""
    if hashlib.sha256(data).hexdigest() != digest:
        raise BuildError("a downloaded package does not match its pinned SHA-256")
    found = {}
    with tarfile.open(fileobj=io.BytesIO(ar_member(data, "data.tar")), mode="r:*") as archive:
        for source, asset in wanted.items():
            handle = archive.extractfile(PREFIX + source)
            if handle is None:
                raise BuildError(f"a package holds no {source}")
            found[asset] = handle.read()
    return found


def assets(cache: Path) -> dict[str, bytes]:
    """Everything the app unpacks: proot, its loader and libraries, and the guest's scripts."""
    cache.mkdir(parents=True, exist_ok=True)
    found = {f"guest/{name}": (HERE / "guest" / name).read_bytes() for name in GUEST}
    for path, digest, wanted in PACKAGES:
        held = cache / Path(path).name
        if not held.is_file():
            with urllib.request.urlopen(ARCHIVE + path, timeout=120) as reply:  # noqa: S310 - a fixed https address, pinned below
                held.write_bytes(reply.read())
        found.update(package_files(held.read_bytes(), digest, wanted))
    return found


def tools(sdk: Path) -> tuple[Path, Path]:
    """The newest build tools and platform of the SDK at ``sdk``."""
    def newest(folder: str) -> Path:
        found = sorted((sdk / folder).glob("*"), key=lambda p: [int(n) for n in p.name.removeprefix("android-").split(".") if n.isdigit()])
        if not found:
            raise BuildError(f"the Android SDK at {sdk} has no {folder}")
        return found[-1]
    return newest("build-tools"), newest("platforms") / "android.jar"


def run(*command: object) -> None:
    subprocess.run([str(word) for word in command], check=True)


def build(out: Path, sdk: Path, release: bool, keystore: Path | None, alias: str) -> Path:
    """The signed APK, built under ``out``."""
    build_tools, platform = tools(sdk)
    work = out / ("release" if release else "debug")
    shutil.rmtree(work, ignore_errors=True)
    (work / "classes").mkdir(parents=True)
    (work / "AndroidManifest.xml").write_text(manifest(release), encoding="utf-8")
    for name, data in assets(out / "packages").items():
        (work / "assets" / name).parent.mkdir(parents=True, exist_ok=True)
        (work / "assets" / name).write_bytes(data)
    unsigned = work / "unsigned.apk"
    run(build_tools / "aapt2", "link", "-o", unsigned, "-I", platform, "--manifest", work / "AndroidManifest.xml", "-A", work / "assets")
    run("javac", "--release", "11", "-Xlint:-options", "-classpath", platform, "-d", work / "classes", *sources(release))
    run(build_tools / "d8", "--min-api", "28", "--lib", platform, "--output", work, *sorted((work / "classes").rglob("*.class")))
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as apk:
        apk.write(work / "classes.dex", "classes.dex")
    aligned, signed = work / "aligned.apk", work / "poolhouse-worker.apk"
    run(build_tools / "zipalign", "-f", "4", unsigned, aligned)
    if release:
        if keystore is None or not alias:
            raise BuildError("a release build needs --keystore and --alias (the password is read from ANDROID_SIGNING_PASSWORD)")
        secret = ["--ks-pass", "env:ANDROID_SIGNING_PASSWORD"]
    else:
        keystore, alias, secret = out / "debug.keystore", "nodetest", ["--ks-pass", "pass:nodetest"]
        if not keystore.is_file():
            run("keytool", "-genkeypair", "-keystore", keystore, "-storepass", "nodetest", "-keypass", "nodetest", "-alias", alias,
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "30", "-dname", "CN=nodetest")
    run(build_tools / "apksigner", "sign", "--ks", keystore, "--ks-key-alias", alias, *secret, "--out", signed, aligned)
    return signed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="a folder outside the checkout")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("ANDROID_HOME") or Path.home() / "Library/Android/sdk"))
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--alias", default="")
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(HERE.parents[1]):
        parser.error("--out is a folder outside the checkout")
    try:
        sys.stdout.write(f"{build(args.out.resolve(), args.sdk, args.release, args.keystore, args.alias)}\n")
    except (BuildError, subprocess.CalledProcessError, OSError) as exc:
        sys.stderr.write(f"phone worker build: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
