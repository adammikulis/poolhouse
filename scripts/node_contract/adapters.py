"""The reviewed native boundary grammar; declarations never issue a proof."""
from __future__ import annotations

import tomllib
from pathlib import Path

from .common import GENERATED_TYPES, IDENTIFIER, keys

HANDLES = ('VerifiedInstallation',
 'RawChannel',
 'VerifiedProducerLaunch',
 'AdmittedProducer',
 'AuthenticatedProducerMessage',
 'VerifiedBootstrap',
 'HeldProcess',
 'RunningProcess',
 'AuthenticatedCaller',
 'BeforeConsumeObligation',
 'RevokedAuthorization',
 'TerminalCleanupObligation',
 'HolderCleanup',
 'OwnedChannelFailure',
 'OwnedSpawnFailure',
 'OwnedReleaseFailure',
 'OwnedCleanupFailure')

REQUIREMENTS = frozenset({'actual-principal-and-process-lifetime',
           'authenticate-retained-producer-launch-and-process',
           'caller-binds-running-process-and-node-binding',
           'cleanup-is-not-domain-completion',
           'crate-private-sealed-platform-and-runtime-parameters',
           'explicit-handle-ownership-and-inheritance',
           'held-drop-kills-and-reaps',
           'hold-binds-node-binding-and-plan',
           'immutable-arguments-environment-directory-and-confinement',
           'no-caller-selected-authority',
           'no-production-proof-issuer-or-bridge',
           'no-serde-or-public-constructors',
           'no-unsupported-platform-fallback',
           'node-validates-caller-observations',
           'opaque-birth-resume-invocation-message',
           'owned-failures-retain-unresolved-custody',
           'phase-specific-single-use-cleanup-obligations',
           'plan-binds-invocation-and-generation',
           'release-binding-matches-held-plan',
           'release-consumes-hold-once',
           'retain-measured-executable-and-assets',
           'revoke-node-authorization-before-terminal-cleanup',
           'single-reader-retained-channel-owner',
           'terminate-without-pid-reuse'})

METHODS = {'verify_installation': {'arguments': [], 'result': 'VerifiedInstallation', 'failure': 'NativeAdapterError'},
 'verify_bootstrap': {'arguments': [{'name': 'installation', 'type': 'VerifiedInstallation', 'ownership': 'borrow'}],
                      'result': 'VerifiedBootstrap',
                      'failure': 'NativeAdapterError'},
 'authenticate_producer': {'arguments': [{'name': 'channel', 'type': 'RawChannel', 'ownership': 'move'},
                                         {'name': 'installation',
                                          'type': 'VerifiedInstallation',
                                          'ownership': 'borrow'},
                                         {'name': 'launch', 'type': 'VerifiedProducerLaunch', 'ownership': 'borrow'}],
                           'result': 'AdmittedProducer',
                           'failure': 'OwnedChannelFailure'},
 'receive_producer_message': {'arguments': [{'name': 'producer', 'type': 'AdmittedProducer', 'ownership': 'borrow'}],
                              'result': 'AuthenticatedProducerMessage',
                              'failure': 'OwnedChannelFailure'},
 'hold_spawn': {'arguments': [{'name': 'producer', 'type': 'AdmittedProducer', 'ownership': 'borrow'},
                              {'name': 'bootstrap', 'type': 'VerifiedBootstrap', 'ownership': 'borrow'},
                              {'name': 'binding', 'type': 'Binding', 'ownership': 'borrow'},
                              {'name': 'plan', 'type': 'SpawnPlan', 'ownership': 'borrow'}],
                'result': 'HeldProcess',
                'failure': 'OwnedSpawnFailure'},
 'observe_held': {'arguments': [{'name': 'held', 'type': 'HeldProcess', 'ownership': 'borrow'}],
                  'result': 'ProcessObservation',
                  'failure': 'NativeAdapterError'},
 'release': {'arguments': [{'name': 'held', 'type': 'HeldProcess', 'ownership': 'move'},
                           {'name': 'binding', 'type': 'Binding', 'ownership': 'borrow'}],
             'result': 'RunningProcess',
             'failure': 'OwnedReleaseFailure'},
 'inspect_caller': {'arguments': [{'name': 'channel', 'type': 'RawChannel', 'ownership': 'move'},
                                  {'name': 'installation', 'type': 'VerifiedInstallation', 'ownership': 'borrow'},
                                  {'name': 'running', 'type': 'RunningProcess', 'ownership': 'borrow'},
                                  {'name': 'binding', 'type': 'Binding', 'ownership': 'borrow'}],
                    'result': 'AuthenticatedCaller',
                    'failure': 'OwnedChannelFailure'},
 'caller_observation': {'arguments': [{'name': 'caller', 'type': 'AuthenticatedCaller', 'ownership': 'borrow'}],
                        'result': 'ProcessObservation',
                        'failure': 'NativeAdapterError'},
 'is_live': {'arguments': [{'name': 'running', 'type': 'RunningProcess', 'ownership': 'borrow'}],
             'result': 'bool',
             'failure': 'NativeAdapterError'},
 'cleanup_before_consume': {'arguments': [{'name': 'running', 'type': 'RunningProcess', 'ownership': 'borrow'},
                                          {'name': 'obligation',
                                           'type': 'BeforeConsumeObligation',
                                           'ownership': 'move'}],
                            'result': 'HolderCleanup',
                            'failure': 'OwnedCleanupFailure'},
 'cancel_hold': {'arguments': [{'name': 'held', 'type': 'HeldProcess', 'ownership': 'move'},
                               {'name': 'revoked', 'type': 'RevokedAuthorization', 'ownership': 'move'},
                               {'name': 'obligation', 'type': 'TerminalCleanupObligation', 'ownership': 'move'}],
                 'result': 'HolderCleanup',
                 'failure': 'OwnedCleanupFailure'},
 'terminate': {'arguments': [{'name': 'running', 'type': 'RunningProcess', 'ownership': 'move'},
                             {'name': 'revoked', 'type': 'RevokedAuthorization', 'ownership': 'move'},
                             {'name': 'obligation', 'type': 'TerminalCleanupObligation', 'ownership': 'move'}],
               'result': 'HolderCleanup',
               'failure': 'OwnedCleanupFailure'}}


