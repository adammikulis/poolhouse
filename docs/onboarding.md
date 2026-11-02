# Onboarding devices on the local network

The first-run wizard offers a model download alongside the optional model server.
Choose a model, tick its download box, and select **Install and continue** to accept.
**Not now** leaves downloads for the Models screen. Downloads run in the background;
the model is loaded only when requested through the serving broker.

The default on a nominal 24 GiB or larger GPU is Qwen3.8 27B in UD-Q4_K_XL,
with its matching Q4_0 MTP head. The tier uses the rounded capacity of the largest
individual card, including cards reporting slightly below their advertised capacity.
It does not add memory from separate cards or use host RAM as VRAM. Shared GPU memory
uses its configured GPU limit. Smaller cards receive a smaller Qwen recommendation.
The wizard shows capacity and current free memory separately: a busy card can download
its model, and starting it checks free memory for weights, the head and its context.
The 27B weights and head are published together in
[the model repository](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/main).

On Linux with an NVIDIA GPU, the first-run server installer selects an official CUDA
release and its matching runtime archive. Both downloads require GitHub's SHA-256
digests. The installer checks that the resulting server initializes a CUDA device;
a CPU build is not substituted when CUDA installation fails.
The WSL download policy requires an available virus scanner. Install ClamAV and
current definitions with `sudo apt-get install clamav && sudo freshclam` before
downloading the server. Release archives retain their original SHA-256 checks;
contained SONAME library aliases are validated and copied as regular files at install.

The request: add a device to the pool without downloading Poolhouse by hand: fetch Poolhouse device to
device, then join the pool with the pool's short join code (`poolhouse-pool add-device` on a member,
`poolhouse-pool join CODE --host HOST` on the new device; docs/fleet.md, "Joining a pool").

## Decision record

1. **Nothing is pushed.** A machine with nothing installed gets an opt-in address, a file
   bundle, or an SSH install the owner starts with their own keys and approves host by host;
   every route ends in a checked manifest, never `curl | sh`.
2. **Files travel by signed manifest**, chunk hashes and a scan seam. Each file has a sharing
   level: `open`, `owner` (a gated model: only the owner's own devices, only after their licence
   acceptance is on record) or `never`. Credentials never travel.
3. **The signing key is generated on the controller and kept in the OS keystore (`keyring`).**
   Signing is automatic; export, rotation and revocation need a person at a terminal. Manifests
   last days.

## What is possible, and what is not

| The new device has | What can be done | By whom |
|---|---|---|
| Poolhouse already | join the pool with a short join code | a person at each device |
| nothing | **nothing can be pushed to it.** A machine that runs no agent of ours has no door; installing software on it without its owner doing something is what malware does, and Poolhouse will not do it | the owner of the new device, opt in |
| nothing, owner willing | open an address on it, run one command, or hand it a file bundle, or let us install over SSH with the owner's own keys | see "Machines with nothing installed" |

So "add devices without manually downloading Poolhouse" has an honest reading: the owner does
not *download* Poolhouse by hand and trust what they got, because the controller serves it,
pinned and verified. They still have to do one thing on the new device.

## Machines with nothing installed

Nobody pushes software to them. Opt-in paths, ranked by ease and then by safety:

1. **An address on the LAN** (`poolhouse pool bootstrap --share DIR`). The controller serves for
   ten minutes, over HTTPS with a certificate made for the offer, an unguessable address
   `https://IP:PORT/b/TOKEN/#fp=CERT-FINGERPRINT` (`--advertise` names the address to print). The
   owner types it (or scans it as a QR code: drawing one needs a library, not built; the string
   is `Offer.url`) on the new device. The page says what will be installed (names, sizes,
   SHA-256), the signing key and manifest digest, and shows one command:
   `curl --fail -k --pinnedpubkey 'sha256//...' -o poolhouse-install.py https://.../install.py && python3 poolhouse-install.py`.
   `-k` is intentional: `--pinnedpubkey` is checked even then, and the certificate has no name a
   CA could vouch for. The installer is fixed text (under 90 lines), pins the certificate, and
   refuses unless the manifest matches the digest printed on the page and every file matches
   its size and SHA-256, before it hands anything to `pip`. Honest limits:
   * **A browser cannot pin a self-signed certificate.** The `#fp=` fragment is for a human or
     the installer. The *authoritative* command is the one printed on the controller's terminal.
   * `pip` fetches the wheel's dependencies from the package index under normal certificate
     checks and **without hashes**; a hash-locked requirements file in the manifest closes
     this (designed, not built).
   * Offers carry program files only: models are refused (tested).
