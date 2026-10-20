"""Real isolated CPU processes prove bridge ownership without compute or accounts."""
import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import lock, maintenance, unix_socket
from poolhouse.fleet import producer_bridge, producer_control, producer_process as process, tls
from poolhouse.fleet.job_records import process_started
from poolhouse.studio.store import digest


def wait(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    pytest.fail('Owned bridge did not settle')


@pytest.fixture
def bridge(monkeypatch):
    source = str(Path(process.__file__).parents[2])
    with tempfile.TemporaryDirectory(prefix='writer-', dir='/tmp') as name:
        root = Path(name).resolve()
        monkeypatch.setattr(process, '_command', lambda fd: [sys.executable, '-I', '-B', '-c',
            f'import sys; sys.path.insert(0,{source!r}); from poolhouse.fleet.producer_process import child; child({fd})'])
        root.chmod(0o700)
        (root / 'app-identity').write_text('ab' * 32)
        (root / 'app-identity').chmod(0o600)
        identity = tls.identity(root / 'tls', 'bridge-test')
        calls, owners, caps = [], [], []
        active = {'value': True}

        def dispatch(action, request, connector):
            checked = caps[0].verify(connector, request)
            calls.append(checked)
            return {'event_id': checked['event_id'], 'state': 'accepted'}

        control = producer_control.ControlServer(root, identity=identity,
            current=lambda: {'pool': 'a' * 64, 'runtime': 'b' * 64}, dispatch=dispatch,
            bridge=lambda connector: bool(owners) and owners[0].matches(connector))
        anchor = control._binding()
        owner = process.OwnedProducer(root, sdk={'pid': os.getpid(), 'started': process_started(os.getpid())},
            control=anchor, current=lambda: active['value'])
        owners.append(owner)
        ready = owner.prepare()
        binding = {'source': '1' * 32, 'generation': '2' * 32, 'scope': 'test-scope', 'epoch': '3' * 64,
                   'pool': anchor['pool'], 'receiver': identity.beacon, 'producer': identity.beacon,
                   'instance': anchor['daemon']['instance'], 'runtime': anchor['runtime'], 'implementation': '4' * 64,
                   'sdk': owner.sdk, 'bridge': ready['process'], 'listener': {'path': ready['path'], **ready['inode']},
                   'activation': ready['nonce']}
        cap = producer_bridge.BridgeCapability(binding, owned=owner.matches,
                                               current=lambda _: active['value'])
        caps.append(cap)
        value = SimpleNamespace(owner=owner, ready=ready, binding=binding, cap=cap, calls=calls,
                                active=active, control=control, root=root)
        yield value
        cap.revoke()
        assert owner.close()
        assert owner.close()
        control.close()
        assert not Path(ready['path']).exists()


def activate(bridge):
    bridge.cap.activate(bridge.ready['process'], digest(bridge.binding))
    assert bridge.owner.activate(bridge.binding, bridge.cap.secret) == digest(bridge.binding)


def connect(bridge):
    channel = socket.socket(socket.AF_UNIX)
    channel.settimeout(2)
    channel.set_inheritable(False)
    channel.connect(bridge.ready['path'])
    assert unix_socket.matches(channel, bridge.ready['process'])
    return channel


def exchange(bridge, request):
    with connect(bridge) as channel:
        deadline = time.monotonic() + 3
        producer_control.send_frame(channel, request, deadline=deadline)
        result = producer_control.receive_frame(channel, deadline=deadline)
        assert unix_socket.matches(channel, bridge.ready['process'])
        return result


def test_real_generic_json_and_receipt_use_exact_owned_bridge(bridge):
    assert bridge.owner.prepare() == bridge.ready
    activate(bridge)
    assert bridge.owner.activate(bridge.binding, bridge.cap.secret) == digest(bridge.binding)
    with pytest.raises(InterruptedError):
        bridge.owner.activate(bridge.binding, b'changed-private-capability-value!!'[:32])
    assert exchange(bridge, {'action': 'publish', 'event_id': 'row-1', 'record': {'question': 'sample', 'nested': [1, True]}}) == {
        'ok': True, 'result': {'event_id': 'row-1', 'state': 'accepted'}}
    assert exchange(bridge, {'action': 'receipt', 'event_id': 'row-1'})['ok']
    assert [row['action'] for row in bridge.calls] == ['bridge-publish', 'bridge-receipt']
    assert bridge.calls[0]['raw'] == '{"nested":[1,true],"question":"sample"}'
    assert bridge.calls[1]['raw'] is None
    assert not bridge.owner.matches({'pid': os.getpid(), 'started': process_started(os.getpid())})


def test_inert_listener_does_not_dispatch_before_activation(bridge):
    with connect(bridge) as channel:
        channel.settimeout(.1)
        producer_control.send_frame(channel, {'action': 'receipt', 'event_id': 'not-active'},
                                    deadline=time.monotonic() + 1)
        with pytest.raises(TimeoutError):
            channel.recv(4)
    assert not bridge.calls


@pytest.mark.parametrize('change', ['activation', 'sdk', 'bridge', 'listener', 'pool', 'producer'])
def test_activation_cannot_substitute_actual_launch_binding(bridge, change):
    altered = {**bridge.binding, change: 'forged'}
    with pytest.raises(PermissionError):
        bridge.owner.activate(altered, bridge.cap.secret)
    assert not bridge.calls


def test_fresh_sibling_process_cannot_use_the_sdk_data_channel(bridge):
    activate(bridge)
    code = ('import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(sys.argv[1]); '
            's.sendall(b"\\x00\\x00\\x00\\x02{}"); '
            '\ntry: result=s.recv(4)\nexcept ConnectionResetError: result=b""\nassert result==b""')
    subprocess.run([sys.executable, '-I', '-B', '-c', code, bridge.ready['path']], check=True, timeout=5)
    assert not bridge.calls


@pytest.mark.parametrize('partial', [b'\x00', b'\x00\x00\x01\x00{'])
def test_revocation_unblocks_partial_frame_and_ends_exact_child(bridge, partial):
    activate(bridge)
    with connect(bridge) as channel:
        channel.sendall(partial)
        bridge.active['value'] = False
        wait(lambda: bridge.owner.process.poll() is not None)
        with contextlib.suppress(ConnectionResetError):
            assert channel.recv(4) == b''
    assert not bridge.calls
    with pytest.raises(PermissionError):
        bridge.owner.prepare()


def test_lost_activation_ack_never_reactivates_or_relaunches(bridge, monkeypatch):
    original = producer_control.receive_frame

    def lost(channel, **kwargs):
        result = original(channel, **kwargs)
        if channel is bridge.owner.channel:
            raise OSError('Lost private activation acknowledgement')
        return result

    monkeypatch.setattr(producer_control, 'receive_frame', lost)
    with pytest.raises(OSError):
        bridge.owner.activate(bridge.binding, bridge.cap.secret)
    with pytest.raises(InterruptedError):
        bridge.owner.activate(bridge.binding, bridge.cap.secret)
    assert bridge.owner.prepare()['process'] == bridge.ready['process']
    assert not bridge.calls


def test_parent_private_eof_stops_the_original_child(bridge):
    activate(bridge)
    bridge.owner.channel.shutdown(socket.SHUT_RDWR)
    bridge.owner.channel.close()
    wait(lambda: bridge.owner.process.poll() is not None)
    assert not bridge.owner.matches(bridge.ready['process'])
    assert not bridge.calls


@pytest.mark.parametrize('operation', ['prepare', 'activate', 'matches'])
def test_current_callback_cannot_revive_reentrantly_closed_owner(bridge, operation):
    def revoke():
        bridge.owner.close()
        return True

    bridge.owner.current = revoke
    if operation == 'matches':
        assert not bridge.owner.matches(bridge.ready['process'])
    else:
        with pytest.raises(PermissionError):
            if operation == 'prepare':
                bridge.owner.prepare()
            else:
                bridge.owner.activate(bridge.binding, bridge.cap.secret)


def test_data_connection_cannot_dispatch_a_second_frame(bridge):
    activate(bridge)
    with connect(bridge) as channel:
        deadline = time.monotonic() + 3
        for identifier in ('first', 'second'):
            producer_control.send_frame(channel, {'action': 'receipt', 'event_id': identifier}, deadline=deadline)
        assert producer_control.receive_frame(channel, deadline=deadline)['ok']
        with contextlib.suppress(ConnectionResetError):
            assert channel.recv(4) == b''
    assert len(bridge.calls) == 1 and bridge.calls[0]['event_id'] == 'first'


def test_original_sdk_death_invalidates_inert_prepared_process(bridge):
    sdk = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(30)'])
    owner = None
    try:
        generation = {'pid': sdk.pid, 'started': process_started(sdk.pid)}
        owner = process.OwnedProducer(bridge.root, sdk=generation, control=bridge.control._binding(), current=lambda: True)
        ready = owner.prepare()
        sdk.terminate()
        sdk.wait(timeout=3)
        wait(lambda: owner.process.poll() is not None)
        assert not owner.matches(ready['process'])
    finally:
        if sdk.poll() is None:
            sdk.terminate()
            sdk.wait(timeout=3)
        if owner is not None:
            assert owner.close()


def test_replaced_data_inode_is_not_unlinked_or_used(bridge):
    activate(bridge)
    location = Path(bridge.ready['path'])
    location.unlink()
    with socket.socket(socket.AF_UNIX) as replacement:
        replacement.bind(str(location))
        replacement.listen(1)
        assert bridge.owner.close()
        assert location.exists()
    location.unlink()
    location.parent.rmdir()


def test_managed_reader_is_retained_until_actual_child_exit(bridge):
    with maintenance.intent() as writer:
        with pytest.raises(lock.Busy), writer.exclusive():
            pytest.fail('A live managed bridge must retain runtime admission')
        assert bridge.owner.close()
        wait(lambda: maintenance._place()[1].retained == 0)
        with writer.exclusive():
            assert bridge.owner.process.poll() is not None


def test_writer_blocks_prepare_before_runtime_verification_and_spawn(bridge, monkeypatch):
    assert bridge.owner.close()
    wait(lambda: maintenance._place()[1].retained == 0)
    owner = process.OwnedProducer(bridge.root, sdk=bridge.owner.sdk,
                                 control=bridge.control._binding(), current=lambda: True)
    command, called, results = process._command, threading.Event(), []

    def verified(fd):
        called.set()
        return command(fd)

    def prepare():
        results.append(owner.prepare())

    monkeypatch.setattr(process, '_command', verified)
    worker = threading.Thread(target=prepare)
    try:
        with maintenance.intent() as writer, writer.exclusive():
            worker.start()
            assert not called.wait(.1)
            assert owner.process is None
        worker.join(3)
        assert not worker.is_alive() and len(results) == 1 and called.is_set()
    finally:
        assert owner.close()
        worker.join(3)


def test_runtime_verification_precedes_fixed_child_command(monkeypatch):
    calls = []

    class MissingRuntime:
        def __init__(self, root):
            self.python = Path('/unavailable/python')

        def require_current_runtime(self):
            calls.append('verified')
            raise OSError('Managed runtime unavailable')

    monkeypatch.setattr(process, 'Environment', MissingRuntime)
    with pytest.raises(OSError):
        process._command(42)
    assert calls == ['verified']


def test_blocked_data_writer_rechecks_revocation_without_extending_deadline():
    # Socketpair exercises partial I/O only; it is never used as child identity proof.
    left, right = socket.socketpair()
    started, revoked, errors = threading.Event(), threading.Event(), []

    def check(_channel):
        started.set()
        if revoked.is_set():
            raise PermissionError('Data authorization revoked')

    stream = process._DataIO(left, time.monotonic() + 30, check)
    stream.settimeout(30)
    left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)

    def send():
        try:
            stream.sendall(b'x' * 2_000_000)
        except OSError as error:
            errors.append(error)

    worker = threading.Thread(target=send)
    worker.start()
    try:
        assert started.wait(1)
        revoked.set()
        worker.join(1)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], PermissionError)
    finally:
        with contextlib.suppress(OSError):
            left.shutdown(socket.SHUT_RDWR)
        left.close()
        right.close()
        worker.join(1)


def test_control_binding_rejects_missing_and_nonfinite_generation():
    for value in ({}, {'daemon': {'pid': 0, 'started': float('nan')}}):
        with pytest.raises(ValueError):
            process._control(value)
