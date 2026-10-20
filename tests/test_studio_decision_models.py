"""Only pinned assets or explicit producer lineage can construct a decision source."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.decide.fetch import Pin
from poolhouse.decide.pins import Checkpoint
from poolhouse.decide.pointer import PointerDecider
from poolhouse.studio import decision_inventory as inventory, decision_models as models
from poolhouse.studio.resources import base_fingerprint, model_fingerprint, model_resource


def adapter():
    return {'peft_type': 'LORA', 'task_type': 'FEATURE_EXTRACTION', 'r': 2,
            'lora_alpha': 4, 'lora_dropout': 0, 'target_modules': ['q_proj'],
            'base_model_name_or_path': '/private/forged', 'megatron_core': 'untrusted.code',
            'auto_mapping': {'parent_library': 'untrusted.code'}}


@pytest.fixture
def trained(tmp_path):
    base, root = tmp_path / 'base', tmp_path / 'trained'
    base.mkdir()
    (base / 'config.json').write_text('{"model_type":"sample"}')
    (base / 'model.safetensors').write_bytes(b'base')
    root.mkdir()
    (root / 'lora').mkdir()
    (root / 'head.safetensors').write_bytes(b'head')
    (root / 'lora/adapter_model.safetensors').write_bytes(b'adapter')
    (root / 'lora/adapter_config.json').write_text(json.dumps(adapter()))
    cfg = {'format': models.FORMAT, 'name': 'sample', 'base': {'path': '/private/forged'},
           'pointer_dim': 4, 'dtype': 'float32', 'temperature': 1,
           'sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in models.ASSETS}}
    (root / 'decider.json').write_text(json.dumps(cfg))
    handle = {'kind': 'model', 'path': str(root), 'decision': True,
              'base': {'kind': 'model', 'path': str(base)}}
    handle['base_digest'] = base_fingerprint(handle['base'])
    return handle, root, base


def test_trained_source_ignores_serialized_locators_and_uses_explicit_base(trained, tmp_path):
    model, _, base = trained
    source, identity = models.source(model, tmp_path / 'owned')
    assert source.base_dir == base and source.tokenizer_dir == base
    assert PointerDecider(source).resolve() is source
    sanitized = json.loads((source.lora_dir / 'adapter_config.json').read_text())
    assert not {'base_model_name_or_path', 'auto_mapping', 'megatron_core'} & set(sanitized)
    assert identity['base_digest'] == model['base_digest']
    assert identity['score_semantics'] == 'uncalibrated'
    assert models.source(model, None)[1] == identity


@pytest.mark.parametrize('attack', ['missing-base', 'changed-base', 'changed-head', 'head-link', 'head-hardlink', 'bad-dtype', 'bad-dimension'])
def test_trained_source_refuses_substitution_and_unsupported_metadata(trained, tmp_path, attack):
    model, root, base = trained
    if attack == 'missing-base':
        model.pop('base')
    elif attack == 'changed-base':
        (base / 'model.safetensors').write_bytes(b'evil')
    elif attack == 'changed-head':
        (root / 'head.safetensors').write_bytes(b'evil')
    elif attack in ('head-link', 'head-hardlink'):
        target = tmp_path / 'private'
        target.write_bytes(b'head')
        (root / 'head.safetensors').unlink()
        if attack == 'head-link':
            (root / 'head.safetensors').symlink_to(target)
        else:
            (root / 'head.safetensors').hardlink_to(target)
    else:
        cfg = json.loads((root / 'decider.json').read_text())
        cfg['dtype' if attack == 'bad-dtype' else 'pointer_dim'] = 'code' if attack == 'bad-dtype' else 0
        (root / 'decider.json').write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        models.source(model, tmp_path / 'owned')


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    head_repo, base_repo = inventory.STRANDS, 'sample/base'
    revision, base_revision = inventory.STRANDS_REV, 'a' * 40
    contents = {'hobson_config.json': json.dumps({'head_type': 'pointer', 'pointer_dim': 4}).encode(),
                'head.safetensors': b'head', 'lora/adapter_config.json': json.dumps(adapter()).encode(),
                'lora/adapter_model.safetensors': b'adapter', 'tokenizer.json': b'{}',
                'config.json': b'{"model_type":"sample"}', 'model.safetensors': b'base'}
    pins = []
    monkeypatch.setattr(inventory.hfstore, 'repo_root', lambda repo: tmp_path / repo.replace('/', '--'))
    for name, content in contents.items():
        repo, rev = (base_repo, base_revision) if name in ('config.json', 'model.safetensors') else (head_repo, revision)
        pin = Pin(repo, rev, name, hashlib.sha256(content).hexdigest(), len(content))
        pins.append(pin)
        path = inventory.hfstore.repo_root(repo) / 'snapshots' / rev / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    checkpoint = Checkpoint('sample', tuple(pins), 'test', base_repo)
    monkeypatch.setattr(models, 'STRANDS_V19', checkpoint)
    monkeypatch.setattr(inventory, 'STRANDS_V19', checkpoint)
    return tmp_path


def test_explicit_fixed_inventory_includes_verified_base_and_no_hidden_lookup(pinned, tmp_path, monkeypatch):
    monkeypatch.setattr('poolhouse.hub.discover', lambda: pytest.fail('fixed inventory must not search arbitrary model paths'))
    assert inventory.inventory()[0]['available']
    handle = model_resource({'kind': 'model', 'id': models.RESOURCE_ID})
    members = inventory.members(handle)
    assert any(member['relative'].startswith('base/') for member in members)
    first = model_fingerprint(handle)
    resolved, _ = models.source(handle, tmp_path / 'owned')
    assert resolved.head.is_file()
    (resolved.base_dir / 'model.safetensors').write_bytes(b'evil')
    with pytest.raises(ValueError, match='content digest'):
        model_fingerprint(handle)
    assert not inventory.inventory()[0]['available']
    assert first


def test_missing_fixed_asset_visible_as_unavailable_without_download(pinned):
    value = inventory.resource()
    member = inventory.members(value)[0]
    Path(member['path']).unlink()
    assert not inventory.inventory()[0]['available']
    with pytest.raises(OSError):
        inventory.resource()
    with pytest.raises(ValueError, match='explicit installed'):
        inventory.members({**value, 'id': 'decision:untrusted', 'path': '/private/forged'})


def test_broken_runtime_destination_link_never_writes_outside(trained, tmp_path):
    model, _, _ = trained
    out = tmp_path / 'owned'
    directory = out / 'decision-adapter'
    directory.mkdir(parents=True)
    private = tmp_path / 'outside'
    (directory / 'adapter_model.safetensors').symlink_to(private)
    with pytest.raises(ValueError, match='owned regular'):
        models.source(model, out)
    assert not private.exists()


def test_source_substitution_during_materialization_is_refused(trained, tmp_path, monkeypatch):
    model, _, _ = trained
    private = tmp_path / 'private'
    private.write_bytes(b'private')
    original = models._copy
    def replaced(source, target, expected):
        if source.name == 'adapter_model.safetensors':
            source.unlink()
            source.symlink_to(private)
        return original(source, target, expected)
    monkeypatch.setattr(models, '_copy', replaced)
    with pytest.raises(ValueError, match='symbolic links'):
        models.source(model, tmp_path / 'owned')
    assert private.read_bytes() == b'private'


def test_fixed_fingerprint_reuses_only_current_descriptor_identities(pinned, monkeypatch):
    from poolhouse.studio import model_content
    model = inventory.resource()
    expected = model_fingerprint(model)
    opened = []
    original = model_content.open_model_file
    def tracked(member):
        opened.append(member['path'])
        return original(member)
    monkeypatch.setattr(model_content, 'open_model_file', tracked)
    assert model_fingerprint(model) == expected
    assert not opened
    base = next(item for item in inventory.members(model) if item['relative'] == 'base/model.safetensors')
    Path(base['path']).write_bytes(b'evil')
    with pytest.raises(ValueError, match='content digest'):
        model_fingerprint(model)
    assert opened


def test_finite_worker_uses_verified_source_and_broker_hold(trained, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from poolhouse.studio import worker
    model, _, _ = trained
    calls = []
    @contextmanager
    def held(purpose, *, wait_s):
        assert wait_s is None
        calls.append('held')
        yield
        calls.append('released')
    def pointer(source, *, download):
        assert not download and source.base_dir == Path(model['base']['path'])
        calls.append('model')
        def choose(question, state, options):
            assert calls[-1] == 'model'
            return {'scores': {'accept': .95, 'review': .05}}
        return SimpleNamespace(decide=choose)
    monkeypatch.setattr(worker.gpu, 'hold', held)
    monkeypatch.setattr(worker, 'PointerDecider', pointer)
    result = worker.execute({'type': 'decision.predict'}, {'record': {'amount': 1}, 'model': model}, tmp_path / 'output')
    assert result['decision']['choice'] == 'accept'
    assert calls == ['held', 'model', 'released']


def test_fixed_decision_bundle_round_trip_uses_only_finalized_dispatch_assets(pinned, tmp_path, monkeypatch):
    from poolhouse.fleet.studio_references import validate_models
    from poolhouse.fleet.studio_transfer import TransferStore, resource_handle
    from poolhouse.fleet.studio_transport import _input_manifest
    from poolhouse.studio.resources import open_model_file

    scope, dispatch = 'enrolled-source', 'a' * 64
    live = {'allowed': True}
    model = inventory.resource()
    manifest, members = _input_manifest(model)
    store = TransferStore(tmp_path / 'target', max_bytes=1024 * 1024,
                          authorize=lambda source, run: live['allowed'] and source == scope and run == dispatch)
    store.begin(scope, dispatch, manifest)
    for name, member in members.items():
        with open_model_file(member) as stream:
            store.put(scope, dispatch, manifest['id'], name=name, offset=0, data=stream.read())
    assert store.finish(scope, dispatch, manifest['id']) == manifest
    wire = {'model': resource_handle(model, manifest)}
    hydrated = store.inputs(scope, dispatch, wire)
    assert 'fixed_source' not in hydrated['model'] and 'id' not in hydrated['model']
    validate_models(wire, [manifest], hydrated)
    staged = hydrated['model']
    assert staged['fixed_source'] == 'staged-strands-v19' and 'id' not in staged
    monkeypatch.setattr(inventory.hfstore, 'repo_root', lambda _: pytest.fail('target must not consult its native model cache'))
    resolved, identity = models.source(staged, tmp_path / 'runtime')
    assert resolved.base_dir == Path(staged['path']) / 'base'
    assert resolved.tokenizer_dir == Path(staged['path']) / 'head'
    assert identity['calibration_digest']
    live['allowed'] = False
    with pytest.raises(PermissionError):
        store.inputs(scope, dispatch, wire)


@pytest.mark.parametrize('attack', ['missing-pin', 'extra-pin', 'wrong-digest', 'wrong-size', 'extra-file', 'link'])
def test_fixed_staged_descriptor_requires_exact_compiled_bytes(pinned, tmp_path, attack):
    files = inventory._expected_files()
    staged = tmp_path / 'staged'
    staged.mkdir()
    original = {item['relative']: item for item in inventory.members(inventory.resource())}
    for item in files:
        target = staged / item['name']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(original[item['name']]['path']).read_bytes())
    if attack == 'missing-pin':
        files.pop()
    elif attack == 'extra-pin':
        files.append({'name': 'extra', 'size': 0, 'sha256': 'a' * 64})
    elif attack == 'wrong-digest':
        files[0] = {**files[0], 'sha256': 'b' * 64}
    elif attack == 'wrong-size':
        files[0] = {**files[0], 'size': files[0]['size'] + 1}
    elif attack == 'extra-file':
        (staged / 'extra').write_bytes(b'extra')
    else:
        target = staged / files[0]['name']
        target.unlink()
        target.symlink_to(tmp_path / 'private')
    with pytest.raises(ValueError):
        inventory.staged_resource(staged, files)
