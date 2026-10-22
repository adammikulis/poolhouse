"""The parses ``scripts/affected.py`` keeps between calls change nothing about what it selects, and stay right when a source moves."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import affected


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def toy(tmp_path: Path) -> Path:
    write(tmp_path, {"src/poolhouse/__init__.py": "", "src/poolhouse/a.py": "X = 1\n", "src/poolhouse/b.py": "Y = 2\n",
                     "tests/__init__.py": "", "tests/test_a.py": "from poolhouse import a\n", "tests/test_b.py": "from poolhouse import b\n"})
    return tmp_path


def chosen(root: Path, changed: list[str], memo: Path | None = None) -> set[str]:
    return affected.select(root, changed, memo_file=memo).files


def test_a_selection_with_the_parses_kept_equals_one_without(tmp_path):
    root, memo = toy(tmp_path), tmp_path / "memo.json"
    plain = chosen(root, ["src/poolhouse/a.py"])
    assert chosen(root, ["src/poolhouse/a.py"], memo) == plain == {"tests/test_a.py"}
    assert memo.is_file()
    assert chosen(root, ["src/poolhouse/a.py"], memo) == plain


def test_a_source_that_changed_after_the_parses_were_kept_is_read_again(tmp_path):
    root, memo = toy(tmp_path), tmp_path / "memo.json"
    assert chosen(root, ["src/poolhouse/b.py"], memo) == {"tests/test_b.py"}
    write(root, {"tests/test_a.py": "from poolhouse import a, b\n"})
    assert chosen(root, ["src/poolhouse/b.py"], memo) == {"tests/test_a.py", "tests/test_b.py"}


def test_a_damaged_file_of_parses_is_ignored_and_replaced(tmp_path):
    root, memo = toy(tmp_path), tmp_path / "memo.json"
    for damage in ("not json", '{"k": 1}', "[]", '{"k": [1]}'):
        memo.write_text(damage, encoding="utf-8")
        assert chosen(root, ["src/poolhouse/a.py"], memo) == {"tests/test_a.py"}
        assert isinstance(json.loads(memo.read_text(encoding="utf-8")), dict)


def test_the_parses_of_sources_that_moved_on_are_dropped(tmp_path):
    root, memo = toy(tmp_path), tmp_path / "memo.json"
    for version in range(12):
        write(root, {"src/poolhouse/a.py": f"X = {version}\n"})
        chosen(root, ["src/poolhouse/a.py"], memo)
    assert len(json.loads(memo.read_text(encoding="utf-8"))) <= affected.MEMO_SLACK * 7


def test_a_test_that_imports_a_module_chosen_at_run_time_is_still_selected(tmp_path):
    root = toy(tmp_path)
    write(root, {"tests/test_dynamic.py": "import importlib\n\n\ndef test_x(name):\n    importlib.import_module(name)\n",
                 "tests/test_plain.py": "def test_y():\n    assert True\n"})
    assert chosen(root, ["src/poolhouse/a.py"]) == {"tests/test_a.py", "tests/test_dynamic.py"}


def test_a_deleted_module_selects_the_tests_that_import_it(tmp_path):
    root = toy(tmp_path)
    (root / "src/poolhouse/b.py").unlink()
    out = affected.select(root, ["src/poolhouse/b.py"], frozenset({"src/poolhouse/b.py"}))
    assert out.files == {"tests/test_b.py"}
    assert out.unmapped == ["src/poolhouse/b.py"]
