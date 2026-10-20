"""Python server commands use the installed matching compute environment."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.serve import python_runtime as runtime
from poolhouse.serve.backend import ServerFailed, ServerSpec
from poolhouse.serve.mlx_tree import MlxTreeBackend
from poolhouse.serve.python_engines import SGLangBackend, VllmBackend


@pytest.fixture
def managed(tmp_path, monkeypatch):
    calls = []
    env = SimpleNamespace(exists=True, python=tmp_path / 'managed/bin/python',
                          require_current_runtime=lambda: calls.append('current'),
                          require_compute=lambda: calls.append('compute'))
    monkeypatch.setattr(runtime, '_checkout', lambda: False)
    monkeypatch.setattr(runtime, 'Environment', lambda root: env)
    monkeypatch.setattr(runtime, 'default_root', lambda: tmp_path)
    return env, calls


@pytest.mark.parametrize('backend,module', [
    (MlxTreeBackend, 'poolhouse.serve.mlx_tree_server'),
    (VllmBackend, 'vllm.entrypoints.openai.api_server'),
    (SGLangBackend, 'sglang.launch_server'),
])
def test_bundled_backend_uses_verified_managed_interpreter(managed, tmp_path, monkeypatch, backend, module):
    env, calls = managed
    observed = []
    monkeypatch.setenv('POOLHOUSE_PYTHON', '/untrusted/python')
    monkeypatch.setenv('PYTHONPATH', '/untrusted/modules')
    monkeypatch.setenv('LD_PRELOAD', '/untrusted/loader')
    monkeypatch.setattr(runtime.subprocess, 'run', lambda argv, **kw:
                        observed.append((argv, kw)) or SimpleNamespace(returncode=0))
    argv = backend().command(ServerSpec(model=tmp_path))
    assert argv[:5] == [str(env.python), '-B', '-I', '-m', module]
    assert calls == ['current', 'compute']
    probe, options = observed[0]
    assert probe[:5] == [str(env.python), '-B', '-I', '-c', runtime._PROBE]
    assert json.loads(probe[5]) == list(runtime._MODULES[backend.name][1])
    assert 'PYTHONPATH' not in options['env'] and 'LD_PRELOAD' not in options['env']
    assert options['capture_output'] is True


@pytest.mark.parametrize('failure', ['missing', 'current', 'compute', 'module', 'timeout'])
def test_unready_managed_runtime_never_falls_back(managed, monkeypatch, failure):
    env, _calls = managed
    def refuse():
        raise OSError('runtime mismatch')
    if failure == 'missing':
        env.exists = False
    elif failure in {'current', 'compute'}:
        setattr(env, 'require_' + ('current_runtime' if failure == 'current' else 'compute'), refuse)
    def probe(*args, **kw):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('fixed-probe', 20)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(runtime.subprocess, 'run', probe)
    with pytest.raises(ServerFailed):
        runtime.command('mlx-tree')


@pytest.mark.parametrize('marker', ['built-from', 'compute-artifact.json', 'linked-marker', 'frozen', 'not-checkout'])
def test_installed_or_forged_source_shape_cannot_downgrade(tmp_path, monkeypatch, marker):
    package = tmp_path / 'src/poolhouse'
    (package / 'fleet').mkdir(parents=True)
    (tmp_path / '.git').mkdir()
    monkeypatch.setattr(runtime.poolhouse, '__file__', str(package / '__init__.py'))
    if marker in {'built-from', 'compute-artifact.json'}:
        (package / 'fleet' / marker).write_text('invalid')
    elif marker == 'linked-marker':
        (package / 'fleet/compute-artifact.json').symlink_to(tmp_path / 'missing')
    elif marker == 'frozen':
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
    else:
        monkeypatch.setattr(runtime.poolhouse, '__file__', str(tmp_path / 'elsewhere/__init__.py'))
    assert not runtime._checkout()


def test_exact_editable_source_keeps_its_interpreter(tmp_path, monkeypatch):
    package = tmp_path / 'src/poolhouse'
    package.mkdir(parents=True)
    (tmp_path / '.git').mkdir()
    monkeypatch.setattr(runtime.poolhouse, '__file__', str(package / '__init__.py'))
    monkeypatch.setattr(runtime, 'Environment', lambda root: pytest.fail('source should not use managed runtime'))
    assert runtime._checkout()
    assert runtime.command('mlx-tree') == [sys.executable, '-m', 'poolhouse.serve.mlx_tree_server']


def test_isolated_module_probe_executes_without_model_import(managed, monkeypatch, tmp_path):
    env, _calls = managed
    env.python = Path(sys.executable)
    poison = tmp_path / 'poison'
    poison.mkdir()
    (poison / 'json.py').write_text('raise RuntimeError("injected")')
    monkeypatch.setenv('PYTHONPATH', str(poison))
    monkeypatch.setattr(runtime, '_MODULES', {'probe': ('fixed.server', ('json', 'pathlib'))})
    assert runtime.command('probe') == [sys.executable, '-B', '-I', '-m', 'fixed.server']
    monkeypatch.setattr(runtime, '_MODULES', {'probe': ('fixed.server', ('poolhouse_missing_dependency_xyz',))})
    with pytest.raises(ServerFailed, match='libraries'):
        runtime.command('probe')


def test_server_environment_preserves_only_explicit_hub_credential(monkeypatch):
    monkeypatch.setenv('HF_TOKEN', 'ambient-credential')
    monkeypatch.setenv('PYTHONHOME', '/untrusted/home')
    monkeypatch.setenv('DYLD_INSERT_LIBRARIES', '/untrusted/lib')
    child = runtime.environment_for_child({'HF_TOKEN': 'authorized-hub-token', 'PYTHONPATH': '/injected',
                                           'LD_LIBRARY_PATH': '/injected', 'DYLD_INSERT_LIBRARIES': '/injected'})
    assert child['HF_TOKEN'] == 'authorized-hub-token'
    assert not any(key.startswith(('PYTHON', 'DYLD_')) for key in child)
    assert 'LD_LIBRARY_PATH' not in child
    assert 'HF_TOKEN' not in runtime.environment_for_child()
