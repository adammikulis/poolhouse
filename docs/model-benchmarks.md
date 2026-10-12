# Model lineups and published benchmarks

Retrieved 2026-10-08 (sections 1.3 and 2.7: 2026-10-12). Every figure carries its source URL, the source's own date and the settings
the source states. Nothing here was measured by this repository. Where the vendor's figure and a
third party's differ, both are shown. `unverified` means the page could not be fetched or only a
secondary write-up was available. The repository's own measurements (JevBench, the decider sets,
`docs/bench.md`) override every number in this file.

Sources fetched directly: Anthropic platform docs and announcements, OpenAI developer docs, Vals AI
model pages. `openai.com/index/...` returned HTTP 403 to the fetch tool, so OpenAI launch-page figures
come from secondary coverage and are marked.

## 1. Lineups and exact API ids

### 1.1 Anthropic (Claude 5 family)

Source: https://platform.claude.com/docs/en/models/overview (retrieved 2026-10-08), model pages
`.../models/{haiku,sonnet,opus,fable}-5-5|5-1/overview`. Every Claude id is a pinned snapshot; the
dateless id is its own alias.

| Tier | Name | Claude API id | Bedrock id | Released | $/MTok in / out | Context | Max out | Default effort |
|---|---|---|---|---|---|---|---|---|
| 1 (lowest) | Claude Haiku 5.5 | `claude-haiku-5-5` | `anthropic.claude-haiku-5-5` | 2026-10-07 | 0.10 / 0.50 (prompt up to 100k); 0.50 / 2.50 above | 1M | 128K | medium |
| 2 | Claude Sonnet 5.5 | `claude-sonnet-5-5` | `anthropic.claude-sonnet-5-5` | 2026-09-28 | 2 / 10 | 1M | 128K | high |
| 3 | Claude Opus 5.5 | `claude-opus-5-5` | `anthropic.claude-opus-5-5` | 2026-09-22 | 4 / 20 | 1M | 128K | medium |
| 4 | Claude Fable 5.1 | `claude-fable-5-1` | `anthropic.claude-fable-5-1` | not on fetched page | 10 / 50 | 1M | 128K | high |

- Batch API is 50% off. Cache reads cost 10% of input (5% on Opus 5.5 and Sonnet 5.5, 2.5% on Fable 5.1).
  Opus 5.5 fast mode: $8 / $40 (https://www.anthropic.com/news/claude-opus-5-5, 2026-09-22).
- Claude Mythos 5.1 is named on the overview page. Its id was not fetched; it is not in the draft tier table.
- Legacy and still available: Fable 5, Opus 5, Opus 4.8/4.7/4.6/4.5, Sonnet 5, Sonnet 4.6, Haiku 4.5.
  `claude-haiku-4-5-20251001` is the Haiku 4.5 id (third-party, https://www.gradually.ai/en/claude-models/,
  unverified against the platform page).
- Anthropic: Haiku 5.5 is "for high-volume, latency-sensitive tasks such as classification, extraction,
  and routing" and "pairs well with Opus 5.5 and Sonnet 5.5 as a subagent on coding work", while "Sonnet 5.5
  and Opus 5.5 remain better choices for complex agentic coding tasks" (https://www.anthropic.com/claude-haiku-5-5,
  2026-10-07).
- Haiku 5.5 uses the newer tokenizer: about 30% more tokens for the same text than Haiku 4.5.

### 1.2 OpenAI (GPT-6 family)

Source: https://developers.openai.com/api/docs/models (retrieved 2026-10-08; page undated).

| Tier | Name | API id | $/MTok in / out | Context | Max out |
|---|---|---|---|---|---|
| 1 (lowest) | GPT-6 Luna | `gpt-6-luna` | 0.10 / 0.50 | 1.05M | 128K |
| 2 | GPT-6.1 Sol | `gpt-6.1-sol` | 2 / 10 | 1.05M | 128K |
| 2 (earlier) | GPT-6 Sol | `gpt-6-sol` | 2 / 10 (secondary, iClarified, 2026-09-22) | 1.05M | 128K |
| 3 | GPT-6 Astra | `gpt-6-astra` | 10 / 50 | 1.05M | 128K |

- Page descriptions: Astra "our most capable model for the most demanding work"; Sol balances
  intelligence and cost; Luna "our most efficient model for focused, high-volume tasks".
