"""Prepare source-keyed native app test executables before pytest's timed cases."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import testnode

from poolhouse.files import write_json, writing
from poolhouse.lock import only_one
from poolhouse.platform import start_process, terminate_process_group

ENV = 'DEV_TEST_APP_TESTS'


def needed(root, files, facts=None):
    if not files:
        return True
    import testreuse_key as keys
    closures = keys.Closures(root, facts)
    names = {*closures.closure(files)[0], *(c for f in files for c in keys.conftests(root, f))}
    closures.save()
    return any(ENV in keys.text_of(root / name) for name in names if name.startswith('tests/'))


def regular(path):
    try:
        return (not any(item.is_symlink() for item in (path, *path.parents))
                and stat.S_ISREG(path.lstat().st_mode) and path.lstat().st_nlink == 1)
    except FileNotFoundError:
        return False


def checksum(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def run(argv, work, environment):
    process = start_process(argv, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=testnode.BUILD_TIMEOUT_S)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            terminate_process_group(process, force=True)
        process.communicate()
        raise
    if process.returncode:
        raise testnode.BuildError('The native app test build failed:\n' + stderr[-2000:])
    return stdout


def executable(output, target):
    for line in output.splitlines():
        row = json.loads(line)
        spec = row.get('target', {})
        if (row.get('reason') == 'compiler-artifact' and spec.get('name') == 'poolhouse-app'
                and spec.get('kind') == ['bin'] and row.get('profile', {}).get('test') is True
                and row.get('package_id', '').rsplit('#', 1)[-1].startswith('poolhouse-app@')):
            path = Path(row.get('executable') or '')
            if (path.is_absolute() and path.parent == target / 'debug' / 'deps'
                    and path.name.startswith('poolhouse_app-') and regular(path)):
                return path
    raise testnode.BuildError('Cargo did not produce the expected native app test executable')


def ensure(root, *, admit=contextlib.nullcontext, say=lambda text: None):
    build = testnode.shared_build(root / 'app')
    key = hashlib.sha256((build.staged.parent.name + checksum(Path(__file__))).encode()).hexdigest()
    base = build.target.parent.parent / 'app-tests' / key
    binary, receipt = base / 'poolhouse-app-tests', base / 'receipt.json'
    if any(item.is_symlink() for item in (base, *base.parents)):
        raise testnode.BuildError('The native app test cache cannot contain directory links')
    base.mkdir(parents=True, exist_ok=True)
    with only_one(build.target / 'app-tests.lock', announce=say, note='preparing native app tests'):
        if regular(binary) and regular(receipt):
            try:
                row = json.loads(receipt.read_text())
            except (ValueError, OSError):
                row = None
            if row == {'version': 1, 'key': key, 'sha256': checksum(binary)}:
                return binary
        say('test: preparing the native app test executable for ' + key[:20])
        with tempfile.TemporaryDirectory(prefix='poolhouse-app-build-') as work, admit():
            temporary = Path(work)
            isolated = temporary / 'home'
            isolated.mkdir()
            environment = {name: value for name, value in os.environ.items()
                           if not name.startswith(('CARGO_', 'RUSTFLAGS', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER'))}
            account = testnode.account()
            environment.update(HOME=str(isolated), TMPDIR=str(temporary), POOLHOUSE_HOME=str(temporary / 'state'),
                               RUSTUP_HOME=str(account / '.rustup'), CARGO_HOME=str(account / '.cargo'),
                               CARGO_TARGET_DIR=str(build.target), CARGO_BUILD_JOBS='1')
            argv = [testnode.tool('cargo'), 'test', '--no-run', '--locked', '--offline',
                    '-p', 'poolhouse-app', '--bin', 'poolhouse-app', '--message-format=json']
            made = executable(run(argv, build.staged, environment), build.target)
            with writing(binary) as staged, made.open('rb') as source:
                with staged.open('wb') as destination:
                    shutil.copyfileobj(source, destination)
                staged.chmod(0o700)
            write_json(receipt, {'version': 1, 'key': key, 'sha256': checksum(binary)})
    return binary
