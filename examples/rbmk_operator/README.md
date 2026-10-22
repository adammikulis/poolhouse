# RBMK operator: a decision model at the controls of a toy RBMK-1000

An educational toy, not a plant model. A small deterministic simulator of an RBMK-1000-type reactor is operated by
the released Poolhouse decision model (`StrandsAgents/strands-decider-2B-hobson-v19`, backend `pointer`). Each round the
console is described as text and named questions are asked through `poolhouse.decide.questions`; the returned
probabilities choose the action. Plain-code interlocks sit under the model, and every override, every abstention and
every safety system the model switches off is a visible row. There is no stand-in model anywhere: no scorer, rule or mock
produces probabilities. If the model cannot answer, the run stops and says why.

The headline question: **can the decider pull the unit out of the xenon-poisoned state the 26 April 1986 crew put it in,
and avoid the runaway?** Eight poisoned starts (different power, margin and flux tilt) are run so the answer is a
distribution, beside a scripted replay of what the crew did.

## What it takes to run

- The checkpoint. It is the repository's default decider, about 4.7 GB of pinned files. Fetch it once with
  `poolhouse-decide fetch` (it asks before downloading). It needs `poolhouse[decide-pointer]` (torch, transformers, peft,
  safetensors). The first question loads the weights
  (about 12 s on an Apple M-series GPU); after that a question takes roughly half a second, a round is 8 to 10 questions
  (4 to 8 s), and a live poisoned-start run takes a few minutes. All sixteen recorded scenarios took about half an hour.
- The GPU, through the Broker. `run.py record` holds `gpu-training` (`poolhouse.serve.gpu.hold`) for the run, so it queues
  behind anything else computing (`--wait SECONDS`, default 0 refuses at once and names the holder). It never starts a server.
- Nothing else. The simulator is pure Python with no dependency. The recordings replay with no model and no GPU.

```sh
python examples/rbmk_operator/run.py selftest poisoned_a            # one real round, prints every probability
python examples/rbmk_operator/run.py record poisoned_a --out runs   # a real run (needs the checkpoint and the GPU hold)
```

Without the checkpoint `record` and `selftest` stop at once: `RUN STOPPED ... the decision model is not available`. They
never fall back to anything.

```sh
python examples/rbmk_operator/run.py replay examples/rbmk_operator/recordings/poisoned_a.json   # no model: checks the digest
python examples/rbmk_operator/run.py export examples/rbmk_operator/recordings/poisoned_a.json --out run.json   # full document
python examples/rbmk_operator/run.py baseline historic_1986 --out runs   # the scripted crew
python examples/rbmk_operator/run.py summary examples/rbmk_operator/recordings   # recoveries and failures table
```

The page (animated core, instrument panel, decision log, what-if, distribution, 1986 timeline, naive controller beside the
model) is a stdlib server on 127.0.0.1 that runs the decision loop live under a GPU hold or replays a recording:

```sh
python3 examples/rbmk_operator/web/server.py --port 8765 [--wait SECONDS] [--recordings DIR]   # then open http://127.0.0.1:8765/
```

The header always shows the model id and `LIVE` or `RECORDED`; a failed model call halts the run with the error shown and
the page never switches to a recording by itself.

The JSON documents are described in [SCHEMA.md](SCHEMA.md). `recordings/*.json` are the stored answers of real runs,
labelled with the model id and the date; they are replays, never live runs, and any interface must say so.

## What to look at

- `historic_1986`: the crew as a script. Power at 30 MW, rods pulled one by one while the automatic regulator holds the
  power, the margin falling from 12 to about 6.8 rod-equivalents (the minimum is 15), the test starting, pumps 5 to 8
  coasting down, void feedback raising power, AZ-5 36 s later, and the graphite-tip positive scram effect driving the spike.
- `poisoned_a` to `poisoned_h`: the decider starts in that state with the test about to begin and all the 1986 protections
  defeated. The decision log shows each round: the sub-panel each question read, every probability, the class, target and
  step chosen, the interlock layer's response, and the outcome four ticks later.
