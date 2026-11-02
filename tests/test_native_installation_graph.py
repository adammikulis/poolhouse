"""Native installation capability is nominal and independent of producer/effect admission."""
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
from node_contract import adapters  # noqa: E402

pytestmark = pytest.mark.redteam


@pytest.fixture
def graph_source():
    return adapters.load(ROOT / 'contracts/node')


@pytest.mark.parametrize('change', ['duplicate-installation', 'base-erasure', 'wrong-base', 'borrowed-facts-as-identity'])
def test_installation_graph_refuses_nominal_identity_erasure(graph_source, change):
    data = copy.deepcopy(graph_source)
    base = data['installation_graph']
    if change == 'duplicate-installation':
        data['graph']['associated'].append(base['associated'][0])
    elif change == 'base-erasure':
        base['associated'].pop()
    elif change == 'wrong-base':
        data['graph']['inherits'] = 'CompatibleInstallationFacts'
    else:
        row = next(row for row in data['projection'] if row['name'] == 'RawChannelProjection')
        row['graph'] = 'none'
    with pytest.raises(ValueError):
        adapters.validate(data)


def rust_source(data, probe):
    # Type-only compile fixture: no instances, platform implementations, authentication or OS behavior.
    types = '\n'.join(f'#[derive(Debug)] pub(crate) struct {name};' for name in
                      ('AuditToken', 'InstallationId', 'ResourceGeneration', 'WindowsSid'))
    return ('#![allow(dead_code, unused_imports)]\nmod contract {' + types + '}\nmod boundary {\n'
            '#[derive(Debug)] pub(crate) enum NativeAdapterError { InvalidInstallation, MissingChannel, WrongPrincipal, StaleProcess, '
            'UnprotectedProcess, ConfinementUnavailable, BindingMismatch, Cancelled, Io }\n'
            '#[derive(Debug)] pub(crate) struct ProcessObservation;\n' + adapters.render(data) + '\n' + probe + '\n}\n')


def compile_probe(tmp_path, data, probe):
    from native_cargo import ACCOUNT, RUN_S, testnode

    path = tmp_path / 'types.rs'
    path.write_text(rust_source(data, probe))
    return subprocess.run([testnode.tool('rustc'), '--edition=2021', '--crate-type=lib', '--emit=metadata',
                           '--error-format=json', '--out-dir', str(tmp_path), str(path)],
                          capture_output=True, text=True, timeout=RUN_S, check=False,
                          env={**os.environ, "RUSTUP_HOME": str(ACCOUNT / ".rustup"),
                               "CARGO_HOME": str(ACCOUNT / ".cargo")})


def test_minimal_installation_inspection_needs_no_producer_or_effect_roles(tmp_path, graph_source):
    assert {row['name'] for row in graph_source['installation_graph']['associated']} == {
        'Installation', 'MeasuredAsset', 'Process', 'Holder', 'RawChannel', 'Channel'}
    probe = '''
fn inspect<G: NativeInstallationGraph>(value: &G::Installation) -> &InstallationIdentity {
    value.identity()
}
fn inherited<G: NativeGraph>(value: &G::Installation) -> &<G as NativeInstallationGraph>::Installation {
    value
}
'''
    result = compile_probe(tmp_path, graph_source, probe)
    assert result.returncode == 0, result.stderr


def test_distinct_installation_graphs_cannot_substitute_compatible_projections(tmp_path, graph_source):
    probe = '''
fn incompatible<A: NativeInstallationGraph, B: NativeInstallationGraph>(value: &A::Installation) -> &B::Installation {
    value
}
'''
    result = compile_probe(tmp_path, graph_source, probe)
    assert result.returncode != 0
    errors = [json.loads(line) for line in result.stderr.splitlines() if line.startswith('{')]
    assert any((error.get('code') or {}).get('code') == 'E0308' for error in errors), result.stderr
