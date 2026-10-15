"""Bounded opaque TLS delivery through an owned container's standard streams."""

import base64
import contextlib
import hashlib
import os
import selectors
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from poolhouse import platform
from poolhouse.board.client import Client, NodeError
from poolhouse.board.session import Session
from poolhouse.coordination.docker import Docker, Refused
from poolhouse.coordination.plan import Plan

IMAGE = 'busybox@sha256:c542a2b3cb3c982440890284aac884e89dbf468d5ff1c206fd2ba8ea23257420'
SHA256 = 'f5131eaf292e8315788f4c0dc3f0dd70172c6a76592e6528ea63cfe0681f7814'
BINARY = '/relay/busybox'
CHUNK = 65536
LIMIT = 8 * 1024**2
INSTALL = '''set -eu
umask 077
[ ! -L /relay ] && [ -d /relay ]
[ ! -e /relay/busybox ] && [ ! -e /relay/pending ]
/bin/base64 -d > /relay/pending
/bin/chmod 0555 /relay/pending
/bin/mv /relay/pending /relay/busybox
'''


def verified_binary(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024**2:
        raise Refused('The relay executable is not a bounded regular file.')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != SHA256:
        raise Refused('The relay executable digest changed.')
    if body[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', body, 18)[0] != 183:
        raise Refused('The relay executable is not a Linux ARM64 binary.')
    offset = struct.unpack_from('<Q', body, 32)[0]
    size, count = struct.unpack_from('<HH', body, 54)
    if any(struct.unpack_from('<I', body, offset + index * size)[0] == 3 for index in range(count)):
        raise Refused('The relay executable requires a dynamic interpreter.')
    return body


def install(docker: Docker, identifier: str, plan: Plan, root: Path, session: Session) -> str:
    docker.image_ready(image=IMAGE, architecture='arm64')
    helper = replace(plan, identifier=plan.identifier + '-relay')
    argv = ['create', '--name', helper.name, '--network', 'none', '--user', '65532:65532',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
            '--cpus', '2', '--memory', '32m', '--memory-swap', '32m', '--pids-limit', '32', '--restart', 'no']
    for key, value in helper.labels.items():
        argv += ['--label', f'{key}={value}']
    session.reserve([('server', helper.name)])
    helper_id = docker.call([*argv, IMAGE, '/bin/busybox', 'nc', '--help']).stdout.strip()
    try:
        value = docker.inspect('container', helper_id, helper)
        host = value['HostConfig']
        if (value['Config']['Image'] != IMAGE or value['Config']['User'] != '65532:65532' or
                host['NetworkMode'] != 'none' or not host['ReadonlyRootfs'] or host['Privileged'] or
                host.get('CapAdd') or set(host['CapDrop']) != {'ALL'} or value.get('Mounts')):
            raise Refused('The relay dependency container isolation changed.')
        docker.call(['start', '--attach', helper_id])
        path = root / 'relay-busybox'
        docker.call(['cp', f'{helper_id}:/bin/busybox', str(path)])
        body = verified_binary(path)
        docker.inspect('container', identifier, plan)
        docker.call(['exec', '-i', '--user', '65533:65533', identifier, '/bin/sh', '-c', INSTALL],
                    body=base64.b64encode(body).decode('ascii'))
        return custody(docker, identifier)
    finally:
        docker.inspect('container', helper_id, helper)
        docker.call(['rm', '--volumes', helper_id])
        session.release('server', helper.name)


def custody(docker: Docker, identifier: str) -> str:
    result = docker.call(['exec', identifier, '/bin/stat', '-c', '%u:%g:%a:%i:%F', '/relay', BINARY], timeout=2)
    rows = result.stdout.splitlines()
    if (len(rows) != 2 or not rows[0].startswith('65533:65533:755:') or
            not rows[0].endswith(':directory') or not rows[1].startswith('65533:65533:555:') or
            not rows[1].endswith(':regular file')):
        raise Refused('The relay executable custody changed.')
    digest = docker.call(['exec', identifier, '/bin/sha256sum', BINARY], timeout=2).stdout.split()
    if digest != [SHA256, BINARY]:
        raise Refused('The installed relay executable digest changed.')
    return result.stdout


@dataclass
class Forwarder:
    docker: Docker = field(repr=False)
    identifier: str
    plan: Plan
    session: Session = field(repr=False)
    stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    listener: socket.socket | None = field(default=None, init=False, repr=False)
    active: socket.socket | None = field(default=None, init=False, repr=False)
    failed: bool = field(default=False, init=False)
    fingerprint: str = field(default='', init=False, repr=False)
    network: str = field(default='', init=False)

    def authorized(self) -> None:
        if self.session.whoami().name != self.plan.owner:
            raise Refused('The relay caller changed.')
        claims = {(row.kind, row.key) for row in self.session.claims()
                  if row.owner == self.plan.owner and row.expires_in_s > 0}
        if not {('server', self.plan.name), ('port', str(self.plan.port))} <= claims:
            raise Refused('The relay resource claim is no longer held.')
        self.docker.verify(self.identifier, self.network, self.plan, timeout=2)
        if custody(self.docker, self.identifier) != self.fingerprint:
            raise Refused('The relay executable was replaced.')

    def check(self) -> None:
        if self.failed:
            raise Refused('The owned local relay stopped before verification completed.')

    def run(self) -> None:
        if self.listener is None:
            raise Refused('The owned relay listener is unavailable.')
        try:
            while not self.stop.is_set():
                try:
                    client, address = self.listener.accept()
                except TimeoutError:
                    continue
                self.active = client
                try:
                    if address[0] != '127.0.0.1':
                        raise Refused('The relay accepted a non-loopback connection.')
                    self.authorized()
                    if not self.stop.is_set():
                        self.deliver(client)
                finally:
                    client.close()
                    self.active = None
        except (OSError, Refused, NodeError, subprocess.SubprocessError):
            self.failed = not self.stop.is_set()

    def deliver(self, client: socket.socket) -> None:
        argv = [self.docker.program, 'exec', '-i', '--user', '65532:65532', self.identifier,
                BINARY, 'nc', '-w', '30', '127.0.0.1', '6443']
        process = platform.start_process(argv, env=self.docker.env, shell=False, close_fds=True,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            pump(client, process, self.stop)
        finally:
            if process.poll() is None:
                platform.terminate_process_group(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                platform.terminate_process_group(process, force=True)
                process.wait(timeout=1)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()


def pump(client: socket.socket, process: subprocess.Popen, stop: threading.Event) -> None:
    if process.stdin is None or process.stdout is None:
        raise Refused('The owned relay streams are unavailable.')
    client.setblocking(False)
    os.set_blocking(process.stdin.fileno(), False)
    os.set_blocking(process.stdout.fileno(), False)
    incoming = bytearray()
    outgoing = bytearray()
    total = 0
    deadline = time.monotonic() + 30
    remote_done = False
    with selectors.DefaultSelector() as selector:
        while not stop.is_set() and time.monotonic() < deadline:
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
            events = (selectors.EVENT_READ if len(incoming) < CHUNK else 0)
            if outgoing:
                events |= selectors.EVENT_WRITE
            if events:
                selector.register(client, events, 'client')
            if incoming:
                selector.register(process.stdin, selectors.EVENT_WRITE, 'input')
            if not remote_done and len(outgoing) < CHUNK:
                selector.register(process.stdout, selectors.EVENT_READ, 'output')
            if remote_done and not outgoing:
                break
            for key, mask in selector.select(timeout=0.2):
                if key.data == 'client':
                    if mask & selectors.EVENT_READ:
                        body = client.recv(CHUNK - len(incoming))
                        if not body:
                            return
                        incoming.extend(body)
                        total += len(body)
                    if mask & selectors.EVENT_WRITE:
                        del outgoing[:client.send(outgoing)]
                elif key.data == 'input':
                    del incoming[:os.write(process.stdin.fileno(), incoming)]
                else:
                    body = os.read(process.stdout.fileno(), CHUNK - len(outgoing))
                    remote_done = not body
                    outgoing.extend(body)
                    total += len(body)
                if total > LIMIT:
                    return


@contextlib.contextmanager
def forward(docker: Docker, identifier: str, plan: Plan, session: Session):
    client = Client(state=session.client.state, autostart=False, timeout=2)
    worker = Forwarder(docker, identifier, plan, replace(session, client=client))
    value = docker.inspect('container', identifier, plan, timeout=2)
    worker.network = value['HostConfig']['NetworkMode']
    worker.fingerprint = custody(docker, identifier)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', plan.port))
        listener.listen(8)
        listener.settimeout(0.2)
        worker.listener = listener
        thread = threading.Thread(target=worker.run, name='owned-metadata-relay')
        thread.start()
        try:
            yield worker
            worker.check()
        finally:
            worker.stop.set()
            listener.close()
            if worker.active is not None:
                worker.active.close()
            thread.join(timeout=12)
            if thread.is_alive():
                raise Refused('The owned relay has not completed its cleanup.')
