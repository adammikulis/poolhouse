# Tailscale

Devices on different networks reach each other over Tailscale, with no port forwarding: pool devices, a linked phone and a
guest. Poolhouse carries its own Tailscale node, and reads an installed Tailscale app where there is one. This page gives the
choice and its sources, what the node exposes, and which parts are built.

## What is built

| Part | State |
|---|---|
| Reading an installed Tailscale app (`fleet/tailnet.py`, `fleet/onboard/routes.py`; macOS, Windows, Linux) | built |
| The helper `app/poolhouse-tail`: which callers reach which port, where it dials, its commands | source and unit tests; not compiled or run, and `go.sum` is not written |
| Starting the helper from the daemon, shipping it in the Mac and Windows apps, building it in a source checkout | not built |
| The node accepting forwarded connections with the caller's own address | not built |
| Sign-in, renewal and share screens | not built |
| The Android app carrying the node | not built |
| A live check on two real devices and a second Tailscale account | not done |

Until the rows above are built, a device crosses networks only through an installed Tailscale app
([onboarding.md](onboarding.md), "Devices on other networks").

## The choice

Poolhouse embeds Tailscale's Go library, `tsnet`, in a helper of its own on a Mac and on Windows, and in the Android app.

| Route | Administrator or system step | What a shared friend can reach | Verdict |
|---|---|---|---|
| Embedded `tsnet` node | none on any platform; one sign-in | only the ports Poolhouse listens on | chosen |
| Official Tailscale app, installed by Poolhouse | macOS: installer and a VPN configuration approval. Windows: installer. Android: an install tap and the VPN consent dialog | every service of the device that listens on the tailnet or on every interface, unless the account holder writes an access rule | not installed; read when present |
| A private `tailscaled` in userspace networking mode | none | every service listening on `127.0.0.1`, because unhandled inbound connections are forwarded there | refused |
| `libtailscale` (the C library around `tsnet`) | none | as `tsnet` | refused: its documentation names no Windows support |
| `tailscale-rs` (Rust) | none | as `tsnet` | refused: a preview its authors say not to use in production, without an external security audit, and without Android |

Sources:

* `tsnet` embeds Tailscale in a Go program over a userspace network stack and prints a sign-in address when it has no key:
  <https://tailscale.com/kb/1244/tsnet>, <https://pkg.go.dev/tailscale.com/tsnet>.
* A `tsnet` node rejects a connection to a port it does not listen on. `getTCPHandlerForFlow` says "don't handle, don't forward
  to localhost": <https://github.com/tailscale/tailscale/blob/v1.104.7/tsnet/tsnet.go>.
* `tailscaled`'s own stack sends such a connection to `127.0.0.1` on the same port (`acceptTCP`):
  <https://github.com/tailscale/tailscale/blob/v1.104.7/wgengine/netstack/netstack.go>.
