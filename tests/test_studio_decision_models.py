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
    head_repo, base_repo = models.STRANDS, 'sample/base'
    revision, base_revision = models.STRANDS_REV, 'a' * 40
    contents = {'hobson_config.json': json.dumps({'head_type': 'pointer', 'pointer_dim': 4}).encode(),
                'head.safetensors': b'head', 'lora/adapter_config.json': json.dumps(adapter()).encode(),
                'lora/adapter_model.safetensors': b'adapter', 'tokenizer.json': b'{}',
                'config.json': b'{"model_type":"sample"}', 'model.safetensors': b'base'}
    pins = []
    monkeypatch.setattr(models.hfstore, 'repo_root', lambda repo: tmp_path / repo.replace('/', '--'))
    for name, content in contents.items():
        repo, rev = (base_repo, base_revision) if name in ('config.json', 'model.safetensors') else (head_repo, revision)
        pin = Pin(repo, rev, name, hashlib.sha256(content).hexdigest(), len(content))
        pins.append(pin)
        path = models.hfstore.repo_root(repo) / 'snapshots' / rev / name
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
