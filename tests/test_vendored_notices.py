"""Vendored JavaScript is listed in THIRD_PARTY_NOTICES.md with its project, version and licence, and --check
fails on a vendored file that has no manifest entry or whose bytes changed."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def notices():
    spec = importlib.util.spec_from_file_location("scripts_notices_vendored", ROOT / "scripts" / "notices.py")
    made = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = made
    spec.loader.exec_module(made)
    return made


def room_tree(tmp_path: Path, *, listed: bool = True, text: bytes = b"export const a = 1;\n") -> Path:
    vendor = tmp_path / "examples" / "demo" / "vendor"
    (vendor / "loaders").mkdir(parents=True)
    (vendor / "loaders" / "Thing.js").write_bytes(text)
    (vendor / "pkg.LICENSE").write_text("The MIT License\n", encoding="utf-8")
    entry = {"path": "loaders/Thing.js", "source": "examples/jsm/loaders/Thing.js",
             "sha256": hashlib.sha256(b"export const a = 1;\n").hexdigest()}
    manifest = {"project": "pkg.js", "package": "pkg", "version": "1.2.3", "licence": "MIT",
                "licence_file": "pkg.LICENSE", "files": [entry] if listed else []}
    (vendor / "vendor.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_every_vendored_file_in_the_repository_is_listed_and_unchanged():
    assert notices().vendored_problems(ROOT) == []


def test_the_notices_file_lists_each_vendored_room_file_with_version_and_licence():
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for name in ("GLTFLoader.js", "BufferGeometryUtils.js", "SkeletonUtils.js"):
        row = next(line for line in text.splitlines() if name in line and line.startswith("|"))
        assert "three.js" in row and "0.186.1" in row and "MIT" in row


def test_a_vendored_file_with_no_manifest_entry_is_flagged(tmp_path):
    found = notices().vendored_problems(room_tree(tmp_path, listed=False))
    assert any("Thing.js" in line and "not listed" in line for line in found)


def test_a_vendored_file_whose_bytes_changed_is_flagged(tmp_path):
    found = notices().vendored_problems(room_tree(tmp_path, text=b"export const a = 2;\n"))
    assert any("Thing.js" in line and "sha256" in line for line in found)


def test_a_clean_tree_has_no_problems_and_one_row_per_file(tmp_path):
    root = room_tree(tmp_path)
    assert notices().vendored_problems(root) == []
    assert [r[:4] for r in notices().vendored_rows(root)][-1] == ("examples/demo/vendor/loaders/Thing.js", "pkg.js", "1.2.3", "MIT")
