# RBMK experiment

A small decision model sits at a toy RBMK-1000 control desk and operates it. Nothing in this directory is a plant model for any
purpose other than this experiment.

## Design

The experiment: a real small decision model (the Strands decider) sits at the Unit 4 control room with only what the 1986 crew had
(their instruments with real lag and faults; their briefing and written rules), operates the same controls (rods individually,
pumps, regulators, bypasses, AZ-5), and starts from normal states and from the xenon-poisoned state. We measure honestly whether it
recovers. Fairness rules:

1. Every answer is a real forward pass of the model: no stand-in, no replacement, no silent fallback, no deferring to a person. A
   failed call halts the run loudly.
2. The model's inputs contain nothing computed from hidden state, nothing the crew lacked, no hints, no steering words; the option
   order is random and logged.
3. The plant, the decider and the display run on separate clocks, and the model's latency is a real delay. Decisions run back to
   back: when an answer has been applied, the decider reads the current view and decides again.
4. The only protections are the real plant's, and the crew's ability to defeat them is kept.
5. Captions, labels and verdicts never state the right action or grade a choice before its outcome.
6. One app, one link: the control room is the app, and the controls the model uses are the desk controls.
7. Results (damaged, stable below 15 rod-equivalents, margin at least 15) are reported as measured, including failures.

Each line is checked by `tests/test_rbmk_experiment.py`: the layering test, the prompt tests, the no-substitution test, the display
tests and the clock tests. Code that conflicts with a line is fixed, not the line.

## The chain

Plant (`plant/`, hidden truth, own fixed substeps) -> sensor model (`sensors/`, the only reader of the plant; produces an immutable
`SensorView`) -> prompt builder (`prompt/`, takes a `SensorView` and nothing else; every item has a provenance entry) -> decider
(`decider/`, the model asked class, then target, then step; always the most probable option) -> controls (`controls/`, maps the
choice to a desk action and applies it to the plant) -> recorder (`recorder.py`, events only) -> display (`display/`, all
presentation text, computed after the fact). `runner.py` runs the steps in simulated time; `live.py` schedules the same steps with
asyncio. `scenarios.py` and `starts.py` are data. The import rules are in the layering test.

## Running it

The model is the released Strands decider (`strands-decider-2B-hobson-v19`, pointer backend). Fetch it once with
`poolhouse-decide fetch`. Every command that asks the model holds the GPU through the Broker (`poolhouse.serve.gpu.hold`), loads the
weights once, and does not count the load as operator delay.

```sh
python examples/rbmk_experiment/run.py batch --count 300 --seed 1 --memory none history --wait 86400
python examples/rbmk_experiment/run.py summary
```

`batch` draws poisoned starts from the ranges below (the k-th start of seed s is always the same start), runs each for each memory
variant until the unit is damaged, shut down, or has its margin back and steady, or three simulated hours have passed, and skips a
start already in the run store, so it can be stopped and started again. Results go to the local run store,
`<poolhouse state>/rbmk-experiment/runs/<model>/<memory>/`, and are not part of the repository.

| range | drawn uniformly from |
|---|---|
| power | 0.03 to 0.1 |
| margin | 5.0 to 11.0 |
| iodine | 0.9 to 1.4 |
| tilt | -0.3 to 0.3 |
| throttle | 0.85 to 0.95 |

The manual rods that are inserted are a random pattern for each start, the regulator is off, eight pumps run, the drum, turbogenerator
and ECCS protections are off, and a supervisor order to begin the turbine rundown test arrives 4 seconds in; the model decides whether
to start it.

Other commands: `record` (named scenarios), `live` (asyncio runner with a speed knob), `replay` (no model; checks the digest),
`export` (the full run document), `baseline` (the scripted 1986 crew or the naive rule; neither is a model), `selftest`.
`SCHEMA.md` describes the recording and the run document.

## What the model is given

Only a `SensorView`. The table lists every item of the prompt and where it comes from. INSAG-7 could not be retrieved while this was
written, so nothing is cited to it by page: its entries say so, and everything marked unverified is the toy's own.

