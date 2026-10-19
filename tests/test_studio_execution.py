"""Target-owned execution admission, replay and per-device process exclusion."""

import json
import sys
import time
from types import SimpleNamespace

import pytest

from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.studio_execution import DeviceExecutions, checked_node
from poolhouse.fleet.studio_references import validate_models
from poolhouse.studio.store import Conflict, digest


class Adapters:
    def validate(self, nodes):
        return []

    def prepare(self, node, inputs, context):
        out = context['output_dir']
        out.mkdir(parents=True, exist_ok=True)
        script = "import json,os,time;time.sleep(.15);open('result.json','w').write(json.dumps({'outputs':{'report':{'ok':True}}}))"
        return {'argv': [sys.executable, '-I', '-c', script], 'cwd': str(out), 'env': {}}

    def collect(self, node, job, context):
        return json.loads((context['output_dir'] / 'result.json').read_text())['outputs']


@pytest.fixture
def target(tmp_path):
    permissions = {'member': True, 'origin': True, 'queue': True}
    runner = JobRunner(tmp_path, slots=2, gate=lambda: (permissions['queue'], 'test'))
    ui = SimpleNamespace(root=tmp_path, runner=runner)
    service = DeviceExecutions(ui, lambda source, dispatch: permissions['member'],
                               lambda source, dispatch, fingerprint: permissions['origin'])
    service.adapters = Adapters()
    try:
        yield service, runner, permissions
    finally:
        runner.shutdown()
        service.close()


def request(service, identifier='a'):
    node = {'id': 'chart', 'type': 'chart', 'version': 1, 'parameters': {}}
    return {'dispatch_id': identifier * 64, 'spec_digest': digest({'request': identifier}),
            'run_id': 'c' * 32, 'node': node, 'inputs': {'metrics': {'loss': 1}}, 'resources': [],
            'implementation': service.validate([node])['implementations']['chart']}


def wait(service, source, payload):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        row = service.status(source, payload)
        if row['state'] in {'done', 'failed', 'interrupted', 'cancelled'}:
            return row
        time.sleep(.01)
    pytest.fail('Device execution did not settle')


def test_duplicate_dispatch_attaches_and_changed_content_conflicts(target):
    service, runner, _ = target
    payload = request(service)
    first = service.submit('source', payload)
    assert service.submit('source', payload)['dispatch_id'] == first['dispatch_id']
    changed = {**payload, 'inputs': {'metrics': {'loss': 2}}}
    with pytest.raises(Conflict):
        service.submit('source', changed)
    assert len(runner.jobs) == 1
    assert wait(service, 'source', payload)['state'] == 'done'
    assert service.result('source', payload)['outputs']['report'] == {'ok': True}


def test_other_source_cannot_read_cancel_or_attach(target):
    service, _, _ = target
    payload = request(service)
    service.submit('source', payload)
    with pytest.raises(KeyError):
        service.status('other-source', payload)
    with pytest.raises(KeyError):
        service.cancel('other-source', payload)
    wait(service, 'source', payload)


def test_revocation_between_queue_and_launch_starts_no_child(target):
    service, runner, permissions = target
    permissions['queue'] = False
    payload = request(service)
    service.submit('source', payload)
    permissions['origin'] = False
    permissions['queue'] = True
    row = wait(service, 'source', payload)
    assert row['state'] == 'interrupted'
    assert next(iter(runner.jobs.values())).pid is None
    with pytest.raises(PermissionError):
        service.submit('source', payload)


def test_actual_jobs_exclude_each_other_even_with_two_runner_slots(target):
    service, runner, _ = target
    first, second = request(service, 'a'), request(service, 'b')
    service.submit('source', first)
    service.submit('source', second)
    assert wait(service, 'source', first)['state'] == 'done'
    assert wait(service, 'source', second)['state'] == 'done'
    jobs = sorted(runner.jobs.values(), key=lambda row: row.started_at)
    assert jobs[1].started_at >= jobs[0].finished_at


def test_missing_dispatch_is_interrupted_without_any_replay(target):
    service, runner, _ = target
    payload = request(service)
    service.submit('source', payload)
    wait(service, 'source', payload)
    runner.jobs.clear()
    assert service.status('source', payload)['state'] == 'interrupted'
    assert service.submit('source', payload)['state'] == 'interrupted'
    assert runner.jobs == {}


@pytest.mark.parametrize('field', ['argv', 'env', 'cwd', 'module', 'url'])
def test_node_never_accepts_executable_fields(field):
    with pytest.raises(ValueError):
        checked_node({'id': 'example', 'type': 'chart', 'version': 1, field: 'untrusted'})


def test_raw_resource_paths_and_changed_target_implementation_are_refused(target):
    service, runner, _ = target
    payload = request(service)
    payload['inputs'] = {'metrics': {'kind': 'model', 'path': '/private/item'}}
    with pytest.raises(ValueError, match='staged'):
        service.submit('source', payload)
    payload = request(service)
    payload['implementation'] = 'changed'
    with pytest.raises(ValueError, match='runtime changed'):
        service.submit('source', payload)
    assert runner.jobs == {}


def test_staged_model_digest_does_not_authorize_serialized_target_paths(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'state.json').write_text(json.dumps({'config': {'base': '/private/target-model'}}))
    manifests = [{'id': 'a' * 64, 'shape': 'directory', 'files': [{'name': 'state.json'}]}]
    handles = {'model': {'kind': 'model', 'resource': 'a' * 64}}
    hydrated = {'model': {'kind': 'model', 'path': str(model)}}
    with pytest.raises(ValueError, match='staged base'):
        validate_models(handles, manifests, hydrated)
    (model / 'state.json').write_text(json.dumps({'config': {'base': 'https://invalid.example/model'}}))
    with pytest.raises(ValueError, match='staged base'):
        validate_models(handles, manifests, hydrated)


@pytest.mark.parametrize("configuration", [{}, {"base": ""}])
def test_checkpoint_cannot_fall_back_to_target_cached_base(tmp_path, configuration):
    model = tmp_path / "model"
    model.mkdir()
    (model / "state.json").write_text(json.dumps({"config": configuration}))
    manifests = [{"id": "a" * 64, "shape": "directory", "files": [{"name": "state.json"}]}]
    with pytest.raises(ValueError, match="staged base"):
        validate_models({"model": {"kind": "model", "resource": "a" * 64}}, manifests,
                        {"model": {"path": str(model)}})


@pytest.mark.parametrize("filename", ["/private/target-weights.safetensors", "../target.safetensors", "missing.safetensors"])
def test_sharded_model_references_only_declared_staged_files(tmp_path, filename):
    model = tmp_path / "model"
    model.mkdir()
    index = "model.safetensors.index.json"
    (model / index).write_text(json.dumps({"weight_map": {"weight": filename}}))
    manifests = [{"id": "a" * 64, "shape": "directory", "files": [{"name": index}]}]
    with pytest.raises(ValueError, match="staged base"):
        validate_models({"model": {"kind": "model", "resource": "a" * 64}}, manifests,
                        {"model": {"path": str(model)}})
