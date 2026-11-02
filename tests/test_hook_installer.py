"""Explicit installation stays within its source repository and preserves foreign hooks."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MARKER = 'poolhouse.hooks.repository'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture
def source(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / 'source'
    subprocess.run(['git', 'init', '-q', '-b', '0.9dev', str(root)], check=True)
    scripts = root / 'scripts'
    (scripts / 'hooks').mkdir(parents=True)
    for name in ('install-hooks.py', 'install-hooks.sh'):
        shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
    (scripts / 'envcheck.py').write_text('def report(purposes):\n    return 0, ""\n')
    for name in ('pre-commit', 'commit-msg', 'pre-push', 'post-merge', 'post-commit'):
        (scripts / 'hooks' / name).write_text('#!/bin/sh\nexit 0\n')
    shutil.copyfile(ROOT / 'scripts/hooks/pre-push', scripts / 'hooks/pre-push')
    return root


def install(source, cwd=None, shell=False):
    name = 'install-hooks.sh' if shell else 'install-hooks.py'
    return subprocess.run(['sh' if shell else sys.executable, str(source / 'scripts' / name)],
                          cwd=cwd or source, env={**os.environ, 'PYTHON': sys.executable}, text=True, capture_output=True)


def snapshot(repo):
    return {p.name: ('link', p.readlink().as_posix()) if p.is_symlink() else (p.read_bytes(), p.stat().st_mode)
            for p in (repo / '.git/hooks').iterdir()}


@pytest.mark.parametrize('shell', [False, True])
def test_foreign_checkout_refused_before_environment_check_or_writes(source, tmp_path, shell):
    (source / 'scripts/envcheck.py').write_text('raise AssertionError("environment imported")\n')
    foreign = tmp_path / 'foreign'
    subprocess.run(['git', 'init', '-q', str(foreign)], check=True)
    before, config = snapshot(foreign), (foreign / '.git/config').read_bytes()
    done = install(source, foreign, shell)
    assert done.returncode != 0 and 'own checkout' in done.stderr and 'Traceback' not in done.stderr
    assert snapshot(foreign) == before and (foreign / '.git/config').read_bytes() == config


@pytest.mark.parametrize('shell', [False, True])
def test_own_installer_sets_scope_and_preserves_foreign_broken_hook(source, shell):
    foreign = source / '.git/hooks/commit-msg'
    foreign.symlink_to('../../scripts/hooks/foreign-missing')
    done = install(source, shell=shell)
    assert done.returncode == 0, done.stderr
    common = Path(git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    assert git(source, 'config', '--local', '--get', MARKER) == str(common)
    assert foreign.is_symlink() and foreign.readlink().as_posix() == '../../scripts/hooks/foreign-missing'
    assert 'poolhouse managed hook' in (common / 'hooks/pre-push').read_text()


@pytest.mark.parametrize('extra', [False, True])
def test_marker_text_or_extra_commands_do_not_own_a_foreign_hook(source, extra):
    hook = source / '.git/hooks/pre-commit'
    text = '#!/bin/sh\n# poolhouse managed hook\n'
    text += f'export PYTHON=/unused\nexec sh {source}/scripts/hooks/pre-commit "$@"; echo foreign\n' if extra else 'exit 0\n'
    hook.write_text(text)
    assert install(source).returncode == 0
    assert hook.read_text() == text


def test_exact_owned_legacy_symlink_and_wrapper_can_be_refreshed(source):
    hook = source / '.git/hooks/pre-push'
    hook.symlink_to('../../scripts/hooks/pre-push')
    assert install(source).returncode == 0
    before = hook.read_text()
    assert not hook.is_symlink() and 'poolhouse managed hook' in before
    assert install(source).returncode == 0 and hook.read_text() == before


@pytest.mark.parametrize('level', ['--local', '--global'])
def test_custom_hooks_directory_unchanged_and_refused(source, tmp_path, level):
    external = tmp_path / 'custom-hooks'
    external.mkdir()
    (external / 'pre-push').write_text('foreign hook')
    git(source, 'config', level, 'core.hooksPath', str(external))
    before, config = snapshot(source), (source / '.git/config').read_bytes()
    done = install(source)
    assert done.returncode != 0 and 'hooks directory' in done.stderr
    assert snapshot(source) == before and (source / '.git/config').read_bytes() == config
    assert (external / 'pre-push').read_text() == 'foreign hook'
    assert git(source, 'config', level, '--get', 'core.hooksPath') == str(external)


def test_unavailable_commit_environment_changes_neither_hooks_nor_scope(source):
    (source / 'scripts/envcheck.py').write_text('def report(purposes):\n    return 1, "commit dependencies unavailable"\n')
    before, config = snapshot(source), (source / '.git/config').read_bytes()
    done = install(source)
    assert done.returncode == 1 and 'commit dependencies unavailable' in done.stderr
    assert snapshot(source) == before and (source / '.git/config').read_bytes() == config


def test_interpreter_shell_expansion_is_foreign_even_with_the_exact_source(source):
    hook = source / '.git/hooks/pre-commit'
    text = ('#!/bin/sh\n# poolhouse managed hook\nexport PYTHON=/unused/$(id)\n'
            f'exec sh {source}/scripts/hooks/pre-commit "$@"\n')
    hook.write_text(text)
    assert install(source).returncode == 0 and hook.read_text() == text


def test_installed_own_scope_keeps_main_guard_and_quotes_checkout_spaces(source):
    moved = source.with_name('source with spaces')
    source.rename(moved)
    assert install(moved).returncode == 0
    git(moved, '-c', 'user.name=hook-fixture', '-c', 'user.email=hook@agents.invalid',
        'commit', '--allow-empty', '-qm', 'chore: fixture')
    sha = git(moved, 'rev-parse', 'HEAD')
    records = moved.parent / 'push-input'
    records.write_text(f'HEAD {sha} refs/heads/main {"0" * 40}\n')
    done = subprocess.run(['git', 'hook', 'run', f'--to-stdin={records}', 'pre-push', '--', 'origin', 'unused'], cwd=moved,
                          text=True, capture_output=True,
                          env={**os.environ, 'CLAUDECODE': '1'})
    assert done.returncode != 0 and 'refused: refs/heads/main' in done.stderr


def test_copied_hook_allows_real_local_git_main_and_feature_pushes_without_scope(source, tmp_path):
    foreign, remote = tmp_path / 'consumer', tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(foreign)], check=True)
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    git(foreign, '-c', 'user.name=hook-fixture', '-c', 'user.email=hook@agents.invalid',
        'commit', '--allow-empty', '-qm', 'chore: fixture')
    installed = foreign / '.git/hooks/pre-push'
    shutil.copyfile(ROOT / 'scripts/hooks/pre-push', installed)
    installed.chmod(0o755)
    git(foreign, 'remote', 'add', 'origin', str(remote))
    for branch in ('main', 'feature/library-user'):
        git(foreign, 'push', '-q', 'origin', f'HEAD:refs/heads/{branch}')
        assert git(remote, 'rev-parse', f'refs/heads/{branch}') == git(foreign, 'rev-parse', 'HEAD')


def test_git_directory_override_cannot_make_a_foreign_cwd_installable(source, tmp_path, monkeypatch):
    foreign = tmp_path / 'foreign-override'
    subprocess.run(['git', 'init', '-q', str(foreign)], check=True)
    before, own = snapshot(foreign), snapshot(source)
    monkeypatch.setenv('GIT_DIR', str(source / '.git'))
    done = install(source, foreign)
    assert done.returncode != 0 and 'own checkout' in done.stderr
    assert snapshot(foreign) == before and snapshot(source) == own
