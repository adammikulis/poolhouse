"""Descriptor DATA, canonical IDs and sealed interface refusals; no native admission."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from node_contract import authority_admission  # noqa: E402
from node_contract.load import load  # noqa: E402
from node_contract.transcripts import ReferenceTranscripts  # noqa: E402

SOURCE = ROOT / "contracts/node"
FIXTURES = json.loads((SOURCE / "native_boundaries/transcript-vectors.json").read_text())["vectors"]
DESCRIPTOR = next(row for row in FIXTURES if row["purpose"] == "authority-descriptor-v1")


def raw(value):
    return json.dumps(value).encode()


def test_old_operation_transcripts_are_byte_for_byte_retained():
    import hashlib
    # Pinned from published 801b3423, independent of the new descriptor profile.
    encoded = json.dumps(FIXTURES[:3], sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "e41a1e37e9202870503eefb3b05287ec65887b95a20804abfb5724e1c147fef3"


def test_descriptor_profile_is_root_issuer_only_and_private_data():
    meta, rows, _ = load(SOURCE)
    model = meta["authority_admission"]
    assert model["issuer_profiles"] == ["authority-descriptor-v1"]
    assert set(model["subject_profiles"]).isdisjoint(model["issuer_profiles"])
    types = {row["name"]: row for row in rows}
    for name in ("AuthorityRootDescription", "AuthorityDescriptorBody", "AuthoritySignedDescriptor"):
        assert types[name]["visibility"] == "private"
    scope = types["AuthorityDescriptorScope"]
    assert {variant["tag"] for variant in scope["variants"]} == {"genesis", "governed", "installation_credential"}
    assert types["AuthorityDescriptorBody"]["fields"][-1]["name"] == "revocation_generation"


@pytest.mark.parametrize("field", ["strength", "assurance", "counts", "current_membership", "future_charter_digest",
                                   "self_admitting_act", "issuer_signature", "label", "role_eligibility"])
def test_claimed_assurance_membership_future_hashes_and_self_signature_cannot_enter_body(field):
    value = copy.deepcopy(DESCRIPTOR["value"])
    value[field] = "self-authorized"
    codec = ReferenceTranscripts(SOURCE)
    with pytest.raises(ValueError):
        codec.digest_bytes("authority-descriptor-v1", raw(value))


@pytest.mark.parametrize("change", ["signer", "algorithm", "issuer_profile", "scope_root", "scope_generation", "expiry", "policy"])
def test_descriptor_consistency_refusals(change):
    value = copy.deepcopy(DESCRIPTOR["value"])
    if change == "signer":
        value["signer_id"] = "ff" * 32
    elif change == "algorithm":
        value["public_key"]["kind"] = "p256"
    elif change == "issuer_profile":
        value["operation_profiles"] = [{"kind": "authority_descriptor_v1", "value": {}}]
    elif change == "scope_root":
        value["scope"]["value"]["context"]["authority_root"] = "ff" * 32
    elif change == "scope_generation":
        value["scope"]["value"]["context"]["authority_generation"] = "00000000-0000-4000-8000-000000000002"
    elif change == "expiry":
        value["expires_at"] = value["issued_at"]
    else:
        value["signature_policy"] = {"kind": "p256_sha256_low_s_der", "value": {}}
    with pytest.raises(ValueError):
        ReferenceTranscripts(SOURCE).digest_bytes("authority-descriptor-v1", raw(value))


def test_current_governance_is_separate_and_historical_issuance_does_not_gain_votes():
    meta, _, _ = load(SOURCE)
    roles = {role["name"]: role for role in meta["authority_admission"]["role"]}
    assert {field["type"] for field in roles["CurrentGovernance"]["getters"]} >= {"ApprovalDomainContext", "AuthorityGovernanceEligibility"}
    assert {field["type"] for field in roles["IdentityComparison"]["getters"]} == {"AuthorityPersonRelation"}
    assert {field["name"] for field in roles["IdentityComparison"]["retains"]} == {"left", "right"}
    assert {field["type"] for field in roles["IdentityComparison"]["retains"]} == {"AdmittedDescriptor"}
    # An ID/profile-consistent descriptor is still only DATA, not this opaque role.
    source = authority_admission.render(meta["authority_admission"])
    assert "impl From<AuthorityDescriptorBody>" not in source
    assert "impl From<AuthorityRootDescription>" not in source
    assert "fn new(" not in source
    assert "pub(crate) trait AuthorityAdmission" in source
    assert "NativeInstallationGraph" in source and "NativeGraph" not in source
    assert "pub(crate) fn origin(&self)" in source


@pytest.mark.parametrize("field", ["subject_profiles", "issuer_profiles", "serialization", "requirements", "role", "method"])
def test_boundary_rejects_profile_escalation_or_missing_custody_contract(tmp_path, field):
    import shutil
    root = tmp_path / "node"
    shutil.copytree(SOURCE, root)
    path = root / "native_boundaries/authority_admission.toml"
    source = path.read_text()
    if field == "subject_profiles":
        source = source.replace('subject_profiles = ["approval-immutable-v1", "local-authority-scoped-v1"]',
                                'subject_profiles = ["authority-descriptor-v1"]')
    elif field == "issuer_profiles":
        source = source.replace('issuer_profiles = ["authority-descriptor-v1"]', 'issuer_profiles = []')
    elif field == "serialization":
        source = source.replace('serialization = "forbidden"', 'serialization = "json"')
    elif field == "requirements":
        source = source.replace('"first-root-is-software-led-protected-native-setup-not-tofu-or-terminal", ', '')
    elif field == "role":
        source = source.replace('retains = [{name = "origin", type = "RecoveryOrigin"}]', 'retains = []')
    else:
        source = source.replace('name = "admit_existing_root"', 'name = "admit_self_signed_root"')
    path.write_text(source)
    with pytest.raises(ValueError):
        load(root)


def test_exact_root_and_identity_custody_is_physically_retained_without_public_clone():
    meta, _, _ = load(SOURCE)
    model = meta["authority_admission"]
    source = authority_admission.render(model)
    assert "custody: Arc<AdmittedRootCustody<'owner, G>>" in source
    assert "custody: Arc<AdmittedDescriptorCustody<'owner, G>>" in source
    assert "root: AdmittedRoot<'owner, G>" in source
    assert "left: AdmittedDescriptor<'owner, G>" in source
    assert "right: AdmittedDescriptor<'owner, G>" in source
    assert "fn retain_private(&self)" in source
    assert "impl Clone" not in source and "derive(Clone" not in source
    methods = {method["name"]: method for method in model["method"]}
    assert "root" not in {argument["name"] for argument in methods["verify_descriptor"]["arguments"]}
    assert methods["retire"]["arguments"] == [{"name": "provider", "type": "ProviderObservation", "ownership": "move"}]


def test_actual_generated_seals_refuse_data_and_missing_original_custody(tmp_path):
    import os
    import subprocess

    import testnode

    meta, rows, _ = load(SOURCE)
    model = meta["authority_admission"]
    # Nominal DATA-only stand-ins isolate the actual generated interface's type
    # barriers. They issue no native observation, graph, key or admitted handle.
    data = sorted({field["type"] for role in model["role"] for field in role["getters"]
                   if field["type"] in {row["name"] for row in rows}}
                  | {"AuthorityDescriptorBody", "AuthoritySignedDescriptor", "ApprovalImmutableOperation", "ApprovalSignatureMaterial"})
    prefix = ("#![allow(dead_code, unused_imports)]\nmod contract {\n"
              + "\n".join("pub struct " + name + ";" for name in data) + "\n}\n"
              + "mod contract_lifecycle { pub trait NativeInstallationGraph { type Installation; type Process; type MeasuredAsset; } }\n"
              + "mod admission {\n" + authority_admission.render(model) + "\n}\n")
    environment = dict(os.environ, HOME=str(tmp_path / "home"), RUSTUP_HOME=str(testnode.account() / ".rustup"),
                       CARGO_HOME=str(testnode.account() / ".cargo"))
    (tmp_path / "home").mkdir()
    source = tmp_path / "probe.rs"

    head = prefix[:prefix.index("mod admission {\n")]
    declaration = authority_admission.render(model)

    def compile(extra, inside="", binary=False):
        source.write_text(head + "mod admission {\n" + declaration + inside + "\n}\n" + extra)
        return subprocess.run([testnode.tool("rustc"), "--edition=2021", "--crate-type=" + ("bin" if binary else "lib"),
                               "--emit=" + ("link" if binary else "metadata"),
                               "--out-dir", str(tmp_path), str(source)], env=environment,
                              capture_output=True, text=True, check=False)

    positive = compile("")
    assert positive.returncode == 0, positive.stderr
    probes = [
        "fn forged<G:admission::AuthorityAdmissionGraph>(data:contract::AuthorityDescriptorBody) -> admission::AdmittedDescriptor<'static,G> { data.into() }",
        "fn root<G:admission::AuthorityAdmissionGraph>(data:contract::AuthorityRootDescription) -> admission::AdmittedRoot<'static,G> { data.into() }",
        "fn vote<G:admission::AuthorityAdmissionGraph>(data:contract::AuthorityPersonRelation) -> admission::IdentityComparison<'static,G> { data.into() }",
        "struct Fake; impl admission::seal::Platform for Fake {}",
        "fn clone_root<'a,G:admission::AuthorityAdmissionGraph>(root:&admission::AdmittedRoot<'a,G>) -> admission::AdmittedRoot<'a,G> { root.clone() }",
    ]
    for probe in probes:
        result = compile(probe)
        assert result.returncode != 0, probe
        assert any(marker in result.stderr for marker in ("private", "trait bound", "mismatched types", "missing fields")), result.stderr
    # Native adapters inside the sealed module still cannot omit originals.
    for probe in (
        "fn missing_root<'a,G:AuthorityAdmissionGraph>(inner:G::ProviderObservation, scope:Arc<AuthorityScope<'a,G>>, policy:NativeKeyPolicy<'a,G>) -> ProviderObservation<'a,G> { ProviderObservation {inner,scope,policy} }",
        "fn missing_identity<'a,G:AuthorityAdmissionGraph>(inner:G::IdentityComparison, scope:Arc<AuthorityScope<'a,G>>) -> IdentityComparison<'a,G> { IdentityComparison {inner,scope} }",
    ):
        result = compile("", inside=probe)
        assert result.returncode != 0 and "missing field" in result.stderr, result.stderr

    role_names = {role["name"] for role in model["role"]}
    view_names = {view["name"] for view in model["view"]}
    fixture = ["struct TestNative;", "impl NativeInstallationGraph for TestNative { type Installation=(); type Process=(); type MeasuredAsset=(); }",
               "struct TestGraph; struct Dummy { keep: Option<Arc<()>> }", "impl seal::Platform for TestGraph {}",
               "impl AuthorityAdmissionGraph for TestGraph { type Native=TestNative;"]
    fixture += ["type " + name + "=Dummy;" for name in sorted(role_names)]
    fixture += ["}"]
    for role in model["role"]:
        name = role["name"]
        retained = {field["name"] for field in role["retains"]}
        fixture += ["impl seal::" + name + " for Dummy {}", "impl " + name + "Projection<TestGraph> for Dummy {"]
        for field in role["getters"]:
            target = field["type"]
            if target in view_names or field["name"] in retained:
                continue
            if target.startswith("native."):
                target = "<TestNative as NativeInstallationGraph>::" + target.split(".")[1]
            elif target in role_names:
                target = "<TestGraph as AuthorityAdmissionGraph>::" + target
            fixture.append("fn " + field["name"] + "(&self)->&" + target + " { panic!(\"not an admission fixture\") }")
        fixture += ["}"]
    fixture += ["""
    pub(crate) fn memory_retention_probe() {
        // Structural lifetime test only. No native observation/admission API is implemented.
        let scope=Arc::new(AuthorityScope::<TestGraph>{installation:(),invariant:PhantomData});
        let resource=Arc::new(()); let weak=Arc::downgrade(&resource);
        let observed=RootObservation{inner:Dummy{keep:Some(resource)},scope:Arc::clone(&scope)};
        let root=AdmittedRoot{custody:Arc::new(AdmittedRootCustody{inner:Dummy{keep:None},scope:Arc::clone(&scope),origin:RootAdmissionOrigin::RootObservation(observed)})};
        let key=ProviderCustody{inner:Dummy{keep:None},scope:Arc::clone(&scope)};
        let policy=NativeKeyPolicy{inner:Dummy{keep:None},scope:Arc::clone(&scope),custody:key};
        let provider=ProviderObservation{inner:Dummy{keep:None},scope:Arc::clone(&scope),policy,root:root.retain_private()};
        let descriptor=AdmittedDescriptor{custody:Arc::new(AdmittedDescriptorCustody{inner:Dummy{keep:None},scope:Arc::clone(&scope),provider})};
        let comparison=IdentityComparison{inner:Dummy{keep:None},scope:Arc::clone(&scope),left:descriptor.retain_private(),right:descriptor.retain_private()};
        drop(root);drop(descriptor);assert!(weak.upgrade().is_some());
        let IdentityComparison{left,right,..}=comparison;
        drop(left);assert!(weak.upgrade().is_some());
        drop(right);assert!(weak.upgrade().is_none());
    }
    """]
    result = compile("fn main(){admission::memory_retention_probe();}", inside="\n".join(fixture), binary=True)
    assert result.returncode == 0, result.stderr
    executable = tmp_path / ("probe.exe" if os.name == "nt" else "probe")
    ran = subprocess.run([str(executable)], env=environment, capture_output=True, text=True, check=False)
    assert ran.returncode == 0, ran.stderr
