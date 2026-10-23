"""The narrow cases of ``affected.select``: a changed script, a lowered budget, a generated file.

A script is reached by every test file that imports it, names ``scripts/<name>`` in a string or a
command line, builds its path from the bare name, or reaches it through another script or a
tests helper that does. Anything these rules cannot place stays with the caller, which runs
everything; where a rule is unsure it selects more.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

SKIP = {"__pycache__", ".git", "node_modules"}
LIMIT = 600_000
LISTERS = ("glob(", "iterdir(", "listdir(", "scandir(", "os.walk(", "ls-files")
GENERATED = {"docs/redteam/coverage.json": "scripts/redteam_coverage.py",
             "docs/commands.md": "scripts/reference"}
BUDGETS = "budgets.json"


@dataclass(frozen=True)
class Source:
    """One scanned file: its text, the names it imports and the string constants it holds."""

    rel: str
    text: str
    segments: frozenset[str] = field(default_factory=frozenset)
    consts: frozenset[str] = field(default_factory=frozenset)
    blob: str = ""
    parsed: bool = False

    def in_scripts_context(self) -> bool:
        """True when a bare name in this file can be a script: it lives in scripts/, holds the
        word ``scripts`` in a string or imports something named for scripts."""
        return (self.rel.startswith("scripts/") or "scripts" in self.blob.lower()
                or any("script" in seg.lower() for seg in self.segments))

    @property
    def lists_scripts(self) -> bool:
        """True when the file enumerates a directory and mentions ``scripts``."""
        return self.parsed and "scripts" in self.blob and any(x in self.text for x in LISTERS)


def parse(rel: str, text: str) -> Source:
    """The imports and string constants of ``text`` when it is Python, else the text alone."""
    src = Source(rel, text)
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return src
    segments: set[str] = set()
    consts: set[str] = set()
    prose = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(
        n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
        and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                segments.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            segments.update((node.module or "").split("."))
            segments.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
            consts.add(node.value)
    return Source(rel, text, frozenset(segments), frozenset(consts), "\n".join(sorted(consts)), True)


def corpus(root: Path) -> dict[str, Source]:
    """Every script, tests Python file and source module, scanned."""
    out: dict[str, Source] = {}
    for top, pattern in (("scripts", "*"), ("tests", "*.py"), ("src", "*.py")):
        for path in sorted((root / top).rglob(pattern)):
            if not path.is_file() or SKIP & set(path.parts) or path.stat().st_size > LIMIT:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(root).as_posix()
            out[rel] = parse(rel, text)
    return out


@dataclass
class Target:
    """How other files name one file: by path, by import or by a bare name in a string."""

    rel: str
    path: re.Pattern
    shell: re.Pattern
    names: set[str]
    stem: str | None


def target(rel: str) -> Target:
    """The patterns that find references to ``rel``."""
    path = Path(rel)
    name = path.name
    stem = path.stem if path.suffix == ".py" else None
    inner = path.with_suffix("").as_posix() if stem else rel
    return Target(
        rel,
        re.compile(re.escape(inner) + (r"(?:\.py)?" if stem else "") + r"(?![\w.-])"),
        re.compile(r"(?<![\w.-])(?:\./|\$\{?\w+\}?/|/)" + re.escape(name) + r"(?![\w.-])"),
        {name, *([stem] if stem else [])},
        stem)


def refers(src: Source, tgt: Target) -> str:
    """Why ``src`` names the target, or an empty string."""
    if src.rel == tgt.rel:
        return ""
    if tgt.stem and tgt.stem in src.segments and src.parsed:
        return f"imports {tgt.stem}"
    if tgt.path.search(src.blob if src.parsed else src.text):
        return f"names {tgt.rel}"
    if any(c.endswith("/" + n) for c in src.consts for n in tgt.names):
        return f"names a path ending in {Path(tgt.rel).name}"
    if src.consts & tgt.names and src.in_scripts_context():
        return f"names {sorted(src.consts & tgt.names)[0]!r} in a string"
    if not src.parsed and tgt.shell.search(src.text):
        return f"runs {Path(tgt.rel).name}"
    return ""


def is_test(rel: str) -> bool:
    """True for a collected test file."""
    return rel.startswith("tests/test_") and rel.count("/") == 1 and rel.endswith(".py")


class ReferenceIndex:
    """Exact reverse references cached within one immutable source snapshot."""

    def __init__(self, files: Mapping[str, Source]) -> None:
        self.files = MappingProxyType(dict(files))
        self._matches: dict[str, tuple[tuple[str, str], ...]] = {}
        self._infrastructure = infrastructure(self.files)
        self._listers = tuple(listers(self.files))

    def referrers(self, rel: str) -> tuple[tuple[str, str], ...]:
        if rel not in self._matches:
            wanted = target(rel)
            self._matches[rel] = tuple((name, reason) for name, source in self.files.items()
                                       if (reason := refers(source, wanted)))
        return self._matches[rel]

    def reaching(self, rel: str) -> tuple[dict[str, str], set[str], str]:
        return _reaching(self, rel)

    def firm(self, rel: str) -> tuple[set[str], set[str]]:
        """The test files and src modules that reach ``rel`` without a reference that is only a bare word or a listing."""
        why, modules, _ = _reaching(self, rel, firm=True)
        return set(why), modules

    def script_tests(self, rel: str) -> tuple[dict[str, str], set[str], str]:
        why, modules, shared = self.reaching(rel)
        for name in self._listers:
            why.setdefault(name, f"{name} enumerates scripts/")
        return why, modules, shared


PLUGINS = re.compile(r"""["']-p["'],\s*["'](\w+)["']""")


