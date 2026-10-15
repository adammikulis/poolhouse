"""Compile the inactive native approval executable through CPU admission."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> int:
    import testslots

    output = Path(sys.argv[1]).resolve()
    with testslots.lease(1, label="native approval compile"), tempfile.TemporaryDirectory() as temporary:
        compile_sources(output, Path(temporary))
    return 0


def compile_sources(output: Path, temporary: Path, testing: bool = False, env=None) -> None:
    source = Path(__file__).parent
    objects = []
    flags = ["-DTESTING"] if testing else []
    for path in sorted(source.glob("*.c")):
        compiled = temporary / (path.stem + ".o")
        subprocess.run(["/usr/bin/clang", "-std=c11", "-fblocks", "-Wall", "-Wextra", "-Werror", *flags,
                        "-c", str(path), "-o", str(compiled)], check=True, env=env, timeout=60)
        objects.append(compiled)
    subprocess.run(["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-module-name", "PoolhouseApproval",
                    *(["-D", "TESTING", "-Xcc", "-DTESTING"] if testing else []), "-import-objc-header", str(source / "NativeSender.h"),
                    *map(str, sorted(source.glob("*.swift"))), *map(str, objects), "-lbsm", "-o", str(output)],
                   check=True, env=env, timeout=120)


if __name__ == "__main__":
    raise SystemExit(main())
