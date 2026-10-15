"""Owned, bounded lifetime of an optional local metadata control plane."""

import contextlib
import ipaddress
import json
import os
import secrets
import signal
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from poolhouse.board.session import Session
from poolhouse.coordination import relay, verify
from poolhouse.coordination.creation import Creation, Unfinished
from poolhouse.coordination.docker import Docker, Refused
from poolhouse.coordination.plan import IMAGE, VERSION, Plan
from poolhouse.log import say


def cleanup(docker: Docker, plan: Plan, identifiers: dict[str, str], receipt: Creation | None = None) -> None:
    """Remove only exact resources owned by this invocation."""
    if identifier := identifiers.get('container'):
        docker.inspect('container', identifier, plan)
        docker.call(['stop', '--time', '10', identifier], required=False)
        docker.call(['rm', '--volumes', identifier])
        identifiers.pop('container')
        if receipt:
            receipt.record('removed')
    if identifier := identifiers.get('network'):
        docker.inspect('network', identifier, plan)
        docker.call(['network', 'rm', identifier])
        identifiers.pop('network')
        if receipt:
            receipt.record('removed')


def run(session: Session, root: Path, endpoint: str, port: int, seconds: int = 0) -> dict:
    """Verify an isolated API and keep it alive for a bounded interval, then remove it."""
    if not 1024 <= port <= 65535 or not 0 <= seconds <= 3600:
        raise Refused('The local port or lifetime is outside its allowed range.')
    actor = session.whoami()
    plan = Plan(actor.name, secrets.token_hex(12), port)
    claims = [('install', str(root.resolve())), ('server', plan.name), ('port', str(port))]
    session.reserve(claims)
    identifiers: dict[str, str] = {}
    receipt: Creation | None = None
    cleanup_allowed = True
    with termination_cleanup():
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.is_symlink() or root.stat().st_uid != os.getuid():
                raise Refused('The control-plane state directory must belong to this account.')
            receipt = Creation(root, plan)
            identifiers = receipt.identifiers
            with tempfile.TemporaryDirectory(prefix='local-', dir=root) as temporary:
                directory = Path(temporary)
                docker = Docker(directory, endpoint)
                try:
                    docker.image_ready()
                    receipt.create(docker, 'network', plan.network())
                    plan = network_plan(docker, identifiers['network'], plan)
                    receipt.create(docker, 'container', plan.container(identifiers['network']))
                    return exercise(docker, plan, session, receipt, seconds)
                except Unfinished:
                    cleanup_allowed = False
                    raise
                finally:
                    if cleanup_allowed:
                        session.reserve(claims)
                        cleanup(docker, plan, identifiers, receipt)
        finally:
            if identifiers or (receipt and receipt.pending) or not cleanup_allowed:
                say(json.dumps({'cleanup_required': identifiers, 'owner': actor.name,
                                'unconfirmed_kind': receipt.pending if receipt else '',
                                'receipt': str(receipt.path) if receipt else ''}), flush=True)
            else:
                for kind, key in claims:
                    session.release(kind, key)
            if receipt:
                receipt.close()

def network_plan(docker: Docker, identifier: str, plan: Plan) -> Plan:
    network = docker.inspect('network', identifier, plan)
    subnet = ipaddress.ip_network(network['IPAM']['Config'][0]['Subnet'])
    private = [ipaddress.ip_network(value) for value in
               ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
    if (subnet.version != 4 or subnet.prefixlen > 30 or
            not any(subnet.subnet_of(value) for value in private) or network.get('Containers')):
        raise Refused('The fresh internal network has no isolated private IPv4 range.')
    address = str(subnet.network_address + 2)
    if address == network['IPAM']['Config'][0].get('Gateway'):
        raise Refused('The private API address would collide with the network gateway.')
    return replace(plan, address=address)


@contextlib.contextmanager
def termination_cleanup():
    """Deliver termination through the owned-resource cleanup path."""
    def stop(_number, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def exercise(docker: Docker, plan: Plan, session: Session, receipt: Creation, seconds: int) -> dict:
    identifier = receipt.identifiers['container']
    network = receipt.identifiers['network']
    docker.verify(identifier, network, plan)
    docker.call(['start', identifier])
    docker.verify(identifier, network, plan)
    verify.readiness(docker, identifier)
    evidence = verify.verify(docker, identifier)
    relay.install(docker, identifier, plan, Path(docker.env['HOME']), session)
    with relay.forward(docker, identifier, plan, session) as forwarder:
        verify.host_tls(docker, identifier, plan.port)
        evidence.update(host_loopback_tls_verified=True, anonymous_refused=True)
        evidence.update(owner=plan.owner, version=VERSION, image=IMAGE,
                        endpoint=f'https://127.0.0.1:{plan.port}', control_plane_only=True)
        say(json.dumps(evidence), flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            forwarder.check()
            session.reserve([('server', plan.name), ('port', str(plan.port)),
                             ('install', str(receipt.path.parent.resolve()))])
            time.sleep(min(30, max(0, deadline - time.monotonic())))
        return evidence
