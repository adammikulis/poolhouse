"""Canonical data shape drift and refusal boundaries."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from poolhouse.api import node_contract as public

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from generate_node_contract import outputs  # noqa: E402
from node_contract.load import load  # noqa: E402


def test_generated_artifacts_match_canonical_source():
    for path, expected in outputs(ROOT).items():
        assert path.read_text() == expected, str(path.relative_to(ROOT))


def test_public_ids_are_nominal_data_and_do_not_export_private_capabilities():
    assert public.DeviceId("ab" * 32) != public.CertificateDigest("ab" * 32)
    assert not hasattr(public, "SessionLease")
    assert not hasattr(public, "ReceiptId")
    assert not hasattr(public, "ProcessObservation")
    assert not hasattr(public, "AdmittedProducer")


@pytest.mark.parametrize("kind,invalid", [
    (public.U32, "4294967296"), (public.U64, "18446744073709551616"),
    (public.U64, "01"), (public.U64, "-0"), (public.U64, 1),
    (public.U64, True), (public.U64, "\uff11"), (public.U64, "1e2"),
    (public.DeviceId, "AB" * 32), (public.DeviceId, "ab" * 31),
    (public.InstallationId, "not-an-installation"),
])
def test_public_scalar_refusals(kind, invalid):
    with pytest.raises(ValueError):
        kind(invalid)


def identity():
    return {"device_id": "ab" * 32, "certificate_digest": "cd" * 32,
            "installation_id": "00000000-0000-4000-8000-000000000001",
            "public_key": "ef" * 32, "contract_version": "1"}


def test_public_record_roundtrip_preserves_untrusted_claims_without_authenticating_them():
    claim = identity()
    decoded = public.NodeIdentity.from_wire(claim)
    assert decoded.to_wire() == claim
    assert decoded.device_id == public.DeviceId(claim["device_id"])
    assert public.U64("18446744073709551615").value == "18446744073709551615"


@pytest.mark.parametrize("change", ["unknown", "missing", "numeric"])
def test_public_record_refuses_unknown_missing_and_wrongly_encoded_fields(change):
    claim = identity()
    if change == "unknown":
        claim["approved"] = True
    elif change == "missing":
        del claim["installation_id"]
    else:
        claim["contract_version"] = 1
    with pytest.raises(ValueError):
        public.NodeIdentity.from_wire(claim)


@pytest.mark.parametrize("change", ["unknown", "duplicate", "dangling", "private", "authority", "union", "version", "member_collision", "variant_collision"])
def test_source_refuses_ambiguous_and_authority_bearing_definitions(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    path = source / "types.toml"
    text = path.read_text()
    if change == "unknown":
        text = text.replace('name = "DeviceId"', 'name = "DeviceId"\ntrusted = true')
    elif change == "duplicate":
        text += '\n[[type]]\nname = "DeviceId"\nkind = "scalar"\nvisibility = "public"\nencoding = "hex32"\nissuer = "node"\n'
    elif change == "dangling":
        text = text.replace('type = "Ed25519PublicKey"', 'type = "Absent"')
    elif change == "private":
        text = text.replace('type = "Ed25519PublicKey"', 'type = "SessionLease"')
    elif change == "authority":
        text = text.replace('name = "NodeIdentity"', 'name = "AdmittedProducer"')
    elif change == "union":
        text = text.replace('tag = "kind"', 'tag = "value"')
    elif change == "member_collision":
        text = text.replace('name = "contract_version"', 'name = "to_wire"')
    elif change == "variant_collision":
        text = text.replace('{tag = "none", type = "NoParent"}, {tag = "thread", type = "ParentThread"}',
                            '{tag = "a_b", type = "NoParent"}, {tag = "a__b", type = "ParentThread"}')
    else:
        meta = source / "contract.toml"
        meta.write_text(meta.read_text().replace("version = 1", "version = 2"))
    path.write_text(text)
    with pytest.raises(ValueError):
        load(source)


@pytest.mark.parametrize("change", ["terminal", "duplicate", "atomic", "hold", "borrow", "handle", "error", "shortcut", "initial", "terminals", "checks", "spawn_plan"])
def test_lifecycle_and_adapter_refusals(tmp_path, change):
    source = tmp_path / "node"
    shutil.copytree(ROOT / "contracts/node", source)
    lifecycle = source / "lifecycle.toml"
    adapter = source / "adapters.toml"
    if change == "terminal":
        lifecycle.write_text(lifecycle.read_text().replace('from = "released", event = "consume"', 'from = "consumed", event = "consume"'))
    elif change == "duplicate":
        lifecycle.write_text(lifecycle.read_text().replace('from = "bound", event = "release", to = "released"', 'from = "reserved", event = "bind", to = "released"'))
    elif change == "atomic":
        lifecycle.write_text(lifecycle.read_text().replace('"atomic-single-consumer"', '"unchecked-consumer"'))
    elif change == "hold":
        lifecycle.write_text(lifecycle.read_text().replace('"single-use-hold"', '"reusable-hold"'))
    elif change == "borrow":
        adapter.write_text(adapter.read_text().replace('type = "HeldProcess", ownership = "move"', 'type = "HeldProcess", ownership = "borrow"'))
    elif change == "handle":
        adapter.write_text(adapter.read_text().replace('"KernelCaller"', '"ProcessObservation"'))
    elif change == "shortcut":
        lifecycle.write_text(lifecycle.read_text().replace('from = "released", event = "consume"', 'from = "reserved", event = "consume"'))
    elif change == "initial":
        lifecycle.write_text(lifecycle.read_text().replace('initial = "reserved"', 'initial = "bound"'))
    elif change == "terminals":
        lifecycle.write_text(lifecycle.read_text().replace('terminal = ["consumed", "cancelled", "invalidated"]', 'terminal = ["consumed", "cancelled"]'))
    elif change == "checks":
        lifecycle.write_text(lifecycle.read_text().replace('"current-generations", ', ''))
    elif change == "spawn_plan":
        adapter.write_text(adapter.read_text().replace(', {name = "plan", type = "SpawnPlan", ownership = "borrow"}', ''))
    elif change == "error":
        adapter.write_text(adapter.read_text().replace('"Io"', '"Self"'))
    with pytest.raises(ValueError):
        load(source)
