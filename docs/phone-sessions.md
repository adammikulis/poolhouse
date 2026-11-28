# Talking to agents from a linked phone

How a message typed on the phone reaches a running agent, what Claude Code supports, what it does not, and what an attacker
meets. The wire contract is [phone-api.md](phone-api.md).

## The mechanism

A connected project has hooks in its own `.claude/settings.local.json` (`poolhouse phone connect --project DIR`, the Phones
screen, and every recently used project when a phone is linked). No global setting is written.

| Hook | What it does |
|---|---|
| `SessionStart`, `Stop` with `asyncRewake` (`poolhouse phone hook listen`) | waits in the background; when a message arrives it ends with status 2, which wakes an idle session with the message |
| `PostToolUse` | hands a message to a session that is working, with its next model request |
| `SubagentStart` | tells a subagent what a message from the phone is |
| `SubagentStop` | a running subagent takes its messages here, when its turn ends, and goes on with them |
| `PreToolUse` on `AskUserQuestion` | when the person's latest words came from the phone, waits up to 10 minutes for the phone's answer and passes it as the tool's answer; else the question is asked at the computer |
| `UserPromptSubmit`, `Stop`, `Notification`, `SessionEnd` | report state, where the person is, and a waiting permission prompt |

The phone signs each message with its own key. The message is queued in `<state>/person/phones`, and the process that hands
it to the agent (the hook, or a delegate's launcher outside its box) checks the signature, the session the signed path names,
its age (a day at most), that the phone is still linked and that it was not delivered before.

A subagent that has finished cannot be reached directly: its lead session is given the message to pass on (`via_parent`), and
a subagent that took a message directly has its lead told, so the lead does not take the answer for something from nowhere.
A sandboxed local delegate gets the message with its next tool result, headed as the person's; a message of a session can
never take that heading, because every session message is headed as that session's.

## Why hooks

| Mechanism of Claude Code | Why not chosen |
|---|---|
| Channels (an MCP server pushing `notifications/claude/channel`) | research preview: a server of our own needs `--dangerously-load-development-channels`, a confirmation dialog at each start and a restart of the session; Team and Enterprise accounts must enable it; the event arrives as a `<channel>` tag, not as typed input |
| Remote Control | the phone side is Anthropic's app and claude.ai, not Poolhouse; no third-party client can send through it |
| Agent SDK | drives sessions it starts itself, not the session the person already has open |
| `asyncRewake` hooks (chosen) | works with an ordinary session of a connected project, no flag, no preview |

Sources: [hooks](https://code.claude.com/docs/en/hooks), [channels](https://code.claude.com/docs/en/channels),
[channels reference](https://code.claude.com/docs/en/channels-reference),
[Remote Control](https://code.claude.com/docs/en/remote-control).

## Limits

- Claude Code has no supported way to submit text as a typed prompt. A message arrives as hook feedback (a system reminder)
  that says it is the person's, signature checked. The session is told at start what that means. A model may still treat it
  with more caution than typed input, or refuse what it would refuse typed.
- A running subagent takes a message only when its turn ends, which can be long after it was sent.
- A session that was open before its project was connected takes messages from its next turn, and wakes when idle only after
  that turn ended (or after a restart of the session).
- A run with nobody at it (`claude -p`, the Agent SDK) gets no listener, because the harness waits for background hooks before
  it ends. It still takes messages at its tool calls. `POOLHOUSE_PHONE_LISTEN=1` gives a run that stays open a listener.
- A listener lasts 7 days of idleness.
- Managed settings that disable project hooks, or a session started with settings that leave local ones out, take nothing.
- Permission prompts are not answered from the phone; the phone is told one waits.
- Codex, OpenCode, Pi, delegates without a sandbox and agents on other computers are listed and take nothing yet.
- History comes from Claude Code's transcript files, whose format is not a promised interface.

## Proof

Live on macOS with Claude Code 2.1.332 and `claude-haiku`, in throwaway projects with their own state folders and real P-256
keys as the phone, in headless sessions kept open over `--input-format stream-json` with `POOLHOUSE_PHONE_LISTEN=1`:

- the whole chain: a client linked to the phone service over pinned TLS with the typed code, listed the session as `wake`, sent
  a signed message, the idle session woke and answered, and the client read the reply and saw the delivery on the stream;
- a message sent while a tool ran was followed in the same turn;
- a message signed by another key was dropped and never shown;
- a running subagent followed a message handed to it when its turn ended, 3 of 3 runs. Handed to it beside a tool result
  instead, it ignored the message as that tool's output in 3 of 4 runs, which is why delivery to a subagent waits for the end
  of its turn;
- `claude -p` in a connected project ends normally (before a headless run got no listener, it did not end);
- session discovery and history read this account's real transcripts: 36 sessions and 164 subagents, each subagent with its
  parent.

Not proven live: the interactive terminal interface (waking it, and leaving it with a listener running), answering
`AskUserQuestion` (the tool is not offered to a headless session), a real phone and the Android app, an installed daemon, the
macOS firewall or local-network prompt, Tailscale, Windows.

## What an attacker meets

| Surface | Reach | Stops it |
|---|---|---|
| The listener, port 7450 | loopback, home network and Tailscale addresses only; public peers are closed unread; open only while a code is open or a phone linked | TLS 1.3 with a pinned certificate; connection and per-address caps |
| `GET /hello` | anyone who reaches the port | says the computer's name, its time and whether a code is open; 60 a minute an address |
| `POST /link` | the same | a 49-bit single-use code for 10 minutes, a proof bound to the certificate seen (a machine in between cannot relay it, and searching the recorded proof costs 200000 hash rounds a guess), 10 failures an address |
| Every other request | a linked phone's key only | signature over certificate, method, path and body; 120 s window; nonce once; revoked at once |
| Showing a code, listing, removing, switching | the person's credentialed session on this computer | refused to an unfounded pool's open page and to commands an agent runs |
| A message in the queue | whoever can write `<state>/person/phones` | checked again at delivery; agents are kept out of that folder by the guard hooks and sandbox profiles |
| The phone's model (`phone:<id>`) | members with the daemon's credential | off for each phone until switched on; text only, tools refused; size, rate and time limits; kept in memory only |
| What a phone returns | the asker | untrusted model text; nothing runs it |
| Transcripts sent to the phone | the person's own phone | recognised credentials are replaced; other private text is sent as it is |

What this does not stop: a program running as the person outside every sandbox can read and write the state folder, so it can
add a key of its own or read the TLS key, as it can for every Poolhouse key; a linked phone in the wrong hands directs the
computer until it is removed (the app may lock itself); words of another agent can still try to imitate the hook's heading in
text an agent reads, which is why the session is told that only hook feedback carries it.
