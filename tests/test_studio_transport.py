"""Fixed sealed member actions and digest-checked resource round trips."""
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from poolhouse import macauth, sealing
from poolhouse.fleet import studio_transport as transport
from poolhouse.fleet.studio_transfer import CHUNK_BYTES, TransferStore
from poolhouse.http import Sealed, ServerError
from poolhouse.studio import resources as model_resources
from poolhouse.studio.catalog import node_spec

DISPATCH = 'a' * 64
SOURCE = 'enrolled-source'


class Response:
    def __init__(self, data, *, sealed=True):
        self.status = 200
        self.headers = {sealing.HEADER: '1'} if sealed else {}
        self.sealed = Sealed(b'x' * 32, 'test-nonce')
        self.data = sealing.seal(self.sealed.key, data, sealing.response_data(self.sealed.nonce, 200))
        self.read_sizes = []
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, size):
        self.read_sizes.append(size)
        return self.data[:size]


@pytest.fixture
def device(tmp_path, monkeypatch):
    monkeypatch.setattr(transport.tls, 'pinned_context', lambda cert: ('pinned', cert))
    monkeypatch.setattr(transport.http, 'pin', lambda host, context: None)
    member = SimpleNamespace(base_url='https://127.0.0.1:12345', token=macauth.PREFIX + 'a' * 64, timeout=5,
                             beacon=SimpleNamespace(cert='enrolled-cert', machine='target', host='127.0.0.1', hostname='target', port=12345))
    live = {'allowed': True}
    store = TransferStore(tmp_path / 'target', max_bytes=10 * CHUNK_BYTES,
                          authorize=lambda source, dispatch: source == SOURCE and live['allowed'])
    calls = []
    def request(url, *, method, data, options):
        assert options.headers[sealing.HEADER] == '2'
        assert options.error_body_limit <= transport.MAX_REPLY + sealing.NONCE_BYTES + 16
        assert options.retry.tries == 1
        options.guard(url)
        parts = urlsplit(url)
        calls.append((method, parts.path))
        if parts.path.startswith('/studio/v1/executions/'):
            return Response(json.dumps({'accepted': True}).encode())
        dispatch, resource, action = parts.path.split('/')[4:]
        query = parse_qs(parts.query)
        if action == 'begin':
            result = store.begin(SOURCE, dispatch, json.loads(data))
        elif action == 'finish':
            result = store.finish(SOURCE, dispatch, resource)
        elif action == 'file':
            name, offset = query['name'][0], int(query['offset'][0])
            if method == 'PUT':
                result = store.put(SOURCE, dispatch, resource, name=name, offset=offset, data=data)
            else:
                return Response(store.read(SOURCE, dispatch, resource, name=name, offset=offset))
        else:
            raise AssertionError(action)
        return Response(json.dumps(result).encode())
    monkeypatch.setattr(transport, 'open_stream', request)
    return transport.MemberExecution(member, lambda: live['allowed']), store, live, calls


def test_fixed_sealed_request_no_fallback_actions(device):
    client, _, _, calls = device
    assert client.request('validate', {'dispatch_id': DISPATCH}) == {'accepted': True}
    assert calls == [('POST', '/studio/v1/executions/validate')]
    with pytest.raises(ValueError, match='fixed'):
        client.request('execute-arbitrary-command', {'argv': ['bad']})
    assert len(calls) == 1
    assert client.request("resume", {"dispatch_id": DISPATCH}) == {"accepted": True}
    assert calls[-1] == ("POST", "/studio/v1/executions/resume")