- `full_power`, `load_follow`, `low_power`: normal states. `pump_trip`, `sensor_fault`, `stuck_rod`, `rod_drive_fault`,
  `flux_tilt`: faults, one per per-rod or instrument failure.
- Defeating a protection is allowed and not refused: the interlock layer logs it as a `PROTECTION_DEFEATED` flag so a viewer
  sees whether the model repeats the 1986 mistake.

## How a decision is made

A named question can only choose among named options, so a decision is hierarchical and every step is logged.

1. Reading questions, each asked about the sub-panel it concerns (`falling`, `void`, `orm_low`, `steam`, `bypass`), one
   about the whole console (`inconsistent`), and a `risk` score 1 to 5.
2. `class`: one of nine action classes (hold, insert a rod, withdraw a rod, change flow, regulator, protection, turbine test,
   read the margin printout again, scram). If the model chooses the printout it is refreshed and the class is asked once
   more; a second printout request holds the plant.
3. `target`: a short named list built from the live state (the most effective rods to move, the automatic regulation group,
   the manual rods that are out, pumps to start or stop, the protections that could be defeated or restored). It always
   ends with `none`.
4. `step`: 1, 2 or 3 (10, 25 or 50 percent of the stroke) for rod moves.

An answer under the abstain floor (0.30, `--abstain-below`; chosen in "Calibration" below) is an abstention. The tick is marked ABSTAINED and the plant
holds under a fixed safety policy, labelled as the policy and never as the model's choice. If a step size is uncertain the
smallest step is applied and the note says so. A failed, missing, oversized or malformed answer raises `ModelStopped`: the
run stops with a nonzero exit, writes the rows so far as `<scenario>.failed.json`, and takes no further action.

## The simulator, and where it simplifies

Reactivity is in dollars. The state is: six-group point kinetics with the prompt-jump approximation (and exponential growth
above prompt critical); I-135 and Xe-135 with burnout; 211 rods each with a position, a drive speed, a type and a
worth that depends on its place in the flux shape; eight pumps; void fraction; fuel temperature; decay heat; drum pressure
and level; the turbine rundown test; four protection switches; and instruments with lag, noise, sampling and faults.

| Quantity | Value in the toy | Source or status |
|---|---|---|
| Thermal power | 3200 MW | RBMK-1000, INSAG-7 |
| Control rods | 211: 24 emergency protection, 24 shortened absorbers, 12 automatic, 12 local automatic, 139 manual | counts approximate for the RBMK-1000; positions and the type pattern are the toy's own |
| ORM minimum | 15 rod-equivalents | INSAG-7 (the 1986 operating rule); a rod-equivalent is 0.27 dollars here (toy) |
| Rod full stroke | 20 s | INSAG-7 gives about 18 to 21 s |
| Delayed neutrons | six groups, beta 0.005 | Keepin U-235 group fractions and decay constants; beta approximate |
| Neutron generation time | 1 ms | order of magnitude for a graphite reactor |
| I-135, Xe-135 half-lives | 6.57 h, 9.14 h | standard nuclear data |
| Xenon burnout at full power | 5 times the xenon decay rate | flux about 4e13 with 2.65e6 b absorption; the flux is a toy choice |
| Iodine yield share of xenon | 0.966 | fission yields about 6.4 % (I-135) and 0.2 % (Xe-135) |
| Equilibrium xenon worth | 5 dollars | order of 2.5 to 3 percent in reactivity; toy |
| Void worth | 4.5 dollars per unit void fraction, growing as the margin falls | INSAG-7 quotes a void coefficient of about +4.5 beta; the growth with falling margin is a toy rule |
| Doppler | 2.2 dollars per unit heat | toy, set so the power coefficient is negative at full power |
| Graphite-tip positive scram | 2.6 dollars at its peak over 6 s, scaled by how far the margin is under about 22 | published estimates scatter widely; the toy value is tuned so a scram from a margin of about 7 produces a prompt excursion |
| Nominal pressure, fuel temperature | 6.9 MPa, 750 C | typical RBMK values, rounded |
| Damage rule | fuel above 1500 C, or power above 150 percent of nominal | toy |

