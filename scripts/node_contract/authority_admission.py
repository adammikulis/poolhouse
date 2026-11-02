"""Canonical authority admission interface; DATA and signature mathematics issue no handles."""
from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from .common import keys

REQUIREMENTS = ['root-data-self-signature-labels-and-hashes-never-admit-root',
 'first-root-is-software-led-protected-native-setup-not-tofu-or-terminal',
 'independent-root-configuration-and-native-observation-select-issuer-key-and-policy',
 'provider-algorithm-exportability-hardware-account-and-dialog-success-are-not-assurance',
 'actual-os-per-signature-key-policy-and-fresh-operation-authentication-are-independent',
 'unknown-provider-policy-remains-unavailable-with-original-key-custody',
 'unsupported-windows-algorithms-never-rekey-coerce-or-downgrade',
 'descriptor-id-excludes-only-own-id-and-issuer-signature-is-outside-body',
 'signer-id-binds-canonical-algorithm-and-exact-raw-key-bytes',
 'descriptor-context-is-historical-issuance-not-current-membership-or-head-proof',
 'no-future-charter-or-self-admitting-act-reference-in-descriptor-body',
 'unrelated-current-revision-does-not-require-new-descriptor',
 'current-root-generation-revocation-expiry-provider-lifetime-and-governance-revalidated-at-use',
 'role-eligibility-is-not-active-membership-quorum-or-person-approval',
 'cross-root-person-relation-unknown-adds-no-vote-and-same-aliases-deduplicate',
 'subject-profiles-exclude-descriptor-issuer-purpose',
 'no-data-to-handle-constructor-public-clone-serde-or-v1-fallback',
 'unknown-retirement-retains-root-provider-operation-and-cleanup-custody',
 'graph-needs-installation-not-unrelated-startup-ui-or-producer-graphs',
 'no-platform-observation-admission-constructor-key-or-os-implementation-in-this-boundary']

ROLES = [{'name': 'RootObservation',
  'getters': [{'name': 'description', 'type': 'AuthorityRootDescription'},
              {'name': 'process', 'type': 'native.Process'},
              {'name': 'code', 'type': 'native.MeasuredAsset'}],
  'retains': []},
 {'name': 'RootSetupPrepared', 'getters': [{'name': 'operation_id', 'type': 'OperationId'}], 'retains': []},
 {'name': 'RootSetupAuthentication',
  'getters': [{'name': 'prepared', 'type': 'RootSetupPrepared'},
              {'name': 'owner_process', 'type': 'native.Process'},
              {'name': 'helper_code', 'type': 'native.MeasuredAsset'}],
  'retains': [{'name': 'prepared', 'type': 'RootSetupPrepared'}]},
 {'name': 'AdmittedRoot',
  'getters': [{'name': 'description', 'type': 'AuthorityRootDescription'},
              {'name': 'observation', 'type': 'RootObservation'},
              {'name': 'origin', 'type': 'RootAdmissionOrigin'}],
  'retains': [{'name': 'origin', 'type': 'RootAdmissionOrigin'}]},
 {'name': 'ProviderCustody',
  'getters': [{'name': 'process', 'type': 'native.Process'},
              {'name': 'code', 'type': 'native.MeasuredAsset'}],
  'retains': []},
 {'name': 'ProviderObservation',
  'getters': [{'name': 'public_key', 'type': 'ApprovalSignerPublicKey'},
              {'name': 'incarnation', 'type': 'ResourceGeneration'},
              {'name': 'policy', 'type': 'NativeKeyPolicy'}],
  'retains': [{'name': 'policy', 'type': 'NativeKeyPolicy'},
              {'name': 'root', 'type': 'AdmittedRoot', 'ownership': 'shared'}]},
 {'name': 'AdmittedDescriptor',
  'getters': [{'name': 'provider', 'type': 'ProviderObservation'},
              {'name': 'description', 'type': 'AuthorityDescriptorBody'},
              {'name': 'assurance', 'type': 'ApprovalSignerAssurance'}],
  'retains': [{'name': 'provider', 'type': 'ProviderObservation'}]},
 {'name': 'CurrentGovernance',
  'getters': [{'name': 'context', 'type': 'ApprovalDomainContext'},
              {'name': 'eligibility', 'type': 'AuthorityGovernanceEligibility'}],
  'retains': [{'name': 'signature', 'type': 'BoundSignatureMath'}]},
 {'name': 'IdentityComparison',
  'getters': [{'name': 'relation', 'type': 'AuthorityPersonRelation'}],
  'retains': [{'name': 'left', 'type': 'AdmittedDescriptor', 'ownership': 'shared'},
              {'name': 'right', 'type': 'AdmittedDescriptor', 'ownership': 'shared'}]},
 {'name': 'BoundSignatureMath',
  'getters': [{'name': 'descriptor', 'type': 'AdmittedDescriptor'},
              {'name': 'operation', 'type': 'LocalAuthorityScopedOperation'}],
  'retains': [{'name': 'descriptor', 'type': 'AdmittedDescriptor'}]},
 {'name': 'RetirementCustody',
  'getters': [],
  'retains': [{'name': 'original', 'type': 'ProviderObservation'}]},
 {'name': 'Recovery',
  'getters': [{'name': 'operation_id', 'type': 'OperationId'}, {'name': 'origin', 'type': 'RecoveryOrigin'}],
  'retains': [{'name': 'origin', 'type': 'RecoveryOrigin'}]},
 {'name': 'NativeKeyPolicy',
  'getters': [{'name': 'custody', 'type': 'ProviderCustody'},
              {'name': 'enforced_policy', 'type': 'AuthorityRequiredKeyPolicy'}],
  'retains': [{'name': 'custody', 'type': 'ProviderCustody'}]},
 {'name': 'RetiredRoot',
  'getters': [{'name': 'original', 'type': 'RetirementCustody'}],
  'retains': [{'name': 'original', 'type': 'RetirementCustody'}]}]