def test_explicit_multichunk_stage_collect_and_duplicate_submission(device, tmp_path):
    client, target, _, calls = device
    source = tmp_path / 'source'
    source.mkdir()
    expected = b'data' * (CHUNK_BYTES // 2)
    (source / 'rows.jsonl').write_bytes(expected)
    spec = node_spec('data.validate')
    staged = client.stage(DISPATCH, {'dataset': {'kind': 'dataset', 'path': str(source), 'rows': 2}}, spec)
    assert client.stage(DISPATCH, {'dataset': {'kind': 'dataset', 'path': str(source), 'rows': 2}}, spec) == staged
    assert str(source) not in json.dumps(staged)
    hydrated = target.inputs(SOURCE, DISPATCH, staged['inputs'])
    assert (Path(hydrated['dataset']['path']) / 'rows.jsonl').read_bytes() == expected
    output = target.directory(SOURCE, DISPATCH) / 'validated'
    output.mkdir()
    (output / 'rows.jsonl').write_bytes(expected)
    reply = target.result(SOURCE, DISPATCH, {'dataset': {'kind': 'dataset', 'path': str(output), 'rows': 2}, 'report': {'valid': True}}, spec)
    assert str(output) not in json.dumps(reply)
    reply['dispatch_id'] = DISPATCH
    collected = client.collect(reply, tmp_path / 'origin-output', spec, dispatch_id=DISPATCH)
    assert (Path(collected['dataset']['path']) / 'rows.jsonl').read_bytes() == expected
    assert Path(collected['dataset']['path']).is_relative_to(tmp_path / 'origin-output')
    assert collected['report'] == {'valid': True}
    assert any(method == 'PUT' for method, _ in calls)
    assert all('/studio/v1/resources/' in path for _, path in calls)


def test_revocation_is_checked_before_each_request(device):
    client, _, live, calls = device
    live['allowed'] = False
    with pytest.raises(PermissionError):
        client.request('status', {'dispatch_id': DISPATCH})
    assert not calls


@pytest.mark.parametrize('behavior', ['plaintext', 'oversized', 'redirect'])
def test_plaintext_oversized_redirect_and_error_responses_are_refused(device, monkeypatch, behavior):
    client, _, _, _ = device
    seen = []
    def request(url, *, options, **_):
        seen.append(options)
        if behavior == 'redirect':
            options.guard('https://unrelated.invalid/studio/v1/executions/status')
        if behavior == 'error':
            raise ServerError('remote private details', status=500)
        return Response(b'x' * (transport.MAX_REPLY + 1) if behavior == 'oversized' else b'{}', sealed=behavior != 'plaintext')
    monkeypatch.setattr(transport, 'open_stream', request)
    with pytest.raises(ValueError) as error:
        client.request('status', {'dispatch_id': DISPATCH})
    assert 'remote private details' not in str(error.value)
    assert seen[0].error_body_limit is not None


def test_only_discovered_https_enrolled_member_is_accepted(device):
    client, _, _, _ = device
    client.member.base_url = 'http://127.0.0.1:12345'
    with pytest.raises(ValueError, match='enrolled'):
        transport.MemberExecution(client.member, lambda: True)


def test_tampered_output_resource_is_not_activated(device, tmp_path, monkeypatch):
    client, store, _, _ = device
    output = store.directory(SOURCE, DISPATCH) / 'report.json'
    output.write_bytes(b'good')
    spec = node_spec('report')
    reply = store.result(SOURCE, DISPATCH, {'artifact': {'kind': 'artifact', 'path': str(output)}}, spec)
    reply['dispatch_id'] = DISPATCH
    original = client._wire
    def changed(method, path, raw, limit):
        if method == 'GET':
            return b'evil'
        return original(method, path, raw, limit)
    monkeypatch.setattr(client, '_wire', changed)
    with pytest.raises(ValueError, match='digest'):
        client.collect(reply, tmp_path / 'origin', spec, dispatch_id=DISPATCH)
    assert not any(path.name == 'files' for path in (tmp_path / 'origin').rglob('*'))


def test_selected_device_identity_cannot_change_after_binding(device):
    client, _, _, calls = device
    client.member.beacon.machine = 'different-device'
    with pytest.raises(PermissionError, match='identity'):
        client.request('status', {'dispatch_id': DISPATCH})
    assert not calls


def test_response_must_be_bound_to_its_sealed_request(device, monkeypatch):
    client, _, _, _ = device
    response = Response(b'{}')
    response.sealed = Sealed()
    monkeypatch.setattr(transport, 'open_stream', lambda *args, **kwargs: response)
    with pytest.raises(ValueError, match='binding'):
        client.request('status', {'dispatch_id': DISPATCH})


def test_equal_content_output_ports_share_one_declared_transfer(device, tmp_path):
    client, store, _, _ = device
    root = store.directory(SOURCE, DISPATCH)
    outputs = {}
    for name in ('train', 'test'):
        directory = root / name
        directory.mkdir()
        (directory / 'data.jsonl').write_bytes(b'same data')
        outputs[name] = {'kind': 'dataset', 'path': str(directory)}
    spec = node_spec('data.split')
    reply = store.result(SOURCE, DISPATCH, outputs, spec)
    reply['dispatch_id'] = DISPATCH
    assert len(reply['resources']) == 1
    result = client.collect(reply, tmp_path / 'origin-output', spec, dispatch_id=DISPATCH)
    assert result['train']['path'] == result['test']['path']
    assert (Path(result['train']['path']) / 'data.jsonl').read_bytes() == b'same data'


@pytest.mark.parametrize('status', [0, 500, 503])
def test_transport_uncertainty_is_typed_for_dispatch_reconciliation(device, monkeypatch, status):
    client, _, _, _ = device
    def failed(*args, **kwargs):
        raise ServerError('remote private detail', status=status)
    monkeypatch.setattr(transport, 'open_stream', failed)
    with pytest.raises(transport.MemberUncertain, match='reconcile') as error:
        client.request('submit', {'dispatch_id': DISPATCH})
    assert 'remote private detail' not in str(error.value)


def test_collect_rejects_another_dispatch_before_fetching(device, tmp_path):
    client, _, _, calls = device
    reply = {'dispatch_id': 'b' * 64, 'resources': [], 'outputs': {}}
    with pytest.raises(ValueError, match='another'):
        client.collect(reply, tmp_path / 'out', node_spec('report'), dispatch_id=DISPATCH)
    assert not calls


def test_native_library_cache_links_stage_only_verified_repository_blobs(device, tmp_path, monkeypatch):
    client, store, _, _ = device
    repository = tmp_path / "repository"
    snapshot = repository / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    blobs = repository / "blobs"
    blobs.mkdir()
    (blobs / "weights").write_bytes(b"approved model bytes")
    (snapshot / "model.safetensors").symlink_to("../../blobs/weights")
    (snapshot / "config.json").write_text('{"model_type":"example"}')
    handle = {"kind": "model", "id": "hf:example/model", "path": str(snapshot), "repo": "example/model"}
    monkeypatch.setattr(model_resources, "model_resource", lambda selected: handle if selected == handle else None)
    monkeypatch.setattr(model_resources.hfstore, "repo_root", lambda repo: repository)
    staged = client.stage(DISPATCH, {"model": handle}, node_spec("generate"))
    local = store.inputs(SOURCE, DISPATCH, staged["inputs"])["model"]
    assert (Path(local["path"]) / "model.safetensors").read_bytes() == b"approved model bytes"
    assert not (Path(local["path"]) / "model.safetensors").is_symlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    (snapshot / "model.safetensors").unlink()
    (snapshot / "model.safetensors").symlink_to(outside)
    with pytest.raises(ValueError, match="repository blob"):
        client.stage(DISPATCH, {"model": handle}, node_spec("generate"))


def test_checkpoint_base_is_separate_finalized_same_dispatch_resource(device, tmp_path):
    client, store, _, _ = device
    model, base = tmp_path / "checkpoint", tmp_path / "base"
    model.mkdir()
    base.mkdir()
    (model / "model.safetensors").write_bytes(b"adapter")
    (base / "model.safetensors").write_bytes(b"frozen weights")
    handle = {"kind": "model", "path": str(model), "base_digest": "c" * 64,
              "base": {"kind": "model", "path": str(base)}}
    staged = client.stage(DISPATCH, {"model": handle}, node_spec("generate"))
    assert len(staged["resources"]) == 2
    safe = staged["inputs"]["model"]
    assert "path" not in safe and "base" not in safe
    assert safe["base_resource"] != safe["resource"]
    hydrated = store.inputs(SOURCE, DISPATCH, staged["inputs"])["model"]
    assert hydrated["base"]["digest"] == "c" * 64
    assert (Path(hydrated["base"]["path"]) / "model.safetensors").read_bytes() == b"frozen weights"
    with pytest.raises((FileNotFoundError, ValueError)):
        store.inputs(SOURCE, "d" * 64, staged["inputs"])
    safe["base_resource"] = "e" * 64
    with pytest.raises((FileNotFoundError, ValueError)):
        store.inputs(SOURCE, DISPATCH, staged["inputs"])


def test_model_result_carries_only_its_own_staged_base_dependency(device, tmp_path):
    client, store, _, _ = device
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'weights.safetensors').write_bytes(b'base bytes')
    staged = client.stage(DISPATCH, {'model': {'kind': 'model', 'path': str(base)}}, node_spec('generate'))
    hydrated = store.inputs(SOURCE, DISPATCH, staged['inputs'])['model']
    own = store.directory(SOURCE, DISPATCH)
    model = own / 'model'
    model.mkdir()
    (model / 'model.safetensors').write_bytes(b'adapter')
    value = {'kind': 'model', 'path': str(model), 'base': hydrated, 'base_digest': 'c' * 64}
    result = store.result(SOURCE, DISPATCH, {'model': value}, node_spec('train.tool-calls'))
    result['dispatch_id'] = DISPATCH
    outputs = client.collect(result, tmp_path / 'received', node_spec('train.tool-calls'), dispatch_id=DISPATCH)
    assert (Path(outputs['model']['base']['path']) / 'weights.safetensors').read_bytes() == b'base bytes'
    value['base'] = {'path': str(base)}
    with pytest.raises(ValueError, match="dispatch's explicitly staged"):
        store.result(SOURCE, DISPATCH, {'model': value}, node_spec('train.tool-calls'))
