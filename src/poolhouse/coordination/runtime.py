"""Owned, bounded lifetime of an optional local metadata control plane."""

import ipaddress
import json
import os
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from poolhouse.board.session import Session
from poolhouse.coordination import verify
from poolhouse.coordination.docker import Docker, Refused
from poolhouse.coordination.plan import IMAGE, VERSION, Plan
from poolhouse.log import say


def cleanup(docker: Docker, plan: Plan, identifiers: dict[str, str]) -> None:
    """Remove only exact resources owned by this invocation."""
    if identifier := identifiers.get('container'):
        docker.inspect('container', identifier, plan)
        docker.call(['stop', '--time', '10', identifier], required=False)
        docker.call(['rm', '--volumes', identifier])
        identifiers.pop('container')
    if identifier := identifiers.get('network'):
        docker.inspect('network', identifier, plan)
        docker.call(['network', 'rm', identifier])
        identifiers.pop('network')


def run(session: Session, root: Path, endpoint: str, port: int, seconds: int = 0) -> dict:
    """Verify an isolated API and keep it alive for a bounded interval, then remove it."""
    if not 1024 <= port <= 65535 or not 0 <= seconds <= 3600:
        raise Refused('The local port or lifetime is outside its allowed range.')
    actor = session.whoami()
    plan = Plan(actor.name, secrets.token_hex(12), port)
    claims = [('install', str(root.resolve())), ('server', plan.name), ('port', str(port))]
    session.reserve(claims)
    identifiers: dict[str, str] = {}
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or root.stat().st_uid != os.getuid():
            raise Refused('The control-plane state directory must belong to this account.')
        with tempfile.TemporaryDirectory(prefix='local-', dir=root) as temporary:
            directory = Path(temporary)
            docker = Docker(directory, endpoint)
            try:
                image_ready(docker)
                identifiers['network'] = docker.call(plan.network()).stdout.strip()
                plan = network_plan(docker, identifiers['network'], plan)
                identifiers['container'] = docker.call(plan.container(identifiers['network'])).stdout.strip()
                docker.verify(identifiers['container'], identifiers['network'], plan)
                docker.call(['start', identifiers['container']])
                docker.verify(identifiers['container'], identifiers['network'], plan)
                verify.readiness(docker, identifiers['container'])
                evidence = verify.verify(docker, identifiers['container'])
                verify.host_tls(docker, identifiers['container'], port)
                evidence.update(host_loopback_tls_verified=True, anonymous_refused=True)
                evidence.update(owner=actor.name, version=VERSION, image=IMAGE,
                                endpoint=f'https://127.0.0.1:{port}', control_plane_only=True)
                say(json.dumps(evidence), flush=True)
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    session.reserve(claims)
                    time.sleep(min(30, max(0, deadline - time.monotonic())))
                return evidence
            finally:
                cleanup(docker, plan, identifiers)
    finally:
        if identifiers:
            say(json.dumps({'cleanup_required': identifiers, 'owner': actor.name}), flush=True)
        else:
            for kind, key in claims:
                session.release(kind, key)


def image_ready(docker: Docker) -> None:
    found = docker.call(['image', 'inspect', IMAGE], required=False)
    if found.returncode:
        docker.call(['pull', '--quiet', IMAGE], timeout=180)
        found = docker.call(['image', 'inspect', IMAGE])
    image = json.loads(found.stdout)[0]
    if IMAGE not in image.get('RepoDigests', []) or image['Os'] != 'linux':
        raise Refused('The pulled image digest or operating system did not verify.')


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
