# Decision models

A decision model answers one question: which of these named options? It returns a
probability for every option, so a caller can act on a confident answer and ask a person
about an unsure one. It does not write text. Use one where a program needs a category (route
this message, is this tool call destructive, which of these files matters) and an LLM where it
needs a sentence or a plan.

    from poolhouse.decide import decide
    got = decide("Which team?", "payouts have failed for 3 days",
                        {"billing": "payments, invoices", "sales": "pricing", "tech": "outages"},
                        abstain_below=0.7)
    got.choice, got.confidence, got.scores, got.abstained, got.latency_ms

`poolhouse-decide batch|ask|train|eval|calibrate|bench|jevbench|list|make-cases|export-cases|check-cases|fetch`
is the command (`train` and `make-cases` are there when the build holds training, a source checkout); `POST /decide` on the daemon (same bearer token as every route, 256 KB body
limit) and the `decide` MCP tool call the same router.

## Named questions about one state

`poolhouse.decide.questions` takes a state and a set of named questions and returns one typed
answer per name, each with probabilities.

    from poolhouse.decide import router
    from poolhouse.decide.questions import choice, decide, noul, score

    got = decide(
        "payouts have failed for 3 days and I want to cancel",
        {"urgent": noul("Is this urgent?"),
         "team": choice("Which team owns this?", ["billing", "sales", "tech"]),
         "anger": score("How angry is the customer?", 1, 5)},
        config=router.Config(backend="logprob", url="http://127.0.0.1:8080"),
        abstain_below=0.6)
    got["urgent"].p_true      # float in [0, 1]
    got["team"].scores        # {"billing": ..., "sales": ..., "tech": ...}, sums to 1
    got["anger"].expected     # float in [1, 5]; got["anger"].scores holds the probability of each of 1..5

The same from a shell, with `--json` printing `{name: answer}`:

    poolhouse-decide ask --state "payouts have failed for 3 days" --backend logprob \
        --noul urgent="Is this urgent?" \
        --choice team="Which team owns this?:billing,sales,tech" \
        --score anger="How angry is the customer?:1..5" --json

- `noul` is a yes/no question: `p_true` is the probability of `yes`. `choice` is one answer among
  named options. `score` is an integer scale `lo..hi`: `scores` holds the probability of each
  value and `expected` is their probability-weighted sum.
- A question that cannot be answered (the server is down, the model's first token is not an option
  letter, a scale wider than the backend takes, a state over the size limit) gives an `Answer`
  with `abstained=True`, `choice=None` and `error` set; the other answers are unaffected. The CLI
  exits 1 when any answer failed.
- The state is clipped and run through the logprob backend's `defang` (look-alikes folded, prompt
  tags and label-like line starts neutralised) and has `<answer>` tags removed, whichever backend
  answers.
- Each question is a separate model call: questions about one state share no computation yet, so
  ten questions cost ten calls.
- This returns categories, probabilities and an expected value. It does not write text.
- A worked example: [`examples/rbmk`](../examples/rbmk/README.md) is a 3D control room of a toy RBMK-1000 whose controls the released
  decider uses, with hierarchical named questions, interlocks and a logged, replayable decision record.


## Shared-state System One server batches

`SystemOne` sends one shared state and a named map of `choice`, `noul` or `score`
questions to an explicitly supplied, externally owned literal-loopback server:

    from poolhouse.client.systemone import SystemOne
    client = SystemOne("http://127.0.0.1:8099", "exact-served-model-id")
    result = client.ask({"items": [{"id": "item-1"}]}, {
        "item-1": {"type": "choice", "instructions": "Which action for item-1?",
                   "criteria": {"keep": "Keep it", "drop": "Drop it"}}})
    result["answers"]["item-1"]["choice"]

The CLI accepts the same `{state, questions}` object from a file or standard input:

    poolhouse-decide batch --url http://127.0.0.1:8099 --model exact-served-model-id \
        --request request.json --timeout 10

One HTTP call carries every question. The client validates the entire returned batch,
including model identity, question IDs, answer types, distributions, choices, normalized
choice confidence and expected score. Score confidence is checked for finite bounds; its
model-specific ordinal calibration is not independently reconstructed. Unsupported response
fields are refused. It never
retries questions individually or selects a replacement backend. Invalid input, transport
failure or malformed answers produce one error and exit status 2. Requests and responses
and HTTP error bodies are limited to 1 MiB; redirects, proxies, DNS hosts and remote hosts are refused. Question
instructions must identify their target explicitly; question-map keys are correlation IDs.

