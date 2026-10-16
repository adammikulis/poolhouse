# LiteRT-LM pin and Gemma 4 K/V cache types (2026-10-16)

What the pinned LiteRT-LM Android release and the official Gemma 4 E2B and E4B `.litertlm` packages are, and what the packaged
graphs say about the K and V cache element types. Static inspection only: no model was run, nothing ran on a phone, no
speed or memory was measured, and this document holds no benchmark number.

## Result

- The Android artifact is `com.google.ai.edge.litertlm:litertlm-android:0.18.0`. Its source is most likely tag `v0.18.0`
  (`b2f686e2ed4718fb84ec398a61dd59ca0f0aff27`), which declares LiteRT `26895c9fbcc25c43faa8c1a98cd1fd28951602c3`.
  Neither the commit nor the LiteRT revision is recorded inside the AAR, so the correspondence is consistent evidence, not proof.
- In the generic `gemma-4-E2B-it.litertlm` and `gemma-4-E4B-it.litertlm` packages every K and V cache tensor of the decode,
  prefill and verify graphs is `INT8`, with one scale per tensor, zero point 0, and an `odml.cache_update` composite that
  carries the same `scale_k` and `scale_v` values.
- That is a static, per-tensor, symmetric 8-bit cache. It is not ggml `q8_0` (blocks of 32 values, each with its own fp16
  scale computed from the data). Both caches being 8-bit in the graph does not make them `q8_0`, and no setting that selects
  another type was found.
- The `-gpu.litertlm` packages hold weights only (no operators, no cache tensors). Their K and V types cannot be read from the
  file and are unknown.
- The type the delegate stores on a phone, and whether a given backend keeps the graph's `INT8`, are not shown by the files.

## Pins

| Item | Pin |
|---|---|
| LiteRT-LM release tag | `v0.18.0` = `b2f686e2ed4718fb84ec398a61dd59ca0f0aff27` (committed 2026-10-06T17:55:16Z; lightweight tag; GitHub release published 2026-10-06T18:18:59Z, target `release/v0.18.0`) |
| LiteRT revision the tag builds against | `26895c9fbcc25c43faa8c1a98cd1fd28951602c3` (2026-09-25), `WORKSPACE` `LITERT_REF` at the tag |
| Android artifact | `litertlm-android` `0.18.0` on Google Maven; `.aar` 20,905,807 bytes, sha256 `706cbb8a87739b2818111bf3a19a78b8f7781586d10f80adf3415581fab7595f` (equals the published `.aar.sha256`) |
| Native libraries in the AAR | `jni/arm64-v8a/liblitertlm_jni.so` sha256 `4a94a3f955ad7c58cbcdfc5cb4ff566187bb9e8c5bc2c4056b4e226646c7e8fd`; `jni/x86_64/liblitertlm_jni.so` sha256 `177ed8b6cbea8fec901e705d78aadf8ccbc51004e6d03cc82915a55f313fb667`; no other ABI |
| AAR dependencies (pom) | gson 2.14.0, kotlin-reflect 2.4.0, kotlinx-coroutines-android 1.11.0; minSdk 24 |
| Research snapshot (main) | LiteRT-LM `d87575de09465e0d1bffd67c3603ce6141d71496`, LiteRT `951b9336bdab9e2054445951289e4e5c78f420ab`; neither is what 0.18.0 ships (below) |
| E2B package | `litert-community/gemma-4-E2B-it-litert-lm` revision `b3ca0d2f076785a8f4b2219ddbd2bdb99954eae1`, `gemma-4-E2B-it.litertlm` 2,588,147,712 bytes, sha256 `181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c` |
| E4B package | `litert-community/gemma-4-E4B-it-litert-lm` revision `2eee7ac325f20eb8c9ac1d0e972f7c84663062da`, `gemma-4-E4B-it.litertlm` 3,659,530,240 bytes, sha256 `0b2a8980ce155fd97673d8e820b4d29d9c7d99b8fa6806f425d969b145bd52e0` |
| E2B GPU package | same revision, `gemma-4-E2B-it-gpu.litertlm` 2,008,432,640 bytes, sha256 `a53a59001894c58e6bdb5b9b227709f91a2e3e556baa7d85acf9c55402ba5cf5` |
| E4B GPU package | same revision, `gemma-4-E4B-it-gpu.litertlm` 2,969,059,328 bytes, sha256 `4912bb5a9c30993c51a7711f763212077458529312175df0573a78323a2bb7ff` |

All four package hashes were recomputed after download and equal the `lfs.sha256` values of the Hub API for those revisions.

