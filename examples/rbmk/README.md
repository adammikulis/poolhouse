# RBMK control room

A small decision model sits at a toy RBMK-1000 control desk and operates it, inside a 3D model of a Chernobyl-style unit control
room. **The room is an interpretive reconstruction from published descriptions and openly licensed photographs, not a survey of any
room, and the reactor behind the dials is an educational toy, not a plant model.** The page says both in its header.

One app, one address, one launch command. The room is the app: the instruments and the desk are the plant and the controls the
model uses, and the tools for looking at a run (the decision log, charts, what-if branches, the 1986 timeline, the poisoned starts)
are drawers over the room.

## Run it

One command starts the whole app: the 3D control room, the live runs, the recordings and every tool.

```sh
python -m examples.rbmk [--port 8765] [--store DIR] [--wait SECONDS]
```

It prints `RBMK control room: http://127.0.0.1:8765/` and serves that address on this machine only. Open it. You are in the
room: the desk and the instrument wall are the controls and readings of the toy plant, and the decision display on the
wall shows what the model last said, with the probability it gave each answer. Nothing runs until you choose a run in the
Run console (it opens first): a scenario and a controller, or a recording of a real model run. Until then every instrument is
dark and the banner says NO RUN.

The room has no second page. The header always shows the model id, `LIVE` or `RECORDED`, and the three clocks (plant
time, wall time, decision cadence and latency). The dock under the room holds what a person uses:

- Views: buttons that fly the camera to the desk overview, the AZ-5 button, the rod position panel, the ORM terminal, the
  reactor dials, the annunciator and the decision display. Fly with W A S D, Q and E, Shift and a drag; click the floor to walk
  there; click any instrument for a card (what it is, its range, how the simulator models it, its value now). On a phone the
  room orbits instead. The header button `Low-power 2D` shows the same room as fixed pictures with the live instruments drawn
  over them (floor plan, front wall and a close-up of each panel); it reads the same run and playhead and uses no WebGL.
- Playback: step back, play, step, speed, a scrubber and the clock of the tick on show. Space plays and pauses, `,` and `.`
  step. The room, the log and the charts all follow this one playhead, for a live run and a recording alike.
- Tools, each a drawer over the room: Run console (live or recorded, scenario, controller, the decider, memory variant and plant clock for a model run, start, stop, open a recording or
  a JSON file), Decision log (every decision with the questions, the probabilities and the desk line each chose; click one
  and the room's instruments, rods and wall display move to that moment), Charts, Core map (the full-size core cross-section,
  also opened from the card of a rod), Readings (the console as the decider was given it, beside the plant's true values), What-if (each action class applied once from a decision's state, no model asked), 1986 timeline, Poisoned starts (what each poisoned-start recording of the run store ended with) and
  Naive controller (plain policy code beside the model, never a model). Export JSON saves the run on show.

A live run with the decision model asks the released Strands decider (`strands-decider-2B-hobson-v19`, pointer backend) under the
GPU hold at the Broker. Setup, stated plainly: fetch the checkpoint once with `poolhouse-decide fetch`; the weights load on the first
run and the load time is not counted as decider delay. When the model cannot answer, the run halts with the reason in the banner;
the app never switches to a recording or a substitute by itself. The naive controller and the 1986 crew script are plain policy
code, labelled as not models, and need neither the model nor the GPU. A finished model run is saved to the local run store (below),
and the Recorded list opens any recording of the store by replaying it with no model.


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

Each line is checked by `tests/test_rbmk_engine.py`: the layering test, the prompt tests, the no-substitution test, the display
tests and the clock tests. Code that conflicts with a line is fixed, not the line.

## The chain

Plant (`plant/`, hidden truth, own fixed substeps) -> sensor model (`sensors/`, the only reader of the plant; produces an immutable
`SensorView`) -> prompt builder (`prompt/`, takes a `SensorView` and nothing else; every item has a provenance entry) -> decider
(`decider/`, the model asked class, then target, then step; always the most probable option) -> controls (`controls/`, maps the
choice to a desk action and applies it to the plant) -> recorder (`recorder.py`, events only) -> display (`display/`, all
presentation text, computed after the fact). `runner.py` runs the steps in simulated time; `live.py` schedules the same steps with
asyncio. `scenarios.py` and `starts.py` are data. The import rules are in the layering test.

## The run store and the command line

The model is the released Strands decider (`strands-decider-2B-hobson-v19`, pointer backend). Fetch it once with
`poolhouse-decide fetch`. Every command that asks the model holds the GPU through the Broker (`poolhouse.serve.gpu.hold`), loads the
weights once, and does not count the load as decider delay.

```sh
python -m examples.rbmk.run batch --count 300 --seed 1 --memory none history --wait 86400
python -m examples.rbmk.run summary
```

