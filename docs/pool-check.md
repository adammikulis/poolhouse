# The pool check

`scripts/pool-check` starts a real pool of N devices and checks, step by step, that it works. Every claim that two or more devices work
together has to pass it before the owner is asked to set up another device (AGENTS.md, section 1). A test with two nodes on one machine
over loopback proves the Rust and the Python are correct; it does not prove a pool works across a different operating system, kernel,
filesystem and network stack, which is where every "it never worked on the other device" has been.

```
scripts/pool-check                          this machine and 2 Linux containers: every step
scripts/pool-check --devices 5              a pool of five
scripts/pool-check --wsl                    one more device, behind NAT and reading as WSL2
scripts/pool-check --only join-secure --only member-removal
scripts/pool-check --distro debian:bookworm-slim      another distribution for the containers
scripts/pool-check --platform native        every device a node on this machine (no Docker): what CI runs on each operating system
scripts/pool-check --list                   the steps, one line each
scripts/pool-check --clean                  also remove the Docker image the check built
```

Each step prints `PASS`, `FAIL` with the exact failing output, or `SKIP` with why (a pool too small, no Docker, no device behind NAT). A
step that passes may print what it measured. The exit status is 1 when any step failed or anything of the run was left behind.

## What it starts

* Device 0 is this machine: a node in a temporary state directory (its own `POOLHOUSE_HOME`; the account's `~/.poolhouse` is never read or
  written), listening on loopback only, with no beacon and no multicast, so nothing asks for Local Network access.
