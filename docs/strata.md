# The Strata engine

[Strata](https://github.com/Niko1221/Strata) (MIT) is a second serving engine beside llama.cpp, for Qwen3.8-Flash-Next only. It
runs on Apple silicon (Metal) and on 64-bit Windows with an NVIDIA card. llama.cpp stays the default on every device.

```
poolhouse-serve up PATH/TO/first-file.gguf --engine strata
poolhouse-serve strata-proof PATH/TO/first-file.gguf
```

The path is the first file of a set `src/poolhouse/serve/strata/pins.json` names, with every file of the set beside it, or its
`hf:` reference. A fetch follows the Hub rule for who may pull from which publisher (`poolhouse-models pull`). `--context`
takes up to 262,144 tokens; without it the largest context this device holds is chosen. `--draft mtp` or `--draft dflash2`
names the drafter, `--spec-n-max` and `--spec-p-min` its window, and `--engine-option=FLAG=VALUE` one of a fixed list of engine
options.

## What is installed, and from where

Everything is fetched the first time a Strata server starts, never at an upgrade, into `~/.poolhouse/strata`.

| Part | Pin |
|---|---|
| Source: engine, server, preparation tools | the commit in `pins.json`, by the archive's SHA-256 and a digest of the unpacked content, checked again at every start |
| Engine on a Mac | built from that source with Apple's compiler and the pinned metal-cpp headers; needs the Command Line Tools |
| Engine on Windows | the release's `cuda13` archive by SHA-256 |
| NVIDIA libraries on Windows | the `serve-strata` extra, by the hashes in `requirements.lock` |
| Draft layer (MTP) | the `mtp.*` tensors of `Qwen/Qwen3.8-Flash-Next` at a pinned revision, each by SHA-256, packed on the device (about 5 GB fetched) |
| DFlash2 drafter | `incoai/Qwen3.8-Flash-Next-DFlash2` at a pinned revision, each file by SHA-256 (1.2 GB) |
| Model files | each by size and SHA-256 |

**The Windows engine is a prebuilt third-party binary.** It is uploaded to the release by hand: it carries no build attestation,
no CI run builds it, and the release tag is unsigned. Its SHA-256 pins which bytes run, not how they were made.

Strata's installers, desktop app, `strata launch` command and signed update feed are never run.

## How it is served

The broker starts Strata's server through the same launch as llama-server: on loopback, with the key Poolhouse issues (in a
private run configuration, never on the command line), with `--offline`, and holding the same lease. The server starts the engine
as its child, and stopping the lease stops both. The run configuration names no MCP server and no command, and the server's
per-user records go to the run folder.

Memory admission and the context fit use Strata's own rules. One compute job runs per device, as with every engine.

`poolhouse-claude`, `poolhouse-codex`, `poolhouse-opencode` and `poolhouse-pi` take `--engine strata`. A coding session or a
delegate started without it takes a Strata server already up for its model, as it takes a llama-server; with none up it serves
with llama.cpp. A model's chat template is not replaced for this engine, which takes a late system message itself. A busy
server's slots are not grown. `poolhouse-serve status` names the engine and its drafter, and a delegate's record names the engine.

## Drafting

A Strata server drafts unless the lease says `--no-mtp`. Without `--draft` the drafter is Strata's default for the model set and
device: MTP for IQ3_S on a Mac; DFlash2 for the other sets on a Mac and whichever suits the card on Windows, when DFlash2's files
are on the device or may be fetched. Its publisher is not one Poolhouse knows: where the allow file does not name it, a server
start does not fetch it, MTP drafts, and `poolhouse-serve status` says so and why. `poolhouse-models pull
hf:incoai/Qwen3.8-Flash-Next-DFlash2/model.safetensors` by a person, or a line for it in the allow file, makes it available.

DFlash2 starts at Strata's own window and confidence for the device; `--spec-n-max N` and `--engine-option=--draft-conf=C`
replace them. On a Mac it drafts for one conversation at a time: with `--parallel` above 1 MTP drafts, and naming DFlash2 is
refused.

## What does not work

- Pictures. The server is started without an image encoder and a request holding a picture is refused. Strata's server reads a
  picture named by a file path or an address, so vision stays off until it is limited to picture data sent in the request.
- The model server sandbox (`POOLHOUSE_SANDBOX_SERVE`): a lease asking for it is refused for this engine, which listens on a TCP
  port. On Windows the server runs unconfined.
- Embeddings, reranking, slot save and restore, escalating slots, chat template files, tensor placement flags and RoPE scaling are
  llama.cpp's.
- Long contexts on a Mac: Strata publishes quality measurements there only to 32,768 tokens.

## Live proof

`poolhouse-serve strata-proof MODEL` serves the model through the broker and records, from the server's own replies: reading and
writing speed; a code word found at three depths of prompts up to 245,000 tokens; a tool call and the answer after its result; how
much of a conversation is reused on the next turn and after another conversation; the thinking switch; refusal of pictures named
by file or address; and the drafter. The record is written under `~/.poolhouse/strata/proof/`. A measurement is quoted with that
record's date, command, model and device.
