# Local coding model capabilities

Researched 2026-10-20 from publisher model cards, configurations, templates and runtime source.
Repository baseline: `68d868c8dfb742bf2bba61b7987b7eb2a614853d`.
This is source research, a downloaded archive inspection and one separately attributed model inspection. No model execution,
hardware benchmark, application journey or runtime update was performed for this note.

## Bonsai 2: format and runtime requirements

The publisher's demo at `b1ad9c8fc9366e214b079f44a4b3b24945608f54` distinguishes three
packings. `PQ2_0` and `PTQ1_0` require the PrismML llama.cpp fork. Official `Q2_0`
requires a sufficiently recent upstream build containing the full Bonsai 2 model and Hadamard
integration; recognizing a quantization type alone does not establish correct execution.
See the [format support table][bonsai-support] and [demo overview][bonsai-demo].

The pinned fork defines `GGML_TYPE_PQ2_0 = 142` and `GGML_TYPE_PTQ1_0 = 143` in
[ggml.h][bonsai-types]. An `output.weight` tensor with type 142 therefore needs that
format's implementation, including the model transforms. It cannot be made compatible by
renaming the file or substituting an ordinary two-bit quantization label.

The demo's [binary downloader][bonsai-downloader] pins `prism-b10787-73bfe6c`.
The [published release][bonsai-release] targets immutable source
`73bfe6cc485554822f80c473a3c0c4f25e80f41b`, published 2026-10-19 at 22:22:27 UTC.
The macOS arm64 archive is
`llama-prism-b10787-73bfe6c-bin-macos-arm64.tar.gz`; its GitHub release asset digest is
`sha256:59230b0d3e90715095a3f1b9a42bb6723308e7cf3d2d3ef562f49213e95fcfc1`.
An inert download through `poolhouse.net.get` independently matched this digest: 11,573,797
bytes and 90 tar members. There are 18 symbolic links, all relative dylib links; each chain
terminates at a regular file inside the archive root, with no cycles or missing targets.
For example, `libllama.dylib` links through `libllama.0.dylib` to `libllama.0.2.0.dylib`.
There are no hard links or special-device entries. `llama-server` and
`libllama-server-impl.dylib` are regular files. This archive is not a collection of static
executables: safe installation needs validated in-root library-link handling. Nothing was
extracted, executed or installed during inspection.
The demo README still links an older release in places; the downloader and support baseline
agree on this newer pin.

