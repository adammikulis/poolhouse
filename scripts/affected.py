"""Which test files a set of changed paths can reach, from the import graph.

A module's tests are the test files whose imports lead to it, directly or through other
modules, and the test files that name it in a string (``import_module``, ``-m`` arguments,
``monkeypatch.setattr("poolhouse.x.y", ...)``). A path nothing here can place is reported as
unmapped and the caller runs everything.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import affected_rules as rules

PACKAGE_ROOTS = (("src", "poolhouse"), ("scripts", "gates"))
SCRIPT_TABLE = re.compile(r'''\[["']project["']\]\[["']scripts["']\]''')
DOTTED = re.compile(r"\b((?:poolhouse|gates|tests)(?:\.\w+)+|poolhouse|gates)\b")
INERT = {"CHANGELOG.md", "HANDOFF.md", "README.md", "LICENSE", "NOTICE", "CLAUDE.md",
         "AGENTS.md", "release-please-config.json", "version.txt", ".gitignore"}
EVERYTHING = {"pyproject.toml", "budgets.json", "tests/conftest.py", "tests/known-fixtures.txt"}
DEPTH = 1
GUARDS = ("test_layers.py", "test_wiring.py", "test_conftest_guard.py", "test_isolation_guard.py",
          "test_one_python.py", "test_suite.py")


@dataclass
class Selection:
    """The test files to run, why each was chosen, and the paths that forced a full run."""

    files: set[str] = field(default_factory=set)
    firm: set[str] = field(default_factory=set)
    why: dict[str, list[str]] = field(default_factory=dict)
    unmapped: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)

    def unmap(self, rel: str, because: str) -> None:
        """Record a path no rule can place, so the caller runs everything, and say why."""
        self.unmapped.append(rel)
        self.reasons[rel] = because

    def add(self, test: str, because: str, firm: bool = True) -> None:
        """Select ``test``; ``firm=False`` says the only reason is a bare word in a string or a directory listing."""
        self.files.add(test)
        self.why.setdefault(test, []).append(because)
        if firm:
            self.firm.add(test)

    @property
    def loose(self) -> set[str]:
        """The selected test files that only a bare word in a string or a directory listing reaches."""
        return self.files - self.firm


def module_of(rel: str) -> str | None:
    """The dotted name of a repo-relative .py path under a package root, else None."""
    path = Path(rel)
    for base, package in PACKAGE_ROOTS:
        if path.parts[:1] == (base,) and path.suffix == ".py":
            parts = list(path.with_suffix("").parts[1:])
            if parts and parts[-1] == "__init__":
                parts.pop()
            if parts and (base == "scripts" or parts[0] == package):
                return ".".join(parts)
    if path.parts[:1] == ("tests",) and path.suffix == ".py" and len(path.parts) == 2:
        return f"tests.{path.stem}"
    return None


def index(root: Path) -> dict[str, str]:
    """Every importable module in the repo, dotted name to repo-relative path."""
    out: dict[str, str] = {}
    for base, _ in PACKAGE_ROOTS:
        for path in sorted((root / base).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            name = module_of(rel)
            if name:
                out[name] = rel
    for path in sorted((root / "tests").glob("*.py")):
        rel = path.relative_to(root).as_posix()
        out[module_of(rel) or path.stem] = rel
    return out


def resolve(name: str, known: dict[str, str]) -> str | None:
    """The longest module in ``known`` that is ``name`` or a package prefix of it."""
    for candidate in (name, f"tests.{name}"):
        parts = candidate.split(".")
        while parts:
            if ".".join(parts) in known:
                return ".".join(parts)
            parts.pop()
    return None


def parents(name: str, known: dict[str, str]) -> set[str]:
    """``name`` and every package above it that importing it runs."""
    parts = name.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts) + 1) if ".".join(parts[:i]) in known}


def absolute(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    """The dotted base a ``from`` import names, with relative levels resolved."""
    if not node.level:
        return node.module or ""
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join([*base, *([node.module] if node.module else [])])


def plugin_names(node: ast.AST) -> list[str]:
    """The module names a ``pytest_plugins`` assignment (plain, annotated or ``+=``) lists; empty for any other node."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    else:
        return []
    if node.value is None or not any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
        return []
    return [c.value for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)]


