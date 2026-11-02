"""Dormant service contracts preserve cold-start availability and opaque owned phases."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from node_contract import adapters, service_lifecycle  # noqa: E402
from node_contract.load import load_with_ownership  # noqa: E402

pytestmark = pytest.mark.redteam


@pytest.fixture
def source():
    meta, _, _, _ = load_with_ownership(ROOT / 'contracts/node')
    return meta['service_lifecycle']


@pytest.mark.parametrize('change', [
    'public', 'json', 'v1', 'cold-effect', 'second-service', 'bare-failure',
    'discard-origin', 'consent-is-approval', 'protected-inspection', 'missing-requirement', 'boolean-version',
    'passive-identity', 'passive-path', 'passive-consent-proof',
])
def test_service_source_refuses_added_authority_or_lost_custody(tmp_path, monkeypatch, source, change):
    data = copy.deepcopy(source)
    if change == 'public':
        data['visibility'] = 'public'
    elif change == 'json':
        data['serialization'] = 'json'
    elif change == 'v1':
        data['transcript_profile'] = 'native-authority-v1'
    elif change == 'cold-effect':
        row = next(row for row in data['role'] if row['name'] == 'ActivationAuthorization')
        row['getters'].append({'name': 'permit', 'type': 'effect.Permit'})
    elif change == 'second-service':
        row = next(row for row in data['method'] if row['name'] == 'activate')
        row['arguments'].append({'name': 'service', 'type': 'ServiceScope', 'ownership': 'borrow'})
    elif change == 'bare-failure':
        data['method'][1]['failure'] = 'NativeAdapterError'
    elif change == 'discard-origin':
        next(row for row in data['role'] if row['name'] == 'ProtectedRecovery')['retains'] = []
    elif change == 'consent-is-approval':
        row = next(row for row in data['role'] if row['name'] == 'StartupPending')
        next(field for field in row['getters'] if field['name'] == 'consent')['type'] = 'ApprovalId'
    elif change == 'protected-inspection':
        next(row for row in data['role'] if row['name'] == 'ServiceInspection')['graph'] = 'ProtectedGraph'
    elif change == 'missing-requirement':
        data['requirements'].remove('cancelled-requires-restored-and-verified-prior-effective-state')
    elif change == 'passive-identity':
        data['passive']['fields'].append({'name': 'identity', 'type': 'NodeIdentity'})
    elif change == 'passive-path':
        data['passive']['input'] = 'caller-selected-path'
    elif change == 'passive-consent-proof':
        data['passive']['os_status_mapping']['requires_approval'] = 'ProductApproved'
    else:
        data['version'] = True
    root = tmp_path / 'contract'
    path = root / 'native_boundaries/service_lifecycle.toml'
    path.parent.mkdir(parents=True)
    path.write_text('fixture parsed as the supplied data')
    monkeypatch.setattr(service_lifecycle.tomllib, 'loads', lambda text: data)
    with pytest.raises(ValueError):
        service_lifecycle.load(root, [{'name': name} for name in ('NodeIdentity', 'StartupChangeRequest')])


def compile_probe(tmp_path, source, probe, *, inside=False, covariant=False):
    from native_cargo import ACCOUNT, RUN_S, testnode
    from test_native_installation_graph import rust_source

    declaration = service_lifecycle.render(source)
    if inside:
        marker = '\n}\npub(crate) use service_projection::*;'
        assert marker in declaration
        declaration = declaration.replace(marker, '\n' + probe + marker)
        probe = ''
    if covariant:
        declaration = declaration.replace("PhantomData<fn(&'owner mut ()) -> &'owner mut ()>", "PhantomData<&'owner ()>")
    code = rust_source(adapters.load(ROOT / 'contracts/node'), declaration + '\n' + probe)
    canonical = {kind for getters in service_lifecycle.ROLES.values() for _, kind in getters
                 if '.' not in kind and kind not in service_lifecycle.ROLES and kind not in service_lifecycle.OBSERVATIONS}
    canonical |= {field['type'] for field in service_lifecycle.PASSIVE['fields']
                  if field['type'] not in service_lifecycle.OBSERVATIONS}
    code = code.replace('mod contract {', 'mod contract {' + '\n'.join(f'pub(crate) struct {name};' for name in canonical))
    path = tmp_path / 'service.rs'
    path.write_text(code)
    return subprocess.run([testnode.tool('rustc'), '--edition=2021', '--crate-type=lib', '--emit=metadata',
                           '--error-format=json', '--out-dir', str(tmp_path), str(path)],
                          capture_output=True, text=True, timeout=RUN_S, check=False,
                          env={**os.environ, 'RUSTUP_HOME': str(ACCOUNT / '.rustup'), 'CARGO_HOME': str(ACCOUNT / '.cargo')})


def errors(result):
    return [json.loads(line) for line in result.stderr.splitlines() if line.startswith('{')]


def test_inspect_and_cold_activation_compile_without_full_graph_or_protected_traits(tmp_path, source):
    probe = '''
fn inspect<'a, S: ActivationGraph, P: NativeServiceInspection<'a,S>>(p: &mut P, service: &ServiceScope<'a,S>) {
    let _ = p.inspect(service);
}
fn activate<'a, S: ActivationGraph, P: NativeServiceActivation<'a,S>>(p: &mut P, authorization: ActivationAuthorization<'a,S>) {
    let _ = p.activate(authorization);
}
'''
    result = compile_probe(tmp_path, source, probe)
    assert result.returncode == 0, result.stderr


def test_activation_cannot_choose_an_independent_service_target(tmp_path, source):
    probe = '''
fn wrong<'a, S: ActivationGraph, P: NativeServiceActivation<'a,S>>(p: &mut P, service: &ServiceScope<'a,S>, authorization: ActivationAuthorization<'a,S>) {
    let _ = p.activate(service, authorization);
}
'''
    result = compile_probe(tmp_path, source, probe)
    assert result.returncode != 0
    assert any((row.get('code') or {}).get('code') == 'E0061' for row in errors(result)), result.stderr


def test_owner_brand_cannot_be_shortened_even_when_lifetimes_overlap(tmp_path, source):
    probe = '''
fn wrong<'long: 'short, 'short, S: ActivationGraph>(auth: ActivationAuthorization<'long,S>) -> ActivationAuthorization<'short,S> {
    auth
}
'''
    result = compile_probe(tmp_path, source, probe)
    assert result.returncode != 0 and 'lifetime may not live long enough' in result.stderr, result.stderr
    # Mutation demonstrates this is the invariant brand, not an unrelated compiler refusal.
    weakened = compile_probe(tmp_path, source, probe, covariant=True)
    assert weakened.returncode == 0, weakened.stderr


@pytest.mark.parametrize('role,graph', [('StartupRecovery', 'StartupGraph'), ('ProtectedRecovery', 'ProtectedGraph')])
def test_recovery_cannot_discard_original_inflight_owner(tmp_path, source, role, graph):
    probe = f'''
fn wrong<'a, S: {graph}>(inner: S::{role}, scope: Arc<ServiceScope<'a,S>>) -> {role}<'a,S>
where S::Native: NativeGraph {{
    {role} {{ inner, scope }}
}}
'''
    result = compile_probe(tmp_path, source, probe, inside=True)
    assert result.returncode != 0
    assert any((row.get('code') or {}).get('code') == 'E0063' and 'original' in row['message']
               for row in errors(result)), result.stderr


def test_service_handles_are_not_exported_or_decoded_by_the_public_data_package():
    from poolhouse.api import node_contract

    for name in ('ServiceScope', 'ActivationAuthorization', 'StartupPending', 'ProtectedAuthenticated'):
        assert not hasattr(node_contract, name)



def test_passive_preinstall_probe_compiles_without_service_or_installation_handle(tmp_path, source):
    probe = """
fn passive<P: NativeServiceAvailability>(platform: &mut P) -> PassiveServiceObservation {
    platform.observe()
}
"""
    result = compile_probe(tmp_path, source, probe)
    assert result.returncode == 0, result.stderr


def test_passive_observation_cannot_be_used_as_protected_authorization(tmp_path, source):
    probe = """
fn wrong<'a, S: ProtectedGraph>(observation: PassiveServiceObservation) -> ProtectedAuthenticated<'a,S>
where S::Native: NativeGraph {
    observation
}
"""
    result = compile_probe(tmp_path, source, probe)
    assert result.returncode != 0
    assert any((row.get('code') or {}).get('code') == 'E0308' for row in errors(result)), result.stderr



def test_renderer_refuses_unvalidated_rust_source_injection(source):
    data = copy.deepcopy(source)
    data['role'][0]['getters'][0]['type'] = 'NodeIdentity; pub fn forged() {}'
    with pytest.raises(ValueError):
        service_lifecycle.render(data)
