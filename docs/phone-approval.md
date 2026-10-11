# Approving from a phone

A person's phone is the second way a person signs. Today a person's signature on a node is a typed `yes` at `/dev/tty`
(docs/node.md, "Key custody"). With the phone, an agent asks, the phone shows what is asked in plain words, the person
looks at it and approves with a fingerprint, and the phone's key signs exactly the text the person saw. Nothing the
person types, nothing an agent can click or call completes it. The typed `yes` stays as an alternative signer: both are
person keys enrolled on the node, and the node cannot tell which one made a signature except by the key.

This page is the protocol (what is signed, what is refused, what is stored), the delivery of the notification, the
friction budget, and the checks only a real phone can make. The app is `app/android` (docs: `app/android/README.md`);
the node side is `app/poolhouse-node/src/approvals/` and `phone/`; the agent side is `poolhouse.board`
(`Board.request_approval`) and `poolhouse-workspace approval`.

## What was decided

- **The authenticator is not a six-digit code.** A code is phishable and binds no action: it says "someone with this phone
  is here", not "the person agreed to this". Each approval is a signature over a challenge that carries the node, the
  action, its plain-words text, the requesting session, a nonce and an expiry, and the node accepts it once.
- **The key is a person key.** The phone makes an ECDSA P-256 key in Android Keystore (hardware-backed where there is
  hardware, StrongBox where the phone has it) that signs only after the fingerprint or the device credential, once per
  use, and is lost on a new fingerprint. Its public half is enrolled on the node in `persons.json` beside the Ed25519 keys
  a typed `yes` uses (`alg: p256`). Grants it signs count exactly as grants a person typed `yes` for.
- **The hard floors do not move.** An agent never approves, never acts as a person, and `grant_issue`, `grant_revoke`,
  `person_enroll`, `act_as_person` and the other floors (docs/node.md, "Standing grants") are refused by name whatever
  anyone signs. An agent can ask; it cannot complete.
- **Push carries content nobody but the phone reads.** The notification shows everything needed to decide, so the
  node encrypts the whole approval to a key only the phone holds (Web Push payload encryption, RFC 8291) and the push
  service sees ciphertext and an opaque id. See "Delivery".

## Who holds what

| Secret | Where it lives | What it can do | Gate |
|---|---|---|---|
| Approval key (P-256) | Android Keystore, StrongBox when present; never leaves the phone | Signs a decision, a phone's own addition, a removal | Fingerprint or device credential for every signature; invalidated by a new fingerprint |
| Push key (P-256 with a 16 byte secret) | A software key wrapped by a Keystore key that needs no prompt | Decrypts the notifications the node sends this phone | None: it must work while the screen is locked, and it reads only what a notification shows |
| Phone token (32 random bytes) | Wrapped by a Keystore key that needs no prompt; the node keeps its SHA-256 | Reads the approvals, lists phones, updates the push address | The token alone decides nothing: a decision needs the approval key's signature |
| Enrolment secret | In the QR code, once | Adds one phone, within ten minutes | 256 random bits, spent by the one use that matches; a wrong guess is counted against its address (ten in ten minutes), never against the code |

The token and the push key are not secret from the phone's own apps' point of view; they are secret from other phones, other
people and the network. What matters is that none of them can approve: only the approval key signs, and it signs only after
the person's fingerprint.

## Adding a phone

One scan. The code carries what the phone needs to find the node and be sure it is the right one: the endpoints, the
SHA-256 of the node's phone certificate, and a one-time secret. The app does the rest silently: makes the keys, signs the
phone's own addition (this is the one fingerprint prompt of the first run), stores the token and registers the push address.

```
poolhouse://add-phone?data=<base64url of {"v":1,"pin":"<64 hex>","endpoints":["https://192.168.1.4:7449",...],
                               "secret":"<43 characters>","expires":<unix seconds>,"name":"<device name>","policy":"open|secure"}>
```

**The first phone.** A person runs `poolhouse-workspace phone add` once on the computer (a person's command: it refuses a
process an agent started, like `grants-setup`). It leaves the hash of a one-time secret beside `persons.json`, as
`grants-setup` does (`phone_offer.json`), and draws the QR code in the terminal; it writes no image file for the first phone,
because a file could be read by any program that runs as the person and the first phone is added on the strength of the code
alone. The person scans it. The phone's key is enrolled directly: the secret is the proof that someone at the computer asked
for it. Nothing else is typed and no `yes` is asked, because the person's phone must scan a code that appeared only on that
computer, within ten minutes, once, and approve with a fingerprint. The command then prints which phone was added and its
key as six groups of four characters, so a code someone else scanned is seen at once and removed ("Removing a phone"). A code
made through the node (`phone_offer`) is refused for the first phone.

**Another phone, later.** The node enrols a second key only when a key it already holds approves it, so a code that an agent
or a bystander saw cannot add a phone by itself. The code is made by the person's command, by an agent (`phone_offer`,
allowed once a person key exists, three an hour for each session), or by the app of a phone already added ("Phones and
notifications", "Add another phone"). The new phone scans it and the node puts "Add the phone `<name>`, key `7f3a 91c2 d04e
5b18 a6c3 7e20`" in front of every phone already added (24 characters of the key, 96 bits, so no key can be ground to look like
another). One tap and one fingerprint on one of them adds it. The text says what it means: the new phone approves everything
this one can. A code asked for through the node records who asked, and the request then carries a `FLAG:` line: "The code this
phone used was made by the session `<name>` on board `<board>`, not by you at the computer. Approve only if you scanned it
yourself just now." A code that is waiting to be scanned is never replaced by one a session or a phone asks for, so an agent
cannot take over the code the person is showing.

