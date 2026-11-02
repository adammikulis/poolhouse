# RBMK control room: what the 2D views cost a browser, 2026-10-22

Command (as run then; the tool is now `python -m examples.rbmk.room.measure`, and the pages below are views of the one app): `python examples/rbmk_control_room/measure.py --seconds 10 --angle metal`, run from a checkout of branch
`feat/rbmk-control-room-2d` with the committed stills and the committed replay `viewer/feed/replay.json` (a recorded run of
`strands-decider-2B-hobson-v19` on `pump_trip`; no model ran during the measurement).

Device: Apple M4 Max, macOS 26.6, headless Chromium 151.0.7922.34 from Playwright with `--use-angle=metal` (the real GPU;
the GL renderer string reads "ANGLE Metal Renderer: Apple M4 Max"), viewport 1280 by 800, device scale factor 1. Other
processes on the device were not controlled.

Each page replays the feed at 5 rows/s (restarted when it ends) for 10 s, then sits paused for 10 s. The numbers are the CPU
time of each Chromium process type over the window as a percentage of one core, read through
`SystemInfo.getProcessInfo`. "GPU" is the CPU time of the GPU process, which executes the page's GL and compositing commands;
it is a proxy for GPU work, not GPU busy time (no timer query was available).

| Page | Replay: renderer / GPU process | Paused: renderer / GPU process |
|---|---|---|
| 3D room (`index.html`, overview) | 31.5 % / 39.3 % | 28.2 % / 37.9 % |
| 2D front wall (`room2d.html#front`) | 4.1 % / 3.9 % | 0.0 % / 0.0 % |
| 2D rod panel (`#rodpanel`) | 0.7 % / 1.0 % | 0.0 % / 0.0 % |
| 2D floor plan (`#plan`) | 0.7 % / 1.0 % | 0.0 % / 0.0 % |

The 3D room renders every frame whether or not anything changes. The 2D pages draw only when the feed row or a moving needle
changes something and stop their frame loop otherwise.

Overlay draw time (CPU, `performance.now()` around the draw call), from the same run, front wall with all 345 elements live
(68 instruments, 204 rod cells and 65 mimic windows visible from that view):

| | mean | p95 | max |
|---|---|---|---|
| a feed row: update every element, redraw the changed ones (about 11 of 345) | 0.41 ms | 0.7 ms | 3.1 ms |
| a full redraw of all 345 elements | 1.8 ms | 3.2 ms | 4.6 ms |
| mean over every frame of the replay window (1335 frames) | 0.09 ms | | |