def reexports(root: Path, known: dict[str, str]) -> dict[tuple[str, str], str]:
    """For each package ``__init__`` that imports a name from a module, ``(package, name)`` to that module.

    ``from poolhouse.serve import ServerManager`` depends on the module that defines ``ServerManager``,
    not only on the package that forwards it, so a change there reaches the importer in one step.
    """
    out: dict[tuple[str, str], str] = {}
    for name, rel in known.items():
        if not rel.endswith("__init__.py"):
            continue
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = absolute(node, name, True)
                if base in known:
                    out.update({(name, a.asname or a.name): base for a in node.names if f"{base}.{a.name}" not in known})
    return out


def forwarded(package: str, name: str, forwards: dict[tuple[str, str], str]) -> str | None:
    """The module that finally defines ``name`` as ``package`` exposes it, following forwarding packages."""
    found, seen = None, set()
    while (package, name) in forwards and package not in seen:
        seen.add(package)
        package = found = forwards[(package, name)]
    return found


Raw = tuple[list[str], list[list[str]]]
"""What a source names before any index is consulted: dotted names, and the ``[package, name]`` couples a package may forward."""


def raw_references(source: str, module: str, is_package: bool) -> Raw:
    """The dotted names ``source`` imports or mentions in a string, and the ``(package, name)`` couples it imports from a package."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []
    wanted: set[str] = set()
    asked: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            wanted.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = absolute(node, module, is_package)
            wanted.add(base)
            wanted.update(f"{base}.{alias.name}" for alias in node.names)
            asked.update((base, alias.name) for alias in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            wanted.update(m.group(1) for m in DOTTED.finditer(node.value))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            wanted.update(plugin_names(node))
    return sorted(wanted), [list(couple) for couple in sorted(asked)]


def resolved(raw: Raw, module: str, known: dict[str, str], forwards: dict[tuple[str, str], str] | None = None) -> set[str]:
    """The modules in ``known`` that a source's raw names reach, through the packages that forward them."""
    wanted = set(raw[0])
    wanted.update(d for base, name in raw[1] if (d := forwarded(base, name, forwards or {})))
    found: set[str] = set()
    for name in wanted:
        hit = resolve(name, known)
        if hit:
            found |= parents(hit, known)
    found.discard(module)
    return found


def references(source: str, module: str, is_package: bool, known: dict[str, str],
               forwards: dict[tuple[str, str], str] | None = None) -> set[str]:
    """The modules in ``known`` that ``source`` imports or names in a string."""
    return resolved(raw_references(source, module, is_package), module, known, forwards)


MEMO_NAME = "affected-references.json"
MEMO_VERSION = 1
MEMO_SLACK = 3


def memo_path(root: Path) -> Path | None:
    """Where the parsed references of ``root``'s sources are kept: beside the objects every worktree of it shares; None outside one."""
    try:
        done = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root,
                              capture_output=True, text=True, check=False)
    except OSError:
        return None
    return Path(done.stdout.strip()) / MEMO_NAME if done.returncode == 0 and done.stdout.strip() else None


def memo_key(module: str, is_package: bool, text: str) -> str:
    """The name of one source's parse: a function of its module name, whether it is a package and its text."""
    return hashlib.blake2b(f"{MEMO_VERSION}\0{module}\0{is_package}\0{text}".encode(), digest_size=16).hexdigest()


