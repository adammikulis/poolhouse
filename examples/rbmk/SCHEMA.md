# Recording and run documents

Plain JSON, UTF-8. A reader needs nothing else from this directory.

- **Recording** (`rbmk-recording/2`, files in `recordings/`): what the model said, with timing, bound to the run it
  produced by a digest. It holds no caption, title, label, verdict or score. `run.py replay FILE` re-runs the simulator with no
  model and refuses the file when the digest differs.
- **Run** (`rbmk-run/2`): the document an interface draws. `run.py export FILE --out OUT` replays a recording into it.
  Everything in it that is not a model answer is derived from the scenario data and the recorded answers. Its `display`, `captions`
  and `outcome` keys are computed after the fact by `display/`; they are never stored in a recording and never shown to the model.

## Recording

```
{schema: "rbmk-recording/2",
 meta: {scenario: key, seed, controller: "model", memory: "none" | "history", speed, finished,
        backend: {mode: "recorded", model, recorded_at, source, warm_up_s}, clocks},
 digest: sha256 of {trace, rows} of the run,
 decisions: [{n, tick, due_s, calls: [{key, state_sha256, answers, latency_ms, sent_at, answered_at, batch_size}]}]}
```

`n` is the request number. `due_s` is the simulated time the answer was applied (a replay applies it there). `state_sha256` is the
SHA-256 of the console text of that call. `warm_up_s` is the model load time, which is not counted as decider delay.

## Run document

| key | content |
|---|---|
| `schema` | `"rbmk-run/2"` |
| `meta` | `scenario` {key, group, seed, ticks, protections_off[], fields{...}}, `controller` (`model`, `naive` or `crew`), `memory`, `backend` {mode (`recorded` or `none`), model, recorded_at, source}, `clocks`, `finished`, `error`, `digest` |
| `display` | `{title, description}`: presentation text for the scenario |
| `limits` | `thermal_mw`, `orm_min` |
| `classes`, `protections` | the desk action classes and protection switches, with their text |
| `provenance` | `{prompt item: [category, source]}` for every item the model can be given |
| `rods` | the 211 rods: `id`, `kind` (MR manual, AR automatic regulation, LAR local regulation, USP shortened absorber, EP emergency protection), `x`, `y`, `w` (worth weight) |
| `trace` | one row per tick of true plant values, see below |
| `rows` | decision rows and plant event rows, in order, see below |
| `captions` | `[{tick, kind, text}]`: events and thresholds reached, stated as facts |
| `outcome` | `{state, damaged, final_margin, final_power, peak_power, peak_MW, az5_pressed, max_fuel_C, decisions}`; `state` is `damaged`, `margin_below_15` or `margin_at_least_15` (true margin at the end) |
| `late_answers` | model answers that arrived after a halt and were discarded: `[{request, note, latency_ms}]` |
| `whatif` | optional: `{request n: {action class: {target, applied, rows[], peak_P, damaged}}}`, each class applied once with no later action |

### `trace[]` row (true plant values, never shown to the model)

`tick`, `t_s`, `dt` (slow tick 300 s or 120 s, fast tick 1 s), `P` (relative power, 1 = 3200 MW), `X` (xenon index, 1 = full-power
equilibrium), `I`, `void`, `orm` (true margin, rod-equivalents), `f` (core flow), `Tf` (hottest fuel C), `p` (drum pressure MPa),
`rho` (reactivity in dollars), `level`, `demand`, `scram`, `damaged`, `pumps[8]`, `rods[211]` (insertion percent, in the order of
`rods`), `throttle`, `ar_on`, `test` (`none`, `pending`, `running`, `aborted`), `prot` {DRUM_TRIPS, TG_TRIP, ECCS: true when on},
`tip` (displacer reactivity, dollars), and what the room showed at that moment: `chA`, `chB` (power channel readings, percent),
`orm_printed` (the printout), `peak_P` (highest relative power so far, not truncated).
`crew` is the sensor view of the tick, as the control room's instruments show it and the decider is given it: `readings` {name: value},
`orm_printout`, `orm_age_s`, `demand_pct`, `pumps[8]` (speed percent, 0 when stopped), `regulator_on`, `regulator_setpoint_pct`,
`switches` (as `prot`), `turbine_test`, `az5`. The room reads only `crew`, `rods` (the indicators) and the decision in force; it
reads no plant value.

### `rows[]`

A decision row (`kind: "decision"`): `i`, `n` (request number), `tick`, `t_s` (snapshot time), `view` (the whole `SensorView` the model
was given: time, tick, demand, readings {name: {value, age_s}}, ORM printout and its age, rod indicators and commanded positions,
pumps, regulator, protection switches, turbine test state, AZ-5, desk log, strip-recorder trends when `memory` is `history`), `calls`
(each call: `key` `decision`, `target` or `step`; `state_text` the console text; `answers`; `latency_ms`; `sent_at`, `answered_at` wall
epoch seconds), `answers` (every answer by question name), `choice` {cls, target, step} (the most probable option of each question),
`batch` {calls[{key, size, latency_ms, sent_at, answered_at, tokens_max}], questions, latency_ms}, `latency_ms`, `delay_s` (latency times
`speed`), `due_s`, `applied_tick`, `applied_t_s` (null if the run ended first), `log_line` (the plain desk log line the action wrote,
or null if it did nothing).

A plant event row (`kind: "plant_event"`): `event` is `scenario_event` (`scenario_kind`, `value`), `protection_trip` (`protection`),
`protection_switch` (`protection`, `state`) or `request_ignored` (`cls`, `target`, `reason`), with `tick` and `t_s`.

### Answer

`{name, kind (noul / choice / score), choice, scores{option: probability}, certainty, error, p_true (noul), expected (score), backend
("pointer"), model, latency_ms, question, options{option: description}, tokens, call}`. `choice` is the most probable option.
`options` lists the options in the order the model saw them; for `class` and `target` that order is a seeded shuffle per request.
`scores` has every option, in that order, and sums to 1. `tokens` is the decider's own token count of the whole prompt. `error` is empty
in a stored answer: a failed answer stops the run.

### Questions in a request

1. `risk` (score 1 to 5) and `class` (choice among the nine action classes): one call on the whole console.
2. `target` (choice among the desk's options for that class, ending with `none`): asked when the class needs one.
3. `step` (score 1 to 3, a tenth, a quarter or half the stroke): asked for rod moves.

The three questions of a request are asked one after the other because each depends on the previous answer; the questions of one call
are answered in one batched request. The decider reads the current view again as soon as the previous answer has been applied.

## Index

`recordings/index.json`: `{schema: "rbmk-index/2", recordings: [{file, scenario, memory, model, recorded_at, digest}]}`,
written by `run.py index recordings`. Files are named `<scenario>[.<memory>]-<model-id>-<date>.json`.
