# Run and recording schema

Two JSON documents. Both are plain JSON, UTF-8, written by `run.py`; a reader needs nothing from this directory.

- **Recording** (`rbmk-operator-recording/1`, files in `recordings/`): only what the decision model said, plus a digest of
  the run it produced. Small (tens of kilobytes). `run.py replay FILE` re-runs the simulator with no model and checks the
  digest; a text or option that differs from what the model was asked is an error (`ReplayMismatch`), never a guess.
- **Run** (`rbmk-operator-run/1`): the full document an interface draws. `run.py export FILE --out OUT` replays a
  recording into it; `bundle.export(result, scenario, backend)` builds it from a live result. Everything in it that is not
  a model answer is derived deterministically from the scenario and seed.

A run has `meta.backend.mode`: `recorded` (a file from a real run of the model, replayed with no model running), `none`
(a baseline controller, no model: the scripted 1986 crew or the naive rule), or `live` (an in-memory run being produced now;
an interface sets it while it streams). `meta.backend.model` is the model id, `recorded_at` is the date the real run was made.
An interface must show the model id and `live` or `recorded from <model> on <date>` at all times.

## Run document

| key | content |
|---|---|
| `schema` | `"rbmk-operator-run/1"` |
| `clocks` | the three clocks: `plant_substep_s` {slow 10, fast 0.5}, `tick_s` {slow, fast}, `decision_cadence_s` {slow, fast}, `speed` (simulated seconds per wall second used to turn latency into plant time), `note` |
| `toy_notice` | the educational-toy notice to show |
| `meta` | `scenario` {key, title, blurb, group, seed, ticks, defeated[]}, `controller` (`model` / `naive` / `crew`), `backend` {mode, model, recorded_at, abstain_below, source}, `finished`, `error` (a string when the run stopped, else null) |
| `limits` | `thermal_mw`, `orm_min`, `fuel_c`, `pressure_mpa` |
| `classes` | the nine action classes and what each means, in the order the model is asked |
| `interlocks` | every interlock id and its rule text |
| `protections` | the four protection switches and what each is |
| `rods` | the 211 rods: `id`, `kind` (MR manual, AR automatic regulation, LAR local regulation, USP shortened absorber, EP emergency protection), `x`, `y` (core position, -1 to 1), `w` (worth weight, mean 1) |
| `trace` | one row per tick, see below |
| `events` | `{tick, kind, text}` the scenario did to the plant (not the operator) |
| `decisions` | one row per question round or automatic trip, see below |
| `captions` | `{tick, kind, text}` plain narration at key moments (xenon pit, margin below minimum, void feedback, graphite-tip scram, damage, interlock, defeat, abstain) |
| `outcome` | `verdict` (`recovered` / `failed`), `why`, `peak_P`, `peak_MW`, `max_hazard`, `scram`, `damaged`, `final_orm`, `final_P`, `decisions`, `abstained`, `interlock_rows`, `protections_defeated` |
| `score` | `in_band` (0 to 40), `within_limits` (0 to 30), `reacted_early` (0 or 20), `avoided_scram` (0 or 10), `total` (0 when the core was damaged) |
| `late_answers` | `[{request, note: "late answer discarded after halt (...)", latency_ms}]`: model answers that arrived after a timeout or a stop and were never applied |
| `whatif` | `{decision index: {action class: branch}}`; a branch is `{applied {cls, target}, interlocks[], rows[], peak_P, end_hazard}` where `rows` are `{tick, P, hazard, orm, f, Tf, scram}` for the next 10 ticks with no further operator action (the trips stay on). Absent from recordings. |

### `trace[]` row (true plant values, plus the two power channels the operator reads)

