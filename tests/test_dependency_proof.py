"""Warm dependency evidence skips subprocesses without hiding code or metadata changes."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from poolhouse import home, maintenance
from poolhouse.fleet import compute_artifact as artifact, dependency_proof as proof
from poolhouse.fleet.environment import Environment
from poolhouse.fleet.wheel_provenance import stamp, write_wheel


@pytest.fixture
def managed(tmp_path, monkeypatch):
    # The runner exposes checkout/user imports; the fixed isolated fixture needs neither.
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(home, 'state', lambda *parts: tmp_path.joinpath('state', *parts))
    source = tmp_path / 'source'
    for name in ('backend', 'train', 'bench', 'gym', 'world'):
        path = source / 'src/poolhouse' / name / '__init__.py'
        path.parent.mkdir(parents=True)
        path.write_text('VALUE = 42\n')
    for name in artifact.EXTRA_FILES:
        path = source / 'src' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('VALUE = 42\n')
    base = tmp_path / 'poolhouse-0.1.0-py3-none-any.whl'
    write_wheel(base, {'poolhouse/__init__.py': b'', 'poolhouse/graph/__init__.py': b'',
                      'poolhouse-0.1.0.dist-info/METADATA': b'Name: poolhouse\nVersion: 0.1.0\n',
                      'poolhouse-0.1.0.dist-info/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
                      'poolhouse-0.1.0.dist-info/RECORD': b''})
    companion = artifact.build(source, base, 'a' * 40)
    stamp(base, 'a' * 40, source)
    manifest = artifact.from_wheel(base)
    app = tmp_path / 'app'
    app.mkdir()
    (app / 'compute-artifact.json').write_text(json.dumps(manifest))
    (app / 'built-from').write_text('a' * 40)
    monkeypatch.setattr(artifact, '__file__', str(app / 'compute_artifact.py'))
    monkeypatch.setattr(artifact, 'expected', lambda: json.loads((app / 'compute-artifact.json').read_text()))
    environment = Environment(tmp_path / 'managed')
    subprocess.run([sys.executable, '-m', 'venv', str(environment.path)], check=True, capture_output=True)
    subprocess.run([str(environment.python), '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-compile',
                    str(base), str(companion)], check=True, capture_output=True)
    site = environment.path / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    counters = {'compute': 0, 'inspect': 0, 'layout': 0}
    compute, installed, layout = environment.require_compute, environment.installed, proof._layout
    def checked():
        counters['compute'] += 1
        return compute()
    def inspected():
        counters['inspect'] += 1
        return installed()
    def located(target):
        counters['layout'] += 1
        return layout(target)
    monkeypatch.setattr(environment, 'require_compute', checked)
    monkeypatch.setattr(environment, 'installed', inspected)
    monkeypatch.setattr(proof, '_layout', located)
    return environment, site, app, counters


def test_actual_cold_checks_are_coalesced_and_warm_calls_launch_no_subprocesses(managed):
    environment, _, _, counters = managed
    with ThreadPoolExecutor(max_workers=4) as workers:
        values = list(workers.map(lambda _: environment.dependency_metadata(), range(8)))
    assert all(value['error'] is None for value in values)
    assert counters == {'compute': 1, 'inspect': 1, 'layout': 1}
    values[0]['packages']['poolhouse'] = 'forged'
    assert environment.dependency_metadata()['packages']['poolhouse'] == '0.1.0'
    assert counters == {'compute': 1, 'inspect': 1, 'layout': 1}


@pytest.mark.parametrize('change', ['source', 'unexpected', 'base-file', 'source-link', 'metadata-link', 'ownership', 'replacement'])
def test_changed_or_unfamiliar_evidence_is_never_reused(managed, change):
    environment, site, _, counters = managed
    assert environment.dependency_metadata()['error'] is None
    source = site / 'poolhouse/train/__init__.py'
    if change == 'source':
        before = source.stat()
        source.write_text('VALUE = 43\n')
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == 'unexpected':
        source.with_name('unexpected.py').write_text('VALUE = 42\n')
    elif change == 'base-file':
        (site / 'poolhouse/graph/__init__.py').write_text('changed')
    elif change == 'source-link':
        source.unlink()
        source.symlink_to(site / 'poolhouse/bench/__init__.py')
    elif change == 'metadata-link':
        metadata = site / 'poolhouse-0.1.0.dist-info/METADATA'
        original = metadata.with_name('original')
        metadata.rename(original)
        metadata.symlink_to(original)
    elif change == 'ownership':
        source.chmod(0o666)
    else:
        old = source.with_suffix('.old')
        source.rename(old)
        source.write_bytes(old.read_bytes())
        old.unlink()
    result = environment.dependency_metadata()
    if change == 'replacement':
        assert result['error'] is None and counters['compute'] == 2
    else:
        assert result['error'] is not None
    assert counters['inspect'] >= 2


def test_distribution_versions_and_top_level_changes_invalidate_metadata(managed):
    environment, site, _, counters = managed
    assert environment.dependency_metadata()['error'] is None
    info = site / 'invented_dependency-1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text('Name: invented-dependency\nVersion: 1.0\n')
    result = environment.dependency_metadata()
    assert result['packages']['invented-dependency'] == '1.0'
    assert counters['compute'] == counters['inspect'] == 2
    (info / 'METADATA').write_text('Name: invented-dependency\nVersion: 2.0\n')
    assert environment.dependency_metadata()['packages']['invented-dependency'] == '2.0'
    assert counters['compute'] == counters['inspect'] == 3
    (site / 'empty.pth').write_text('# inert test file\n')
    assert environment.dependency_metadata()['error'] is None
    assert counters['compute'] == counters['inspect'] == 4


def test_writer_repair_and_app_manifest_changes_do_not_reuse_old_proof(managed):
    environment, site, app, counters = managed
    assert environment.dependency_metadata()['error'] is None
    with maintenance.intent() as writer, writer.exclusive():
        source = site / 'poolhouse/train/__init__.py'
        source.write_text(source.read_text())
        environment._cache.clear()
    assert environment.dependency_metadata()['error'] is None
    assert counters == {'compute': 2, 'inspect': 2, 'layout': 2}
    manifest = app / 'compute-artifact.json'
    value = json.loads(manifest.read_text())
    value['source'] = 'b' * 40
    manifest.write_text(json.dumps(value))
    assert environment.dependency_metadata()['error'] is not None


def test_file_changes_during_actual_verification_are_not_cached(managed, monkeypatch):
    environment, site, _, counters = managed
    original = environment.require_compute
    def racing():
        result = original()
        source = site / 'poolhouse/train/__init__.py'
        source.write_text(source.read_text())
        return result
    monkeypatch.setattr(environment, 'require_compute', racing)
    result = environment.dependency_metadata()
    assert result['error'] and 'changed during' in result['error']
    assert 'dependency_proof' not in environment._cache
    monkeypatch.setattr(environment, 'require_compute', original)
    assert environment.dependency_metadata()['error'] is None
    assert counters['compute'] == 2


def test_unrelated_sibling_activity_does_not_invalidate_unchanged_dependency_roots(managed):
    environment, _, app, counters = managed
    assert environment.dependency_metadata()['error'] is None
    (app.parent / 'unrelated-work').write_text('independent task')
    assert environment.dependency_metadata()['error'] is None
    assert counters == {'compute': 1, 'inspect': 1, 'layout': 1}


def test_warm_snapshot_detects_replacement_after_its_first_stat(managed, monkeypatch):
    environment, site, _, counters = managed
    assert environment.dependency_metadata()['error'] is None
    source = site / 'poolhouse/train/__init__.py'
    original, changed = proof._identity, False
    def replacing(path, **kwargs):
        nonlocal changed
        value = original(path, **kwargs)
        if path == source and not changed:
            changed = True
            replacement = source.with_suffix('.new')
            replacement.write_text('VALUE = 43\n')
            replacement.replace(source)
        return value
    monkeypatch.setattr(proof, '_identity', replacing)
    assert 'changed during enumeration' in environment.dependency_metadata()['error']
    assert 'dependency_proof' not in environment._cache
    assert counters['compute'] == 1


def test_interpreter_configuration_and_process_identity_reverify(managed, monkeypatch):
    environment, _, _, counters = managed
    assert environment.dependency_metadata()['error'] is None
    configuration = environment.path / 'pyvenv.cfg'
    configuration.write_text(configuration.read_text() + '\n# rewritten configuration\n')
    assert environment.dependency_metadata()['error'] is None
    assert counters == {'compute': 2, 'inspect': 2, 'layout': 2}
    current = os.getpid()
    monkeypatch.setattr(proof.os, 'getpid', lambda: current + 1)
    assert environment.dependency_metadata()['error'] is None
    assert counters == {'compute': 3, 'inspect': 3, 'layout': 3}


def test_external_pth_metadata_uses_uncached_existing_contract(managed):
    environment, site, _, counters = managed
    external = environment.path / 'external-metadata'
    info = external / 'outside_package-1.0.dist-info'
    info.mkdir(parents=True)
    metadata = info / 'METADATA'
    metadata.write_text('Name: outside-package\nVersion: 1.0\n')
    (site / 'external.pth').write_text(str(external) + '\n')
    assert environment.dependency_metadata()['packages']['outside-package'] == '1.0'
    metadata.write_text('Name: outside-package\nVersion: 2.0\n')
    assert environment.dependency_metadata()['packages']['outside-package'] == '2.0'
    assert counters['compute'] == counters['inspect'] == 2
    assert 'dependency_proof' not in environment._cache


def test_executable_pth_preserves_supported_uncached_verification(managed):
    environment, site, _, counters = managed
    (site / 'hook.pth').write_text('import sys\n')
    assert environment.dependency_metadata()['error'] is None
    assert environment.dependency_metadata()['error'] is None
    assert counters['compute'] == counters['inspect'] == 2
    assert 'dependency_proof' not in environment._cache


def test_actual_layout_ignores_hostile_pythonpath(managed, monkeypatch):
    environment, _, app, _ = managed
    marker = app / 'executed'
    poison = app / 'poison'
    poison.mkdir()
    (poison / 'sitecustomize.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
    monkeypatch.setenv('PYTHONPATH', str(poison))
    assert proof._layout(environment)[0]
    assert not marker.exists()


@pytest.mark.parametrize('value', [None, [], {'roots': [None]}, {'roots': 'not-a-list'}])
def test_malformed_layout_is_a_controlled_proof_failure(tmp_path, monkeypatch, value):
    from types import SimpleNamespace
    if isinstance(value, dict):
        value = {'prefix': str(tmp_path), 'executable': str(tmp_path / 'bin/python'),
                 'roots': [str(tmp_path / 'site-packages')], 'standard': [], 'paths': [],
                 'abi': 'fixture', 'user_site': False, **value}
    monkeypatch.setattr(proof.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(value)))
    with pytest.raises(OSError, match='another interpreter'):
        proof._layout(SimpleNamespace(path=tmp_path, python=tmp_path / 'bin/python'))


@pytest.mark.parametrize('selector', ['PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE'])
def test_python_startup_selectors_cannot_bless_cached_inspection(tmp_path, monkeypatch, selector):
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
        monkeypatch.delenv(name, raising=False)
    assert proof._cacheable(('abi', (tmp_path,), (), (str(tmp_path),), False))
    monkeypatch.setenv(selector, str(tmp_path / 'outside'))
    assert not proof._cacheable(('abi', (tmp_path,), (), (str(tmp_path),), False))


@pytest.mark.parametrize('customization', ['sitecustomize.py', 'sitecustomize', 'usercustomize.py'])
def test_startup_customization_requires_uncached_inspection(tmp_path, monkeypatch, customization):
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
        monkeypatch.delenv(name, raising=False)
    assert proof._cacheable(('abi', (tmp_path,), (), (str(tmp_path),), False))
    path = tmp_path / customization
    path.mkdir() if '.' not in customization else path.write_text('# harmless fixture\n')
    assert not proof._cacheable(('abi', (tmp_path,), (), (str(tmp_path),), False))