def load(root: Path) -> dict:
    value = tomllib.loads((root / "adapters.toml").read_text())
    fixed = {"version": 2, "trait": "NativeCallerPlatform", "binding": "node-owned-runtime-handle",
             "serialization": "forbidden", "spawn_plan": "node-owned-immutable-measured-plan",
             "visibility": "crate-private-sealed", "status": "incomplete-proposal"}
    fields = {*fixed, "handles", "errors", "requirements", "method"}
    keys(value, fields, fields)
    if type(value["version"]) is not int or any(value[key] != expected for key, expected in fixed.items()):
        raise ValueError("unsupported native adapter boundary")
    for name in ("handles", "errors"):
        items = value[name]
        if (not isinstance(items, list) or not items or any(not isinstance(item, str)
                or not IDENTIFIER.fullmatch(item) or item in GENERATED_TYPES for item in items)
                or len(set(items)) != len(items)):
            raise ValueError("invalid adapter identifiers")
    if set(value["handles"]) != set(HANDLES):
        raise ValueError("invalid opaque handle boundary")
    requirements = value["requirements"]
    if (not isinstance(requirements, list) or any(not isinstance(item, str) for item in requirements)
            or len(set(requirements)) != len(requirements) or set(requirements) != REQUIREMENTS):
        raise ValueError("unsupported native adapter ownership requirements")
    if not isinstance(value["method"], list):
        raise ValueError("invalid native adapter methods")
    seen = set()
    for method in value["method"]:
        keys(method, {"name", "arguments", "result", "failure"}, {"name", "arguments", "result", "failure"})
        name = method["name"]
        if not isinstance(name, str) or name not in METHODS or name in seen:
            raise ValueError("unknown or duplicate native adapter method")
        if {key: method[key] for key in ("arguments", "result", "failure")} != METHODS[name]:
            raise ValueError("native adapter method weakens exact ownership or binding")
        seen.add(name)
    if seen != set(METHODS):
        raise ValueError("incomplete native caller adapter")
    return value


def render(adapter: dict) -> str:
    output = [
        "// Incomplete declarations: no platform implementation, issuer, projection graph or bridge.",
        "pub(crate) mod native_seal {",
        "    pub(crate) trait Platform {}",
        "    pub(crate) trait Binding {}",
        "    pub(crate) trait SpawnPlan {}",
        "}",
        "pub(crate) trait NodeBinding: native_seal::Binding {}",
        "pub(crate) trait NodeSpawnPlan: native_seal::SpawnPlan {",
        "    type Binding: NodeBinding;",
        "    fn binding(&self) -> &Self::Binding;",
        "}",
        "/// Runtime handles and diagnostics cannot be converted into core proofs.",
        "pub(crate) trait NativeCallerPlatform<Binding: NodeBinding, SpawnPlan: NodeSpawnPlan<Binding = Binding>>: native_seal::Platform {",
    ]
    output.extend("    type " + name + ";" for name in adapter["handles"])
    for method in adapter["method"]:
        arguments = []
        for argument in method["arguments"]:
            kind = argument["type"] if argument["type"] in {"Binding", "SpawnPlan"} else "Self::" + argument["type"]
            arguments.append(argument["name"] + ": " + ("&" if argument["ownership"] == "borrow" else "") + kind)
        result = method["result"]
        result = result if result in {"bool", "ProcessObservation"} else "Self::" + result
        failure = method["failure"]
        failure = failure if failure == "NativeAdapterError" else "Self::" + failure
        suffix = ", " + ", ".join(arguments) if arguments else ""
        output.append(f'    fn {method["name"]}(&mut self{suffix}) -> Result<{result}, {failure}>;')
    output.append("}\n")
    return "\n".join(output)