2. **SSH install** (`poolhouse pool bootstrap --ssh [user@]host --share DIR`, built, owner
   initiated, its own flag, never automatic). Properties, each a test:
   * the system `ssh` with the owner's keys, agent and config; `BatchMode=yes`, so a password is
     never asked for or seen; `StrictHostKeyChecking=yes` always, never `no`; no agent, X11 or
     other forwarding; timeouts; output cut at 64 KiB;
   * the target is checked character by character (`[user@]host`, no leading dash, no
     whitespace, no shell metacharacters; more than thirty hostile forms refused including
     `-oProxyCommand=...` and `host;rm`), and passed as one argv element after `--`;
     no shell string is built from input: both remote commands are constants;
   * the host key must already be in the owner's `known_hosts`, or `ssh-keyscan` fetches it, its
     SHA-256 fingerprint is shown, and the owner types it in full (or passes it with
     `--host-key-fingerprint` after reading it off that machine); only that key is then trusted,
     through a temporary `known_hosts` that is removed afterwards;
   * `--dry-run` prints the commands, the remote script in full (145 lines) with its SHA-256 and
     the files with theirs, and runs and contacts nothing and touches no key;
   * the payload goes over the SSH connection on stdin as a tar of plain files; the remote
     script (copied, then run) checks Python >= 3.12, macOS or Linux, not root, disk space, the
     manifest's **OpenSSH signature with `ssh-keygen -Y verify`** (the framing is written here,
     the Ed25519 signature is `cryptography`'s, and the real `ssh-keygen` verifies it in the
     tests; a pure-Python verifier would have been home-made crypto), that the key is the one
     the owner named, expiry, and the size and SHA-256 of every file, and that no other file came;
     only then a per-user venv (`~/.poolhouse/venv`, no sudo, no system Python touched) and
     `pip install`; the person then joins the new device to the pool with a code
     (`poolhouse-pool join CODE --host HOST`). Models are not copied;
   * nothing is fetched from the internet by the remote side except pip's dependencies (the
     same gap as above); macOS and Linux targets only, Windows OpenSSH designed not built;
   * sentinel events for each step (`onboard.ssh.*`), no secrets in logs.
   Tested: an exact-argv shim for `ssh`, `ssh-keygen` and `ssh-keyscan`; the remote script run for
   real against a temporary HOME with good, tampered, swapped, wrong-key, extra-file and expired
   payloads; a real venv install of a tiny wheel (slow test); a localhost sshd test exists but
   runs only with `POOLHOUSE_TEST_SSHD=1`.
3. **A file bundle** (USB stick, AirDrop; the output of `bootstrap --share`'s manifest plus the
   installer): the manifest digest is read to the person from the owner's screen.

All three verify with the same two ingredients: a pinned channel or a typed digest, and a
manifest whose files' hashes are checked before anything runs. None is `curl | sh`.

## Device to device distribution

A device that fetches holds the certificate of the machine it fetches from, the signing public key, and a
file-request key of its own. `onboard/transfer.py`:

* Any device serves the signed manifest and, in ranges of one chunk, the files in it
  (`GET /onboard/v1/files/NAME`, `Range` required, signed by `macauth`, over the pinned
  certificate; `ShareServer`). A request signed with a device's own key says which device asks.
