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

`batch` draws poisoned starts from the ranges below (the k-th start of seed s is always the same start) and runs each for each
memory variant until one of three outcomes: fuel damaged (the fuel temperature passes its limit), the unit shut down (AZ-5 pressed
and every free rod fully in), or the margin restored and steady (true margin at least 15 rod-equivalents, no AZ-5, power constant
within the power channels' 1 percent noise over two consecutive ticks). A run that reaches none of them stops at 12 simulated hours,
by which time the xenon has passed its peak (8 to 11 h after a shutdown, checked in the xenon test). It skips a start already in the
run store, so it can be stopped and started again. Results go to the local run store,
`<poolhouse state>/rbmk-experiment/runs/<model>/<memory>/`, and are not part of the repository.

| range | drawn uniformly from (relative power; rod-equivalents; iodine index) | source |
|---|---|---|
| power | 0.0094 to 0.0625 (30 to 200 MW of 3200) | 30 MW after 00:28 and stabilised at 200 MW at 01:03 (INSAG-7 annex I, table I-I) |
| margin | 7 to 8 | 8 rod-equivalents at 01:22:30 (table I-I); 7 in the positive-scram analysis (annex I section 1-4) |
| iodine | 0.9 to 1.4 | the toy's own; unverified |

The manual rods that are inserted are a random pattern for each start. The regulator is off, eight pumps run at a throttle of 0.9
(the toy's own), the drum, turbogenerator and ECCS protections are off (as before the test), and a supervisor order to begin the turbine
rundown test arrives 4 seconds in (the toy's own); the model decides whether to start it. Flux tilt is zero.

Other commands: `record` (named scenarios), `live` (asyncio runner with a speed knob), `replay` (no model; checks the digest),
`export` (the full run document), `baseline` (the scripted 1986 crew or the naive rule; neither is a model), `selftest`.
`SCHEMA.md` describes the recording and the run document.

## What the model is given

Only a `SensorView`. The table lists every item of the prompt and where it comes from. The source is INSAG-7 (IAEA Safety Series
75-INSAG-7, 1992), fetched as `https://www-pub.iaea.org/MTCD/publications/PDF/Pub913e_web.pdf` (the IAEA catalogue page and a fetch
tool answered 402; a plain `curl` of that file worked); section numbers below are its own. Anything marked unverified is the toy's.

| item | kind | provenance |
|---|---|---|
| `clock` | instrument | a control-room clock; the format is the toy's |
| `briefing` | briefing | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992): ORM 26 to 30 rods at rated power, authorization below 26, immediate shutdown at 15 (annex II, operating procedures), the test at 700 to 1000 MW thermal and the four pumps run down on the turbogenerator (sections 2.9 and 3); the list of desk controls is the toy's |
| `demand` | briefing | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992): the test programme power; each scenario's demand schedule is the toy's |
| `ion_chambers` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) section 2.4: below 10 percent of nominal power control relied on the ex-core detectors (lateral ionisation chambers); two channels shown; their lag, noise and sampling are the toy's, unverified |
| `heat_balance` | instrument | unverified: the toy's own value or presentation: a heat-balance power with a 120 s lag |
| `orm_printout` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) section 2.5: the computer took 10 to 15 min to cycle through its measurements and calculate the ORM; section 3 item 5: it became unreliable during the test. The toy refreshes every 12.5 min with the true margin |
| `rod_indicators` | instrument | unverified: the toy's own value or presentation: INSAG-7 annex I section 1-4.3.2 says only nine of the 211 rod positions were recorded by the data recorder; the console indicators are not described. The panel shows counts and means by type |
| `regulator` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) section 2.4 and annex II: automatic power regulators and local automatic control; one regulator with on/off and a setpoint is a simplification |
| `pumps` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) section 2.9: eight main circulation pumps, 8000 m3/h each; speed in percent is the toy's |
| `flow_meter` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) annex II: a ball-type flow meter on every channel; one lagged core flow summary is the toy's |
| `drums` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) section 2.9 and annex I: steam separator drums with level (mm) and pressure; the lags are the toy's |
| `steam_header` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) section 2.9: two steam headers; the reading is the toy's |
| `feedwater` | instrument | unverified: the toy's own value or presentation: feedwater flow |
| `turbine_test` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) annex I table I-I: stop valves of turbogenerator 8 closed at 01:23:04 and four pumps ran down; the three states shown are the toy's |
| `protection_switches` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) sections 2.7 and 3: protections could be blocked (ECCS 14:00, turbogenerator trip signal 00:43:27, steam drum protection); the three switches and their ON/OFF presentation are the toy's |
| `az5` | instrument | INSAG-7 (IAEA Safety Series 75-INSAG-7, 1992) section 2.3: the AZ-5 (EPS-5) button |
| `desk_log` | instrument | unverified: the toy's own value or presentation: the last five desk actions in plain words |
| `strip_recorders` | instrument | unverified: the toy's own value or presentation: INSAG-7 annex I section 1-4.3 describes recording systems; the window and speed here are the toy's |

