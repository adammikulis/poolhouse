# Optional local metadata API verification

Source `e157d4c59d7a5ccf89158249fcc39730750e415d` was exercised by an authenticated agent
on the available macOS ARM64 device with Docker Desktop. The maintained CPU broker held
exactly two required CPUs throughout the bounded lifecycle. This was an agent-driven
hardware check, not a person's journey or a production pool adapter.

The source CLI prepared and ran the pinned K3s agentless API. Its container stayed on one
internal network, with no published Docker port, execution nodes, Pods, host mounts,
additional capabilities or privileged fallback. The loopback relay carried opaque TLS
through the exact owned container; the executable's pinned digest and separate nonroot
ownership were checked. The dependency image and its source/licensing are recorded in
[local coordination](../local-coordination.md).

The run verified:

- Fresh CA and server-name verification, including rejection of an incorrect server name.
- Anonymous refusal through both the source client and the installed `c4fa3aee` HTTP client,
  run in isolated mode: HTTP 401, with an exact destination guard and explicit empty
  Authorization, without ambient credentials or a client certificate.
- Reads and writes to the named `status` ConfigMap and `coordinator` Lease. Their data had
  no Poolhouse authority.
- Denial of secrets, nodes, Pod creation, role bindings, listing
  ConfigMaps and updates to a different ConfigMap; invalid and wrong-audience tokens were
  refused. The administrative check found zero execution nodes.
- A requested and issued 600-second token lifetime. Kubernetes v1.36.5
  [uses the JWT validator](https://github.com/kubernetes/kubernetes/blob/v1.36.5/pkg/serviceaccount/claims.go#L137)
  with [60 seconds of clock-skew allowance](https://github.com/kubernetes/kubernetes/blob/v1.36.5/vendor/gopkg.in/go-jose/go-jose.v2/jwt/validation.go#L22).
  The actual token expired at 02:30:51 UTC on October 15 and was refused at
  02:31:56.19 UTC, an observed age of 665.19 seconds. The token file was unchanged and the
  administrative readiness check still passed. No clock, token or verifier setting was
  changed. This proves refusal after the allowance, not an exact 600-second cutoff.
- Normal return from the 750-second hold, removal of the exact owned container and network,
  completed creation receipts, unchanged existing volume IDs and release of the CPU lease.
  No unproven volume was pruned.

A subsequent audit corrected two authorization queries: resource/name syntax does not
select a subresource. Source `bdc91bf6c902e0ca1ccb4e66446790b210773e4c` then completed
a fresh zero-hold CLI lifecycle with explicit `pods --subresource=exec` and
`serviceaccounts --subresource=token` authorization checks. Both returned `no` with status
1, as did all eight negative checks. These were authorization queries, not attempts to
execute a command or mint a token. The Role, tokens and TLS transport were unchanged from
the expiry run. Exact owned cleanup completed and the volume inventory was unchanged;
the receipt is `/private/tmp/poolhouse-coordination-subresources-_17i0rf0/receipt.json`.
All 49 coordination checks passed; restoring the imprecise query syntax made all three new
subresource regressions fail, before restoring the correct source.

The local raw receipt is `/private/tmp/poolhouse-coordination-live-e13wpig_/receipt.json`.
It records only nonsecret identifiers, hashes, public timing and outcomes. Generated service
credentials stayed in container memory. The verified executable's private host temporary
copy was removed during normal cleanup.

Before the hardware run, 119 affected checks passed across coordination, HTTP trust,
layers, coverage inventory and command reference. The subsequent client-reset correction
passed all 46 coordination checks. Removing reset handling, claim verification, executable
hash checking, byte limits or pending-creation evidence caused the relevant tests to fail;
the restored source passed. Earlier anonymous-credential and environment-isolation
mutations also failed. Coverage, Ruff, Pyright and command-reference checks passed.

Publication, selected-runtime activation and a fresh installed **full coordination CLI**
prepare/run/cleanup journey remain the coordinator's responsibility. The installed HTTP
client was exercised above; that alone does not establish the full installed CLI. Persistent
coordination, work dispatch, multi-device/WSL onboarding and real pool founding remain outside
this bounded batch.
