"""Private native declarations are typed projections, never proof issuers."""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest
import testnode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from node_contract import adapters  # noqa: E402


def test_native_projection_graph_is_sealed_and_nominal():
    boundary = adapters.load(ROOT / "contracts/node")
    assert boundary["version"] == 3, "v2 opaque associated handles do not express the reviewed graph"
    source = adapters.render(boundary)
    assert "trait NativeGraph:" in source
    assert "struct HeldHandle<G: NativeGraph>" in source
    assert "struct RunningHandle<G: NativeGraph>" in source
    assert "trait NodeEffectOwner<G: NativeGraph, E: EffectGraph<G>>" in source
    assert "type HeldProcess;" not in source
    assert "type RunningProcess;" not in source
    assert "impl NativeCallerPlatform" not in source


def named(rows, name):
    return next(row for row in rows if row["name"] == name)


@pytest.mark.parametrize("attack", [
    "missing_binding", "foreign_graph", "phase_alias", "owned_getter", "mutable_getter",
    "lost_failure_custody", "caller_observation", "raw_node_key", "cleanup_bool",
    "borrowed_permit", "cleanup_as_completion", "serde", "constructor", "rust_text",
    "invented_budget", "missing_profile", "bool_coercion", "lost_source_fence",
])
def test_native_projection_schema_refuses_authority_and_custody_substitution(attack):
    value = copy.deepcopy(adapters.load(ROOT / "contracts/node"))
    if attack == "missing_binding":
        value["projection"].remove(named(value["projection"], "BindingProjection"))
    elif attack == "foreign_graph":
        named(value["graph"]["associated"], "Plan")["projection"] = "ForeignPlanProjection"
    elif attack == "phase_alias":
        value["phase"][1]["name"] = "HeldHandle"
    elif attack in {"owned_getter", "mutable_getter"}:
        named(value["projection"], "ProducerProjection")["getters"][0]["ownership"] = "move" if attack == "owned_getter" else "borrow-mut"
    elif attack == "lost_failure_custody":
        row = named(value["projection"], "ReleaseFailureProjection")
        row["getters"].remove(named(row["getters"], "custody"))
    elif attack == "caller_observation":
        named(named(value["projection"], "CallerProjection")["getters"], "binding")["result"] = "ProcessObservation"
    elif attack == "raw_node_key":
        named(value["view"], "NativeBirthView")["fields"].append({"name": "session_key", "result": "NodeSessionKey", "ownership": "borrow", "optional": False})
    elif attack == "cleanup_bool":
        named(value["projection"], "CleanupProjection")["getters"].append({"name": "clean", "result": "bool", "ownership": "value", "optional": False})
    elif attack == "borrowed_permit":
        value["effect_owner"]["method"]["arguments"][0]["ownership"] = "borrow"
    elif attack == "cleanup_as_completion":
        value["effect_owner"]["method"]["result"] = "Cleanup"
    elif attack == "serde":
        value["representation"][0]["serde"] = True
    elif attack == "constructor":
        value["phase"][0]["constructor"] = "public"
    elif attack == "rust_text":
        named(value["projection"], "ProducerProjection")["getters"][0]["result"] = "Installation; pub fn forge() {}"
    elif attack == "invented_budget":
        value["profile"][0]["max_bytes"] = 256
    elif attack == "missing_profile":
        value["profile"].pop()
    elif attack == "bool_coercion":
        named(value["projection"], "ProducerProjection")["getters"][0]["optional"] = 0
    else:
        value["requirements"].remove("missing-or-rolled-back-occurrence-ledger-refuses-prior-source")
    with pytest.raises(ValueError):
        adapters.validate(value)
    with pytest.raises(ValueError):
        adapters.render(value)


