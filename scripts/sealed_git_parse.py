"""Whether the Python files a range of commits changes parse at its tip.

    scripts/sealed_git_parse.py BASE TIP   BASE and TIP are full commit ids; exit 1 names each file that does not parse, or that either is missing

Git objects are read as stored (`sealed_git`).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sealed_git as lg


def unparseable(root: Path, base: str, tip: str) -> list[str]:
    """Python files changed in ``base..tip`` that do not parse at ``tip``; for a ``base`` of no commit here (a new branch), the files of the
    commits no remote-tracking ref holds."""
    names = lg.changed(root, base, tip, "--diff-filter=ACMR") if lg.commit_id(root, base) else lg.unpushed_names(root, tip)
    bad = []
    for name in sorted({n for n in names if n.endswith(".py")}):
        source = lg.blob(root, tip, name)
        if source is None:
            continue
        try:
            ast.parse(source, filename=name)
        except SyntaxError as exc:
            bad.append(f"  {name}:{exc.lineno}: {exc.msg}")
        except ValueError as exc:
            bad.append(f"  {name}: {exc}")
    return bad


def main(argv: list[str]) -> int:
    """0 when every changed Python file parses; 1 when one does not or the range cannot be read."""
    if len(argv) != 2:
        raise SystemExit(__doc__)
    root = Path.cwd()
    try:
        if missing := [sha for sha in argv if not lg.commit_id(root, sha)]:
            print(f"parse check: refused: no commit {', '.join(missing)} here, so the range cannot be read", file=sys.stderr)
            return 1
        bad = unparseable(root, *argv)
    except RuntimeError as exc:
        print(f"parse check: refused: the changed files could not be read ({exc})", file=sys.stderr)
        return 1
    if bad:
        print("parse check: refused: Python that does not parse:\n" + "\n".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