The strip-recorder trends appear only in the `history` memory variant. A `state` variant (carrying the model's recurrent state
between decisions) is not implemented.

## The toy plant

Six-group point kinetics with the prompt-jump approximation, the I-135 and Xe-135 chain with burnout, 211 rods each with a position,
a drive speed, a type and a worth that depends on its place in the flux shape, eight pumps, a void fraction with a positive void
coefficient that grows as the margin falls, a Doppler term, decay heat, drum pressure and level, the turbine rundown test, three
protection switches, and the positive scram effect: the graphite displacers add reactivity per rod as rods enter from the withdrawn
end, scaled by how far the margin is below about 22 rod-equivalents.

From INSAG-7: 3200 MW thermal, 211 control and protection rods of which 24 are shortened absorbers inserted from below and 12 are
automatic rods without displacers (sections 2.2, annex II); full insertion of the emergency rods takes 18 s (2.3); the ORM is the
number of equivalent fully inserted rods, 26 to 30 at rated power, authorization needed below 26, immediate shutdown at 15 (annex II,
operating procedures); the computer that calculated it took 10 to 15 min per cycle and was unreliable during the test (2.5, 3);
the void coefficient is +2.0 to +2.5 x 10^-4 per percent void (delta k over k) in the steady refuelling regime and a total loss of
coolant adds +4 to +5 beta (2.1; the toy's void worth of 4.5 dollars per unit void fraction follows that); the fuel temperature
coefficient is -1.2 x 10^-5 per C (table II-I); with the margin at 15 rods the first six seconds of an AZ-5 add less than beta of negative
reactivity and at 7 rods the first eight seconds add positive reactivity (annex I section 1-3); the displacers add reactivity as
rods leave their upper stop (2.2); all eight pumps ran, four of them on the turbogenerator that was run down (2.9); the chronology
(annex I, table I-I): ECCS disconnected 14:00 on 25 April, power cut to 30 MW at 00:28, the drum pressure protection trip point
changed from 55 to 50 kg/cm2 at 00:36:24, the turbogenerator trip signal blocked at 00:43:35, power stabilised at 200 MW at 01:03,
the post-accident calculation of 8 rods at 01:22:30, stop valves closed and the four pumps run down at 01:23:04, AZ-5 at 01:23:40, the
power-rise protection signals at 01:23:43 with a period under 20 s and power above 530 MW, and rods stopped short of the lower stop at
01:24.

Every constant in `plant/reactor.py` and `plant/rods.py` is either standard data (Keepin delayed-neutron groups, I-135 and Xe-135 half
lives) or the toy's own and unverified: the void and Doppler worths, the displacer worth, the rod-equivalent worth, the xenon worth,
the time constants of void and pump coastdown (set so that hold-only damage follows the test start by tens of seconds to a few minutes,
the real sequence taking about 40 s). Damage is the fuel temperature passing 1204 C, the US light-water peak cladding temperature limit
(10 CFR 50.46(b)(1), 2200 F), used as an analogy and unverified for the RBMK. There is no cap on power: the prompt-critical growth is
limited only by the numerical guards named in `plant/reactor.py`, so the peak a run reports at the moment of damage is a toy output and not
a claim about the real accident. The protections the plant has: a reactor period under 20 s (INSAG-7, as above), power above 120 percent (the trip point is not given in INSAG-7: unverified), the low
steam drum level at -600 mm and the drum pressure drop at 55 kg/cm2 (both sourced; switchable together), and turbogenerator disconnection (a switch).

Left out: spatial neutronics, per-channel thermal hydraulics, the graphite temperature coefficient and the fast power coefficient of table II-I
(the toy keeps one lumped Doppler term of 2.2 dollars per unit heat so that the plant is controllable; INSAG-7's fuel coefficient would give
about 1.3), instrument offsets and drift, rods stopping short of the lower stop, and any ECCS action.

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
- Checking the rest of the toy's constants against INSAG-7 and against other reactor-dynamics codes.
