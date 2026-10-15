"""An isolated optional Kubernetes metadata control plane."""

from dataclasses import dataclass

IMAGE = 'rancher/k3s@sha256:057cfd5a9d51b89f1f0ee139c28d5d5fdd5aa7ddeac2742a0ba90c6dbc0329bb'
VERSION = 'v1.36.5-k3s1'
LABEL = 'org.poolhouse.coordination'


@dataclass(frozen=True)
class Plan:
    owner: str
    identifier: str
    port: int
    address: str = '192.0.2.2'

    @property
    def name(self) -> str:
        return f'poolhouse-coordination-{self.identifier}'

    @property
    def labels(self) -> dict[str, str]:
        return {f'{LABEL}.owner': self.owner, f'{LABEL}.id': self.identifier}

    def network(self) -> list[str]:
        argv = ['network', 'create', '--internal']
        for key, value in self.labels.items():
            argv += ['--label', f'{key}={value}']
        return [*argv, self.name]

    def container(self, network_id: str) -> list[str]:
        argv = ['create', '--name', self.name, '--network', network_id, '--ip', self.address,
                '--user', '65532:65532', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--read-only',
                '--cpus', '2', '--memory', '2g', '--memory-swap', '2g',
                '--pids-limit', '512', '--restart', 'no',
                '--env', 'HOME=/state',
                '--env', 'K3S_DATA_DIR=/state/k3s']
        for key, value in self.labels.items():
            argv += ['--label', f'{key}={value}']
        for path, size, executable in (('/state', '1g', True), ('/tmp', '64m', False),  # noqa: S108 - container tmpfs
                                      ('/run', '16m', False), ('/relay', '2m', True), ('/var/lib/cni', '1m', False),
                                      ('/var/lib/kubelet', '1m', False),
                                      ('/var/lib/rancher/k3s', '1m', False),
                                      ('/var/log', '1m', False), ('/etc/rancher', '1m', False)):
            owner, mode = ('65533', '0755') if path == '/relay' else ('65532', '0700')
            options = f'rw,nosuid,nodev,uid={owner},gid={owner},mode={mode},size={size}'
            argv += ['--tmpfs', f'{path}:{options},{"exec" if executable else "noexec"}']
        return [*argv, IMAGE, 'server', '--disable-agent', '--data-dir', '/state/k3s',
                '--write-kubeconfig', '/state/kubeconfig', '--write-kubeconfig-mode', '600',
                '--https-listen-port', '6443', '--tls-san', '127.0.0.1',
                '--node-ip', self.address, '--advertise-address', self.address,
                '--disable=coredns,traefik,servicelb,local-storage,metrics-server',
                '--disable-helm-controller', '--disable-cloud-controller',
                '--disable-network-policy', '--flannel-backend=none', '--disable-kube-proxy',
                '--secrets-encryption', '--kube-apiserver-arg=api-audiences=poolhouse-local']