| item | kind | provenance |
|---|---|---|
| `clock` | instrument | a control-room clock; the format is the toy's |
| `briefing` | briefing | INSAG-7 (1992), general description; the section was not checked while writing this: the 15 rod-equivalent margin rule, the 700 to 1000 MW thermal test, the AZ-5 button; the list of desk controls is the toy's |
| `demand` | briefing | INSAG-7 (1992), general description; the section was not checked while writing this: the test programme power; each scenario's demand schedule is the toy's |
| `ion_chambers` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: power from ionisation chambers; two channels; their lag, noise and sampling are the toy's |
| `heat_balance` | instrument | unverified: the toy's own value or presentation: a heat-balance power with a 120 s lag |
| `orm_printout` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: the margin came from the unit computer on request; the value here is the true margin at the moment of the request, and it ages |
| `rod_indicators` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: rod position indicators; the panel shows counts and means by rod type (toy summary) |
| `regulator` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: automatic power regulators; one regulator with an on/off and a setpoint is a simplification |
| `pumps` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: eight main circulation pumps; speed in percent is the toy's |
| `flow_meter` | instrument | unverified: the toy's own value or presentation: one lagged core flow summary of the per-channel flow meters |
| `drums` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: steam separator drum levels and pressures; the numbers and lags are the toy's |
| `steam_header` | instrument | unverified: the toy's own value or presentation: main steam header pressure |
| `feedwater` | instrument | unverified: the toy's own value or presentation: feedwater flow |
| `turbine_test` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: the turbine rundown test; the three states shown are the toy's |
| `protection_switches` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: trip bypass and blocking switches existed; the three modelled switches and their ON/OFF presentation are the toy's |
| `az5` | instrument | INSAG-7 (1992), general description; the section was not checked while writing this: the AZ-5 button |
| `desk_log` | instrument | unverified: the toy's own value or presentation: the last five desk actions in plain words |
| `strip_recorders` | instrument | unverified: the toy's own value or presentation: strip recorders existed in the room; their window and speed are the toy's |

The strip-recorder trends appear only in the `history` memory variant. A `state` variant (carrying the model's recurrent state
between decisions) is not implemented.

## The toy plant

Six-group point kinetics with the prompt-jump approximation, the I-135 and Xe-135 chain with burnout, 211 rods each with a position,
a drive speed, a type and a worth that depends on its place in the flux shape, eight pumps, a void fraction with a positive void
coefficient that grows as the margin falls, a Doppler term, decay heat, drum pressure and level, the turbine rundown test, three
protection switches, and the positive scram effect: the graphite displacers add reactivity per rod as rods enter from the withdrawn
end, scaled by how far the margin is below about 22 rod-equivalents.

Facts used for the 1986 start and sequence, from secondary sources (World Nuclear Association, "Chernobyl accident appendix 1:
sequence of events", and the Wikipedia article on the accident): the ECCS isolated before the test, the turbogenerator trip signal
blocked at 00:43:27, the drum automatic trips deactivated about 01:19, power near 30 MW at 00:28 and held near 200 MW from about 01:00,
an after-the-fact margin of 8 rods against the 15 minimum at 01:22:30, the test at 01:23:04, AZ-5 at 01:23:40, 18 to 20 s for full rod
travel, and the displacer effect adding reactivity at the start of a scram.

Every constant in `plant/reactor.py` and `plant/rods.py` is either standard data (Keepin delayed-neutron groups, I-135 and Xe-135 half
lives) or the toy's own and unverified: the void and Doppler worths, the displacer worth, the rod-equivalent worth, the xenon worth,
the time constants of void and pump coastdown (set so that hold-only damage follows the test start by tens of seconds to a few minutes,
the real sequence taking about 40 s), the damage rule (fuel above 1500 C, or more than 8 nominal-power-seconds above full power) and
the power cap of 60 times nominal. The protections the plant has: power above 120 percent, drum pressure above 8 MPa or a level
outside plus or minus 45 cm, and turbogenerator disconnection; the last two are switches. The real thresholds were not found and are
the toy's.

Left out: spatial neutronics, per-channel thermal hydraulics, the per-channel flow and temperature instruments, a margin computer
cycle time, instrument offsets and drift, rod jamming during a scram, and any ECCS action.

## The three clocks

The plant steps on fixed substeps (10 s in the slow regime, 0.5 s in the fast one) and never waits. The decider's latency is turned
into plant time (`speed` simulated seconds per wall second; one for the fast regime of a poisoned start, 60 for the quiet regime): a
slow model is a late operator. `live.LiveRunner` runs the plant task and the decision tasks with asyncio, a speed knob, a timeout
that halts the run, and a record of any late answer that was discarded. Playback speed in an interface changes none of this.

## Not done

- The 12B decider (`strands-decider-12B-gemma4-v1-2610`): it needs the upstream `strands-decider` package from its git main in its own
  environment, pinned by commit, behind the same decider interface. Not integrated.
- Runs by the batch command: none are committed, and none had been run to a count when this was written.
- Agreement of the 2B decider with the upstream implementation on the same prompts; the answer quality at 1k, 2k and 4k tokens.
- The unified control room still reads `examples/rbmk_operator`; migrating it onto this package is open.
- Checking the toy's constants against INSAG-7 and against other reactor-dynamics codes.
