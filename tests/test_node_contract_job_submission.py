"""Fixed repair wire boundaries; no runtime execution or authority is simulated."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
load = importlib.import_module("node_contract.load").load
render = importlib.import_module("node_contract.python").render


@pytest.fixture(scope='module')
def declaration(tmp_path_factory):
    meta, rows, digest = load(ROOT / 'contracts/node')
    path = tmp_path_factory.mktemp('job-codec') / 'codec.py'
    path.write_text(render(meta, rows, digest))
    spec = importlib.util.spec_from_file_location('_job_submission_codec', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    evidence = json.loads((ROOT / 'contracts/node/domain-evidence/job_submission.json').read_text())
    return module, rows, evidence


def test_fixed_repair_wire_vectors_refuse_execution_and_approval_injection(declaration):
    module, _, evidence = declaration
    for vector in evidence['shape_vectors']['accepted']:
        assert getattr(module, vector['type']).from_wire(vector['value']).to_wire() == vector['value']
    for vector in evidence['shape_vectors']['refused']:
        with pytest.raises(ValueError):
            getattr(module, vector['type']).from_wire(vector['value'])


def test_frozen_plan_stays_private_and_claimed_phase_is_not_authority(declaration):
    module, rows, _ = declaration
    assert not hasattr(module, 'JobFrozenRepairPlan')
    plan = next(row for row in rows if row['name'] == 'JobFrozenRepairPlan')
    assert plan['visibility'] == 'private'
    # Wire decoding accepts observations; it does not execute or validate a commit transition.
    claim = {'kind': 'verified_installed', 'value': {}}
    assert module.JobPreparationEffect.from_wire(claim).to_wire() == claim
