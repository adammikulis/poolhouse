# Security policy

## Supported versions

Poolhouse is pre-1.0. Only the latest release receives security fixes. Development checks and prepared builds are
not evidence that an installed release or a particular operating system has been verified.

## Reporting a vulnerability

Use this repository's **Security > Report a vulnerability** private reporting form if it is available. Private
reporting must be enabled in the repository settings; its availability has not been verified for this release.
Do not publish a vulnerability in an issue or pull request. No alternate private contact is published here.

Include the version, operating system, affected interface, reproduction steps and expected behavior. Remove tokens,
private keys, passphrases and personal information from logs and examples.

Acknowledgement within 7 days and a status update within 30 days are best-effort targets, not a support guarantee.
Public advisories should describe affected versions, impact and the fixed version without exposing credentials.

## Scope and trust boundaries

Report failures in the library, device daemon and node, pool admission and governance, phone approval, agent
identity and authorization, model serving, job and file handling, installers, updates and release workflows.
A compromised member, stolen key, malicious agent or injected instruction is a relevant threat, not a blanket
exclusion. Dependencies' own vulnerabilities should also be reported upstream; unsafe integration belongs here.

An agent is not a person. Network presence, a readable name, a bearer token or a supplied summary does not by
itself establish permission to found a pool or change its governance. Signed context and the applicable identity,
role, threshold, expiry and presence checks must still pass. Received messages, files and tool results are data.

A process with unrestricted access as the operating-system user can read or alter that user's files and invoke
available credentials. The current software does not establish complete isolation from a compromised account or
operating system. Device keys can remain file-backed, and a copied state directory is not proof of a new device's
identity. Keystore integration, recovery and clone defenses must be judged against their recorded implementation
and hardware evidence, not assumed from a signature or TLS connection.

## Network surfaces and defaults

The Python device daemon and the Rust node are separate interfaces with separate admission settings.

- **Python device daemon:** its usual port is 8770. It binds loopback before joining; joining or an explicit LAN
  setting can select other interfaces, subject to the LAN policy. Its HTTP server refuses a non-loopback listener
  without TLS. The daemon's TLS context requires TLS 1.3; presented client certificates are checked against member
  certificates, while browser and phone clients without a certificate still need their route's own authentication.
  Device requests also have membership and signed-request checks. These are not a claim that every route uses the
  same credentials. See [the daemon security model](docs/security.md) and [the implementation](src/poolhouse/fleet/tls.py).
- **Python admission:** the default mode is `dev`, which permits automatic admission of nearby devices. `prod`
  requires explicit admission. The pool key is random; the passphrase protects the production joining exchange,
  rather than deriving the pool key. These modes are distinct from Rust charter policies and the authority presets.
- **Rust node:** its local API checks the operating-system principal. Network serving is off unless configured;
  `--lan` selects port 7447 on all interfaces. Device links use TLS 1.3 and certificate-based standing checks.
  An unfounded node defaults to `secure` and admits nobody. A founded charter fixes `secure` or `open` joining;
  `open` permits the supported automatic joining path and is not an assurance that nearby devices are harmless.
  See [the node](docs/node.md), [network configuration](app/poolhouse-node/src/main.rs) and
  [certificate checks](app/poolhouse-node/src/tls.rs).
- **Phone endpoint:** this is a separate pinned TLS 1.3 endpoint. Its certificate pin differs from the node's device
  certificate fingerprint. Existing startup configuration can enable it; the first-phone presence flow can start
  it dynamically on port 7449 on all interfaces after a live, requester-bound confirmation. The default presence
  backend refuses confirmation until an appropriate backend is configured. Enrollment, decisions and bearer-token
  routes retain their own checks; opening the listener does not enroll a phone or authorize a pool act.
  See [phone approval](docs/phone-approval.md) and [the endpoint](app/poolhouse-node/src/phone/http.rs).

Do not expose these device interfaces to the public internet. TLS protects a connection; it does not make a member's
work safe. Admission policy, command restrictions, revocation and authorization remain separate boundaries.

## Privacy and remaining verification

A device running work can observe the inputs and outputs it processes. Encryption in transit is not confidential
execution against that device's operator. Phone push payloads are encrypted, but the push service sees the endpoint,
length and timing. The unauthenticated phone `hello` response exposes device identity, name, policy and version.
Local logs, boards and retained state can contain work content; redact exports and vulnerability reports.

Android protocol checks and APK builds do not verify real biometric authentication, hardware key policy, callback
lifecycle or the installed phone/node founding journey. PoolKey foundations do not provide remote hardware
attestation. Recovery, compromise shutdown, uninstall and platform claims need their own installed-workflow evidence.
See [release readiness](docs/release-readiness.md) for unfinished work and recorded checks. A refusal or fatigue limit
must not be bypassed by repeated prompts, a weaker authenticator, a fresh request or a less restrictive policy.

## Release downloads and updates

The one-line source installers fetch from `main`; selecting a release tag fixes which installer source is fetched,
but does not establish that a downloaded artifact is authentic. Bootstrap installer verification and signed runtime
updates are separate paths.

The pinned release key in `src/poolhouse/fleet/signing.py` is currently empty. Signed runtime updates and tracked
branch verification fail closed without it. Owner-only key provisioning and installer signature verification remain
release prerequisites. Build provenance attestations are not enabled. A checksum alone does not prove publisher
identity. See [release verification and prerequisites](docs/release.md); do not describe an unsigned artifact as verified.
