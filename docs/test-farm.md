# Test farm

`scripts/test TIER [FILE...] --on DEVICE` runs the tests on another device of the pool, so an agent on the Mac
tests Windows, WSL, Linux or another Mac without anyone at that device. It is the entry point the local tiers
use: same tier names, same printed lines, same content-keyed reuse, and `--on all` runs it on every other device.

```
scripts/test all tests/test_x.py --on "Windows PC"     those files on one device (name or fingerprint)
scripts/test quick --on all                            the files the change reaches, on every other device
scripts/test gate --on wsl-box                         the structural checks there
scripts/test full --on all --split                     (a person) divide the list between this machine and the devices
poolhouse-test-devices                                  the devices: platform, free slots out of two, last result
```

The exit status is the worst of the runs; 70 means a device never ran its part (not in the pool, shards off,
unreachable, refused, Python 3.13 missing). A refused or unreachable device is named on stderr with its reason.

## In a pool in dev mode

A member of a pool in dev mode (docs/fleet.md, "Two devices on a LAN") takes tests from the pool's other members with no step on either
device. `scripts/test --on` and `poolhouse-test-devices` list those members beside the devices of the node's pool; a device in both is
asked by the node first and by the pool in dev mode when the node says no. The sending machine needs nothing turned on, and a device
takes tests until a person runs `poolhouse features disable remote-tests` there.

The transport is the member's daemon over the TLS the pool pinned for it, with every request signed by the pool's key and the member's
certificate checked against the pool's record. The routes are `GET /shards/v1/caps` (whether it takes tests and why not, its platform, Python
and free slots), `PUT /shards/v1/ID/tree` (the packed tree, in order, 8 MB at a time with `Content-Range`, at most 24 MiB),
`POST /shards/v1/start` (`id`, `tree_sha256`, `tier`, `files`, `timeout_s` and nothing else; the digest must match the tree held),
`GET /shards/v1/ID?since=N` (state, the output from byte N, and the result once it has ended) and `POST /shards/v1/ID/cancel`. The tree,
spec and result checks, the executor and the limits are those of the node's shards below; the daemon builds the one command it runs, on
the interpreter it found (`~/.venvs/poolhouse-dev`, else its own Python, else `POOLHOUSE_SHARD_PYTHON`, which must be 3.13 and import
pytest).

A shard belongs to the member that first sent to its id. No other member writes to it, starts it, reads its status, output or result,
cancels it, or reaches its files or its job's log and stop: they are told "no such shard" or "unknown job", and `/files/shards` is closed to
every member. The mode that counts is that of the pool the request was signed in, so a device that is in a pool in dev mode and a pool
that is not takes tests from the first and not from the second. A device holds at most eight shards and four of any one member; one
that was never finished is dropped after ten minutes, a finished one after a day or when room is needed, and one that is queued or
running never. The run is a job of the daemon's runner, so it waits for a free slot like any job, and its `by` field names the device and
the agent that asked. When the run ends the tree is deleted and the executor ends what its runner left behind. Turning `remote-tests`
off stops the next shard (a settings file that cannot be read counts as off); a run already going finishes or is cancelled by its sender.

A member that is not in dev mode (a pool made with a passphrase or a recovery file) answers `accepts: false` with the sentence that says it
takes tests only from devices a person named, and refuses an upload and a start with 403. Tests: `tests/test_dev_shards.py` (a real daemon
over pinned TLS, a real executor and pytest, every refusal), `tests/test_dev_shards_hostile.py` (another member's shard, a pool that is not in
dev mode, the disk, the settings and the text a device sends back), `tests/test_dev_pool_join.py` (a device with no pool joins the pool it
hears, then runs a shard there) and `tests/test_windows_firewall.py`.

## How the owner turns it on (devices reached through the node)

Nothing runs on a device until its person says so, per device, and the default is off. There are two switches,
both off, and both audited:

1. The experimental feature `remote-tests` (`poolhouse.features`), on each machine that sends or takes tests:
   `poolhouse features enable remote-tests`. `scripts/test --on` refuses to start while it is off on the asking
   machine, and `consent on` refuses while it is off on the device. It is recorded in the authority audit log
   with who did it.