def compile_projection(tmp_path, probe):
    """Type-check the actual generated interfaces with diagnostic data leaf fixtures only.

    No fixture implements a seal, creates a proof, invokes an OS adapter or runs a model.
    The coordinator also compiles the integrated actual contract crate after generation.
    """
    compiler = testnode.tool("rustc")
    # Same build-tool scoping as testapp: HOME stays isolated; only installed Rust tools are shared.
    account = testnode.account()
    environment = {**os.environ, "RUSTUP_HOME": str(account / ".rustup"), "CARGO_HOME": str(account / ".cargo")}
    generated = adapters.render(adapters.load(ROOT / "contracts/node"))
    source = '''
mod contract {
    #[derive(Debug)] pub struct InstallationId;
    #[derive(Debug)] pub struct ResourceGeneration;
    #[derive(Debug)] pub struct WindowsSid;
    #[derive(Debug)] pub struct AuditToken;
    pub struct ProcessObservation;
}
mod declarations {
    use super::contract::ProcessObservation;
    #[derive(Debug)] pub enum NativeAdapterError { Io }
''' + generated + "\n}\nuse declarations::*;\n" + probe
    path = tmp_path / "projection.rs"
    path.write_text(source)
    return subprocess.run([compiler, "--edition=2021", "--crate-type=lib", "--emit=metadata",
                           "--out-dir", str(tmp_path), str(path)], cwd=tmp_path,
                          capture_output=True, text=True, check=False, env=environment)


def test_native_projection_signatures_compile_without_a_proof_issuer(tmp_path):
    result = compile_projection(tmp_path, '''
fn held<G: NativeGraph, P: NativeCallerPlatform<G>>(platform: &mut P,
    producer: &G::Producer, bootstrap: &G::Bootstrap, binding: &G::Binding, plan: &G::Plan)
    -> Result<HeldHandle<G>, G::SpawnFailure> {
    platform.hold_spawn(producer, bootstrap, binding, plan)
}
fn release<G: NativeGraph, P: NativeCallerPlatform<G>>(platform: &mut P,
    held: HeldHandle<G>, binding: &G::Binding) -> Result<RunningHandle<G>, G::ReleaseFailure> {
    platform.release(held, binding)
}
fn effect<G: NativeGraph, E: EffectGraph<G>, O: NodeEffectOwner<G, E>>(owner: &mut O,
    permit: E::Permit, operation: E::Operation) -> Result<E::Completion, E::Unknown> {
    owner.execute(permit, operation)
}
''')
    assert result.returncode == 0, result.stderr[:6000]


@pytest.mark.parametrize("probe,diagnostic", [
    ("fn wrong<G: NativeGraph>(held: HeldHandle<G>) -> RunningHandle<G> { held }", "E0308"),
    ("fn forge<G: NativeGraph>(inner: G::ChildCustody) -> HeldHandle<G> { HeldHandle { inner } }", "E0451"),
    ("struct Untrusted; impl declarations::native_projection::native_seal::Graph for Untrusted {}", "E0603"),
    ("fn wrong<'a, G: NativeGraph, H: NativeGraph>(plan: &'a G::Plan) -> &'a H::Binding { plan.binding() }", "E0308"),
    ("fn reuse<G: NativeGraph, E: EffectGraph<G>, O: NodeEffectOwner<G,E>>(o: &mut O, p: E::Permit, a: E::Operation, b: E::Operation) { let _ = o.execute(p,a); let _ = o.execute(p,b); }", "E0382"),
    ("fn copy<G: NativeGraph>(custody: UnresolvedCustody<G>) { let _ = custody.clone(); }", "E0599"),
    ("fn wrong<G: NativeGraph, E: EffectGraph<G>, F: EffectGraph<G>>(completion: E::Completion) -> F::Completion { completion }", "E0308"),
    ("fn forge<G: NativeGraph>(data: contract::ProcessObservation) -> G::Producer { data }", "E0308"),
    ("fn wrong(message: NativeBirthView<'_>) { message.session_key(); }", "E0599"),
    ("fn escape<G: NativeGraph>(message: &G::Message) -> NativeMessageView<'static> { message.view() }", "lifetime may not live long enough"),
])
def test_native_projection_rust_refuses_wrong_graph_phase_or_custody(tmp_path, probe, diagnostic):
    result = compile_projection(tmp_path, probe)
    assert result.returncode != 0, probe
    assert diagnostic in result.stderr, result.stderr[:6000]
