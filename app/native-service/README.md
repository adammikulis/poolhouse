# Passive macOS service observation

`PassiveServiceObserver.observe()` implements only the separate passive availability shape in
`contracts/node/native_boundaries/service_lifecycle.toml`. It accepts no caller-selected path or service label.
The fixed planned containing app is `app.poolhouse.app`; its planned plist is
`Contents/Library/LaunchDaemons/app.poolhouse.node.plist`. That binding is not an installed service identity.

Outside that containing app, observation returns `BindingUnavailable` before Security or Service Management queries.
Inside it, bounded descriptor reads reject missing, substituted or unsupported plist layouts; a consistent static
bundle seal and matching signing label are required before querying registration status. The explicit no-network
validation flag does not establish an approved signer: an ad-hoc lookalike may satisfy consistency checks.
Held descriptors do not eliminate all mutable ancestor or Service Management lookup races. Every result is
non-authoritative and timestamped, never installation proof, a service scope or a launch right.

`RegisteredEligible` means OS eligibility only. `RequiresConsent` does not distinguish initial consent from withdrawal.
No result establishes readiness, a startup mode, `Neither`, a principal, endpoint, storage or product approval.
No registration, unregistration, start, native prompt, key operation, runtime selection or host replacement occurs.
A clock failure returns `Unknown` with zero timestamp as an unavailable-clock sentinel.

The internal verification entry point is compiled only with `SERVICE_INSPECTION_VERIFY`; it is not packaged as an app.
The focused selector `tests/test_native_service_inspection.py` compiled the real SDK and exercised the actual
outside-app rejection on macOS 26.6 arm64: one pass, 2.86 seconds. It also checked the four documented OS-status
projections as pure values. This is negative-path hardware evidence, not a genuine in-app positive observation.
The planned dedicated app/plist installation is absent; scoped inspection and activation remain unavailable.

The first SDK compile failed because the C option name imports as Swift `SecCSFlags.noNetworkAccess`.
Using that documented member fixed the compiler error without removing the no-network bit. No failed executable
was run. Source and exact verification harness were independently reviewed before execution. No fake containing app,
registration or privileged activation was manufactured for a positive result.