2. The device's own switch, at the device (its agent does it on the person's order; see `docs/windows-wsl.md`),
   from this repository's checkout, with the Python 3.13 the tests should use:

```
python -m poolhouse.testfarm.consent on --from "MAC NAME"     # allow DEVICE... | deny DEVICE... | off | status
```

A DEVICE is a name from `poolhouse-test-devices` or a fingerprint. **Being in the pool is not being allowed.**
The device takes tests only from the devices in its `allowed` list, which is empty until a person names some
(`--from`, `allow`), so a device that joined the pool by itself (for instance under policy `open` on the same
network) can ask whether tests are taken (`shard_caps`) and nothing else: `shard_put`, `shard_start`,
`shard_status` and `shard_cancel` are refused with `denied`, and the refusal is an audit entry on the pool board
(at most one a minute for each device). Allowing and denying are audit entries naming the session that did it and
the device. `deny`, and putting a member out of the pool (`member_remove`), take it off the list, cancel its runs
that are going, and a removed member is also refused at the connection.

`on` is `shard_consent` on the device's own node: it saves `<state>/shards.json` (on, the interpreter, the
checkout), refuses a Python that is not 3.13 and writes an `audit` entry to the pool board naming the session
that did it. `off` stops the next upload at once (the file is read at every request). Turning it on lets any
other device of the pool run a tree's tests here as this user, so do it only on a device whose pool you trust;
removing a pool member (`member_remove`) refuses it at its next request. `poolhouse features disable remote-tests` also switches this device's node off.
`shard_consent` and `shard_call` are grants (docs/node.md, "Standing grants"): the consent command, run by a person at a terminal, signs the one it needs (a typed yes), and the lead's first-run bundle carries `shard_call` and not `shard_consent`, which a person grants on its own.

## What runs where

| step | where | what |
| --- | --- | --- |
| pick the devices | the Mac | the pool's other active members, from the local node; `all` or a name or a fingerprint |
| ask what they take | the Mac's node to each device | `shard_caps`: accepts or why not, platform (a WSL Linux says `wsl`), free slots |
| reuse | the Mac | a file that passed on that device with this content, platform and tier is not sent |
| pack and upload | the Mac to the device | the working tree as a gzip tar (uncommitted edits travel), 256 KiB at a time, over the node's TLS |
| run | the device | its node checks the digest, takes CPU slots from its lease service, starts the executor on the device's own Python 3.13 and checkout, which unpacks the tree into a scratch checkout and runs `scripts/test TIER --no-reuse` there |
| result | the device to the Mac | per-file counts, failing node ids with messages, platform, wall and CPU seconds, an output tail |
| record | the Mac | the printed lines, the per-device ledger, and a `test-result device=NAME ...` message on the board |

## Mechanism

The node (`app/poolhouse-node/src/shard/`) is the transport, so a shard rides the pool's own membership: a
request is a device op on a TLS 1.3 connection pinned to the certificate the pool record holds for the device, and
the sender is that certificate, never a field of the request. Removed or unknown devices are refused at every
op. The local API has two methods: `shard_call` (a registered session asks one op of a pool device; the node adds
`op` and `by`, the session behind the token, as a label for the audit trail) and `shard_consent`.

- Device ops, members only (`shard_caps` for any member; the rest only for a member on the allowed list): `shard_caps`, `shard_put` (one chunk, in order, from offset 0, 256 KiB at most, hex),
  `shard_start`, `shard_status`, `shard_cancel`. A shard belongs to the certificate that created it: another
  member gets "no such shard" for its status and its cancel.
- The job: `id` (32 hex), `tree_sha256`, `size`, `tier` (`all fast full gate slow`), `files` (0 to 200 names, each
  `tests/NAME.py`, none for the gate) and `timeout_s` (1 to 3600). Any other field (argv, env, cwd) is refused.
  The executor builds the only command it runs: `python scripts/test TIER --no-reuse --junitxml=OWN FILE...`.
