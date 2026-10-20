"""docs/glossary.md is binding: a word it bans does not appear where a person or an agent reads it.

The scan is `scripts/nomenclature.py`'s own, so the check and the rewrite cannot disagree about what a banned
word is. The places a banned word may stay are in `contracts/glossary-allowlist.toml`, each with its reason.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY = ROOT / "docs" / "glossary.md"


def _load():
    spec = importlib.util.spec_from_file_location("nomenclature", ROOT / "scripts" / "nomenclature.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["nomenclature"] = module
    spec.loader.exec_module(module)
    return module


nomenclature = _load()
VOCAB = nomenclature.Vocabulary()
TRACKED = nomenclature.tracked()


def test_no_banned_word_is_left_outside_the_allowlist():
    found = []
    for path in TRACKED:
        text = nomenclature.text_of(path)
        if text is None or VOCAB.excluded(path):
            continue
        found += [f"{path}:{n}: {word} ({family})" for n, word, family in VOCAB.findings(text, path)]
    assert not found, f"{len(found)} banned words (docs/glossary.md); first: " + "; ".join(found[:12])


def test_the_rewrite_has_nothing_left_to_change():
    changes, moves = nomenclature.plan(VOCAB)
    assert (sorted(changes)[:5], sorted(moves)[:5]) == ([], [])


def test_every_allowlist_entry_says_why_and_still_matches_something():
    allow = nomenclature.load(nomenclature.ALLOWLIST)
    for kind in ("exclude", "allow", "token"):
        for entry in allow.get(kind, []):
            assert entry.get("reason", "").strip(), entry
    for entry in allow.get("exclude", []) + allow.get("allow", []):
        patterns = [entry["path"]] if isinstance(entry["path"], str) else entry["path"]
        for pattern in patterns:
            assert any(fnmatch.fnmatch(p, pattern) for p in TRACKED), f"{pattern} matches no tracked file"
    families = set(VOCAB.families.values()) | {g[1] for g in VOCAB.gates}
    for entry in allow.get("allow", []):
        assert set(entry["words"]) <= families, entry
    words = {w for p in TRACKED if (t := nomenclature.text_of(p)) for w in set(nomenclature.WORD.findall(t))}
    assert {t["token"] for t in allow.get("token", [])} <= words


def test_an_allowed_word_is_only_allowed_where_the_allowlist_says():
    assert VOCAB.findings("a PairingServer", "src/poolhouse/fleet/x.py")
    assert not VOCAB.findings("a pair of values", "src/poolhouse/graph/x.py")
    assert VOCAB.findings("a pair of devices", "src/poolhouse/fleet/x.py")


@pytest.mark.parametrize("text, family", [
    ("PairingServer", "pair"), ("paired_fingerprints", "pair"), ("an unpaired device", "pair"),
    ("cluster_key_path", "cluster"), ("the journal of a board", "journal"), ("the mesh syncs", "mesh"),
    ("a seat in the harness", "seat"), ("peer.rs", "peer"), ("the host board", "board-kind"),
    ("a remote board", "board-kind"), ("the project board", "board-kind"), ("the Dev cluster", "home-pool"),
    ("revoke the device", "revoke"), ("a revoked device", "revoke"),
    ("the activation owner", "update"), ("Activating a fix", "update"), ("the landed, activated result", "update"),
    ("changing or activating a runtime", "update"),
])
def test_the_scan_finds_each_banned_word_in_the_shapes_it_takes(text, family):
    assert family in {f for _, _, f in VOCAB.findings(text, "docs/x.md")}


@pytest.mark.parametrize("text", [
    "repair the file", "a socketpair", "pairwise distances", "a seatbelt profile", "revoke the key",
    "the token is revoked", "a keypair", "the node daemon", "clustering the entries",
    "activate the venv", "activations cost memory per token", "the LiteRT activation type",
])
def test_the_scan_leaves_ordinary_words_alone(text):
    assert not VOCAB.findings(text, "docs/x.md")


def test_the_rewrite_maps_old_words_to_the_glossary_words():
    new, _ = VOCAB.rewrite("PairingClient pairs a paired device; cluster_key, journals, a seat", "src/poolhouse/x.py")
    assert new == "JoinCodeClient joins a member; pool_key, logs, a session"
    again, hits = VOCAB.rewrite(new, "src/poolhouse/x.py")
    assert again == new and sum(hits.values()) == 0


def test_the_glossary_names_every_banned_word_and_what_to_say_instead():
    text = GLOSSARY.read_text(encoding="utf-8").lower()
    for family in ("pair", "cluster", "journal", "mesh", "seat", "peer"):
        assert family in text, family
    for phrase in ("host board", "remote board", "project board", "home pool", "revoke", "node"):
        assert phrase in text, phrase
    for word in ("pool", "device", "member", "join", "add a device", "remove", "board", "entry", "lease", "capacity"):
        assert re.search(rf"\*\*{re.escape(word)}\*\*", text), word


def test_the_instruction_files_point_at_the_glossary():
    for name in ("AGENTS.md", "CLAUDE.md"):
        assert "docs/glossary.md" in (ROOT / name).read_text(encoding="utf-8"), name
