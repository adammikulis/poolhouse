# Guest access

Someone who is not in your pool asks a model on your computer. She is a guest, not a member: she gets model replies and
nothing else. Code: `poolhouse.guest`, `poolhouse.serve.private`.

```
host:   python -m poolhouse.guest share MODEL.gguf       answer guests until stopped
either: python -m poolhouse.guest show host|guest        show a joining code
either: python -m poolhouse.guest scan 'poolhouse://guest?...'
guest:  python -m poolhouse.guest connect ID             a local address and key for her tools
host:   python -m poolhouse.guest list | limit ID NAME=NUMBER | remove ID
```

`share`, `show`, `scan`, `limit` and `connect` are refused, with the reason and an audit row, to an agent, to a confined
process and below an unattended agent's runner. An agent holding a pool grant may add a device but may not do these: the
rule here is the stricter one. `poolhouse.guest.control.Control` is what a screen calls; no screen calls it yet.

## What each side is told

The exact sentences are `poolhouse.guest.disclosure`. The guest's say, in short: the connection is encrypted between the
two computers; unchanged Poolhouse on the host saves, logs and shows nothing of the content; whoever controls the host can
still read everything; the host's operating system may copy memory to disk; replies can be forged, so tool calls are
untrusted.

## What the code enforces

| Promise | How |
|---|---|
| Only the two joined computers talk | TLS 1.3, both certificates pinned; the guest listener completes no handshake without an active guest's certificate, matched exactly, and re-checks it on every request and every block of a reply |
| Model calls only | `/v1/models`, `/v1/chat/completions`, `/v1/messages`, `/v1/messages/count_tokens`, `/v1/responses`. No `/slots`, `/props`, `/metrics`, web page, board, files or jobs |
| The host cannot be made to fetch or switch things | Request fields are listed (`guest/requests.py`): slot ids, cache switches, adapters and `n` are dropped; pictures, sound and files must be inside the request; schema references only to itself |
| Nothing of the content on the host's disk or in a log | The guest's server is private: no log file, output discarded, `--log-disable`, `--no-slots`, `--no-webui`, no `--metrics`, no slot save path, no escalation, no lookup file, no n-gram table, no `LLAMA_*` environment. The listener logs the kind of a failure and nothing else. Disk holds certificates, limits and counts (`<state>/guest/`) |
| No reuse or leak between users | One server per active guest. Nobody else's request reaches that process; the broker never shares it, never hands a guest a server that was already up and never stops another server to make room. The host's own servers, slots and caches are not touched |
| Her conversation is not read again each turn | It stays in her server's memory between turns (slot plus `--cache-mb` of earlier conversations), counted in the memory the share needs |
| Limits and removal | Requests per minute, one at a time, reply tokens per request, tokens per day, request size, expiry (30 days). Removal ends a request in flight, also while the prompt is being read, and stops her server |
| Her computer opens nothing to the host | Her side listens on loopback only, wants a token, refuses a browser `Origin` and any other host name |
| Her coding agents | A Poolhouse harness pointed at a guest connection with `--on` is switched to `--sandbox`. Without that switch these launchers run in place, with her rights. A tool of another maker decides for itself what a reply may do |

## What it cannot do

- The host's owner can change the software, read process or GPU memory, or record replies. Nothing lets the guest check
  what runs there.
- Memory is not locked and not wiped, on macOS or Windows. A guest's server starts only when it fits the memory that is
  free then; later pressure can page it out, and hibernation or a crash report can write it to disk. Ending the process
  gives the memory back to the system.
- The host, and the network, see when and how much she sends. Waiting for the GPU shows her that something else is using it.
- Each active guest costs a copy of the model's context and, where the system does not share them, of its weights. A model
  that fits only once cannot be shared while its owner is using it. Sharing one server (a llama.cpp change that keeps a
  slot per guest in memory) is not built.
- Only llama.cpp with GGUF weights. Strata, MLX, vLLM and SGLang are not covered.
- The requests wait for the same one-at-a-time turn on the device as the owner's, so a guest can slow the owner by as long as
  one of her replies, within her limits.

## Joining

One side shows a code (a QR code, or the same text sent by a trusted messenger), the other reads it. It carries the
shower's certificate fingerprint, a one-time secret that lasts ten minutes and a private address. The scanner connects,
checks the fingerprint and sends its certificate under a MAC of the secret. Both screens then show four words made from the
secret and both certificates; each side keeps the other only when the person there says they match. A photographed code is
not enough: whoever uses it first leaves the real scanner with no words to match.

## Reaching each other

Both listeners bind one loopback, local-network or Tailscale address; `0.0.0.0` and public addresses are refused, so no port
is forwarded and nothing is relayed by us. On different networks, use Tailscale and share the host computer with her
Tailscale account (a step only the account holder can take, in the Tailscale console). Tailscale carries only the
connection above, which it cannot read. A host on Windows currently has to name its address (`share --address`).

## Not built yet

The app's panel and phone screens; supervision of `share` by the daemon; the embedded Tailscale node's forwarder; a harness
launcher that uses the guest connection's key; the shared-server llama.cpp change.

## Attack pass

Owed before merge, by an agent other than the author (docs/agent-policy.md, section 1, "Attack first").
