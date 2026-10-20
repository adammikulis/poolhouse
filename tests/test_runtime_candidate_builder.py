"""An older caller cannot substitute its artifact catalogue for the candidate's closure."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from poolhouse.fleet import compute_artifact, runtime_wheel
from poolhouse.fleet.wheel_provenance import stamp, write_wheel

REVISION = 'a' * 40
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def candidate(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(ROOT / 'src', source / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    wheel = tmp_path / 'wheels' / 'poolhouse-0.1.0-py3-none-any.whl'
    wheel.parent.mkdir()
    files = {path.relative_to(source / 'src').as_posix(): path.read_bytes()
             for path in (source / 'src/poolhouse').rglob('*') if path.is_file()
             and not path.relative_to(source / 'src').as_posix().startswith(compute_artifact.PREFIXES)
             and path.relative_to(source / 'src').as_posix() not in compute_artifact.EXTRA_FILES}
    # Keep native environment installation outside this packaging regression; candidate cache verification is real.
    files['poolhouse/fleet/runtime_wheel.py'] += b'''\n
def prepare(wheel, commit, *, timeout, target):
    target.prefix.mkdir(parents=True)
    cache_wheel(wheel, commit, prefix=target.prefix)
    return runtime.Runtime(target.prefix, commit, '0.1.0', runtime.identity())
'''
    files.update({'poolhouse-0.1.0.dist-info/METADATA': b'Name: poolhouse\nVersion: 0.1.0\n',
                  'poolhouse-0.1.0.dist-info/RECORD': b''})
    write_wheel(wheel, files)
    stamp(wheel, REVISION, ROOT)
    return source, wheel, tmp_path / 'candidate-prefix'


def test_old_caller_build_and_cache_catalogues_cannot_override_candidate(candidate, monkeypatch, tmp_path):
    source, wheel, prefix = candidate
    def obsolete(*_args, **_kwargs):
        pytest.fail('the caller artifact implementation must not be used')
    monkeypatch.setattr(compute_artifact, 'build', obsolete)
    monkeypatch.setattr(compute_artifact, 'cache', obsolete)
    monkeypatch.setattr(compute_artifact, 'verify', obsolete)
    monkeypatch.setattr(runtime_wheel.runtime, 'verify', lambda chosen: chosen)
    poisoned = tmp_path / 'poison'
    poisoned.mkdir()
    (poisoned / 'sitecustomize.py').write_text("raise RuntimeError('ambient code must not load')")
    monkeypatch.setenv('PYTHONPATH', str(poisoned))
    chosen = runtime_wheel._candidate_prepare(wheel, REVISION, ROOT, timeout=20,
                                             target=runtime_wheel.Target(prefix=prefix, host=Path(sys.executable)))
    assert chosen.prefix == prefix and chosen.commit == REVISION
    manifest = compute_artifact.from_wheel(wheel)
    assert len(compute_artifact.EXTRA_FILES) == 58
    assert set(compute_artifact.EXTRA_FILES) <= set(manifest['files'])
    assert any(name.startswith('poolhouse/backend/') for name in manifest['files'])
    cached = prefix / 'poolhouse-wheels' / REVISION
    assert (cached / wheel.name).is_file() and (cached / manifest['filename']).is_file()
    assert (cached / manifest['filename']).read_bytes() == wheel.with_name(manifest['filename']).read_bytes()
    assert source.is_dir()


def test_candidate_digest_is_checked_before_any_candidate_code_executes(candidate):
    source, wheel, prefix = candidate
    payload = {'source': str(source), 'wheel': str(wheel), 'commit': REVISION,
               'checkout': str(ROOT), 'timeout': 20, 'target': {'prefix': str(prefix), 'creator': None, 'host': sys.executable},
               'wheel_sha256': '0' * 64}
    with pytest.raises(ValueError, match='Candidate wheel changed'):
        runtime_wheel._run([sys.executable, '-B', '-I', '-c', runtime_wheel.CANDIDATE, str(wheel)],
                           20, input=json.dumps(payload))
    assert not prefix.exists() and compute_artifact.from_wheel(wheel) is None


def test_loaded_foreign_poolhouse_module_is_rejected_before_build(candidate):
    source, wheel, prefix = candidate
    files = compute_artifact.contents(wheel)
    files['poolhouse/fleet/runtime_wheel.py'] += b"\nimport types\nsys.modules['poolhouse.foreign']=types.SimpleNamespace(__file__='/outside/foreign.py')\n"
    write_wheel(wheel, files)
    payload = {'source': str(source), 'wheel': str(wheel), 'commit': REVISION,
               'checkout': str(ROOT), 'timeout': 20, 'target': {'prefix': str(prefix), 'creator': None, 'host': sys.executable},
               'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='Candidate imported caller code'):
        runtime_wheel._run([sys.executable, '-B', '-I', '-c', runtime_wheel.CANDIDATE, str(wheel)],
                           20, input=json.dumps(payload))
    assert not prefix.exists() and compute_artifact.from_wheel(wheel) is None
