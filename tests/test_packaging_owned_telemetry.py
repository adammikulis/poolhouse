"""Owned telemetry wheels build in a snapshot and install from the existing wheelhouse."""

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def builder(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'packaging' / 'build.py'
    spec = importlib.util.spec_from_file_location('owned_build', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'DIST', tmp_path / 'dist')
    return module


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'Owned source with spaces'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n')
    (root / 'backend.py').write_text('''import pathlib,zipfile
def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):
    pathlib.Path('build-marker').write_text('snapshot build')
    name='metal_smi-1.1.0-py3-none-any.whl'
    with zipfile.ZipFile(pathlib.Path(wheel_directory)/name,'w') as wheel:
        wheel.writestr('metal_smi/__init__.py','__version__="1.1.0"\\n')
        wheel.writestr('metal_smi-1.1.0.dist-info/METADATA','Metadata-Version: 2.1\\nName: metal-smi\\nVersion: 1.1.0\\n')
        wheel.writestr('metal_smi-1.1.0.dist-info/WHEEL','Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        wheel.writestr('metal_smi-1.1.0.dist-info/RECORD','')
    return name
''')
    (root / '.git').mkdir()
    (root / '.git' / 'owner-marker').write_text('untouched')
    return root


@pytest.mark.slow
def test_owned_source_build_and_offline_install_leave_source_unchanged(builder, source, tmp_path):
    before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    wheel = builder.owned_telemetry(source)
    after = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    assert after == before
    assert wheel.parent == builder.DIST and wheel.name == 'metal_smi-1.1.0-py3-none-any.whl'
    target = tmp_path / 'installed'
    result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps',
                             '--find-links', str(builder.DIST), '--target', str(target), 'metal-smi>=1.1.0'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (target / 'metal_smi' / '__init__.py').read_text() == '__version__="1.1.0"\n'


@pytest.mark.parametrize('name,version', [('another-package', '1.1.0'), ('metal-smi', '1.0.9')])
def test_wrong_distribution_or_old_owned_wheel_is_refused(builder, tmp_path, name, version):
    wheel = tmp_path / 'bad.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('owned.dist-info/METADATA', f'Name: {name}\nVersion: {version}\n')
    with pytest.raises(SystemExit, match=r'metal-smi>=1\.1\.0'):
        builder._telemetry_metadata(wheel)


def test_dependency_wheelhouse_reuses_owned_and_existing_wheel_links(builder, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(builder, 'run', lambda argv, **kwargs: calls.append(argv))
    output = tmp_path / 'dependency wheels'
    builder.wheelhouse(output)
    command = calls[0]
    links = [command[index+1] for index, value in enumerate(command) if value == '--find-links']
    assert links == [str(builder.DIST), str(output)]
    assert any(argument.startswith('poolhouse[') for argument in command)


def test_project_wheel_rebuild_preserves_owned_and_dependency_wheels(builder, monkeypatch):
    builder.DIST.mkdir()
    owned = builder.DIST / 'metal_smi-1.1.0-py3-none-any.whl'
    owned.write_bytes(b'owned fixture')
    previous = builder.DIST / 'poolhouse-0.1.0-py3-none-any.whl'
    previous.write_bytes(b'project fixture')
    dependencies = builder.DIST / 'wheels'
    dependencies.mkdir()
    dependency = dependencies / 'dependency-1.0-py3-none-any.whl'
    dependency.write_bytes(b'dependency fixture')
    from poolhouse.fleet import compute_artifact

    built = builder.DIST / 'poolhouse-0.0.0-py3-none-any.whl'

    def run(argv, **kwargs):
        if argv[:2] == ['git', 'archive']:
            subprocess.run(argv, cwd=builder.ROOT, check=True)
        else:
            with zipfile.ZipFile(built, 'w'):
                pass

    monkeypatch.setattr(builder, 'run', run)
    monkeypatch.setattr(builder, 'STAMP', lambda *args: None)
    monkeypatch.setattr(compute_artifact, 'build', lambda *args: None)
    assert builder.wheels() == [owned, built]
    assert not previous.exists() and owned.exists() and dependency.exists()


def test_explicit_source_option_is_used_before_dependency_wheelhouse(builder, monkeypatch, source):
    calls = []
    (source / 'wheel.whl').write_bytes(b'fixture')
    monkeypatch.setattr(builder, 'wheels', lambda: [])
    monkeypatch.setattr(builder, 'owned_telemetry', lambda path: calls.append(('source', path)) or source / 'wheel.whl')
    monkeypatch.setattr(builder, 'wheelhouse', lambda path: calls.append(('house', path)) or [])
    assert builder.main(['--metal-smi-source', str(source), '--wheelhouse']) == 0
    assert calls == [('source', source), ('house', builder.DIST / 'wheels')]


@pytest.fixture
def daemon_requirement(builder, monkeypatch, tmp_path):
    root = tmp_path / 'build root'
    (root / '.build-venv').mkdir(parents=True)
    monkeypatch.setattr(builder, 'ROOT', root)
    monkeypatch.setattr(builder, 'built_from', lambda python: None)
    builder.DIST.joinpath('bundle').mkdir(parents=True)
    made = builder.DIST / 'bundle' / ('poolhouse-headless.exe' if sys.platform == 'win32' else 'poolhouse-headless')
    made.write_bytes(b'fixture frozen output')
    calls = []
    original_run = builder.run
    monkeypatch.setattr(builder, 'run', lambda argv, **kwargs: calls.append(argv))
    assert builder.daemon() == made
    monkeypatch.setattr(builder, 'run', original_run)
    install, = [argv for argv in calls if any(arg.startswith('poolhouse[') for arg in argv)]
    assert install[1:3] == ['install', '-q']
    assert install[install.index('--find-links') + 1] == str(builder.DIST)
    return install[-1]


def test_daemon_installs_declared_telemetry_before_freezing(daemon_requirement):
    from packaging.requirements import Requirement

    assert Requirement(daemon_requirement).extras == {'agents', 'hub', 'fleet-onboard', 'coordinator', 'telemetry'}


def test_telemetry_dependency_retains_platform_marker():
    import tomllib

    from packaging.requirements import Requirement

    project = Path(__file__).resolve().parents[1] / 'pyproject.toml'
    requirement, = tomllib.loads(project.read_text())['project']['optional-dependencies']['telemetry']
    parsed = Requirement(requirement)
    assert parsed.name == 'metal-smi' and parsed.specifier.contains('1.1.0')
    assert parsed.marker.evaluate({'sys_platform': 'darwin'})
    for platform in ('linux', 'win32'):
        assert not parsed.marker.evaluate({'sys_platform': platform})


@pytest.mark.slow
def test_fresh_daemon_requirement_installs_owned_module_offline(builder, source, daemon_requirement, tmp_path):
    from packaging.requirements import Requirement

    builder.owned_telemetry(source)
    base = builder.DIST / 'poolhouse-0.0.0-py3-none-any.whl'
    prefix = 'poolhouse-0.0.0.dist-info/'
    with zipfile.ZipFile(base, 'w') as wheel:
        extras = ''.join(f'Provides-Extra: {extra}\n' for extra in Requirement(daemon_requirement).extras)
        wheel.writestr(prefix + 'METADATA', 'Metadata-Version: 2.1\nName: poolhouse\nVersion: 0.0.0\n' + extras
                       + 'Requires-Dist: metal-smi>=1.1.0; extra == "telemetry"\n')
        wheel.writestr(prefix + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        wheel.writestr(prefix + 'RECORD', '')
    target = tmp_path / 'fresh frozen dependencies'
    done = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--find-links', str(builder.DIST),
                           '--target', str(target), daemon_requirement], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (target / 'metal_smi' / '__init__.py').is_file()
    probe = subprocess.run([sys.executable, '-I', '-B', '-c',
                            'import sys;sys.path.insert(0,sys.argv[1]);import metal_smi;assert metal_smi.__version__=="1.1.0"',
                            str(target)], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