* `Downloader` takes a manifest verified under the pinned key and a list of devices and fetches
  chunks from several at once. Every chunk is checked against its listed SHA-256 before it is
  kept; a device that sends one that does not match is dropped after a second such answer, a
  `critical` event is raised and the chunk is fetched from another device; if no device is left
  nothing is staged. The finished file is hashed whole again.
* Resumable: verified chunks are listed beside the partial file and each is hashed from disk
  before it is trusted on the next run. Disk: the file plus a 1 GiB reserve must be free first.
* The finished file goes to a **staging directory**, not to its home, and `on_staged(path,
  entry)` is called: that is where the guarded pipeline's scan and sentinel's `manifest.pin` /
  quarantine step belong.

**Who may have which file** (`onboard/sharing.py`; the level is a signed field of each manifest
entry, so changing it breaks the signature):

| level | who gets it | for |
|---|---|---|
| `open` | any member | programs, ungated models |
| `owner` | only a device the owner marked **mine**, and only once the owner's acceptance of that file's licence is on record (who, when, licence id, URL) | gated or licence-restricted models |
| `never` | nobody; each device downloads it itself from the entry's `source` | licences that forbid copies |

The owner who accepted a licence may put the model on their device #2; another person's device
in the same fleet may not have it. The first transfer of an `owner` file asks the person at the
terminal to type the licence id back, which writes the record; without a terminal nothing is
recorded and the file stays withheld. A file whose status is not known is `owner` and the owner
is asked; it is `open` only when it is known not to be gated and not to forbid copies
(`classify`). A manifest entry that does not say is read as `owner`. **Credentials (a Hub token,
an API key) are never copied between devices**: they are not files in a manifest or a field in
a grant, and a test fails if any onboarding module mentions one. The server refuses with a reason
(`403`), the client reports it without counting it against the device, and `onboard.transfer.withheld`
is raised.

Never from an unauthenticated source: the manifest must verify under the pinned key (signature,
expiry, serial no lower than the last seen, entry names through `safenames`), and the devices are
reached over pinned TLS.

## Signing keys

One Ed25519 key per controller, generated on first use. `onboard/signing.py`:

* **Wrapped under the keystore master key by default** (`docs/keystore.md`): the key sits in
  `signing.key.wrapped` in the state directory, sealed under the `fleet-signing` subkey for that
  directory; an older install's item in the OS keystore is moved there on first use and deleted
  once the file unwraps to the same key. With no usable keystore it is stored in a file
  encrypted under a passphrase (scrypt, ChaCha20-Poly1305, mode 0600) with a warning and a
  `warning` event; the passphrase comes from `POOLHOUSE_SIGNING_PASSPHRASE` or a prompt, and with
  neither it refuses rather than write a plaintext key. There is no plaintext path; tests scan
  every file under the state directory for the key's raw, base64 and hex forms.
* **Signing is automatic** when the owner shares or offers files: no prompt.
* **Export, rotation and revocation need a person at a terminal** (a human grant that follows
  sentinel's rule: stdin and stdout are terminals, no agent marker in the environment, the
  person types the key id back; it is kept as `onboard/human.py` until sentinel is merged).
  The commands are `poolhouse pool signing show|export|rotate|revoke|confirm|accept`. An agent
  has no path to the key.
* **Manifests are short-lived** (3 days) and carry a serial that may not go backwards.
* **Devices pin the public key**; its fingerprint is printed by `signing show` so a person can compare it.
  **Rotation is announced**, not applied:
  the old key signs a statement naming the new one, manifests carry the chain, and a member
  that sees a manifest signed by the successor reports `RotationAnnounced` and waits for `signing
  accept` (a human step) before it pins the new key. Revoked key ids travel in manifests.
* **High-assurance mode** (`signing confirm on`, a human step): each signing asks the person at the
  terminal first. A hardware key (a device that never releases the key) is the designed next
  step and is not built.

