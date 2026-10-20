"""App-owned wheels move together offline without interrupting admitted work."""
import contextlib
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import home, maintenance
from poolhouse.fleet import compute_artifact as artifact, managed_alignment as alignment
from poolhouse.fleet.environment import Environment
from poolhouse.fleet.wheel_provenance import stamp, write_wheel
from poolhouse.lock import Busy


@pytest.fixture(autouse=True)
def isolated_barrier(tmp_path, monkeypatch):
    monkeypatch.setattr(home, 'state', lambda *parts: tmp_path.joinpath('state', *parts))
    monkeypatch.setattr(alignment, '_retiring', None)


def bundle(root, revision):
    root.mkdir()
    source = root / 'source'
    for name in ('backend', 'train', 'bench', 'gym', 'world'):
        path = source / 'src/poolhouse' / name / '__init__.py'
        path.parent.mkdir(parents=True)
        path.write_text('VALUE = 42\n')
    for name in artifact.EXTRA_FILES:
        path = source / 'src' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('VALUE = 42\n')
    base = root / 'poolhouse-0.1.0-py3-none-any.whl'
    write_wheel(base, {'poolhouse/__init__.py': b'', 'poolhouse/graph/__init__.py': b'',
                      'poolhouse-0.1.0.dist-info/METADATA': b'Name: poolhouse\nVersion: 0.1.0\n',
                      'poolhouse-0.1.0.dist-info/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
                      'poolhouse-0.1.0.dist-info/RECORD': b''})
    companion = artifact.build(source, base, revision)
    stamp(base, revision, source)
    return base, companion, artifact.from_wheel(base)


@pytest.mark.parametrize('crash', ['none', 'base-uninstall', 'base-installed', 'companion-uninstall'])
def test_real_installed_bundle_updates_offline_and_preserves_other_distributions(tmp_path, monkeypatch, crash):
    old, old_compute, _ = bundle(tmp_path / 'old', 'a' * 40)
    base, _, manifest = bundle(tmp_path / 'new', 'b' * 40)
    environment = Environment(tmp_path / 'managed')
    subprocess.run([sys.executable, '-m', 'venv', str(environment.path)], check=True, capture_output=True)
    sentinel = tmp_path / 'sentinel_dependency-7.3-py3-none-any.whl'
    write_wheel(sentinel, {'sentinel_dependency/__init__.py': b'VALUE = 7\n',
                          'sentinel_dependency-7.3.dist-info/METADATA': b'Name: sentinel-dependency\nVersion: 7.3\n',
                          'sentinel_dependency-7.3.dist-info/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
                          'sentinel_dependency-7.3.dist-info/RECORD': b''})
    subprocess.run([str(environment.python), '-m', 'pip', 'install', '--no-deps', '--no-index', '--no-compile',
                    str(old), str(old_compute), str(sentinel)], check=True, capture_output=True)
    monkeypatch.setattr(artifact, 'expected', lambda: manifest)
    monkeypatch.setattr(environment, 'wheels', lambda: base.parent)
    monkeypatch.setattr(alignment.runtime, 'selected', lambda: SimpleNamespace(commit=manifest['source'], prefix=Path(sys.prefix)))
    calls = []
    run = alignment.packages.run
    interrupted = []
    def tracked(python, args, **kwargs):
        if args[0] == 'install':
            calls.append(args)
            is_base = Path(args[-1]).name.startswith('poolhouse-')
            if not interrupted and ((crash == 'base-uninstall' and is_base)
                                    or (crash == 'companion-uninstall' and not is_base)):
                interrupted.append(crash)
                package = 'poolhouse' if is_base else artifact.DISTRIBUTION
                subprocess.run([str(python), '-m', 'pip', 'uninstall', '-y', package], check=True, capture_output=True)
                raise SystemExit('simulated updater crash after uninstall')
            result = run(python, args, **kwargs)
            if not interrupted and crash == 'base-installed' and is_base:
                interrupted.append(crash)
                raise SystemExit('simulated updater crash after base install')
            return result
        return run(python, args, **kwargs)
    monkeypatch.setattr(alignment.packages, 'run', tracked)
    if crash != 'none':
        with pytest.raises(SystemExit), maintenance.intent() as writer, writer.exclusive():
            alignment.synchronize(environment)
        with pytest.raises(OSError, match='repair is pending'), maintenance.read():
            pytest.fail('A crash must not expose partially installed code')
    with maintenance.intent() as writer, writer.exclusive():
        assert alignment.synchronize(environment)
        count = len(calls)
        assert not alignment.synchronize(environment)
        assert len(calls) == count
    assert len(calls) == (2 if crash == 'none' else 4 if crash == 'companion-uninstall' else 3)
    assert all(args[1:5] == ['--no-index', '--no-deps', '--no-compile', '--force-reinstall'] for args in calls)
    assert environment.installed()['sentinel-dependency'] == '7.3'
    assert environment.require_compute() == manifest['sha256']
    assert (environment.path / 'poolhouse-wheels' / manifest['source'] / base.name).is_file()


