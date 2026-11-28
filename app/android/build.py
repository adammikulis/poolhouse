"""Build and check the Android app under the CPU broker.

    python app/android/build.py            the protocol checks (:core) and the debug APK
    python app/android/build.py test       the protocol checks only
    python app/android/build.py lint       the checks, Android lint and the debug APK
    python app/android/build.py release    the checks and the release APK (ANDROID_SIGNING_STORE and ANDROID_SIGNING_PASSWORD)
    python app/android/build.py sample     the classpath of the sample computer that answers canned data (core/.../SampleDay.kt)
    python app/android/build.py --home DIR ...   keep Gradle, its cache and the debug key in DIR instead

Needed on the computer, and found automatically: an Android SDK with the packages of `PACKAGES` and its licences accepted by a
person, and a JDK 17 or newer. This script never installs an SDK package or accepts a licence; it names what is missing. Gradle
needs no licence: the pinned release is downloaded and checked against its SHA-256 when none is named with GRADLE.

Gradle, its cache and the debug signing key live in the account's cache folder (`~/.cache/poolhouse-android`, on Windows
`%LOCALAPPDATA%\\poolhouse-android`), so a later build can update the app it installed; --home, GRADLE_USER_HOME or
ANDROID_USER_HOME move them. Never into Poolhouse's state folder: it holds thousands of files there, which breaks the programs
that walk the state, so such a place is refused.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

GRADLE_VERSION = "9.8.1"
GRADLE_SHA256 = "dce76f55f8e251a3a1f130eb120f30b3d271de2b76c9b0729d316b5a1b6dc01f"
GRADLE_URL = f"https://services.gradle.org/distributions/gradle-{GRADLE_VERSION}-bin.zip"
COMPILE_SDK = 37
BUILD_TOOLS = "36.0.0"
PACKAGES = {f"platforms;android-{COMPILE_SDK}.0": (f"platforms/android-{COMPILE_SDK}.0", f"platforms/android-{COMPILE_SDK}"),
            f"build-tools;{BUILD_TOOLS}": (f"build-tools/{BUILD_TOOLS}",)}
WORKERS = 3
CHECKS = [":core:test", ":app:testDebugUnitTest"]
TASKS = {"debug": [*CHECKS, ":app:assembleDebug"], "test": CHECKS,
         "lint": [*CHECKS, ":app:lintDebug", ":app:assembleDebug"], "release": [*CHECKS, ":app:assembleRelease"],
         "sample": [":core:sampleClasspath"]}
APK = {"debug": "app/build/outputs/apk/debug/app-debug.apk", "lint": "app/build/outputs/apk/debug/app-debug.apk",
       "release": "app/build/outputs/apk/release/app-release.apk"}


class Missing(Exception):
    """Something the build needs is not on this computer; the message says what and how it gets there."""


def sdk_candidates(env: dict[str, str], user: Path) -> list[Path]:
    named = [env[name] for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if env.get(name)]
    local = env.get("LOCALAPPDATA")
    return [Path(p) for p in named] or [user / "Library" / "Android" / "sdk", *([Path(local) / "Android" / "Sdk"] if local else []),
                                         user / "Android" / "Sdk"]


def find_sdk(candidates: list[Path]) -> Path:
    """The first SDK there is, with every package of `PACKAGES` and an accepted licence."""
    sdk = next((p for p in candidates if (p / "platforms").is_dir() or (p / "licenses").is_dir()), None)
    if sdk is None:
        raise Missing(f"No Android SDK at {', '.join(map(str, candidates))}. Install Android Studio or the command-line tools (a person "
                      "accepts their licence at https://developer.android.com/studio), or set ANDROID_HOME.")
    missing = [name for name, places in PACKAGES.items() if not any((sdk / place).is_dir() for place in places)]
    if missing:
        quoted = " ".join(f'"{name}"' for name in missing)
        raise Missing(f"The Android SDK at {sdk} lacks {', '.join(missing)}. A person runs `sdkmanager {quoted}` and accepts its licence.")
    if not (sdk / "licenses" / "android-sdk-license").is_file():
        raise Missing(f"The licence of the Android SDK at {sdk} is not accepted. A person runs `sdkmanager --licenses`.")
    return sdk


def java_major(text: str) -> int:
    """The major version a `java -version` names, or 0."""
    found = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    if not found:
        return 0
    first = int(found.group(1))
    return int(found.group(2) or 0) if first == 1 else first


def find_java(env: dict[str, str]) -> Path:
    """A JDK home of version 17 or newer: JAVA_HOME, macOS's registry, Android Studio's, then the java on PATH."""
    homes = [env.get("JAVA_HOME")]
    if sys.platform == "darwin":
        answer = subprocess.run(["/usr/libexec/java_home", "-v", "17+"], capture_output=True, text=True, check=False)
        homes.append(answer.stdout.strip() if answer.returncode == 0 else None)
        homes.append("/Applications/Android Studio.app/Contents/jbr/Contents/Home")
    if env.get("ProgramFiles"):
        homes.append(str(Path(env["ProgramFiles"]) / "Android" / "Android Studio" / "jbr"))
    if found := shutil.which("javac", path=env.get("PATH")):
        homes.append(str(Path(found).resolve().parents[1]))
    for home in filter(None, homes):
        javac = Path(home) / "bin" / ("javac.exe" if os.name == "nt" else "javac")
        if javac.is_file():
            said = subprocess.run([str(javac.with_name(javac.name.replace("javac", "java"))), "-version"], capture_output=True, text=True,
                                  check=False)
            if java_major(said.stderr + said.stdout) >= 17:
                return Path(home)
    raise Missing("No JDK 17 or newer was found. Install one (for example Eclipse Temurin) or set JAVA_HOME.")


def checked(archive: Path, sha256: str) -> Path:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != sha256:
        archive.unlink()
        raise Missing(f"The download {archive.name} has the SHA-256 {digest}, not the pinned {sha256}; it was removed.")
    return archive


def find_gradle(env: dict[str, str], store: Path) -> Path:
    """GRADLE when set, else the pinned release in `store`, downloaded and checked the first time."""
    if env.get("GRADLE"):
        return Path(env["GRADLE"])
    binary = store / f"gradle-{GRADLE_VERSION}" / "bin" / ("gradle.bat" if os.name == "nt" else "gradle")
    if binary.is_file():
        return binary
    store.mkdir(parents=True, exist_ok=True)
    archive = store / f"gradle-{GRADLE_VERSION}-bin.zip"
    print(f"build: downloading {GRADLE_URL}", file=sys.stderr, flush=True)
    urllib.request.urlretrieve(GRADLE_URL, archive)
    with zipfile.ZipFile(checked(archive, GRADLE_SHA256)) as packed:
        packed.extractall(store)
    archive.unlink()
    binary.chmod(0o755)
    return binary


def default_home(env: dict[str, str], user: Path) -> Path:
    """Where the build keeps its own files when nothing says otherwise: a folder of its own in the account's cache folder."""
    cache = env.get("LOCALAPPDATA") or env.get("XDG_CACHE_HOME")
    return (Path(cache) if cache else user / ".cache") / "poolhouse-android"


