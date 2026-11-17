"""`located` finds an installed GGUF by its name with or without the .gguf suffix, as `core.resolve` documents."""
from pathlib import Path

from poolhouse.hub import local


def _root(tmp_path: Path) -> Path:
    (tmp_path / "m").mkdir()
    f = tmp_path / "m" / "tiny-model-Q4_K_M.gguf"
    f.write_bytes(b"GGUF" + b"\0" * 64)
    return tmp_path / "m"


def test_bare_name_matches_the_gguf_file(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr(local, "weight_paths", lambda roots=None: sorted(root.glob("*.gguf")))
    assert local.located("tiny-model-Q4_K_M").name == "tiny-model-Q4_K_M.gguf"
    assert local.located("tiny-model-q4_k_m.gguf").name == "tiny-model-Q4_K_M.gguf"
    assert local.located("tiny-model-Q4") is None  # a prefix is not a name unless loose