- GPT-6 Sol and Luna launched 2026-09-22 (https://www.iclarified.com/102351/openai-cuts-api-prices-50-with-gpt-6-sol-and-luna,
  secondary; the primary page https://openai.com/index/introducing-gpt-6-sol-and-luna/ was not fetchable).
  GPT-6.1 Sol launched 2026-09-29 (https://www.vals.ai/models/openai_gpt-6.1-sol, release date field).
  Earlier family: GPT-5.6 Sol, Terra, Luna (ids not verified).
- Codex-facing names (https://learn.chatgpt.com/docs/models, redirect of developers.openai.com/codex/models,
  retrieved 2026-10-08): the same ids. `gpt-6.1-sol` is recommended for complex coding and agentic work and is
  the example `model` in `config.toml`; Luna for "focused, repeatable tasks" (extraction, classification,
  transformation, structured summaries); Astra for the hardest end-to-end work. The page does not say which
  model Codex subagents use.

### 1.3 Local (Qwen3.8)

Names as they appear in this repository's docs, not looked up externally: `Qwen3.8-27B`
(`Qwen3.8-27B-UD-Q4_K_XL.gguf` is the owner's default 27B quant), `Qwen3.8-Flash-Next`, and the decider
base `Qwen3.5-2B`. Vendor-reported and aggregator figures for the 27B and Flash-Next were collected on
2026-10-12 (section 2.7); none was collected for the 2B.

**The tier table's `qwen` family (owner's decision, 2026-10-12).** `src/poolhouse/workspace/model_tiers.json`
lists every quantisation of Qwen3.8-27B and Qwen3.8-Flash-Next, with no per-quantisation exclusion, above the
lowest tier of its family, so a local session on either counts as landing-level (docs/landing-queue.md,
"Eligibility, review and brakes"). The Qwen3.5 0.8B, 2B and 4B models are the family's lowest tier, which never
lands. The owner's view is that the 27B is at Claude Opus 4.6's level and Flash-Next comparable or higher.
Qwen's own evaluation table, as three pages relay it, prints Flash-Next above the 27B on every row where
both appear (section 2.7): a vendor claim, not reproduced here. Section 2.7's figures are for full-precision weights, vendor-reported and unreproduced, and no published
benchmark covers the low-bit quantisations, so the tier of every quantisation is the owner's decision and not
a measurement; this file does not claim a quantisation matches full precision. A local agent registers the
id `model_identity` gives (the quantisation removed, for example `qwen3.8-27b`), so the table cannot tell the
quantisations of one model apart for an agent started by `agent start`; a named file or MLX id is matched by
its model name and quantisation suffix. A draft head, a fine-tune or another Qwen is not listed.

## 2. Benchmarks

Scores from different harnesses and effort levels are not comparable to each other.

### 2.1 Agentic coding

Terminal-Bench 4.0.

| Model id | Vendor figure | Third-party figure |
|---|---|---|
| `claude-sonnet-5-5` | 70.6%, effort not stated (https://www.anthropic.com/claude-sonnet-5-5, 2026-09-28) | Vals 64.14% +/-1.01, rank 2/45; update post 53.03% (https://www.vals.ai/models/anthropic_claude-sonnet-5-5, undated, effort max) |
| `claude-opus-5-5` | 66.4%, xhigh, +/-2.6 (https://www.anthropic.com/news/claude-opus-5-5, 2026-09-22) | Vals 65.15%, rank 1/45; update post 61.62%, or 53.54% counting fallback-assisted tasks as failures (https://www.vals.ai/models/anthropic_claude-opus-5-5) |
| `claude-fable-5-1` | 55.8% (Opus 5.5 page) | not collected |
| `claude-haiku-5-5` | 39.2%, effort not stated (https://www.anthropic.com/claude-haiku-5-5, 2026-10-07) | Vals 35.35% +/-2.20, rank 12/45 (https://www.vals.ai/models/anthropic_claude-haiku-5-5) |
| `gpt-6-astra` | 57.9%, high, "as reported by OpenAI", quoted on the Opus 5.5 page | not collected |
| `gpt-6.1-sol` | not collected from OpenAI | Vals 55.05% +/-1.82, rank 6/45 (https://www.vals.ai/models/openai_gpt-6.1-sol) |
| `gpt-6-luna` | 16.4% (Haiku 5.5 page, 2026-10-07) | Vals 13.64% +/-1.51, rank 29/45; update post 9.60% (https://www.vals.ai/models/openai_gpt-6-luna) |

Vals pages state that table and update-post figures differ without explanation, and that fallback
models completed some tasks. Vals pages retrieved 2026-10-08 and are undated.

FrontierCode 1.1 (Main), vendor tables:

| Model | Score | Source |
|---|---|---|
| Opus 5.5 | 54.4% (text: 54.6% at medium) | Opus 5.5 page, 2026-09-22 |
| Sonnet 5.5 | 52.1% xhigh; 46.2% at max, penalised for out-of-scope edits | Sonnet 5.5 page, 2026-09-28 |
| Haiku 5.5 | 46.4% | Haiku 5.5 page, 2026-10-07 |
| Fable 5.1 | 50.3% | Opus 5.5 page |
| GPT-6 Astra | 53.3% | Opus 5.5 page |
| GPT-6 Sol | 49.3% | Sonnet 5.5 page |
| GPT-6 Luna | 42.4% | Haiku 5.5 page |

- CursorBench 4.0: Opus 5.5 57.8%, Sonnet 5.5 55.5%, Fable 5.1 51.8% (Opus 5.5 and Sonnet 5.5 pages).
- DeepSWE v1.1, OpenAI-reported, secondary (https://www.iclarified.com/102351/openai-cuts-api-prices-50-with-gpt-6-sol-and-luna,
  2026-09-22): GPT-6 Sol (max) 68.8%, GPT-6 Luna (max) 66.6%. GPT-6.1 Sol: secondary coverage cites 75.2% at high
  and BenchLM lists 71.9% at max; unverified.
- Vibe Code Bench v1.1 (Vals): Sonnet 5.5 92.39%, Haiku 5.5 90.44%, Opus 5.5 90.29%, GPT-6.1 Sol 88.93%, GPT-6 Luna 81.65%.
- SWE-bench Verified and SWE-bench Pro: no score for these models appeared on the vendor pages fetched. The
  Sonnet 5.5 system card lists SWE-Bench Pro; its scores were not read. unverified.

### 2.2 Reasoning, computer use, knowledge work (vendor tables)

| Benchmark | Haiku 5.5 | Sonnet 5.5 | Opus 5.5 | Fable 5.1 | Luna | Others |
|---|---|---|---|---|---|---|
| Humanity's Last Exam, with tools | 57.4% | 64.5% | 67.7% | 65.6% | n/a | Astra 57.2% |
| Humanity's Last Exam, no tools | 45.9% | 56.9% | n/a | n/a | n/a | n/a |
| OSWorld 2.1 (partial or offline subset) | 72.4% | 83.9% (80.1% on Sonnet page) | 81.8% | 80.7% | 48.9% | n/a |
| GDPval-AA v2.1 (Elo) | 1620 | 1840, 1844 on the Sonnet page | 1846 | 1735 | 1437 | Astra 1542, Sol 1487 |
| AA-Briefcase v1.1 (Elo) | 1578 | 1824, 1811 on the Sonnet page | 1822 | n/a | 1336 | Sol 1483 |
| Terminal-Bench-Science 0.1 | n/a | n/a | 58.7% | 52.6% | n/a | Astra 64.6% |

Sources: Haiku 5.5 page (2026-10-07), Sonnet 5.5 page (2026-09-28), Opus 5.5 page (2026-09-22). Pages disagree
with each other on the same cell; the Haiku page's reference column is shown first, the Sonnet page's value in
brackets. Artificial Analysis ran GDPval-AA and AA-Briefcase on a pre-release deployment with a
structured-output bug since fixed, and OpenAI fixed a GPT-6 Sol image-understanding bug after those runs
(Sonnet 5.5 page footnotes).

### 2.3 Tool use and long-horizon agents

- AutomationBench (Zapier-run): Opus 5.5 40.0%, Astra 41.4%, Fable 5.1 31.4% (Opus 5.5 page, 2026-09-22);
  GPT-6 Sol (xhigh) 33.2% at $0.27 per task (secondary, iClarified). Agents' Last Exam: GPT-6 Sol (max) 56.4%
  (secondary; unverified).
- Vals SRE Bench: Opus 5.5 33.59%, Sonnet 5.5 30.15%, GPT-6.1 Sol 50.76% (rank 2/32), GPT-6 Luna 2.67%. Opus 5.5's
  score drops to 5.34% when fallback-assisted tasks (82.82% of them) count as failures.
- Vals Index and cost per test, effort max: Sonnet 5.5 67.04% at $21.34; Opus 5.5 66.97% at $32.14; GPT-6.1 Sol
  61.15% at $3.237; Haiku 5.5 54.31% at $2.991; GPT-6 Luna 51.22% at $0.431. Update posts give different values
  (Opus 5.5 69.69%, Sonnet 5.5 69.22%, Luna 58.45%).

### 2.4 Long context, cost, latency

Context and output limits are in section 1 (1M or 1.05M context, 128K output). No long-context retrieval
benchmark was found on the pages fetched; unverified. Latency for the full Vals Index run at max effort (not per
call): Sonnet 5.5 78 min, Opus 5.5 79 min, GPT-6.1 Sol 43 min, Haiku 5.5 47 min, GPT-6 Luna 31 min. Anthropic's
comparative latency labels: Haiku 5.5 "Fastest", Sonnet 5.5 "Fast", Opus 5.5 "Moderate", Fable 5.1 "Slower"
(models overview). Anthropic claims Opus 5.5 costs about 40% less than Opus 5 on typical workloads and
generates output over 30% faster (Opus 5.5 page).

### 2.5 OSWorld 2.1 offline subset: accuracy against cost per attempt (owner's chart, 2026-10-08)

Source: a chart the owner supplied (partial-credit score against cost per attempt in USD, log scale).
The numbers below were read off the plot and are approximate, to about two points of accuracy and
the nearest step of cost. It is a computer-use benchmark, not this repository's work.

| model | effort | accuracy | cost per attempt |
| --- | --- | --- | --- |
| Haiku 5.5 | low | 42% | $0.07 |
| Haiku 5.5 | medium | 53% | $0.12 |
| Haiku 5.5 | high | 61% | $0.18 |
| Haiku 5.5 | xhigh | 68% | $0.28 |
| Haiku 5.5 | max | 72% | $0.60 |
| Sonnet 5.5 | low | 58% | $0.68 |
| Sonnet 5.5 | medium | 66% | $0.92 |
| Sonnet 5.5 | high | 73% | $1.35 |
| Sonnet 5.5 | xhigh | 81% | $2.20 |
| Sonnet 5.5 | max | 84% | $5.50 |
| Haiku 4.5 | default | 16% | $1.40 |

Haiku 5.5 is cheaper than Sonnet 5.5 at every accuracy up to about 72%: xhigh beats Sonnet low and
medium, and max matches Sonnet high at about 45% of its cost. Above 73% only Sonnet reaches.
`CLAUDE.md`, "Subagent model order", sets the delegation rule from this.

### 2.6 Terminal-Bench 4.0: accuracy against cost per attempt (owner's chart, 2026-10-10)

Source: a chart the owner supplied (score in percent against cost per attempt in USD, log scale). The
numbers were read off the plot and are approximate, to about two points and the nearest step of cost. It
is a terminal-agent benchmark, not this repository's work.

| model | effort | score | cost per attempt |
| --- | --- | --- | --- |
| Sonnet 5.5 | low | 20% | $0.7 |
| Sonnet 5.5 | medium | 29% | $0.8 |
| Sonnet 5.5 | high | 43% | $1.9 |
| Sonnet 5.5 | xhigh | 61% | $5.3 |
| Sonnet 5.5 | max | 70% | $13 |
| Opus 5.5 | five steps, lowest to highest | 39%, 47%, 58%, 64%, 65% | $1.3, $1.9, $3, $3.9, $5 |
| Opus 5.5 | two highest steps | 66%, 65% | $7.5, $11.5 |

Reading it: Opus reaches about 64% at $3.9, which is cheaper than Sonnet xhigh at 61% for $5.3, and above
about $4 Opus flattens near 65% and falls at its top effort. Sonnet at max (70% for $13) is the highest
point on the chart and beats Opus at its top effort, but it costs about 2.5 times Sonnet xhigh for about nine
more points. `CLAUDE.md`, "Subagent effort", sets the delegation rule from this: avoid max.

### 2.7 Qwen3.8-27B and Qwen3.8-Flash-Next (vendor-reported and aggregator figures, fetched 2026-10-12)

Every figure here is vendor-reported or from a third-party aggregator, for full-precision weights, and none
was reproduced by this repository or, as far as the pages say, by anyone else. No published benchmark
covers a low-bit quantisation of either model (IQ1_M, IQ3_S and the like); section 1.3 says what that
means for the tier table.

Qwen3.8-27B against Claude Opus 4.6 Max, Alibaba's model-card figures as two aggregator pages quote them:

| Benchmark | Qwen3.8-27B | Opus 4.6 Max | Note |
| --- | --- | --- | --- |
| Benchmarks won | 16 of 24 | | regolo.ai only; orcarouter.ai says "the majority" with no count |
| SWE-bench Pro | 61.7 | 53.4 | Opus's published score, not re-run in the same harness; the page calls the two "not perfectly comparable"; orcarouter.ai gives no Opus figure |
| Terminal-Bench 2.1 | 73.0 | 78.2 | both pages; Opus ahead |
| NL2Repo-Bench | 42.3 | 47.6 | both pages; Opus ahead |
| GPQA Diamond | 89.2 | 91.3 | both pages; Opus ahead |
| Humanity's Last Exam | 30.8 | 40.0 | both pages; Opus ahead |

Sources: `https://regolo.ai/qwen3-8-27b-benchmarks-every-test-where-alibabas-27b-model-beats-claude-opus-4-6-max/`
(dated 2026-08-21; "vendor-reported by Alibaba as of the August 14, 2026 release"; "had not been independently
reproduced at launch"; third-party evaluations "still pending as of August 19, 2026") and
`https://www.orcarouter.ai/blog/qwen-3-8-27b-benchmarks` (published 2026-08-17, changed 2026-08-25; "vendor-reported
and unreproduced"). Both name the Qwen3.8-27B model card as the origin. No independent reproduction was found.

Qwen3.8-Flash-Next is a sparse mixture-of-experts model. Qwen's technical report (section below) gives 125B
parameters with 6B activated per token and an additional 51B of n-gram embedding tables held in host memory;
two aggregators repeat 125B and 6B, and one counts "roughly 180B" stored once the n-gram table and the
multi-token-prediction head are included, which is the same 125B plus the 51B the report calls additional. A
reading of the model's config that gives a smaller stored size and fewer active parameters was reported to the
author and could not be fetched, so no figure from it is used here.

- `https://benchlm.ai/md/models/qwen3-8-flash-next.md`, last updated 2026-10-10 by the page, fetched 2026-10-12:
  overall 64.23 of 100, rank 37 of 889, scored on 37 of 625 benchmarks (the page says the coverage is partial);
  SWE-bench Pro 62.5, OSWorld 2.0 19.4, Toolathlon-Verified 73.5. The page also lists Qwen3.8-27B at an overall
  58.27, from a different subset of benchmarks, with no per-benchmark comparison; this file does not read that as a
  like-for-like result. An earlier read of the same page (last updated 2026-09-10) gave 56.84 of 100, rank 77 of 483,
  38 of 435 benchmarks: the page moves between visits.
- `https://www.mindstudio.ai/blog/qwen-3-8-flash-next-benchmarks`, dated 2026-08-29: 56 of 80 on a suite of eight
  tasks scored out of 10, behind GLM 5.3 Flash at 63 of 80, mostly on 3D rendering and front-end tasks; the two tied
  on math and agentic tasks. It makes no comparison with the 27B.

Flash-Next against Qwen3.8-27B, Qwen's own evaluation table as three pages relay it (fetched 2026-10-12;
vendor-reported, relayed by secondary sources, full-precision weights, no independent reproduction found):

| Benchmark | Flash-Next | 27B | Pages that print the row |
| --- | --- | --- | --- |
| SWE-bench Pro | 62.5 | 61.7 | acecloud.ai, respan.ai |
| DeepSWE 1.1 | 58.7 | 42.2 | acecloud.ai, respan.ai, datacamp.com (in its text) |
| AndroidWorld | 84.5 | 81.9 | acecloud.ai, respan.ai |
| SWE-bench Multilingual | 81.0 | 73.8 | acecloud.ai only; datacamp.com gives only 81.0 |
| NL2Repo-Bench | 48.1 | 42.3 | acecloud.ai only |
| RecreationBench, Vision2Web, CoWorkBench, JobBench | 49.9, 64, 73.9, 55.7 | 47.1, 62.9, 70.7, 33.4 | acecloud.ai only |
| Toolathlon Verified | 73.5 | 67.1 | respan.ai only; datacamp.com gives only 73.5 |
| GPQA Diamond | 91.7 | 89.2 | respan.ai only; datacamp.com gives only 91.7 |
| HLE | 35.9 | 30.8 | respan.ai only; datacamp.com gives only 35.9 |
| IFBench | 81.3 | 79.5 | respan.ai only |
| RealWorldQA | 88.5 | 85.9 | respan.ai only |

The pages agree on every figure more than one of them prints; they differ in which rows they print, so a row
marked "only" rests on one page. `https://acecloud.ai/blog/qwen3-8-27b-vs-flash-next/` (last updated 2026-10-06,
"Qwen's published evaluations") prints nine rows and Flash-Next is higher on all nine.
`https://www.respan.ai/articles/qwen-3.8-models-comparison` (2026-09-24, attributed to the Flash-Next model card)
prints eight and says Flash-Next is higher on every row of Qwen's shared table.
`https://www.datacamp.com/blog/qwen3-8-flash-next` (2026-08-27, "Qwen's own release table", an image whose
27B column could not be read) says Flash-Next beats its 27B sibling on every published benchmark; only the
DeepSWE 1.1 pair is in its text. The 27B's GPQA Diamond and HLE here match its figures in the Opus table above.

The technical report `https://arxiv.org/pdf/2608.30320` (Qwen Team, "On the Design of Qwen3.8-Next Architecture:
Evaluation, Efficiency, and Training Stability", 2026-08-31) compares Flash-Next's base model with the 397B-A17B
predecessor on fourteen pre-training benchmarks: ahead on eight and behind on the rest by at most 2.6 points
(abstract and introduction; the first three pages were read, not the evaluation tables). Those pages show no
comparison with a 27B base. datacamp.com says the base model "wins 8 of 14" against the 27B and Qwen3.7-Plus
bases, which matches the report's eight of fourteen against the predecessor and not a 27B comparison. A claim
that Flash-Next-Base beats 27B-Base on all fourteen is not confirmed and is not recorded here.

This repository has no 27B-versus-Flash-Next run of six or more scored questions: `docs/report-2026-09-23.md`
lists two short 27B runs, left out for fewer than six scored questions.

Not claimed: that Flash-Next is above the 27B in a run of this repository or in any independent run (Qwen's
table says it is, on every row it prints, and that is a vendor claim), that either model matches Claude Opus
4.6 in this repository's work, or that a quantisation matches full precision.

## 3. What to launch when

Cheapest adequate tier, using the repo order: Haiku 5.5 for read-only lookup and for ratchet-lowering with a named
metric and test selector, Sonnet 5.5 for anything else that writes or reviews code, Opus 5.5 only after Sonnet
fails, Fable only when the owner asks.

| Work | Launch | Evidence |
|---|---|---|
| Read-only search, lookup, summarising, compaction, classification | `claude-haiku-5-5` | Vendor positioning; $0.10 / $0.50 |
| Small, well-specified edit (one function, one doc) | Haiku 5.5; escalate after the first failed test run | Vibe Code Bench 90.44% |
| Routine implementation with tests in a claimed worktree | `claude-sonnet-5-5` | Terminal-Bench 4.0 39.2% (Haiku) vs 70.6% (Sonnet); FrontierCode 46.4% vs 52.1%; Anthropic names Sonnet/Opus the better choice for complex agentic coding |
| Architecture, security diagnosis, hard debugging | Sonnet 5.5 first; Opus 5.5 after it fails | Opus leads HLE (67.7 vs 64.5) and FrontierCode (54.4 vs 52.1) by small margins at twice the price |
| Independent review | A different model from the author; Sonnet 5.5 or higher | Same-model review shares blind spots |
| Coordination (main session) | Sonnet 5.5 or higher; never Haiku 5.5 or Luna | Lowest tier never coordinates |
| Codex-side work | `gpt-6.1-sol` for coding; `gpt-6-luna` for read-only and extraction | OpenAI Codex docs; Luna Terminal-Bench 4.0 13.64% to 16.4% |
| Hardest end-to-end work, on request | `claude-fable-5-1` or `gpt-6-astra` | Fable 5.1 scores below Opus 5.5 on every vendor-table benchmark collected, at 2.5 times the price |

Effort is a separate setting from the model. Anthropic reports Sonnet 5.5 at max effort scoring lower on
FrontierCode than at xhigh, so max is not a default.

### Where the evidence argues against a rule

- "Default to Haiku 5.5 for most subagent work" (former CLAUDE.md rule): replaced. Haiku 5.5 holds for lookup,
  summarising and narrow edits, and the in-repo run below shows it matching Sonnet 5.5 on ratchet-lowering with a
  named metric. For open-ended implementation the Terminal-Bench 4.0 gap (39.2% vs 70.6%, vendor) argues for
  Sonnet; a failed Haiku attempt costs a full rerun at Sonnet price.
- "Opus only after Sonnet fails": consistent. Sonnet 5.5 matches Opus 5.5 on Terminal-Bench 4.0 (70.6 vs 66.4
  vendor; 64.14 vs 65.15 Vals) at half the price.
- "Fable only when the owner asks": consistent.
- "Lowest tier never coordinates": nothing collected argues against it; Haiku 5.5 and Luna are the weakest
  agentic scores above.

## 4. Limits of public benchmarks

- Vendor numbers use vendor-chosen effort and harnesses; footnotes state that safeguards intervened and
  fallback models completed some tasks, lowering Opus 5.5's scores.
- Third-party pages disagree with their own update posts and with the vendor, and ranks move between visits.
- Terminal-Bench, FrontierCode and OSWorld measure public tasks. This repository's work is claims, hooks,
  graph code, gates and long-lived worktrees; no benchmark here measures obeying AGENTS.md or reporting honestly.
- Prices change; the vendor pages cited are the source, not this table.
- Repository measurements (JevBench, decider sets, `docs/bench.md`) override this file. Re-collect it when a
  tier ships or a vendor retires an id.

## 5. In-repo run: ratchet-lowering, Haiku 5.5 against Sonnet 5.5

Run 2026-10-08 by the lead session. One attempt per model per task, each in its own throwaway worktree cut
from `0.2dev` at `b9f28265`, identical brief per pair, no branch landed. Models `claude-haiku-5-5` and
`claude-sonnet-5-5`. Source of the targets: `scripts/budgets --show ruff-other` on that commit. Checks the
lead re-ran in each worktree (not the workers' reports): `ruff check <files> --output-format concise` and
`scripts/test all -n 1 <test files>`. A blind Sonnet reviewer (model labels shuffled, commit messages hidden)
scored the diffs and ran a differential test of old against new code.

| Task | Target | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|---|
| T1 | 11 `RUF059` in `tests/test_graph_cache.py`, `tests/test_graph_bench_standard.py` | 11 fixed, 30 tests pass, 10+/10-, 123 s, 59k tokens | 11 fixed, 30 tests pass, 10+/10-, 101 s, 55k tokens |
| T2 | 10 `RUF012`/`RUF007` in `tests/test_fleet_work.py` | 10 fixed, 34 tests pass, 19+/14- (reflowed lines), 145 s, 62k | 10 fixed, 34 tests pass, 12+/10-, 168 s, 58k |
| T4 | `PLR0915` (58 > 50 statements), `UP035`, `RUF046` in `src/poolhouse/bench/history.py` | all fixed, 16 tests pass, 71+/42-, largest function 26 statements, 157 s, 68k | all fixed, 16 tests pass, 51+/36-, largest function 32 statements, 104 s, 57k |

- Objective checks tie: every target finding fixed, no new finding, every test file passes, the same
  out-of-scope findings remain (3 in T1, 2 in T2, 1 in T4).
- T4 differential test, 316 inputs: 0 differences against the original for both models.
- Blind review, with the labels resolved afterwards: T1 prefers Haiku moderately (keeps the discarded names,
  `_calls`); T2 prefers Haiku strongly; T4 prefers Sonnet weakly (smaller diff; Haiku's decomposition read
  better but added docstrings and renamed `exit`). The reviewer's T2 and T4 line-length penalties do not
  apply to the repo's gates: `pyproject.toml` sets `line-length = 100` but does not select `E501`.
- Haiku's diffs carried more unrequested churn on T2 and T4 (reflowed continuation lines, docstrings, one
  rename). Price at the vendor's figures is about twenty times lower for Haiku.
- Limits: three tasks, one attempt each, one reviewer. Not a measurement of design-heavy refactors.