**Handing the code over.** An agent can make the code and hand the person an image or a link (`poolhouse-workspace phone
offer --image FILE`, `Board.offer_phone()`, and `phone add --image FILE` once one phone is added); a link opens the app. What an
agent holds is the secret, which adds a phone only after an existing phone approves it, and the approval says the agent made
the code, so it is safe to hand over. The first phone is the one exception and is a person's, with no image.

**The code is a bearer secret for ten minutes.** Anyone who photographs it can add their phone to the pending list; on the
first phone that is an addition with no second approval, on every later phone the person sees the new phone's name and key
before approving. The terminal says what was added, and "Removing a phone" ends it.

## Asking for an approval (the agent)

```python
board = ph.board.connect()
ask = board.request_approval([ph.board.Ask("add_device", "*", lasts="10m", uses=1)], note="adding the Linux box")
ask.wait(timeout_s=60)     # "waiting" is not a failure: the same request is on the phone until it expires
```

`poolhouse-workspace approval request ACTION[:RESOURCE] [ACTION[:RESOURCE] ...] [--for 10m] [--uses N] [--note TEXT] [--wait 60]`
prints a handle (`ap-` and 16 hex digits) and the plain-words text, and with `--wait` waits that long. `approval status ID`
and `approval wait ID` read it later; `approval cancel ID` withdraws it.

A request is one to eight **asks**, each a grant the requesting session would receive: an action of the registry
(docs/node.md, "Standing grants"), a resource (an exact name, `*`, or a prefix before a final `*`), how long it lasts (a
minute to thirty days), how often it may be used, and `review` where each use needs an independent review. They go to the person
as one list, one tap and one fingerprint, and each becomes its own grant entry on approval. The grantee is always the
requesting session: an ask names no other. A request lives for `--ttl` (default 30 minutes, at most 24 hours) and is
refused past the node's limits: eight asks, sixty-four requests pending on the node, six requests a session in ten minutes.
Asking the same thing again (the same session, the same actions on the same resources, whatever the note, the time or the count) is
the same request: the reply says `deduplicated` and no new notification is made ("Approval fatigue"). A request that is not routine
comes back with a `code`, a two-digit number the agent shows its person in its own chat or screen; the phone offers three numbers
and the person chooses that one after the fingerprint.

The note is the agent's own words, up to 160 characters of plain text. It is shown, labelled as the agent's, as one line of the
text; it is never the sentence that says what is being asked. `task` (a board entry reference such as `task-12`) says what the
request came from and is shown with a link to it on the board when the board has it. `expect_policy` is what the agent believes
the pool's join policy is; if it disagrees with the node, the request is refused.

### What an agent is told

| State | Meaning | What the agent says to its person |
|---|---|---|
| `pending` | on the phone, undecided | "Waiting for you to approve on your phone." The agent keeps working on anything else |
| `held` | kept back unseen: earlier requests were declined or ran out | "Your requests are being held; a person was told once and nothing was shown to them." The agent does not ask again |
| `approved` | the grants exist, with their ids | continues |
| `denied` | the person said no | "You said no; nothing changed." |
| `expired` | nobody decided before it ended | "The request ended before you answered; ask me again if it is still needed." |
| `cancelled` | the agent withdrew it | none |
| `failed` | a signature was valid and the grants could not be written (a node error, named) | the named reason |

Every refusal is one sentence an agent can pass on. The refusals are in "Defences".

## The plain-words text

The text is made by the node from fields it checked, never taken from the agent: a fixed template per action fills in
the resource, the grantee, the time and the count. The agent's only words are the note. The text is plain ASCII, one fact a
line, each line starting with a label, in this order:

```
FLAG: <anything that looks off, one line each: first>
POOL: <for anything that connects something: the pool is OPEN or SECURE and what that means, from the node's own state>
RISK: ROUTINE | SENSITIVE | IRREVERSIBLE, and why
SAID: You said: "<what the person told the session>" (recorded by the agent's harness from your prompt | sent from this app)  --  or: No instruction from you is recorded for this session.
WHO: <session name>, which says it is <model> in <harness>, a main session or a subagent of <parent>, on device <name>, board <board>
VERDICT: The node found this request matches | goes beyond | has nothing to do with what you said.  --  or: No recent instruction from you was found to compare it with.
TAINT: <this session read content from outside it cannot vouch for | has read nothing from outside | not tracked: the node cannot say>
ASK: <the one plain sentence: what it wants to do>          (one per ask, numbered when there are several)
SCOPE: <to whom, on what, on which device, how long and until when, how often>
HELD: If you approve, this happens at <time> UTC and not before; a notification lets you cancel it before then.   (only for an act that waits)
PICK: After your fingerprint you choose the number this agent showed you, out of three. A wrong choice counts as a decline.   (only when not routine)
CANNOT: <what it will not be able to do: other actions, other boards or devices, anything after the end, approve anything for you>
WHY: The agent says: "<note>" (not checked). From task-12 on board demo.
DENY: If you deny, the agent is told no and nothing changes. If you deny and block, it also cannot ask again for 1 day.
EXPIRES: This request ends at <time> UTC.
```