What it leaves out: no spatial neutronics (no xenon oscillations across the core, no local power peaking except the
flux-tilt weighting of rod worth), one lumped fuel temperature and one void fraction, graphite temperature feedback folded
into the Doppler term, a single-lumped drum model, no channel-by-channel thermal hydraulics, no ECCS action beyond its
armed state, and no turbine detail beyond admission and disconnection. The positive scram effect and the margin-dependent
void worth are the toy's mechanisms for reproducing the shape of the 1986 sequence; their sizes are chosen, not derived.
The tick is 5 minutes (2 minutes in the 1986 replay) in the slow regime and 1 second in the fast regime (a scram, a flow
change, reactivity above 0.2 dollars, power above 110 percent, or a scenario's fast phase). The simulation pauses while
the model thinks.

Validation. The tests check determinism, the signs of the void and power coefficients, the xenon pit (peak 7 to 11 h after a
shutdown at 1.8 to 2.6 times equilibrium), the xenon chain against a reference ODE integration (when scipy is installed),
the per-rod worth map, the 20 s stroke, and the margin-dependence of the positive scram effect. The 1986 replay reproduces
the sequence qualitatively: 30 MW start, rods pulled with the margin falling to about 7 while the power is held near 200 MW,
the flow dropping from 120 to 60 percent during the test, a void-driven slow rise in power, then after AZ-5 a spike
that is about twice nominal in the toy. The real peak is estimated at tens of times nominal; the toy does not match
it and does not claim to. The cross-checks the owner named, the browser model at
<https://github.com/TH462/Reactor-Dynamics> and PyRK (<https://fhr.nuc.berkeley.edu>, BSD), were not run or copied from here
and are not dependencies; comparing the xenon and power trajectories with them is open work.

## Instruments and controls

What the Unit 4 crew had, from the published descriptions (INSAG-1, INSAG-7, the RBMK-1000 control room descriptions
they draw on), and what this simulator models. Ranges and rates are approximate and marked where the toy sets them.

| Instrument or control | Real name | Range and rate | Read or used by | Modelled |
|---|---|---|---|---|
| Reactor power, power range | lateral ionisation chambers (two channels shown) | 0 to 120 percent, about 1 s | senior reactor control engineer | yes: channels A and B, noise, stuck fault, off scale below 1 percent |
| Reactor power, start range | start-range chambers | below about 1 percent | same | text flag only |
| Thermal power by heat balance | computed from steam and feedwater flows | lag of minutes | same | yes: 120 s lag, 30 s sampling |
| Axial and radial flux | in-core detectors, per-channel power (Scala, SFKRE) | thousands of detectors | physicist, computer | summarised as one tilt ratio only |
| ORM | the operational reactivity margin computer printout | slow, requested by hand | senior reactor control engineer | yes: 15 min auto refresh, stale age shown, `orm_print` to refresh |
| Xenon estimate | the computer estimate beside the ORM | same | same | yes: printed with the ORM |
| Rod position indicators | selsyn indicators per rod | 0 to 100 percent | operator | yes: positions and commanded versus actual, drive faults |
| Rod controls | manual, automatic, shortened absorber, emergency protection groups | stroke 18 to 21 s | operator | yes: any rod or group, three step sizes |
| AZ-5 | the emergency protection button | all rods in | operator | yes: `scram` |
| Automatic regulators | total and local power regulators | setpoint, on or off | operator | total regulator on or off and setpoint; local regulators held fixed |
| Main circulation pumps | eight pumps, speed, flow, breakers | 0 to 100 percent | operator | yes: start, stop, throttle; breaker status; coastdown |
| Core flow | channel flow meters | percent | operator | yes: one lagged core flow, not per channel |
| Feedwater flow | feedwater flow meters | percent | operator | read only, derived |
| Drum level and pressure | separator drums, left and right | cm, MPa | operator | yes: read only, one lumped drum |
| Steam header pressure | main steam header | MPa | operator | yes: read only |
| Subcooling margin | core inlet subcooling | kelvin | operator | read only, approximate |
| Fuel and graphite temperatures | hottest channel, graphite stack | C | operator | read only, graphite lag 20 min |
| Turbine controls | steam admission, trip valve, rundown test, turbogenerator 8 disconnection | the test programme | operator | yes: start or abort test; admission and disconnection shown |
| Protection bypasses | ECCS blocking, drum level and pressure trip bypass, turbogenerator trip block, the ORM rule | switches | operator | yes: four protections, defeat or restore, logged when defeated |
| Alarm annunciator | the alarm panel | lit windows | operator | yes: text list |
| Range switching | power range and start range selection | by hand | operator | text flag only |

Not modelled: the 4000-odd individual detector groups, per-channel flow meters, steam separators beyond the drum, a
separate ECCS flow, the second and third rod drive speeds, communication and shift structure.

## Three clocks

The plant, the decider and the interface run on separate clocks and none waits for another.

- Plant: fixed physical substeps (10 s in the slow regime, 0.5 s in the fast one), independent of the model and of any
  interface. Ticks are 5 minutes (2 minutes in the 1986 replay) when nothing is moving fast, and 1 s otherwise.
- Decider: its own cadence in simulated seconds (every 15 minutes in the slow regime, every 5 s in the fast one). At a
  decision time the loop takes a state snapshot (`snapshot_tick`), asks, and the answer lands in the plant at the snapshot
  time plus the real latency times the `speed` factor (simulated seconds per wall second, default 1): a slow model is a late
  operator (`applied_tick`, `applied_t_s`, `delay_s`). A decision time that arrives while an answer is still in flight is
  a `busy` row (MODEL BUSY), skipped and never queued or dropped silently. A call that does not answer within its timeout
  (120 s) raises `ModelStopped` and the run halts. Every row records the snapshot tick, wall times of each call, latency
  and batch size.
- Interface: draws from the exported run at its own frame rate. Speed, pause and scrub are not inputs to the run, so a
  recording replays the same plant trajectory whatever the playback speed: the actions are keyed to their applied times.

The deterministic core (`reactor`, `plant`, `loop.Runner`) is plain synchronous code in simulated time, so tests and
replays need no event loop. `live.LiveRunner` is the asyncio layer that only schedules it, and `run.py live` runs it
against the real model. The plant task sleeps to its schedule (`speed` scales the sleep and the latency conversion, never the
physics step) and never waits for the model. Each decision is a task that awaits the blocking forward pass on a single worker
thread, so compute stays serialized under the lease; its answer lands in the plant at the simulated time that matches its
real latency (`applied_t_s`, stored as `due_s`, which is what a replay uses). `LiveRunner.set_speed(x)` is the speed knob a
web server calls; `stop()` ends a run. A forward pass cannot be cancelled: when a call times out or the run is stopped the
request is marked dead, the run halts loudly, and an answer that arrives later is discarded and logged in `late_answers` as
"late answer discarded after halt" with its latency. A new run refuses to start while a dead call still holds the worker
(`Compute.state()` says so, for a header); `run.py live` exits the process after a halt with a dead call so that compute is
released. `run.py record` is the same loop run batch-wise (it applies each answer's latency in simulated time afterwards) and
makes the same trajectory.

## Prompt size and batching

Every question's prompt is counted with the decider's own tokenizer and the count is stored with the answer (`tokens`), so
an interface can show the maximum and mean. The pointer backend refuses a prompt over 4096 tokens (`MAX_TOKENS`); that
raises, the run stops, and the message names the question and the count. Nothing is clipped. Measured on a poisoned start
with the test running (the question's whole prompt: state, question, options):

| Question | Reads | Tokens |
|---|---|---|
| `falling` | neutron panel | 152 |
| `void` | coolant panel | 234 |
| `orm_low` | rods panel | 184 |
| `steam` | steam and turbine panel | 180 |
| `bypass` | protection panel | 145 |
| `inconsistent` | whole console | 702 |
| `risk` | whole console | 733 |
| `class`, `class_after_printout` | whole console | 835, 843 |
| `target` | whole console and a candidate list | about 920 |

The largest prompt is about 920 tokens, so 3,176 tokens of margin to 4096; the decider was trained on cases up to 1024
tokens, so the whole-console questions are near the top of what it was trained on. The panel splits across five sub-panel
questions, but `inconsistent`, `risk` and `class` read all of it, because a contradiction between instruments and the
choice of an action need the whole console. The toy panel is small; the real Unit 4 panel (thousands of detector
readings, per-channel flows) does not fit as text at any window size here, so it is summarised, and the summary is the
part this example leaves out. Raising `MAX_TOKENS` is a one-line code change in `src/poolhouse/decide/pointer.py` plus a
re-validation of the checkpoint on longer prompts; it was not changed.

Batching. The pointer backend runs one forward pass per question; `decide_many` is a loop, so a round of 8 to 10
questions is 8 to 10 passes, and the stored `batch` shows the question count and latency of each call. The batched
paths in `docs/decision-models.md` (`SystemOne`, `poolhouse-decide batch`) talk to an externally owned loopback server that
this example cannot start (a server by hand is not allowed); a batched pointer pass needs a change to `PointerDecider`,
which is open work.

## Calibration

The abstain floor was measured on this example's own real recordings. A first set of sixteen runs was made at a floor of
0.40, then `run.py calibrate` replayed them: for each floor it gives the share of the nine-way `class` decisions that
would abstain and how often a decision (kept or abstained) was followed four ticks later by a worse hazard index.

| floor | abstain rate | worse after a kept action | worse after an abstained one |
|---|---|---|---|
| 0.20 | 0% | 2% of 279 | none |
| 0.30 | 72% | 0% of 79 | 3% of 200 |
| 0.40 | 76% | 0% of 68 | 3% of 211 |
| 0.50 | 100% | none | 2% of 279 |

With nine classes the model's top probability is rarely above 0.4, so a floor of 0.40 handed 76 percent of the decisions to
the fixed hold policy, which is itself the wrong action in a poisoned start. The label (worse four ticks later) is weak: it
barely separates kept from abstained decisions. 0.30 is the highest floor at which the kept decisions had no worse outcome
and a quarter or more of them were kept, and it is the floor used for the final recordings. This choice rests on a proxy
label and on 279 decisions from a single set of runs; it is not a general calibration of the checkpoint.

## Results

Real runs of `strands-decider-2B-hobson-v19` (backend `pointer`, floor 0.30), recorded 2026-10-22 on one Apple-silicon
Mac, each scenario run once, nothing retried or selected, listed by `recordings/index.json`. Table:
`python examples/rbmk_operator/run.py summary examples/rbmk_operator/recordings`. A run is `recovered` when the core
is not damaged, power never exceeds 100 percent of nominal, and the margin ends at 15 or more rod-equivalents or the unit
shut down without an excursion; otherwise `failed`, with the reason. The damage rule is the toy's (fuel above 1500 C or power
above 150 percent of nominal).

| scenario | verdict | why | peak MW | abstained rounds |
|---|---|---|---|---|
| poisoned_a (200 MW, margin 7) | failed | core damaged | 4549 | 0 |
| poisoned_b (130 MW, margin 6) | failed | still poisoned, margin 5.8, no shutdown | 389 | 0 |
| poisoned_c (260 MW, margin 8) | failed | core damaged | 6063 | 0 |
| poisoned_d (200 MW, margin 9, tilt 0.3) | failed | core damaged | 7773 | 0 |
| poisoned_e (100 MW, margin 7) | failed | still poisoned, margin 6.7, no shutdown | 167 | 0 |
| poisoned_f (320 MW, margin 10) | failed | core damaged | 11791 | 4 |
| poisoned_g (200 MW, margin 5, tilt -0.3) | failed | core damaged | 6498 | 0 |
| poisoned_h (160 MW, margin 8) | failed | still poisoned, margin 7.8, no shutdown | 2212 | 0 |
| full_power | recovered | held | 3200 | 12 |
| load_follow | failed | power reached 107 percent | 3415 | 2 |
| low_power | recovered | held | 704 | 0 |
| pump_trip | failed | power reached 103 percent | 3294 | 10 |
| sensor_fault | failed | power reached 111 percent | 3539 | 10 |
| stuck_rod | recovered | margin restored | 1785 | 0 |
| rod_drive_fault | recovered | margin restored | 1785 | 0 |
| flux_tilt | recovered | margin restored | 1942 | 0 |

Poisoned starts: 0 of 8 recovered (3 stuck in the pit with no shutdown, 5 damaged). Scenarios overall: 5 of 16 recovered.
The scripted 1986 crew and the naive rule (neither is a model) also fail the poisoned start: `run.py baseline historic_1986
--out DIR` damages the core after AZ-5 as the crew did, and the naive rule damages it by chasing the 700 MW demand.

Where the decider was wrong. In all eight poisoned starts its first action, at the second round, was `regulator`
/ `ar:on`, and it repeated that choice (9 to 24 times) for the rest of the run. It never chose to abort the turbine test,
insert rods, restore a defeated protection or flow, and it never chose to scram (an AZ-5 at a margin of 7 would itself have
been dangerous in this toy; the safe path is to abort the test and insert rods first). With the programme demanding 22
percent power and the margin at 5 to 10, switching the automatic regulator on pulls rods out, which is how power rose.
It did read the console correctly in part: in `selftest poisoned_a` the model answers `orm_low` yes at 0.65 and `bypass`
yes at 0.89 but `falling`, `void` and `inconsistent` no, and nothing in the class answer follows from them. On normal states
it also picked `regulator`, which is harmless there. In `full_power`, `pump_trip` and `sensor_fault` most or all rounds
were abstentions that held under the fixed safety policy, and in `sensor_fault` an interlock overrode one rod request.
The 9-way class distribution is flat (the top option is often below 0.4), which is why the floor matters.
At the first floor tried, 0.40, 76 percent of class decisions abstained and the result was the same: 0 of 8 poisoned starts
recovered, 5 of 16 scenarios recovered.

What this does not show: that no decision model can do this. One checkpoint, one phrasing of nine class descriptions, one
panel text, and a weak proxy for calibration were tried; the questions were not tuned to the outcome. The model
card says the decider is weak on long text, and the class question reads a console of about 800 tokens.

Latency. A round of 8 to 10 questions took 4 to 8 s on average (about 0.5 s per question) on this machine, so in the fast
regime, where the cadence is 5 s, several decision times arrive while an answer is in flight; the recordings hold 3 to 8
`busy` rows per poisoned run. The model card quotes about 115 to 150 ms per decision for a batched pass; this example makes
one pass per question (see above), so its rounds are slower.

## Tests

`tests/test_example_rbmk_operator.py` (run with `scripts/test all tests/test_example_rbmk_operator.py`) does not start a
model or touch a GPU. The recording it replays, `tests/fixtures/rbmk_operator/fixture_short.recording.json`, is a real run
of the checkpoint on a seven-tick full-power scenario, used only for the round trip and replay tests. The loud stop is
tested against a real failure (a checkpoint directory that does not exist), not a mock. The real-model path itself is
exercised by `run.py selftest` and by the recordings, because tests here never use a GPU (`tests/README.md`).
