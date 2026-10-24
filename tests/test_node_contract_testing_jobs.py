"""Managed-test selector/shape boundaries only; no collection or test workload runs."""
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
    path = tmp_path_factory.mktemp('testing-contract-codec') / 'codec.py'
    path.write_text(render(meta, rows, digest))
    spec = importlib.util.spec_from_file_location('_managed_testing_codec', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    evidence = json.loads((ROOT / 'contracts/node/domain-evidence/testing_jobs.json').read_text())
    return module, meta, rows, evidence


def test_plain_exact_selector_accepts_parameter_punctuation_but_not_commands(proposal):
    module, _, _, evidence = proposal
    for vector in evidence['shape_vectors']['accepted']:
        assert getattr(module, vector['type']).from_wire(vector['value']).to_wire() == vector['value']
    for vector in evidence['shape_vectors']['refused']:
        with pytest.raises(ValueError):
            getattr(module, vector['type']).from_wire(vector['value'])
    assert module.TestingExactSelector('tests/test_unit.py::test_item[a*b]').value.endswith('[a*b]')


def test_managed_data_does_not_export_plans_or_grant_source_execution(proposal):
    module, meta, rows, _ = proposal
    for name in ('JobFrozenTestingPlan', 'JobFrozenTestingCollectionPlan', 'JobTestingPlanStages'):
        assert not hasattr(module, name)
        assert next(row for row in rows if row['name'] == name)['visibility'] == 'private'
    assert not hasattr(module, 'TestExecutionPermit')
    assert module.TestingReportedOutcome.from_wire({'kind': 'passed', 'value': {}})
    operations = {row['name']: row for row in meta['operations']}
    assert not any(name.startswith('testing.') for name in operations)
    assert {
        'testing-admission-and-execution-permission-before-collection-code',
        'testing-collection-fence-and-unknown-custody-before-any-project-code',
        'testing-resolve-one-unique-catalog-unit-and-freeze-grouped-ids-once',
    } <= set(operations['jobs.prepare']['authorization'])
    assert 'testing-separate-collection-run-and-output-publication-fences' in operations['jobs.submit']['authorization']


def test_precollection_plan_freezes_hang_and_output_limits_before_discovery(proposal):
    _, _, rows, _ = proposal
    fields = {field['name']: field['type'] for field in next(
        row for row in rows if row['name'] == 'JobFrozenTestingCollectionPlan')['fields']}
    assert fields['collection_timeout_seconds'] == 'FiniteF64'
    assert fields['collection_output_bytes'] == 'U64'
    assert fields['resource_profile_digest'] == 'JobPreparationDigest'
    assert fields['source'] == 'TestingSourceIdentity'
    assert 'unit' not in fields