METHODS = [{'name': 'prepare_first_root_setup',
  'arguments': [{'name': 'scope', 'type': 'scope', 'ownership': 'borrow'}],
  'result': 'RootSetupPrepared',
  'failure': 'Recovery'},
 {'name': 'authenticate_first_root_setup',
  'arguments': [{'name': 'prepared', 'type': 'RootSetupPrepared', 'ownership': 'move'}],
  'result': 'RootSetupAuthentication',
  'failure': 'Recovery'},
 {'name': 'finish_first_root_setup',
  'arguments': [{'name': 'authentication', 'type': 'RootSetupAuthentication', 'ownership': 'move'}],
  'result': 'AdmittedRoot',
  'failure': 'Recovery'},
 {'name': 'observe_existing_root',
  'arguments': [{'name': 'scope', 'type': 'scope', 'ownership': 'borrow'}],
  'result': 'RootObservation',
  'failure': 'Recovery'},
 {'name': 'admit_existing_root',
  'arguments': [{'name': 'observation', 'type': 'RootObservation', 'ownership': 'move'}],
  'result': 'AdmittedRoot',
  'failure': 'Recovery'},
 {'name': 'observe_provider',
  'arguments': [{'name': 'root', 'type': 'AdmittedRoot', 'ownership': 'borrow'},
                {'name': 'candidate', 'type': 'AuthorityDescriptorBody', 'ownership': 'borrow'}],
  'result': 'ProviderObservation',
  'failure': 'Recovery'},
 {'name': 'verify_descriptor',
  'arguments': [{'name': 'candidate', 'type': 'AuthoritySignedDescriptor', 'ownership': 'borrow'},
                {'name': 'provider', 'type': 'ProviderObservation', 'ownership': 'move'}],
  'result': 'AdmittedDescriptor',
  'failure': 'Recovery'},
 {'name': 'observe_current_governance',
  'arguments': [{'name': 'signature', 'type': 'BoundSignatureMath', 'ownership': 'move'},
                {'name': 'operation', 'type': 'ApprovalImmutableOperation', 'ownership': 'borrow'}],
  'result': 'CurrentGovernance',
  'failure': 'Recovery'},
 {'name': 'compare_identity',
  'arguments': [{'name': 'left', 'type': 'AdmittedDescriptor', 'ownership': 'borrow'},
                {'name': 'right', 'type': 'AdmittedDescriptor', 'ownership': 'borrow'}],
  'result': 'IdentityComparison',
  'failure': 'Recovery'},
 {'name': 'verify_bound_signature_math',
  'arguments': [{'name': 'descriptor', 'type': 'AdmittedDescriptor', 'ownership': 'move'},
                {'name': 'operation', 'type': 'LocalAuthorityScopedOperation', 'ownership': 'borrow'},
                {'name': 'material', 'type': 'ApprovalSignatureMaterial', 'ownership': 'borrow'}],
  'result': 'BoundSignatureMath',
  'failure': 'Recovery'},
 {'name': 'retire',
  'arguments': [{'name': 'provider', 'type': 'ProviderObservation', 'ownership': 'move'}],
  'result': 'RetirementCustody',
  'failure': 'Recovery'},
 {'name': 'inspect_retirement',
  'arguments': [{'name': 'custody', 'type': 'RetirementCustody', 'ownership': 'move'}],
  'result': 'RetirementOutcome',
  'failure': 'Recovery'}]

