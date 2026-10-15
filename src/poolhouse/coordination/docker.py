"""Credential-isolated local Docker operations and configuration verification."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from poolhouse.coordination.plan import IMAGE, Plan


class Refused(RuntimeError):
    """An isolated control-plane operation could not be verified."""


class Docker:
    def __init__(self, root: Path, endpoint: str):
        if not endpoint.startswith('unix:///') or not endpoint.endswith('/docker.sock'):
            raise Refused('A local Docker socket is required.')
        self.program = shutil.which('docker')
        if not self.program:
            raise Refused('Docker is unavailable.')
        self.env = {'PATH': os.defpath, 'HOME': str(root), 'DOCKER_CONFIG': str(root),
                    'DOCKER_HOST': endpoint}
        config = root / 'config.json'
        config.write_text('{}', encoding='utf-8')
        config.chmod(0o600)

    def call(self, argv: list[str], *, body: str | None = None, timeout: int = 30,
             required: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([self.program, *argv], env=self.env, input=body,
                                capture_output=True, text=True, timeout=timeout, check=False)
        if required and result.returncode:
            raise Refused(f'Docker {argv[0]} failed (exit {result.returncode}).')
        return result

    def inspect(self, kind: str, identifier: str, plan: Plan, *, timeout: int = 30) -> dict:
        result = self.call([kind, 'inspect', identifier] if kind == 'network' else ['inspect', identifier], timeout=timeout)
        value = json.loads(result.stdout)[0]
        labels = value.get('Labels') if kind == 'network' else value['Config'].get('Labels')
        if any((labels or {}).get(key) != expected for key, expected in plan.labels.items()):
            raise Refused('The Docker resource belongs to a different session.')
        if value['Id'] != identifier:
            raise Refused('The Docker resource identifier changed.')
        return value

    def verify(self, identifier: str, network: str, plan: Plan, *, timeout: int = 30) -> None:
        value = self.inspect('container', identifier, plan, timeout=timeout)
        host, config = value['HostConfig'], value['Config']
        required = {'Privileged': False, 'ReadonlyRootfs': True, 'NanoCpus': 2_000_000_000,
                    'Memory': 2 * 1024**3, 'MemorySwap': 2 * 1024**3, 'PidsLimit': 512,
                    'Binds': None, 'NetworkMode': network, 'CapAdd': None}
        if any(host.get(key) != expected for key, expected in required.items()):
            raise Refused('Docker isolation or resource limits changed.')
        if (config['User'] != '65532:65532' or config['Image'] != IMAGE or
                set(host.get('CapDrop') or []) != {'ALL'} or
                not ({'no-new-privileges', 'no-new-privileges=true'} & set(host.get('SecurityOpt') or [])) or
                host['RestartPolicy']['Name'] != 'no' or value.get('Mounts')):
            raise Refused('Docker identity, capabilities or mounts changed.')
        command = plan.container(network)
        if config.get('Cmd') != command[command.index(IMAGE) + 1:]:
            raise Refused('The control-plane command changed.')
        if host.get('PortBindings') not in (None, {}):
            raise Refused('The internal control plane unexpectedly publishes a Docker port.')
        attached = value.get('NetworkSettings', {}).get('Networks', {})
        if (len(attached) != 1 or
                next(iter(attached.values())).get('IPAMConfig', {}).get('IPv4Address') != plan.address):
            raise Refused('The private API address or network attachments changed.')
        if value.get('State', {}).get('Running'):
            actual = next(iter(attached.values()))
            if actual.get('NetworkID') != network or actual.get('IPAddress') != plan.address:
                raise Refused('The running API address does not match its isolated network.')
        expected_tmpfs = {}
        argv = plan.container(network)
        for index, item in enumerate(argv):
            if item == '--tmpfs':
                key, options = argv[index + 1].split(':', 1)
                expected_tmpfs[key] = options
        if host.get('Tmpfs') != expected_tmpfs or not self.inspect('network', network, plan, timeout=timeout)['Internal']:
            raise Refused('The private state or network isolation changed.')


    def image_ready(self, *, image: str = IMAGE, architecture: str = '') -> None:
        found = self.call(['image', 'inspect', image], required=False)
        if found.returncode:
            self.call(['pull', '--quiet', image], timeout=180)
            found = self.call(['image', 'inspect', image])
        actual = json.loads(found.stdout)[0]
        if (image not in actual.get('RepoDigests', []) or actual['Os'] != 'linux' or
                (architecture and actual['Architecture'] != architecture)):
            raise Refused('The pulled image digest or operating system did not verify.')
