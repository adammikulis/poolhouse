"""The reviewed native boundary grammar; declarations never issue a proof."""
from __future__ import annotations

import tomllib
from pathlib import Path

from . import adapter_schema as schema
from .adapter_render import render as _render
from .common import keys

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



ERRORS = ["InvalidInstallation", "MissingChannel", "WrongPrincipal", "StaleProcess", "UnprotectedProcess", "ConfinementUnavailable", "BindingMismatch", "Cancelled", "Io"]


def _same(actual, expected) -> bool:
    """Exact schema constants must not coerce bools/ints or permit unknown subfields."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_same(actual[key], item) for key, item in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected, strict=True))
    return actual == expected


def _rows(value, expected, label):
    if not isinstance(value, list):
        raise ValueError("invalid native " + label)
    seen = set()
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("invalid native " + label)
        name = row["name"]
        if name in seen or name not in expected or not _same(row, {"name": name, **expected[name]}):
            raise ValueError("native " + label + " weakens exact projection or custody")
        seen.add(name)
    if seen != set(expected):
        raise ValueError("incomplete native " + label)


def validate(value: dict) -> dict:
    """Only the reviewed closed graph is accepted; no schema field contains Rust text."""
    fixed = {"version": 3, "trait": "NativeCallerPlatform", "binding": "node-owned-runtime-handle",
             "serialization": "forbidden", "spawn_plan": "node-owned-immutable-measured-plan",
             "visibility": "crate-private-sealed", "status": "incomplete-proposal"}
    fields = {*fixed, "errors", "requirements", "method", "graph", "effect_owner", "projection", "leaf", "view", "representation", "phase", "profile"}
    keys(value, fields, fields)
    if any(not _same(value[key], expected) for key, expected in fixed.items()) or not _same(value["errors"], ERRORS):
        raise ValueError("unsupported native adapter boundary")
    requirements = value["requirements"]
    if (not isinstance(requirements, list) or any(not isinstance(item, str) for item in requirements)
            or len(set(requirements)) != len(requirements) or set(requirements) != REQUIREMENTS | schema.NEW_REQUIREMENTS):
        raise ValueError("unsupported native adapter ownership requirements")
    graph = {"name": "NativeGraph", "seal": "Graph", "associated": [
        {"name": name, "projection": projection, "role": role} for name, projection, role in schema.ASSOCIATIONS]}
    if not _same(value["graph"], graph):
        raise ValueError("native graph loses exact associated-type identity")
    effect = {"graph": "EffectGraph", "seal": "Graph", "trait": "NodeEffectOwner", "owner_seal": "Owner", "identity": schema.G("identity", "Owner"),
              "associated": [{"name": name, "projection": projection} for name, projection in schema.EFFECT_ASSOCIATIONS],
              "method": {"name": "execute", "receiver": "mutable", "arguments": [
                  {"name": "permit", "type": "Permit", "ownership": "move"},
                  {"name": "operation", "type": "Operation", "ownership": "move"}], "result": "Completion", "failure": "Unknown"}}
    if not _same(value["effect_owner"], effect):
        raise ValueError("native effect owner loses moved-input completion ownership")
    for label, expected in (("projection", schema.PROJECTIONS), ("leaf", schema.LEAVES), ("view", schema.VIEWS), ("representation", schema.REPRESENTATIONS)):
        _rows(value[label], expected, label)
    for label, expected in (("phase", schema.PHASES), ("profile", schema.PROFILES)):
        if not _same(value[label], expected):
            raise ValueError("unsupported native " + label)
    _rows(value["method"], METHODS, "methods")
    return value


def load(root: Path) -> dict:
    return validate(tomllib.loads((root / "adapters.toml").read_text()))


def render(value: dict) -> str:
    return _render(validate(value))
