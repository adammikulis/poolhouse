"""A word docs/glossary.md bans, in a place the allowlist does not name."""

from __future__ import annotations

import importlib.util
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

from . import Finding
from ._util import EVERYTHING

NAME = "glossary-words"
INPUTS = (EVERYTHING,)
OWNER = "docs/glossary.md"
HARD = True
SCRIPT = Path(__file__).resolve().parents[1] / "nomenclature.py"


def describe() -> str:
    return ("A word docs/glossary.md bans (pair, cluster, journal, mesh, seat, peer, a host or project board, "
            "a revoked device ...) outside `contracts/glossary-allowlist.toml`. Say the glossary's word; "
            "`scripts/nomenclature.py --dry-run` lists the rewrite.")


@cache
def _nomenclature() -> ModuleType:
    spec = importlib.util.spec_from_file_location("nomenclature", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("nomenclature", module)
    spec.loader.exec_module(module)
    return module


def find(root: Path) -> list[Finding]:
    """Every banned word left in a tracked text file of ``root`` that no allowlist entry covers."""
    lib = _nomenclature()
    vocab = lib.Vocabulary(root=root)
    out = []
    for where in lib.tracked(root):
        text = None if vocab.excluded(where) else lib.text_of(where, root)
        if text is not None:
            out += [Finding(where, line, f"{word} ({family})") for line, word, family in vocab.findings(text, where)]
    return out