## The 0.18.0 artifact against the sources

Established:

- The tag `v0.18.0` exists and the GitHub release names it. Google Maven lists `0.18.0` for `litertlm-android` and
  `litertlm-jvm` and has no `-sources.jar` for it.
- The AAR's Kotlin classes include `ModelInfo`, `ActivationDataType`, `EmbeddingEngine` and `ExperimentalFlags` with
  `enableBenchmark`, `enableConversationConstrainedDecoding` and `enableSpeculativeDecoding`. That flag list equals
  `ExperimentalFlags.kt` at the tag and lacks `enableYnnpack`, which the main snapshot has (added on main 2026-09-30).
- The native library contains the string `0.18.0`, the `odml.cache_update` handling (`odml.cache_update is missing head_size.`,
  `scale_v`) and the K/V cache slice names, so the packaged graphs' cache composite is a feature of this build.
- At the tag, `Config.kt` (`activationDataType`), `ModelInfo.kt` and `Conversation.kt` are byte-identical to the main
  snapshot; `Engine.kt`, `Benchmark.kt`, `state.cc`, `llm_executor_settings_utils.cc`, `executor_settings_base.h` and the NPU
  cache file differ.
- `runtime/engine/accelerator_memory_monitor.cc` does not exist at the tag. The earlier note that accelerator memory
  tracking returns `nullopt` describes the main snapshot only; whether 0.18.0 reports accelerator memory is not established.
- The main snapshot's `WORKSPACE` pins LiteRT `9b8c7f77e7a29e6073e614a05e1e38be7b761229` (2026-10-13). The earlier cache
  research cited LiteRT `951b9336...` (2026-10-15), which is 464 commits after the tag's LiteRT and 97 after the main snapshot's,
  so its line numbers describe neither build.
- At the tag's LiteRT, the cache composite parser sets the cache outputs to `UINT8` and reads `scale_k` and `scale_v`
  (`ml_drift_delegate/delegate/composite/add_values_to_cache_parser.cc`). The parser and kernel at `951b9336...` differ in layout
  handling and type names but keep the same attributes.

Not established:

- The commit the AAR was built from. Google Maven's `Last-Modified` for the AAR is 2026-10-06 15:49:10 GMT, which is 2 hours
  6 minutes before the tag's commit; the tag commit is an embedding-tool change after the release-notes commit
  (`f8fb0445cd47bd4b5273c9cded62b96a4d4d4dbd`, 17:38Z). The AAR may have been built from an earlier release-branch commit with the
  same Kotlin API. The tag has four commits that the main snapshot lacks.
- Which LiteRT revision is compiled into the AAR's native library.
- Whether the `1.6.0` container version of the `-gpu` packages is read by 0.18.0. The reader at the tag compares only the major
  version (`schema/core/litertlm_read.cc`).

## What the packaged graphs say about K and V

The generic packages are container format `1.5.0` with 12 sections: metadata, tokenizer and ten TFLite models (vision, audio,
embedding, the language model, and a multi-token-prediction drafter among them). Section 10 is the language model. It has
the subgraphs `decode`, `prefill_128`, `prefill_1024` and `verify`; section 11 is the drafter.

The section table carries string items. In the generic packages: sections 2 and 3 `tf_lite_embedder` and
`tf_lite_per_layer_embedder`; 4 `tf_lite_audio_encoder_hw` and 5 `tf_lite_audio_adapter` with `backend_constraint: cpu`; 6
`tf_lite_end_of_audio`; 7 `tf_lite_vision_encoder` with `prefer_activation_type: fp16`; 8 `tf_lite_vision_adapter` with
`backend_constraint: cpu`; 9 `tf_lite_end_of_vision`; 10 `tf_lite_prefill_decode` with `prefer_activation_type: fp16`; 11
`tf_lite_mtp_drafter`. The `-gpu` packages have one model section, `tf_lite_artisan_text_decoder` with
`backend_constraint: gpu_artisan` and `gpu_artisan_weights_version: 0.0`, and no vision or audio sections: a text-only decoder
for one GPU executor. Another Android application of the owner's, which runs LiteRT-LM 0.14.0 with Gemma 4 E2B on the S26 Ultra
(reported there, not repeated here), reads the same section table and pins the vision and audio encoders to the CPU; that
agrees with the `backend_constraint` items above.

