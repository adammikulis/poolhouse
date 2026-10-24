# Passive macOS service observation

The observer and in-process bridge consume generated data from
`contracts/node/native_boundaries/service_lifecycle.toml`. The fixed planned containing app is `app.poolhouse.app`;
its planned plist is `Contents/Library/LaunchDaemons/app.poolhouse.node.plist`. There is no caller-selected service,
path, identity or authority. The binding is not an installed service identity.

Outside that containing app, observation returns `BindingUnavailable` before querying Service Management.
Inside it, bounded descriptor reads reject missing or unsupported plist layouts and inconsistent labels.
These are cheap context/metadata checks, not signature, signer or installation verification. Automatic passive
observation deliberately performs no whole-bundle seal scan. Mutable ancestor and OS lookup races remain possible;
the result is always untrusted. Genuine installed-scope/code/caller admission must independently verify its evidence.

The generated versioned C ABI transfers only integer availability/reason codes and a timestamp in the fixed
16-byte layout. A null output returns the generated failure code without observing; successful calls fully write
the app-owned output. Private Rust decoding validates all codes and the unavailable-clock sentinel. No strings,
allocator ownership, JSON, service scope or verification handles cross this boundary.

The app compiles a static Swift archive only on macOS and records optional diagnostic state from a single background
thread after normal window setup. There is no join, retry, public command, banner or startup gate. Failure to create
the diagnostic thread leaves its optional state unavailable. No observation registers, unregisters or starts a service,
requests consent, creates an identity, selects a runtime or changes a host. `RegisteredEligible` is OS eligibility only;
`RequiresConsent` cannot distinguish initial consent from withdrawal. These observations establish no readiness, startup mode or rights.

The genuine app must not be launched for isolated verification: its existing startup may start or replace the daemon.
The bounded harness instead links the production Swift archive and Rust mapper in an inert CLI, exercising only null
output and actual outside-app rejection. No fake containing app or positive installed-service result is manufactured.
Actual in-app eligibility remains unverified because the planned dedicated service installation is absent.
Build target metadata is link-input sanity information, not protected custody or reproducibility evidence.

Actual macOS 26.6 arm64 checks passed for the cheap observer (one test, 4.06 seconds) and the production static-archive
build plus real Swift/Rust FFI mapper (one test, 6.43 seconds). Only the null-output and genuine CLI negative paths ran.
The existing macOS 12 deployment floor and the macOS 13 Service Management availability guard remain unchanged;
current-hardware linking does not prove behavior on older macOS releases or a genuine installed containing app.

The maintained staged real-app build also compiled and linked successfully with `cargo test --no-run --locked --offline`
under one CPU admission (42.75 seconds); its executable was never launched. Production staging includes only the three
Swift sources and C header consumed by the build. App-only Swift/SDK metadata changes the staged source key so Cargo
reruns native compilation; the shared Rust dependency target and Node-only metadata behavior remain unchanged. This
cache invalidation is not hermetic-build or custody evidence. In-app/service behavior still requires separate proof.