The caller owns and coordinates the external server's compute. This client does not start,
discover or register a server, acquire a managed model lease, or read a person's credentials.
Returned model identity is a comparison with a supplied string, not authentication of the
local service. Model answers remain untrusted advice and grant no permission. The timeout bounds individual socket waits, not total wall time against a server that drips data. Server-side
parallelism depends on that server's implementation; one HTTP batch alone does not prove it.

## Backends

| backend | what runs | needs | options |
|---|---|---|---|
| `pointer` | a LoRA on a 2B model with its output head removed, and a ~1M-parameter pointer head, one forward pass | `pip install 'poolhouse[decide-pointer]'`, 4.7 GB of pinned files fetched on request | any number |
| `logprob` | the first-token distribution of any chat model a server reports log-probabilities for, read over the option letters | a running llama-server (or vLLM, OpenAI) | at most 26 |
| `embed` | sentence embeddings, then cosine ranking or a head trained from a few dozen cases | `poolhouse[decide]` and an embedding server | any number (`pairwise` head), a fixed set (`classes` head) |
| `rules` | patterns, words and allowed values; `ScopeDecider` checks paths and hosts | nothing | any number |

`router.decide(..., config=Config(backend="auto"))` takes the first of `pointer`, `logprob`,
`embed`, `rules` that is available and takes the option count. `Layered(rules, learned)`
lets a matching hard rule overrule a learned score.

## What was measured

2026-10-02, one Apple M4 Max with 128 GB. The cases are the built-in guard set at this
commit (412 hand-written tool calls, three questions). The set is split by whole groups
(a call and its reworded requests are one group) into a dev half and a test half, seed 0;
every temperature below is fitted on dev and every figure is scored on the 207 test cases
(96 destructive, 51 grounded, 60 scope). Brier is the sum over options of the squared
error (0 to 2, lower is better); ECE uses ten bins. "cal" is after the fitted temperature.
Latency is the median over the test cases with the model warm, one request at a time.
Raw rows: `poolhouse-decide bench guards --split test --tag QUESTION --backend NAME`; the
temperature fit is `poolhouse-decide calibrate --split dev`. The embed heads are trained on
a 70% slice of dev and calibrated on the other 30%.

Note (2026-10-08): the embed backend now targets `embeddinggemma-2` (Apache-2.0); the embeddinggemma-300M rows below were measured with the 300M model and are historical.

| backend and model | question | accuracy | Brier raw / cal | ECE raw / cal | p50 ms |
|---|---|---|---|---|---|
| logprob, Qwen3-VL-8B-Instruct Q4_K_M | destructive | 0.969 | 0.041 / 0.063 | 0.028 / 0.032 | 141 |
| | grounded | 0.882 | 0.228 / 0.207 | 0.112 / 0.057 | 199 |
| | scope | 0.933 | 0.097 / 0.091 | 0.045 / 0.105 | 136 |
| logprob, Qwen3-4B-Instruct-2507 Q4_K_M | destructive | 0.917 | 0.156 / 0.122 | 0.076 / 0.032 | 82 |
| | grounded | 0.784 | 0.427 / 0.297 | 0.213 / 0.114 | 84 |
| | scope | 0.833 | 0.341 / 0.271 | 0.174 / 0.107 | 83 |
| logprob, Qwen3.5-2B Q4_K_M | destructive | 0.698 | 0.352 / 0.336 | 0.184 / 0.161 | 88 |
| | grounded | 0.667 | 0.459 / 0.419 | 0.190 / 0.120 | 82 |
| | scope | 0.867 | 0.366 / 0.196 | 0.285 / 0.136 | 72 |
| logprob, granite-4.0-h-350m Q4_K_M | destructive | 0.333 | 1.199 / 0.666 | 0.606 / 0.043 | 56 |
| | grounded | 0.569 | 0.842 / 0.494 | 0.417 / 0.014 | 55 |
| | scope | 0.617 | 0.484 / 0.493 | 0.102 / 0.105 | 51 |
| pointer, strands-decider-2B-hobson-v19 (MPS, bf16) | destructive | 0.927 | 0.208 / 0.117 | 0.239 / 0.043 | 263 |
| | grounded | 0.824 | 0.302 / 0.272 | 0.136 / 0.059 | 451 |
| | scope | 0.933 | 0.243 / 0.162 | 0.245 / 0.119 | 207 |
| embed, embeddinggemma-300M + classes head | destructive | 0.844 | 0.262 / 0.259 | 0.075 / 0.108 | 9 |
| | grounded | 0.725 | 0.445 / 0.372 | 0.193 / 0.076 | 10 |
| | scope | 0.650 | 0.599 / 0.449 | 0.278 / 0.132 | 9 |
| embed, embeddinggemma-300M + pairwise head | destructive | 0.802 | 0.389 / 0.295 | 0.270 / 0.167 | 9 |
| | grounded | 0.745 | 0.387 / 0.358 | 0.124 / 0.065 | 10 |
| | scope | 0.433 | 0.552 / 0.531 | 0.171 / 0.150 | 10 |
| embed, embeddinggemma-300M, no head | destructive | 0.365 | 0.749 / 0.643 | 0.347 / 0.225 | 10 |
| rules, `ScopeDecider` | scope | 1.000 | 0 / 0 | 0 / 0 | under 1 |

