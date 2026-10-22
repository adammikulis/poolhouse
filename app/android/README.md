# The Android app

The phone a person approves from (docs/phone-approval.md). An agent asks, the phone shows the request in plain words, the person
approves with a fingerprint, and the phone's key signs exactly the text the person saw. The app also keeps the chat and status of
the computer's Fleet page.

## What it does

- **Add this phone** by scanning the code `poolhouse-workspace phone add` shows (or the one an agent hands over as an image or a link).
  One scan: the app makes its keys, signs its own addition behind one fingerprint prompt, and stores everything else silently.
  A phone added later waits for one of the phones already added to approve it.
- **Requests**: the home screen lists what waits, newest first, kept current by a request the computer holds open. Tapping one shows
  the whole text: what looks off, the pool line (open or secure), the risk, who asks, what is asked and how far it reaches, what it
  cannot do, why, what a deny does and when it ends, and below it what the signed request itself asks, read from the structured asks the
  key signs. Approve and Deny each ask for the fingerprint, strong face unlock or device credential once (to approve a request above routine it asks for a strong
  fingerprint or face and no PIN, and a phone without one approves nothing that is not routine); a request that is not routine
  then asks which of three numbers the agent showed, a scope declined before asks "Approve anyway?" first, and a request an agent made also
  offers Deny and block. What an agent asked on its own, notices about requests kept back and what came too fast to be pushed are in a
  quiet list under the requests.
- **Notifications**: the node seals each request to the phone's push key (Web Push payload encryption, RFC 8291) and posts it to the
  address a UnifiedPush distributor gave the app. The lock screen says only `Approval waiting from <agent>`; once unlocked the
  notification shows the whole text with Approve and Deny, and Approve opens the fingerprint prompt (for a routine request) or the
  whole request first (for any other, and for any the node flagged). A notice about requests kept back is on a channel with no sound,
  no heads-up and no badge. Without a distributor the app still works: requests show while it is open.
- **Phones**: list the phones, remove one (another phone's fingerprint confirms), add another phone (a code to scan on it).
- **Chat** with the computer: the status and streamed chat of Fleet, behind the fingerprint, over pinned TLS.

## Keys

| Key | Where | Needs the person |
|---|---|---|
| Approval key, ECDSA P-256 | Android Keystore, StrongBox where the phone has it | the fingerprint, strong face unlock or device credential, every signature; lost on a new fingerprint |
| Push key, P-256 | a software key sealed by a Keystore key | nothing: it must open a notification while the screen is locked |
| Token for the computer | in the same sealed file | nothing; it reads requests and cannot decide one |

The app never receives a biometric or a PIN, stores no image, is excluded from backups, and hides its screens from screenshots and
the recent-apps view. The minimum is Android 11 (API 30).

## Layout

`src/main/java/app/poolhouse/phone/`: the protocol classes (`Canonical`, `Challenge`, `ApprovalText`, `ApprovalView`, `AddLink`,
`AddRequest`, `WebPushCipher`, `PushPayload`, `NotificationModel`, `PushHandler`, `Decider`, `NodeApi`, `ApprovalFlow`) use no
Android class and are checked on a JVM; the rest are the screens and what talks to the platform (`MainActivity`, `ApprovalActivity`,
`PhonesActivity`, `ChatActivity`, `ScanActivity`, `Notifier`, `UnifiedPushChannel`, `PushReceiver`, `PhoneStore`, `ApprovalKey`,
`BiometricAuthorizer`).

## Build and checks

Build from this directory with an installed Android SDK and JDK. Select a stable Android Gradle plugin through
`-PandroidPlugin`, and the installed platform through `-PandroidApi`. `python build.py GRADLE PLUGIN API debug` runs the protocol
checks and builds the debug APK; without `debug` it also lints and builds the release APK, which needs `ANDROID_SIGNING_STORE` and
`ANDROID_SIGNING_PASSWORD` for a signing keystore whose alias is `poolhouse-preview`. The maintained CPU broker must admit builds and
protocol checks.

`gradle protocolChecks` runs without a device: `PhoneProtocolTest` (the chat link, pinned HTTPS and the chat setup) and
`PhoneApprovalTest`, which checks the bytes a key signs against `tests/fixtures/phone-approval-vectors.json` (made by another
implementation), the RFC 8291 example, what a request is checked for before the fingerprint is asked, every part of a notification,
what a push channel carries (ciphertext only), and the friction budget in `contracts/phone-friction.json`. `AdmitTest` checks
the listing of a phone added to a pool as an admin key against `tests/fixtures/phone-admit-vectors.json` (made by the device): the terms
a code carries, the new key's acceptance of them, the act the approving phone checks before its second fingerprint, and the two extra
fingerprints in `docs/phone-approval.md`.

Android SDK downloads require a person to accept the current SDK license at
https://developer.android.com/studio#downloads before downloading the command-line tools.
The selected SDK packages prompt for person approval during setup; do not run an automatic blanket license
acceptance. Keep SDK, Gradle caches, and signing material outside the checkout.

## Checks on a real phone

The camera, the fingerprint prompt, the notification and the push distributor need a phone; the list is in docs/phone-approval.md,
"Checks on a real phone".