def load_memo(path: Path | None) -> dict[str, Raw]:
    """The parses kept at ``path``; a file that is missing or unreadable keeps none, and every source is parsed again."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}
    return {k: (v[0], v[1]) for k, v in data.items() if isinstance(v, list) and len(v) == 2} if isinstance(data, dict) else {}


def save_memo(path: Path | None, memo: dict[str, Raw]) -> None:
    """Replace the file at ``path`` with ``memo`` in one rename; a failure to write only costs the next run its parses."""
    if path is None:
        return
    try:
        scratch = path.with_name(f"{path.name}.{os.getpid()}")
        scratch.write_text(json.dumps(memo), encoding="utf-8")
        scratch.replace(path)
    except OSError:
        pass


def graph(root: Path, known: dict[str, str], memo: dict[str, Raw] | None = None,
          raws: dict[str, Raw] | None = None) -> dict[str, set[str]]:
    """Module to the modules it depends on, over the whole index.

    ``memo`` (key to parse) is read and filled in place; ``raws``, when given, is filled with each module's parse.
    """
    edges: dict[str, set[str]] = {}
    forwards = reexports(root, known)
    used: dict[str, Raw] = {}
    for name, rel in known.items():
        if name == "tests.conftest":
            edges[name] = set()
            continue
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        is_package = rel.endswith("__init__.py")
        key = memo_key(name, is_package, text)
        raw = used[key] = (memo or {}).get(key) or raw_references(text, name, is_package)
        edges[name] = resolved(raw, name, known, forwards)
        if raws is not None:
            raws[name] = raw
    if memo is not None:
        memo.update(used)
        if len(memo) > MEMO_SLACK * len(used):    # the sources that moved on: drop their parses
            memo.clear()
            memo.update(used)
    return edges


def dependants(edges: dict[str, set[str]]) -> dict[str, set[str]]:
    """The reverse of ``edges``: module to the modules that depend on it."""
    out: dict[str, set[str]] = {name: set() for name in edges}
    for name, deps in edges.items():
        for dep in deps:
            out.setdefault(dep, set()).add(name)
    return out


def helper_dependants(step: set[str], reverse: dict[str, set[str]], seen: set[str]) -> None:
    """Add to ``step`` what depends on the test helper modules in it, and on those, without using up a step."""
    queue = [y for y in step if y.startswith("tests.") and not y.startswith("tests.test_")]
    while queue:
        for y in reverse.get(queue.pop(), ()):
            if y not in seen and y not in step:
                step.add(y)
                if not y.startswith("tests.test_"):
                    queue.append(y)


def reach(start: set[str], reverse: dict[str, set[str]], depth: int) -> set[str]:
    """``start`` and what depends on it within ``depth`` steps; a test file ends a path and a test helper module is no step."""
    seen, frontier = set(start), set(start)
    for _ in range(depth):
        step = {y for x in frontier for y in reverse.get(x, ()) if y not in seen}
        helper_dependants(step, reverse, seen)
        seen |= step
        frontier = {y for y in step if not y.startswith("tests.")}
    return seen


def test_files(root: Path) -> list[str]:
    """Every collected test file, repo-relative."""
    return sorted(p.relative_to(root).as_posix() for p in (root / "tests").glob("test_*.py"))


def dynamic_tests(root: Path, tests: list[str]) -> list[str]:
    """Test files that import a module chosen at run time or read the console-script table."""
    out = []
    for rel in tests:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        if not (SCRIPT_TABLE.search(text) or "import_module" in text or "__import__" in text):
            continue                              # only these can import a module chosen at run time
        if SCRIPT_TABLE.search(text) or any(
                isinstance(n, ast.Call) and ast.unparse(n.func).endswith(("import_module", "__import__"))
                and n.args and not isinstance(n.args[0], ast.Constant)
                for n in ast.walk(ast.parse(text))):
            out.append(rel)
    return out


def mentioning(root: Path, rel: str, tests: list[str]) -> list[str]:
    """Test files whose text contains the changed path or its base name."""
    needles = {rel, Path(rel).name}
    return [t for t in tests if any(n in (root / t).read_text(encoding="utf-8", errors="replace")
                                    for n in needles)]


def package_dir_modules(rel: str, known: dict[str, str]) -> set[str]:
    """Modules that live beside a non-Python file under a package root."""
    folder = Path(rel).parent.as_posix() + "/"
    return {name for name, path in known.items() if path.startswith(folder)}


def add_script_tests(out: Selection, scripts: rules.ReferenceIndex, rel: str, reverse: dict[str, set[str]],
                     tests: list[str]) -> None:
    """Select the tests that import, run or name the script ``rel``, through helpers too."""
    why, modules, shared = scripts.script_tests(rel)
    if shared:
        out.unmap(rel, shared)
        return
    firm_tests, firm_modules = scripts.firm(rel)
    direct = {n for n in why if not why[n].endswith("enumerates scripts/")}
    for test, reason in why.items():
        out.add(test, f"{rel}: {reason}", firm=test in firm_tests)
    names = {m for m in (module_of(x) for x in modules) if m}
    firm_names = {m for m in (module_of(x) for x in firm_modules) if m}
    for is_firm, group in ((True, firm_names), (False, names - firm_names)):
        for dependant in sorted(reach(group, reverse, DEPTH)):
            if dependant.startswith("tests.") and f"tests/{dependant[6:]}.py" in tests:
                out.add(f"tests/{dependant[6:]}.py", f"{rel} -> a source module that names it", firm=is_firm)
    if not (direct or names or rel.endswith(".py")):
        out.unmap(rel, "no test imports, runs or names it, so nothing says which tests it affects")


def users_of_deleted(root: Path, rel: str, raws: dict[str, Raw], reverse: dict[str, set[str]], tests: list[str]) -> set[str]:
    """The test files that import a deleted module (or something below it), directly or through one module, or name its path."""
    module = module_of(rel)
    users = {m for m, raw in raws.items() if module and any(w == module or w.startswith(f"{module}.") for w in raw[0])}
    reached = {f"tests/{d[6:]}.py" for d in reach(users, reverse, DEPTH) if d.startswith("tests.")}
    return (reached & set(tests)) | set(mentioning(root, rel, tests))


GLOSSARY_TEST = "tests/test_glossary.py"


def glossary_reads(root: Path, changed: list[str], deleted: frozenset[str]) -> list[str]:
    """Changed paths the glossary scan reads, any language: a banned word can arrive in a file no test names.

    The scan's own exclusions decide; where they cannot be loaded, every kept path counts.
    """
    kept = [c for c in changed if c not in deleted]
    try:
        import nomenclature
        vocab = nomenclature.Vocabulary(root=root)
        return [c for c in kept if not vocab.excluded(c) and nomenclature.text_of(c, root) is not None]
    except (ImportError, ValueError):  # unsure selects more
        return kept


ENV_TEST = "tests/test_documented_env_vars.py"
ENV_READS = ("src/", "scripts/", "packaging/", "app/src/", "app/src-tauri/src/", "app/poolhouse-node/src/", "contracts/",
             "tests/pool_check/", "docs/", "pyproject.toml", "README.md", "CHANGELOG.md")
"""The paths the switch-table test scans (its SOURCE_DIRS and SOURCE_FILES, and the docs it reads): a variable can arrive in any of them."""


def add_tree_wide(out: Selection, root: Path, changed: list[str], deleted: frozenset[str], tests: list[str]) -> None:
    """The tests that read the whole tree: the glossary scan for what it reads, the guards once anything runs."""
    read = glossary_reads(root, changed, deleted) if GLOSSARY_TEST in tests else []
    if read:
        out.add(GLOSSARY_TEST, f"{read[0]} is read by the glossary scan")
    scanned = [c for c in changed if c.startswith(ENV_READS)]
    if scanned and ENV_TEST in tests:
        out.add(ENV_TEST, f"{scanned[0]} is scanned for POOLHOUSE_ variables")
    if out.files:
        for guard in GUARDS:
            if f"tests/{guard}" in tests:
                out.add(f"tests/{guard}", "tree-wide guard")


def reverse_graph(root: Path, known: dict[str, str], memo_file: Path | None) -> tuple[dict[str, set[str]], dict[str, Raw]]:
    """Module to the modules that depend on it, and each module's parse; the parses are kept in ``memo_file`` between calls."""
    memo = load_memo(memo_file)
    before = set(memo)
    raws: dict[str, Raw] = {}
    reverse = dependants(graph(root, known, memo, raws))
    if memo_file is not None and memo.keys() != before:
        save_memo(memo_file, memo)
    return reverse, raws


