"""scripts/testslots.py: every module that loads a real model takes a heavy lane."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import testslots  # noqa: E402


def marks_real_model(tree: ast.AST) -> bool:
    """Whether the module uses ``pytest.mark.real_model`` anywhere (as a module mark or on a test)."""
    return any(isinstance(n, ast.Attribute) and n.attr == "real_model" and isinstance(n.value, ast.Attribute)
               and n.value.attr == "mark" for n in ast.walk(tree))


def test_every_module_marked_real_model_takes_a_heavy_lane():
    marked = {p.stem for p in (ROOT / "tests").glob("test_*.py")
              if marks_real_model(ast.parse(p.read_text(encoding="utf-8")))}
    assert len(marked) >= 8, f"the scan found too few real_model modules: {sorted(marked)}"
    assert not marked - testslots.HEAVY_MODULES, f"real_model modules without a heavy lane: {sorted(marked - testslots.HEAVY_MODULES)}"
