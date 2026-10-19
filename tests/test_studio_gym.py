"""Gym policies retain native checkpoint trust when copied into Studio."""
import json

import pytest

from poolhouse.gym import training


def setup_policy(tmp_path, monkeypatch):
    cache = tmp_path / 'gym'
    native_dir = cache / 'training-one'
    native_dir.mkdir(parents=True)
    native = native_dir / 'policy.zip'
    native.write_bytes(b'trusted policy bytes')
    (native_dir / 'manifest.json').write_text(json.dumps({'checkpoint': str(native), 'environment': 'car', 'config': {'seed': 3}}))
    owned = tmp_path / 'studio'
    owned.mkdir()
    copied = owned / 'policy.zip'
    copied.write_bytes(native.read_bytes())
    monkeypatch.setattr(training, 'artifact_root', lambda: cache)
    return native, copied, owned


def test_owned_copy_resolves_native_manifest(tmp_path, monkeypatch):
    native, copied, root = setup_policy(tmp_path, monkeypatch)
    (root / 'manifest.json').write_text(json.dumps({'environment': 'malicious', 'checkpoint': '/elsewhere'}))
    source, manifest = training.matching_policy(copied, root)
    assert source == native
    assert manifest['environment'] == 'car'


@pytest.mark.parametrize('change', ['copy', 'native', 'missing', 'manifest'])
def test_changed_or_absent_original_refused(tmp_path, monkeypatch, change):
    native, copied, root = setup_policy(tmp_path, monkeypatch)
    if change == 'copy':
        copied.write_bytes(b'changed')
    elif change == 'native':
        native.write_bytes(b'changed')
    elif change == 'manifest':
        (native.parent / 'manifest.json').write_text('{}')
    else:
        native.unlink()
    with pytest.raises(ValueError, match='trusted original'):
        training.matching_policy(copied, root)


@pytest.mark.parametrize('which', ['copy', 'native'])
def test_policy_symlinks_refused(tmp_path, monkeypatch, which):
    native, copied, root = setup_policy(tmp_path, monkeypatch)
    target = copied if which == 'copy' else native
    saved = target.with_suffix('.saved')
    target.rename(saved)
    target.symlink_to(saved)
    with pytest.raises(ValueError):
        training.matching_policy(copied, root)
