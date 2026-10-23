"""Run the tests a push can break before it goes through: the ones ``scripts/affected.py`` selects for the pushed paths.

The selection is the import graph from each pushed path to the test files that reach it, the tests that read the whole tree (the
glossary and the switch table) when a path they scan changed, and the tree-wide guards. The run is ``scripts/test full FILES`` in the
pushing checkout, so a file whose tree already passed is reused and the broker admits the rest. A test that fails is run again on a
checkout of the commit the push is measured from: one that fails there too was red before this push and is named, not held against
it. A selection or a run that cannot be made refuses the push with its cause; nothing passes because the check could not run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1] / "src")]
import affected  # noqa: E402
import sealed_git as lg  # noqa: E402

SHOWN = 8
"""How many test files the opening line names before it only counts them."""
RUNNER = Path("scripts") / "test"
TIER = "all"
FLAGS = ("--redteam",)
"""The tier and flags the selected files run with: every test of them, the slow ones, the whole-tree scans and the red-team ones too.
A test marked ``real_model`` needs a served model under a lease, which a push never takes; it is named in a warning instead."""
FAILED = ("FAILED ", "ERROR ")


def say(text: str) -> None:
    """One line on stderr, where git shows a hook's output."""
    print(f"affected-tests: {text}", file=sys.stderr, flush=True)


def pushed_paths(root: Path, base: str, tip: str) -> tuple[list[str], frozenset[str], str]:
    """The paths the push changes, those it deletes, and the commit the push is measured from (empty when it is not known).

    The remote's old tip ``base`` is used when this repository has it; otherwise the commits on no remote-tracking ref are.
    """
    held = lg.commit_id(root, base)
    if not held:
        names = sorted(set(lg.unpushed_names(root, tip)))
        return names, frozenset(n for n in names if lg.blob(root, tip, n) is None), ""
    start = lg.sealed(root, "merge-base", held, tip, check=False).stdout.strip() or held
    return lg.changed(root, start, tip), frozenset(lg.changed(root, start, tip, "--diff-filter=D")), start


def moved_module(root: Path, start: str, tip: str, names: list[str]) -> bool:
    """Whether the push adds, deletes or renames a Python or Rust source (any it changes, when the start is not known)."""
    found = lg.changed(root, start, tip, "--diff-filter=AD") if start else names
    return any(n.endswith((".py", ".rs")) for n in found)


def needing_a_model(root: Path, files: list[str]) -> list[str]:
    """The test files that hold tests marked ``real_model``."""
    return [f for f in files if "real_model" in (root / f).read_text(encoding="utf-8", errors="replace")]


def selected(root: Path, dev: str, base: str, tip: str) -> tuple[list[str], list[str], str]:
    """The test files to run, the notes (a path no rule could place, files left to CI or the gate) and the start commit, for ``tip``."""
    names, gone, start = pushed_paths(root, base, tip)
    picked = affected.select(root, names, gone, base=start or f"origin/{dev}")
    files = set(picked.files) - picked.loose
    notes = [f"{p}: {picked.reasons.get(p, 'nothing says which tests it affects')}" + (
        "; the tests that import or name it run" if p in gone else "; only the tree-wide guards run for it") for p in picked.unmapped]
    if picked.unmapped or moved_module(root, start, tip, names):    # a new, deleted or renamed module is for the layer and wiring guards
        files.update(f"tests/{g}" for g in affected.GUARDS if (root / "tests" / g).is_file())
    if picked.loose:
        notes.append(f"{len(picked.loose)} more test file(s) are reached only by a bare word in a string or a directory listing, "
                     "which CI runs")
    if held := needing_a_model(root, sorted(files)):
        notes.append(f"{', '.join(held)} hold tests marked real_model, which need a served model under a lease; this check does not "
                     "run them, the gate and CI do")
    return sorted(files), notes, start


def run_tests(root: Path, selectors: list[str], echo: bool = True) -> tuple[int, list[str]]:
    """Run ``scripts/test full SELECTORS`` in ``root``; its exit status and the failing test lines it printed (all of it too when ``echo``)."""
    env = {k: v for k, v in os.environ.items() if not lg.REDIRECTS.match(k)}
    failing: list[str] = []
    with subprocess.Popen([sys.executable, str(root / RUNNER), TIER, *selectors, *FLAGS], cwd=root, env=env, text=True, errors="replace",
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as child:
        for line in child.stdout:
            if echo:
                print(line, end="", file=sys.stderr, flush=True)
            if line.startswith(FAILED):
                failing.append(line.rstrip())
        return child.wait(), failing


def test_id(line: str) -> str:
    """The test id a ``FAILED id - reason`` line names."""
    return line.split(" ", 1)[1].split(" - ", 1)[0].strip()


def failing_at(root: Path, commit: str, ids: list[str]) -> set[str]:
    """Which of the test ``ids`` fail on a checkout of ``commit``, made beside ``root``'s repository and removed again."""
    with tempfile.TemporaryDirectory(prefix="affected-base-") as scratch:
        where = Path(scratch) / "base"
        lg.git(root, "worktree", "add", "--detach", str(where), commit)
        try:
            return {test_id(line) for line in run_tests(where, ids, echo=False)[1]}
        finally:
            lg.git(root, "worktree", "remove", "--force", str(where), check=False)


def split_old(root: Path, start: str, failing: list[str]) -> tuple[list[str], list[str]]:
    """The failing lines this push breaks and those that fail on ``start`` too (all of them new when ``start`` is not known)."""
    if not start:
        return failing, []
    say(f"running the {len(failing)} failed test(s) again on {start[:12]}, the commit the push is measured from, to tell new from old")
    try:
        old = failing_at(root, start, [test_id(line) for line in failing])
    except (RuntimeError, OSError) as exc:
        say(f"warning: the commit {start[:12]} could not be checked out ({exc}); every failure is held against the push")
        return failing, []
    return [line for line in failing if test_id(line) not in old], [line for line in failing if test_id(line) in old]


def failing_files(lines: list[str]) -> list[str]:
    """The test files the ``FAILED id`` lines name, each once."""
    seen = []
    for line in lines:
        path = test_id(line).split("::", 1)[0]
        if path.startswith("tests/") and path not in seen:
            seen.append(path)
    return seen


def rerun(files: list[str]) -> str:
    """The command that runs ``files`` again."""
    return " ".join(["scripts/test", TIER, *files, *FLAGS])


def refuse(why: str, again: str = "") -> int:
    """Say that the push is refused, why, and the command that tries again; the return value is the hook's failure."""
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
                          "so push from a checkout of the commit", f"git push origin HEAD:refs/heads/{dev}")
        files, notes, start = selected(root, dev, base, tip)
    except (RuntimeError, OSError, ValueError, SyntaxError, KeyError, ImportError) as exc:
        return refuse(f"the tests this push can break could not be selected ({type(exc).__name__}: {exc})", "python scripts/hooks/affected_push.py")
    for note in notes:
        say(f"warning: {note}")
    if not files:
        return 0
    if not (root / RUNNER).is_file():
        return refuse(f"{RUNNER} is missing from {root}, so the {len(files)} selected test file(s) cannot run")
    named = ", ".join(files[:SHOWN]) + (f" and {len(files) - SHOWN} more" if len(files) > SHOWN else "")
    say(f"running {len(files)} test file(s) the pushed paths reach, a file whose tree already passed is reused: {named}")
    status, failing = run_tests(root, files)
    if status == 0:
        return 0
    if not failing:
        return refuse(f"scripts/test exited {status} without naming a failing test (see its output above)", rerun(files))
    new, old = split_old(root, start, failing)
    if old:
        say(f"warning: {len(old)} test(s) were already red before this push and are not held against it:")
        for line in old:
            say(f"  {line}")
    if not new:
        return 0
    say("these tests fail on the tree being pushed and passed before it:")
    for line in new:
        say(f"  {line}")
    return refuse("the push breaks the tests above", rerun(failing_files(new) or files))


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