* Devices 1 to N-1 are Linux containers on one Docker network: an Ubuntu LTS userland by default (`--distro`), each with its own
  hostname, kernel namespace, filesystem, state, node, session and address. The node is built from this tree for Linux in a Rust image
  (cargo's registry and target folder are build caches), and the Python package is installed from the tree's own `pyproject.toml` with
  uv into Python 3.13, so the container runs what `pip install poolhouse` would, not a copy of the source edited by hand. The image is
  tagged by the hash of its inputs and reused until they change. A container reaches this machine's loopback listener at
  `host.docker.internal`; this machine reaches a container at the port Docker publishes on its loopback address.
* With `--wsl`, one more device behind NAT, described below.

Every node runs under the real supervisor (`node_launch`), is asked through its real local socket, and joins through the real join path
(`add_device` and `join_pool`, which `python -m poolhouse.node_join` calls) with an explicit address: Docker Desktop cannot carry
multicast to the machine it runs on, so discovery by beacon is not exercised.

## The steps

| step | what it proves |
|---|---|
| `join-open` | under `open`, a device on the same network joins with no code and is recorded as joined by `open`; a device only reachable through NAT or loopback is refused with the reason |
| `join-secure` | every device joins with a join code, one after another |
| `join-wrong-code` | a wrong code is refused, and the right one is refused after three wrong tries |
| `members-listed` | every device lists every other member by name and fingerprint, itself as `self`, with the address it dials where it has one |
| `entries-converge` | an entry written on any device is on every device |
| `direct-message` | a message to a session on another device reaches only that session |
| `claim-pool-wide` | a branch claimed on one device is refused to every other device naming the holder, and can be taken once released |
| `lease-queues` | an area held on one device queues the same request from another, and grants it when the holder lets go |
| `build-stale` | one device on an older build shows `catching up` and takes no work; every other device shows it `older`, and nobody else is flagged |
| `build-wire` | a device on another wire version is shown as needing an update with the command that fixes it |
| `build-update` | the real updater on a stale device fetches a newer build from a local git origin (git is real; only the compile is replaced), moves the node onto it, and the pool then shows it current |
| `join-order` | a chain forwards, a chain backwards and a star on the last device all end as one pool with one board |
| `member-removal` | after one member removes another, every other member (not only the remover) refuses that device's next request, and nothing it writes afterwards reaches them |
| `cert-swap` | a device that comes back at a member's address with a different certificate is refused by every member and by the pin of the members that dial it |
| `partition-heal` | a device cut off from the network (`docker network disconnect`) writes and claims a branch, so does the rest; after healing every entry is everywhere and exactly one device holds the branch |
| `crash-mid-sync` | the node of a device is killed with SIGKILL while 1500 entries are arriving; the supervisor restarts it and it ends with all of them and no damaged log |
| `tests-on-device` | `scripts/test --on` (the real `test_on` code) sends a tiny tree to a Linux device: refused until its person enables the feature and names the sender, then a test file that asserts it is Linux passes there; a device not named is refused |
| `tests-on-undialable` | a request for tests on a member the sender has no address for ends with the reason and the fix |
| `nat-board`, `nat-claims`, `nat-build`, `nat-tests`, `nat-reach`, `nat-removal`, `nat-setup-plan` | the device behind NAT (`--wsl`), below |
| `chain-of-dials` | in a chain of five, where only neighbours have each other's address, every entry still reaches every device |

### The device behind NAT (`--wsl`)

WSL2 puts its Linux behind NAT: it can dial out to the Windows machine and to the network, and nothing can dial in. The `--wsl` device is a
container on a Docker network of its own with nothing published, so no other device has a route to it, and it reaches the others the way
WSL2 reaches Windows (through the machine's loopback, where the other devices' ports are published). It also reproduces what the code can
detect: `/proc/version` and `/proc/sys/kernel/osrelease` are bind-mounted to say `microsoft-standard-WSL2`, `WSL_DISTRO_NAME` is set, and
stand-ins for `powershell.exe`, `wslpath` and `wsl.exe` answer the way Windows interop does for a Windows with no firewall rule, so
`python -m poolhouse.device_setup --dry-run` plans what it would on a real WSL (the Windows firewall rules, mirrored networking in
`.wslconfig`, and a WSL restart). Production code has no test-only branch: it reads the same files it reads on a real WSL.

What the pool does with a device that can only dial out:

* **Sync works.** One dial swaps pool records and boards in both directions, so a device behind NAT sends and receives every entry,
  claim and lease by dialing the members whose address it holds. This is `nat-board` and `nat-claims`.
* **Test runs needed the device to be dialed in, and failed** with a bare `Connection refused`. A device now keeps a long-lived
  connection to each member it dials, and the member sends it test requests down that connection (docs/node.md, "Reaching a device
  nothing can dial"). A member that has no address and no such connection says why and what to do. `nat-tests` is the proof.
* **Landing and leases** are board entries, so they follow sync and need no inbound dial.
* **Which members can reach it** is the members it dials: a join gives it one address (the member it joined), so a member it never
  dialed cannot send it requests. Entries and builds still reach and leave it through the members in between.
* **How each member is reached is shown.** Every member row says `direct` (this device dialed it and it answered), `channel` (it is
  reached only over the connection it opened), `none` or `self`, with the exact fix when it is not direct; for a device that reads as
  WSL2 on a private range the fix is mirrored networking (docs/windows-wsl.md). This is `nat-reach`; `nat-setup-plan` checks that the
  plan recommends mirrored networking.
* **Mirrored mode is untested by CI.** The `--wsl` container and every WSL job of CI are the NAT case (a GitHub runner's WSL is in NAT
  mode). WSL's mirrored mode, where the device shares the Windows network and is dialed like any other, has not been run by this rig.

## What it found that the loopback tests did not

Each is fixed at its root with a failing-first test (the Rust ones in `app/poolhouse-node/tests`):

1. A claim refused because another device holds the branch said `held by another session of yours`: only local holders were listed as
   blockers (`claims_foreign.rs`).
2. Two devices that each took a branch while they could not see each other both kept it after their boards met: the board's order only
   blocked new grants. The later acquire now ends as `lost` (`claims_foreign.rs`).
3. A device behind NAT could not be sent tests (`reverse.rs`).
4. A member whose address failed was dialed again every cycle, each dial waiting for the connection timeout in front of the members
   that answer. A failing member is now left alone for a second, doubling to a minute (`reverse.rs`).
5. A device never learned the build of a member it had not dialed itself (a chain, a NAT'd device), so it could be stale in a pool and not
   know. Members now tell each other the builds they know, shown as `relayed` until the device reports its own (`builds_relayed.rs`).
6. A device put out of the pool saw only `received fatal alert: HandshakeFailure`. It is told the member may have put it out
   (`refused.rs`).

Also seen, and not changed: a direct message addressed to the name a foreign sender is shown by (`name@d2`) is refused, while the bare
name works; devices with the same hostname (native devices on one machine) cannot be told apart by name, only by fingerprint; and the
address a member keeps for a device that joined it is the source address it saw plus the port the device announced, which is right on a
network and wrong behind a NAT that rewrites the source (the rig publishes each container's port as the same number on the machine so
that this works).

## How a device dials (and what N=5 shows)

Every device dials every member whose address it holds, each cycle, and swaps records and boards in both directions on that one
connection. Addresses are learned at the join and from a beacon and are not passed on, because an address is only good from where it was
seen; a device that joined by dialing member B has B's address, and B has the address the joiner announced. So a pool built as a chain
is a chain of dials, not every member dialing every member, and entries, removals and builds travel along it through the members in between (`chain-of-dials`
prints which device has an address for which). The cost of a member nothing can dial is a backoff, not a stall.

## What it cannot show

* **Windows.** Docker cannot run Windows. The Windows named pipe, ACLs, the Windows firewall and the supervisor on Windows are covered
  only by the `windows-latest` jobs of `.github/workflows/pool-check.yml` (a native pool, and the Windows device and a WSL
  device of one runner joined), and those have to run on GitHub. They have: the Windows node, its named pipe and every step that fits
  passed there (see "In CI").
* **A real WSL2 kernel and its networking modes.** The `--wsl` device is an Ubuntu container: `uname -r` (and so `platform.release()`,
  which the test farm's WSL marking reads) still says the container's kernel; mirrored mode in `.wslconfig`, Hyper-V's firewall, the
  Windows firewall prompt and Windows processes are not here. The `windows-and-wsl` CI job runs a real WSL distribution beside a real
  Windows node.
* **Discovery by beacon.** Docker Desktop carries no multicast to the device it runs on, and the rig must not use it on an owner's
  machine (it triggers the macOS Local Network prompt). Joining by beacon, and a beacon heard on a real network, are unverified here.
* **A real network:** wifi, VPNs, a router's NAT hairpinning, packet loss and latency beyond a Docker bridge.
* **Firewall prompts and permission dialogs** of any operating system.
* **Another CPU architecture:** the containers are the architecture of the machine Docker runs on (arm64 on an Apple Silicon Mac), so an x86 Linux build is not exercised.

## For agents

Before telling the owner to set up another device, run `scripts/pool-check` and fix what fails. Run it with `--wsl` when the device is
Windows or WSL. When a step fails, the output is the evidence: fix the Rust or Python at the root, with a test that fails first, and run
the step again. Do not weaken a step to make it pass. Say, in the report, which of "what it cannot show" still applies to the claim.

The rig is `tests/pool_check/` (Docker in `dock.py`, devices in `device.py`, the pool in `world.py`, the steps in `steps_*.py`) and its own
tests are `tests/test_pool_check.py`: the fast ones run in every tier, and the ones that start nodes are slow and opt-in
(`POOLHOUSE_POOL_CHECK=native` for nodes on this machine, `=docker` for containers too). It touches nothing of the account's state, leaves
no process, container, network or volume (`docker ps -a --filter label=poolhouse.pool-check`), and keeps only the image (`--clean`
removes it).

## In CI

`.github/workflows/pool-check.yml` runs the same steps without Docker: `native` on ubuntu, macos and windows runners (every device a node
on the runner); `wsl` on a Windows runner (a real Ubuntu WSL distribution with the nodes inside it); and `windows-and-wsl`, where a Windows
node and a WSL node of the same runner are joined, which is the Windows and WSL situation of the owner's own device. They run on a pull
request that touches the node, the test farm or the rig, on a push to `0.3dev` that does, and on demand.
All five jobs were green on runs 38098615088 and 38099163909 (2026-10-11).

### What each job proves

| job | devices | ran | skipped, and why |
|---|---|---|---|
| `native on ubuntu-latest` | three nodes on one Linux runner | every step that fits three native devices | `partition-heal` (needs Docker), the seven `nat-*` steps (need `--wsl`), `chain-of-dials` (needs five devices) |
| `native on macos-latest` | three nodes on one macOS (arm64) runner | the same | the same; the node, its Unix socket, `getpeereid`, the supervisor and the updater's real `git` on macOS |
| `native on windows-latest` | three nodes on one Windows runner | the same | the same; the named pipe, its access rule, the Windows supervisor and the updater's real `git` on Windows |
| `native in WSL Ubuntu on a Windows runner` | three nodes inside a real Ubuntu 24.04 WSL2 distribution | the same | the same; a WSL2 kernel and the node built and installed inside it |
| `a Windows device and a WSL device of one runner in one pool` | a Windows node and a node in WSL, joined: the WSL device reaches Windows at the address of the Windows side of the virtual switch (read from the default route), Windows reaches WSL at `127.0.0.1` (localhost forwarding) | `join-open`, `join-secure`, `join-wrong-code`, `members-listed`, `entries-converge`, `direct-message`, `claim-pool-wide`, `lease-queues`, `build-update`, `join-order`, `crash-mid-sync`, `tests-on-device` (12 of 12) | `build-stale`, `build-wire`, `member-removal`, `cert-swap`, `tests-on-undialable` (each needs three devices), plus the same Docker-only and NAT steps |

What none of them proves, beyond "What it cannot show": a removal, a swapped certificate and a stale device among *three* members across
the Windows and WSL boundary (that job has two devices); WSL's mirrored networking mode (the runner is in NAT mode, so the CI WSL job is the NAT case and mirrored mode is untested by CI); and a Windows machine
that is not a CI runner (a laptop's firewall profile, a VPN). A runner's loopback and virtual switch are not a LAN.

### What the first real runs found

Run 38092844765 failed three jobs; each was a defect of the rig, not of the node, except the one that is still open:

* **`build-update` on macOS (`no node answers`).** The step writes the runtime selection (`selected.json`) to make the device a checkout one
  commit behind, and the node's supervisor restarts the node whenever that file changes, so the updater's first question to the node met
  the old node closing (`the node closed the connection`) or no node. Linux and Windows won the race, a loaded macOS runner lost it, and
  the updater's report hid the reason. The updater now says why no node answered (`node_pool.ask`), and the rig waits until a new node
  answers and knows a member on a newer build again (a restarted node learns builds only by syncing).
* **The Windows and WSL job passed `--wsl-distro` and two more flags the script did not take.** `scripts/pool-check` now takes them, and a
  test parses every `scripts/pool-check` line of the workflow with the script's own parser, so the two cannot drift again. A job with two devices
  is also counted as two (it was counted as three, so three-device steps ran on two and failed), the Windows address in WSL is read from
  the route (the first line `wsl.exe` prints can be a proxy warning, which was taken as a host name), and the checkout path of the WSL
  device is spelled as a Linux path when the rig runs on Windows.
* **`member-removal` in WSL: `linux took an entry from the removed linux2`.** Not reproduced: the step passed in every later run of the same
  job and, on a Mac, seven times unloaded and four times with three busy processes per core. The step's refusal check accepts any error from
  the probe as a refusal, so a transient error can end the wait before a member has learned of the removal; the failure message now says
  what each member says of the removed device (standing, address, last error), so the next occurrence names its cause. Until it recurs with
  that output, this is an open question, not a fixed defect.
