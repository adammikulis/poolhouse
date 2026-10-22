"""`scripts/affected.py` places a changed script, a lowered budget and a generated file narrowly.

Each case builds a small repository on disk, so what is selected comes from real files and real
`git show` output; the last cases replay recent commits of this repository.
"""

from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from collections import Counter
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import affected  # noqa: E402
import affected_rules as rules  # noqa: E402

FILES = {
    "scripts/alpha.py": "def one():\n    return 1\n",
    "scripts/beta.py": "import alpha\n\n\ndef two():\n    return alpha.one()\n",
    "scripts/runner": "#!/usr/bin/env python3\nprint('run')\n",
    "scripts/hooks/guard": "#!/bin/sh\nexit 0\n",
    "scripts/wrapper.sh": "#!/bin/sh\nexec ./runner \"$@\"\n",
    "scripts/launcher": "#!/usr/bin/env python3\nimport alpha\n",
    "scripts/orphan.py": "VALUE = 1\n",
    "scripts/data.txt": "nothing reads this\n",
    "tests/kit.py": "import subprocess\nRUNNER = 'scripts/runner'\n\n\ndef go():\n    subprocess.run([RUNNER])\n",
    "tests/test_import.py": "import alpha\n\n\ndef test_it():\n    assert alpha.one()\n",
    "tests/test_from.py": "from alpha import one\n\n\ndef test_it():\n    assert one()\n",
    "tests/test_string.py": "def test_it():\n    assert 'scripts/runner' and 'scripts/alpha.py'\n",
    "tests/test_subprocess.py": (
        "import subprocess\nimport sys\nfrom pathlib import Path\nSCRIPTS = Path('x') / 'scripts'\n\n\n"
        "def test_it():\n    subprocess.run([sys.executable, str(SCRIPTS / 'runner')], check=True)\n"),
    "tests/test_guard.py": "import subprocess\n\n\ndef test_it():\n    subprocess.run(['scripts/hooks/guard'])\n",
    "tests/test_launcher.py": "def test_it():\n    assert 'scripts/launcher'\n",
    "tests/test_wrapper.py": "def test_it():\n    assert 'scripts/wrapper.sh'\n",
    "tests/test_helper.py": "import kit\n\n\ndef test_it():\n    kit.go()\n",
    "tests/test_chain.py": "import beta\n\n\ndef test_it():\n    assert beta.two()\n",
    "tests/test_doc.py": 'def test_it():\n    """Mentions scripts/runner in prose only."""\n',
    "tests/test_unrelated.py": "def test_it():\n    assert 'test' == 'test'\n",
    "tests/test_lister.py": "from pathlib import Path\n\n\ndef test_it():\n    assert list(Path('scripts').glob('*'))\n",
    "tests/test_budgets.py": "def test_it():\n    assert 1\n",
    "tests/test_reads.py": "import json\n\n\ndef test_it():\n    json.load(open('budgets.json'))\n",
    "tests/test_gen.py": "def test_it():\n    assert 'coverage.json'\n",
    "tests/conftest.py": "",
    "budgets.json": json.dumps({"a": 5, "b": 3, "floors": {"n": 10}}),
    "docs/redteam/coverage.json": "{}\n",
    "scripts/redteam_coverage.py": "print('gen')\n",
    "tests/test_cover.py": "def test_it():\n    assert 'scripts/redteam_coverage.py'\n",
}


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def chosen(root: Path, *changed: str, **kw) -> set[str]:
    out = affected.select(root, list(changed), **kw)
    return set(out.files) - {"tests/test_conftest_guard.py"}


def test_a_script_is_selected_by_the_tests_that_import_it_either_way(toy) -> None:
    got = chosen(toy, "scripts/alpha.py")
    assert {"tests/test_import.py", "tests/test_from.py"} <= got


