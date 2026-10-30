"""Compile the offline native candidate checker through CPU admission."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def compile_sources(output: Path, temporary: Path, testing: bool = False, library: bool = False, env=None) -> None:
    source = Path(__file__).parent
    objects = []
    for path in (source / "Capture.c", source / "CodeIdentity.c", source / "NativeIdentity.c"):
        compiled = temporary / (path.stem + ".o")
        subprocess.run(["/usr/bin/clang", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        *(["-DTESTING"] if testing else []), "-c", str(path), "-o", str(compiled)],
                       check=True, env=env, timeout=60)
        objects.append(compiled)
    swift = sorted(source.glob("*.swift"))
    if library:
        swift = [path for path in swift if path.name != "Entry.swift"]
    subprocess.run(["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-module-name", "NativeHostCandidate",
                    *(["-D", "TESTING", "-Xcc", "-DTESTING"] if testing else []),
                    *(["-emit-library"] if library else []), "-import-objc-header", str(source / "Capture.h"),
                    *map(str, swift), *map(str, objects), "-framework", "Security", "-o", str(output)],
                   check=True, env=env, timeout=120)


def main() -> int:
    import testslots

    output = Path(sys.argv[1]).resolve()
    with testslots.lease(1, label="native host candidate compile"), tempfile.TemporaryDirectory() as temporary:
        compile_sources(output, Path(temporary), library=output.suffix == ".dylib")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
