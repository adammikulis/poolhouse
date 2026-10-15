"""Control-plane TLS, audience and namespace authorization checks."""

import json
import ssl
import time

from poolhouse import httpguard
from poolhouse.coordination.docker import Docker, Refused
from poolhouse.net import loopback
from poolhouse.redact.secrets import redact

BOOTSTRAP = '''set -eu
umask 077
/bin/kubectl --kubeconfig /state/kubeconfig apply -f - <<'YAML'
apiVersion: v1
kind: Namespace
metadata:
  name: poolhouse-coordination
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: coordination-status
  namespace: poolhouse-coordination
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: status
  namespace: poolhouse-coordination
data:
  authority: none
---
apiVersion: coordination.k8s.io/v1
kind: Lease
metadata:
  name: coordinator
  namespace: poolhouse-coordination
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: coordination-status
  namespace: poolhouse-coordination
rules:
- apiGroups: [""]
  resources: ["configmaps"]
  resourceNames: ["status"]
  verbs: ["get", "update", "patch"]
- apiGroups: ["coordination.k8s.io"]
  resources: ["leases"]
  resourceNames: ["coordinator"]
  verbs: ["get", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: coordination-status
  namespace: poolhouse-coordination
subjects:
- kind: ServiceAccount
  name: coordination-status
  namespace: poolhouse-coordination
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: coordination-status
YAML
/bin/kubectl --kubeconfig /state/kubeconfig -n poolhouse-coordination create token coordination-status --duration=10m --audience=poolhouse-local > /state/service-token
cat > /state/serviceconfig <<'YAML'
apiVersion: v1
kind: Config
clusters:
- name: local
  cluster:
    certificate-authority: /state/k3s/server/tls/server-ca.crt
    server: https://127.0.0.1:6443
users:
- name: local-service
  user:
    tokenFile: /state/service-token
contexts:
- name: local
  context:
    cluster: local
    user: local-service
    namespace: poolhouse-coordination
current-context: local
YAML
/bin/kubectl --kubeconfig /state/kubeconfig -n poolhouse-coordination create token coordination-status --duration=10m --audience=wrong-local-audience > /state/wrong-audience-token
sed 's@/state/service-token@/state/wrong-audience-token@' /state/serviceconfig > /state/wrong-audienceconfig
printf '%s' invalid > /state/invalid-token
sed 's@/state/service-token@/state/invalid-token@' /state/serviceconfig > /state/invalidconfig
sed 's@tokenFile: /state/service-token@token: ""@' /state/serviceconfig > /state/anonymousconfig
'''


def kubectl(docker: Docker, identifier: str, args: list[str], *, required: bool = True, identity: str = 'service'):
    config = '/state/kubeconfig' if identity == 'admin' else f'/state/{identity}config'
    return docker.call(['exec', identifier, '/bin/kubectl', '--kubeconfig', config,
                        '--request-timeout=5s', *args], timeout=10, required=required)


def readiness(docker: Docker, identifier: str, *, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = kubectl(docker, identifier, ['get', '--raw=/readyz'], identity='admin', required=False)
        if result.returncode == 0 and result.stdout.strip() == 'ok':
            return
        time.sleep(1)
    logs = docker.call(['logs', '--tail', '12', identifier], required=False)
    client = redact(result.stderr + result.stdout)[0][-600:]
    detail = redact(logs.stderr + logs.stdout)[0][-1000:]
    raise Refused(f'The isolated control plane did not become ready. Client: {client}; server: {detail}')


def verify(docker: Docker, identifier: str) -> dict:
    docker.call(['exec', '-i', identifier, '/bin/sh'], body=BOOTSTRAP, timeout=30)
    status = json.loads(kubectl(docker, identifier, ['get', 'configmap', 'status', '-o', 'json']).stdout)
    if status.get('data') != {'authority': 'none'}:
        raise Refused('The metadata status does not have the expected authority boundary.')
    kubectl(docker, identifier, ['patch', 'configmap', 'status', '--type=merge',
                                 '--patch', '{"data":{"observed":"verified"}}'])
    kubectl(docker, identifier, ['patch', 'lease', 'coordinator', '--type=merge',
                                 '--patch', '{"spec":{"holderIdentity":"verification-only","leaseDurationSeconds":30}}'])
    saved = json.loads(kubectl(docker, identifier, ['get', 'lease', 'coordinator', '-o', 'json']).stdout)
    if saved.get('spec', {}).get('holderIdentity') != 'verification-only':
        raise Refused('The service account could not update its named metadata resource.')
    claims = token_claims(docker, identifier)
    lifetime = claims.get('exp', 0) - claims.get('iat', 0)
    if claims.get('aud') != ['poolhouse-local'] or not 590 <= lifetime <= 610:
        raise Refused('The software service token audience or lifetime did not verify.')
    nodes = json.loads(kubectl(docker, identifier, ['get', 'nodes', '-o', 'json'], identity='admin').stdout)
    if nodes.get('items') != []:
        raise Refused('This metadata control plane unexpectedly has execution nodes.')
    for args in [('get', 'secrets'), ('get', 'nodes'), ('create', 'pods'),
                 ('create', 'pods', '--subresource=exec'), ('create', 'rolebindings'),
                 ('create', 'serviceaccounts', '--subresource=token'), ('list', 'configmaps'),
                 ('update', 'configmaps/other')]:
        result = kubectl(docker, identifier, ['auth', 'can-i', *args], required=False)
        if result.returncode != 1 or result.stdout.strip() != 'no':
            raise Refused(f'The service account can perform an unexpected operation: {' '.join(args)}.')
    for identity, args, expected in [('invalid', ['get', 'configmap', 'status'], 'Unauthorized'),
                                     ('wrong-audience', ['get', 'configmap', 'status'], 'Unauthorized'),
                                     ('service', ['--tls-server-name=invalid.example', 'get', 'configmap', 'status'], 'certificate')]:
        result = kubectl(docker, identifier, args, required=False, identity=identity)
        if result.returncode == 0 or expected.lower() not in result.stderr.lower():
            raise Refused('The control plane did not reject invalid identity or TLS.')
    return {'tls_verified': True, 'restricted_service_account': True,
            'nodes': 0, 'authority': 'none', 'token_lifetime_seconds': lifetime, 'wrong_audience_refused': True,
            'named_metadata_writes_verified': True}


def token_claims(docker: Docker, identifier: str) -> dict:
    script = """set -eu
body=$(cut -d. -f2 /state/service-token)
while [ $(( ${#body} % 4 )) -ne 0 ]; do body="${body}="; done
printf '%s' "$body" | tr '_-' '/+' | base64 -d
"""
    result = docker.call(['exec', '-i', identifier, '/bin/sh'], body=script)
    return json.loads(result.stdout)


def host_tls(docker: Docker, identifier: str, port: int) -> None:
    authority = docker.call(['exec', identifier, '/bin/cat',
                             '/state/k3s/server/tls/server-ca.crt']).stdout
    netloc = f'127.0.0.1:{port}'
    url = f'https://{netloc}/api/v1/namespaces/poolhouse-coordination/configmaps/status'
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=authority)
    try:
        answer = loopback.fetch(url, context=context, timeout=5, headers={'Authorization': ''})
    except httpguard.Refused as error:
        raise Refused('The loopback TLS endpoint could not be verified.') from error
    if answer.status not in (401, 403):
        raise Refused('The loopback TLS endpoint did not return an authenticated refusal.')
