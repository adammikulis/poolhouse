"""`scripts/affected.py` follows a name a package forwards to the module that defines it.

A test that does `from poolhouse.serve import ServerManager` runs the code in `serve/manager.py`; a
change there must select it at the usual one-step depth, not only at two steps through the
package's `__init__`. Each case builds a small package on disk.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import affected  # noqa: E402

TREE = {
    "src/poolhouse/__init__.py": "",
    "src/poolhouse/pkg/__init__.py": "from poolhouse.pkg.core import Thing\nfrom .more import other\n",
    "src/poolhouse/pkg/core.py": "class Thing:\n    pass\n",
    "src/poolhouse/pkg/more.py": "def other():\n    return 1\n",
    "src/poolhouse/pkg/unused.py": "VALUE = 1\n",
    "src/poolhouse/outer/__init__.py": "from poolhouse.pkg import Thing\n",
    "tests/conftest.py": "",
    "tests/test_forwarded.py": "from poolhouse.pkg import Thing\n\n\ndef test_it():\n    assert Thing\n",
    "tests/test_relative.py": "from poolhouse.pkg import other\n\n\ndef test_it():\n    assert other()\n",
    "tests/test_chained.py": "from poolhouse.outer import Thing\n\n\ndef test_it():\n    assert Thing\n",
    "tests/test_submodule.py": "from poolhouse.pkg import unused\n\n\ndef test_it():\n    assert unused.VALUE\n",
    "tests/test_other.py": "import poolhouse\n\n\ndef test_it():\n    assert poolhouse\n",
}


@pytest.fixture
def pkg(tmp_path: Path) -> Path:
    for rel, text in TREE.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def chosen(root: Path, changed: str) -> set[str]:
    out = affected.select(root, [changed])
    assert not out.unmapped
    return {t for t in out.files if not t.endswith(("test_layers.py", "test_wiring.py"))}


def test_a_name_a_package_forwards_selects_the_importer_when_its_defining_module_changes(pkg) -> None:
    assert "tests/test_forwarded.py" in chosen(pkg, "src/poolhouse/pkg/core.py")


def test_a_relative_forwarding_import_is_followed_too(pkg) -> None:
    assert "tests/test_relative.py" in chosen(pkg, "src/poolhouse/pkg/more.py")


def test_a_name_forwarded_through_two_packages_reaches_its_defining_module(pkg) -> None:
    assert "tests/test_chained.py" in chosen(pkg, "src/poolhouse/pkg/core.py")


def test_a_module_the_importer_does_not_name_stays_unselected(pkg) -> None:
    got = chosen(pkg, "src/poolhouse/pkg/core.py")
    assert "tests/test_submodule.py" not in got and "tests/test_relative.py" not in got
    assert "tests/test_other.py" not in got
