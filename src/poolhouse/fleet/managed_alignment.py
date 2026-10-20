"""Offline synchronization of already-installed app-owned compute at an idle boundary."""
from __future__ import annotations

import contextlib
import math
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from poolhouse import maintenance, runtime
from poolhouse.lock import Busy
from poolhouse.log import warn
from poolhouse.net import packages
from poolhouse.redact.secrets import env_secrets, redact
from poolhouse.serve import broker_wire, process as processes
from poolhouse.serve.process import pid_exists, started_at

from . import compute_artifact
from .environment import CATALOG
from .managed_compute import process_environment
from .runtime_wheel import cache_wheel, wheel_commit

_retiring = None


def catalogue(environment):
    """Fixed keys identify owned locations, never paths read from a transaction."""
    found = {'default': environment}
    for library in CATALOG:
        target = environment.for_library(library)
        if target.path != environment.path:
            found[library.name] = target
    return found


def targets(environment):
    return list(catalogue(environment).values())

def managed_busy(environments):
    """Notice surviving owned children after their launching daemon lost its reader."""
    interpreters = {str(target.python.absolute()): target.python for target in environments}
    coordinator = psutil.Process()
    own = (os.getpid(), coordinator.create_time())
    username = coordinator.username()
    for process, info in processes.readable(['pid', 'cmdline', 'username'], uncertain=True):
        try:
            born = process.create_time()
            if (process.pid, born) == own:
                continue
            arguments = info['cmdline']
            if not arguments:
                # Only a proven different executable can clear an unreadable owned child.
                executable = process.exe()
                if executable and any(Path(executable).samefile(path) for path in interpreters.values() if path.exists()):
                    return True
                if not executable and info['username'] in (None, username):
                    return True
                continue
            if arguments[0] not in interpreters:
                continue
            if hasattr(os, 'getuid') and process.uids().real != os.getuid():
                return True
            executable = process.exe()
            if (not executable or not Path(executable).samefile(interpreters[arguments[0]])
                    or process.create_time() != born):
                return True
            return True
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError, SystemError):
            arguments = info['cmdline']
            if (info['username'] in (None, username)
                    or (arguments and arguments[0] in interpreters)):
                return True
    return False


def broker_idle():
    """Seal an empty actual broker and confirm that its process generation has exited."""
    global _retiring
    if _retiring is not None:
        pid, born = _retiring
        if pid_exists(pid) and started_at(pid) in (None, born):
            return False
        _retiring = None
    record = broker_wire._record()
    if not record:
        return True
    pid, born = record.get('pid'), record.get('pid_started')
    if (type(pid) is not int or pid <= 0 or type(born) not in (int, float)
            or not math.isfinite(born) or born <= 0):
        return False
    if not pid_exists(pid):
        return True
    actual = started_at(pid)
    if actual is None:
        return False
    if actual != born:
        return True
    answering = broker_wire._answering()
    if not answering or (answering.get('pid'), answering.get('pid_started')) != (pid, born):
        return False
    reply = broker_wire._send(record, {'op': 'idle-retire', 'pid': os.getpid()}, timeout=5.0,
                              expected={'pid': pid, 'started': born})
    if reply.get('ok') is not True or reply.get('retiring') is not True:
        return False
    _retiring = (pid, born)
    return False  # A socket acknowledgment is not proof that the process has exited.


@contextlib.contextmanager
def idle(environment, quiet):
    """Never hold intent while waiting for an existing child's first broker request."""
    if not quiet():
        raise Busy('Existing work is still using its runtime')
    with maintenance.intent() as writer:
        if not quiet() or not broker_idle() or managed_busy(targets(environment)):
            raise Busy('Existing managed work is still using its runtime')
        with writer.exclusive():
            if not quiet() or managed_busy(targets(environment)):
                raise Busy('Existing managed work is still using its runtime')
            yield


def _bundle(environment):
    expected = compute_artifact.expected()
    selected = runtime.selected()
    if selected is not None and (selected.commit != expected['source'] or selected.prefix != Path(sys.prefix)):
        raise OSError('Managed code synchronization must use the currently selected app')
    if selected is None and not getattr(sys, 'frozen', False):
        raise OSError('Managed code synchronization requires the selected installed app')
    directory = environment.wheels()
    wheels = list(directory.glob('poolhouse-*.whl')) if directory else []
    if len(wheels) != 1 or wheel_commit(wheels[0]) != expected['source']:
        raise OSError('The installed app has no matching immutable base wheel')
    base = wheels[0]
    if compute_artifact.from_wheel(base) != expected:
        raise OSError('The base wheel does not match the installed app compute manifest')
    compute_artifact.verify(base.parent / expected['filename'], expected, expected['source'])
    return base, expected


