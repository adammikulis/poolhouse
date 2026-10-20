"""Warm dependency evidence skips subprocesses without hiding code or metadata changes."""
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import distribution

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
    assert counters == {'compute': 1, 'inspect': 0, 'layout': 1}
    values[0]['packages']['poolhouse'] = 'forged'
    assert environment.dependency_metadata()['packages']['poolhouse'] == '0.1.0'
    assert counters == {'compute': 1, 'inspect': 0, 'layout': 1}


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
    assert counters['inspect'] == 0


def test_distribution_versions_and_top_level_changes_invalidate_metadata(managed):
    environment, site, _, counters = managed
    assert environment.dependency_metadata()['error'] is None
    info = site / 'invented_dependency-1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text('Name: invented-dependency\nVersion: 1.0\n')
    result = environment.dependency_metadata()
    assert result['packages']['invented-dependency'] == '1.0'
    assert counters['compute'] == 2 and counters['inspect'] == 0
    (info / 'METADATA').write_text('Name: invented-dependency\nVersion: 2.0\n')
    assert environment.dependency_metadata()['packages']['invented-dependency'] == '2.0'
    assert counters['compute'] == 3 and counters['inspect'] == 0
    (site / 'empty.pth').write_text('# inert test file\n')
    assert environment.dependency_metadata()['error'] is None
    assert counters['compute'] == 4 and counters['inspect'] == 0


def test_writer_repair_and_app_manifest_changes_do_not_reuse_old_proof(managed):
    environment, site, app, counters = managed
    assert environment.dependency_metadata()['error'] is None
    with maintenance.intent() as writer, writer.exclusive():
        source = site / 'poolhouse/train/__init__.py'
        source.write_text(source.read_text())
        environment._cache.clear()
    assert environment.dependency_metadata()['error'] is None
    assert counters == {'compute': 2, 'inspect': 0, 'layout': 2}
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
    assert counters == {'compute': 1, 'inspect': 0, 'layout': 1}


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
    assert counters == {'compute': 2, 'inspect': 0, 'layout': 2}
    current = os.getpid()
    monkeypatch.setattr(proof.os, 'getpid', lambda: current + 1)
    assert environment.dependency_metadata()['error'] is None
    assert counters == {'compute': 3, 'inspect': 0, 'layout': 3}


def test_external_editable_metadata_is_not_an_installed_fixed_site_version(managed):
    environment, site, _, counters = managed
    external = environment.path / 'external-metadata'
    info = external / 'outside_package-1.0.dist-info'
    info.mkdir(parents=True)
    metadata = info / 'METADATA'
    metadata.write_text('Name: outside-package\nVersion: 1.0\n')
    (site / 'external.pth').write_text(str(external) + '\n')
    assert 'outside-package' not in environment.dependency_metadata()['packages']
    metadata.write_text('Name: outside-package\nVersion: 2.0\n')
    assert 'outside-package' not in environment.dependency_metadata()['packages']
    assert counters == {'compute': 1, 'inspect': 0, 'layout': 1}
    assert 'dependency_proof' in environment._cache


def test_actual_setuptools_distutils_hook_does_not_repeat_cold_verification(managed):
    environment, site, _, counters = managed
    setuptools = distribution('setuptools')
    hook = setuptools.locate_file('distutils-precedence.pth').read_bytes()
    assert b'_distutils_hack' in hook
    (site / 'distutils-precedence.pth').write_bytes(hook)
    shutil.copytree(setuptools.locate_file('_distutils_hack'), site / '_distutils_hack',
                    ignore=shutil.ignore_patterns('__pycache__'))
    info = site / f'setuptools-{setuptools.version}.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text(f'Name: setuptools\nVersion: {setuptools.version}\n')
    first = environment.dependency_metadata()
    assert first['error'] is None and first['packages']['setuptools'] == setuptools.version
    assert environment.dependency_metadata() == first
    assert counters == {'compute': 1, 'inspect': 0, 'layout': 1}


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
                 'roots': [str(tmp_path / 'site-packages')], 'abi': 'fixture', **value}
    monkeypatch.setattr(proof.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(value)))
    with pytest.raises(OSError, match='another interpreter'):
        proof._layout(SimpleNamespace(path=tmp_path, python=tmp_path / 'bin/python'))