`SAID:`, `VERDICT:` and `TAINT:` are the node's, never the agent's, and are always there. How the node finds the instruction, judges the
request against it and knows whether the session read content from outside is not decided yet: a node starts with a source that says
nothing is recorded, and the text says so in plain words ("No instruction from you is recorded", "Not tracked") rather than leaving
a line out. A source that is wired in later fills the same fields and changes no signed byte but their values.

`FLAG:` lines come first so they cannot scroll away. A request carries them when it:

- is from a session no grant you signed ever named (`never named`);
- is the first request of its kind on this node (`first of its kind`);
- asks for more than that session's last grant of the kind (a longer time, more uses, a wider resource, or no limit);
- comes from a subagent;
- asks for a wildcard resource, at any risk level, on an action that names a resource (a star on `sync_now` or `add_device`, which take none, says nothing), or for a grant that lasts over a day;
- would change the pool's join policy from secure to open;
- names a task that is not on the board;
- asks for a scope the person declined before (`This was declined N time(s) before ...`), which also takes one more deliberate step;
- is one nobody told the agent to make, when the node knows that (`You gave this agent no instruction to do this; it asked on its own.`);
- came from a device that is not a member of the pool (`This request came from a device that is not a member of your pool.`).

A request to change how devices join names one policy, `open` or `secure`, and never a pattern: a resource of `*` or `op*`
would cover `open` without the text saying so, so the node refuses it when it is asked. The model on the `WHO:` line is what
the session said about itself when it registered; the node cannot check it, and the line says so.

The `RISK:` level is fixed per action in `approvals::content::risk_of` (a table, not a judgement of the agent): `sync_now`,
`link_remove`, `note_verify`, `land_control` are routine; `link_read`, `set_join_policy`, `add_device`, `join_pool`, `shard_call`,
`shard_consent`, `land_trust`, `task_authority`, `quarantine_release`, `link_write`, `source_add` and adding a phone are
sensitive; `member_remove` and `file_delete` are irreversible. A request is as risky as its riskiest ask.

### The pool line

Every approval about connecting something (`add_device`, `join_pool`, `set_join_policy`, `link_read`, `link_write`,
`shard_call`, `shard_consent`, `sync_now`, and adding a phone) states the pool's join policy as a labelled line near the
top, with what it means for the person, taken from the node's own pool state at the moment of the request:

- `POOL: OPEN. Devices on the same network join without asking.`
- `POOL: SECURE. Each device needs your code to join.`
- For `set_join_policy`: `POOL: SECURE now, would become OPEN. Devices on the same network would join without asking.` and a
  `FLAG:` line when the change is secure to open.
- For `join_pool` the pool being joined tells its policy only when this device connects, so the line says that:
  `POOL: this device's pool is SECURE; the pool it would join states its own policy when it connects.`

The policy is part of the signed text. If the agent's request carries `expect_policy` and it is not the node's policy, the
request is refused. Because the text is signed, a policy that changes between the request and the decision does not matter: the
person approved what they saw, and the grants name the action and the resource, not the policy.

### Same content everywhere

The notification, the approval screen and the list in the app show the same text and the same fields: `title`, the
`FLAG:` lines, the `POOL:` line, `RISK:`, `WHO:`, the asks, `SCOPE:`, `CANNOT:`, `WHY:`, `DENY:` and the time left (a countdown made
from `expires_ms`). The signed challenge contains the text, so what the person saw is what was signed. Below the text the screen
lists what the signed request itself asks, one terse line for each grant (`add_device on anything, once, for 10 minutes`), which the
app reads from the structured asks the key signs and not from the node's prose: the grant an approval writes is built from those
asks, so a person can compare the sentences with the bytes.

## The signed challenge

A decision is a signature over `poolhouse/approval/v1` and a line break, then the canonical JSON (sorted keys, no spaces,
integers only, plain ASCII) of:

```
{"action": {"kind": "grants", "grants": [{"action", "device", "gid", "lasts_ms", "resource", "review", "uses"} ...]},
 "board": "demo", "context": {"block_ms": 86400000, "held": {"act_at_ms": 0, "cancel": false}, "pick": ["12", "47", "83"],
 "prompted": "yes" | "no" | "unknown", "repeat": 0, "said": {"origin": "none" | "harness" | "app", "quote": "<what the person said>"},
 "taint": "unknown" | "clean" | "tainted", "verdict": "matches" | "beyond" | "unrelated" | "none"},
 "created_ms": 1790000000000, "decision": "approve" | "approve_repeat" | "deny" | "block", "expires_ms": 1790001800000,
 "id": "ap-0123456789abcdef", "key": "<fingerprint of the signing key>", "node": "<this device's certificate fingerprint>",
 "nonce": "<32 hex digits>", "op": "approval", "session": "claude-1a2b3c", "text": "<the plain-words text>", "v": 1}
```

