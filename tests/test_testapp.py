"""Native app compilation happens before cases and reuses only verified source-keyed artifacts."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import testapp


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    target = cache / 'targets' / 'compiler'
    staged = cache / 'workspaces' / 'source-a' / 'app'
    staged.mkdir(parents=True)
    target.mkdir(parents=True)
    build = SimpleNamespace(staged=staged, target=target)
    monkeypatch.setattr(testapp.testnode, 'shared_build', lambda root: build)
    monkeypatch.setattr(testapp.testnode, 'account', lambda: tmp_path / 'toolchain')
    monkeypatch.setattr(testapp.testnode, 'tool', lambda name: name)
    calls = []
    def compile(argv, work, env):
        assert '--no-run' in argv and '--offline' in argv and '--locked' in argv
        assert env['HOME'] != str(tmp_path / 'toolchain')
        made = target / 'debug' / 'deps' / 'poolhouse_app-fixed'
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_bytes(b'compiled actual source ' + work.parent.name.encode())
        calls.append(argv)
        return json.dumps({'reason': 'compiler-artifact', 'target': {'name': 'poolhouse-app', 'kind': ['bin']},
                           'package_id': 'path+file:///app#poolhouse-app@0.3.0', 'profile': {'test': True}, 'executable': str(made)})
    monkeypatch.setattr(testapp, 'run', compile)
    return tmp_path, build, calls


def test_selector_only_prepares_cases_that_use_the_app(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_plain.py').write_text('def test_plain(): pass\n')
    (tests / 'test_app.py').write_text('VALUE = ' + repr(testapp.ENV) + '\n')
    assert not testapp.needed(tmp_path, ['tests/test_plain.py'])
    assert testapp.needed(tmp_path, ['tests/test_app.py'])
    assert testapp.needed(tmp_path, [])


def test_prepared_artifact_reuses_exact_sources_and_tamper_rebuilds(preparation):
    root, build, calls = preparation
    first = testapp.ensure(root)
    assert testapp.ensure(root) == first and len(calls) == 1
    first.write_bytes(b'tampered')
    assert testapp.ensure(root) == first and len(calls) == 2
    first.with_name('receipt.json').write_text('{"version":1,"path":"/untrusted"}')
    assert testapp.ensure(root) == first and len(calls) == 3
    build.staged = build.staged.parent.parent / 'source-b' / 'app'
    build.staged.mkdir(parents=True)
    second = testapp.ensure(root)
    assert second != first and len(calls) == 4
    assert first.read_bytes() != second.read_bytes()


def test_concurrent_preparation_attaches_to_one_build(preparation):
    root, _, calls = preparation
    admissions = []
    @contextmanager
    def admit():
        admissions.append(True)
        yield
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: testapp.ensure(root, admit=admit), range(2)))
    assert results[0] == results[1] and len(calls) == 1 and admissions == [True]


def test_cache_parent_links_are_never_followed(preparation):
    root, build, calls = preparation
    cache = build.target.parent.parent
    outside = root / 'outside'
    outside.mkdir()
    (cache / 'app-tests').symlink_to(outside, target_is_directory=True)
    with pytest.raises(testapp.testnode.BuildError, match='directory links'):
        testapp.ensure(root)
    assert not calls and list(outside.iterdir()) == []


def test_unexpected_compiler_artifacts_are_not_executable(preparation):
    root, build, _ = preparation
    for target in ['other-app', 'poolhouse-app']:
        output = json.dumps({'reason': 'compiler-artifact', 'target': {'name': target, 'kind': ['bin']},
                             'package_id': 'path+file:///app#other@1', 'profile': {'test': True},
                             'executable': str(root / 'outside')})
        with pytest.raises(testapp.testnode.BuildError):
            testapp.executable(output, build.target)


def test_cancelled_compilation_terminates_owned_process_group(monkeypatch):
    calls = []
    process = SimpleNamespace(returncode=None)
    def communicate(**kwargs):
        calls.append('communicate')
        if len(calls) == 1:
            raise KeyboardInterrupt
        return '', ''
    process.communicate = communicate
    monkeypatch.setattr(testapp, 'start_process', lambda *args, **kwargs: process)
    monkeypatch.setattr(testapp, 'terminate_process_group', lambda p, **kw: calls.append('terminated'))
    with pytest.raises(KeyboardInterrupt):
        testapp.run(['cargo'], '.', {})
    assert calls == ['communicate', 'terminated', 'communicate']
