"""Run Android build tasks under the maintained CPU broker.

    python build.py GRADLE ANDROID_PLUGIN ANDROID_API            the protocol checks, lint and the signed release APK
    python build.py GRADLE ANDROID_PLUGIN ANDROID_API debug      the protocol checks and the debug APK (no signing key needed)
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_native
import testslots

TASKS = {"release": ["protocolChecks", "lintRelease", "assembleRelease"], "debug": ["protocolChecks", "assembleDebug"]}


def main() -> int:
    gradle, plugin, api, *kind = sys.argv[1:]
    with testslots.lease(0, label="Android build") as admitted:
        build_native.build(Path(__file__).parent.resolve(), admitted.workers)
        return subprocess.run([gradle, "--no-daemon", f"--max-workers={admitted.workers}",
                               f"-PandroidPlugin={plugin}", f"-PandroidApi={api}",
                               f"-Pandroid.suppressUnsupportedCompileSdk={api}", *TASKS[kind[0] if kind else "release"]],
                              cwd=Path(__file__).parent).returncode


if __name__ == "__main__":
    raise SystemExit(main())
