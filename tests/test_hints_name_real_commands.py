"""No string in the source, the README or the current documents names a console script that ``pyproject.toml`` does not install."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from test_layers import EXPERIMENTAL

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "poolhouse"

REMOVED = (*(f"poolhouse-{name}" for name in EXPERIMENTAL),
           "poolhouse-train-run", "poolhouse-train-tools", "poolhouse-train-decider", "poolhouse-suite")
"""The names the experimental layer's commands had as console scripts; they run as ``python -m poolhouse.<name>``."""

DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
UNCHANGED_DOCUMENTS = {"CHANGELOG.md", "glossary.md", "design-shrink-surface.md"}


def installed() -> set[str]:
    return set(tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"])


def named_in(text: str) -> set[str]:
    """The removed console scripts ``text`` names."""
    found = {m.group(0) for m in re.finditer(r"(?<![\w./-])poolhouse-[a-z]+(?:-[a-z]+)*(?![\w-])", text)}
    return {name for name in found if name in REMOVED and name not in installed()}


def strings_of(path: Path) -> list[tuple[int, str]]:
    """Every string constant in a Python file, docstrings included, with its line."""
    return [(n.lineno, n.value) for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def offenders() -> list[str]:
    out: list[str] = []
    for path in sorted(SRC.rglob("*")):
        if path.suffix == ".py":
            out += [f"{path.relative_to(REPO)}:{line} {name}" for line, text in strings_of(path) for name in sorted(named_in(text))]
        elif path.suffix in {".html", ".js", ".css", ".md", ".toml", ".json"}:
            out += [f"{path.relative_to(REPO)} {name}" for name in sorted(named_in(path.read_text(encoding="utf-8")))]
    documents = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
    for path in documents:
        if path.name in UNCHANGED_DOCUMENTS or DATED.search(path.name):
            continue
        out += [f"{path.relative_to(REPO)} {name}" for name in sorted(named_in(path.read_text(encoding="utf-8")))]
    return out


def test_the_scan_finds_a_removed_script_and_leaves_an_installed_one() -> None:
    assert named_in("run `poolhouse-bench sweep` or poolhouse-serve status") == {"poolhouse-bench"}
    assert named_in("python -m poolhouse.bench and the poolhouse-gym-session key") == set()
    assert named_in("`poolhouse-train-run --recipe R` and poolhouse-suite") == {"poolhouse-train-run", "poolhouse-suite"}


def test_every_experimental_package_has_a_name_to_look_for() -> None:
    assert "poolhouse-bench" in REMOVED and "poolhouse-gym" in REMOVED
    assert not set(REMOVED) & installed(), "an experimental package is installed as a console script"


def test_no_user_facing_string_names_a_console_script_that_is_not_installed() -> None:
    found = offenders()
    assert not found, "these name a console script pyproject.toml does not install; write `python -m poolhouse.<name>`:\n" + "\n".join(found)