What this protects: the key leaking through a file copy, a backup or a repository. What it does
not: **code running as you can ask the keystore for the key** unless the operating system prompts
for each use; the keystore protects against theft of files, not against execution as you. If the
signing key is stolen anyway a thief can sign manifests members accept until the owner rotates
and each member runs `signing accept`; that recovery is by hand. What a signature vouches for:
the controller lists a file only after checking it itself (a wheel against the digest the release
or index publishes, through `httpguard`; a model through the guarded download and scan); it is
"the controller vouches for these bytes", not "these bytes are safe".


## Threat model

| Threat | Defence | Tested |
|---|---|---|
| Poisoned file from a device | signed manifest, chunk and whole-file hashes, device dropped, nothing staged on failure, then the caller's scan and sentinel | yes (scan hook seam only) |
| Path tricks in manifests and requests | `safenames` on every name; `safe_join` resolves symlinks; no suffix ranges | yes |
| Wrong signing key | pinned key only; expiry; serial rollback; key revocation list | yes |
| A device of another person gets a gated model | `owner` level needs a device marked mine and the licence acceptance on record; the field is signed | yes |
| A stolen signing key | keystore, short expiry, serial, rotation announced and accepted by a person, revocation list | yes (the keystore does not stop code running as the user) |
| Hostile SSH target or host | per-character validation, argv after `--`, no shell, host key typed in full | yes |
| Offer address guessing or reuse | 128-bit token, 10 minutes, lockout after 8 wrong in a minute, uniform 404 | yes |

### Out of scope

A compromised owner machine or signing key; physical access; what the upstream package index
or model host serve before the controller checked it; IPv6 and multi-subnet
networks; the internet; the Windows SSH targets; a web interface panel; phone
platforms; QR rendering; a hardware signing key; hash-locked dependency install.

## What is built

| Part | State |
|---|---|
| Signed manifest (Ed25519), chunked and resumable multi-member download, scan hook, sharing levels | built, two server processes on loopback, with pinned TLS |
| Signing key in the OS keystore, encrypted-file fallback, human-only export / rotate / revoke, confirm mode | built; the keystore is exercised through a real `keyring` backend over a file (the real Keychain is never touched by tests) |
| Bootstrap offer, pinned installer | built; installer run as a separate process; `curl --pinnedpubkey` checked against the real `curl` |
| SSH install | built; tested with recording shims for the argv, with the remote script run for real against a sandbox HOME, and a real venv install; **not run against a real sshd or another machine** (opt-in test exists) |
| CLI (`bootstrap share fetch signing`, all `--json`) | built; end-to-end tests across separate processes |
| Sentinel events | emitted for each step (list below) |
| QR drawing, mDNS, Windows SSH target, hardware signing key, web panel, hashed dependencies | designed only |

Events (all on `onboard.events.BUS`; `Event(kind, severity, subject, evidence)`):
`onboard.transfer.bad_chunk` (critical),
`onboard.transfer.bad_file` (critical), `onboard.transfer.member_dropped`,
`onboard.transfer.withheld`, `onboard.transfer.staged`, `onboard.bootstrap.offered`,
`onboard.bootstrap.refused`, `onboard.signing.created|signed|exported|rotated|revoked|file_fallback`,
`onboard.ssh.started|hostkey_confirmed|hostkey_refused|copied|verified|installed|listening|failed`.
Evidence holds short facts, never a code, key or secret. Sentinel subscribes with a six-line
adapter on its own side (it imports none of its sources):
```python
def watch_onboarding(bus, sentinel, Event, Severity):
    return bus.subscribe(lambda e: sentinel.bus.emit(
        Event(e.kind, Severity.parse(e.severity), "onboard", e.subject, e.evidence, e.ts)))
```

## Devices on other networks (Tailscale)

If this machine already runs Tailscale, a member that is not on the local network is
reached over the tailnet. The tailnet is a route and nothing else: the node's pool membership, the pinned
certificate, the signed manifest and the signed requests decide who is trusted, exactly as on a
LAN, and no port is opened to the public internet.