Reading it:

- The first-token readout of the 8B instruct model beats the released pointer checkpoint on
  accuracy and latency on this set; the 4B is 1 to 10 points below it on accuracy and still
  faster. The pointer model needs no letter labels, so it handles more than 26 options, and
  once a temperature is fitted it is calibrated about as well as the 8B.
- The pointer checkpoint's latency here (200 to 450 ms median, 1.4 s at p95) is higher than
  the 153 ms its authors report for an M3, because the guard prompts run 300 to 600 tokens
  and 18 of the 24 layers are Gated DeltaNet, which transformers runs in a PyTorch reference
  loop on MPS. An 86-token prompt takes 117 ms. On CPU in float32 the same prompt takes 9 s.
- Raw confidences are badly calibrated for the small models; a temperature fitted on dev
  fixes ECE for most rows (granite 350M: 0.61 to 0.04) without making them accurate.
- The embed backend answers in about 10 ms. Trained on 144 cases it reaches 0.84 on the
  destructive question; it is the right tool when a decision repeats thousands of times and
  a few dozen labelled examples exist.
- `rules` scores 1.000 on the scope question because the cases were labelled with the same
  path and host definition; it shows the rule covers every case in the set, not that it
  generalises.
- A Qwen3.5-4B Q4_K_M server on this llama.cpp build returned non-finite logits (the
  `logprob` backend reports the garbled first token and stops); it is not in the table.
- Memory was not measured per process. The pointer model held 3.7 GB on the MPS device.

### Fine-tuning

`python -m poolhouse.train decider guards --init strands --steps 120 --lr 5e-5 --out DIR`, run before
the defaults below were the reference recipe's (so the head trained at five times the rate, 4 cases a
step), continued the released checkpoint on 248 guard cases (61 for calibration, 103 held-out test cases from
whole groups, all three questions pooled) in 364 s: accuracy 0.903 to 0.951, Brier 0.230 to
0.069, ECE 0.216 to 0.035. The cases are one author's templates, so the test cases resemble
the training cases more than a new project's calls would; this is a pipeline check with
a direction, not a quality claim. A fresh adapter on Qwen3.5-0.8B-Base (what `--smoke` now is) for 30 steps reached
0.398 (the three questions pooled; a floor), in 87 s. The model card each run writes says
which kind of run it was.

## Train your own

