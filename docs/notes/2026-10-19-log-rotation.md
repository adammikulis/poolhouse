# Board log segments

A live origin log rotates at 75 percent of its existing row or byte budget. Its prior transport chain becomes a private
archive; a signed new segment replaces the live file. Rotation changes transport storage, while durable operations retain
their original identity, sequence, clock, actor, idempotency key and body.

## Transport and logical operations

A `base` continues the transport sequence and hash chain with body `{"carried": n, "left": d}`. Its declared prefix contains
exactly `n` `carry` rows, with signed heads interspersed as required by the existing batch bounds. A carry has exactly
`{"original": <complete original Row>}`. Only note, room, task and grant operations are durable. The original must belong to
the same board and origin, have a valid own hash and shape, precede the base, and keep the carry's actor and idempotency key.
Original sequences increase strictly within the prefix. Nested carries and ordinary writes of base, carry or head are refused.
A known original ID must retain exactly its original operation, including across later batches and repeated rotations.

The new signed transport chain covers the whole envelope. `Log.rows`, `Board.chain`, sync tips, signed heads, cuts, quotas and
archive positions use physical transport rows. `Log.view` and `Board.rows` expose validated original operations at carry
positions. Folds, task identifiers, cross-origin ordering, supersession, TTL and grant consumers therefore keep the same
logical evidence. Disk reopen validates both the transport chain and the complete carry prefix before exposing that view.

The existing maximum serialized row size also applies to a carry envelope. Rotation refuses before replacement if an original
cannot fit inside that bound. No size budget is raised. Notes already expired or superseded may be omitted. Unlimited or
revoked grant uses need no retention, but every held revocation must remain with its exact original identity and hash.
Other retained durable rows follow the existing carry budget; the base records omissions in `left`.

## Atomic replacement and incomplete reception

The replacement is built and validated before writing. Immediately before a replacement operation that may rename the live
file, the in-memory log becomes poisoned. Only reopening the replacement descriptor and reconciling all physical rows,
logical operations, bytes and pending state clears that poison. An error after rename, including directory fsync failure,
leaves append, truncate and further replacement refused until the log is reopened from disk truth. It never appends to an old
unlinked descriptor.

Every incomplete first-base segment is staged, including a new origin with no prior view. The old view remains unchanged
until every declared carry and its covering head arrive. A process restart discards incomplete RAM staging and refetches from
the base. Staged origins and installed logs share the same origin limit. Reset removes either form. Before installation the
complete staged segment is checked against all original operations previously held, and all held revocations must survive.
A passive receiver prunes archives after installation just as a writer does: the existing count, byte and age budgets apply.

## Trust boundaries

Only authenticated signed chains are unwrapped. Rotation cannot create an owner, person signature, membership, grant or
process identity. A copied device still has the existing fork/equivocation limits of its signing key; a writer's authoritative
jump does not prove unavailable intervening history. Known original substitutions and revocation retractions are refused.
Relay restart over a held view remains forbidden. Malformed or below-threshold signatures, wrong origin/board, replayed old
segments and injected payload instructions do not select operations or change these checks. Text stays data. Rotation adds no
approval or presence-based authority and no downgrade or compatibility path.

The non-author attack review covers cloned pools/devices, replay, forged signatures, malicious members, stolen keys,
malicious and injected agents, downgrade, approval fatigue and presence takeover. Its concrete findings drove logical carry
validation, poisoned replacement, complete initial staging, the shared staged-origin limit, whole-segment original validation
and passive-copy archive pruning. Final acceptance and affected-check receipts are recorded with the landing checkpoint.

## Verification

Explicit Rust targets are `log_rotation`, `log_rotation_review`, `log_rotation_node`, `log_rotation_semantics`, and the
`log::replacement_tests` library filter. They cover task/room ordering and IDs across repeated rotation, original TTL and
supersession, actual rename/open failure, partial new recipients, multi-batch revocation and original substitution, origin
limits, passive receiver archive bounds, signed exchange and reopen. They run through the maintained staged Cargo build and
CPU admission, with isolated temporary state and no real keystore or external endpoint.