**The context** is what the person weighs besides the asks, written by the node and signed with them. Every field is always present,
so a challenge that leaves one out is not one the node wrote: the phone refuses it before any prompt, and the node's own copy of the
bytes would not match a signature made over it. `said` is the instruction of the person the request supposedly follows and how the node
came to know it (the harness attested the prompt, or the app sent it); `verdict` is how the node judged the request against it;
`taint` says whether the session read content from outside; `held` is when the act would happen once approved and whether a
notification lets the person cancel it first; `prompted` says whether the person recently told the session to do this; `repeat`
is how often the scope was declined before; `pick` is the three numbers of "Approval fatigue"; `block_ms` is what a deny that blocks
lasts. The text carries a line for each of them, so what is shown is what is signed; the app also reads `repeat`, `pick` and `block_ms`
to decide which buttons and steps to offer. The `action` is the structured form of what it wants, signed the same way, and a grant an
approval writes is built from it, never from the prose. `decision` is `approve`, `approve_repeat` (the deliberate approval of a
scope declined before), `deny`, or `block` (a deny that also stops the agent asking for `block_ms`); each is signed over its own bytes.

The node sends the phone the object; the phone fills in `decision` and `key`, checks it before it signs (the node is the
certificate it pinned, the text is the text on its screen, `expires_ms` is in the future), and signs after the fingerprint. A
signature is ECDSA P-256 with SHA-256 in the DER form Android produces.

When the person approves, the node writes the grants to the board as `grant` entries whose body carries an `approval` object
(the id, the node, the nonce, the times, the text and the asks) instead of a signature over the grant itself: any device that
holds the key can rebuild the challenge from the entry and check it, so a grant approved on the phone verifies the same way
everywhere a grant a person typed `yes` for does. The grant's own `at_ms` is the node's time at the decision and must fall
inside the request's life, and `until_ms` is `at_ms` plus the signed `lasts_ms`.

**One use.** A request leaves `pending` once, under the node's lock, and is written to disk before anything is done with it. A
second decision, a replay of the same bytes, or a decision after the end is refused.

**Expiry.** A request is `expired` at `expires_ms` by the node's clock; the phone's clock is never asked.

**A deny is signed too.** An unsigned deny would let anything that reaches the node end the person's pending requests. The
agent that asked is told "denied" the same way.

## Approval fatigue

An agent, or someone behind it, can ask again and again until a person accepts to be left alone. Every defence below is the node's,
none costs a routine approval anything, and the person's own answer is never what is rationed: what is limited is how much an agent
can ask and how loudly.

