"""Project-free classifier proposal shape checks, never a simulated training run."""
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
    path = tmp_path_factory.mktemp('training-codec') / 'codec.py'
    path.write_text(render(meta, rows, digest))
    spec = importlib.util.spec_from_file_location('_training_contract_codec', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    evidence = json.loads((ROOT / 'contracts/node/domain-evidence/training_jobs.json').read_text())
    return module, meta, rows, evidence


def test_training_and_revised_repair_views_refuse_legacy_shape_and_injection(proposal):
    module, _, _, evidence = proposal
    for vector in evidence['shape_vectors']['accepted']:
        assert getattr(module, vector['type']).from_wire(vector['value']).to_wire() == vector['value']
    for vector in evidence['shape_vectors']['refused']:
        with pytest.raises(ValueError):
            getattr(module, vector['type']).from_wire(vector['value'])


def test_project_free_public_closure_and_repair_only_private_plan(proposal):
    module, meta, rows, _ = proposal
    by_name = {row['name']: row for row in rows}
    seen = set()

    def visit(name):
        if name in seen:
            return
        seen.add(name)
        row = by_name[name]
        for key in ('item',):
            if key in row:
                visit(row[key])
        for field in row.get('fields', []):
            visit(field['type'])
        for case in row.get('variants', []):
            visit(case['type'])

    for name in ('JobTrainClassifyTextIntent', 'TrainingResult'):
        visit(name)
    assert not any(by_name[name]['visibility'] != 'public' for name in seen)
    assert not seen & {'ProjectId', 'ProjectCatalogRef', 'StudioControlRunRef', 'StudioGraphDraftRef'}
    assert not hasattr(module, 'JobFrozenClassifierPlan')
    assert not hasattr(module, 'JobFrozenPlan')
    assert by_name['JobRepairIntent']['variants'] == [{'tag': 'agent_runtime_repair', 'type': 'Empty'}]
    operations = {row['name']: row for row in meta['operations']}
    assert not any(name.startswith('training.') for name in operations)
    assert 'repair-no-cancelled-after-replacement-commit-fence' in operations['jobs.submit']['authorization']
    assert 'classifier-launch-fence-distinct-from-verified-output-publication' in operations['jobs.submit']['authorization']


def test_repair_still_requires_its_original_approval_cancel_and_recovery_boundaries(proposal):
    _, meta, _, _ = proposal
    operations = {row['name']: set(row['authorization']) for row in meta['operations']}
    assert {
        'fresh-exact-plan-bound-protected-authority-and-separate-os-consent-where-required',
        'stale-approval-or-consent-callback-refuses',
        'repair-rechecks-candidate-manifest-source-policy-launcher-prior-state-and-expiry',
        'repair-unknown-effect-retains-candidate-backup-launcher-fence-until-protected-inspection',
        'repair-verifies-installed-artifact-health-source-and-runtime-before-success',
    } <= operations['jobs.submit']
    assert {
        'repair-cancel-precommit-only-atomic-with-submit-fence',
        'repair-commit-fenced-or-unknown-never-report-cancelled',
        'existing-jobs-cancel-delegates-same-owned-fence-and-settlement',
    } <= operations['jobs.preparations.cancel']
