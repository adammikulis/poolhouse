"""Compile the inactive native approval executable through CPU admission."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> int:
    import testslots

    output = Path(sys.argv[1]).resolve()
    sources = sorted(Path(__file__).parent.glob("*.swift"))
    with testslots.lease(1, label="native approval compile"):
        return subprocess.run(["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-module-name", "PoolhouseApproval",
                               *map(str, sources), "-o", str(output)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
