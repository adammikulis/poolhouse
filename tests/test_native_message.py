"""Closed original DATA parsing; no exporter, model, authenticated source or admission."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import testunits
from native_cargo import native_node_units, native_targets_built  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from node_contract import adapters, native_message  # noqa: E402

NATIVE_TARGETS = (testunits.LIB,)
SOURCE = ROOT / "contracts/node/native_boundaries/native_message.toml"


def test_native_message_generated_parser_table_matches_authoritative_grammar():
    model = native_message.load(ROOT / "contracts/node")
    assert native_message.render(model) == (ROOT / "app/poolhouse-node/src/contract_native_message.rs").read_text()


def test_native_producer_fields_use_canonical_tags_order_and_exhaustive_source_fields():
    model = native_message.load(ROOT / "contracts/node")
    rendered = native_message.render_producer(model)
    assert rendered == (ROOT / "patches/codex/generated/native_original_fields.rs").read_text()
    # A new complete-source field must enter the source destructure and encoding,
    # causing Rust compile refusal until the actual source mapping is reviewed.
    record = next(row for row in model["types"] if row["name"] == "PreToolUse")
    record["fields"].append({"name": "future_private_field", "type": "String"})
    changed = native_message.render_producer(model)
    assert "future_private_field } = value" in changed
    assert "self.string(future_private_field)?;" in changed
    assert ".." not in rendered


@pytest.mark.parametrize("change", ["duplicate-type", "unknown-reference", "duplicate-tag", "incomplete-envelope", "zero-width-list", "authority-status"])
def test_native_message_grammar_refuses_ambiguous_or_unbounded_shapes(tmp_path, change):
    text = SOURCE.read_text()
    old, new = {
        "duplicate-type": ('name = "Subagent"', 'name = "String"'),
        "unknown-reference": ('type = "OptionalSubagent"', 'type = "Missing"'),
        "duplicate-tag": ('name = "PermissionRequest", tag = 2, type = "PermissionRequest"', 'name = "PermissionRequest", tag = 1, type = "PermissionRequest"'),
        "incomplete-envelope": ('"Version", "SourceFeature"', '"SourceFeature", "SourceFeature"'),
        "zero-width-list": ('name = "Strings"\nitem = "String"', 'name = "Strings"\nitem = "Unit"'),
        "authority-status": ('status = "untrusted-data-only"', 'status = "authenticated-native"'),
    }[change]
    assert old in text
    folder = tmp_path / "native_boundaries"
    folder.mkdir()
    (folder / "native_message.toml").write_text(text.replace(old, new, 1))
    with pytest.raises(ValueError):
        native_message.load(tmp_path)


def test_native_original_message_decoder(native_targets_built):  # noqa: F811
    output = native_targets_built.start_built(testunits.LIB, ["lifecycle::original_message::tests::"])
    assert "test result: ok. 8 passed; 0 failed;" in output, output


def test_source_message_cannot_declare_protected_effect_purpose():
    model = adapters.load(ROOT / "contracts/node")
    view = next(row for row in model["view"] if row["name"] == "NativeInvocationView")
    assert all(field["result"] != "OperationPurpose" for field in view["fields"])
    binding = next(row for row in model["projection"] if row["name"] == "BindingProjection")
    assert any(field["name"] == "purpose" and field["result"] == "OperationPurpose" for field in binding["getters"])
    view["fields"].append({"name": "purpose", "result": "OperationPurpose", "ownership": "borrow", "optional": False})
    with pytest.raises(ValueError):
        adapters.validate(model)
