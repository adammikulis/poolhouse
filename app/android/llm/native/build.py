"""Build the phone's llama.cpp libraries under the maintained CPU broker.

    python app/android/llm/native/build.py [--fetch-only] [--jobs N]

The llama.cpp commit is the pool's verified pin (patches/llama.cpp/verified.json). The sources land in
``app/android/llm/build/sources`` and the libraries in ``app/android/llm/build/llama-jni``, where the Gradle module takes them.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NATIVE = Path(__file__).resolve().parent
MODULE = NATIVE.parent
ROOT = MODULE.parents[2]
SOURCES = MODULE / "build/sources"
ABI = "arm64-v8a"
MINIMUM_API = 31
OPENCL = {
    "OpenCL-Headers": ("https://github.com/KhronosGroup/OpenCL-Headers", "6fe718c31a45fe25151362a72ef041c3a1047cbd"),
    "OpenCL-ICD-Loader": ("https://github.com/KhronosGroup/OpenCL-ICD-Loader", "b7bd2803acc779c03d96588e9ca9e9568a18698a"),
}


def run(*command: str | Path, cwd: Path | None = None) -> None:
    subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def checkout(name: str, url: str, commit: str) -> Path:
    """The source tree ``name`` at exactly ``commit``."""
    tree = SOURCES / name
    if not (tree / ".git").is_dir():
        tree.mkdir(parents=True, exist_ok=True)
        run("git", "init", "--quiet", cwd=tree)
        run("git", "remote", "add", "origin", url, cwd=tree)
    head = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=tree, capture_output=True, text=True).stdout.strip()
    if head != commit:
        run("git", "fetch", "--quiet", "--depth", "1", "origin", commit, cwd=tree)
        run("git", "checkout", "--quiet", "--detach", commit, cwd=tree)
    return tree


def sources() -> dict[str, Path]:
    pin = json.loads((ROOT / "patches/llama.cpp/verified.json").read_text("utf-8"))
    wanted = {"llama.cpp": (pin["upstream"], pin["commit"]), **OPENCL}
    return {name: checkout(name, *where) for name, where in wanted.items()}


def newest(directory: Path) -> Path:
    found = sorted(directory.iterdir(), key=lambda p: tuple(int(n) for n in p.name.split(".") if n.isdigit()))
    if not found:
        raise SystemExit(f"Nothing is installed in {directory}.")
    return found[-1]


@dataclass(frozen=True)
class Toolchain:
    ndk: Path
    jobs: int

    def build(self, source: Path, target: Path, options: list[str], goal: str) -> None:
        """Configure ``source`` for the phone in ``target`` and build ``goal``."""
        run("cmake", "-S", source, "-B", target, "-G", "Ninja", f"-DCMAKE_TOOLCHAIN_FILE={self.ndk / 'build/cmake/android.toolchain.cmake'}",
            f"-DANDROID_ABI={ABI}", f"-DANDROID_PLATFORM=android-{MINIMUM_API}", "-DCMAKE_BUILD_TYPE=Release", *options)
        run("cmake", "--build", target, "--parallel", str(self.jobs), "--target", goal)


def build(jobs: int) -> None:
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
    ndk = newest(sdk / "ndk")
    trees = sources()
    work = MODULE / "build/native-cmake"
    headers = trees["OpenCL-Headers"]
    loader = work / "opencl-loader"
    toolchain = Toolchain(ndk, jobs)
    toolchain.build(trees["OpenCL-ICD-Loader"], loader,
                    [f"-DOPENCL_ICD_LOADER_HEADERS_DIR={headers}", "-DOPENCL_ICD_LOADER_BUILD_TESTING=OFF", "-DBUILD_TESTING=OFF"], "OpenCL")
    library = next(loader.rglob("libOpenCL.so"))
    engine = work / "engine"
    toolchain.build(NATIVE, engine,
                    [f"-DLLAMA_SOURCE={trees['llama.cpp']}", f"-DOpenCL_INCLUDE_DIR={headers}", f"-DOpenCL_LIBRARY={library}"], "all")
    destination = MODULE / "build/llama-jni" / ABI
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    strip = next((ndk / "toolchains/llvm/prebuilt").iterdir()) / "bin/llvm-strip"
    made = list(engine.rglob("*.so"))
    if not any(p.name == "libpoolhouse_llama.so" for p in made):
        raise SystemExit("The build made no libpoolhouse_llama.so.")
    for produced in made:
        run(strip, "--strip-unneeded", "-o", destination / produced.name, produced)
    runtime = next((ndk / "toolchains/llvm/prebuilt").iterdir()) / "sysroot/usr/lib/aarch64-linux-android/libc++_shared.so"
    shutil.copyfile(runtime, destination / runtime.name)
    # The OpenCL loader is only linked against: the phone's own driver library is the one loaded.
    (destination / "libOpenCL.so").unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fetch-only", action="store_true")
    parser.add_argument("--jobs", type=int, default=3, help="the most compile jobs at once (default 3, shared with the machine's other builds)")
    arguments = parser.parse_args()
    if arguments.fetch_only:
        sources()
        return 0
    sys.path.insert(0, str(ROOT / "scripts"))
    import testslots

    with testslots.lease(arguments.jobs, label="Android llama.cpp build") as admitted:
        build(admitted.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