`tick`, `t_s` (plant seconds), `dt` (300 or 120 s slow tick, 1 s fast tick), `P` (relative power, 1 = 3200 MW), `chA`, `chB`
(channel readings, fractions), `X` (xenon index, 1 = full-power equilibrium), `I` (iodine), `void`, `orm` (true margin,
rod-equivalents), `orm_printed` (what the stale ORM printout shows), `f` (core flow, 1 = nominal), `Tf` (hottest fuel C), `p`
(drum pressure MPa), `rho` (reactivity in dollars), `level` (drum level cm), `setpoint`, `hazard` (0 to 1.5, 1 is a limit),
`scram`, `damaged`, `pumps[8]` (speeds 0 to 1), `rods[211]` (insertion 0 to 100, in the order of `rods`), `throttle`, `ar_on`,
`test` (`none` / `pending` / `running` / `aborted`), `prot` {ORM_RULE, DRUM_TRIPS, TG_TRIP, ECCS: true when active},
`model_risk` (the model's expected 1 to 5 risk at its last decision, or null), `tip` (graphite-tip reactivity, dollars).

### `decisions[]` row

| key | content |
|---|---|
| `i`, `n`, `kind` | row index; `n` is the request number a model decision answers (a recording is keyed by it); kind: `decision` (the operator was asked), `busy` (a decision time arrived while an earlier answer was still in flight: MODEL BUSY, skipped, nothing queued) or `trip` (an automatic trip fired, no question asked) |
| `tick`, `t_s`, `snapshot_tick`, `fast` | when the state snapshot was taken (simulated) |
| `applied_tick`, `applied_t_s`, `due_s`, `delay_s` | when the answer landed in the plant: snapshot time plus latency times `meta` speed. `due_s` is the exact simulated time it was due (a replay lands the action there). Null if the run ended first |
| `batch` | `{calls: [{key, size, latency_ms, sent_at, answered_at (wall epoch seconds), tokens_max}], rounds: [{keys, sent_at, answered_at, wall_ms}], questions, latency_ms}`. A round is one batched forward pass of the pointer backend: every independent question of a level (the reading questions, `risk` and `class`) goes in one pass; `target` and `step` follow as their own rounds |
| `state_text` | the whole console text the model read (all sub-panels) |
| `state` | a trace row at that moment |
| `calls[]` | each model call: `{key, state_text, answers{name: answer}}`. Keys: `neutron`, `coolant`, `rods`, `steam`, `protection`, `instruments`, `decision`, `decision_after_printout`, `target`, `step`. Each call shows the model only the sub-panel its question concerns. The text of a call is never clipped: a panel over the model's 4096-token limit stops the run with an error. |
| `answers` | every answer by question name (`falling`, `void`, `orm_low`, `inconsistent`, `risk`, `class`, `target`, `step`), with a `call` key naming the call it came from |
| `model_choice` | what the model chose: `{cls, target, step}`, or null if it abstained before choosing a class |
| `requested` | what was asked of the interlock layer: `{cls, target, step}` (a held abstention shows `hold`) |
| `applied` | what the plant actually did when the answer landed (null until it lands) |
| `source` | who decided what was applied: `model`, `safety-policy` (the model abstained: hold and flag a person), `interlock` (an interlock changed the request), `naive-rule`, `crew-history` |
| `abstained` | true when the fixed safety policy replaced a model answer |
| `interlocks[]` | blocks: `{id, rule, detail, severity: "block"}`; the request was changed |
| `flags[]` | non-blocking: `{id: "PROTECTION_DEFEATED", detail: <protection>, severity: "defeat"}`; the decision switched a safety system off and it was applied |
| `note` | free text, including the abstain explanation |
| `latency_ms` | summed model time for the round |
| `model_risk` | the `risk` answer's expected value |
| `outcome` | what happened four ticks later: `{tick, P, hazard, hazard_before, P_before, verdict: better / steady / worse}` |

### Answer (one model question)

`{name, kind (noul / choice / score), choice, scores{option: probability}, certainty, abstained, error, p_true (noul), expected
(score), backend ("pointer"), model, latency_ms, question (text), options{option: description}, tokens (the decider's own token count of this question's whole prompt), call}`. `scores` always holds
every option, in the order of `options`, and sums to 1 (a run stops with `ModelStopped` otherwise). `error` is empty in any
stored answer: a failed answer stops the run and is never stored as an answer.

`options` lists the options in the order the model saw them. For `class`, `class_after_printout` and `target` that order is a
seeded random shuffle per request (scenario seed, request number, call key), so no option is first by design.
Every console starts with a `KEY FACTS` line of plain numbers against their limits.

### Questions asked in a round

1. `falling` (noul, neutron panel), `void` (noul, coolant panel), `orm_low` (noul, rods panel), `steam` (noul, steam panel), `bypass` (noul, protection panel), `inconsistent` (noul, every panel).
2. `risk` (score 1 to 5) and `class` (choice among the nine action classes), both on the full console.
3. `class_after_printout`: when the class is `orm_print` the printout is refreshed and the class is asked again once (the printout changes nothing in the plant). A second `orm_print` holds.
4. `target` (choice among a short named list built from the live state; always ends with `none`), asked when the class needs one.
5. `step` (score 1 to 3: 10, 25 or 50 percent of the stroke), asked for rod moves.

### Action classes

`hold`, `insert_rod`, `withdraw_rod`, `change_flow` (pump start/stop, throttle), `regulator` (automatic regulator on/off,
setpoint), `protection` (defeat or restore ORM_RULE, DRUM_TRIPS, TG_TRIP, ECCS), `turbine_test` (start or abort), `orm_print`
(ask for a fresh margin printout), `scram` (AZ-5).

## Recording document

`{schema: "rbmk-operator-recording/1", meta, outcome, digest, decisions: [{i, tick, calls: [{key, state_sha256, answers, latency_ms, sent_at, answered_at}]}]}`.
`meta` and `outcome` are as in the run document. `digest` is the SHA-256 of the run document (without `whatif`) that the
recording replays to. `state_sha256` is the SHA-256 of the console text of that call.

A failed live run writes `<scenario>.failed.json` in the run-document form with `meta.error` set and
`meta.backend.mode = "failed"`; it holds the rows up to the stop and nothing after.

## Index

`recordings/index.json`: `{schema: "rbmk-operator-index/1", recordings: [{file, scenario, title, group, model, recorded_at,
abstain_below, verdict, why, peak_MW}]}`, written by `run.py index recordings`. Files are named
`<scenario>-<model-id>-<date>.json`.