* A new tailnet lets its devices all reach each other (<https://tailscale.com/kb/1018/acls>), and a source of `*` includes
  `autogroup:shared`, the people a device is shared with (<https://tailscale.com/kb/1337/policy-syntax>,
  <https://tailscale.com/kb/1084/sharing>).
* Android runs one VPN at a time and asks the person before an app's first connection:
  <https://developer.android.com/develop/connectivity/vpn>. The embedded node uses no VPN service.
* Official app installs: <https://tailscale.com/kb/1016/install-mac>, <https://tailscale.com/kb/1065/macos-variants>,
  <https://tailscale.com/kb/1022/install-windows>.
* `libtailscale`: <https://github.com/tailscale/libtailscale>. `tailscale-rs`:
  <https://tailscale.com/blog/tailscale-rs-rust-tsnet-library-preview>, <https://docs.rs/crate/tailscale/latest>.

What the choice costs: each device is one more machine in the tailnet, named `poolhouse-<name>`; each signs in once; a node
works only while Poolhouse runs; the build needs Go; the Android app grows by the library and has to hand it the network
interfaces, which Android denies to Go's own lookup (`netmon.RegisterInterfaceGetter`,
<https://github.com/tailscale/tailscale/blob/v1.104.7/net/netmon/state.go>).

## The route is never an authority

The helper copies bytes between the tailnet and a listener on `127.0.0.1`. It never ends TLS. Pool membership, the pinned
certificates, signed phone requests and guest credentials decide every request, as on a local network. Tailscale identity is
read for one thing, and only to refuse: the pool and phone ports.

| Service | Tailnet port | Callers the helper lets through |
|---|---|---|
| pool | 7447 | devices signed in as the same Tailscale user as this node; no tagged device |
| phone | 7450 | the same |
| guest | 7453 | anyone the account's access rules let reach the node, shared friends included |

No other port exists on the node. The web interface and model servers are never exposed. The helper dials only a tailnet
address (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) or a `.ts.net` name, and only on those three ports. The local ports it opens
for dialing listen on `127.0.0.1`.

Consequences:

* A pool member that belongs to another Tailscale user does not reach the pool port through the embedded node.
* A forwarded connection arrives from `127.0.0.1`. No listener may admit or trust a caller for that address. The node's
  per-address limits see every forwarded caller as one address until it receives the caller's own address.

## What listens where

* Poolhouse's services listen on `127.0.0.1` for the helper, and the three ports above exist only inside the node's userspace
  stack. The helper opens nothing on `0.0.0.0` or a public address for them.
* WireGuard itself uses one UDP socket on all interfaces, as every Tailscale node does. It answers keys of the node's own
  tailnet and the people it is shared with.
* The helper turns the router port mapper off (`TS_DISABLE_PORTMAPPER`), so it asks no router to open a port. Connections go
  directly where NAT traversal allows and through Tailscale's relays otherwise.
* The helper turns Tailscale's log upload off, which Tailscale says limits the support it can give
  (<https://tailscale.com/kb/1011/log-mesh-traffic>).

## An existing Tailscale setup

The node keeps its state in a folder under Poolhouse's state, drops every `TS_` and `TSNET_` environment variable before it
starts (an inherited auth key or control address would join it elsewhere), and runs no `tailscale` command. An installed app,
its sign-in, its access rules and the system's network settings stay as they are. The detector runs only
`tailscale status --json`.

## Signing in

No terminal command is involved.

1. The app shows "Sign in to Tailscale" with a button that opens Tailscale's sign-in page and a QR of the same link, so a phone
   can finish the sign-in for a computer. The link lets whoever opens it add this node to their account, so it is shown and
   never logged.
2. The account holder signs in with their identity provider and approves the device where the tailnet requires approval.
3. The app says, in these words: "This computer appears in your Tailscale account as one more machine, poolhouse-<name>. Its
   sign-in lasts 180 days." The renewal is one button that opens the same sign-in page. Key expiry is the tailnet's setting
   (1 to 180 days) and can be turned off per machine in the admin console (<https://tailscale.com/kb/1028/key-expiry>).

The free Personal plan covers 6 users and unlimited user devices (<https://tailscale.com/pricing>).

## Sharing one device with a friend

Tailscale's device sharing gives one person, in their own tailnet, access to one machine (<https://tailscale.com/kb/1084/sharing>):

* The sharer is an Owner, Admin or IT admin. A one-person tailnet's owner is its Owner.
* An invite is a link, `https://login.tailscale.com/admin/invite/<code>`. A single-use link works once; a reusable one up to
  1,000 times; an unused one expires after 30 days. Anyone holding the link can accept it.
* The friend signs in to their own free account and accepts. They see the machine by its full `.ts.net` name, and its IPv4
  address can differ from the one the sharer sees.
* A shared machine accepts connections from the friend's tailnet and cannot start one. The guest dials the host, so guest
  access needs nothing else. A tagged machine cannot accept a share.

The API creates invites (`POST /api/v2/device/{deviceId}/device-invites`) and accepts them
(`POST /api/v2/device-invites/-/accept`). Its specification (<https://api.tailscale.com/api/v2?outputOpenapiSchema=true>)
refuses both to tokens made from an OAuth client, because a share belongs to a user. That leaves a personal access token with
full access to the tailnet, made by hand in the admin console and valid for 90 days at most. Poolhouse stores no such token.

The flow:

1. The host presses "Invite a friend". The app opens the admin console's Machines page, where the host chooses the
   `poolhouse-<name>` machine, Share, and Copy invite link.
2. The link goes back to Poolhouse by paste, or from the phone's share sheet.
3. Poolhouse shows one QR with the invite link, the machine's `.ts.net` name and address, the pinned certificate's
   fingerprint and a one-time secret.
4. The friend scans it, accepts on Tailscale's page, and their Poolhouse, signed in as the same account, connects.

Removing the guest in Poolhouse ends their access at once, whatever the share says. Removing the share is done in the admin
console. The deep link to a machine's page is not a documented address; the Machines page is the documented place.

The account's access rules apply to a shared friend. A tailnet with rules of its own can block the guest port; Poolhouse says
so and edits no rule.

## What others can see

| Who | Sees | Does not see |
|---|---|---|
| Tailscale's coordination server | account emails, machine names, operating system, IP addresses, public keys, client version, access times, who shares with whom | traffic content; private keys never leave a device |
| Tailscale's relays (DERP) | encrypted WireGuard packets, which node keys talk, IP addresses, timing and volume | anything inside the encryption |
| A friend who accepted a share | the shared machine, its name and the physical addresses needed to connect | the sharer's other machines |
| The host who shared with a friend | the friend's email address and picture | the friend's other machines |

Sources: <https://tailscale.com/blog/how-tailscale-works>, <https://tailscale.com/kb/1232/derp-servers>,
<https://tailscale.com/privacy-policy>, <https://tailscale.com/kb/1084/sharing>.

A coordination server that added a node to the tailnet would reach the three ports and meet Poolhouse's own TLS and
credentials. Tailnet Lock (<https://tailscale.com/kb/1226/tailnet-lock>) is the account holder's choice; Poolhouse does not
change it.

## The helper

`app/poolhouse-tail` is one Go package (`tail`) with a command around it. Packaged apps (Mac, Windows, Android) ship it
built, so a person's device compiles nothing. A source checkout builds it with a pinned Go toolchain that Poolhouse downloads
on first use. Go `1.27.4` and `tailscale.com v1.104.7` are the pins in `go.mod`.

The command takes `--dir` (the node's state folder) and `--name` (the device's name). It reads one JSON request per line from
standard input, writes one reply per line, and ends when its input closes, so it never outlives the daemon that started it.

| `op` | Fields | Reply |
|---|---|---|
| `status` | | `status`: state, sign-in link, `.ts.net` name, addresses, key expiry |
| `signin` | | `status` with a fresh sign-in link |
| `signout` | | |
| `expose` | `service`, `target` (a `127.0.0.1` port) | |
| `unexpose` | `service` | |
| `forward` | `address` (tailnet address or name, with port) | `local`: the `127.0.0.1` port that leads there |

On Android the same package is bound into the app (`gomobile`), which calls `Do` with the same requests. The phone only dials
out. Android 17 asks for a local network permission for apps that target it
(<https://developer.android.com/privacy-and-security/local-network-permission>).