def test_a_script_is_selected_by_the_tests_that_name_its_path_or_build_it(toy) -> None:
    assert "tests/test_string.py" in chosen(toy, "scripts/alpha.py")
    assert {"tests/test_string.py", "tests/test_subprocess.py"} <= chosen(toy, "scripts/runner")


def test_an_extensionless_script_in_a_folder_is_found_by_its_path(toy) -> None:
    assert "tests/test_guard.py" in chosen(toy, "scripts/hooks/guard")


def test_a_helper_that_names_the_script_carries_it_to_the_tests_that_use_the_helper(toy) -> None:
    got = chosen(toy, "scripts/runner")
    assert "tests/test_helper.py" in got and "tests/test_chain.py" not in got


def test_a_script_another_script_imports_reaches_the_tests_of_the_importer(toy) -> None:
    assert "tests/test_chain.py" in chosen(toy, "scripts/alpha.py")


def test_an_extensionless_python_script_that_imports_the_changed_one_carries_it_to_its_tests(toy) -> None:
    assert "tests/test_launcher.py" in chosen(toy, "scripts/alpha.py")


def test_a_shell_script_that_runs_the_changed_one_carries_it_to_the_tests_of_the_shell_script(toy) -> None:
    assert "tests/test_wrapper.py" in chosen(toy, "scripts/runner")


def test_tests_that_enumerate_scripts_follow_any_script_change(toy) -> None:
    assert "tests/test_lister.py" in chosen(toy, "scripts/orphan.py")


def test_a_script_selects_nothing_it_cannot_reach(toy) -> None:
    got = chosen(toy, "scripts/runner")
    assert not got & {"tests/test_import.py", "tests/test_unrelated.py", "tests/test_doc.py"}


def test_reference_index_reuses_exact_matches_across_changed_scripts(toy, monkeypatch):
    files = rules.corpus(toy)
    expected = {rel: rules.script_tests(files, rel)
                for rel in ("scripts/runner", "scripts/alpha.py", "scripts/beta.py")}
    matched = Counter()
    original = rules.refers

    def counted(source, target):
        matched[(source.rel, target.rel)] += 1
        return original(source, target)

    monkeypatch.setattr(rules, "refers", counted)
    prepared = rules.ReferenceIndex(files)
    for _ in range(2):
        for rel, result in expected.items():
            assert prepared.script_tests(rel) == result
    assert matched and max(matched.values()) == 1


def test_reference_index_owns_an_immutable_source_snapshot(toy):
    files = rules.corpus(toy)
    prepared = rules.ReferenceIndex(files)
    previous = prepared.script_tests("scripts/runner")
    del files["tests/test_string.py"]
    files["tests/test_added.py"] = rules.parse("tests/test_added.py", "RUNNER = 'scripts/runner'")
    files["tests/test_helper.py"] = rules.parse("tests/test_helper.py", "VALUE = 0")
    assert prepared.script_tests("scripts/runner") == previous
    fresh, _, _ = rules.script_tests(files, "scripts/runner")
    assert "tests/test_string.py" not in fresh
    assert "tests/test_helper.py" not in fresh
    assert "tests/test_added.py" in fresh
    with pytest.raises(FrozenInstanceError):
        files["tests/test_added.py"].text = "VALUE = 0"


def test_multiple_changed_scripts_share_one_selection_snapshot(toy, monkeypatch):
    scanned = []
    original = rules.corpus

    def counted(root):
        scanned.append(root)
        return original(root)

    monkeypatch.setattr(rules, "corpus", counted)
    got = chosen(toy, "scripts/runner", "scripts/alpha.py")
    assert {"tests/test_subprocess.py", "tests/test_import.py", "tests/test_chain.py"} <= got
    assert scanned == [toy]


def test_a_script_no_test_reaches_is_unmapped_unless_it_is_python(toy) -> None:
    out = affected.select(toy, ["scripts/data.txt"])
    assert out.unmapped == ["scripts/data.txt"] and "no test" in out.reasons["scripts/data.txt"]
    assert affected.select(toy, ["scripts/orphan.py"]).unmapped == []


