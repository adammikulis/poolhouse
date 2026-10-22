"""A test file that only a bare word in a string reaches is selected but told apart from one that imports or runs the changed script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import affected


def toy(tmp_path: Path) -> Path:
    files = {"scripts/runner.py": "VALUE = 1\n",
             "tests/__init__.py": "",
             "tests/test_imports.py": "import runner\n\n\ndef test_it():\n    assert runner.VALUE\n",
             "tests/test_word.py": "WHERE = 'scripts'\nNAME = 'runner'\n\n\ndef test_it():\n    assert NAME\n",
             "tests/test_path.py": "PATH = 'scripts/runner.py'\n\n\ndef test_it():\n    assert PATH\n"}
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_a_bare_word_reaches_a_test_but_only_loosely(tmp_path):
    out = affected.select(toy(tmp_path), ["scripts/runner.py"])
    assert {"tests/test_imports.py", "tests/test_path.py", "tests/test_word.py"} <= out.files
    assert out.loose == {"tests/test_word.py"}
    assert {"tests/test_imports.py", "tests/test_path.py"} <= out.firm


def test_a_test_that_one_firm_path_reaches_is_not_loose(tmp_path):
    root = toy(tmp_path)
    (root / "tests/test_word.py").write_text("WHERE = 'scripts/runner.py'\nNAME = 'runner'\n", encoding="utf-8")
    assert affected.select(root, ["scripts/runner.py"]).loose == set()


def test_a_change_of_prose_alone_selects_no_tree_wide_guard_but_a_change_of_code_does(tmp_path):
    root = toy(tmp_path)
    (root / "docs").mkdir()
    (root / "docs/notes.md").write_text("x\n", encoding="utf-8")
    (root / "tests/test_notes.py").write_text("TEXT = open('docs/notes.md').read()\n", encoding="utf-8")
    (root / "tests/test_suite.py").write_text("def test_guard():\n    assert True\n", encoding="utf-8")
    assert affected.select(root, ["docs/notes.md"]).files == {"tests/test_notes.py"}
    assert "tests/test_suite.py" in affected.select(root, ["docs/notes.md", "scripts/runner.py"]).files