| Defence | What happens |
|---|---|
| **One item.** The same request made again | The same session asking for the same actions on the same resources is the same request, whatever its note, time or count: the reply is the item already waiting (`deduplicated`, and `retries` goes up). No new notification, no new row. Two hundred asks are one item and one push. The same request asked again within ten minutes of one that was withdrawn or ran out is made, but listed and never pushed again, so withdrawing and asking again cannot make a new notification. |
| **Backoff** | After one decline or timeout the same kind of request (the same set of actions) waits 30 seconds; the agent is told how long. Other kinds are not held back. |
| **Hold** | After two declines or timeouts of the same kind by a session, the session is held. After five declines by the person, of any kind, across an agent (its main session and the sessions that started under it, which the node knows from who registered whom, never from the model or harness a session says it is), or twenty on a board, the sessions of that agent or board that were themselves declined or ran out are held; a session nobody declined is not. A timeout or a withdrawal counts at the session only, so what one session leaves unanswered never holds another. A held requester's requests are kept, shown to nobody, pushed to nobody. The first one makes one quiet notice for the person; nothing more is pushed for the hold, however it asks. Holds last one hour, then four, then a day. A third hold in a day that the person's own declines started suspends that session; holds that timeouts started never do, so an agent that runs while the person sleeps is held, its requests queue quietly and expire, and it keeps its grants. |
| **The quiet notice** | One notice per hold, on a channel with no sound, no heads-up and no badge: "the session X asked N times and is held". Its answers are **Allow once** (one held request becomes a normal request, pushed as one), **Keep holding** (the hold goes on and the notice is not repeated) and **Block** (nothing from it for a day). The count is made again, silently, only when it has doubled. |
| **Deny and block** | On the request itself, and on the notification: a deny that also stops that session asking for a day. The text says so before the person chooses. It also ends what the session still had waiting. |
| **Declined before** | A scope the person declined shows `declined N time(s) before` as a flag, is asked on its own (never in a batch), and takes one more deliberate step: the node accepts only `approve_repeat` for it, which the app asks for in a confirmation before the fingerprint and the terminal asks for as a typed word. |
| **Unprompted** | When the node knows nobody told the session to do this (the evidence source says so), the request is labelled, kept in a low-urgency list and never pushed with urgency. Where the node cannot tell, which is every node until a harness attests instructions, a request is not called unprompted and behaves as it always did. |
| **A number to match** | A request that is not routine comes back to the agent with a number; the agent shows it to its person where the person reads it. After the fingerprint the phone offers three numbers and the person chooses that one. A choice that is not the agent's number ends the request as a decline and counts against the requester; a choice that is not one of the three is no answer and changes nothing. Routine requests have none. |
| **Withdrawing** | A request the person was pushed that its own session then withdraws counts as a decline at that session (only: a withdrawal never counts against the agent or the pool), so asking and withdrawing again and again holds the session as two declines do. Withdrawing one nobody was told about costs nothing. |
| **A push budget** | Each requester may use three of the hour's ten pushes. What it asks past that is listed, with the one quiet notice that things wait in the list, so one session cannot spend the hour's pushes and silence an honest one. |
| **No consent by silence** | A request nobody answers expires, is never approved, and counts as a timeout, the same as a decline, against the requester. |
| **A trust event** | Starting a hold writes `approval.held` and `approval.bombing` (the pool's other devices read it) and a penalty for the trust ledger to read; a third hold in a day that the person's declines started writes `approval.suspended`, refuses that session for a day, ends what it still had waiting, and takes its grants away for as long (a suspended session holds none). No other session of the same model, agent or board loses anything. |
| **Caps on the person** | At most three requests of one requester, eight of anyone's, and ten pushes in an hour interrupt the person. Past that, requests wait in the list and one quiet notice, at most once an hour, says how many. |
| **A stranger** | A request from a device that is not a member of the pool the person governs is flagged, listed and never pushed. |

The cost to the normal case is none for a routine approval and one pick for a sensitive or irreversible one ("The friction budget").
A request made once, answered once, never meets a hold, a backoff, a cap or a repeat. Where a number is shown: the agent prints it
(`SHOW YOUR PERSON THIS NUMBER`), the phone's three-way choice follows the fingerprint, and a person at a terminal is asked after
the typed yes. The ledger of declines, timeouts, holds and pushes is `fatigue.json` in the node's directory and survives a restart.

### Defences

Each refusal is one sentence the agent can pass to its person.

| What is tried | Result, as the node says it |
|---|---|
| The same decision again | "That request was already approved, so it cannot be decided again." |
| A decision after the end | "That request ended before it was decided; ask again if it is still needed." |
| A signature made for another node | "That signature does not match this request, so nothing was approved." |
| The text the phone showed is not the node's text | "The text you were shown is not the text of this request, so nothing was signed." |
| A signature of the right text by a key the node does not hold | "That key is not one of your keys on this device, so nothing was approved." |
| A phone that was removed | "That phone was removed from this device and can no longer approve." |
| A session that signs with a key it made itself | "That key is not one of your keys on this device, so nothing was approved." |
| The requester carrying its own decision | "The session that asked cannot carry the answer to its own request." |
| An ask for a floor (`grant_issue`, `push_main`...) | "`grant_issue` is a floor: no grant, from anyone, covers it." |
| An agent's `expect_policy` that is not the node's | "The pool is secure, not open as you wrote; ask again with what is true." |
| Text with other than printable ASCII | "That text must be plain English letters, digits and punctuation." |
| A request beyond the limits | "6 requests in ten minutes is the most one session may make; try again later." |
| A request to change how devices join with a pattern (`*`, `op*`) | "A request to change how devices join names one policy, open or secure, and no pattern." |
| A code a session made, used to add the first phone | "A code made through the node cannot add the first phone: a person at the computer adds it with `poolhouse-workspace phone add`." |
| An agent asking for a code while one waits to be scanned | "A code for a phone is already waiting to be scanned; use it or wait for it to run out." |
| Wrong codes from one address (ten in ten minutes) | "Too many wrong codes came from this address; wait ten minutes." The code itself stays good for the person |
| A host that opens connections and says nothing | the handshake and request have ten seconds in all, one address holds six connections, and the last eight of forty-eight are kept for addresses a phone answered from |
| A phone removed while its request was being handled | "This phone is not added to this device, or it was removed." |
| A grant entry copied from an approval and changed (another session, other words, a longer time, a start after the request ended) | not an answer to anyone: the node lists it as ignored ("not signed by a person key enrolled on this device") and it counts for nothing |

Each grant an approval wrote carries the approval, text included, so what each key approved stays on the board and verifies on any
device that holds the key.

## Several phones, removing one, losing one

Up to eight person keys fit on a node, phones and typed-`yes` keys together. Every phone sees every request and any one
phone decides it; on the others it leaves the list, and its notification goes when the app next looks and in any case when the request ends.

**Removing a phone.** "Phones and notifications" in the app lists the phones with their key and whether each has a wake-up. "Remove" on another
phone makes a request, "Remove the phone `<name>`", and one fingerprint on a phone that stays removes it: its key stops counting at
once, what it signed earlier stays as signed and is listed as signed by a key that was removed, and the node never enrols the
same key again (`persons_removed.json`). A phone removes itself the same way. The last key is never removed; to start again
a person stops the node, deletes `persons.json` and `phones.json` and runs `phone add` (docs/node.md, "Recovery").

**A lost phone.** From another phone as above. At the computer, `poolhouse-workspace phone remove NAME` asks a key the node holds to
remove it, with the typed yes: that command is a person's (it refuses an agent's process) and needs a person key kept at the computer
(`grants-setup`). With one phone and no such key, the offline recovery of docs/node.md ("Recovery") starts again. A phone that is only
locked is not an exposure: a signature needs the person's fingerprint or the device credential.

