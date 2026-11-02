"""The parses ``scripts/affected.py`` keeps between calls change nothing about what it selects, and stay right when a source moves."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import affected


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def toy(tmp_path: Path, repository: bool = True) -> Path:
    if repository:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path, {"src/poolhouse/__init__.py": "", "src/poolhouse/a.py": "X = 1\n", "src/poolhouse/b.py": "Y = 2\n",
                     "tests/__init__.py": "", "tests/test_a.py": "from poolhouse import a\n", "tests/test_b.py": "from poolhouse import b\n"})
    return tmp_path


def kept(root: Path) -> dict:
    return json.loads((root / ".git" / affected.MEMO_NAME).read_text(encoding="utf-8"))


def test_a_selection_keeps_the_parses_and_a_second_one_equals_the_first(tmp_path):
    root = toy(tmp_path)
    first = affected.select(root, ["src/poolhouse/a.py"]).files
    assert first == {"tests/test_a.py"}
    assert len(kept(root)) >= 5
    assert affected.select(root, ["src/poolhouse/a.py"]).files == first


def test_a_root_outside_a_repository_keeps_nothing_and_selects_the_same(tmp_path):
    root = toy(tmp_path, repository=False)
    assert affected.memo_path(root) is None
    assert affected.select(root, ["src/poolhouse/a.py"]).files == {"tests/test_a.py"}


def test_a_source_that_changed_after_the_parses_were_kept_is_read_again(tmp_path):
    root = toy(tmp_path)
    assert affected.select(root, ["src/poolhouse/b.py"]).files == {"tests/test_b.py"}
    write(root, {"tests/test_a.py": "from poolhouse import a, b\n"})
    assert affected.select(root, ["src/poolhouse/b.py"]).files == {"tests/test_a.py", "tests/test_b.py"}


def test_a_damaged_file_of_parses_is_ignored_and_replaced(tmp_path):
    root = toy(tmp_path)
    memo = root / ".git" / affected.MEMO_NAME
    for damage in ("not json", '{"k": 1}', "[]", '{"k": [1]}'):
        memo.write_text(damage, encoding="utf-8")
        assert affected.select(root, ["src/poolhouse/a.py"]).files == {"tests/test_a.py"}
        assert isinstance(kept(root), dict) and len(kept(root)) >= 5


def test_the_parses_of_sources_that_moved_on_are_dropped(tmp_path):
    root = toy(tmp_path)
    for version in range(12):
        write(root, {"src/poolhouse/a.py": f"X = {version}\n"})
        affected.select(root, ["src/poolhouse/a.py"])
    assert len(kept(root)) <= affected.MEMO_SLACK * 7


def test_a_test_that_imports_a_module_chosen_at_run_time_is_still_selected(tmp_path):
    root = toy(tmp_path)
    write(root, {"tests/test_dynamic.py": "import importlib\n\n\ndef test_x(name):\n    importlib.import_module(name)\n",
                 "tests/test_plain.py": "def test_y():\n    assert True\n"})
    assert affected.select(root, ["src/poolhouse/a.py"]).files == {"tests/test_a.py", "tests/test_dynamic.py"}


def test_a_deleted_module_selects_the_tests_that_import_it(tmp_path):
    root = toy(tmp_path)
    (root / "src/poolhouse/b.py").unlink()
    out = affected.select(root, ["src/poolhouse/b.py"], frozenset({"src/poolhouse/b.py"}))
    assert out.files == {"tests/test_b.py"}
    assert out.unmapped == ["src/poolhouse/b.py"]
