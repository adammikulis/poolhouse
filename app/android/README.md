# The Android app

Direct a Poolhouse computer from a phone: talk to Claude sessions, their subagents, local delegates and other agents as
conversations; see every device of the pool; follow and cancel work; start delegates and serve models. The app speaks only the
phone API of [docs/phone-api.md](../../docs/phone-api.md) to the computer it is linked to.

## What is on each screen

- **Chats** (first): every agent the computer sees, subagents under their lead, the ones that need you first; finished agents fold
  to one line and stay readable. Each row says how a message reaches the agent (at its next step, through its lead, read only).
  A conversation shows the history, new replies as the computer streams them, the tools run (grouped), the delivery of your
  messages, and an answer dock when the agent asks a question. A delegate can be stopped there.
- **Pool**: the one switch for this phone (off by default: the phone only directs), then every device with its state, GPU,
  memory, load, slots, models served, version, address and when it was last seen, with a detail screen. The linked computer's
  detail serves and stops models. Pause or resume the pool.
- **Work**: delegates, jobs and scheduled jobs with their state; a detail screen follows the record or log live and cancels.
- **Inbox**: questions waiting for you and the newest notices.
- **Direct** (the button above the bar): tell an agent, start a delegate on a project, serve or stop a model, pause the pool.
- **Settings** (the gear): the linked computer and the key words, unlink, light or dark.

## Linking

On the computer open **Phones** (or run `poolhouse phone link`) and scan the code, open its `poolhouse://link` address on the
phone, or type the computer's address and the 10 characters. The phone's key is made in Android Keystore (StrongBox where there is
one) and never leaves it; the computer's certificate is pinned; the link is kept only once the computer has proved it knows the
code. Android 17 asks once for the local network, which the app needs to reach the computer.

The app keeps the link, what you have seen and the theme. It stores no conversation, is excluded from backups, and talks to
nothing but the linked computer.

## Build and install

The app runs on Android 12 or later. Needed on the computer: an Android SDK with `platforms;android-37.0` and `build-tools;36.0.0` and its licence accepted by a person, and a JDK 17
or newer. Gradle is downloaded and checked by its SHA-256; the other libraries come from Google's and Maven Central's
repositories.

```
python app/android/build.py          # protocol checks and the debug APK
python app/android/build.py lint     # the same with Android lint (what the `android` workflow runs)
python app/android/build.py test     # the protocol checks only
```

The APK is `app/android/app/build/outputs/apk/debug/app-debug.apk`, signed with the debug key kept in Poolhouse's state folder
(`android/android-home`), so a later build updates the installed app. Install it with a phone that has wireless or USB debugging on:
`adb install -r <apk>`, or copy it to the phone and open it. A release APK needs `ANDROID_SIGNING_STORE` and
`ANDROID_SIGNING_PASSWORD` for a keystore whose alias is `poolhouse-preview`.

## Layout

| Module | What |
|---|---|
| `core` | plain Kotlin, no Android class: the phone API's shapes (`api/`), linking and signing (`link/`), pinned TLS and the stream (`net/`), the app's state (`store/`), the web UI's colours (`theme/`) and the on-device model's interface (`device/`) |
| `app` | the screens (Jetpack Compose), the Keystore key, preferences |
| `llm` | optional: a model on the phone. The app finds `app.poolhouse.llm.LlmPlugin` at run time; without the module it only directs |

## Checks

- `python app/android/build.py test` runs `core`'s tests on a JVM: the bytes of linking and signing against
  `tests/fixtures/phone-api-vectors.json` (computed by Python, checked by `tests/test_android_phone_api.py`), and the client,
  the stream and the state against a real TLS 1.3 server that verifies every signature, time and nonce.
- `tests/test_android_theme.py` holds the app's colours and navigation icons to the web UI's files.
- **Looking at it without a computer:** `python app/android/build.py sample`, then run
  `java -cp "$(cat app/android/core/build/sample-classpath.txt)" app.poolhouse.core.SampleDayKt 7450`. It answers canned data on
  loopback, prints a link for an emulator (`10.0.2.2`) and one for a phone through `adb reverse tcp:7450 tcp:7450`, and the app
  shows a **CANNED SAMPLE DATA** strip.

## Design

The layout takes its cues from mobile coding-agent apps, OpenCode's among them (its official Android app is public but names no
licence, so nothing of it is copied): a list of sessions first, a compact transcript with grouped tool steps, a composer with
delivery state, and a dock for what the agent asks. Colours, the mark and the navigation icons are Poolhouse's own web UI.