**A new fingerprint on the phone** destroys the approval key (Android's own rule). The app says so plainly and goes straight
to the scan, because the old key is gone for good: a person with another phone removes the old one from it, or removes it at the
computer.

## When the phone cannot be reached

Nothing is lost and the agent is never told it failed.

- The request lives on the node until `expires_ms`. The agent's `wait` answers `waiting` when its own time runs out and the
  same handle stays good.
- The app asks the node for pending requests when it opens, and keeps one open request for new ones while it is on screen
  (`wait`, held up to fifteen seconds). A request made while the phone was off appears the next time the app is open, and the
  push (below) tries as soon as there is a route.
- A request that ends before the phone saw it is `expired`, and the agent is told so in the words above.

## Delivery

Layered, the most private and cheapest layer first. The notification itself is an encrypted message for the phone only.

| Layer | Privacy | Reliability | Onboarding | Recurring cost | Used for |
|---|---|---|---|---|---|
| The app's own connection to the node over the LAN or Tailscale (pinned TLS, long poll) | Nobody else sees anything | Only while the app is open and the node is reachable | None: the scan did it | None | The base: the approval list and the screen, always correct |
| UnifiedPush (a distributor such as ntfy, self-hosted or the public server) carrying a Web Push payload | The push server sees an opaque id, the size and the time; the content is ciphertext | Good where the distributor app is allowed to run in the background | Installing the distributor app once (optional; the app works without it) | None (the public ntfy server needs no account; a self-hosted one costs what its host costs) | The wake-up and the notification while the app is closed |
| FCM | Google sees the same metadata | Best on Google phones | None for the person, but it needs a Firebase project, an account of ours | An account to keep | Not chosen: it needs an account we do not have (owner-only, below) |
| APNs | | | A paid Apple developer account | | Blocked until the product takes off (docs/pool-encryption.md, 8.1) |

**The choice.** The base layer is built and always on. UnifiedPush is the wake-up because it needs no account of ours and no
cost, and the payload is standard Web Push (RFC 8291), so an FCM or a browser Web Push sender can be added later without
changing the message or the app's decryption. The app has a `PushChannel` interface; `UnifiedPushChannel` is the first
implementation, `FakePushChannel` is what the tests use.

**What the message is.** The node seals one JSON document to the phone's push key: the id, the title, the requesting
session's name, the risk level, the expiry, how loudly to show it (`urgency`: `urgent`, or `quiet` for a notice) and the whole challenge (which contains the text and the context). A request that is only listed (`silent`) is not pushed at all. An approval that does not fit in
3000 bytes (eight long asks) is sent as the title, the id and `"more": true`, and the app fetches the rest from the node when
the person opens it. The push server is told the endpoint, the length and the time; it learns nothing else, and the id is a random
handle that means nothing without the node.

**What the phone does with it.** The app decrypts it where it arrives (no prompt: the push key needs none), posts a notification and
nothing else; it does not sign. The notification:

- on the lock screen: `Approval waiting from claude-1a2b3c` and nothing more (`VISIBILITY_PRIVATE` with a public version);
- once the phone is unlocked: the title, and the whole text as the expanded body, with **Approve** and **Deny**;
- **Deny** signs a deny after the same fingerprint (a signed deny, above); the person can swipe the notification away to leave the
  request pending; **Deny and block** (on a request an agent made, whose text says what a block lasts) signs a block after the same
  fingerprint, and that session cannot ask again for a day;
- a quiet notice (a held requester, requests waiting in the list) is on its own channel with no sound, no heads-up, no vibration
  and no badge, says only `A quiet notice is waiting` at the lock screen, and has buttons that say what they do (**Allow once**,
  **Keep holding**, **Block it**; **Lift the suspension**; **Dismiss**);
- **Approve** always asks for the fingerprint or the device credential before anything is signed, and never from a locked screen
  without it. For a routine request that the node did not flag, the notification's Approve opens the prompt directly, with the
  text just read in the expanded notification; for a sensitive or irreversible request, and for any request that carries a `FLAG:`
  line, it opens the approval screen first, so the full text is on the screen when the prompt appears. The level and the flags
  are read from the signed text (its `RISK:` and `FLAG:` lines), never from a field beside it that the signature does not cover.
  No one tap approves a sensitive, irreversible or flagged request beyond the prompt.
- the notification is local to the phone (`setLocalOnly`: it is not bridged to a watch), each button of each request has its own
  intent so two requests cannot swap each other's Approve and Deny, and the request the app keeps between the push and the tap is
  sealed by a Keystore key, bound to its id.

**The node's side of the push** is a `Pusher` with two implementations: the real one POSTs the sealed body to the endpoint over TLS
(only to an `https` address whose host resolves to a public address, unless a person lists the host in `push-hosts.json`:
a phone's token must not make the node call an address inside the network) and the fake one the tests use records the
ciphertext it was given. A push that cannot be delivered is dropped; the request is still on the node.

### What needs an owner to do (nothing is registered by an agent)

- Install a UnifiedPush distributor on the phone (ntfy from F-Droid or the Play Store) and open it once, or run a distributor
  server of the owner's own. Without it the app works without the wake-up.
- (Only if the owner wants it) a Firebase project and its keys to add an FCM sender: an account of ours, so not done.
- Sideload the APK (the app is not on a store), and, to have it built signed, the preview signing key
  (`app/android/README.md`).
- For the push to leave the network from a Mac: the macOS Local Network permission for `Poolhouse.app` if the phone endpoint
  listens beyond loopback (docs/node.md, "Poolhouse.app").

## The friction budget

Steps the person sees, counted by their kind. The numbers are the budget (`contracts/phone-friction.json`);
`tests/test_phone_friction.py` and the app's `ApprovalFlow` check they do not grow, and a step that is not in the table
is not allowed without a one-line reason in the last column.

**First run, on a computer and a phone (the first phone).**

| # | Step | Kind | Why it is needed |
|---|---|---|---|
| 1 | Run `poolhouse-workspace phone add` on the computer | a command, once | only a person at the computer can ask for the first key (an agent cannot) |
| 2 | Scan the code with the app | one scan | carries the node's address, its certificate and the one-time secret |
| 3 | Fingerprint to sign the phone's own addition | one biometric prompt | the key signs, and every signature needs the person |

Counted: 1 command, 1 scan, 1 biometric prompt. Not counted because the system asks them, once: the camera permission, the
notification permission (Android 13 and later), the install of the app. Done silently by the app: making the keys, storing the
token, registering the push address, pinning the certificate, choosing the node's address.

**Another phone.** Scan (1), one fingerprint on the phone that approves (1), and the new phone does steps 2 and 3 above.
The tap on the notification on the approving phone is the routine approval below.

**A routine approval (one request, one or many asks).**

| # | Step | Kind |
|---|---|---|
| 1 | Approve on the notification | one tap |
| 2 | Fingerprint | one biometric prompt |

**A sensitive or irreversible approval.**

| # | Step | Kind | Why it is needed |
|---|---|---|---|
| 1 | Tap the notification | one tap | the whole text must be on the screen before the prompt: it is sensitive or cannot be undone |
| 2 | Approve on the screen | one tap | |
| 3 | Fingerprint | one biometric prompt | |
| 4 | Pick the number the agent showed, out of three | one tap | a tap made by reflex, or on a request the person never looked into, cannot approve it: the number is on the agent's own screen |

A routine request the node flagged as looking off takes the first three rows and no pick; a scope that was declined before takes one more
deliberate step in the screen (the confirmation before the prompt), described under "Approval fatigue".

**What the defences against being worn down cost the normal case** ("Approval fatigue"): a request made once and answered once.

| Defence | A routine approval | A sensitive or irreversible approval |
|---|---|---|
| One item, backoff, hold, caps, the quiet list | nothing: they act only on a requester that was declined or that asks again and again | nothing |
| The number to match | none: a routine request has no number | one pick (row 4 above) |
| Deny and block | one more button, not a step: a deny is still a tap and the prompt | the same |
| A scope declined before | one confirmation before the prompt, only when it was declined | the same |

A request with several asks is one approval, with the same counts. A deny is a tap and the same prompt. Opening the app to look
at the list asks nothing: the phone's own lock is the lock. Nothing prompts twice.

## Node API

On the local socket, with a session's token (docs/node.md, "Local API"):

| method | params | result |
|---|---|---|
| `approval_request` | `asks` (one to eight of `{action, resource, lasts_ms, uses, review}`), `note`, `task`, `ttl_s`, `expect_policy` | `{id, state: "pending" or "held", text, expires_ms, urgency, deduplicated, code (when not routine)}` |
| `approval_status` | `id` | `{id, state, text, decided_ms, by, grants}` for the requesting session only |
| `approval_cancel` | `id` | `{id, state}` |
| `approvals` | `state` | the requesting session's own requests |
| `approval_pending` | | every pending request (any registered session may read the text it would show a person) |
| `approval_decide` | `id`, `decision` (`approve`, `approve_repeat`, `deny`, `block`), `key`, `sig`, `text_sha256`, `pick` | `{id, state}`: needs a signature of an enrolled person key; a session only carries it. `pick` is the number the person chose and is not signed |
| `phone_info` | | `{pin, port, endpoints, device, policy}` |
| `phone_offer` | | a code for a further phone (needs a person key to exist; three an hour for each session; not while another code waits to be scanned; the request to approve the phone says which session made the code); the first is a person's command |
| `phones` | | the phones: name, key, state, when added and last seen, whether a push address is set |
| `phone_remove` | `phone` | the request "Remove the phone", for a key the node holds to approve |

On the phone endpoint (HTTPS, TLS 1.3, one P-256 self-signed certificate the code pins; `--phone ADDR`, port 7449 by default),
`POST /phone/v1/<op>` with a JSON body, always `{"ok":true,"result":...}` or `{"ok":false,"error":{"code","message"}}` with a
bearer token except `hello`, `add` and `decide`: `hello` (GET), `add`, `status` (a phone waiting for approval), `list`, `get`, `wait`, `decide`, `push`, `phones`, `remove`, `offer`.

Audit: each addition, removal, request, decision and expiry is an audit entry on the pool board (`phone.add`,
`phone.remove`, `approval.request`, `approval.approved`, `approval.denied`, `approval.blocked`, `approval.expired`, `approval.kept_back`,
`approval.held`, `approval.summary`, `approval.released`, `approval.suspended`, and the events `approval.bombing` and `approval.suspended`
that the pool's other devices read), naming the id, the session and the key, never the text.

## Threats this answers

| Threat | Answer |
|---|---|
| A forged answer from an agent or a session | Only a signature of an enrolled person key decides; a session only carries it, and never the one it asked for |
| Text that is not what was signed | The key signs the text, the context and the structured asks; the phone refuses text that is not on its screen, and the node rebuilds the bytes itself |
| A grant for more than was shown | The grant is built from the signed asks; an entry that differs, or lacks its context, counts for nothing |
| A replay | One state change under the node's lock, written before anything is done with it |
| A notification that leaks | Ciphertext only to the push service; the lock screen shows the agent's name and nothing else |
| **Approval fatigue, prompt bombing** | One item per request, a hold after two declines, a quiet notice instead of a buzz, deny and block, a scope declined before is flagged and takes a deliberate step, unprompted requests are listed not pushed, a number to match for what is not routine, timeouts count, a trust event and suspension, caps on what reaches the phone, strangers never push ("Approval fatigue") |
| A person who approves everything by reflex | The number to match ties a sensitive approval to the agent's own screen; the flags and `RISK:` are the rest of the help; a tap on a request the person never looked into cannot approve it |
| An agent that does not know it is compromised (injected text) | The request carries what the person said, how the node judged it against that, and whether the session read content from outside, as lines the person reads and bytes the key signs; how the node finds them is a placeholder the node fills (`SAID`, `VERDICT`, `TAINT`) |

## What this protects, and what it does not

- It protects the person against an agent that asks for more than it was given, the same request replayed, a request made to
  look like another, a node or a network that changes the words, and a phone that is lost (its key needs the person's body).
- It makes the text, not a code, the thing signed; it does not make the agent's note true. The note is labelled.
- A person who approves everything without reading is not protected by anything here; the flags and the one-line `RISK:` are
  the help the design gives, and the friction budget is why there is no more than that.
- A process of the same operating-system user that can write the node's state directory can still enrol a key by planting a
  secret, exactly as for a key at a terminal (docs/node.md, "Key custody"); the phone adds no new way to do that and takes none away.
- Android Keystore, StrongBox and the biometric prompt are the phone vendor's; a rooted phone is out of scope.
- The defences against being worn down limit an agent and spare the person; they cannot make a person read. A hold is lifted by the
  person's answer, a block by its time, a suspension by its time or the person's **Lift the suspension**. Until a harness attests
  what the person said, the node cannot tell an unprompted request from a prompted one and treats none as unprompted, so that
  defence does nothing yet but is ready (`approvals::context::Evidence`).
- A block and a suspension are by session. An agent that registers a new main session is a new requester until it is declined;
  the sessions that started under a main session share its count of the person's declines, and nothing a session says about its own
  model or harness changes who it is counted as.

## Checks on a real phone

The tests prove the protocol and the app's logic without a device (`PhoneApprovalTest` on a JVM, `tests/test_board_approval.py`, `app/poolhouse-node/tests/approvals.rs`), and the pool check (`scripts/pool-check --only phone-approval`) runs the node's side on a Mac and on Linux containers with a phone made of keys. These need the phone and are the owner's, in this order:

1. Install the APK (`python app/android/build.py GRADLE PLUGIN API debug` builds it); open it; the notification permission prompt (Android 13 and later) and the camera permission prompt each appear once, and the scan opens.
2. Run `poolhouse-workspace phone add`; scan the code; the fingerprint prompt appears once; the app says the phone is added;
   the terminal names it with its key.
3. Have an agent run `poolhouse-workspace approval request add_device --for 10m --uses 1`: the notification arrives with the app
   open, the text is complete (FLAG, POOL, RISK, WHO, ASK, SCOPE, CANNOT, WHY, DENY, EXPIRES), Approve asks for the fingerprint, and
   the agent sees `approved`.
4. Lock the phone, repeat: the lock screen says only `Approval waiting from <agent>`; unlock and the whole text shows.
5. Request a sensitive action (`set_join_policy:open` on a secure pool): the notification opens the screen, not the prompt; the
   FLAG line for secure to open is on top; deny it and the agent sees `denied`.
6. Turn off the phone's Wi-Fi, make a request, turn it on and open the app: the request is there; let another expire and see
   `expired`.
7. Enrol a fingerprint on the phone: the old key is lost, the app says so and offers the scan.
8. Add a second phone and remove the first from it: "Remove" opens the same full-text screen as any request (nothing about a
   removal is signed that was not shown), and the second phone's request names the first phone's key in six groups of four.
9. With a UnifiedPush distributor installed (ntfy): in "Phones and notifications" choose "Turn on wake-up", close the app, make a request: the
   notification arrives with the app closed, and a capture of the push server's side shows only ciphertext.
10. Change the node's address (another network, then Tailscale): the app finds it from the code's other endpoint and says nothing.
10b. Make a request that is not routine: the notification opens the screen, the fingerprint prompt comes, then "Which number did your agent show you?" with three numbers; choose the one the agent printed and see it approved; choose another on the next request and see it denied, with the request counted as a decline.
10c. Decline the same request twice: the second says "declined 1 time(s) before" and asks "Approve anyway?" before the fingerprint; after two declines a third request makes no sound at all and one quiet notice (no sound, no badge) says the agent is held, with Allow once, Keep holding and Block it.
10d. Tap Deny and block on a notification: the agent is refused for a day and its waiting requests end.
11. Confirm the camera is not opened after the scan, no image is stored, and `FLAG_SECURE` hides the text in the recents screen.