- The tree: tracked and untracked-not-ignored files under `src tests scripts contracts patches packaging docs app`
  and top-level files. Limits: 24 MiB packed, 256 MiB unpacked, 20000 members; plain files only, no links, no `..`,
  no absolute, hidden, backslash or drive paths; a gzip stream that inflates past the limit is refused before a
  member is read. The node checks the sha256 of the upload before anything starts; the executor checks everything
  else again (`fleet/shard_spec.py`, `shard_tree.py`) because it trusts nothing it did not verify.
- Concurrency and the lease: two shards run at once; six are open at once (uploading or running), two uploading
  per sender; an upload idle for ten minutes is dropped. Each running shard holds the device's CPU slots (half
  its cores, at least one, class background) in the node's lease table until it ends, so it queues behind and
  yields to the device's own work like any other holder. `shard_start` and `shard_done` are `audit` entries on
  the pool board.
- Cancel and time: the executor enforces `timeout_s` on the runner (an interrupt, then a kill). Cancelling, a
  node stop or `timeout_s` plus 120 s makes the node send SIGTERM to the executor, which interrupts the runner;
  after 45 s it kills the executor's whole process group, and it always kills the group when the executor has
  gone, so no child outlives a shard. The runner shares the executor's group (the node leads it). Windows:
  `taskkill /T /F` on the tree.
- Output and results: a runner that writes more than 64 MiB is cancelled (exit 124); a result is at most 900 KiB
  (3000 slowest tests, 200 failures); the executor's own output tail is 8000 characters.
- Environment: the executor starts without `PYTHONPATH`, `PYTHONHOME` and the variables that mark an agent's
  shell (`CLAUDECODE`, `AI_AGENT`, `POOLHOUSE_WORKSPACE_*`, `POOLHOUSE_SESSION_*`, ...); the runner also loses
  anything that looks like a credential, and gets its own `POOLHOUSE_HOME` inside the scratch folder.
- Per-device results: the reuse key holds the device's fingerprint, its platform (`wsl` included), Python and the
  tier besides the file's content key, so a pass on Windows never satisfies macOS or WSL. Only a run that ended
  with pytest's exit 0 or 1 records passes, and only for files with passes and no failures. The ledger is
  `remote-results.json` in the project's reuse folder; `poolhouse-test-devices` reads the last result from it.
- Retired: the member HTTP route (`/workspace/v1/test-shards`), `Settings.test_shards` and
  `fleet/shard_consent.py`. There is one remote path.

## Windows, WSL, Linux

Each of those is its own pool device with its own node, certificate and consent; WSL is not the Windows around
it. The Python used is the one recorded when the person turned shards on (`--python`, default the interpreter
running the command) and it must be 3.13: otherwise the node says "Python 3.13 is needed and PATH is X" and the
sender sees it as a refusal. Files are written byte for byte and the scratch checkout is made with
`core.autocrlf=false`, so line endings are the sender's. Paths in the tree are POSIX relative names; the device
joins them with its own separators, and a name with a backslash or a drive is refused.

## Tests

`app/poolhouse-node/tests/shard.rs` (two real devices, TLS on loopback, a shell stub for the interpreter): consent
off by default, membership and revocation, a forged requester, every malformed request, ordering and size of an
upload, the lease, two at once, cancel killing a grandchild. `tests/test_testfarm.py` (the executor as the node
starts it, over a real git tree whose `scripts/test` is a stub: hostile trees and jobs, the environment, the
ledger keys, the report, the command line) and `tests/test_testfarm_nodes.py` (two node processes on loopback
ports, no beacon, no multicast, with a real executor: consent, a run, reuse, a failure, the gate, an escaping
tree, cancel, the two-at-once cap, devices by name and fingerprint) cover the Python side.

## Not verified

Native Windows has not run it: the Rust side compiles for it (the process helpers use `taskkill`) and the
executor avoids POSIX-only calls, but `scripts/test` itself has only run on macOS and Linux, and WSL start-up,
the firewall and the real network are the device setup's (`docs/windows-wsl.md`). Neither has a speedup been
measured on real machines.
