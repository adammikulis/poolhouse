"""Validate macOS paths statically and other platforms with pytest's parser."""

import ast
import os
import sys
from pathlib import Path


def refuse_unrequested(target: Path, shown: str, arguments: list[str]) -> None:
    """Refuse a named file whose every test is marked real_model when --real-model is not given: the run would
    deselect all of it and report nothing, which hides a real-model test that was asked for."""
    if "--real-model" in arguments or not target.is_file() or target.suffix != ".py":
        return
    try:
        body = ast.parse(target.read_text(encoding="utf-8")).body
    except (OSError, SyntaxError, UnicodeDecodeError):
        return
    for node in body:
        marked = isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in node.targets)
        if marked and any(isinstance(n, ast.Attribute) and n.attr == "real_model" for n in ast.walk(node.value)):
            raise ValueError(f"{shown} marks every test real_model, so a run without --real-model leaves all of it out "
                             f"and says nothing; ask for it: scripts/test all {shown} --real-model")


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
                refuse_unrequested(target, selector.split("::", 1)[0], arguments)
        return
    import testoptions
    for selector in testoptions.positional(arguments):
        path = Path(selector.split('::', 1)[0])
        target = path if path.is_absolute() else root / path
        if not target.exists():
            raise ValueError(f"test path does not exist: {path}")
        if not (target.is_file() or target.is_dir()):
            raise ValueError(f"test path is not a file or directory: {path}")
        refuse_unrequested(target, str(path), arguments)