| Package | K tensors per graph | V tensors per graph | Element type | Scale | Zero point | Graph cache length |
|---|---|---|---|---|---|---|
| E2B generic, section 10 | 15 | 15 | `INT8` (K and V) | one per tensor; 15 distinct K values, 2 distinct V values | 0 | 32003 |
| E4B generic, section 10 | 24 | 24 | `INT8` (K and V) | one per tensor; 24 distinct K values, 2 distinct V values | 0 | 32003 |
| E2B and E4B generic, section 11 (drafter) | 2 | 2 | `INT8` (K and V) | one per tensor | 0 | 32003 |

- K tensors are `[1, kv_batch, 32003, head_size]` and V tensors `[1, kv_batch, head_size, 32003]`, with `head_size` 256 or 512 and
  `kv_batch` 1 (E2B) or 2 (E4B).
- Every `odml.cache_update` composite (one per cache layer in each of the four graphs) has the attributes `cache_size`,
  `head_size`, `kv_cache_batch_size`, `scale_k` and `scale_v`. In every composite `scale_k` and `scale_v` equal the scale of
  the K and V cache tensor it updates.
- The composite's decomposition subgraph (the fallback a backend without a fused kernel runs) is `QUANTIZE` of the float
  K and V updates to `INT8`, then `DYNAMIC_UPDATE_SLICE` on `INT8` buffers. A decomposed run also stores `INT8`.
- The scales are constants chosen when the package was made. They do not vary with the data of a conversation.

Against ggml `q8_0` (from the earlier note, llama.cpp `2851b3bcbf8d993a2243ae751c0a7dfa8e944a40`: 32 values per block, fp16 scale
per block, maximum absolute value over 127): the packages use one scale for a whole tensor, fixed at conversion. The
representations differ in granularity, in where the scale comes from, and in memory per value, so no quality or memory
equivalence follows from "both are 8-bit". For Poolhouse's requirement that K and V both use an 8-bit cache, the generic Gemma 4
packages satisfy "8-bit K and V in the graph" and do not satisfy "`q8_0`". No FP16 cache appears in these graphs.

Not shown:

- The `-gpu` packages: container `1.6.0`, three sections, one TFLite model of 2,003,697,664 bytes (E2B) or 2,964,324,352 bytes
  (E4B) with no operators, no inputs, no outputs, no signatures and no cache-named tensors. The tensor types are `INT8`, `INT4`,
  `UINT8`, `INT32` and `FLOAT32`. The native library names a graph builder for the language model
  (`runtime/executor/tensor_llm/gemma3_graph_builder.cc`) and a `GPU_ARTISAN` executor, so the K/V types of this package are
  decided at run time and are unknown here. The model card's S26 Ultra GPU row lists a model size of 2583 MB, near the generic file (2,588 MB) and far
  from the `-gpu` file (2,008 MB), so the card's phone rows describe the generic package.
