"""Validate macOS paths statically and other platforms with pytest's parser."""

import os
import sys
from pathlib import Path


def canonical(word: str, root: Path) -> str:
    """``word``, a test path or node id, spelled from ``root`` when it names something inside it; any other word as it was.

    The reuse store, the node build and the junit file name a test file by its path from the checkout, so the same file spelled with its
    absolute path, a leading ``./`` or a ``..`` matches none of them: its tests ran and the run was then judged a run of nothing.
    """
    path, colons, node = word.partition("::")
    if not path or word.startswith("-"):
        return word
    target = Path(path) if Path(path).is_absolute() else root / path
    try:
        inside = target.resolve().relative_to(root.resolve())
    except ValueError:
        return word
    return inside.as_posix() + colons + node if target.exists() else word


def relative(arguments: list[str], root: Path) -> list[str]:
    """``arguments`` with every test path inside ``root`` spelled from it (``canonical``); a word that is no path is left as it was."""
    return [canonical(word, root) for word in arguments]


def validate(arguments: list[str], root: Path) -> None:
    """Reject missing files before broker admission while preserving pytest selectors."""
    if sys.platform == "darwin" and os.environ.get("DEV_TEST_CONFINE") == "1":
        from test_kernel_isolation import check_selectors
        try:
            check_selectors([sys.executable, "-m", "pytest", *arguments])
        except RuntimeError as error:
            raise ValueError(str(error)) from None
        for selector in arguments:
            if selector.startswith("tests/"):
                target = root / selector.split("::", 1)[0]
                if not target.is_file():
                    raise ValueError(f"test path does not exist: {selector}")
        return
    import testoptions
    for selector in testoptions.positional(arguments):
        path = Path(selector.split('::', 1)[0])
        target = path if path.is_absolute() else root / path
        if not target.exists():
            raise ValueError(f"test path does not exist: {path}")
        if not (target.is_file() or target.is_dir()):
            raise ValueError(f"test path is not a file or directory: {path}")
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"test path is outside this checkout, whose root, store and node build the run would use for it: {path}")
        if target.is_file() and target.suffix != ".py":
            raise ValueError(f"test path is not a Python test file: {path} (a Rust test runs through the Python test that wraps it, "
                             "such as tests/test_governed_grants_native.py)")