def test_absent_environment_or_companion_never_installs(tmp_path, monkeypatch):
    environment = Environment(tmp_path)
    monkeypatch.setattr(alignment.packages, 'run', lambda *a, **k: pytest.fail('No implicit setup'))
    with maintenance.intent() as writer, writer.exclusive():
        assert not alignment.synchronize(environment)
    environment.python.parent.mkdir(parents=True)
    environment.python.touch()
    monkeypatch.setattr(environment, 'installed', lambda: {'poolhouse': '0.1'})
    with maintenance.intent() as writer, writer.exclusive():
        assert not alignment.synchronize(environment)


@pytest.mark.parametrize('fault', ['source', 'manifest', 'companion'])
def test_invalid_current_bundle_is_refused_before_any_install(tmp_path, monkeypatch, fault):
    base, companion, manifest = bundle(tmp_path / 'new', 'b' * 40)
    environment = Environment(tmp_path / 'managed')
    environment.python.parent.mkdir(parents=True)
    environment.python.touch()
    monkeypatch.setattr(environment, 'installed', lambda: {'poolhouse': '0.1', artifact.DISTRIBUTION: '0.1'})
    monkeypatch.setattr(environment, 'wheels', lambda: base.parent)
    monkeypatch.setattr(artifact, 'expected', lambda: manifest)
    selected = SimpleNamespace(commit=manifest['source'], prefix=Path(sys.prefix))
    if fault == 'source':
        selected.commit = 'c' * 40
    elif fault == 'manifest':
        manifest = dict(manifest, sha256='c' * 64)
    else:
        companion.write_bytes(companion.read_bytes() + b'tampered')
    monkeypatch.setattr(alignment.runtime, 'selected', lambda: selected)
    monkeypatch.setattr(alignment.packages, 'run', lambda *a, **k: pytest.fail('No install before integrity proof'))
    with pytest.raises((ValueError, OSError)), maintenance.intent() as writer, writer.exclusive():
        alignment.synchronize(environment)


