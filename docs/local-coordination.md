# Optional local coordination API

Poolhouse can prepare an isolated local Kubernetes metadata API. Kubernetes is optional;
Poolhouse remains responsible for agent identity, pool membership, grants, reviews and compute allocation.
This control plane has no execution nodes and runs no Pods.

An authenticated agent runs `poolhouse runtime coordination prepare` to inspect the plan,
then `poolhouse runtime coordination run`. The agent supplies its own credential selector
when required by its harness. The command starts a pinned K3s agentless server in Docker,
verifies its isolation, TLS and restricted service account, reports nonsecret evidence,
and removes its exact container and network before returning. If creation or cleanup cannot
be confirmed, it preserves a private recovery receipt and its resource claims for the agent;
it never deletes a resource based only on its name. `--seconds` keeps the verified
API available for a bounded interval, up to one hour. The caller must retain its resource
allocation throughout that interval; a test broker lease must request and require two CPUs.

Docker configuration is private and empty. The command never reads a person's Docker
credentials or Kubernetes configuration, changes the current context, installs a login
service, mounts host paths or grants container privileges. Generated credentials and the temporary relay executable
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

K3s calls agentless servers experimental. The server has no kubelet,
container runtime or CNI. See [agentless servers](https://docs.k3s.io/advanced#running-agentless-servers-experimental)
and [server configuration](https://docs.k3s.io/cli/server).

The host endpoint is an opaque TLS stream over the owned container's standard input/output,
not a published Docker port. The container remains on one internal network. Each connection
checks the current claims, exact container identity, isolation and executable custody. The
relay accepts one active connection, with bounded time, bytes and buffering. Normal cancellation
closes the listener and reaps its owned child before container cleanup; an unconfirmed child
keeps the resources and recovery evidence. Abrupt process death still needs agent recovery.

The relay uses the official static BusyBox 1.37.0 musl Linux ARM64 image, pinned to
`sha256:c542a2b3cb3c982440890284aac884e89dbf468d5ff1c206fd2ba8ea23257420`.
A separate container with no network supplies the executable. Its exact binary digest is
verified before an atomic install into private memory owned by a different nonroot UID;
the server cannot modify it. Poolhouse does not include that binary in its distribution.
See the [official image source](https://github.com/docker-library/busybox/tree/4c1756d5e2fd5dc60276cba8c01b366e5eb667ef),
[exact ARM64 artifact](https://github.com/docker-library/busybox/blob/b6edf7d5c5200d629d7c3f7758293a3675471dde/latest-1/musl/arm64v8/index.json),
[BusyBox source and GPLv2 license](https://busybox.net/license.html) and
[musl license](https://git.musl-libc.org/cgit/musl/tree/COPYRIGHT).
