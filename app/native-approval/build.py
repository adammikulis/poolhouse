"""Compile the inactive native approval executable through CPU admission."""
import os
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
    rust_env = dict(os.environ if env is None else env)
    rust_env["CARGO_TARGET_DIR"] = str(temporary / "authority-target")
    rust_env["CARGO_BUILD_JOBS"] = "1"
    import testnode
    rust_env.setdefault("RUSTUP_HOME", str(testnode.account() / ".rustup"))
    rust_env.setdefault("CARGO_HOME", str(testnode.account() / ".cargo"))
    subprocess.run([testnode.tool("cargo"), "build", "--offline", "--locked", "-p", "poolhouse-manifest", "--lib"],
                   cwd=ROOT / "app", env=rust_env, check=True, timeout=600)
    authority = temporary / "authority-target" / "debug" / "libpoolhouse_manifest.a"
    objects = []
    flags = ["-DTESTING"] if testing else []
    for path in sorted(source.glob("*.c")):
        compiled = temporary / (path.stem + ".o")
        subprocess.run(["/usr/bin/clang", "-std=c11", "-fblocks", "-Wall", "-Wextra", "-Werror", *flags,
                        "-c", str(path), "-o", str(compiled)], check=True, env=env, timeout=60)
        objects.append(compiled)
    subprocess.run(["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-module-name", "PoolhouseApproval",
                    *(["-D", "TESTING", "-Xcc", "-DTESTING"] if testing else []), "-import-objc-header", str(source / "NativeSender.h"),
                    *map(str, sorted(source.glob("*.swift"))), *map(str, objects), str(authority), "-lbsm", "-lresolv", "-o", str(output)],
                   check=True, env=env, timeout=120)


if __name__ == "__main__":
    raise SystemExit(main())
