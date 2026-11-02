"""Dormant previous-runtime wire boundaries; never a quiescence or updater simulation."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
load = importlib.import_module('node_contract.load').load
render = importlib.import_module('node_contract.python').render


@pytest.fixture(scope='module')
def proposal(tmp_path_factory):
    meta, rows, digest = load(ROOT / 'contracts/node')
    path = tmp_path_factory.mktemp('runtime-selection-codec') / 'codec.py'
    path.write_text(render(meta, rows, digest))
    spec = importlib.util.spec_from_file_location('_runtime_selection_codec', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    evidence = json.loads((ROOT / 'contracts/node/domain-evidence/runtime_selection_jobs.json').read_text())
    return module, meta, rows, evidence


def test_previous_runtime_intent_refuses_target_and_fake_handoff_injection(proposal):
    module, _, _, evidence = proposal
    for vector in evidence['shape_vectors']['accepted']:
        assert getattr(module, vector['type']).from_wire(vector['value']).to_wire() == vector['value']
    for vector in evidence['shape_vectors']['refused']:
        with pytest.raises(ValueError):
            getattr(module, vector['type']).from_wire(vector['value'])


def test_model_data_does_not_export_handoff_or_conflate_selected_and_running(proposal):
    module, meta, rows, evidence = proposal
    assert not hasattr(module, 'RuntimeSelectionHandoffBinding')
    assert not hasattr(module, 'JobFrozenRuntimeSelectionPlan')
    assert not hasattr(module, 'CommitWitness')
    observation = evidence['shape_vectors']['accepted'][2]['value']
    assert observation['running']['kind'] == 'prior_runtime_observed'
    assert module.RuntimeSelectionResult.from_wire(observation).to_wire() == observation
    # A syntactically valid committed observation is raw data, not a production witness.
    assert module.RuntimeSelectionBoundaryObservation.from_wire({'kind': 'committed', 'value': {}})
    ops = {row['name']: row for row in meta['operations']}
    assert not any(name.startswith('runtime.') for name in ops)
    assert {
        'runtime-selection-real-supported-owned-handoff-before-selection-publication-or-node-replacement',
        'runtime-selection-unsupported-old-node-or-incomplete-writer-coverage-stays-pending-with-old-state',
        'runtime-selection-model-json-idle-counts-or-claims-never-issue-quiescence',
        'runtime-selection-claim-only-holds-are-not-active-work-blockers',
    } <= set(ops['jobs.submit']['authorization'])
    assert next(row for row in rows if row['name'] == 'JobFrozenRuntimeSelectionPlan')['visibility'] == 'private'
