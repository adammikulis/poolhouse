"""Built app and compute wheels close over their maintained internal imports."""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse.fleet import compute_artifact as artifact
from poolhouse.fleet.wheel_provenance import stamp

REPO = Path(__file__).resolve().parents[1]
REVISION = 'a' * 40


def _modules(files):
    return {name.removesuffix('.py').replace('/', '.').removesuffix('.__init__'): name
            for name in files if name.startswith('poolhouse/') and name.endswith('.py')}


def _imports(name, source):
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = name.removesuffix('.py').split('/')[:-1]
                parts = parts[:len(parts) - node.level + 1]
                prefix = '.'.join(parts + ([node.module] if node.module else []))
            else:
                prefix = node.module or ''
            names = [prefix, *(prefix + '.' + item.name for item in node.names)]
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in {'module', 'import_module'} and node.args
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            names = [node.args[0].value]
        for module in names:
            parts = module.split('.')
            yield from ('.'.join(parts[:count]) for count in range(2, len(parts) + 1))


def _missing(base, compute):
    sources = {path.relative_to(REPO / 'src').as_posix(): path.read_bytes()
               for path in (REPO / 'src/poolhouse').rglob('*.py')}
    modules = _modules(sources)
    available = set(base) | set(compute)
    return sorted({(name, modules[module]) for name, content in compute.items() if name.endswith('.py')
                   for module in _imports(name, content) if module in modules and modules[module] not in available})


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp('compute-closure')
    source = root / 'source'
    source.mkdir()
    for name in ('src', 'contracts', 'patches'):
        shutil.copytree(REPO / name, source / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('pyproject.toml', 'version.txt', 'README.md', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(REPO / name, source / name)
    subprocess.run([sys.executable, '-m', 'build', '--no-isolation', '--wheel', '--outdir', str(root), str(source)],
                   check=True, capture_output=True, text=True)
    base, = root.glob('poolhouse-*.whl')
    companion = artifact.build(source, base, REVISION)
    stamp(base, REVISION, REPO)
    return root, base, companion, artifact.from_wheel(base)


def test_actual_wheels_have_complete_disjoint_internal_code_ownership(built):
    _, base, companion, manifest = built
    base_files, compute = artifact.contents(base), artifact.contents(companion)
    assert not set(base_files) & set(compute)
    assert _missing(base_files, compute) == []
    assert 'poolhouse/backend/__init__.py' in compute
    assert manifest['base_files'] and all(name.startswith('poolhouse/graph/') for name in manifest['base_files'])
    artifact.verify(companion, manifest, REVISION)
    del compute['poolhouse/backend/__init__.py']
    assert any(target == 'poolhouse/backend/__init__.py' for _, target in _missing(base_files, compute))


def test_built_wheel_recipe_imports_and_repeated_integrity_probe_are_isolated(built):
    root, base, companion, manifest = built
    site = root / 'installed'
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-compile',
                    '--target', str(site), str(base), str(companion)], check=True, capture_output=True, text=True)
    program = '''import json,sys
from pathlib import Path
site=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(site))
import poolhouse.backend,poolhouse.train.recipes,poolhouse.bench,poolhouse.gym,poolhouse.world
assert 'tool-calls' in poolhouse.train.recipes.known()
for name,module in tuple(sys.modules.items()):
 if name=='poolhouse' or name.startswith('poolhouse.'):
  if getattr(module,'__file__',None):
   assert Path(module.__file__).resolve().is_relative_to(site), (name,module.__file__)
from poolhouse.fleet.compute_artifact import PROBE
exec(PROBE)
'''
    for _ in range(2):
        result = subprocess.run([sys.executable, '-B', '-I', '-c', program, str(site)],
                                input=json.dumps(manifest), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == manifest['sha256']
    assert not list((site / 'poolhouse/backend').rglob('*.pyc'))
    (site / 'poolhouse/backend/unlisted.py').write_text('VALUE = 1')
    result = subprocess.run([sys.executable, '-B', '-I', '-c', program, str(site)],
                            input=json.dumps(manifest), capture_output=True, text=True)
    assert result.returncode != 0 and 'unexpected file' in result.stderr


def test_dynamic_import_sites_have_explicit_command_boundaries(built):
    _, _, companion, _ = built
    documented = {
        ('poolhouse/train/run.py', 'name'): 'Training CLI recipe registration',
        ('poolhouse/train/suite.py', 'name'): 'Training CLI suite registration',
        ('poolhouse/train/tools/cli.py', 'module'): 'Training CLI tool-schema selection',
        ('poolhouse/bench/run.py', "f'poolhouse.bench.{name}'"): 'Bundled benchmark subcommands',
    }
    found = set()
    for name, content in artifact.contents(companion).items():
        if not name.endswith('.py'):
            continue
        for node in ast.walk(ast.parse(content)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {'module', 'import_module'} and node.args
                    and not isinstance(node.args[0], ast.Constant)):
                found.add((name, ast.unparse(node.args[0])))
    assert found == set(documented)
