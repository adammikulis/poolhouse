"""Native preflight uses module dependencies without enabling partial-file result reuse."""
from pathlib import Path

import pytest
import testapp
import testnode
import testselectors
from test_test_selectors import runner

ROOT = Path(__file__).resolve().parents[1]
DECLARATION = 'tests/test_node_contract.py::test_native_adapter_declarations_remain_private_and_without_proof_issuers'


def test_real_declaration_node_selector_has_no_unrelated_native_preflight(monkeypatch):
    module = runner()
    monkeypatch.setenv('DEV_TEST_JOB', 'inline')
    observed = []
    monkeypatch.setattr(module, 'build_node', lambda files=None: observed.append(files) or 0)
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: 0)
    assert module.main(['fast', DECLARATION]) == 0
    assert observed == [['tests/test_node_contract.py']]
    assert not testnode.needed(ROOT, observed[0])
    assert not testapp.needed(ROOT, observed[0])
    assert module.testreuse_run.selectors(module.pytest_command(0, DECLARATION), ROOT) is None


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    tests = root / 'tests'
    tests.mkdir(parents=True)
    (tests / 'test_plain.py').write_text('def test_plain(): pass\n')
    native = testnode.NODE_MARKERS[0]
    (tests / f'{native}.py').write_text('def fixture(): pass\n')
    (tests / 'test_native.py').write_text(f'import {native}\ndef test_native(): pass\n')
    (tests / 'test_app.py').write_text('VALUE = ' + repr(testapp.ENV) + '\n')
    monkeypatch.chdir(root)
    import testoptions
    testoptions._positional.cache_clear()
    return root


@pytest.mark.parametrize('arguments', [
    ['tests/test_plain.py::test_plain'],
    ['tests/test_plain.py::TestGroup::test_case[value]'],
    ['-k', 'tests/test_app.py::fake', '--junitxml', 'tests/test_native.py', 'tests/test_plain.py::test_plain'],
    ['--override-ini', 'testpaths=tests/test_app.py', 'tests/test_plain.py::test_plain'],
    ['--', 'tests/test_plain.py::test_plain'],
])
def test_actual_positional_nodes_are_separate_from_option_values(project, arguments):
    assert testselectors.native_files(arguments, project) == ['tests/test_plain.py']


@pytest.mark.parametrize('arguments', [
    [], ['-k', 'tests/test_plain.py::fake'], ['--junitxml', 'tests/test_plain.py'],
    ['--override-ini', 'testpaths=tests/test_plain.py'], ['tests'], ['tests/no_such.py::test_x'],
])
def test_implicit_directory_or_unknown_selection_stays_conservative(project, arguments):
    assert testselectors.native_files(arguments, project) is None


@pytest.mark.parametrize('name,needs_node,needs_app', [('native', True, False), ('app', False, True)])
def test_native_dependencies_still_require_their_actual_preflight(project, name, needs_node, needs_app, monkeypatch):
    files = testselectors.native_files(['tests/test_plain.py::test_plain', f'tests/test_{name}.py::test_{name}'], project)
    assert testnode.needed(project, files) is needs_node
    assert testapp.needed(project, files) is needs_app
    module = runner()
    monkeypatch.setattr(module, 'ROOT', project)
    built = []
    monkeypatch.setattr(module.testnode, 'ensure', lambda *a, **k: built.append('node') or project / 'node')
    monkeypatch.setattr(module.testapp, 'ensure', lambda *a, **k: built.append('app') or project / 'app')
    assert module.build_node(files) == 0
    assert built == (['node'] if needs_node else ['app'])


def test_outside_root_and_symlink_escape_are_not_a_narrow_native_plan(project, tmp_path):
    outside = tmp_path / 'test_outside.py'
    outside.write_text('def test_x(): pass\n')
    assert testselectors.native_files([str(outside)], project) is None
    link = project / 'tests/link.py'
    link.symlink_to(outside)
    assert testselectors.native_files(['tests/link.py::test_x'], project) is None


def test_confined_selection_does_not_import_pytest_parser(project, monkeypatch):
    import testoptions
    monkeypatch.setattr(testselectors.sys, 'platform', 'darwin')
    monkeypatch.setenv('DEV_TEST_CONFINE', '1')
    monkeypatch.setattr(testoptions, 'explicit_positional', lambda *a: pytest.fail('loaded pytest before confinement'))
    assert testselectors.native_files(['tests/test_plain.py::test_plain'], project) is None


def test_parser_failure_cannot_skip_required_native_preflight(project, monkeypatch):
    import testoptions
    def refused(*args):
        raise RuntimeError('unavailable positional parser')
    monkeypatch.setattr(testoptions, 'explicit_positional', refused)
    assert testselectors.native_files(['tests/test_plain.py'], project) is None


def test_symlinked_parent_cannot_hide_its_visible_conftest(project):
    actual = project / 'actual'
    actual.mkdir()
    (actual / 'test_plain.py').write_text('def test_plain(): pass\n')
    (project / 'tests/linked').symlink_to(actual, target_is_directory=True)
    (project / 'tests/conftest.py').write_text('import ' + testnode.NODE_MARKERS[0] + '\n')
    assert testnode.needed(project, ['tests/linked/test_plain.py'])
    assert not testnode.needed(project, ['actual/test_plain.py'])
    assert testselectors.native_files(['tests/linked/test_plain.py::test_plain'], project) is None