The released model is `StrandsAgents/strands-decider-2B-hobson-v19` from Strands Labs, the
open-source lab of the Strands Agents project at AWS (announcement:
<https://strandsagents.com/blog/introducing-strands-decider/>; code:
github.com/strands-labs/strands-decider). It is a rank-16 LoRA and a pointer head on
Qwen3.5-2B, scored on JevBench, and its training data, scripts and weights are released for
fine-tuning. Its three question kinds are `noul` (yes/no), `choice` and `score`, and it is
calibrated by a temperature per kind. `poolhouse-decide train` is this package's own
re-implementation of that recipe for a few hundred of your own cases; it does not run the
released scripts (the reference recipe is about 11 hours on one RTX 3090 for the full corpus).
Its defaults are the reference recipe's (next section); `--smoke` is the small pipeline check.

    poolhouse-decide train --data tickets.jsonl --name tickets --dry-run
    poolhouse-decide train --data tickets.jsonl --eval held-out.jsonl --name tickets \
        --baseline strands --epochs 3
    poolhouse-decide train --data tickets.jsonl --name try --smoke
    poolhouse-decide eval held-out.jsonl --decider tickets --decider strands

**Data.** One JSON object per line (the format `export-cases` writes):

    {"id": "t1", "question": "Which team?", "state": "payouts failing for 3 days",
     "options": {"billing": "payments", "tech": "outages"}, "label": "billing",
     "group": "customer-17", "kind": "choice"}

`question`, `state`, `options` (names, or names with descriptions) and `label` (one of the
option names) are required. `group` keeps related cases on one side of every split (cases
without one are grouped with the cases that say the same thing); `kind` is `noul`, `choice`
or `score` and defaults to `noul` for two options, `choice` otherwise. `--data guards` is the
built-in guard set.

**The defaults, and where each comes from.** Read from github.com/strands-labs/strands-decider
at commit `d942e5515db750f80a51032629ec312455a87976`, the reference recipe that trained the released checkpoint.
A value upstream does not state is marked as this package's.