def test_site_hooks_and_customization_are_not_metadata_discovery(managed):
    environment, site, app, _ = managed
    marker = app / 'executed'
    source = f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
    (site / 'sitecustomize.py').write_text(source)
    (site / 'injected_hook.py').write_text(source)
    (site / 'injected.pth').write_text('import injected_hook\n')
    layout = proof._layout(environment)
    assert proof._versions(layout, dict(proof._signature(layout, artifact.expected())))['poolhouse'] == '0.1.0'
    assert not marker.exists()


@pytest.mark.parametrize('raw', [b'Name: first\nName: second\nVersion: 1\n',
                                 b'Name: first\nVersion: 1\nVersion: 2\n',
                                 b'Name: ../escape\nVersion: 1\n',
                                 b'Name: valid\nVersion: invalid-version\n', b'Name: missing-version\n',
                                 b'Name: \xff\nVersion: 1\n'])
def test_ambiguous_or_invalid_declared_versions_are_refused(tmp_path, raw):
    metadata = tmp_path / 'METADATA'
    metadata.write_bytes(raw)
    with pytest.raises((OSError, ValueError)):
        proof._metadata(metadata, proof._file(metadata))


def test_duplicate_normalized_projects_and_metadata_links_are_refused(tmp_path):
    for folder, name in (('one.dist-info', 'Invented_Name'), ('two.dist-info', 'invented-name')):
        info = tmp_path / folder
        info.mkdir()
        (info / 'METADATA').write_text(f'Name: {name}\nVersion: 1\n')
    with pytest.raises(OSError, match='duplicate project'):
        observed = {str(path): proof._file(path) for path in tmp_path.glob('*/METADATA')}
        proof._versions(('abi', (tmp_path,)), observed)
    link = tmp_path / 'linked'
    link.symlink_to(tmp_path / 'one.dist-info/METADATA')
    with pytest.raises(OSError, match='linked'):
        proof._metadata(link, proof._file(link))


def test_metadata_read_uses_existing_body_bound(tmp_path):
    metadata = tmp_path / 'METADATA'
    metadata.write_bytes(b'x' * (proof.MAX_BODY + 1))
    with pytest.raises(OSError, match='metadata limit'):
        proof._metadata(metadata, proof._file(metadata))


def test_directory_egg_info_is_declared_metadata_and_standalone_file_is_refused(tmp_path):
    directory = tmp_path / 'declared.egg-info'
    directory.mkdir()
    metadata = directory / 'PKG-INFO'
    metadata.write_text('Name: Invented_Project\nVersion: 01.0\nHome-page: https://example.invalid/inert\n')
    observed = {str(metadata): proof._file(metadata)}
    assert proof._versions(('abi', (tmp_path,)), observed) == {'invented-project': '1.0'}
    standalone = tmp_path / 'standalone.egg-info'
    standalone.write_text('Name: standalone\nVersion: 1\n')
    observed[str(standalone)] = proof._file(standalone)
    with pytest.raises(OSError, match='missing or changed'):
        proof._versions(('abi', (tmp_path,)), observed)


def test_metadata_replacement_cannot_substitute_the_snapshot(tmp_path):
    metadata = tmp_path / 'METADATA'
    metadata.write_text('Name: original\nVersion: 1\n')
    expected = proof._file(metadata)
    replacement = tmp_path / 'replacement'
    replacement.write_text('Name: substituted\nVersion: 2\n')
    replacement.replace(metadata)
    with pytest.raises(OSError, match='changed before reading'):
        proof._metadata(metadata, expected)


@pytest.mark.parametrize('kind', ['traversal', 'duplicate'])
def test_layout_roots_must_be_confined_without_aliases(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    roots = [str(tmp_path / 'site-packages')] * 2 if kind == 'duplicate' else [str(tmp_path / '../outside/site-packages')]
    value = {'prefix': str(tmp_path), 'executable': str(tmp_path / 'bin/python'), 'roots': roots, 'abi': 'fixture'}
    monkeypatch.setattr(proof.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(value)))
    with pytest.raises(OSError, match='inside its interpreter'):
        proof._layout(SimpleNamespace(path=tmp_path, python=tmp_path / 'bin/python'))