def test_a_deleted_script_runs_everything(toy) -> None:
    out = affected.select(toy, ["scripts/runner"], frozenset({"scripts/runner"}))
    assert out.unmapped == ["scripts/runner"] and "deleted" in out.reasons["scripts/runner"]


def test_a_script_the_conftest_imports_runs_everything_and_says_why(toy) -> None:
    (toy / "tests/conftest.py").write_text("import alpha\n", encoding="utf-8")
    out = affected.select(toy, ["scripts/alpha.py"])
    assert out.unmapped == ["scripts/alpha.py"] and "every test session" in out.reasons["scripts/alpha.py"]
    assert affected.select(toy, ["scripts/runner"]).unmapped == []


def test_a_plugin_the_runner_loads_runs_everything_and_so_does_what_it_imports(toy) -> None:
    (toy / "scripts/test").write_text("ARGS = ['-p', 'plug']\n", encoding="utf-8")
    (toy / "scripts/plug.py").write_text("import beta\n", encoding="utf-8")
    assert affected.select(toy, ["scripts/plug.py"]).unmapped == ["scripts/plug.py"]
    assert affected.select(toy, ["scripts/beta.py"]).unmapped == ["scripts/beta.py"]
    assert affected.select(toy, ["scripts/orphan.py"]).unmapped == []


def test_a_name_a_helper_holds_does_not_make_a_script_session_wide(toy) -> None:
    (toy / "tests/conftest.py").write_text("import kit\n", encoding="utf-8")
    assert affected.select(toy, ["scripts/runner"]).unmapped == []


def run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                   check=True, capture_output=True)


@pytest.fixture
def repo(toy: Path) -> Path:
    run_git(toy, "init", "-q", "-b", "base")
    run_git(toy, "add", ".")
    run_git(toy, "commit", "-q", "-m", "base")
    run_git(toy, "checkout", "-q", "-b", "work")
    return toy


def write_budgets(root: Path, value: object) -> None:
    (root / "budgets.json").write_text(json.dumps(value), encoding="utf-8")


def test_a_budget_that_only_fell_selects_the_budget_tests(repo) -> None:
    write_budgets(repo, {"a": 4, "b": 3, "floors": {"n": 9}})
    out = affected.select(repo, ["budgets.json"], base="base")
    assert out.unmapped == [] and {"tests/test_budgets.py", "tests/test_reads.py"} <= out.files
    assert "tests/test_import.py" not in out.files


@pytest.mark.parametrize("after", [
    {"a": 6, "b": 3, "floors": {"n": 10}},
    {"a": 5, "b": 3, "floors": {"n": 11}},
    {"a": 5, "b": 3, "floors": {"n": 10}, "c": 1},
    {"a": 5, "b": 3},
    {"a": 4.5, "b": 3, "floors": {"n": 10}},
    {"a": True, "b": 3, "floors": {"n": 10}},
    {"a": "4", "b": 3, "floors": {"n": 10}},
])
def test_a_budget_that_rose_or_changed_shape_runs_everything(repo, after) -> None:
    write_budgets(repo, after)
    out = affected.select(repo, ["budgets.json"], base="base")
    assert out.unmapped == ["budgets.json"] and "budget" in out.reasons["budgets.json"]


def test_a_budget_with_no_base_to_compare_against_runs_everything(repo) -> None:
    write_budgets(repo, {"a": 1, "b": 1, "floors": {"n": 1}})
    assert affected.select(repo, ["budgets.json"], base="nowhere").unmapped == ["budgets.json"]


def test_a_generated_file_selects_the_tests_that_check_it_and_its_generator(toy) -> None:
    got = chosen(toy, "docs/redteam/coverage.json")
    assert {"tests/test_gen.py", "tests/test_cover.py"} <= got
    assert "tests/test_import.py" not in got


