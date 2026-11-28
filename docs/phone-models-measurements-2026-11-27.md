# Phone model measurements, 2026-11-27

Galaxy S26 Ultra (SM-S948W, SM8850), Android API 37, 11.6 GB of memory, battery saver off unless said. Model files:
`gemma-4-E2B-it.litertlm` at revision `2e12bfd869b2962984f97a1f2a26807316d534cc` of
`litert-community/gemma-4-E2B-it-litert-lm`, in the app's own storage. LiteRT-LM 0.20.0. Command, one run at a time:

```
adb shell am instrument -w -r -e action speed -e model gemma-4-e2b-litert -e context 16384 \
    app.poolhouse.phone.llmtest/app.poolhouse.llm.bench.BenchRunner
```

`-e activation fp32` chose 32-bit GPU activations; without it the runs below used the runtime's 16 bits. No run used the
draft model. Each figure is from one run. `app/android/llm/measure.py` repeats the set, with the draft model and the shared
folder, letting the phone cool between runs.

## E2B on the GPU at 16384, with the picture encoder

| | 16-bit | 32-bit |
|---|---|---|
| Load | 8.4 s (first after install) | 8.5 s |
| Memory (PSS) at load / peak | 3.5 / 4.5 GB | 5.4 / 6.9 GB |
| Short question: first token, tokens/s | 0.59 s, 37 | 0.63 s, 37 |
| Picture of a number: first token, read right | 0.97 s, yes | 1.9 s, yes |
| 15426-token prompt: first token | 14.8 s | 42.0 s |
| 15426-token prompt: prefill tokens/s | 1063 | 370 |
| 15426-token prompt: answer | wrong digits | right |
| Follow-up in the same conversation: first token | 0.36 s | 0.54 s |
| Thermal status, battery | 1 to 2, 39 C | 2, 41 C |

## Copying and fixed tasks at 16384, text only (`-e action quality`)

| | 16-bit | 32-bit |
|---|---|---|
| Short snippets of digits, hashes and code copied exactly | 7 of 7 | 7 of 7 |
| Two lines out of a 150 and a 300 line log | 0 of 2 | 2 of 2 |
| Questions and code tasks passed | 9 of 12 | 9 of 12 |
| Load, peak memory | 3.1 s, 3.7 GB | 2.7 s, 5.7 GB |
| Thermal status at the end | 2 | 3 |

## Other configurations, 16-bit

| Configuration | First token on a long prompt | Tokens/s | Memory | Long-prompt answer |
|---|---|---|---|---|
| GPU 8192, 7040 tokens (battery saver on) | 4.0 s | 25 | 2.6 GB | wrong digits |
| CPU 8192, 7040 tokens (battery saver on) | 54 s | 18 | 1.5 to 2.5 GB | right |
| GPU 32000, 30330 tokens (battery saver not recorded) | 59 to 61 s | 11 | 6.1 GB | wrong digits |

A 36868-token prompt at 32000 fails with `Input token ids are too long`. 32000 in 32 bits is not measured.

## llama.cpp, E2B GGUF at 8192, 7040-token prompt

| Processor | Load | First token | Tokens/s | Memory | Answer |
|---|---|---|---|---|---|
| GPU (OpenCL) | 6.3 s (24 s first) | 144 s | 4.9 (17 on a short question) | 4.5 GB | right |
| CPU, 6 threads | 2.9 s | 162 s | 14 | 3.9 GB | right |

## Shared folder, on an Android 16 emulator (arm64)

- A file made in `Download/.models` with no permission is read by path. After a reinstall it is listed by size and refused
  by path (`EACCES`), also through the folder grant's descriptor path.
- Copying the E2B file into an own file through the grant took 23 s.
- LiteRT-LM loaded the model by its path in `Download/.models`, mapping that file; the only large file it added in the
  app's storage was its CPU weight cache (788 MB). The file reports draft model support, and the engine started with the
  drafter on the CPU at 4096.
- Replies cannot be measured there: the runtime's CPU code stops with an illegal instruction on the emulator's processor.

## Not measured

Load time, memory mapping and speed from the shared folder against the app's own storage; the draft model's effect and
whether the GPU accepts it in 32 bits; 8192 and 32000 in 32 bits; sustained speed under heat; E4B; behaviour in the
background beside other work.
