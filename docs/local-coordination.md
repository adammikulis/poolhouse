# Optional local coordination API

Poolhouse can prepare an isolated local Kubernetes metadata API. Kubernetes is optional;
Poolhouse remains responsible for agent identity, pool membership, grants, reviews and compute allocation.
This control plane has no execution nodes and runs no Pods.

An authenticated agent runs `poolhouse runtime coordination prepare` to inspect the plan,
then `poolhouse runtime coordination run`. The agent supplies its own credential selector
when required by its harness. The command starts a pinned K3s agentless server in Docker,
verifies its isolation, TLS and restricted service account, reports nonsecret evidence,
and removes its exact container and network before returning. `--seconds` keeps the verified
API available for a bounded interval, up to one hour. The caller must retain its resource
allocation throughout that interval; a test broker lease must request and require two CPUs.

Docker configuration is private and empty. The command never reads a person's Docker
credentials or Kubernetes configuration, changes the current context, installs a login
service, mounts host paths or grants container privileges. State and generated credentials
exist in private container memory and disappear when the container is removed. Stopping
this service does not join, restore or found a pool.

The generated bootstrap administrator and restricted service account are fresh software
service principals inside this isolated API. They are never alternative Poolhouse agent
identities, person credentials or sources of pool authority. Their tokens are not exported
to MCP or another agent. The restricted token has a verified ten-minute lifetime and an
API-specific audience.

The service account can read and update only the pre-created `status` ConfigMap and
`coordinator` Lease in `poolhouse-coordination`. These objects have no Poolhouse authority.
There is no work dispatch or execution adapter. Native workers continue to use the existing
node and compute broker. An agent does not obtain leadership or permissions from a
Kubernetes label or lease.

K3s calls agentless servers experimental. The server deliberately has no kubelet,
container runtime or CNI. See [agentless servers](https://docs.k3s.io/advanced#running-agentless-servers-experimental)
and [server configuration](https://docs.k3s.io/cli/server).