def test_busy_reader_releases_intent_so_existing_crossprocess_work_can_continue(tmp_path, monkeypatch):
    environment = Environment(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def work():
        with maintenance.read():
            entered.set()
            assert release.wait(5)
    child = threading.Thread(target=work)
    child.start()
    assert entered.wait(5)
    try:
        with pytest.raises(Busy), alignment.idle(environment, lambda: True):
            pytest.fail('A live reader prevents mutation')
        # The failed turn must not retain writer intent and deadlock a first claim.
        with maintenance.read():
            pass
    finally:
        release.set()
        child.join(5)
    assert not child.is_alive()


def test_pending_alignment_failure_stays_closed_without_repeated_install(tmp_path, monkeypatch):
    environment = Environment(tmp_path)
    state = alignment.Alignment(environment)
    state.pending = True
    runner = SimpleNamespace(managed_alignment_pending=True)
    calls = []
    def failed(target):
        calls.append(target)
        raise OSError('failed fixed installation')
    monkeypatch.setattr(alignment, 'idle', lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(alignment, 'targets', lambda target: [target])
    monkeypatch.setattr(alignment, 'synchronize', failed)
    state._run(runner, lambda: True, contextlib.nullcontext)
    assert len(calls) == 1 and state.pending and runner.managed_alignment_pending
    assert state.error == 'failed fixed installation'


def test_broker_ack_must_be_followed_by_actual_process_generation_exit(monkeypatch):
    record = {'pid': 41234, 'pid_started': 123.0}
    monkeypatch.setattr(alignment.broker_wire, '_record', lambda: record)
    monkeypatch.setattr(alignment.broker_wire, '_answering', lambda: record)
    calls = []
    monkeypatch.setattr(alignment.broker_wire, '_send', lambda *a, **k: calls.append(a) or {'ok': True, 'retiring': True})
    monkeypatch.setattr(alignment, 'pid_exists', lambda pid: True)
    monkeypatch.setattr(alignment, 'started_at', lambda pid: 123.0)
    assert not alignment.broker_idle()
    assert not alignment.broker_idle()
    assert len(calls) == 1
    monkeypatch.setattr(alignment, 'started_at', lambda pid: 124.0)
    monkeypatch.setattr(alignment.broker_wire, '_record', dict)
    assert alignment.broker_idle()


def test_actual_surviving_managed_child_after_parent_exit_is_not_mistaken_for_idle(tmp_path):
    import json

    import psutil

    python = tmp_path / 'managed-python'
    python.symlink_to(sys.executable)
    source = Path(maintenance.__file__).parents[1]
    script = (f"import sys;sys.path.insert(0,{str(source)!r});"
              "import os,json,subprocess,psutil;from pathlib import Path;"
              "from poolhouse import home,maintenance;"
              f"home.state=lambda *parts:Path({str(tmp_path / 'state')!r}).joinpath(*parts);"
              f"child=maintenance.spawn(subprocess.Popen,[{str(python)!r},'-c','import time;time.sleep(60)'],"
              "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
              "print(json.dumps([child.pid,psutil.Process(child.pid).create_time()]),flush=True);os._exit(0)")
    parent = subprocess.run([sys.executable, '-B', '-I', '-c', script], capture_output=True, text=True, check=True, timeout=5)
    pid, born = json.loads(parent.stdout)
    child = psutil.Process(pid)
    try:
        assert child.create_time() == born
        # Its launcher's OS lock died; the independent actual-process proof must stop sync.
        with maintenance.intent() as writer, writer.exclusive():
            pass
        assert alignment.managed_busy([SimpleNamespace(python=python)])
        assert not alignment.managed_busy([SimpleNamespace(python=tmp_path / 'unrelated')])
    finally:
        if child.is_running() and child.create_time() == born:
            child.terminate()
            child.wait(timeout=5)


def test_real_loopback_broker_retirement_supplies_connected_caller_proof(tmp_path, monkeypatch):
    import os

    from poolhouse.serve import broker_wire
    from poolhouse.serve.broker import Broker

    table = SimpleNamespace(listing=lambda: {'leases': []})
    broker = Broker(manager=SimpleNamespace(table=table, state_file=tmp_path / 'servers.json'), scan=lambda: [])
    monkeypatch.setattr(broker_wire.broker_runtime, 'snapshot', dict)
    server = broker_wire._Server(broker)
    record = {'pid': os.getpid(), 'pid_started': alignment.started_at(os.getpid()),
              'port': server.server_address[1], 'token': server.token}
    monkeypatch.setattr(broker_wire, '_record', lambda: record)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        assert not alignment.broker_idle()  # This test broker's process is still ourselves.
        assert server.closing.is_set() and broker._retiring
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert not thread.is_alive()


@pytest.mark.parametrize('fault', ['unreadable-command', 'unreadable-executable', 'unknown-owner', 'reused-pid'])
def test_uncertain_owned_managed_process_is_not_assumed_idle(tmp_path, monkeypatch, fault):
    import os

    import psutil

    python = tmp_path / 'managed-python'
    python.symlink_to(sys.executable)
    current = psutil.Process()
    calls = []
    class Process:
        pid = current.pid + 100
        def __init__(self):
            self.info = {'cmdline': [str(python)], 'username': current.username()}
        def create_time(self):
            calls.append('born')
            return 1.0 if len(calls) == 1 else 2.0
        def exe(self):
            if fault in ('unreadable-executable', 'unknown-owner'):
                raise psutil.AccessDenied(self.pid)
            return sys.executable
        def uids(self):
            return SimpleNamespace(real=os.getuid())
    process = Process()
    if fault in ('unreadable-command', 'unknown-owner'):
        process.info = dict(process.info, cmdline=None)
    if fault == 'unknown-owner':
        process.info['username'] = None
    monkeypatch.setattr(alignment.processes, 'readable', lambda *a, **k: [(process, process.info)])
    assert alignment.managed_busy([SimpleNamespace(python=python)])


def test_close_waits_for_started_sync_to_settle_before_process_exit(tmp_path, monkeypatch):
    state = alignment.Alignment(Environment(tmp_path))
    state.pending = True
    runner = SimpleNamespace(managed_alignment_pending=True)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(alignment, 'idle', lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(alignment, 'targets', lambda target: [target])
    def synchronize(target):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(alignment, 'synchronize', synchronize)
    state.start(runner, lambda: True, contextlib.nullcontext)
    assert entered.wait(5)
    waiter = threading.Thread(target=lambda: (state.close(), closed.set()))
    waiter.start()
    assert not closed.wait(.05)
    release.set()
    assert closed.wait(5)
    waiter.join(5)
    assert not runner.managed_alignment_pending


@pytest.mark.parametrize('fault', ['unknown-target', 'no-survivor'])
def test_transaction_record_never_creates_target_or_installs_without_surviving_evidence(tmp_path, monkeypatch, fault):
    environment = Environment(tmp_path)
    environment.python.parent.mkdir(parents=True)
    environment.python.touch()
    monkeypatch.setattr(environment, 'installed', dict)
    monkeypatch.setattr(alignment.packages, 'run', lambda *a, **k: pytest.fail('No record-selected setup'))
    record = {'version': 1, 'source': 'a' * 40, 'artifact': 'a' * 64,
              'targets': {'default' if fault == 'no-survivor' else 'untrusted':
                          {'poolhouse': '0.1', artifact.DISTRIBUTION: '0.1'}}}
    with maintenance.intent() as writer, writer.exclusive():
        maintenance.mark_unverified(record)
        with pytest.raises((OSError, ValueError)):
            alignment.synchronize(environment)
    with pytest.raises(OSError, match='repair is pending'), maintenance.read():
        pytest.fail('Unverified state must remain denied')
