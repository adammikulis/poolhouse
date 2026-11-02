/* The run panel of the desk: what its keys do, and the lines its display shows. The keys and the display are objects of the room. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';
  const S = RB.state;
  const CONTROLLERS = [['model', 'decision model'], ['naive', 'naive controller (not a model)'], ['crew', '1986 crew script (not a model)']];
  const SPEEDS = [0.5, 1, 2, 5, 10, 25];
  
  const D = { version: 0, scenario: 0, controller: 0, decider: 0, memory: 0, clock: 0, recording: 0, batch: null, notice: '',
    batchCount: 300, batchSeed: 1, batchMemories: null };
  RB.desk = D;
  const bump = function () { D.version += 1; RB.touch(); };
  const cycle = function (i, n, dir) { return n ? (i + dir + n) % n : 0; };

  D.scenarios = function () { return S.info ? S.info.scenarios : []; };
  D.scenarioInfo = function () { return D.scenarios()[D.scenario] || null; };
  D.controllers = function () { const s = D.scenarioInfo(); return CONTROLLERS.filter(function (c) { return c[0] !== 'crew' || (s && s.crew); }); };
  D.recordings = function () { return (S.recordings || []).filter(function (r) { return r.ok; }); };

  D.init = function () {
    const i = D.scenarios().findIndex(function (s) { return s.key === 'pump_trip'; });
    D.scenario = i < 0 ? 0 : i;
    D.clock = Math.max(0, S.info.speeds.indexOf(S.info.default_speed));
    bump();
  };

  D.options = function () {
    const c = D.controllers()[D.controller % D.controllers().length];
    return { scenario: D.scenarioInfo().key, controller: c[0], decider: S.info.deciders[D.decider].id, memory: S.info.memories[D.memory], speed: S.info.speeds[D.clock] };
  };

  D.lines = function () {
    if (!S.info) { return []; }
    const s = D.scenarioInfo(), c = D.controllers()[D.controller % D.controllers().length], run = S.main, rec = D.recordings()[D.recording];
    const model = c[0] === 'model';
    const lines = [{ label: 'SOURCE', value: S.mode === 'live' ? 'LIVE RUN' : 'RECORDED RUN' }];
    if (S.mode === 'live') {
      lines.push({ label: 'SCENARIO', value: s ? s.title : '-' }, { label: 'CONTROLLER', value: c[1] });
      lines.push({ label: 'DECIDER', value: model ? S.info.deciders[D.decider].label : '-' }, { label: 'MEMORY', value: model ? S.info.memories[D.memory] : '-' });
      lines.push({ label: 'CLOCK', value: model ? S.info.speeds[D.clock] + ' plant s per s' : 'instant' });
    } else {
      const n = D.recordings().length;
      lines.push({ label: 'RECORDING', value: rec ? (D.recording + 1) + ' of ' + n + ': ' + rec.title : (n ? '-' : 'none in the run store') });
      lines.push({ label: 'MODEL', value: rec ? rec.model + ', memory ' + rec.memory : '-' });
    }
    lines.push({ label: 'RUN', value: !run ? 'none' : run.live ? 'running, tick ' + (run.trace.length ? run.trace[run.last].tick : 0) : 'finished, ' + run.trace.length + ' ticks', alert: !!(run && run.status === 'error') });
    lines.push({ label: 'PLAYBACK', value: (S.playing ? 'playing' : 'paused') + ', ' + SPEEDS[SPEEDS.indexOf(S.speed) < 0 ? 2 : SPEEDS.indexOf(S.speed)] + ' ticks per s' + (S.pauseAtCaptions ? ', stops at captions' : '') });
    if (D.batch) { lines.push({ label: 'BATCH', value: D.batch.status === 'running' ? D.batch.done + ' of ' + D.batch.total + ' poisoned starts' : D.batch.status + ', ' + D.batch.done + ' of ' + D.batch.total, alert: D.batch.status === 'error' }); }
    if (D.notice) { lines.push({ label: 'NOTE', value: D.notice, alert: true }); }
    return lines;
  };

  D.lit = function (id) {
    switch (id) {
      case 'K_START': return !!(S.main && S.main.live);
      case 'K_STOP': return !!(S.main && S.main.live) || !!(D.batch && D.batch.status === 'running');
      case 'K_PLAY': return S.playing;
      case 'K_SOURCE': return S.mode === 'recorded';
      case 'K_BATCH': return !!(D.batch && D.batch.status === 'running');
      case 'K_CAPTION': return !!S.pauseAtCaptions;
      default: return false;
    }
  };

  function startBatch() {
    const memories = D.batchMemories || S.info.memories;
    RB.api('/api/batch', { count: D.batchCount, seed: D.batchSeed, memories: memories, decider: S.info.deciders[D.decider].id }).then(function (job) {
      D.batch = { jobId: job.id, status: 'running', done: 0, total: D.batchCount * memories.length };
      const es = new EventSource('/api/jobs/' + job.id + '/events');
      es.onmessage = function (m) {
        const ev = JSON.parse(m.data), b = D.batch;
        if (ev.type === 'plan') { b.total = ev.total; b.done = ev.skipped; } else if (ev.type === 'item_done') { b.done += 1; b.last = ev; }
        else if (ev.type === 'status') { b.status = ev.status === 'done' ? 'finished' : ev.status; b.error = ev.error; es.close(); if (ev.error) { RB.fail('Batch: ' + ev.error); } }
        bump();
      };
      es.onerror = function () { if (D.batch && D.batch.status !== 'running') { es.close(); } };
      bump();
    }).catch(function (e) { RB.fail('Batch refused: ' + e.message); });
  }

  function open() {
    const rec = D.recordings()[D.recording];
    if (rec) { RB.openRecording(rec.name); } else { RB.fail('There is no recording to open in the run store.'); }
  }

  const ACTIONS = {
    scenario_prev: function () { D.scenario = cycle(D.scenario, D.scenarios().length, -1); D.controller = 0; },
    scenario_next: function () { D.scenario = cycle(D.scenario, D.scenarios().length, 1); D.controller = 0; },
    controller_prev: function () { D.controller = cycle(D.controller, D.controllers().length, -1); },
    controller_next: function () { D.controller = cycle(D.controller, D.controllers().length, 1); },
    decider_prev: function () { D.decider = cycle(D.decider, S.info.deciders.length, -1); },
    decider_next: function () { D.decider = cycle(D.decider, S.info.deciders.length, 1); },
    memory_prev: function () { D.memory = cycle(D.memory, S.info.memories.length, -1); },
    memory_next: function () { D.memory = cycle(D.memory, S.info.memories.length, 1); },
    clock_prev: function () { D.clock = cycle(D.clock, S.info.speeds.length, -1); },
    clock_next: function () { D.clock = cycle(D.clock, S.info.speeds.length, 1); },
    source: function () { RB.setMode(S.mode === 'live' ? 'recorded' : 'live'); },
    recording_prev: function () { D.recording = cycle(D.recording, D.recordings().length, -1); },
    recording_next: function () { D.recording = cycle(D.recording, D.recordings().length, 1); },
    open: open,
    start: function () { if (S.mode === 'recorded') { open(); } else { RB.startRun(D.options()); } },
    stop: function () { RB.stopRun(); if (D.batch && D.batch.status === 'running') { RB.api('/api/stop', { job: D.batch.jobId }).catch(function (e) { RB.fail(e.message); }); } },
    batch: function () { if (D.batch && D.batch.status === 'running') { ACTIONS.stop(); } else { startBatch(); } },
    export: function () { RB.exportJson(); },
    back: function () { S.playing = false; S.cursor = Math.max(0, Math.round(S.cursor) - 1); },
    step: function () { S.playing = false; S.cursor = Math.min(S.main ? S.main.last : 0, Math.round(S.cursor) + 1); },
    play: function () { S.playing = !S.playing; },
    end: function () { S.cursor = S.main ? S.main.last : 0; },
    speed_prev: function () { const i = SPEEDS.indexOf(S.speed); S.speed = SPEEDS[Math.max(0, (i < 0 ? 2 : i) - 1)]; },
    speed_next: function () { const i = SPEEDS.indexOf(S.speed); S.speed = SPEEDS[Math.min(SPEEDS.length - 1, (i < 0 ? 2 : i) + 1)]; },
    caption: function () { S.pauseAtCaptions = !S.pauseAtCaptions; },
  };

  /* A key of the run panel was pressed, in the 3D room or in a 2D view, or its shortcut on the keyboard. */
  D.press = function (action) {
    const fn = ACTIONS[action];
    if (!fn || !S.info) { return; }
    D.notice = '';
    if ((action === 'recording_prev' || action === 'recording_next' || action === 'open') && S.mode === 'live') { D.notice = 'Set SOURCE to RECORDED first.'; bump(); return; }
    fn();
    bump();
  };

  RB.onRecordings = function () { D.recording = Math.min(D.recording, Math.max(0, D.recordings().length - 1)); bump(); };
})(window.RB);