| setting | default | source |
|---|---|---|
| base | `qwen3.5-2b-base` (Qwen/Qwen3.5-2B-Base) | `configs/train.yaml:28` |
| epochs | 1, so the steps follow the number of cases: `ceil(cases x epochs / 32)` (`--steps` sets them instead) | `configs/train.yaml:51` |
| cases per forward pass | 8 (`--batch-size`) | `configs/train.yaml:52` (`micro_batch_size`) |
| forward passes per optimizer step | 4 (`--grad-accum`), 32 cases a step | `configs/train.yaml:53` |
| learning rate, adapter | 1e-4 (`--lr`) | `configs/train.yaml:54` |
| learning rate, head | 10 times the adapter's for a new head (1e-3); the same as the adapter's when continuing the released head (`--init strands`, 1e-4) | `configs/train.yaml:55`; `skills/customize-decider/references/training.md` ("head_lr: 0.0001 # the head is already trained") |
| optimizer | AdamW, betas (0.9, 0.95), eps 1e-8; weight decay 0.01 on the head's matrices only, none on the adapter, norms or biases | `training/train.py:291-297`, `configs/train.yaml:56` |
| schedule | linear warmup over 3% of the steps, then cosine decay to zero | `configs/train.yaml:57`, `training/train.py:330-334` |
| gradient clip | 1.0 | `configs/train.yaml:58` |
| gradient checkpointing | on, non-reentrant (what lets it work with LoRA) | `configs/train.yaml:59`, `training/train.py:527-535` |
| LoRA | rank 16, alpha 32, dropout 0.05, on `q_proj k_proj v_proj o_proj gate_proj up_proj down_proj in_proj_qkv in_proj_z in_proj_a in_proj_b out_proj` (the last five are the recurrent layers', without which a LoRA reaches 6 of the 24 layers) | `configs/train.yaml:43-48`, `src/strands_decider/modeling.py:105` (dropout) |
| precision | bfloat16 | `src/strands_decider/modeling.py:79` |
| options shuffled every pass | yes | `configs/train.yaml:61` |
| longest prompt | `max_length: 4096`, the serving window saved with the checkpoint; this package sets no length (below) | `configs/train.yaml:39` |
| calibration and test share | 15% and 25% of the cases, by whole group | this package's: upstream calibrates on one half of a held-out file and scores the other half (`training/steps.md`, "4. Calibrate") and holds out 3% of its training rows for validation (`configs/train.yaml:26`) |

Upstream gives no value for training on a few hundred cases, and its own note on the learning rates
is that they "are starting points, not measured optima for fine-tuning" (`skills/customize-decider/references/training.md`).
One epoch of 250 cases is 8 steps of 32; raise `--epochs` for a small file. Upstream trains with three more things this
trainer does not have: a KL term to the frozen model's own answer (`kl_frozen_weight: 0.3`), the parent model's answer
distributions on the multi-step rows (`teacher_file`) and ordinal smoothing (`ordinal_smoothing: 0.1`,
`reverse_score_prob: 0.5`); a run here is cross-entropy on the gold label alone. `--smoke` is the
earlier small settings: the 0.8B base, 60 steps of 4 cases, the head at 5 times the rate, no checkpointing.

**Prompt length.** This package sets no maximum prompt length for a decision model. A prompt is bounded by two
things that are true of the machine in front of it. One is the base model's own window, read from the loaded model's
config at load time (`max_position_embeddings`, in `text_config` when the config has one; Qwen3.5-2B-Base reports
262144): a prompt over it is refused with the number and the config it came from, and a model whose config reports no
window is refused by the missing field, with no other number to fall back on. The other is the memory free on the
device when the prompt arrives (`poolhouse.decide.window`): a prompt whose working memory (attention scores and
per-layer activations, as an upper estimate) does not fit is refused with the memory it needs and the memory
available. Inference (`PointerDecider`, the resident decision host) and training read the same two bounds. The
broker's resident reservation is the weights and a base; a prompt's working memory is measured by the host against
the free memory when the prompt arrives, and the broker allows the host that much on top of its reservation. The
trainer's `--max-tokens` (`Settings.max_tokens`) is unset by default; set it to refuse training cases over that many
tokens, as a memory trade you choose.

What is known about length, as information and not a rule. The released checkpoint is saved with a serving window of
4096 (`max_length: 4096`, "the serving window saved with the checkpoint", `configs/train.yaml:39-40`) and its model
card scores JevBench "served at 4096"; the v19 checkpoint itself was trained at 3072 (`configs/experiments/v19.yaml:24`,
because no training row is longer). Upstream measured the training length: 1024 to 3072 moved its long-policy tasks
from 0.263 to 0.368 and changed nothing else; 3072 to 4096 changed nothing (`src/strands_decider/modeling.py:75-78`).
Quality on prompts longer than the length a checkpoint was trained at has not been measured here or upstream.

**Measured here** (Apple-silicon GPU, 128 GB unified memory; one training step of the default settings on the pinned
bases, a synthetic case of the stated length; memory is the process's peak footprint from `/usr/bin/time -l`, which
includes the allocator's cache and so is an upper bound):

| base | tokens per case | cases per forward | checkpointing | seconds per forward and backward | peak memory |
|---|---|---|---|---|---|
| 0.8B | 1,002 | 1 | off | 1.8 | 10.1 GiB |
| 0.8B | 3,998 | 1 | off | 9.5 | 34.0 GiB |
| 0.8B | 1,002 | 1 | on | 2.5 | 3.3 GiB |
| 0.8B | 3,998 | 1 | on | 12 to 20 | 7.4 GiB |
| 0.8B | 1,002 | 4 | off | 7.2 | 30.4 GiB |
| 0.8B | 3,998 | 4 | off | not finished: stopped after 12 minutes | 125.9 GiB reached, of 128 GiB |
| 2B | 302 | 8 | on | 7.4 | 7.2 GiB |
| 2B | 1,002 | 1 | on | 3.0 | 6.2 GiB |
| 2B | 3,998 | 1 | on | 13.8 | 9.3 GiB |
| 2B | 1,002 | 8 | on | 24.6 | 11.2 GiB |
| 2B | 3,998 | 8 | on | 110 (one of three passes 181) | 41.1 GiB |

A batch is padded to its longest case, so a long case costs nothing for short ones and only matters when a case is
that long. At 4,000 tokens the 2B base at the default 8 cases a forward
pass takes 41 GiB and 110 s a forward pass (4 of them an optimizer step) when every case is that long; with checkpointing
off the 0.8B base at 4 cases of 4,000 tokens does not fit this machine, which is why checkpointing is on by default.
`--batch-size 1 --grad-accum 32` is the same optimizer step in 9.3 GiB for the 2B base at 4,000 tokens.
The whole default run on the 412 built-in guard cases (`--data guards`, 248 of them for training): 8 steps of 32
cases, 195 s from start to written decider including loading, scoring the baseline and 164 calibration and test cases,
peak footprint 7.0 GiB; test accuracy 0.544 against 0.398 for the majority label (a pipeline check on one author's
templates, not a quality claim). A 2-step run of the same took 82 s.

**What `train` does, in order**
1. Checks the file: every line parses and its label is one of its options; fewer than 40 cases,
   a single label, or the same case under two labels is an error; fewer than 500 cases, one
   label over 90%, labels under 5 cases, repeated cases and repeated ids are warnings
   (`--dry-run` prints these and the split sizes, and stops).
2. Refuses a bad `--name` (lower-case letters, digits, `.`, `_`, `-`), a name already
   registered (see `--replace`), an `--out` that has files in it, and an `--out` inside a git
   work tree (`--allow-in-repo`): trained weights and data are never committed. The default
   `--out` is under the state directory.
3. Splits by whole group into train, calibration (15%) and test (25%), the same way for the
   same `--seed`. With `--eval FILE` the test cases are that file instead, and the run stops if
   any of its cases (same question, state and options, ignoring case and spacing) or groups
   also appear in `--data`.
4. Holds the GPU: a claim named `gpu-training` at the Broker (visible in `poolhouse-serve
   status`). A model server held by another process, or another training run, refuses the
   hold; `--wait SECONDS` waits for the claim. A `--device cpu` run holds nothing.
5. Scores the baseline on the test cases: `auto` is the released checkpoint when `--init
   strands` and otherwise the training-set majority label; also `strands`, `majority`, `none`
   or the name of a registered decider.
6. Trains the LoRA and the head (options shuffled every pass), then fits a temperature on the
   calibration cases: one over all of them and one per kind that has 20 or more. A fit on 50 or
   more cases minimises ECE (as the upstream recipe does, because an NLL fit can leave a binary
   head badly calibrated); a smaller one minimises NLL. A trained decider applies the
   temperature of the kind its question had in training.
7. Writes `decider.json` (hashes, temperatures), `manifest.json`, `model_card.md` and the
   safetensors files, scores the test cases (accuracy, Brier, ECE, accuracy and rate of
   abstaining at confidence 0.8, `--floor` to change it) and puts the baseline's numbers beside
   them in the card.
8. Refuses to register a decider whose accuracy is below, or Brier above, the baseline's on the
   test cases (the directory is still written, `manifest.json` says `registered: false`);
   `--allow-worse` registers it anyway. Otherwise it registers the name and pins the weights
   and config in the sentinel (source `trained:NAME`), so a changed file is a finding.

`--replace` re-registers a name that is taken; the old entry and its directory stay, under
`NAME.prev`. `poolhouse-decide eval FILE --decider NAME` scores a registered decider (or
`strands`, or a directory) on any labelled file with the same metrics, repeatable to compare.
The same metrics are in `poolhouse.decide.metrics` for other runners.

**Untrusted data.** Labels, ids, groups and text are only ever data: nothing from the file
becomes a path (the name and `--out` come from the command line), a command or a pickle
(weights are safetensors; a directory with a `.bin`, `.pt` or `.pkl` file is not loaded), and
the card prints only a reduced character set. Never commit the file you trained on.

**Honest limits**
- Time and memory of one step are measured above; a whole run is not. The runs measured
  earlier are in "Fine-tuning": 30 steps on Qwen3.5-0.8B-Base in 87 s, and 120 steps continuing
  the released 2B checkpoint in 364 s, both on one Apple-silicon GPU. Nothing here checks that
  a run fits next to a served model, and a held server refuses the run.
- A few hundred cases check the pipeline; they do not support a quality claim, and the card
  says so. A model trained on your cases is calibrated on cases like them and on nothing else.
- The baseline check compares point estimates on the test cases; with 60 test cases a gap of
  a few points is noise.
- It trains the head and one rank-16 LoRA; it does not resume, shard or train on several
  GPUs, and a case over the base model's window or the memory free is refused (prompt length, above).
- A temperature cannot change which option wins, only how sure the decider says it is.
- The real-model path has been run here only on a random 2-layer model on CPU (the tests); the
  same code on Qwen3.5 was last run by the earlier `python -m poolhouse.train decider` measurements above.

## Licences

| part | licence | how it is used |
|---|---|---|
| strands-decider code, github.com/strands-labs/strands-decider | Apache-2.0 (LICENSE; GitHub reports the same) | read for the architecture; none of it is copied |
| checkpoint `StrandsAgents/strands-decider-2B-hobson-v19` | Apache-2.0 (card metadata and LICENSE.md) | downloaded on request, pinned by commit, size and SHA-256; not redistributed |
| base `Qwen/Qwen3.5-2B-Base` | Apache-2.0 (card and LICENSE) | same; the release is a LoRA on this model (the post's base is Qwen3.5-2B, not Qwen2.5) |
| base `Qwen/Qwen3.5-0.8B-Base` | Apache-2.0 | the base of `--smoke` runs of `python -m poolhouse.train decider` |
| training data of the checkpoint | 29 public Hub datasets plus two author releases and teacher outputs from Qwen3.5-4B, each under its own licence; its `data/sources.md` lists them | not read or redistributed here; read that file before using the checkpoint where the data's terms matter |
| this package | Apache-2.0 | a re-implementation of the described architecture and prompt layout; see `NOTICE` |

## A guard for an embedding application

    from poolhouse.decide import router
    from poolhouse.decide.guard import Policy, ToolCallGuard
    from poolhouse.interventions import Call, Context, guard_tool_call

    guard = ToolCallGuard(router.build("logprob", router.shared("logprob", "http://127.0.0.1:8080")),
                          project_dir="/work/app", allowed_hosts=["api.internal.test"],
                          policy=Policy(abstain_below=0.8, scope="deny"))
    ctx = Context(task=user_request, messages=conversation)
    result = await guard_tool_call(Call(name, arguments), ctx, run_tool, [guard], confirm=ask_person)

A destructive answer or an answer under `abstain_below` becomes a Confirm, a call that follows
instructions found in tool output becomes a Deny, a path or host outside the project becomes
a Confirm or (`scope="deny"`) a Deny, and a decider that fails asks a person. On the whole guard set an early, uncalibrated run of the Qwen3-4B-Instruct logprob decider
labelled 43% of the injected calls as injected: put `Layered` hard rules and a person in front
of anything irreversible.

## Limits

- A question is read less than a document: with the state and options fixed, rewording the
  question often gives the same answer. Phrase it so the obvious reading is the one meant.
- Long multi-step documents are the weak spot of the pointer model; quality beyond
  the length a checkpoint was trained at (3072 for the released one) is unmeasured; the prompt length is bounded by the
  model's window and the memory free, not by a limit of ours.
- Calibration is fitted on cases like the ones you will see. A temperature fitted on the
  guard set says nothing about another task.
- The state text is untrusted input to the decider. A tool result that tells the decider how
  to answer can move its answer; the guard's `grounded` check is itself a model.
- The guard set is 412 cases from one author. Labels were checked by hand, and the scope
  labels agree with the deterministic path and host check on every case.
- `make-cases` labels cases by rule from tool names and descriptions; read a sample.
- Only PyTorch is wired for training. The released checkpoint runs on CUDA, MPS and CPU
  through transformers; there is no MLX path and no prefix cache across questions about one
  state.

## JevBench (opt-in)

`poolhouse-decide jevbench --fetch` downloads the 231 public items of
[JevBench](https://github.com/fstandhartinger/jevbench) (MIT, Benchmark Heaven; not affiliated
with TypeSafe AI) into the cache through the net pipeline, pinned by commit and SHA-256;
`poolhouse-decide jevbench --yes --backend rules --backend pointer --backend logprob --gguf M.gguf`
scores the backends per question type and tier (accuracy with an unanswered item counted wrong,
Brier and ECE over the answered ones). The held-out half and the sealed items are not public, and
this is not the official JevBench Score (which also weighs cost, speed and a sealed set). It uses
the GPU, so it is in no test tier. Measured 2026-10-03, one M-series Mac, llama.cpp b11380,
torch 2.14 on MPS: pointer (Strands v19) 170/231 = 0.736 (easy 1.000, original 0.917, hard 0.505;
Brier 0.339, ECE 0.073); logprob Qwen3.8-27B Q4_K_M 0.874 (hard 0.748; Brier 0.166, ECE 0.031);
first-option rules floor 0.303.