def test_a_generated_file_nothing_checks_is_left_to_the_gate(toy) -> None:
    (toy / "tests/test_gen.py").unlink()
    (toy / "tests/test_cover.py").unlink()
    out = affected.select(toy, ["docs/redteam/coverage.json"])
    assert out.unmapped == [] and out.ignored == ["docs/redteam/coverage.json"]


def test_an_unknown_file_type_still_runs_everything_and_says_why(toy) -> None:
    out = affected.select(toy, ["contracts/shape.bin"])
    assert out.unmapped == ["contracts/shape.bin"] and out.reasons["contracts/shape.bin"]
    assert affected.select(toy, ["pyproject.toml"]).reasons["pyproject.toml"]


def glossary_toy(toy: Path, with_policy: bool) -> Path:
    """The toy repository with a glossary test, a Rust test file that one other test names, and optionally the real policy."""
    (toy / "tests" / "test_glossary.py").write_text("def test_it():\n    assert 1\n", encoding="utf-8")
    (toy / "tests" / "test_native.py").write_text("def test_it():\n    assert 'native_case.rs'\n", encoding="utf-8")
    (toy / "app").mkdir()
    (toy / "app" / "native_case.rs").write_text("struct Duo;\n", encoding="utf-8")
    (toy / "docs" / "notes").mkdir(parents=True, exist_ok=True)
    (toy / "docs" / "notes" / "old.rs").write_text("struct Duo;\n", encoding="utf-8")
    if with_policy:
        (toy / "contracts").mkdir()
        for name in ("glossary-rules.toml", "glossary-allowlist.toml"):
            (toy / "contracts" / name).write_text((REPO / "contracts" / name).read_text(encoding="utf-8"), encoding="utf-8")
    return toy


def test_a_rust_file_a_test_names_still_selects_the_glossary_scan(toy) -> None:
    root = glossary_toy(toy, with_policy=True)
    got = chosen(root, "app/native_case.rs")
    assert got == {"tests/test_native.py", "tests/test_glossary.py"}


def test_the_glossary_scan_is_selected_when_its_policy_cannot_be_read(toy) -> None:
    root = glossary_toy(toy, with_policy=False)
    assert "tests/test_glossary.py" in chosen(root, "app/native_case.rs")


def test_a_file_the_glossary_scan_leaves_out_does_not_select_it(toy) -> None:
    root = glossary_toy(toy, with_policy=True)
    assert "tests/test_glossary.py" not in chosen(root, "docs/notes/old.rs")
    assert "tests/test_glossary.py" not in chosen(root, "app/native_case.rs", deleted=frozenset({"app/native_case.rs"}))


