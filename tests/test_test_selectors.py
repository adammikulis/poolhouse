"""Explicit pytest paths cannot vanish into an empty successful quick run."""

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def runner():
    loader = importlib.machinery.SourceFileLoader('tier_runner', str(ROOT / 'scripts' / 'test'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


@pytest.mark.parametrize('path', ['tests/no-such-file.py', 'tests/no-such-directory',
                                  'tests/no-such-file.py::test_missing'])
def test_missing_explicit_path_is_rejected_before_broker(path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'test'), 'all', path, '-n', '2'],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 4
    assert 'test path does not exist' in result.stderr
    assert '+ ' not in result.stdout


def test_existing_node_selector_and_option_values_are_preserved():
    module = runner()
    module.testselectors.validate(['-k', 'test_missing', '-m', 'not slow',
                                  'tests/test_test_selectors.py::test_missing_explicit_path_is_rejected_before_broker'], ROOT)


def test_xdist_empty_explicit_node_is_an_error_even_for_quick(monkeypatch):
    module = runner()
    monkeypatch.setattr(module.testslots_runner, 'run_pytest', lambda *args, **kwargs: 5)
    monkeypatch.setattr(module, 'record_run', lambda *args: None)
    status = module.run(module.pytest_command(2, 'tests/test_test_selectors.py::test_nonexistent'), tier='quick')
    assert status == 4 and module.clean(status) == 4


@pytest.mark.parametrize(('options', 'workers'), [([], 0), (['-n', '0'], 0), (['-n', '1'], 1), (['-n', '3'], 3)])
def test_runner_defaults_to_the_brokers_automatic_pool_and_preserves_explicit_limits(monkeypatch, options, workers):
    module = runner()
    calls = []
    monkeypatch.setenv('DEV_TEST_JOB', 'inline')
    monkeypatch.setattr(sys, 'argv', ['scripts/test', 'all', 'tests/test_test_selectors.py', *options])
    judged = []
    monkeypatch.setattr(module, 'build_node', lambda files=None: judged.append(files) or 0)
    monkeypatch.setattr(module, 'run', lambda command, **kwargs: calls.append(command) or 0)
    assert module.main() == 0
    assert calls[0][calls[0].index('-n') + 1] == str(workers)
    assert judged == [['tests/test_test_selectors.py']]


@pytest.mark.parametrize('spelled', [str(ROOT / 'tests' / 'test_test_selectors.py'), './tests/test_test_selectors.py',
                                     'tests/../tests/test_test_selectors.py', str(ROOT / 'tests' / 'test_test_selectors.py') + '::test_x'])
def test_a_test_path_in_the_checkout_reaches_the_runner_spelled_from_its_root(monkeypatch, spelled):
    """The reuse store, the node build and the junit file name a file by its path from the checkout: a file named by an absolute path
    ran its tests and was then reported as a run of nothing, exit 4."""
    module = runner()
    calls = []
    monkeypatch.setenv('DEV_TEST_JOB', 'inline')
    monkeypatch.setattr(sys, 'argv', ['scripts/test', 'all', spelled, '-k', 'not slow and absolute'])
    judged = []
    monkeypatch.setattr(module, 'build_node', lambda files=None: judged.append(files) or 0)
    monkeypatch.setattr(module, 'run', lambda command, **kwargs: calls.append(command) or 0)
    assert module.main() == 0
    named = 'tests/test_test_selectors.py' + ('::test_x' if spelled.endswith('::test_x') else '')
    assert named in calls[0] and (spelled == named or spelled not in calls[0])
    assert calls[0][calls[0].index('-k') + 1] == 'not slow and absolute', 'a word that is no path is left as it was'
    assert judged == ([None] if '::' in named else [['tests/test_test_selectors.py']])


def test_a_test_path_outside_the_checkout_is_refused_before_the_broker(tmp_path):
    """The runner's root, store and node build are this checkout's: a file of another tree would be run as if it were this one's."""
    outside = tmp_path / 'test_outside.py'
    outside.write_text('def test_x():\n    pass\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'test'), 'all', str(outside), '-n', '2'],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 4
    assert 'outside this checkout' in result.stderr
    assert '+ ' not in result.stdout


def test_a_rust_test_file_is_refused_before_the_broker_and_names_the_test_that_runs_it():
    """Pytest was handed the .rs path, loaded no conftest and ended with "unrecognized arguments: --slow", exit 4, whose cause was not in it."""
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'test'), 'all', 'app/poolhouse-node/tests/grants_authority.rs', '-n', '2'],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 4
    assert 'not a Python test file' in result.stderr and 'tests/test_governed_grants_native.py' in result.stderr
    assert '+ ' not in result.stdout


def test_no_gate_step_writes_to_the_tree():
    """A gate only reads: a step that rewrote a tracked file dirtied the worktree it was judging."""
    module = runner()
    steps = module.gate_steps(0, [])
    assert [name for name, _ in steps] == ['budgets', 'redteam coverage', 'command reference', 'structure tests']
    for name, command in steps:
        assert not {'--write', '--update', '--fix'} & set(command), (name, command)
    assert [part for part in dict(steps)['command reference'] if part.startswith('--')] == ['--check']


def test_a_stale_command_table_fails_the_check_and_leaves_the_page_alone(tmp_path):
    page = tmp_path / 'commands.md'
    page.write_text('# The commands\n\n| stale | table |\n', encoding='utf-8')
    before = page.read_bytes()
    done = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'reference'), '--check', '--page', str(page)],
                          capture_output=True, text=True, check=False, cwd=ROOT)
    assert done.returncode != 0
    assert page.read_bytes() == before