def _transaction(record, known):
    if (not isinstance(record, dict) or set(record) != {'version', 'source', 'artifact', 'targets'}
            or type(record['version']) is not int or record['version'] != 1
            or not isinstance(record['source'], str) or not re.fullmatch('[0-9a-f]{40}', record['source'])
            or not isinstance(record['artifact'], str) or not re.fullmatch('[0-9a-f]{64}', record['artifact'])
            or not isinstance(record['targets'], dict) or not record['targets']
            or not set(record['targets']).issubset(known)):
        raise ValueError('Managed maintenance transaction record is invalid')
    for versions in record['targets'].values():
        if (not isinstance(versions, dict) or set(versions) != {'poolhouse', compute_artifact.DISTRIBUTION}
                or any(not isinstance(value, str) or not 0 < len(value) < 128 for value in versions.values())):
            raise ValueError('Managed maintenance transaction record has no installed bundle evidence')
    return record


def _replace(environment, base, expected):
    result = packages.run(environment.python, ['install', '--no-index', '--no-deps', '--no-compile',
                          '--force-reinstall', str(base)], timeout=3600, env=process_environment())
    if result.returncode:
        raise OSError('The app-owned managed runtime could not be synchronized')
    result = compute_artifact.install(environment.python, base.parent, environment=process_environment())
    if result.returncode:
        raise OSError('The app-owned compute artifact could not be synchronized')
    cache_wheel(base, expected['source'], prefix=environment.path)
    environment._cache.clear()


def synchronize(environment):
    """Continue only existing fixed app-owned bundles, without new environments or dependencies."""
    known = catalogue(environment)
    record = maintenance.transaction_record()
    recovered = record is not None
    if recovered:
        _transaction(record, known)
    planned, pending = {}, []
    distribution_names = {'poolhouse', compute_artifact.DISTRIBUTION}
    for key, target in known.items():
        if recovered and key not in record['targets']:
            continue
        if not target.exists:
            if recovered:
                raise OSError('An interrupted managed environment is unavailable for verified repair')
            continue
        with target._mutation(['core']):
            have = target.installed()
            if recovered:
                if not distribution_names.intersection(have):
                    raise OSError('The interrupted managed bundle has no surviving installed evidence')
            elif not distribution_names.issubset(have):
                continue
            base, expected = _bundle(target)  # Current imported app, NEVER record-selected code.
            planned[key] = (target, base, expected, {name: have.get(name, '') for name in distribution_names})
            try:
                target.require_compute()
            except (OSError, ValueError, subprocess.SubprocessError):
                pending.append(key)
    if pending:
        if not recovered:
            expected = planned[pending[0]][2]
            record = {'version': 1, 'source': expected['source'], 'artifact': expected['sha256'],
                      'targets': {key: item[3] for key, item in planned.items()}}
            maintenance.mark_unverified(record)
        for key in pending:
            target, base, expected, _ = planned[key]
            with target._mutation(['core']):
                _replace(target, base, expected)
    for target, _, _, _ in planned.values():
        target.require_compute()
    if record is not None:
        maintenance.mark_verified()
    return bool(pending)

class Alignment:
    """Keep admission pending until the existing managed installation passes verification."""
    def __init__(self, environment):
        self.environment = environment
        installed = getattr(sys, 'frozen', False) or Path(compute_artifact.__file__).with_name('built-from').is_file()
        self.pending = bool(installed and any(target.exists for target in targets(environment)))
        self.error = ''
        self.done = threading.Event()
        self.thread = None

    def start(self, runner, quiet, admission):
        runner.managed_alignment_pending = self.pending
        if not self.pending or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, args=(runner, quiet, admission),
                                       daemon=True, name='managed-runtime-alignment')
        self.thread.start()

    def _run(self, runner, quiet, admission):
        while not self.done.is_set():
            try:
                if not quiet():
                    raise Busy('Existing work is still using its runtime')
                with admission(), idle(self.environment, quiet):
                    synchronize(self.environment)
                self.pending = runner.managed_alignment_pending = False
                return
            except Busy:
                self.done.wait(.2)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self.error = redact(str(error), env_secrets(os.environ))[0][:1000]
                warn('Managed runtime synchronization stopped; new compute remains paused: ' + self.error)
                return  # No unverified admission and no blind retry after a mutation failed.

    def close(self):
        self.done.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join()  # A started package write must settle before the daemon exits.


@contextlib.contextmanager
def code_update_admission(daemon):
    with daemon.update_admission(), idle(daemon.environment, daemon.nothing_running):
        yield


def may_start(daemon):
    """Admit new work only after managed alignment, benchmark and schedule checks."""
    if daemon.managed_alignment.pending:
        return (False, 'managed runtime synchronization is pending')
    if daemon.bench_host and daemon.bench_host.measuring():
        return (False, 'a benchmark is measuring on this machine')
    return daemon.schedule.may_start()


def background_busy(daemon) -> bool:
    return (bool(daemon.web and daemon.initial_setup and not daemon.settings.setup_done) or bool(daemon.runner.status()["busy"]) or any(row.state == "getting" for row in daemon.downloads.active())
            or bool(daemon.interface and daemon.interface.setup_jobs and daemon.interface.setup_jobs.active()))
