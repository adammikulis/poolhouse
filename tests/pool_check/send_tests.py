"""Run on the sending device: send a tiny test file to another device of the pool through the real `scripts/test --on` code and print how it went.

    python send_tests.py --repo DIR --on DEVICE [--tier all] FILE...

``DIR`` is a root with `scripts/test_on.py` of this checkout beside it; the tree that travels is a small git tree made here: a
`scripts/test` that runs pytest on the files it is given (the executor's own command line) and test files that check where they
ran. The sender is `scripts/test_on.main`, the same function `scripts/test --on` calls.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = '''\
import subprocess
import sys

junit = next(a for a in sys.argv if a.startswith("--junitxml="))
files = [a for a in sys.argv[2:] if a.startswith("tests/")]
raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", junit, *files]))
'''
WHERE = "import platform\n\n\ndef test_this_ran_on_the_device_asked():\n    assert platform.system() == {system!r}, platform.platform()\n"
ANYWHERE = "def test_adds():\n    assert 1 + 1 == 2\n"


def make_tree(root: Path, system: str) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "scripts" / "test").write_text(RUNNER, encoding="utf-8")
    (root / "tests" / "test_where.py").write_text(WHERE.format(system=system), encoding="utf-8")
    (root / "tests" / "test_anywhere.py").write_text(ANYWHERE, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--on", required=True)
    ap.add_argument("--tier", default="all")
    ap.add_argument("--expect", required=True, help="what platform.system() says on the device the tests are sent to")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(Path(args.repo) / "scripts"))
    import test_on

    with tempfile.TemporaryDirectory(prefix="pcsend") as folder:
        tree = make_tree(Path(folder) / "tree", args.expect)
        namespace = argparse.Namespace(tier=args.tier, on=args.on, split=False, base="main", timeout=300.0)
        return int(test_on.main(namespace, args.files or ["tests/test_where.py", "tests/test_anywhere.py"], tree, lambda tier, f: [sys.executable, "-m", "pytest", "-q", f], False))


if __name__ == "__main__":
    raise SystemExit(main())