`batch` draws poisoned starts from the ranges below (the k-th start of seed s is always the same start), runs each for each memory
variant until the unit is damaged, shut down, or has its margin back and steady, or three simulated hours have passed, and skips a
start already in the run store, so it can be stopped and started again. Results go to the local run store,
`<poolhouse state>/rbmk/runs/<model>/<memory>/`, and are not part of the repository.

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
slow model is a late decider. `live.LiveRunner` runs the plant task and the decision tasks with asyncio, a speed knob, a timeout
that halts the run, and a record of any late answer that was discarded. Playback speed in an interface changes none of this.

## The room

A 3D model of a Chernobyl-style RBMK-1000 unit control room. **It is an interpretive reconstruction from published
descriptions and openly licensed photographs, not a survey of any room**, and the reactor behind the dials is the educational
toy above, not a plant model. The header says both.

Every instrument in `room/instruments.json` reads a field of the row of the run at the playhead (the newest row of a live run):

- 15 round dials (power channels A and B, demand, ORM, mean rod insertion, coolant flow, pressure, fuel temperature, void,
  xenon, iodine, reactivity, displacer reactivity, drum level, the model's expected risk rating);
- 3 strip recorders (power, fuel temperature and pressure, margin and rods);
- 211 rod-position cells in the round map, one per rod, from `rods[i]`;
- annunciator tiles and lamps, each lit by a rule on the row (the plant's limits, its protection switches, the pumps, the regulator, the turbine test);
- the AZ-5 box: the guard lifts and the button lights while the shutdown is active;
- the ORM terminal (printed and true margin) and the wall decision display: the model's latest answers as probability bars,
  the action it chose, when it was applied and the desk log line it wrote, and the scenario events.

Everything else (mimic windows, rod key matrix, switches, chairs, doors, the turbine hall behind the glazing) is static decor,
and its card says so. A dial with no value in the row leaves its needle at rest. With no run open the instruments are dark and
nothing is drawn in their place.

The room's plan (a faceted arc wall behind a sloped desk, a round rod map at the centre), the section names, the instrument
placement, colours and proportions are read from published descriptions and a few openly licensed photographs; real panel
layouts, lettering, meter types and the number of annunciator windows differ. Labels are in English. The rod map is a 17 by 17
grid of the 211 nearest cells in the simulator's own order, not the plant's channel map. Reference photographs are listed in
[REFERENCES.md](REFERENCES.md) (links only, none copied).

## Low-power 2D view

The header's `Low-power 2D` button (or `#plan`, `#front` and `#<panel>` on the address, which opens the app on that view without
ever loading the 3D room) shows the same room as fixed pictures with the live instruments drawn over them, so it needs no
WebGL and almost no GPU. Three modes, switched from the dock:

- **Floor plan**: the room from above (desk sections, instrument wall, engineers' desks, doors, the window band). Each panel
  is a dashed click target that turns amber or red when something in it is lit or out of range.
- **Front wall**: the whole instrument wall seen straight on from the crew's side, with the desk sections at the bottom.
  Click an instrument for its card, or a panel's outline for its close-up.
- **Panel close-up**: reactor dials, annunciator, decision display, strip recorders, desk lamps, rod position panel, AZ-5
  button, ORM terminal; the view fills with that panel.

The pictures (`room/stills/*.webp`) are renders of the 3D room's own geometry from fixed cameras with the moving parts left out
(faces, needles, rod cells, the AZ-5 cap and guard). `room/stills/views.json` holds, for every view, the image position of every
instrument face, rod cell and mimic window, projected from the geometry by the same Blender run. The view draws what the run
says at those positions: needles (eased only while they move), lamps and annunciator tiles, the 211 rod cells, strip recorders,
the decision display with the model's probability bars, the ORM terminal, the AZ-5 guard and button. Only the elements whose
picture changed are redrawn, and nothing runs while the tab is hidden or nothing changed. With no run open the instruments are
dark and the banner says NO RUN; a missing still is a loud banner, not a blank view.

Rebuild the stills (Blender 5.3, about ten minutes on a laptop CPU; also rewrites `room/stills/masks/`, the flat id pictures the
tests check the projected positions against):

```sh
blender --background --factory-startup --python examples/rbmk/generator/render_views.py -- \
    --out examples/rbmk/room/stills [--scale 0.5 --samples 32 --only front,dials]
```

`python -m examples.rbmk.room.measure --store DIR --recording FILE` (a recording of yours) reports what each view costs a browser
(CPU time per process over a replay, the overlay's draw time per frame); measurements are kept in
[docs/notes/2026-10-22-rbmk-room-2d-cost.md](../../docs/notes/2026-10-22-rbmk-room-2d-cost.md).

## How the room is built

`generator/` runs inside Blender (tested with 5.3) from a clean start:

```sh
blender --background --factory-startup --python examples/rbmk/generator/build_room.py -- --out examples/rbmk/room/room.glb.gz
```

It builds every object from code (no imported models, textures or photographs) and exports `room/room.glb.gz`, which the app
decompresses with `DecompressionStream`. Instruments are named nodes (`INS_<id>`, `INS_<id>__face`, `INS_<id>__needle`,
`ROD_R-ii-jj`, `CAM_<view>`); the app paints the dial faces, tile labels, recorders, screens, floor, ceiling and mimic windows on
canvases at load, so the labels always match `room/instruments.json`. The GLB stores a hash of the generator and the instrument
list; `tests/test_rbmk_room.py` fails when the file is older than its source.

## Layout

| Path | What |
|---|---|
| `plant/`, `sensors/`, `prompt/`, `decider/`, `controls/`, `display/` | the engine's chain: the hidden plant, the sensor model, the prompt builder, the decider and the baselines, the desk controls, the presentation text and the run document |
| `runner.py`, `live.py`, `recorder.py`, `scenarios.py`, `starts.py`, `whatif.py` | the run in simulated time, the same run on separate clocks (asyncio), the recording and its replay, the scenarios and drawn starts as data, the what-if branches |
| `run.py` | the command line: record, batch, live, replay, export, baseline, summary, selftest |
| `SCHEMA.md` | the recording and run documents |
| `web/` | the server (`server.py`, `hub.py`, `jobs.py`, `recordings.py`) and the app's page, scripts and styles (`static/`) |
| `room/` | the room in the browser: `main.js` (feed, view switch), `feed.js` (the run as the instruments read it), the 3D view (`room3d.js`, `drive.js`, `fly.js`, `paint.js`), the 2D view (`room2d.js`, `overlay.js`, `paint2d.js`, `state.js`, `loop.js`, `bench.js`, `stills/`), shared `rules.js`, `rods.js`, `faces.js`, `screens.js`, `cards.js`, `instruments.json`, `room.glb.gz`, `vendor/`, and the tools `check.mjs`, `shoot.py`, `measure.py` |
| `generator/` | the Blender generator of the room and of the 2D stills (`render_views.py`, `views.py`) |
| `__main__.py` | `python -m examples.rbmk` |

`python -m examples.rbmk.room.shoot --out DIR --store DIR --recording FILE --row 30 overview az5 decision` takes screenshots of named
views in a headless Chromium of its own (a recording of yours; without `--recording` the room is shown with no run).

## Tests

```sh
scripts/test all tests/test_rbmk_engine.py tests/test_rbmk_room.py tests/test_rbmk_room_2d.py tests/test_rbmk_web.py tests/test_rbmk_app.py
scripts/test all tests/test_rbmk_engine_real.py tests/test_rbmk_web.py tests/test_rbmk_app.py tests/test_rbmk_room.py --real-model
```

`test_rbmk_engine.py` covers the plant, the one-way chain (a layering test), the prompt, the controls, the display text, the three
clocks and the recording replay with no model. `test_rbmk_room.py` covers the GLB (valid, current, a node for every instrument and
rod, camera marks), the instrument list against a hub run, the rules evaluated in node and the rod order. `test_rbmk_room_2d.py`
covers the stills (current with the generator, under the file limit), a rect for every instrument in the front view and for all 211
rods in the rod panel, the projected rects against the id masks rendered from the same cameras, the three modes and the cards in a
headless browser, and that an idle or hidden view draws nothing. `test_rbmk_web.py` covers the server, its routes and the
documents. `test_rbmk_app.py` drives the app in a headless Chromium: no run leaves the room dark, a decision picked in the log is
the state the room shows, a live run drives the room, a phone width holds every tool, and nothing is graded or given away before the
run reaches it. A test that needs a run takes one from the app's own hub (`tests/rbmk_run.py`): a plain-code controller, or, in the
tests marked `real_model`, the real decision model on the seven-tick scenario while the test holds the GPU; a missing model fails
the test naming the command that fixes it. No run result is committed to the repository.

## Licences of vendored files

| File | Source | Licence |
|---|---|---|
| `room/vendor/loaders/GLTFLoader.js`, `room/vendor/utils/BufferGeometryUtils.js`, `room/vendor/utils/SkeletonUtils.js` | three.js 0.186.1 (`three/examples/jsm`), unmodified | MIT, `room/vendor/three.LICENSE` |
| three.js core, `OrbitControls.js`, `RoomEnvironment.js` | already vendored in `src/poolhouse/ui/assets/` (`vendor.json`), served from there | MIT, `src/poolhouse/ui/assets/three.LICENSE` |

Everything else in this directory is Poolhouse code under the repository licence. See [LICENSES.md](LICENSES.md).

## Not done

- The 12B decider (`strands-decider-12B-gemma4-v1-2610`): it needs the upstream `strands-decider` package from its git main in its own
  environment, pinned by commit, behind the same decider interface. Not integrated.
- Runs by the batch command: none are committed, and none had been run to a count when this was written.
- Agreement of the 2B decider with the upstream implementation on the same prompts; the answer quality at 1k, 2k and 4k tokens.
- Checking the toy's constants against INSAG-7 and against other reactor-dynamics codes.
- The Run console is a drawer in the dock, not an object on the desk in the 3D room.
- The 12B Gemma decider and the state memory variant are not integrated; the Decider and Memory selects list `DECIDERS` and `MEMORIES` of `decider/model.py`.
- The wall dials read the plant's true values; the Readings drawer shows the console as the decider was given it.

