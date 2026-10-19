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


def test_companion_build_is_reproducible(bundle):
    source, base, companion, manifest = bundle
    first = companion.read_bytes()
    artifact.build(source, base, REVISION)
    assert companion.read_bytes() == first
    assert artifact.from_wheel(base)['sha256'] == manifest['sha256']


@pytest.mark.parametrize('returncode,stdout', [(1, ''), (0, 'forged-digest')])
def test_probe_uses_fixed_isolated_argv_and_refuses_hostile_result(bundle, tmp_path, monkeypatch, returncode, stdout):
    import json
    import subprocess

    _, _, _, manifest = bundle
    interpreter = tmp_path / 'python; touch injected'
    secret = 'invented-private-token'
    environment = {'PATH': '/untrusted', 'POOLHOUSE_TOKEN': secret,
                   'PYTHONPATH': '/untrusted', 'PYTHONOPTIMIZE': '2'}
    monkeypatch.setattr(artifact, 'expected', lambda: manifest)
    calls = []

    def hostile(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=secret)

    monkeypatch.setattr(subprocess, 'run', hostile)
    with pytest.raises(OSError, match='verified compute') as failure:
        artifact.probe(interpreter, environment=environment)
    assert secret not in str(failure.value)
    assert len(calls) == 1, 'a failed integrity check must never install, retry or use a fallback'
    argv, options = calls[0]
    assert argv == [str(interpreter), '-I', '-c', artifact.PROBE]
    assert not options.get('shell', False)
    assert options['env'] == environment
    assert json.loads(options['input']) == manifest
    assert secret not in options['input'] and secret not in artifact.PROBE
    assert options['capture_output'] and options['text']


def test_probe_ignores_hostile_python_environment_and_refuses_unlisted_code(bundle, tmp_path, monkeypatch):
    import os
    import py_compile
    import subprocess
    import sys
    from pathlib import Path

    from poolhouse.fleet.wheel_provenance import stamp

    _, base, companion, manifest = bundle
    stamp(base, REVISION, tmp_path)
    prefix = tmp_path / 'managed'
    subprocess.run([sys.executable, '-m', 'venv', str(prefix)], check=True, capture_output=True)
    python = prefix / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--no-index', '--no-compile', str(base), str(companion)],
                   check=True, capture_output=True)
    monkeypatch.setattr(artifact, 'expected', lambda: manifest)
    injected = tmp_path / 'injected'
    injected.mkdir()
    marker = tmp_path / 'executed'
    (injected / 'sitecustomize.py').write_text(f'from pathlib import Path; Path({str(marker)!r}).touch()\n')
    (injected / 'hashlib.py').write_text('raise RuntimeError("injected hashlib")\n')
    fake_manifest = injected / 'compute-artifact.json'
    fake_manifest.write_text('{"source":"forged","files":{}}')
    environment = {**os.environ, 'PYTHONPATH': str(injected), 'PYTHONHOME': str(injected),
                   'PYTHONOPTIMIZE': '2', 'POOLHOUSE_COMPUTE_MANIFEST': str(fake_manifest)}
    assert artifact.probe(python, environment=environment) == manifest['sha256']
    assert not marker.exists(), 'isolated probing must never load environment-selected startup code'
    located = subprocess.run([str(python), '-B', '-I', '-c', "import poolhouse.train;print(poolhouse.train.__file__)"],
                             check=True, text=True, capture_output=True)
    package = Path(located.stdout.strip()).parent
    # A matching source hash cannot authorize injected Python or compiled bytecode.
    payload = injected / 'payload.py'
    payload.write_text(f'from pathlib import Path; Path({str(marker)!r}).touch()\n')
    for relative in ('unlisted.py', f'__pycache__/unlisted.{sys.implementation.cache_tag}.pyc'):
        extra = package / relative
        extra.parent.mkdir(exist_ok=True)
        if extra.suffix == '.pyc':
            py_compile.compile(str(payload), cfile=str(extra), doraise=True)
        else:
            extra.write_text(payload.read_text())
        with pytest.raises(OSError, match='verified compute'):
            artifact.probe(python, environment=environment)
        extra.unlink()
    revision = package.parent / 'fleet/built-from'
    revision.write_text('b' * 40)
    with pytest.raises(OSError, match='verified compute'):
        artifact.probe(python, environment=environment)
    assert not marker.exists()