def infrastructure(files: Mapping[str, Source]) -> dict[str, str]:
    """Files that run in every test session: conftest, the package marker and the ``-p`` plugins."""
    out = {"tests/conftest.py": "tests/conftest.py", "tests/__init__.py": "tests/__init__.py"}
    runner = files.get("scripts/test")
    for name in PLUGINS.findall(runner.text) if runner else ():
        out[f"scripts/{name}.py"] = f"the {name} plugin that scripts/test loads"
    return out


def loose(reason: str) -> bool:
    """Whether a reference rests on a bare word in a string or on a directory listing, which names a file by chance as often as by use."""
    return "in a string" in reason or reason.startswith("enumerates")


def _reaching(index: ReferenceIndex, rel: str, firm: bool = False) -> tuple[dict[str, str], set[str], str]:
    """Test files that reach ``rel`` through scripts and helpers, with why, the src modules on
    the way and, when a file every test session runs depends on ``rel``, which one.

    ``firm`` follows only references that are not :func:`loose`.
    """
    why: dict[str, str] = {}
    modules: set[str] = set()
    infra = index._infrastructure
    shared = f"{infra[rel]} runs in every test session" if rel in infra else ""
    seen, queue = {rel}, [(rel, True)]
    while queue:
        current, pure = queue.pop()
        for name, reason in index.referrers(current):
            if firm and loose(reason):
                continue
            if name in infra:
                if pure:    # its code runs in every session; a name only held by a helper may never run
                    shared = shared or f"{infra[name]} {reason} (every test session loads it)"
            elif is_test(name):
                why.setdefault(name, f"{name} {reason}" + ("" if current == rel else f" (via {current})"))
            elif name.startswith("src/"):
                modules.add(name)
            elif name not in seen:
                seen.add(name)
                queue.append((name, pure and reason.startswith("imports")))
    return why, modules, shared


def reaching(files: Mapping[str, Source], rel: str) -> tuple[dict[str, str], set[str], str]:
    """Exact reverse references from a fresh source snapshot."""
    return ReferenceIndex(files).reaching(rel)


def listers(files: Mapping[str, Source]) -> list[str]:
    """Test files that enumerate directories while mentioning ``scripts``."""
    return sorted(n for n, s in files.items() if is_test(n) and s.lists_scripts)


def script_tests(files: dict[str, Source], rel: str) -> tuple[dict[str, str], set[str], str]:
    """Test files that exercise the script ``rel``, with why, the src modules that name it and
    the session-wide file that loads it, if any."""
    return ReferenceIndex(files).script_tests(rel)


def lowered_only(old: object, new: object) -> bool:
    """True when ``new`` differs from ``old`` only by integers that did not rise."""
    if isinstance(old, dict) and isinstance(new, dict):
        return old.keys() == new.keys() and all(lowered_only(old[k], new[k]) for k in old)
    ints = isinstance(old, int) and isinstance(new, int) and not isinstance(old, bool) \
        and not isinstance(new, bool)
    return ints and new <= old  # type: ignore[operator]


def budgets_lowered(root: Path, base: str) -> bool:
    """True when budgets.json against its merge-base with ``base`` only fell; unsure is False."""
    try:
        mb = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=root, capture_output=True,
                            text=True, check=True).stdout.strip()
        old = subprocess.run(["git", "show", f"{mb}:{BUDGETS}"], cwd=root, capture_output=True,
                             text=True, check=True).stdout
        return lowered_only(json.loads(old), json.loads((root / BUDGETS).read_text(encoding="utf-8")))
    except (subprocess.CalledProcessError, OSError, ValueError):
        return False


def budget_tests(files: dict[str, Source]) -> dict[str, str]:
    """Test files that check or read budgets.json, directly or through the scripts that read it."""
    why = {n: f"{n} checks the budgets" for n in files if is_test(n) and Path(n).name.startswith(
        ("test_budget", "test_optional_task_budget"))}
    for name, src in files.items():
        if BUDGETS in src.text and not name.startswith("src/"):
            if is_test(name):
                why.setdefault(name, f"{name} reads {BUDGETS}")
            elif not name.startswith("tests/"):
                why.update({k: f"{v} (reads {BUDGETS})" for k, v in reaching(files, name)[0].items()})
    return why


def generated_tests(files: dict[str, Source], rel: str) -> dict[str, str]:
    """Test files that read the generated ``rel`` or run its generator themselves; the gate checks the rest.

    A generated file is prose in a push: what it can break is a test that reads it or one that regenerates it and compares. A
    test that only reaches the generator through helpers, or by a bare word, reads neither, and following those chains selects
    nearly every test (a changed generator is :func:`script_tests`' case, where a chain is followed).
    """
    why = {n: f"{n} names {rel}" for n, s in files.items()
           if is_test(n) and Path(rel).name in s.text}
    for name, reason in ReferenceIndex(files).referrers(GENERATED[rel]):
        if is_test(name) and not loose(reason):
            why.setdefault(name, f"{name} {reason}")
    return why
