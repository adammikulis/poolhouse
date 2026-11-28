# Models on the phone

The Android app runs a language model on the phone, with no computer linked and no network after the download
(`app/android/llm`). Chat takes text and pictures, streams the reply, can be stopped, and shows the context used and the
speed. Measurements are in [phone-models-measurements-2026-11-27.md](phone-models-measurements-2026-11-27.md).

## Runtime and models

- LiteRT-LM, pinned by version and by the checksums of its archives (`llm/build.gradle.kts`). It reads a long prompt on the
  phone's GPU many times faster than llama.cpp does there.
- Each model is one `.litertlm` file pinned by repository revision, size and SHA-256 (`llm/model/Catalog.kt`): Gemma 4 E2B
  and E4B, Apache 2.0, ungated, each reading pictures, with up to 32000 tokens of context. A conversation starts at 16384.
- The GPU computes in 32 bits. In the runtime's 16-bit default the model miscopies numbers and hashes out of long input.
  The setting changes the GPU delegate's arithmetic; the key and value cache is 8-bit in the model file in both. 32 bits
  costs about 2 GB more memory at 16384 and slows the reading of a long prompt.
- Every load asks for the file's draft model (speculative decoding). Where a processor refuses it, the model loads without
  it and the chat says so.

## Where the files lie

`Download/.models`, a hidden folder in shared storage.

- The app makes it and writes into it with no permission and no prompt. File lists and galleries do not show it.
- It stays when the app is removed.
- The runtime reads a model by its path. Android lets an install read by path only the files it made itself.
- A later install sees that the files are there and asks for the folder once: **Use them**, then the system's
  **Use this folder** and **Allow**. It copies each model into a file of its own, checking the SHA-256 on the way, and
  removes the old file. The copy needs one model's size free for a moment.
- **Add model files from another folder** takes a model another app saved, by its size and SHA-256, and leaves the original
  where it is.
- A file is hashed again before a load only when its identity or change times differ from the last check; the record is in
  the app's own storage. The system sets the change time on every change and no app can set it back. The file is looked at
  again after the load.
- Deleting a model removes the file this install holds, after a question that names it.

### What Android does not allow

| Wanted | Result |
|---|---|
| `.models` at the top of shared storage | Only with all-files access (`MANAGE_EXTERNAL_STORAGE`), which reads and writes every shared file and is turned on in a settings screen after each install. The app does not ask for it |
| Reading an earlier install's file without copying it | The picker gives a descriptor; the runtime takes only a path, and opening the descriptor again by path is refused |
| Writing downloads through the picker's folder access | A file made that way cannot be opened by path |
| Making the hidden folder through MediaStore | MediaStore renames a leading dot; plain file calls keep it |
| Picking the storage root or `Download` itself | The picker refuses both; a folder inside `Download` is allowed |

## Android AICore

AICore offers Gemma 4 as the system's shared model on the Galaxy S26 series through ML Kit's Prompt API. The app does not
use it: the Prompt API takes under 4000 tokens of input
([get started](https://developers.google.com/ml-kit/genai/prompt/android/get-started)), answers only while the app is in
front and under a quota ([overview](https://developers.google.com/ml-kit/genai)), and offers no control of precision.

## Lending the model to the pool

Off by default, with one switch ([phone-api.md](phone-api.md), section 5). Off listens for nothing. On, the phone answers
pool requests and a node on the same phone, pauses when hot, in battery saver or low on charge, and puts the person's own
questions first. A request and its answer stay in memory only.
