"""Private untrusted observations; no producer transport, admission or execution."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import testunits
from native_cargo import native_node_units, native_targets_built  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from node_contract import native_selection  # noqa: E402

NATIVE_TARGETS = (testunits.LIB,)
SOURCE = ROOT / "contracts/node/native_boundaries/native_selection.toml"


def test_selection_grammar_generates_only_private_unmeasured_declarations():
    model = native_selection.load(ROOT / "contracts/node")
    assert native_selection.render(model) == (ROOT / "app/poolhouse-node/src/contract_native_selection.rs").read_text()
    declarations = next(row for row in model["types"] if row["name"] == "Declaration")
    assert [case["name"] for case in declarations["cases"]] == ["UnmeasuredCommand", "UnmeasuredMcp", "UnmeasuredNotify", "OpaqueCallback"]


@pytest.mark.parametrize("change", ["measured", "environment", "authority", "reference", "zero-list", "version", "order"])
def test_selection_grammar_refuses_unsupported_authority_or_shapes(tmp_path, change):
    text = SOURCE.read_text()
    old, new = {
        "measured": ('name = "UnmeasuredCommand", tag = 1', 'name = "MeasuredCommand", tag = 5'),
        "environment": ('name = "Unavailable", tag = 1', 'name = "RawEnvironment", tag = 2'),
        "authority": ('name = "configured_command", type = "String"', 'name = "purpose", type = "String"'),
        "reference": ('type = "OptionalPluginTrust"', 'type = "Missing"'),
        "zero-list": ('name = "Strings"\nitem = "String"', 'name = "Strings"\nitem = "Unit"'),
        "version": ('version = 1', 'version = true'),
        "order": ('"Ordinal", "SourceIndex"', '"SourceIndex", "Ordinal"'),
    }[change]
    assert old in text
    directory = tmp_path / "native_boundaries"
    directory.mkdir()
    (directory / "native_selection.toml").write_text(text.replace(old, new, 1))
    (directory / "native_message.toml").write_bytes((ROOT / "contracts/node/native_boundaries/native_message.toml").read_bytes())
    with pytest.raises(ValueError):
        native_selection.load(tmp_path)


def test_native_selection_observation_decoder(native_targets_built):  # noqa: F811
    result = native_targets_built.start_built(testunits.LIB, ["lifecycle::selection_observation::tests::"])
    assert "test result: ok. 8 passed; 0 failed;" in result, result
