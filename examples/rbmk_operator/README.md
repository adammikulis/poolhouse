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
  safetensors). The first question loads the weights (about 12 s on an Apple M-series GPU); after that a question takes about
  3.5 s, and a full round is 6 to 8 questions, so about 25 to 30 s a round. A live poisoned-start run is a few minutes to
  about 15 minutes; all sixteen recorded scenarios took roughly two hours on one machine.
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

1. Reading questions, each asked about the sub-panel it concerns (`falling`, `void`, `orm_low`, `inconsistent`) and a `risk`
   score 1 to 5.
2. `class`: one of nine action classes (hold, insert a rod, withdraw a rod, change flow, regulator, protection, turbine test,
   ORM printout, scram).
3. `target`: a short named list built from the live state (the most effective rods to move, the automatic regulation group,
   the manual rods that are out, pumps to start or stop, the protections that could be defeated or restored). It always
   ends with `none`.
4. `step`: 1, 2 or 3 (10, 25 or 50 percent of the stroke) for rod moves.

An answer under the abstain floor (0.40, `--abstain-below`) is an abstention. The tick is marked ABSTAINED and the plant
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

## Results

The table below is `run.py summary examples/rbmk_operator/recordings` over real runs of `strands-decider-2B-hobson-v19`.
Failures are in it. Nothing was retried or selected: each scenario was run once.

The recordings are being generated and the table is added with them (`recordings/index.json` lists them as they land).

## Tests

`tests/test_example_rbmk_operator.py` (run with `scripts/test all tests/test_example_rbmk_operator.py`) does not start a
model or touch a GPU. The recording it replays, `tests/fixtures/rbmk_operator/fixture_short.recording.json`, is a real run
of the checkpoint on a seven-tick full-power scenario, used only for the round trip and replay tests. The loud stop is
tested against a real failure (a checkpoint directory that does not exist), not a mock. The real-model path itself is
exercised by `run.py selftest` and by the recordings, because tests here never use a GPU (`tests/README.md`).
