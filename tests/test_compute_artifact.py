"""Optional compute artifacts remain bound to the minimal app revision."""
import hashlib
import zipfile

import pytest

from poolhouse.fleet import compute_artifact as artifact
from poolhouse.fleet.wheel_provenance import write_wheel

REVISION = 'a' * 40


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / 'source'
    for name in ('train', 'bench', 'gym', 'world'):
        package = source / 'src/poolhouse' / name
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('VALUE = 42\n')
    base = tmp_path / 'poolhouse-0.1.0-py3-none-any.whl'
    write_wheel(base, {'poolhouse/__init__.py': b'', 'poolhouse-0.1.0.dist-info/METADATA':
                      b'Name: poolhouse\nVersion: 0.1.0\n', 'poolhouse-0.1.0.dist-info/WHEEL':
                      b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
                      'poolhouse-0.1.0.dist-info/RECORD': b''})
    companion = artifact.build(source, base, REVISION)
    return source, base, companion, artifact.from_wheel(base)


def test_bundle_has_distinct_ownership_and_verified_inventory(bundle, tmp_path):
    _, base, companion, manifest = bundle
    assert not set(artifact.contents(base)) & set(artifact.contents(companion))
    artifact.verify(companion, manifest, REVISION)
    target = tmp_path / 'cached' / base.name
    artifact.cache(base, target, REVISION)
    assert target.with_name(companion.name).read_bytes() == companion.read_bytes()
    assert manifest['source'] == REVISION


def test_revision_and_payload_substitution_are_refused(bundle):
    _, _, companion, manifest = bundle
    with pytest.raises(ValueError, match='revision'):
        artifact.verify(companion, manifest, 'b' * 40)
    companion.write_bytes(companion.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='integrity'):
        artifact.verify(companion, manifest, REVISION)


@pytest.mark.parametrize('name', ['../escape.py', '/escape.py', 'poolhouse/fleet/daemon.py', 'poolhouse_studio_compute-0.1.0.dist-info/entry_points.txt'])
def test_unsafe_and_unexpected_files_are_refused(bundle, name):
    _, _, companion, manifest = bundle
    files = artifact.contents(companion)
    files[name] = b'changed'
    write_wheel(companion, files)
    manifest['sha256'] = hashlib.sha256(companion.read_bytes()).hexdigest()
    manifest['files'][name] = hashlib.sha256(b'changed').hexdigest()
    with pytest.raises(ValueError):
        artifact.verify(companion, manifest, REVISION)


def test_symlink_companion_and_source_refused(bundle, tmp_path):
    source, base, companion, manifest = bundle
    link = tmp_path / 'linked' / companion.name
    link.parent.mkdir()
    link.symlink_to(companion)
    with pytest.raises(ValueError, match='regular'):
        artifact.verify(link, manifest, REVISION)
    (source / 'src/poolhouse/train/escape.py').symlink_to(base)
    with pytest.raises(ValueError, match='symbolic'):
        artifact.build(source, base, REVISION)


def test_duplicate_archive_names_refused(bundle):
    _, _, companion, manifest = bundle
    with zipfile.ZipFile(companion, 'a') as archive, pytest.warns(UserWarning):
        archive.writestr('poolhouse/train/__init__.py', b'changed')
    manifest['sha256'] = hashlib.sha256(companion.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='duplicate'):
        artifact.verify(companion, manifest, REVISION)


def test_managed_isolated_probe_checks_installed_bytes(bundle, tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from poolhouse.fleet.wheel_provenance import stamp

    _, base, companion, manifest = bundle
    stamp(base, REVISION, tmp_path)
    prefix = tmp_path / 'managed'
    subprocess.run([sys.executable, '-m', 'venv', str(prefix)], check=True, capture_output=True)
    python = prefix / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-index', '--no-compile', str(base), str(companion)],
                   check=True, capture_output=True)
    monkeypatch.setattr(artifact, 'expected', lambda: manifest)
    assert artifact.probe(python, environment=dict(os.environ)) == manifest['sha256']
    located = subprocess.run([str(python), '-B', '-I', '-c', "import poolhouse.train;print(poolhouse.train.__file__)"],
                             check=True, text=True, capture_output=True)
    installed = __import__('pathlib').Path(located.stdout.strip())
    installed.write_text('changed')
    with pytest.raises(OSError, match='verified compute'):
        artifact.probe(python, environment=dict(os.environ))