- **Detection** (`fleet/tailnet.py`) is read-only. It looks for the `tailscale` command (on
  `PATH`, or the macOS app's copy) and runs `tailscale status --json`, never anything else (a
  test records the arguments of every call). Output is capped at 1 MiB and 3 seconds, the child
  gets the environment without secret-looking variables, and only these fields are copied out:
  state, this device's tailnet addresses, and per device its host name, MagicDNS name, tailnet
  addresses and online flag. Auth URLs, keys and tokens in the output are never read into a
  result or a log. An address outside 100.64.0.0/10 and fd7a:115c:a1e0::/48 is dropped; names
  are reduced to printable text.
- **Reaching a device** (`fleet/onboard/routes.py`): the address it was last reached at is tried
  first (`lan`), then its tailnet address (`tailnet`), then nothing (`unreachable`). Each try
  is a TLS connection that must present the device's pinned certificate, after the usual check
  that the address is not public (`lan.py`; tailnet ranges pass, public addresses never do).
  A tailnet device is a candidate when it is online and goes by the device's name; a device that
  merely has the same name but another certificate is not reached.
- **Where a tailnet address comes from.** From `poolhouse-pool devices --learn`: a status device is stored for a
  device only after its certificate matched there. An announcement never sets one; `learn_tailnet`
  refuses any other source.
- **Names.** `poolhouse pool fetch NAME --from DEVICE:PORT` accepts the name of a member. Names
  are looked up in the device list `tailscale status` reported and nowhere else: no DNS query for
  an arbitrary name is made.
- **Where the tailnet is used.** Every path to a member goes through `routes.py`: `pool fetch` and the
  `bootstrap --ssh` target. In each, a public address is
  refused and a TCP-only path (SSH) leans on the host key the owner confirmed.
- **Without Tailscale** nothing changes: no command is run when `poolhouse-pool devices` or `fetch`
  does not need the tailnet, and a device is `lan` or `unreachable`.

Not done: installing Tailscale, logging in or starting it, Funnel or Serve, ACL management, exit
nodes, a daemon that listens on the tailnet interface only, and checking the caller's tailnet
identity (the pinned certificate and the signed requests are what is checked). Other overlays
(WireGuard, Headscale) work as plain addresses.

## Commands

```
poolhouse pool bootstrap --share DIR [--valid 10m]                 offer poolhouse on an address
poolhouse pool bootstrap --ssh [user@]host --share DIR [--dry-run] [--host-key-fingerprint SHA256:...]
poolhouse pool share --dir DIR [--sharing NAME=open|owner|never] [--licence NAME=ID,URL] [--source NAME=URL]
poolhouse pool share --models [--models-dir DIR ...] [--port 8773]  serve the model store
poolhouse pool fetch NAME... --from HOST:PORT                      fetch into staging (not installed)
poolhouse pool signing show|export FILE|rotate|revoke KEYID|confirm on|off|accept
```

All take `--json` and `--state DIR`. State is under `<state root>/onboard` (devices,
licences, the public half of the signing key), every file
private; the signing key itself is in the OS keystore.

## Decisions for the owner

1. **Signing key** (decided: keystore, signing automatic, human-only
   export / rotation / revocation, short manifests, high-assurance mode available). Still yours:
   whether to turn confirm-before-signing on by default, and when to build the hardware-key path.
   The honest limit: code running as you can use the keystore unless the OS prompts.
2. **Ship the address/QR bootstrap?** It is the easiest path and the weakest in a browser.
   Recommended: ship it with the terminal command as the authoritative one; QR left out.
3. **SSH install** is built, owner-initiated and off unless `--ssh` is given. It was tested
   without a real sshd; run it once on a spare machine with `--dry-run` first.
4. **Licence records**: the first transfer of a gated file asks the person at the
   terminal to type the licence id back. Deciding `gated` / `forbids_copies` from Hub metadata is not built:
   the owner names the level with `--sharing`.
