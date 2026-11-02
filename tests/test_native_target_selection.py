"""Collected native contract cases prepare only their selected Cargo targets."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = 'tests/test_node_contract_native.py'
LIBRARY_CASES = (
    'test_private_contract_vectors_stay_inside_the_node_crate',
    'test_reference_transcript_signature_math_has_no_issuer',
)
COLLECT = '''
import json,sys
from pathlib import Path
from types import SimpleNamespace
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'tests'),str(root/'scripts')]
import pytest,native_cargo
class Record:
    def pytest_collection_modifyitems(self,session,config,items):
        prepared=[]
        request=SimpleNamespace(module=items[0].module,session=session)
        units=SimpleNamespace(prepare=lambda targets:prepared.append(targets))
        native_cargo.native_targets_built.__wrapped__(units,request)
        selected=[{'name':item.originalname,'target':getattr(getattr(item,'callspec',None),'params',{}).get('target')} for item in items]
        print('TARGET_PLAN:'+json.dumps({'prepared':prepared,'selected':selected}))
raise SystemExit(pytest.main(['--collect-only','--noconftest','-q','-p','no:cacheprovider',*sys.argv[2:]],plugins=[Record()]))
'''


def target_plan(*selectors):
    environment = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': '', 'PYTEST_PLUGINS': ''}
    done = subprocess.run([sys.executable, '-c', COLLECT, str(ROOT), *selectors], cwd=ROOT,
                          env=environment, capture_output=True, text=True, check=False, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    records = [line.removeprefix('TARGET_PLAN:') for line in done.stdout.splitlines() if line.startswith('TARGET_PLAN:')]
    assert len(records) == 1, done.stdout
    return json.loads(records[0])


@pytest.mark.parametrize('name', LIBRARY_CASES)
def test_library_only_selector_prepares_no_integration_executable(name):
    plan = target_plan(MODULE + '::' + name)
    assert plan['prepared'] == [['lib']]
    assert plan['selected'] == [{'name': name, 'target': 'lib'}]


def test_full_module_preserves_every_case_and_batches_requested_targets_once():
    plan = target_plan(MODULE)
    assert plan['prepared'] == [['contract', 'lib']]
    assert {row['name']: row['target'] for row in plan['selected']} == {
        'test_native_contract_vectors': 'contract',
        'test_private_contract_vectors_stay_inside_the_node_crate': 'lib',
        'test_rust_consumer_can_use_public_contract_but_not_private_data': 'contract',
        'test_reference_transcript_signature_math_has_no_issuer': 'lib',
    }
