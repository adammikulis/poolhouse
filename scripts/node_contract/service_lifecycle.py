"""Closed dormant service/helper declarations; no platform or authority issuer."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

REQUIREMENTS = frozenset({'activate-does-not-implicitly-configure-startup-mode',
           'activation-authorization-is-installed-supervisor-native-os-caller-admission-not-a-public-boolean',
           'activation-only-starts-or-attaches-owned-service-and-grants-no-domain-or-installation-right',
           'activation-requires-admitted-native-os-launch-rights-not-raw-channel-facts-alone',
           'activation-unknown-retains-actual-attempt-channel-service-and-cleanup-owner',
           'authorization-alone-selects-owned-service-no-independent-target-argument',
           'cancel-and-commit-use-one-atomic-terminal-fence',
           'cancelled-requires-restored-and-verified-prior-effective-state',
           'cold-start-activation-needs-no-running-node-effect-permit-or-caller-message',
           'commit-winner-is-reported-committed-after-cancel-race',
           'configuration-and-protected-graphs-inherit-same-installation-type-and-owner-scope',
           'consume-and-cancel-are-atomic-and-one-shot',
           'consume-revalidates-current-installation-head-generation-nonce-and-expiry',
           'disconnect-owner-code-or-authority-change-retains-cancellation-custody',
           'drop-is-not-rollback-reap-or-domain-completion',
           'fresh-authentication-is-opaque-and-not-a-dialog-boolean',
           'helper-does-not-schedule-or-discover-product-work',
           'inspection-is-passive-and-does-not-create-projects-claims-identities-or-consent',
           'minimal-inspection-and-activation-have-no-producer-effect-or-protected-profile-prerequisite',
           'neither-allows-ordinary-authorized-attach-and-demand-activation-without-product-approval',
           'neither-controls-scheduling-not-authorized-demand-availability',
           'no-platform-implementation-issuer-or-native-acceptance-claim',
           'no-v1-fallback-no-json-proof-no-arbitrary-digest-signing',
           'normal-policy-authorization-does-not-imply-a-protected-presentation',
           'one-owned-service-installation-endpoint-storage-and-principal-for-all-modes',
           'one-pending-startup-change-per-owned-service',
           'only-node-owned-opaque-authorization-can-prepare-a-change',
           'os-consent-denial-withdrawal-and-storage-unavailability-remain-distinct',
           'os-consent-is-not-persisted-as-governed-approval',
           'os-registration-is-not-effective-mode-service-readiness-or-product-approval',
           'owned-recovery-origin-retains-original-phase-snapshot-authentication-and-native-resources',
           'passive-app-and-plist-binding-is-fixed-planned-and-currently-absent',
           'passive-inspection-resumes-existing-pending-operation-without-reprompt',
           'passive-observation-never-registers-starts-unregisters-prompts-or-grants-rights',
           'passive-preinstall-observation-needs-no-installation-or-service-capability',
           'passive-registration-eligibility-is-not-identity-principal-readiness-mode-or-authority',
           'passive-timestamp-is-not-freshness-authority-and-reason-is-bounded-value-free',
           'prepare-binds-original-operation-generation-request-revision-and-prior-effective-state',
           'protected-result-binds-current-owner-session-helper-code-and-visible-snapshot',
           'protected-result-binds-exact-original-scoped-operation-and-native-message',
           'requires-approval-os-status-does-not-distinguish-pending-denied-or-withdrawn-consent',
           'same-native-effect-and-invariant-owner-scope-through-every-handle',
           'service-scope-is-generative-invariant-and-identity-revalidated-before-effects',
           'storage-unavailability-cannot-project-ready',
           'unknown-retains-operation-service-custody-and-cleanup-obligation',
           'unresolved-drop-transfers-custody-to-owner-and-never-settles-domain-state',
           'unresolved-transcript-profile-forbids-production-validation'})

ROLES = {'ServiceRuntime': [('node_identity', 'NodeIdentity'),
                    ('endpoint', 'Endpoint'),
                    ('storage', 'Storage'),
                    ('principal', 'native.ConfiguredServicePrincipal')],
 'Endpoint': [],
 'Storage': [],
 'NormalAuthorization': [('owner', 'effect.Owner'),
                         ('permit', 'effect.Permit'),
                         ('operation', 'effect.Operation'),
                         ('caller', 'native.Caller'),
                         ('message', 'native.CallerMessage')],
 'StartupAuthorization': [('normal', 'NormalAuthorization'),
                          ('request', 'StartupChangeRequest'),
                          ('operation_id', 'OperationId'),
                          ('previous_effective', 'StartupServiceObservation')],
 'ServiceInspection': [('view', 'StartupView'),
                       ('os_status', 'OsServiceStatus'),
                       ('consent', 'OsConsentState'),
                       ('readiness', 'ServiceReadiness')],
 'InspectionUnknown': [('reason', 'SafeMessage')],
 'ServiceActivation': [('authorization', 'ActivationAuthorization'),
                       ('view', 'StartupView'),
                       ('readiness', 'ServiceReadiness')],
 'ActivationRecovery': [('authorization', 'ActivationAuthorization'),
                        ('attempt', 'ActivationAttempt'),
                        ('reason', 'SafeMessage')],
 'StartupPending': [('authorization', 'StartupAuthorization'),
                    ('view', 'StartupChangeView'),
                    ('consent', 'OsConsentState'),
                    ('cleanup', 'CleanupObligation')],
 'StartupRecovery': [('authorization', 'StartupAuthorization'),
                     ('view', 'StartupChangeView'),
                     ('cleanup', 'CleanupObligation'),
                     ('reason', 'SafeMessage')],
 'StartupTerminal': [('authorization', 'StartupAuthorization'), ('view', 'StartupChangeView')],
 'CleanupObligation': [('operation_id', 'OperationId')],
 'ProtectedAuthorization': [('normal', 'NormalAuthorization'),
                            ('operation', 'LocalAuthorityScopedOperation'),
                            ('owner_session', 'OwnerSession')],
 'OwnerSession': [('caller', 'native.Caller'), ('message', 'native.CallerMessage')],
 'VisibleSnapshot': [('operation', 'LocalAuthorityScopedOperation'),
                     ('owner_session', 'OwnerSession'),
                     ('caller_message', 'native.CallerMessage')],
 'FreshAuthentication': [('helper_process', 'native.Process'),
                         ('helper_code', 'native.MeasuredAsset'),
                         ('owner_session', 'OwnerSession'),
                         ('caller_message', 'native.CallerMessage')],
 'ProtectedPrepared': [('authorization', 'ProtectedAuthorization'),
                       ('snapshot', 'VisibleSnapshot'),
                       ('cleanup', 'CleanupObligation')],
 'ProtectedAuthenticated': [('prepared', 'ProtectedPrepared'),
                            ('fresh_authentication', 'FreshAuthentication')],
 'ProtectedRecovery': [('authorization', 'ProtectedAuthorization'),
                       ('observation', 'LocalAuthorityBoundObservation'),
                       ('cleanup', 'CleanupObligation')],
 'ProtectedConsumed': [('authorization', 'ProtectedAuthorization'),
                       ('observation', 'LocalAuthorityBoundObservation')],
 'ProtectedCancelled': [('authorization', 'ProtectedAuthorization'),
                        ('observation', 'LocalAuthorityBoundObservation')],
 'ActivationAuthorization': [('installation', 'native.Installation'),
                             ('channel', 'native.RawChannel'),
                             ('principal', 'native.ConfiguredOwnerPrincipal')],
 'ActivationAttempt': [('channels', 'slice.native.RawChannel'),
                       ('processes', 'slice.native.Process'),
                       ('cleanup', 'ActivationCleanup')],
 'ActivationCleanup': []}

OBSERVATIONS = {'OsServiceStatus': ['NotRegistered',
                     'RegisteredEligible',
                     'RequiresConsent',
                     'NotFound',
                     'Unknown'],
 'OsConsentState': ['NotRequired',
                    'Pending',
                    'Satisfied',
                    'Denied',
                    'Withdrawn',
                    'Unavailable',
                    'Unknown'],
 'ServiceReadiness': ['Ready', 'NotRunning', 'StorageUnavailable', 'Unknown'],
 'PassiveServiceAvailability': ['BindingUnavailable',
                                'PlistAbsent',
                                'Unverified',
                                'NotRegistered',
                                'RegisteredEligible',
                                'RequiresConsent',
                                'NotFound',
                                'Unknown']}

OUTCOMES = {'ServiceProgress': [('Pending', 'StartupPending'),
                     ('Committed', 'StartupTerminal'),
                     ('Unknown', 'StartupRecovery')],
 'ServicePendingState': [('Pending', 'StartupPending'), ('Unknown', 'StartupRecovery')],
 'ServiceCancellation': [('Committed', 'StartupTerminal'),
                         ('RestoredCancelled', 'StartupTerminal'),
                         ('Unknown', 'StartupRecovery')],
 'ProtectedPresentation': [('Pending', 'ProtectedPrepared'),
                           ('Authenticated', 'ProtectedAuthenticated'),
                           ('Cancelled', 'ProtectedCancelled'),
                           ('Unknown', 'ProtectedRecovery')],
 'ProtectedPendingState': [('Prepared', 'ProtectedPrepared'),
                           ('Authenticated', 'ProtectedAuthenticated'),
                           ('Unknown', 'ProtectedRecovery')],
 'ProtectedCancellation': [('Committed', 'ProtectedConsumed'),
                           ('Cancelled', 'ProtectedCancelled'),
                           ('Unknown', 'ProtectedRecovery')],
 'StartupRecoveryOrigin': [('Authorization', 'StartupAuthorization'),
                           ('Pending', 'StartupPending')],
 'ProtectedRecoveryOrigin': [('Authorization', 'ProtectedAuthorization'),
                             ('Prepared', 'ProtectedPrepared'),
                             ('Authenticated', 'ProtectedAuthenticated')]}

METHODS = [('inspection',
  'inspect',
  [('service', 'ServiceScope', 'borrow')],
  'ServiceInspection',
  'InspectionUnknown'),
 ('activation',
  'activate',
  [('authorization', 'ActivationAuthorization', 'move')],
  'ServiceActivation',
  'ActivationRecovery'),
 ('startup',
  'prepare',
  [('authorization', 'StartupAuthorization', 'move')],
  'StartupPending',
  'StartupRecovery'),
 ('startup', 'apply', [('pending', 'StartupPending', 'move')], 'ServiceProgress', ''),
 ('startup', 'cancel', [('pending', 'ServicePendingState', 'move')], 'ServiceCancellation', ''),
 ('protected',
  'prepare',
  [('authorization', 'ProtectedAuthorization', 'move')],
  'ProtectedPrepared',
  'ProtectedRecovery'),
 ('protected', 'present', [('prepared', 'ProtectedPrepared', 'move')], 'ProtectedPresentation', ''),
 ('protected',
  'consume',
  [('authenticated', 'ProtectedAuthenticated', 'move')],
  'ProtectedConsumed',
  'ProtectedRecovery'),
 ('protected',
  'cancel',
  [('pending', 'ProtectedPendingState', 'move')],
  'ProtectedCancellation',
  '')]

RETAINS = {'StartupAuthorization': [('normal', 'NormalAuthorization')],
 'ServiceActivation': [('authorization', 'ActivationAuthorization')],
 'ActivationRecovery': [('authorization', 'ActivationAuthorization'),
                        ('attempt', 'ActivationAttempt')],
 'StartupPending': [('authorization', 'StartupAuthorization')],
 'StartupRecovery': [('original', 'StartupRecoveryOrigin')],
 'StartupTerminal': [('authorization', 'StartupAuthorization')],
 'ProtectedAuthorization': [('normal', 'NormalAuthorization')],
 'ProtectedPrepared': [('authorization', 'ProtectedAuthorization'),
                       ('snapshot', 'VisibleSnapshot')],
 'ProtectedAuthenticated': [('prepared', 'ProtectedPrepared'),
                            ('fresh_authentication', 'FreshAuthentication')],
 'ProtectedRecovery': [('original', 'ProtectedRecoveryOrigin')],
 'ProtectedConsumed': [('original', 'ProtectedAuthenticated')],
 'ProtectedCancelled': [('original', 'ProtectedRecoveryOrigin')]}

GRAPHS = {'ActivationGraph': {'inherits': '', 'native': 'NativeInstallationGraph', 'effect': False},
 'StartupGraph': {'inherits': 'ActivationGraph', 'native': 'NativeGraph', 'effect': True},
 'ProtectedGraph': {'inherits': 'StartupGraph', 'native': 'NativeGraph', 'effect': False}}

ROLE_GRAPH = {'ServiceRuntime': 'ActivationGraph',
 'Endpoint': 'ActivationGraph',
 'Storage': 'ActivationGraph',
 'NormalAuthorization': 'StartupGraph',
 'StartupAuthorization': 'StartupGraph',
 'ServiceInspection': 'ActivationGraph',
 'InspectionUnknown': 'ActivationGraph',
 'ServiceActivation': 'ActivationGraph',
 'ActivationRecovery': 'ActivationGraph',
 'StartupPending': 'StartupGraph',
 'StartupRecovery': 'StartupGraph',
 'StartupTerminal': 'StartupGraph',
 'CleanupObligation': 'StartupGraph',
 'ProtectedAuthorization': 'ProtectedGraph',
 'OwnerSession': 'ProtectedGraph',
 'VisibleSnapshot': 'ProtectedGraph',
 'FreshAuthentication': 'ProtectedGraph',
 'ProtectedPrepared': 'ProtectedGraph',
 'ProtectedAuthenticated': 'ProtectedGraph',
 'ProtectedRecovery': 'ProtectedGraph',
 'ProtectedConsumed': 'ProtectedGraph',
 'ProtectedCancelled': 'ProtectedGraph',
 'ActivationAuthorization': 'ActivationGraph',
 'ActivationAttempt': 'ActivationGraph',
 'ActivationCleanup': 'ActivationGraph'}

OUTCOME_GRAPH = {'ServiceProgress': 'StartupGraph',
 'ServicePendingState': 'StartupGraph',
 'ServiceCancellation': 'StartupGraph',
 'ProtectedPresentation': 'ProtectedGraph',
 'ProtectedPendingState': 'ProtectedGraph',
 'ProtectedCancellation': 'ProtectedGraph',
 'StartupRecoveryOrigin': 'StartupGraph',
 'ProtectedRecoveryOrigin': 'ProtectedGraph'}

PASSIVE = {'trait': 'NativeServiceAvailability',
 'input': 'none',
 'authority': 'untrusted-observation-only',
 'containing_app_identifier': 'app.poolhouse.app',
 'bundled_plist': 'Contents/Library/LaunchDaemons/app.poolhouse.node.plist',
 'binding_status': 'planned-currently-absent-not-installed-service-identity',
 'fields': [{'name': 'availability', 'type': 'PassiveServiceAvailability'},
            {'name': 'observed_at', 'type': 'UnixMicros'},
            {'name': 'reason', 'type': 'SafeMessage'}],
 'os_status_mapping': {'not_registered': 'NotRegistered',
                       'enabled': 'RegisteredEligible',
                       'requires_approval': 'RequiresConsent',
                       'not_found': 'NotFound',
                       'unrecognized_or_error': 'Unknown'}}


def expected() -> dict:
    """The closed reviewed grammar; names and relationships cannot inject Rust declarations."""
    return {
        'version': 1, 'status': 'incomplete-proposal', 'visibility': 'crate-private-sealed',
        'serialization': 'forbidden', 'native_graph': 'NativeInstallationGraph', 'effect_graph': 'EffectGraph',
        'transcript_profile': 'unresolved-no-production-validation', 'requirements': sorted(REQUIREMENTS),
        'passive': PASSIVE,
        'graph': [{'name': name, **declaration} for name, declaration in GRAPHS.items()],
        'role': [{'name': name, 'graph': ROLE_GRAPH[name], 'getters': [{'name': key, 'type': kind} for key, kind in getters],
                  'retains': [{'name': key, 'type': kind} for key, kind in RETAINS.get(name, [])]}
                 for name, getters in ROLES.items()],
        'observation': [{'name': name, 'variants': variants} for name, variants in OBSERVATIONS.items()],
        'outcome': [{'name': name, 'graph': OUTCOME_GRAPH[name], 'variants': [{'name': key, 'role': role} for key, role in variants]}
                    for name, variants in OUTCOMES.items()],
        'method': [{'owner': owner, 'name': name,
                    'arguments': [{'name': key, 'type': kind, 'ownership': ownership}
                                  for key, kind, ownership in arguments], 'result': result, 'failure': failure}
                   for owner, name, arguments, result, failure in METHODS],
    }


def load(root: Path, rows: list[dict]) -> dict:
    """Load only fixed declarations, checked against the existing canonical data graph."""
    path = root / 'native_boundaries/service_lifecycle.toml'
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('native service declarations cannot be symlinked')
    source = tomllib.loads(path.read_text(encoding='utf-8'))
    requirements = source.get('requirements')
    if not isinstance(requirements, list) or any(type(item) is not str for item in requirements):
        raise ValueError('native service requirements must be explicit strings')
    if len(requirements) != len(set(requirements)):
        raise ValueError('native service requirements cannot repeat')
    source['requirements'] = sorted(requirements)
    if json.dumps(source, sort_keys=True) != json.dumps(expected(), sort_keys=True):
        raise ValueError('unsupported native service declarations or authority relationships')
    known = {row['name'] for row in rows}
    required = {kind for getters in ROLES.values() for _, kind in getters
                if '.' not in kind and kind not in ROLES and kind not in OBSERVATIONS}
    required |= {field['type'] for field in PASSIVE['fields'] if field['type'] not in OBSERVATIONS}
    if not required <= known:
        raise ValueError('native service declarations require existing canonical domain types')
    return source


def _kind(kind: str) -> str:
    if kind.startswith('native.'):
        name = kind.split('.')[1]
        return (name if name in {'ConfiguredServicePrincipal', 'ConfiguredOwnerPrincipal'}
                else f'<S::Native as {"NativeInstallationGraph" if name in {"Installation", "MeasuredAsset", "Process", "Holder", "RawChannel", "Channel"} else "NativeGraph"}>::{name}')
    if kind.startswith('effect.'):
        return f'<S::Effect as EffectGraph<S::Native>>::{kind.split(".")[1]}'
    return 'S::' + kind if kind in ROLES else kind


def _returned(kind: str) -> str:
    if kind.startswith('slice.'):
        return '&[' + _kind(kind[6:]) + ']'
    return '&' + _kind(kind)


def _where(graph: str) -> str:
    return '' if graph == 'ActivationGraph' else ' where S::Native: NativeGraph'


def _projections(source: dict) -> list[str]:
    output = []
    for graph in source['graph']:
        name = graph['name']
        parent = graph['inherits']
        bound = parent + ' + ' if parent else ''
        where = '' if not parent else ' where Self::Native: NativeGraph'
        output.append(f'pub(crate) trait {name}: {bound}seal::{name} + Sized{where} {{')
        if not parent:
            output.append('type Native: NativeInstallationGraph;')
        if graph['effect']:
            output.append('type Effect: EffectGraph<Self::Native>;')
        for role in source['role']:
            if role['graph'] == name:
                output.append(f'type {role["name"]}: {role["name"]}Projection<Self>;')
        output.append('}')
    for role in source['role']:
        name, graph = role['name'], role['graph']
        output.append(f'pub(crate) trait {name}Projection<S: {graph}>: seal::{name}{_where(graph)} {{')
        output.extend(f'fn {field["name"]}(&self) -> {_returned(field["type"])};' for field in role['getters'])
        output.append('}')
    return output


def _handles(source: dict) -> list[str]:
    output = [
        '#[must_use] pub(crate) struct ServiceScope<\'owner, S: ActivationGraph> {',
        'native_installation: <S::Native as NativeInstallationGraph>::Installation,',
        'runtime: S::ServiceRuntime,',
        "invariant_owner: PhantomData<fn(&'owner mut ()) -> &'owner mut ()>,", '}',
        "impl<'owner, S: ActivationGraph> ServiceScope<'owner, S> {",
        'pub(crate) fn installation(&self) -> &<S::Native as NativeInstallationGraph>::Installation { &self.native_installation }',
        'pub(crate) fn runtime(&self) -> &S::ServiceRuntime { &self.runtime }', '}',
    ]
    for row in source['role']:
        name, graph = row['name'], row['graph']
        if name in {'ServiceRuntime', 'Endpoint', 'Storage'}:
            continue
        output += [f"#[must_use] pub(crate) struct {name}<'owner, S: {graph}>{_where(graph)} {{",
                   f'inner: S::{name},', "scope: Arc<ServiceScope<'owner, S>>,",
                   *(f"{field['name']}: {field['type']}<'owner, S>," for field in row['retains']), '}',
                   f"impl<'owner, S: {graph}> {name}<'owner, S>{_where(graph)} {{",
                   "pub(crate) fn scope(&self) -> &ServiceScope<'owner, S> { &self.scope }",
                   f'pub(crate) fn description(&self) -> &S::{name} {{ &self.inner }}', '}']
    return output


def _outcomes(source: dict) -> list[str]:
    output = []
    for row in source['observation']:
        output += [f'#[derive(Debug)] pub(crate) enum {row["name"]} {{',
                   ','.join(row['variants']), '}']
    for row in source['outcome']:
        output.append(f"#[must_use] pub(crate) enum {row['name']}<'owner, S: {row['graph']}>{_where(row['graph'])} {{")
        output.extend(f"{variant['name']}({variant['role']}<'owner, S>)," for variant in row['variants'])
        output.append('}')
    return output


def _methods(source: dict, owner: str, trait: str, seal: str, graph: str) -> list[str]:
    output = [f"pub(crate) trait {trait}<'owner, S: {graph}>: seal::{seal}{_where(graph)} {{"]
    for method in source['method']:
        if method['owner'] != owner:
            continue
        arguments = [f"{arg['name']}: " + ('&' if arg['ownership'] == 'borrow' else '')
                     + f"{arg['type']}<'owner, S>" for arg in method['arguments']]
        result = f"{method['result']}<'owner, S>"
        if method['failure']:
            result = f"Result<{result}, {method['failure']}<'owner, S>>"
        output.append(f"fn {method['name']}(&mut self, " + ', '.join(arguments) + f') -> {result};')
    return [*output, '}']


def render(source: dict) -> str:
    """Dormant Rust interfaces only: no constructor, codec, authorization bridge or OS execution."""
    if json.dumps(source, sort_keys=True) != json.dumps(expected(), sort_keys=True):
        raise ValueError("native service rendering requires the validated closed grammar")
    canonical = sorted({kind for getters in ROLES.values() for _, kind in getters
                        if '.' not in kind and kind not in ROLES and kind not in OBSERVATIONS}
                       | {field['type'] for field in PASSIVE['fields'] if field['type'] not in OBSERVATIONS})
    output = ['// Dormant service/helper proposal. No issuer, platform implementation or protected transcript codec.',
              'mod service_projection {', 'use std::{marker::PhantomData, sync::Arc};',
              'use super::{ConfiguredOwnerPrincipal, ConfiguredServicePrincipal, EffectGraph, NativeGraph, NativeInstallationGraph};',
              'use super::super::contract::{' + ', '.join(canonical) + '};', 'mod seal {',
              'pub trait ActivationGraph {} pub trait StartupGraph {} pub trait ProtectedGraph {}',
              'pub trait Availability {} pub trait Inspection {} pub trait Activation {} pub trait Configuration {} pub trait Helper {}']
    output += [f'pub trait {row["name"]} {{}}' for row in source['role']]
    output += ['}', *_projections(source), *_handles(source), *_outcomes(source)]
    output += _methods(source, 'inspection', 'NativeServiceInspection', 'Inspection', 'ActivationGraph')
    output += _methods(source, 'activation', 'NativeServiceActivation', 'Activation', 'ActivationGraph')
    output += _methods(source, 'startup', 'NativeStartupConfiguration', 'Configuration', 'StartupGraph')
    output += _methods(source, 'protected', 'ProtectedAuthorityPresentation', 'Helper', 'ProtectedGraph')
    output += ['pub(crate) struct PassiveServiceObservation {',
               *(f"pub(crate) {field['name']}: {field['type']}," for field in source['passive']['fields']), '}',
               'pub(crate) trait NativeServiceAvailability: seal::Availability {',
               'fn observe(&mut self) -> PassiveServiceObservation;', '}',
               'pub(crate) const PLANNED_CONTAINING_APP_IDENTIFIER: &str = ' + json.dumps(source['passive']['containing_app_identifier']) + ';',
               'pub(crate) const PLANNED_BUNDLED_PLIST: &str = ' + json.dumps(source['passive']['bundled_plist']) + ';',
               'pub(crate) const PASSIVE_BINDING_STATUS: &str = ' + json.dumps(source['passive']['binding_status']) + ';',
               'pub(crate) const PRODUCTION_PROTECTED_VALIDATION_AVAILABLE: bool = false;',
               '}', 'pub(crate) use service_projection::*;', '']
    return '\n'.join(output)