VIEWS = [{'name': 'RootAdmissionOrigin', 'variants': ['RootObservation', 'RootSetupAuthentication']},
 {'name': 'RecoveryOrigin',
  'variants': ['scope',
               'RootObservation',
               'RootSetupPrepared',
               'RootSetupAuthentication',
               'AdmittedRoot',
               'ProviderCustody',
               'ProviderObservation',
               'AdmittedDescriptor',
               'CurrentGovernance',
               'BoundSignatureMath',
               'RetirementCustody']},
 {'name': 'RetirementOutcome', 'variants': ['RetiredRoot', 'RetirementCustody', 'Recovery']}]


def load(root: Path, rows: list[dict], profiles: dict) -> dict:
    value = tomllib.loads((root / "native_boundaries/authority_admission.toml").read_text())
    fields = {"version", "status", "native_graph", "serialization", "descriptor_profile", "descriptor_domain",
              "signer_id_domain", "subject_profiles", "issuer_profiles", "requirements", "role", "method", "view"}
    keys(value, fields, fields)
    if (type(value["version"]) is not int or value["version"] != 1
            or value["status"] != "sealed-interface-only" or value["native_graph"] != "NativeInstallationGraph"
            or value["serialization"] != "forbidden" or value["descriptor_profile"] != "authority-descriptor-v1"
            or value["descriptor_domain"] != "poolhouse.authority.descriptor.v1\0"
            or value["signer_id_domain"] != "poolhouse.authority.signer-id.v1\0"
            or value["subject_profiles"] != ["approval-immutable-v1", "local-authority-scoped-v1"]
            or value["issuer_profiles"] != ["authority-descriptor-v1"]
            or value["requirements"] != REQUIREMENTS or value["role"] != ROLES
            or value["method"] != METHODS or value["view"] != VIEWS):
        raise ValueError("unsupported authority admission interface")
    definitions = {row["name"]: row for row in rows}
    if any(name not in definitions or definitions[name]["visibility"] != "private" for name in
           ("AuthorityRootDescription", "AuthorityDescriptorBody", "AuthoritySignedDescriptor")):
        raise ValueError("authority descriptor DATA must remain private")
    by_name = {profile["name"]: profile for profile in profiles["profile"]}
    if set(by_name) != {*value["subject_profiles"], *value["issuer_profiles"]}:
        raise ValueError("authority purpose sets differ from canonical transcripts")
    descriptor = by_name["authority-descriptor-v1"]
    if (descriptor["root"] != "AuthorityDescriptorBody" or descriptor["omit_root_field"] != "descriptor_id"
            or descriptor["domain"] != value["descriptor_domain"]):
        raise ValueError("descriptor issuer purpose mismatch")
    return value


def signer_id(public_key: dict) -> str:
    """Content identity only; algorithm/key bytes are not an admitted person or root."""
    if type(public_key) is not dict or set(public_key) != {"kind", "value"}:
        raise ValueError("invalid signer key identity")
    algorithm, encoded = public_key["kind"], public_key["value"]
    if type(algorithm) is not str or algorithm not in {"ed25519", "p256"} or type(encoded) is not str:
        raise ValueError("unsupported signer key algorithm")
    from poolhouse.api.node_contract._codec import _valid
    if not _valid("bytes", encoded):
        raise ValueError("invalid signer key bytes")
    raw = bytes.fromhex(encoded)
    if len(raw) != {"ed25519": 32, "p256": 65}[algorithm] or (algorithm == "p256" and raw[0] != 4):
        raise ValueError("invalid signer key shape")
    tag = algorithm.encode("ascii")
    message = (b"poolhouse.authority.signer-id.v1\0" + len(tag).to_bytes(4, "big") + tag
               + len(raw).to_bytes(4, "big") + raw)
    return hashlib.sha256(message).hexdigest()


def validate_descriptor(value: dict) -> None:
    """Structural profile consistency only. No root/membership/provider admission."""
    import hmac
    if not hmac.compare_digest(value["signer_id"], signer_id(value["public_key"])):
        raise ValueError("descriptor signer identity mismatch")
    algorithm = value["public_key"]["kind"]
    policy = value["signature_policy"]["kind"]
    if policy != {"ed25519": "ed25519_raw_message", "p256": "p256_sha256_low_s_der"}[algorithm]:
        raise ValueError("descriptor key and signature policy mismatch")
    scope = value["scope"]
    if scope["kind"] == "installation_credential":
        context = scope["value"]
        if [profile["kind"] for profile in value["operation_profiles"]] != ["local_authority_scoped_v1"]:
            raise ValueError("installation descriptor cannot admit pool or issuer profiles")
    else:
        label = "context" if scope["kind"] == "genesis" else "issuance_context"
        context = scope["value"][label]
    if (context["authority_root"] != value["authority_root"]
            or context["authority_generation"] != value["authority_generation"]):
        raise ValueError("descriptor issuance context mismatch")
    if int(value["issued_at"]) >= int(value["expires_at"]):
        raise ValueError("invalid descriptor validity interval")


