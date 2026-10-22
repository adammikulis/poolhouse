"""Run the tests a push can break before it goes through: the ones ``scripts/affected.py`` selects for the pushed paths.

The selection is the import graph from each pushed path to the test files that reach it, the tests that read the whole tree (the
glossary and the switch table) when a path they scan changed, and the tree-wide guards. The run is ``scripts/test all FILES`` in the
pushing checkout, so a file whose tree already passed is reused and the broker admits the rest. A selection or a run that cannot be
made refuses the push with its cause; nothing passes because the check could not run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1] / "src")]
import affected  # noqa: E402
import sealed_git as lg  # noqa: E402

SHOWN = 8
"""How many test files the opening line names before it only counts them."""
RUNNER = Path("scripts") / "test"
FAILED = ("FAILED ", "ERROR ")


def say(text: str) -> None:
    """One line on stderr, where git shows a hook's output."""
    print(f"affected-tests: {text}", file=sys.stderr, flush=True)


def pushed_paths(root: Path, base: str, tip: str) -> tuple[list[str], frozenset[str], str]:
    """The paths the push changes, those it deletes, and the commit the push is measured from.

    The remote's old tip ``base`` is used when this repository has it; otherwise the commits on no remote-tracking ref are, measured
    from the development branch on ``origin``.
    """
    held = lg.commit_id(root, base)
    if not held:
        names = sorted(set(lg.unpushed_names(root, tip)))
        return names, frozenset(n for n in names if lg.blob(root, tip, n) is None), ""
    start = lg.sealed(root, "merge-base", held, tip, check=False).stdout.strip() or held
    return lg.changed(root, start, tip), frozenset(lg.changed(root, start, tip, "--diff-filter=D")), start


def selected(root: Path, dev: str, base: str, tip: str) -> tuple[list[str], list[str]]:
    """The test files to run and the notes (one per path no rule could place), for the push of ``tip``."""
    names, gone, start = pushed_paths(root, base, tip)
    picked = affected.select(root, names, gone, base=start or f"origin/{dev}", memo_file=affected.memo_path(root))
    files = set(picked.files)
    notes = [f"{p}: {picked.reasons.get(p, 'nothing says which tests it affects')}" + (
        "; the tests that import or name it run" if p in gone else "; only the tree-wide guards run for it") for p in picked.unmapped]
    if picked.unmapped:
        files.update(f"tests/{g}" for g in affected.GUARDS if (root / "tests" / g).is_file())
    return sorted(files), notes


def run_tests(root: Path, files: list[str]) -> tuple[int, list[str]]:
    """Run ``scripts/test all FILES`` in ``root``, echoing its output; its exit status and the failing test lines it printed."""
    env = {k: v for k, v in os.environ.items() if not lg.REDIRECTS.match(k)}
    failing: list[str] = []
    with subprocess.Popen([sys.executable, str(root / RUNNER), "all", *files], cwd=root, env=env, text=True, errors="replace",
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as child:
        for line in child.stdout:
            print(line, end="", file=sys.stderr, flush=True)
            if line.startswith(FAILED):
                failing.append(line.rstrip())
        return child.wait(), failing


def rerun(files: list[str]) -> str:
    """The command that runs ``files`` again."""
    return "scripts/test all " + " ".join(files)


def failing_files(lines: list[str]) -> list[str]:
    """The test files the ``FAILED path::test`` lines name, each once."""
    seen = []
    for line in lines:
        path = line.split(" ", 1)[1].split("::", 1)[0].split(" - ", 1)[0].strip()
        if path.startswith("tests/") and path not in seen:
            seen.append(path)
    return seen


def refuse(why: str, again: str = "") -> int:
    """Say that the push is refused, why, and how to run the same check again; the return value is the hook's failure."""
    say(f"refused: {why}")
    if again:
        say(f"run it again with: {again}")
    return 1


def check(dev: str, base: str, tip: str) -> int:
    """0 when every test the push can break passes; 1, with the reason on stderr, when one fails or the check cannot be made."""
    root = lg.toplevel(Path.cwd())
    try:
        head = lg.out(root, "rev-parse", "HEAD")
        if head != tip:
            return refuse(f"this checkout is at {head[:12]} and the push is of {tip[:12]}; the tests run on the checked-out tree, "
                          f"so push from a checkout of the commit: git push origin HEAD:refs/heads/{dev}")
        files, notes = selected(root, dev, base, tip)
    except (RuntimeError, OSError, ValueError, SyntaxError, KeyError, ImportError) as exc:
        return refuse(f"the tests this push can break could not be selected ({type(exc).__name__}: {exc})")
    for note in notes:
        say(f"warning: {note}")
    if not files:
        say("no test reaches the pushed paths; nothing to run")
        return 0
    if not (root / RUNNER).is_file():
        return refuse(f"{RUNNER} is missing from {root}, so the {len(files)} selected test file(s) cannot run")
    named = ", ".join(files[:SHOWN]) + (f" and {len(files) - SHOWN} more" if len(files) > SHOWN else "")
    say(f"running {len(files)} test file(s) the pushed paths reach, a file whose tree already passed is reused: {named}")
    status, failing = run_tests(root, files)
    if status == 0:
        return 0
    if failing:
        say("these tests fail on the tree being pushed:")
        for line in failing:
            say(f"  {line}")
        return refuse("the push breaks the tests above", rerun(failing_files(failing) or files))
    return refuse(f"scripts/test exited {status} without naming a failing test (see its output above)", rerun(files))


def before_pushing() -> int:
    """The check of HEAD against ``origin/<dev>``, as the push hook would run it."""
    from poolhouse.devbranch import development_branch
    root = lg.toplevel(Path.cwd())
    dev = development_branch(root)
    return check(dev, lg.out(root, "rev-parse", f"origin/{dev}"), lg.out(root, "rev-parse", "HEAD"))


if __name__ == "__main__":
    if len(sys.argv) not in (1, 4):
        raise SystemExit("usage: affected_push.py [DEV BASE TIP]   (no arguments: HEAD against origin/<dev>)")
    raise SystemExit(check(*sys.argv[1:]) if len(sys.argv) == 4 else before_pushing())
