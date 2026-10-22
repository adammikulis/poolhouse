# RBMK-1000 unit control room

A 3D model of a Chernobyl-style RBMK-1000 unit control room that a person flies around in a browser, with its instruments
driven by the RBMK operator demo (`examples/rbmk_operator/`). **It is an interpretive reconstruction from published
descriptions and openly licensed photographs, not a survey of any room**, and the reactor behind the dials is the demo's
educational toy, not a plant model. The page says both in its header.

## Run it

The page imports the three.js build the repository already vendors under `src/poolhouse/ui/assets/`, so serve the
repository root (no CDN, no network):

```
python -m http.server 8000
```

Open `http://localhost:8000/examples/rbmk_control_room/viewer/index.html` (a recorded run of the real decision model) or add
`?feed=./feed/replay-1986-crew.json` (the demo's scripted 1986 crew, which presses AZ-5; no model in that feed).

Controls: W A S D move, Q E down and up, Shift fast, drag to look, click the floor to walk there, click any instrument for a
card (what it is, its range, how the demo models it, its value now). The buttons jump to named views: desk overview, AZ-5,
rod position panel, ORM terminal, reactor dials, annunciator, decision display. On a phone (or with the button) the page
orbits instead of flying. The page follows the light or dark setting of the device.

## Live link to the demo

The page reads one JSON feed. A replay is a finished run; a live feed is rewritten after every decision while the run goes on
and the page polls it every 1.5 s.

```
python examples/rbmk_control_room/export_feed.py --scenario pump_trip --operator model \
    --live examples/rbmk_control_room/viewer/feed/live.json \
    --replay examples/rbmk_control_room/viewer/feed/replay.json
```

then open `index.html?feed=./feed/live.json` while it runs. `--operator model` asks the released decision checkpoint under a
GPU hold at the Broker (`poolhouse.serve.gpu.hold`); when the model cannot answer the run stops and says why, with no
substitute. `--operator naive` and `--operator crew` run the demo's two scripted baselines, which are not models and are
labelled so. The committed `viewer/feed/replay.json` is one real run of `strands-decider-2B-hobson-v19` on the `pump_trip`
scenario; `replay-1986-crew.json` is the scripted crew on `historic_1986`.

The feed is the demo's `Result.public()` (`trace`, `decisions`, `events`, `finished`, `controller`, `scenario`) with `meta`
added: `meta.mode` (`live` or `replay`), `meta.model`, `meta.operator`, `meta.rods` (the id and kind of each rod). The
exporter drops each decision's `state_text` and `calls` to keep the file small. `instruments.json` lists the trace fields
the page needs.

Nothing pretends to be live. The header badge says LIVE, LIVE: STALLED (no new row for 12 s), LIVE RUN FINISHED or REPLAY,
and names the model taken from the answers in the feed (or says there is none). A missing, unreadable or malformed feed
shows a red NO FEED banner with the reason, the badge reads NO FEED, the needles rest and every lamp, tile and screen is
dark. There is no fallback data.

## What is driven by the demo

Every instrument in `instruments.json` reads a field of the feed row at the playhead (the newest row when live):

- 16 round dials (power channels A and B, demand, ORM, mean rod insertion, coolant flow, pressure, fuel temperature, void,
  xenon, iodine, reactivity, graphite-tip reactivity, hazard, drum level, the model's risk score);
- 3 strip recorders (power, fuel temperature and pressure, margin and rods);
- 211 rod-position cells in the round map, one per rod, from `rods[i]`;
- 26 annunciator tiles and 20 lamps, each lit by a rule on the row or on the latest decision (limits the demo enforces,
  its interlocks, the pumps, the protection switches, who chose the last action);
- the AZ-5 box: the guard lifts and the button lights while the shutdown is active;
- the ORM terminal (printed and true margin) and the wall decision display: the model's latest answers as probability bars,
  the requested and applied action, the source, interlocks, flags and scenario events.

Everything else (mimic windows, rod key matrix, switches, chairs, doors, the turbine hall behind the glazing) is static decor,
and its card says so. A dial with no value in the feed leaves its needle at rest instead of guessing. The replay bar steps
through a recorded run.

## How it was built

`generator/` holds the generator, run inside Blender (tested with 5.3) from a clean start:

```
blender --background --factory-startup --python examples/rbmk_control_room/generator/build_room.py -- \
    --out examples/rbmk_control_room/viewer/room.glb.gz
```

It builds every object from code (no imported models, textures or photographs): the faceted instrument wall, the sloped desk in
5 degree sections, chairs, desks, the ORM terminal, ceiling light panels, the glazed band and the 211 rod cells, merges the
static geometry per material, and exports `room.glb` and gzips it to `viewer/room.glb.gz` (about 0.2 MB, the file that is committed; a tracked file is limited to 1 MiB and the page decompresses it with `DecompressionStream`). Instruments are named nodes
(`INS_<id>`, `INS_<id>__face`, `INS_<id>__needle`, `ROD_R-ii-jj`, `CAM_<view>`); the viewer paints the dial faces, tile
labels, recorders, screens, floor, ceiling and mimic windows on canvases at load, so the labels always match
`instruments.json`. The GLB stores a hash of the generator and the instrument list; `tests/test_rbmk_control_room.py` fails
when the file is older than its source.

Reference photographs for the proportions and layout are listed in [REFERENCES.md](REFERENCES.md) (links only, none copied).
No existing 3D model of an RBMK control room under a licence that allows redistribution was found, so none is used.

## What is interpretive

The room's plan (a faceted arc wall behind a sloped desk, a round rod map at the centre), the section names, the instrument
placement, colours and proportions are read from published descriptions and a few openly licensed photographs. Real panel
layouts, lettering, meter types and the exact number and arrangement of annunciator windows differ and are not claimed.
Labels are in English. The rod map is a 17 by 17 grid of the 211 nearest cells in the demo's own order, not the plant's
channel map.

## Files

| Path | What |
|---|---|
| `instruments.json` | the instrument list, rules, zones, bookmarks and the cards' text |
| `generator/` | the Blender generator (`build_room.py`, `room_shell.py`, `room_wall.py`, `room_desk.py`, `roomkit.py`) |
| `export_feed.py` | runs a demo scenario and writes the feed |
| `shoot.py` | screenshots named views in a headless Chromium of its own |
| `viewer/` | the page: `index.html`, `main.js`, `drive.js`, `rules.js`, `feed.js`, `screens.js`, `paint.js`, `cards.js`, `fly.js`, `rods.js`, `style.css`, `room.glb.gz`, `feed/` |
| `viewer/check.mjs` | prints what every lamp and dial shows for each row of a feed (used by the tests) |

## Licences of vendored files

| File | Source | Licence |
|---|---|---|
| `viewer/vendor/loaders/GLTFLoader.js`, `viewer/vendor/utils/BufferGeometryUtils.js`, `viewer/vendor/utils/SkeletonUtils.js` | three.js 0.186.1 (`three/examples/jsm`), unmodified | MIT, `viewer/vendor/three.LICENSE` |
| three.js core, `OrbitControls.js`, `RoomEnvironment.js` | already vendored in `src/poolhouse/ui/assets/` (`vendor.json`), imported from there | MIT, `src/poolhouse/ui/assets/three.LICENSE` |

Everything else in this directory is Poolhouse code under the repository licence. See [LICENSES.md](LICENSES.md).

## Tests

```
scripts/test all tests/test_rbmk_control_room.py
```

Covers the GLB (valid, current, a node for every instrument and rod, camera marks), the instrument list against the real
feed (every dial, recorder and rule field exists; interlock and protection ids are the demo's; the replay is a model run
with no canned id), the rules evaluated in node against the feed's own values, the rod order against the demo's `Rods`,
and the page in a headless browser (REPLAY and the model id shown; a missing feed shows NO FEED and dark instruments).

Screenshots of named views: `python examples/rbmk_control_room/shoot.py --out DIR --row 30 overview dials az5 decision`.
