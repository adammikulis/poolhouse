"""An owned inert bridge becomes a data writer only after its private activation.

The factory owns source authorization and durable delivery. This process owns no
device credential and never chooses a receiver, source, executable or callback.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import math
import os
import secrets
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path

from poolhouse import home, maintenance, unix_socket
from poolhouse.platform import start_process
from poolhouse.studio.store import digest

from . import producer_bridge, producer_control, tls
from .environment import Environment
from .framing import HANDSHAKE_S, SOCKET_S
from .job_records import process_started
from .managed_compute import process_environment


def _generation(value):
    return (isinstance(value, dict) and set(value) == {'pid', 'started'}
            and type(value['pid']) is int and value['pid'] > 0
            and type(value['started']) in (int, float)
            and math.isfinite(value['started']) and value['started'] > 0)


def _live(value):
    return _generation(value) and process_started(value['pid']) == value['started']


def _command(fd):
    environment = Environment(home.state('traind'))
    environment.require_current_runtime()
    return [str(environment.python), '-I', '-B', '-c',
            'from poolhouse.fleet.producer_process import child; child(' + str(fd) + ')']


def _control(value):
    keys = {'certificate', 'control_path', 'daemon', 'pool', 'runtime'}
    if (not isinstance(value, dict) or set(value) != keys
            or not isinstance(value['daemon'], dict)
            or set(value['daemon']) != {'pid', 'started', 'instance'}
            or not _generation({key: value['daemon'][key] for key in ('pid', 'started')})):
        raise ValueError('The fixed producer control binding is invalid')
    for item in (value['pool'], value['runtime'], value['daemon']['instance']):
        if not isinstance(item, str) or len(item) != 64 or any(c not in '0123456789abcdef' for c in item):
            raise ValueError('The producer control identity is invalid')
    path = value['control_path']
    if (not isinstance(path, str) or not Path(path).is_absolute() or '..' in Path(path).parts
            or '\0' in path or len(os.fsencode(path)) > 103):
        raise ValueError('The producer control location is invalid')
    if not isinstance(value['certificate'], str) or not 0 < len(value['certificate']) <= 16384:
        raise ValueError('The producer control certificate is invalid')
    certificate = base64.b64decode(value['certificate'], validate=True)
    if not certificate or base64.b64encode(certificate).decode() != value['certificate']:
        raise ValueError('The producer control certificate is invalid')
    return copy.deepcopy(value)


def _proof(root, value, process):
    if (not isinstance(value, dict) or set(value) != {'process', 'path', 'inode', 'nonce'}
            or value['process'] != process or not isinstance(value['path'], str)
            or not isinstance(value['nonce'], str) or len(value['nonce']) != 64
            or any(c not in '0123456789abcdef' for c in value['nonce'])
            or not isinstance(value['inode'], dict) or set(value['inode']) != {'dev', 'ino'}):
        raise PermissionError('The owned producer listener proof is invalid')
    path = Path(value['path'])
    if path.name != 'data.sock' or path.parent.parent != Path(root) or not path.parent.name.startswith('producer-'):
        raise PermissionError('The owned producer listener is outside its private directory')
    # Anchor every ancestor without following a substituted directory link.
    at = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:-1]:
            child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)
            os.close(at)
            at = child_fd
        row = os.stat(path.name, dir_fd=at, follow_symlinks=False)
        if (not stat.S_ISSOCK(row.st_mode) or stat.S_IMODE(row.st_mode) != 0o600
                or row.st_uid != os.geteuid() or value['inode'] != {'dev': row.st_dev, 'ino': row.st_ino}):
            raise PermissionError('The owned producer listener changed')
    finally:
        os.close(at)
    return copy.deepcopy(value)


class OwnedProducer:
    """Retain the actual child; failures never adopt or restart a source process."""

    def __init__(self, root, *, sdk, control, current):
        if not _live(sdk) or not callable(current):
            raise PermissionError('The original publisher process is unavailable')
        self.root, self.sdk, self.control = Path(root), copy.deepcopy(sdk), _control(control)
        self.current = current
        self.process, self.started, self.channel, self.ready = None, None, None, None
        self.binding, self.acknowledgement = None, None
        self._secret_digest = None
        self._stopped, self._lock = threading.Event(), threading.RLock()
        self._watcher = None

    def _allowed(self):
        if (self._stopped.is_set() or not _live(self.sdk) or self.current() is not True
                or self._stopped.is_set() or not _live(self.sdk)):
            raise PermissionError('The original publisher intent is no longer current')

    def _alive(self):
        return (self.process is not None and self.process.poll() is None
                and self.started is not None and process_started(self.process.pid) == self.started)

    def _spawn(self, descriptor):
        self._allowed()
        command = _command(descriptor)
        self._allowed()
        return start_process(command, pass_fds=(descriptor,), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=process_environment())

    def prepare(self):
        with self._lock:
            self._allowed()
            if self.process is not None:
                if self._alive() and self.ready is not None:
                    return _proof(self.root, self.ready, self.ready['process'])
                raise InterruptedError('The original publisher launch needs reconciliation')
            parent, inherited = socket.socketpair()
            prepared = False
            try:
                # Admission covers verification and transfers to the actual child lifetime.
                self.process = maintenance.spawn(self._spawn, inherited.fileno())
                self.started = process_started(self.process.pid)
                if self.started is None:
                    raise InterruptedError('The owned publisher ended during launch')
                self.channel = parent
                inherited.close()
                nonce = secrets.token_hex(32)
                deadline = time.monotonic() + SOCKET_S
                producer_control.send_frame(parent, {'root': str(self.root), 'sdk': self.sdk,
                    'control': self.control, 'parent': {'pid': os.getpid(), 'started': process_started(os.getpid())},
                    'nonce': nonce}, deadline=deadline)
                answer = producer_control.receive_frame(parent, deadline=deadline)
                if answer.get('nonce') != nonce:
                    raise PermissionError('The owned publisher challenge changed')
                self._allowed()
                if not self._alive():
                    raise InterruptedError('The owned publisher generation changed')
                self.ready = _proof(self.root, answer, {'pid': self.process.pid, 'started': self.started})
                self._watcher = threading.Thread(target=self._watch, daemon=True)
                self._watcher.start()
                prepared = True
                return copy.deepcopy(self.ready)
            finally:
                inherited.close()
                if not prepared:
                    parent.close()
                    self.close()

    def activate(self, binding, secret):
        """Called only after durable owner CAS and receiver custody acknowledgement."""
        with self._lock:
            self._allowed()
            if self.ready is None or not self._alive() or not isinstance(secret, bytes) or len(secret) != 32:
                raise PermissionError('An owned inert publisher and private capability are required')
            _binding(binding, self.ready, self.sdk, self.control)
            checksum = digest(binding)
            secret_digest = hashlib.sha256(secret).hexdigest()
            if self.binding is not None:
                if (self.binding == binding and self.acknowledgement == checksum
                        and self._secret_digest == secret_digest):
                    return checksum
                raise InterruptedError('The original publisher activation needs reconciliation')
            # Retain the exact intent before a possible activation or lost ACK.
            self.binding = copy.deepcopy(binding)
            self._secret_digest = secret_digest
            deadline = time.monotonic() + SOCKET_S
            producer_control.send_frame(self.channel, {'binding': binding, 'secret': secret.hex()}, deadline=deadline)
            answer = producer_control.receive_frame(self.channel, deadline=deadline)
            self._allowed()
            if not self._alive() or answer != {'active': checksum, 'nonce': self.ready['nonce']}:
                raise InterruptedError('The publisher activation outcome is unknown')
            self.acknowledgement = checksum
            return checksum

    def matches(self, process):
        try:
            self._allowed()
            return self.ready is not None and process == self.ready['process'] and self._alive()
        except (OSError, ValueError, RuntimeError):
            return False

    def _watch(self):
        while not self._stopped.wait(0.1):
            try:
                self._allowed()
                if not self._alive():
                    break
            except (OSError, ValueError, RuntimeError):
                break
        self._stopped.set()
        if self.channel is not None:
            with contextlib.suppress(OSError):
                self.channel.shutdown(socket.SHUT_RDWR)
            self.channel.close()

    def close(self):
        self._stopped.set()
        if self.channel is not None:
            with contextlib.suppress(OSError):
                self.channel.shutdown(socket.SHUT_RDWR)
            self.channel.close()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.wait(timeout=SOCKET_S)
            except subprocess.TimeoutExpired:
                if self._alive():
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=SOCKET_S)
                    except subprocess.TimeoutExpired:
                        return False  # Keep Popen/generation tracking for later cleanup.
        return self.process is None or self.process.poll() is not None


def _binding(value, proof, sdk, control):
    keys = {'source', 'generation', 'scope', 'epoch', 'pool', 'receiver', 'producer', 'instance',
            'runtime', 'implementation', 'sdk', 'bridge', 'listener', 'activation'}
    if (not isinstance(value, dict) or set(value) != keys or value.get('sdk') != sdk or value.get('bridge') != proof['process']
            or value.get('listener') != {'path': proof['path'], **proof['inode']}
            or value.get('activation') != proof['nonce'] or value.get('pool') != control['pool']
            or value.get('runtime') != control['runtime'] or value.get('instance') != control['daemon']['instance']
            or value.get('producer') != control['certificate']):
        raise PermissionError('The persisted producer binding changed')
    for key, length in (('source', 32), ('generation', 32), ('epoch', 64), ('implementation', 64)):
        item = value[key]
        if not isinstance(item, str) or len(item) != length or any(c not in '0123456789abcdef' for c in item):
            raise ValueError('The persisted producer source identity is invalid')
    if not isinstance(value['scope'], str) or not 0 < len(value['scope']) <= 256:
        raise ValueError('The persisted producer scope is invalid')
    receiver = value['receiver']
    if not isinstance(receiver, str) or not 0 < len(receiver) <= 32768:
        raise ValueError('The persisted producer receiver is invalid')
    der = base64.b64decode(receiver, validate=True)
    if not der or base64.b64encode(der).decode() != receiver:
        raise ValueError('The persisted producer receiver is invalid')
    digest(value)


def _forward(control, binding, secret, request, deadline):
    action = request.get('action')
    if (action not in ('publish', 'receipt') or set(request) !=
            ({'action', 'event_id', 'record'} if action == 'publish' else {'action', 'event_id'})):
        raise ValueError('Invalid publisher data request')
    raw = producer_bridge.record_bytes(request['record']) if action == 'publish' else None
    action = 'bridge-' + action
    body = producer_bridge.signed_request(secret, binding, action, request['event_id'], raw)
    process = {key: control['daemon'][key] for key in ('pid', 'started')}
    context = tls.certificate_context(control['certificate'], context_type=tls.ExactContext)
    context.expected_der = base64.b64decode(control['certificate'])
    with socket.socket(socket.AF_UNIX) as plain:
        producer_control._remaining(plain, deadline)
        plain.connect(control['control_path'])
        if not unix_socket.matches(plain, process):
            raise PermissionError('The producer control process changed')
        plain.settimeout(min(HANDSHAKE_S, max(0.001, deadline - time.monotonic())))
        with context.wrap_socket(plain, server_hostname='localhost') as channel:
            if not unix_socket.matches(channel, process):
                raise PermissionError('The producer control process changed')
            producer_control.send_frame(channel, {'version': 1, 'action': action, 'request': body},
                                        deadline=deadline, maximum=producer_control.BRIDGE_BYTES)
            result = producer_control.receive_frame(channel, deadline=deadline, maximum=producer_control.BRIDGE_BYTES)
            if not unix_socket.matches(channel, process):
                raise PermissionError('The producer control process changed')
            if not isinstance(result, dict) or set(result) != {'ok', 'result'} or result['ok'] is not True or not isinstance(result['result'], dict):
                raise OSError('The producer request outcome requires reconciliation')
            return result['result']


class _DataIO:
    """Cancellation-aware partial I/O on one plain stream and one absolute deadline."""

    def __init__(self, channel, deadline, check):
        self.channel, self.deadline, self.check = channel, deadline, check

    def settimeout(self, value):
        self.channel.settimeout(min(value, 0.1))

    def _allowed(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Publisher data connection expired')
        self.check(self.channel)

    def recv(self, size):
        while True:
            self._allowed()
            try:
                value = self.channel.recv(size)
            except (TimeoutError, BlockingIOError):
                continue
            self._allowed()
            return value

    def sendall(self, raw):
        pending = memoryview(raw)
        while pending:
            self._allowed()
            try:
                count = self.channel.send(pending)
            except (TimeoutError, BlockingIOError):
                continue
            if count <= 0:
                raise BrokenPipeError('Publisher data connection ended')
            self._allowed()
            pending = pending[count:]


class _Data:
    def __init__(self, private, value):
        self.private, self.parent, self.sdk = private, value['parent'], value['sdk']
        self.control = _control(value['control'])
        self.stop, self.channels = threading.Event(), set()
        self.location = producer_control._Location(value['root'])
        prepared = False
        try:
            self.listener = self.location.bind('data.sock')
            row = os.stat('data.sock', dir_fd=self.location.directory, follow_symlinks=False)
            self.proof = {'process': {'pid': os.getpid(), 'started': process_started(os.getpid())},
                          'path': str(self.location.path / 'data.sock'), 'inode': {'dev': row.st_dev, 'ino': row.st_ino},
                          'nonce': value['nonce']}
            prepared = True
        finally:
            if not prepared:
                if hasattr(self, 'listener'):
                    self.listener.close()
                self.location.close()

    def activate(self):
        producer_control.send_frame(self.private, self.proof, deadline=time.monotonic() + SOCKET_S)
        value = producer_control.receive_frame(self.private, deadline=time.monotonic() + SOCKET_S)
        if set(value) != {'binding', 'secret'}:
            raise PermissionError('The publisher activation is invalid')
        _binding(value['binding'], self.proof, self.sdk, self.control)
        self.binding, self.secret = value['binding'], bytes.fromhex(value['secret'])
        if len(self.secret) != 32 or not _live(self.parent) or not _live(self.sdk):
            raise PermissionError('The publisher activation is unavailable')
        producer_control.send_frame(self.private, {'active': digest(self.binding), 'nonce': self.proof['nonce']},
                                    deadline=time.monotonic() + SOCKET_S)

    def watch(self):
        while not self.stop.is_set():
            self.private.settimeout(0.1)
            try:
                self.private.recv(1)  # EOF or an unexpected frame permanently ends this child.
                break
            except TimeoutError:
                if _live(self.parent) and _live(self.sdk) and os.getppid() == self.parent['pid']:
                    continue
            except OSError:
                pass
            break
        self.stop.set()
        self.listener.close()
        for channel in tuple(self.channels):
            with contextlib.suppress(OSError):
                channel.shutdown(socket.SHUT_RDWR)
            channel.close()

    def check(self, channel):
        self.location.check()
        if self.stop.is_set() or not unix_socket.matches(channel, self.sdk) or not _live(self.parent):
            raise PermissionError('The publisher process is unavailable')

    def serve(self, channel):
        self.channels.add(channel)
        try:
            deadline = time.monotonic() + SOCKET_S
            stream = _DataIO(channel, deadline, self.check)
            self.check(channel)
            request = producer_control.receive_frame(stream, deadline=deadline, maximum=producer_control.BRIDGE_BYTES)
            self.check(channel)
            result = _forward(self.control, self.binding, self.secret, request, deadline)
            self.check(channel)
            producer_control.send_frame(stream, {'ok': True, 'result': result}, deadline=deadline,
                                        maximum=producer_control.BRIDGE_BYTES)
            producer_control._retain(stream, deadline)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            pass  # No resend or second response; the durable provider owns reconciliation.
        finally:
            self.channels.discard(channel)
            channel.close()

    def run(self):
        threading.Thread(target=self.watch, daemon=True).start()
        while not self.stop.is_set():
            try:
                channel, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self.serve(channel)

    def close(self):
        self.stop.set()
        self.listener.close()
        self.location.close()


def child(fd):
    """Fixed installed entrypoint; the inherited descriptor carries all private input."""
    parent = {'pid': os.getppid(), 'started': process_started(os.getppid())}
    with socket.socket(fileno=fd) as private:
        private.set_inheritable(False)
        value = producer_control.receive_frame(private, deadline=time.monotonic() + SOCKET_S)
        if (set(value) != {'root', 'sdk', 'control', 'parent', 'nonce'} or value['parent'] != parent
                or not _live(parent) or not _live(value['sdk'])):
            raise PermissionError('The owning publisher launch is unavailable')
        server = _Data(private, value)
        try:
            server.activate()
            server.run()
        finally:
            server.close()