For a source build, the [publisher's macOS builder][bonsai-build] requires Xcode command-line
tools and CMake. Its Apple Silicon configuration uses Release, arm64, deployment target 14.0,
`GGML_METAL=ON`, `GGML_METAL_EMBED_LIBRARY=ON`, `BUILD_SHARED_LIBS=OFF`,
`GGML_STATIC=ON` and `LLAMA_OPENSSL=OFF`. The script clones the moving `prism` branch;
a reproducible integration must also record the resolved immutable source revision.

### Native MTP and separate drafters

Three independent questions must stay separate: whether a runtime implements an MTP graph,
whether the selected artifact contains its trained MTP tensors, and whether a separately
published drafter is verified for that exact target.

The [pinned Qwen35 runtime implementation][bonsai-mtp] supports native MTP with a
positive `nextn_predict_layers` and the required `nextn.eh_proj`, `nextn.enorm` and
`nextn.hnorm` tensors. Its MTP graph also handles Hadamard-transformed embeddings.
The existence of that code does not add a trained head to a checkpoint.

The coordinating agent's read-only inspection of the selected Bonsai 2 27B PQ2 artifact
reported architecture `qwen35`, 402 type-142 tensors, 353 F32 tensors and 96 BF16 tensors;
`output.weight` is type 142. It found no nextn, MTP or draft metadata and no tensor names
containing nextn, MTP, `eh_proj` or `shared_head`. That inspected artifact has no embedded
MTP head. This is an inspection report, not an inference test.

At [model repository revision `b072e1d3`][bonsai-card], the published file listing contains
F16, PQ2_0, PTQ1_0 and vision-projector files, with no separate MTP or drafter file.
The publisher's [speculation guide][bonsai-speculation] explicitly says Bonsai 2 27B has
no official DSpark drafter released. Its older-generation drafters and measurements do not
establish Bonsai 2 compatibility; the cited community experiments are not fully validated.
An upstream Qwen head must not be selected solely because Bonsai has Qwen lineage.

For Bonsai 2 27B, the [exact model card][bonsai-card] names default effort `xhigh` and
supported shorter effort `medium`; it explicitly says `low` is unsupported and behaves close
to `xhigh`. The [demo][bonsai-demo] also distinguishes model effort from fixed reasoning-token
budgets. A UI label such as Medium meaning a 2,048-token budget is a different control from
the model's `reasoning_effort="medium"`.

## Catalogue labels and evidence

At the repository baseline, `poolhouse.fleet.catalogue.KNOWN` contains 24 display families:
GPT-OSS, Command R, DeepSeek, Nemotron, MiniCPM, SmolLM, Granite, Mistral, Mixtral,
StarCoder, Codestral, EXAONE, InternLM, Falcon, Bonsai, Ornith, Gemma, Llama, Qwen,
Phi, OLMo, Yi, GLM and LFM. Unknown names can form further groups from their first word.
These are grouping labels, not a capability allowlist.

The same module's fallback suggestions include Qwen3.5 4B/9B, Gemma 4 E4B/12B,
Qwen3.8 27B and a tiny story model. Hub discovery is broader. Meanwhile,
`poolhouse.client.families` supplies Qwen, Gemma, GPT-OSS and generic transport adapters;
their reply parsing and request formatting do not prove a particular model supports tools,
thinking switches or reasoning levels. `poolhouse.hub.capabilities` currently determines
chat eligibility from model metadata, not these detailed controls.

No family-wide context limit, output limit, thinking default or tool support is established
for any of the 24 labels by that catalogue. Entries below apply only to the exact variants
and source revisions named. Other variants remain unknown until their own evidence is read.

## Exact variant evidence

Context values describe the published model, not the context admitted by the local broker.
"Tools" below means publisher/template support; the actual runtime's parser and the coding
harness still need compatible round-trip support. "No switch" distinguishes fixed behavior
from an unknown control. None of these rows implies that a particular quantized conversion
preserves the cited template or that it has been run on available hardware.

| Exact publisher variant | Published context tokens | Thinking control and default | Tools and constraints |
|---|---:|---|---|
| Qwen/Qwen3.5-0.8B | 262,144 | `enable_thinking`; default off | Tool-use serving documented. Card warns about thinking loops. [Card, configuration and template][qwen35-08] |
| Qwen/Qwen3.5-2B | 262,144 | `enable_thinking`; default off | Tool-use serving documented; same explicit looping warning. [Sources][qwen35-2] |
| Qwen/Qwen3.5-4B | 262,144 native; extension advertised separately | `enable_thinking`; default on | Tool parser required; Qwen3 prompt soft switches are not officially supported. No discrete effort levels established by this template. [Sources][qwen35-4] |
| Qwen/Qwen3.5-9B | 262,144 native; extension advertised separately | `enable_thinking`; default on | Exact card documents tools and non-thinking API control. [Sources][qwen35-9] |
| Qwen/Qwen3.8-27B | 262,144 native; up to 1,000,000 with configured extension | `enable_thinking`, default on; effort low/medium/xhigh, default xhigh; `preserve_thinking`, default true | Tool schema template present. Unsupported effort values raise an error. [Sources][qwen38-27] |
| Qwen/Qwen3.8-Flash-Next | 262,144 native; up to 1,000,000 with configured extension | Same explicitly verified controls and defaults as the preceding row | Exact card/template independently checked; distinct architecture and runtime requirements remain. [Sources][qwen38-flash] |
| Qwen/Qwen3-4B | Card: 32,768 native; configuration: 40,960; up to 131,072 with YaRN | `enable_thinking`, default on; prompt soft switches only while enabled | Tools documented. Card explains 40,960 allocation as 8,192 typical prompt plus 32,768 output allowance. Preserve both evidence fields. [Sources][qwen3-4] |
| Qwen/Qwen3-4B-Thinking-2507 | 262,144 | Thinking only; no off switch documented | Tool use documented. Do not offer the baseline Qwen3 toggle. [Sources][qwen3-thinking] |
| Qwen/Qwen3-4B-Instruct-2507 | 262,144 | Non-thinking only; no on switch | Tool use documented. [Sources][qwen3-instruct] |
| Qwen/Qwen3-Coder-30B-A3B-Instruct | 262,144 | Non-thinking only; no on switch | Coding/tool calling documented, with the model's own tool syntax. [Sources][qwen3-coder] |
| google/gemma-4-E4B-it | 131,072 | `enable_thinking`, default false; `preserve_thinking`, default false | Native function calling; custom turn/channel/tool serialization. [Sources][gemma4-e4b] |
| google/gemma-4-12B-it | 262,144 | `enable_thinking`, default false; `preserve_thinking`, default false | Native function calling. Disabled thought-block rendering differs from E4B. [Sources][gemma4-12b] |
| openai/gpt-oss-20b | 131,072 | Reasoning effort low/medium/high, default medium; no reasoning-off switch established | Harmony formatting required; function calling supported. [Model documentation][gptoss] and [Harmony specification][harmony] |
| zai-org/GLM-4.7-Flash | Configuration: 202,752 | Template uses `enable_thinking`; absent/true opens thinking, false closes it | Card specifies glm47 tool parser and glm45 reasoning parser; no discrete effort enum verified. [Sources][glm47] |
| mistralai/Devstral-Small-2-24B-Instruct-2512 | Card: 256K; configuration: 393,216 | No thinking toggle or effort enum verified | Tool calling documented with Mistral parser. Published card/config disagreement remains explicit. [Sources][devstral] |
| deepseek-ai/DeepSeek-R1-Distill-Qwen-7B | Configuration: 131,072 | No thinking-off switch verified | Native tool calling not established by the inspected sources; Qwen lineage grants neither toggle nor tool support. [Sources][deepseek-distill] |

Source links in this table point to immutable repository trees containing the exact card,
configuration and tokenizer/template inspected. GPT-OSS limits additionally come from
official OpenAI documentation, checked on the research date.

## Output limits and UI contract

A context window, a generation default and a hard output maximum are separate fields.
For example, Qwen3.8's [27B card][qwen38-27] recommends separate reasoning and final-output
budgets for frameworks that support them within extended context; these recommendations do
not establish independent hard caps on every local runtime. Example `max_tokens` values in
cards likewise are request choices. The [GPT-OSS documentation][gptoss] explicitly publishes
131,072 maximum output tokens; the effective request still has to fit its served context.
No separate hard output cap is established here for the other exact variants.

Suggested capability records should identify the exact model artifact/revision, template
digest, runtime/build and evidence source; keep published native context, configured extension,
effective served context, output default and verified output maximum separate. Represent
reasoning as switchable, fixed-thinking, fixed-direct or unknown; carry its default, supported
effort enum and history-retention controls independently. Represent tools as supported,
unsupported or unknown, with runtime parser evidence separate from publisher capability.
This record shape is a recommendation inferred from the differing source contracts above.

For unknown metadata, retain an explicit unknown state. A generic request adapter does not
justify showing a functioning reasoning control. A family substring does not justify copying
another variant's context, output cap, sampler, MTP head or tool contract. A model's defaults
also do not prove the currently served runtime retained them: inspect the selected artifact
and effective runtime configuration before claiming a live capability.

[bonsai-demo]: https://github.com/PrismML-Eng/Bonsai-demo/blob/b1ad9c8fc9366e214b079f44a4b3b24945608f54/README.md
[bonsai-support]: https://github.com/PrismML-Eng/Bonsai-demo/blob/b1ad9c8fc9366e214b079f44a4b3b24945608f54/BACKEND-SUPPORT.md
[bonsai-types]: https://github.com/PrismML-Eng/llama.cpp/blob/73bfe6cc485554822f80c473a3c0c4f25e80f41b/ggml/include/ggml.h#L435
[bonsai-downloader]: https://github.com/PrismML-Eng/Bonsai-demo/blob/b1ad9c8fc9366e214b079f44a4b3b24945608f54/scripts/download_binaries.sh
[bonsai-release]: https://github.com/PrismML-Eng/llama.cpp/releases/tag/prism-b10787-73bfe6c
[bonsai-build]: https://github.com/PrismML-Eng/Bonsai-demo/blob/b1ad9c8fc9366e214b079f44a4b3b24945608f54/scripts/build_mac.sh
[bonsai-mtp]: https://github.com/PrismML-Eng/llama.cpp/blob/73bfe6cc485554822f80c473a3c0c4f25e80f41b/src/models/qwen35.cpp#L693
[bonsai-speculation]: https://github.com/PrismML-Eng/Bonsai-demo/blob/b1ad9c8fc9366e214b079f44a4b3b24945608f54/SPECULATIVE.md
[bonsai-card]: https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/tree/b072e1d3b35a0a630cece372c2127528e0994386
[qwen35-08]: https://huggingface.co/Qwen/Qwen3.5-0.8B/tree/2fc06364715b967f1860aea9cf38778875588b17
[qwen35-2]: https://huggingface.co/Qwen/Qwen3.5-2B/tree/15852e8c16360a2fea060d615a32b45270f8a8fc
[qwen35-4]: https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a
[qwen35-9]: https://huggingface.co/Qwen/Qwen3.5-9B/tree/c202236235762e1c871ad0ccb60c8ee5ba337b9a
[qwen38-27]: https://huggingface.co/Qwen/Qwen3.8-27B/tree/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
[qwen38-flash]: https://huggingface.co/Qwen/Qwen3.8-Flash-Next/tree/de4b8e4d43b917e7706784d8bb445c9af86a3540
[qwen3-4]: https://huggingface.co/Qwen/Qwen3-4B/tree/1cfa9a7208912126459214e8b04321603b3df60c
[qwen3-thinking]: https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507/tree/768f209d9ea81521153ed38c47d515654e938aea
[qwen3-instruct]: https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/tree/cdbee75f17c01a7cc42f958dc650907174af0554
[qwen3-coder]: https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct/tree/b2cff646eb4bb1d68355c01b18ae02e7cf42d120
[gemma4-e4b]: https://huggingface.co/google/gemma-4-E4B-it/tree/ee0ef6023621cff504d758262d4e04895a5af4a2
[gemma4-12b]: https://huggingface.co/google/gemma-4-12B-it/tree/707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7
[gptoss]: https://developers.openai.com/api/docs/models/gpt-oss-20b
[harmony]: https://developers.openai.com/cookbook/articles/openai-harmony
[glm47]: https://huggingface.co/zai-org/GLM-4.7-Flash/tree/7dd20894a642a0aa287e9827cb1a1f7f91386b67
[devstral]: https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512/tree/55c5b41e98c2dbd21b0c8afffc540dcfc9eb5128
[deepseek-distill]: https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B/tree/916b56a44061fd5cd7d6a8fb632557ed4f724f60
