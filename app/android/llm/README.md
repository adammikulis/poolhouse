# The model on the phone

The `:llm` module of the Android app: chat with a model on the phone, the model screen, and lending the model to the pool.
What it does and why Android makes it do so: [docs/phone-models.md](../../../docs/phone-models.md).

| Package | What |
|---|---|
| `engine` | one loaded model, one reply at a time, the person's questions first |
| `model` | the pinned catalogue, the shared folder's files, downloads, the fit verdict |
| `pool` | the pool switch, the computer's protocol and the loopback endpoint |
| `android` | LiteRT-LM, the services and what is read from the phone |
| `ui` | the chat and model screens |

The tests (`:llm:testDebugUnitTest`) use no model. Measurements run on a phone through the debug build's instrumentation:
`measure.py` for the set, `src/debug/.../bench/BenchRunner.kt` for one run.