def select(root: Path, changed: list[str], deleted: frozenset[str] = frozenset(),
           depth: int = DEPTH, base: str = "HEAD") -> Selection:
    """The test files within ``depth`` imports of ``changed``; ``unmapped`` non-empty means run all.

    Each source's parse is kept between calls in the repository's directory of ``root`` (:func:`memo_path`), named by the
    source's text, so a call parses only what moved; a ``root`` outside a repository keeps nothing.
    """
    out = Selection()
    known = index(root)
    tests = test_files(root)
    reverse, raws = reverse_graph(root, known, memo_path(root))
    scripts = None
    for rel in changed:
        name = Path(rel).name
        module = module_of(rel)
        script = rel.startswith("scripts/") and not rel.startswith("scripts/gates/")
        if rel in deleted and (module or script):
            out.unmap(rel, "a deleted module: anything may have used it")
            for test in sorted(users_of_deleted(root, rel, raws, reverse, tests)):
                out.add(test, f"{rel} was deleted and the test imports or names it")
        elif rel == "budgets.json":
            if rules.budgets_lowered(root, base):
                for test, why in rules.budget_tests(rules.corpus(root)).items():
                    out.add(test, f"{rel} only fell: {why}")
            else:
                out.unmap(rel, "a budget rose or the file changed in shape: every gate's baseline moved")
        elif rel in EVERYTHING:
            out.unmap(rel, "shared by every test")
        elif name in INERT or ((rel.endswith(".md") or rel.startswith(".github/")) and not mentioning(root, rel, tests)):
            out.ignored.append(rel)               # no test names it, so it cannot change a result (CI config, CODEOWNERS ...)
        elif rel in rules.GENERATED:
            checks = rules.generated_tests(rules.corpus(root), rel)
            for test, why in checks.items():
                out.add(test, f"{rel} is generated: {why}")
            if not checks:
                out.ignored.append(rel)           # only the gate (--check) reads it
        elif script:
            if scripts is None:
                scripts = rules.ReferenceIndex(rules.corpus(root))
            add_script_tests(out, scripts, rel, reverse, tests)
            if module:
                for dependant in sorted(reach({module}, reverse, depth)):
                    if dependant.startswith("tests.") and f"tests/{dependant[6:]}.py" in tests:
                        out.add(f"tests/{dependant[6:]}.py", f"{rel} -> {module}")
        elif module:
            hit = reach({module}, reverse, depth)
            for dependant in sorted(hit):
                if dependant.startswith("tests.") and f"tests/{dependant[6:]}.py" in tests:
                    out.add(f"tests/{dependant[6:]}.py", f"{rel} -> {module}")
        elif rel.startswith(("src/poolhouse/", "scripts/gates/")):
            near = package_dir_modules(rel, known)
            for dependant in sorted(reach(near, reverse, depth)):
                if dependant.startswith("tests.") and f"tests/{dependant[6:]}.py" in tests:
                    out.add(f"tests/{dependant[6:]}.py", f"{rel} -> its package")
        else:
            said = mentioning(root, rel, tests)
            for t in said:
                out.add(t, f"{rel} named in the test")
            if not said:
                out.unmap(rel, "not source, a test or prose, and no test names it")
    if any(Path(c).parts[:2] == ("src", "poolhouse") for c in changed):
        for rel in dynamic_tests(root, tests):
            out.add(rel, "imports a module chosen at run time")
    add_tree_wide(out, root, changed, deleted, tests)
    return out


def lines(root: Path, *args: str) -> list[str]:
    """Output lines of a git command run in ``root``."""
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return [x for x in done.stdout.splitlines() if x]


def changed_since(root: Path, base: str) -> tuple[list[str], frozenset[str]]:
    """Paths changed against the merge-base with ``base``, working tree and untracked included.

    The second value is the subset that no longer exists.
    """
    mb = lines(root, "merge-base", base, "HEAD")[0]
    paths = set(lines(root, "diff", "--name-only", "--no-renames", mb))
    paths |= set(lines(root, "ls-files", "--others", "--exclude-standard"))
    gone = frozenset(p for p in paths if not (root / p).exists())
    return sorted(paths), gone