def render(model: dict) -> str:
    """Private invariant wrappers and sealed declarations, with no constructors/backend."""
    roles = {role["name"] for role in model["role"]}
    views = {view["name"] for view in model["view"]}
    output = ["// Generated by scripts/generate_node_contract.py. No platform admission or issuer.",
              "use std::{marker::PhantomData, sync::Arc};", "use crate::contract::*;",
              "use crate::contract_lifecycle::NativeInstallationGraph;", "mod seal {", "pub trait Platform {}"]
    output += [f"pub trait {role} {{}}" for role in sorted(roles)]
    output += ["}", "pub(crate) struct AuthorityScope<'owner, G: AuthorityAdmissionGraph> {",
               "installation: <G::Native as NativeInstallationGraph>::Installation,",
               "invariant: PhantomData<fn(&'owner mut ()) -> &'owner mut ()>,", "}",
               "impl<'owner, G: AuthorityAdmissionGraph> AuthorityScope<'owner, G> {",
               "pub(crate) fn installation(&self) -> &<G::Native as NativeInstallationGraph>::Installation { &self.installation }", "}",
               "pub(crate) trait AuthorityAdmissionGraph: seal::Platform + Sized {", "type Native: NativeInstallationGraph;"]
    output += [f"type {role}: {role}Projection<Self>;" for role in sorted(roles)]
    output += ["}"]

    def projection_type(name):
        if name.startswith("native."):
            return "&<G::Native as NativeInstallationGraph>::" + name.split(".")[1]
        if name in roles:
            return "&G::" + name
        return "&" + name

    shared = {"AdmittedRoot", "AdmittedDescriptor"}
    for role in model["role"]:
        name = role["name"]
        retained = {field["name"] for field in role["retains"]}
        output += [f"pub(crate) trait {name}Projection<G: AuthorityAdmissionGraph>: seal::{name} {{"]
        for field in role["getters"]:
            if field["type"] not in views and field["name"] not in retained:
                output.append(f"fn {field['name']}(&self) -> {projection_type(field['type'])};")
        output += ["}"]
        storage = name + "Custody" if name in shared else name
        output += [f"#[must_use] pub(crate) struct {storage}<'owner, G: AuthorityAdmissionGraph> {{",
                   f"inner: G::{name},", "scope: Arc<AuthorityScope<'owner, G>>,"]
        for field in role["retains"]:
            output.append(f"{field['name']}: {field['type']}<'owner, G>,")
        output += ["}"]
        access = "self.custody" if name in shared else "self"
        if name in shared:
            output += [f"#[must_use] pub(crate) struct {name}<'owner, G: AuthorityAdmissionGraph> {{",
                       f"custody: Arc<{storage}<'owner, G>>,", "}"]
        output += [f"impl<'owner, G: AuthorityAdmissionGraph> {name}<'owner, G> {{",
                   f"pub(crate) fn scope(&self) -> &AuthorityScope<'owner, G> {{ &{access}.scope }}",
                   f"pub(crate) fn description(&self) -> &G::{name} {{ &{access}.inner }}"]
        if name in shared:
            # Retain an existing opaque origin only; never an admission constructor.
            output += ["fn retain_private(&self) -> Self { Self { custody: Arc::clone(&self.custody) } }"]
        output += [f"pub(crate) fn {field['name']}(&self) -> &{field['type']}<'owner, G> {{ &{access}.{field['name']} }}"
                   for field in role["retains"]]
        output += ["}"]
    for view in model["view"]:
        output.append(f"#[must_use] pub(crate) enum {view['name']}<'owner, G: AuthorityAdmissionGraph> {{")
        for variant in view["variants"]:
            output.append("Scope(Arc<AuthorityScope<'owner, G>>)," if variant == "scope" else f"{variant}({variant}<'owner, G>),")
        output.append("}")
    output += ["pub(crate) trait AuthorityAdmission<'owner, G: AuthorityAdmissionGraph>: seal::Platform {"]
    for method in model["method"]:
        arguments = []
        for argument in method["arguments"]:
            name = argument["type"]
            target = "AuthorityScope<'owner, G>" if name == "scope" else f"{name}<'owner, G>" if name in roles else name
            arguments.append(argument["name"] + ": " + ("&" if argument["ownership"] == "borrow" else "") + target)
        output.append(f"fn {method['name']}(&self, {', '.join(arguments)}) -> Result<{method['result']}<'owner, G>, Recovery<'owner, G>>;")
    output += ["}"]
    return "\n".join(output) + "\n"