def recent_scripts(count: int) -> list[str]:
    """Scripts that still exist and were touched by the latest commits that touched scripts/."""
    done = subprocess.run(["git", "log", "-n", "400", "--no-renames", "--name-only", "--format=@@%h",
                           "--", "scripts/"], cwd=REPO, capture_output=True, text=True, check=False)
    commits = [c.splitlines()[1:] for c in done.stdout.split("@@") if c.strip()]
    sampled = commits[:: max(1, len(commits) // count)][:count]
    found = {p for c in sampled for p in c if p.startswith("scripts/") and "/gates/" not in p
             and (REPO / p).is_file() and not p.endswith((".md", ".json", ".txt"))}
    return sorted(found)


def executable_source(text: str) -> str:
    """Source with actual docstrings and comments replaced by spaces."""
    try:
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, ValueError, tokenize.TokenError, IndentationError):
        return text
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def position(row: int, column: int, byte_column: bool = False) -> int:
        line = lines[row - 1]
        if byte_column:
            column = len(line.encode("utf-8")[:column].decode("utf-8"))
        return offsets[row - 1] + column

    spans = [(position(token.start[0], token.start[1]),
              position(token.end[0], token.end[1])) for token in tokens
             if token.type == tokenize.COMMENT]
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                value = first.value
                spans.append((position(value.lineno, value.col_offset, True),
                              position(value.end_lineno, value.end_col_offset, True)))
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def oracle_sources(root: Path) -> dict[str, str]:
    """Executable test sources enumerated independently from the production corpus."""
    return {f"tests/{path.name}": executable_source(path.read_text(encoding="utf-8", errors="replace"))
            for path in sorted((root / "tests").glob("test_*.py"))}


def plain_oracle(script: str, sources: dict[str, str] | None = None) -> set[str]:
    """Test files that import the script or put its path in quotes, found with plain regexes."""
    stem = Path(script).stem if script.endswith(".py") else Path(script).name
    imports = re.compile(rf"^\s*(?:import|from)\s+{re.escape(stem)}\b", re.M)
    quoted = re.compile(rf"""["']{re.escape(script)}(?![\w.-])""")
    if sources is None:
        sources = oracle_sources(REPO)
    out = set()
    for name, text in sources.items():
        if (script.endswith(".py") and imports.search(text)) or quoted.search(text):
            out.add(name)
    return out


@pytest.mark.gate
def test_every_test_a_plain_search_finds_for_a_recently_changed_script_is_selected() -> None:
    scripts = recent_scripts(30)
    assert len(scripts) >= 5
    files = rules.corpus(REPO)
    references = rules.ReferenceIndex(files)
    sources = oracle_sources(REPO)
    missed = {}
    for script in scripts:
        why, _, shared = references.script_tests(script)
        if not shared and (gap := plain_oracle(script, sources) - set(why)):
            missed[script] = sorted(gap)
    assert not missed


@pytest.mark.parametrize("source", [
    '\"\"\"scripts/runner\"\"\"',
    'class Scope:\n    \"\"\"scripts/runner\"\"\"',
    'def scope():\n    \"\"\"scripts/runner\"\"\"',
    'async def scope():\n    \"\"\"scripts/runner\"\"\"',
    '# "scripts/runner"',
    'def é(): "scripts/runner"',
])
def test_oracle_excludes_actual_docstrings_and_comments(source):
    assert "scripts/runner" not in executable_source(source)


@pytest.mark.parametrize("source", [
    'value = "scripts/runner"',
    'value = \"\"\"scripts/runner\"\"\"',
    'value = f"scripts/runner"',
    'def scope():\n    pass\n    \"\"\"scripts/runner\"\"\"',
    'class Scope:\n    value: "scripts/runner"',
    'def scope():\n    "docstring"\n    return "scripts/runner"',
    'label = "é"; value = "scripts/runner"',
])
def test_oracle_retains_executable_strings(source):
    assert "scripts/runner" in executable_source(source)


def test_oracle_keeps_imports_and_nested_executable_references():
    source = '\"\"\"module docs\"\"\"\nimport alpha\ndef scope():\n    "docs"\n    return "scripts/runner"'
    result = executable_source(source)
    assert "import alpha" in result and '"scripts/runner"' in result


def test_oracle_keeps_unparseable_source_conservatively():
    source = 'broken = (\n"scripts/runner"'
    assert executable_source(source) == source


def test_oracle_masks_unicode_docstring_without_hiding_following_literal():
    source = 'def é(): "docs"; return "scripts/runner"'
    result = executable_source(source)
    assert '"docs"' not in result and '"scripts/runner"' in result


def test_oracle_matches_every_executable_literal_style():
    sources = {"tests/test_literals.py": executable_source(
        'a = "scripts/runner"; b = \"\"\"scripts/runner\"\"\"; c = f"scripts/runner"')}
    assert plain_oracle("scripts/runner", sources) == {"tests/test_literals.py"}


def test_oracle_detects_a_test_omitted_from_production_corpus(toy, monkeypatch):
    files = rules.corpus(toy)
    del files["tests/test_string.py"]
    monkeypatch.setattr(rules, "corpus", lambda root: files)
    selected, _, _ = rules.script_tests(files, "scripts/runner")
    expected = plain_oracle("scripts/runner", oracle_sources(toy))
    assert "tests/test_string.py" in expected - set(selected)