def build_home(asked: Path | None, env: dict[str, str], user: Path, states: list[Path]) -> Path:
    """The build's folder, `asked` or the default. It, GRADLE_USER_HOME and ANDROID_USER_HOME are refused inside a state folder."""
    chosen = (asked or default_home(env, user)).resolve()
    named = [("The build folder", chosen), *((name, Path(env[name]).resolve()) for name in ("GRADLE_USER_HOME", "ANDROID_USER_HOME") if env.get(name))]
    for name, path in named:
        if any(path.is_relative_to(state.resolve()) for state in states):
            raise Missing(f"{name} {path} is inside Poolhouse's state folder, which a build must not fill. Name a folder outside it.")
    return chosen


def environment(env: dict[str, str], sdk: Path, java: Path, folder: Path) -> dict[str, str]:
    out = {**env, "ANDROID_HOME": str(sdk), "JAVA_HOME": str(java)}
    out.pop("ANDROID_SDK_ROOT", None)
    out.setdefault("GRADLE_USER_HOME", str(folder / "gradle-home"))
    out.setdefault("ANDROID_USER_HOME", str(folder / "android-home"))
    for name in ("GRADLE_USER_HOME", "ANDROID_USER_HOME"):
        Path(out[name]).mkdir(parents=True, exist_ok=True)
    return out


def arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python app/android/build.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", nargs="?", default="debug", choices=sorted(TASKS))
    parser.add_argument("--home", type=Path, help="keep Gradle, its cache and the debug key here instead of ~/.cache/poolhouse-android")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    asked = arguments(argv)
    kind = asked.kind
    sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
    import testslots
    from poolhouse import home

    try:
        folder = build_home(asked.home, dict(os.environ), Path.home(), [home.home(), home.account_roots()[0]])
        sdk = find_sdk(sdk_candidates(dict(os.environ), Path.home()))
        java = find_java(dict(os.environ))
        gradle = find_gradle(dict(os.environ), folder / "gradle")
    except Missing as missing:
        print(f"build: {missing}", file=sys.stderr)
        return 3
    with testslots.lease(WORKERS, 1, label="Android build") as admitted:
        workers = min(admitted.workers, WORKERS)
        code = subprocess.run([str(gradle), "--no-daemon", "--console=plain", f"--max-workers={workers}",
                               "-Pkotlin.compiler.execution.strategy=in-process", *TASKS[kind]], cwd=HERE,
                              env=environment(dict(os.environ), sdk, java, folder), check=False).returncode
    if code == 0 and kind in APK:
        print(f"build: {HERE / APK[kind]}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