- The physical cache a GPU or CPU backend allocates on a phone (the kernel may keep or change the graph's type), its memory,
  and whether the Kotlin `activationDataType` override changes it. At the tag, `activationDataType` selects the GPU calculation
  precision (`FLOAT32` or otherwise `FLOAT16`) in `llm_executor_settings_utils.cc`; it is not a cache type.
- The metadata section is a binary protocol buffer that was not decoded; it holds no readable cache or quantisation strings.
- NPU variants and the vendor delegates. Only the generic and `-gpu` packages were inspected.

## Reproduce

Work in a directory outside the repository. `$WORK` below is any such directory.

```sh
WORK=...                                   # outside the repository
python3 -m venv "$WORK/venv" && "$WORK/venv/bin/pip" install flatbuffers==25.12.19 ai-edge-litert==2.3.0
mkdir -p "$WORK/dl" "$WORK/maven" && cd "$WORK"

# 1. The Android artifact
M=https://dl.google.com/dl/android/maven2/com/google/ai/edge/litertlm/litertlm-android/0.18.0
curl -fLO --output-dir maven "$M/litertlm-android-0.18.0.aar" && curl -fs "$M/litertlm-android-0.18.0.aar.sha256"
shasum -a 256 maven/litertlm-android-0.18.0.aar
unzip -o maven/litertlm-android-0.18.0.aar -d maven/aar && shasum -a 256 maven/aar/jni/*/liblitertlm_jni.so
curl -s https://api.github.com/repos/google-ai-edge/LiteRT-LM/git/ref/tags/v0.18.0
curl -s https://raw.githubusercontent.com/google-ai-edge/LiteRT-LM/b2f686e2ed4718fb84ec398a61dd59ca0f0aff27/WORKSPACE | grep LITERT_REF

# 2. The packages, pinned by revision (sizes and sha256 from the Hub API)
H=https://huggingface.co/litert-community
curl -fL -o dl/e2b.litertlm "$H/gemma-4-E2B-it-litert-lm/resolve/b3ca0d2f076785a8f4b2219ddbd2bdb99954eae1/gemma-4-E2B-it.litertlm"
curl -fL -o dl/e4b.litertlm "$H/gemma-4-E4B-it-litert-lm/resolve/2eee7ac325f20eb8c9ac1d0e972f7c84663062da/gemma-4-E4B-it.litertlm"
curl -s "https://huggingface.co/api/models/litert-community/gemma-4-E2B-it-litert-lm/revision/b3ca0d2f076785a8f4b2219ddbd2bdb99954eae1?blobs=true"
shasum -a 256 dl/*.litertlm                # compare with the Pins table

# 3. The graphs (save the script below as litertlm_kv.py)
"$WORK/venv/bin/python" -I litertlm_kv.py dl/e2b.litertlm
"$WORK/venv/bin/python" -I litertlm_kv.py dl/e4b.litertlm
```

`litertlm_kv.py` reads the file through a read-only memory map; it does not run a model or load a delegate.

```python
"""Read-only: list the sections of a .litertlm package and the K/V cache tensors of its TFLite graphs."""
import collections
import mmap
import struct
import sys

from ai_edge_litert import schema_py_generated as tfl
from flatbuffers import flexbuffers, number_types as nt, table as fbtable

TYPES = {v: k for k, v in vars(tfl.TensorType).items() if not k.startswith("_")}
OPS = {v: k for k, v in vars(tfl.BuiltinOperator).items() if not k.startswith("_")}
KINDS = ["NONE", "GenericBinaryData", "Deprecated", "TFLiteModel", "SP_Tokenizer", "LlmMetadataProto",
         "HF_Tokenizer_Zlib", "TFLiteWeights", "EmbeddingMetadataProto", "ExecutorMetadataProto",
         "TtsMetadataProto", "AsrMetadataProto"]


def slot(tab, i):
    off = tab.Offset(4 + 2 * i)
    return tab.Pos + off if off else 0


def scalar(tab, i, flags):
    return tab.Get(flags, slot(tab, i)) if slot(tab, i) else 0


def sections(mm):
    """Header layout: magic, three u32 version fields, 4 spare bytes, u64 header end, flatbuffer."""
    assert bytes(mm[:8]) == b"LITERTLM"
    version = struct.unpack("<III", mm[8:20])
    buf = bytearray(mm[32:struct.unpack("<Q", mm[24:32])[0]])
    root = fbtable.Table(buf, struct.unpack("<I", buf[:4])[0])
    meta = fbtable.Table(buf, root.Indirect(slot(root, 1)))
    vec = meta.Vector(slot(meta, 0) - meta.Pos)
    count = meta.VectorLen(slot(meta, 0) - meta.Pos)
    out = []
    for i in range(count):
        obj = fbtable.Table(buf, meta.Indirect(vec + 4 * i))
        out.append((KINDS[scalar(obj, 3, nt.Uint8Flags)], scalar(obj, 1, nt.Uint64Flags),
                    scalar(obj, 2, nt.Uint64Flags), strings(buf, obj)))
    return version, out


def strings(buf, obj):
    """The string-valued key/value items of a section (model_type, backend_constraint, ...)."""
    pos = slot(obj, 0)
    found = {}
    for k in range(obj.VectorLen(pos - obj.Pos) if pos else 0):
        kv = fbtable.Table(buf, obj.Indirect(obj.Vector(pos - obj.Pos) + 4 * k))
        if scalar(kv, 1, nt.Uint8Flags) == 9:
            val = fbtable.Table(buf, kv.Indirect(slot(kv, 2)))
            found[kv.String(slot(kv, 0)).decode()] = val.String(slot(val, 0)).decode()
    return found


def row(sg, index):
    t = sg.Tensors(index)
    q = t.Quantization()
    scale = q.ScaleAsNumpy().tolist() if q is not None and q.ScaleLength() else []
    zero = q.ZeroPointAsNumpy().tolist() if q is not None and q.ZeroPointLength() else []
    return t.Name().decode(), TYPES[t.Type()], scale, zero


def cache_update_attributes(model, op):
    code = model.OperatorCodes(op.OpcodeIndex())
    if OPS.get(max(code.BuiltinCode(), code.DeprecatedBuiltinCode())) != "STABLEHLO_COMPOSITE":
        return None
    comp = tfl.StableHLOCompositeOptions()
    comp.Init(op.BuiltinOptions2().Bytes, op.BuiltinOptions2().Pos)
    if comp.Name() != b"odml.cache_update":
        return None
    return flexbuffers.Loads(comp.CompositeAttributesAsNumpy().tobytes()), comp.DecompositionSubgraphIndex()


def summarize(buf, label):
    model = tfl.Model.GetRootAs(buf, 0)
    for si in range(model.SubgraphsLength()):
        sg = model.Subgraphs(si)
        cache = {k: [] for k in "kv"}
        for i in range(sg.TensorsLength()):
            name, dtype, scale, zero = row(sg, i)
            for k in cache:
                if "kv_cache_%s_" % k in name:
                    cache[k].append((dtype, tuple(scale), tuple(zero)))
        if not any(cache.values()):
            continue
        print("%s subgraph %r" % (label, sg.Name().decode()))
        for k, entries in cache.items():
            scales = {e[1] for e in entries}
            print("  %s: tensors=%d types=%s zero_points=%s scales_per_tensor=%s distinct_scales=%d" % (
                k.upper(), len(entries), sorted({e[0] for e in entries}), sorted({e[2] for e in entries}),
                sorted({len(s) for s in scales}), len(scales)))
        updates, decomposition, mismatch = collections.Counter(), set(), 0
        for i in range(sg.OperatorsLength()):
            op = sg.Operators(i)
            found = cache_update_attributes(model, op)
            if found:
                updates[tuple(sorted(found[0]))] += 1
                decomposition.add(found[1])
                for j in range(op.InputsLength()):
                    name, _, scale, _ = row(sg, op.Inputs(j)) if op.Inputs(j) >= 0 else ("", "", [], [])
                    for k in "kv":
                        if "kv_cache_%s_" % k in name and abs(scale[0] - found[0]["scale_" + k]) > 1e-9:
                            mismatch += 1
        for idx in sorted(decomposition)[:1]:
            d = model.Subgraphs(idx)
            names = [OPS[max(model.OperatorCodes(d.Operators(j).OpcodeIndex()).BuiltinCode(),
                             model.OperatorCodes(d.Operators(j).OpcodeIndex()).DeprecatedBuiltinCode())]
                     for j in range(d.OperatorsLength())]
            print("  odml.cache_update composites=%s scale mismatches=%d decomposition=%s" % (
                dict(updates), mismatch, names))


with open(sys.argv[1], "rb") as handle:
    data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
    version, parts = sections(data)
    print(sys.argv[1], "litertlm format", version)
    for n, (kind, begin, end, items) in enumerate(parts):
        print("section %d %s size=%d %s" % (n, kind, end - begin, items))
        if kind == "TFLiteModel":
            summarize(memoryview(data)[begin:end], "section %d" % n)
```

Expected output for each generic package: the section list with each section's items, then for `decode`, `prefill_128`, `prefill_1024` and `verify` of section
10 and for `main` of section 11 a `K:` and a `V:` line with `types=['INT8']`, `zero_points=[(0,)]` and `scales_per_tensor=[1]`,
and for section 10 `scale mismatches=0`.
For a `-gpu` package the script prints the section list with its items and nothing else.

## Limits

- Poolhouse's model fetch refused `litert-community` as an unknown publisher until a person answers in the requests inbox, and
  the Hugging Face command-line download is blocked for agents. The four packages were fetched with `curl` from the pinned
  revisions into a scratch directory, hashed against the Hub, and read as bytes; no runtime loaded them.
- The inspection shows what the files declare. Backend selection, the delegate's stored type, the Kotlin `activationDataType`
  override and Android behaviour need a run on the phone under maintained admission.
- The TFLite reader is `ai-edge-litert` 2.3.0 (schema bindings) with `flatbuffers` 25.12.19; the container header layout is read from
  `schema/core/litertlm_read.cc` and `litertlm_header_schema.fbs` at the tag. A container whose layout differs is not read.
- Numbers quoted from the model cards in the earlier notes (context, sizes) are the cards' claims at these revisions, not
  measurements made here.

## Sources

- LiteRT-LM tag `v0.18.0` and `d87575de09465e0d1bffd67c3603ce6141d71496`:
  https://github.com/google-ai-edge/LiteRT-LM
- LiteRT `26895c9fbcc25c43faa8c1a98cd1fd28951602c3`, `9b8c7f77e7a29e6073e614a05e1e38be7b761229`,
  `951b9336bdab9e2054445951289e4e5c78f420ab`: https://github.com/google-ai-edge/LiteRT
- Google Maven: https://dl.google.com/dl/android/maven2/com/google/ai/edge/litertlm/
- E2B and E4B packages: https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm and
  https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm
