/* The page: header, run picker, playback, the animation loop and the tabs. */
window.RB = window.RB || {};
(function (RB) {
  'use strict';
  const S = RB.state, $ = function (id) { return document.getElementById(id); };
  const store = function (k, v) { try { if (v === undefined) { return localStorage.getItem(k); } localStorage.setItem(k, v); } catch (e) { return null; } return v; };
  let core, panel, chartCv = {}, chartDefs, lastCursor = -1, lastLog = '', lastDetail = '', lastCaption = -1;

  /* ---------------------------------------------------------------------------------- runs */
  RB.setMain = function (run) {
    S.main = run; S.cursor = 0; S.selected = -1; S.naive = null; S.banner = ''; RB.notify();
  };

  RB.startRun = async function () {
    const key = $('scenario').value, controller = $('controller').value;
    try {
      const job = await RB.api('/api/run', { scenario: key, controller: controller, decider: $('decider').value, memory: $('memory').value, speed: +$('speedLive').value });
      const run = new RB.Run('main'); run.origin = 'live'; run.follow(job.id);
      RB.setMain(run); S.playing = true;
      if ($('alsoNaive').checked && controller === 'model') {
        const nj = await RB.api('/api/run', { scenario: key, controller: 'naive' });
        S.naive = new RB.Run('naive'); S.naive.follow(nj.id);
      }
    } catch (e) { RB.fail('Run refused: ' + e.message); }
  };

  RB.stopRun = function () {
    [S.main, S.naive].forEach(function (r) { if (r && r.live) { RB.api('/api/stop', { job: r.jobId }).catch(function (e) { RB.fail(e.message); }); } });
  };

  RB.openDocument = function (doc, label, origin, name) {
    const run = new RB.Run('main'); run.loadDocument(doc, label, origin); run.recordingName = name || null;
    run.jobId = null; RB.setMain(run); S.playing = false;
  };

  RB.openRecording = async function (name) {
    try { const r = await RB.api('/api/recording?name=' + encodeURIComponent(name)); RB.openDocument(r.doc, r.label, 'recorded', name); }
    catch (e) { RB.fail('Cannot open ' + name + ': ' + e.message); }
  };

  RB.openFile = async function (file) {
    try {
      const res = await fetch('/api/load', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: await file.text() });
      const data = await res.json();
      if (!res.ok) { throw new Error(data.error); }
      RB.openDocument(data.doc, data.label + ' (file ' + file.name + ')', 'recorded');
    } catch (e) { RB.fail('Cannot open ' + file.name + ': ' + e.message); }
  };

  RB.seekTick = function (tick) {
    const run = S.main; if (!run) { return; }
    const i = run.trace.findIndex(function (r) { return r.tick >= tick; });
    S.cursor = i < 0 ? run.last : i; RB.touch();
  };
  RB.pick = function (d) { S.selected = d.i; S.playing = false; RB.seekTick(RB.snapTick(d)); RB.notify(); };

  RB.exportJson = async function () {
    const run = S.main; if (!run) { RB.fail('Nothing to export yet.'); return; }
    try {
      const doc = run.jobId ? await RB.api('/api/jobs/' + run.jobId + '/document') : run.doc;
      if (!doc) { throw new Error('this run has no document'); }
      const name = 'rbmk-' + doc.meta.scenario.key + '-' + doc.meta.controller + (doc.meta.memory && doc.meta.memory !== 'none' ? '-' + doc.meta.memory : '') + '-' + (doc.meta.backend.recorded_at || 'run') + '.json';
      const a = RB.el('a', { href: URL.createObjectURL(new Blob([JSON.stringify(doc, null, 1)], { type: 'application/json' })), download: name });
      document.body.appendChild(a); a.click(); a.remove();
    } catch (e) { RB.fail('Export failed: ' + e.message); }
  };

  /* ------------------------------------------------------------------------------- header */
  function headerText() {
    const info = S.info, m = info.model, run = S.main;
    if (run) {
      const label = run.live ? 'LIVE, running' : run.origin === 'live' ? 'LIVE run finished' : 'RECORDED';
      const plain = run.controller === 'crew' ? 'none: 1986 crew script, not a model' : run.controller === 'naive' ? 'none: naive controller, plain code, not a model' : '';
      return { id: plain || run.model || (run.origin === 'live' ? m.pinned + ' (pinned, answers not yet received)' : 'no model id in this run'), state: label, cls: run.origin === 'live' ? 'live' : 'rec', label: run.label };
    }
    if (S.mode === 'live') { return { id: m.id || (m.pinned + (m.available ? ' (pinned, loads at the first run)' : ' (pinned, NOT available)')), state: 'LIVE: no run', cls: 'live', label: '' }; }
    return { id: 'no recording open', state: 'RECORDED: no run', cls: 'rec', label: '' };
  }

  function bannerText(run) {
    if (run && run.status === 'error') { return 'RUN HALTED: ' + run.error + '. No action was taken after this point.'; }
    if (run && run.status === 'stopped') { return 'Run stopped by the person at tick ' + (run.trace.length ? run.trace[run.trace.length - 1].tick : 0) + '.'; }
    const parts = [];
    if (S.banner) { parts.push(S.banner); }
    if (S.mode === 'live' && !S.info.model.available) { parts.push('The decision model cannot run here: ' + S.info.model.why + '. Model runs will halt with this error; naive and crew runs and recordings still work.'); }
    if (!run) { parts.push('NO RUN: every instrument in the room is dark. Open the Run console to start a scenario or open a recording.'); }
    return parts.join(' ');
  }

  function updateHeader() {
    const h = headerText();
    $('modelid').textContent = h.id;
    $('modestate').textContent = h.state; $('modestate').className = 'chip ' + h.cls + (S.main && S.main.live ? ' pulse' : '');
    $('runlabel').textContent = h.label;
    const banner = bannerText(S.main);
    $('banner').textContent = banner; $('banner').style.display = banner ? '' : 'none';
    $('scndesc').textContent = S.mode === 'live' ? (RB.scenarioInfo($('scenario').value) || {}).description || '' : '';
  }
  RB.scenarioInfo = function (key) { return S.info.scenarios.find(function (s) { return s.key === key; }); };

  function buildPicker() {
    const sel = $('scenario'), groups = {};
    S.info.scenarios.forEach(function (s) { (groups[s.group] = groups[s.group] || []).push(s); });
    const names = { normal: 'Normal states', faults: 'Faults', poisoned: 'Xenon-poisoned starts', history: '1986 replay', fixture: 'Short test run' };
    Object.keys(groups).forEach(function (g) {
      const og = RB.el('optgroup', { label: names[g] || g });
      groups[g].forEach(function (s) { og.appendChild(RB.el('option', { value: s.key, text: s.title })); });
      sel.appendChild(og);
    });
    sel.addEventListener('change', syncController);
    $('controller').addEventListener('change', syncModelBoxes);
    S.info.deciders.forEach(function (d) { $('decider').appendChild(RB.el('option', { value: d.id, text: d.label })); });
    S.info.memories.forEach(function (m) { $('memory').appendChild(RB.el('option', { value: m, text: m })); });
    S.info.speeds.forEach(function (v) { const o = RB.el('option', { value: v, text: v + ' plant s per s' }); o.selected = v === S.info.default_speed; $('speedLive').appendChild(o); });
    syncController();
  }

  function syncController() {
    const s = RB.scenarioInfo($('scenario').value), c = $('controller'), keep = c.value;
    c.replaceChildren(RB.el('option', { value: 'model', text: 'Decision model' }), RB.el('option', { value: 'naive', text: 'Naive controller (policy code, not a model)' }));
    if (s && s.crew) { c.appendChild(RB.el('option', { value: 'crew', text: '1986 crew script (not a model)' })); }
    if (keep && Array.from(c.options).some(function (o) { return o.value === keep; })) { c.value = keep; }
    syncModelBoxes();
    RB.touch();
  }

  function syncModelBoxes() {
    ['deciderBox', 'memoryBox', 'speedBox'].forEach(function (id) { $(id).style.display = $('controller').value === 'model' ? '' : 'none'; });
  }

  RB.loadRecordings = async function () {
    S.recordings = await RB.api('/api/recordings');
    const sel = $('recording'); sel.replaceChildren();
    S.recordings.filter(function (r) { return r.ok; }).forEach(function (r) {
      sel.appendChild(RB.el('option', { value: r.name, text: r.title + ' | ' + r.label }));
    });
    const bad = S.recordings.filter(function (r) { return !r.ok; });
    $('badfiles').textContent = bad.length ? 'Could not show: ' + bad.map(function (r) { return r.name + ' (' + r.error + ')'; }).join('; ') : '';
    $('norecs').textContent = S.recordings.length ? '' : 'The recordings folder (' + S.info.recordings_dir + ') has no files.';
    RB.touch();
  };

  RB.setMode = setMode;
  const frameHooks = [];
  RB.onFrame = function (fn) { frameHooks.push(fn); };
  function setMode(mode) {
    S.mode = mode; S.main = null; S.naive = null; S.banner = ''; S.playing = false;
    document.body.dataset.mode = mode;
    $('liveBar').style.display = mode === 'live' ? '' : 'none'; $('recBar').style.display = mode === 'recorded' ? '' : 'none';
    ['modeLive', 'modeRec'].forEach(function (id) { $(id).classList.toggle('on', (id === 'modeLive') === (mode === 'live')); });
    if (mode === 'recorded') { RB.loadRecordings().catch(function (e) { RB.fail(e.message); }); }
    RB.notify();
  }

  /* ------------------------------------------------------------------------------ playback */
  function controls() {
    $('play').onclick = function () { S.playing = !S.playing; RB.touch(); };
    $('back').onclick = function () { S.playing = false; S.cursor = Math.max(0, Math.round(S.cursor) - 1); RB.touch(); };
    $('fwd').onclick = function () { S.playing = false; S.cursor = Math.min(S.main ? S.main.last : 0, Math.round(S.cursor) + 1); RB.touch(); };
    $('speed').onchange = function () { S.speed = +$('speed').value; };
    $('scrub').oninput = function () { S.playing = false; S.cursor = +$('scrub').value; RB.touch(); };
    $('pauseCap').onchange = function () { S.pauseAtCaptions = $('pauseCap').checked; };
    $('colormode').onchange = function () { core.mode = $('colormode').value; RB.touch(); };
    $('export').onclick = RB.exportJson; $('start').onclick = RB.startRun; $('stop').onclick = RB.stopRun;
    $('openrec').onclick = function () { if ($('recording').value) { RB.openRecording($('recording').value); } };
    $('file').onchange = function () { if ($('file').files[0]) { RB.openFile($('file').files[0]); $('file').value = ''; } };
    $('modeLive').onclick = function () { setMode('live'); }; $('modeRec').onclick = function () { setMode('recorded'); };
    $('theme').onclick = function () {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next; store('rbmk-theme', next); RB.touch();
    };
    document.addEventListener('keydown', function (e) {
      if (/INPUT|SELECT|TEXTAREA|BUTTON/.test((e.target || {}).tagName || '')) { return; }
      if (e.key === ' ') { e.preventDefault(); $('play').click(); } else if (e.key === '.') { $('fwd').click(); } else if (e.key === ',') { $('back').click(); }
    });
  }

  function advance(dt) {
    const run = S.main;
    if (!run || !S.playing || !run.trace.length) { return; }
    const prev = S.cursor, end = run.last;
    S.cursor = Math.min(S.cursor + S.speed * dt, end);
    if (S.pauseAtCaptions) {
      const hit = run.captions.find(function (c) { const i = run.trace.findIndex(function (r) { return r.tick >= c.tick; }); return i > prev && i <= S.cursor; });
      if (hit) { S.cursor = run.trace.findIndex(function (r) { return r.tick >= hit.tick; }); S.playing = false; }
    }
    if (S.cursor >= end && !run.live) { S.playing = false; }
    RB.touch();
  }

  /* ---------------------------------------------------------------------------- rendering */
  function activeDecision(run, frame) {
    let d = null;
    run.decisions.forEach(function (x) { if (x && x.tick <= frame.tick) { d = x; } });
    return d;
  }

  function renderState() {
    const run = S.main, frame = run && run.trace.length ? RB.frameAt(run, S.cursor) : null;
    panel.update(frame, run);
    if (!run || !frame) { $('clock').textContent = run && run.live ? 'waiting for the GPU hold and the model...' : ''; $('caption').style.display = 'none'; return; }
    $('clock').textContent = 'tick ' + frame.tick + '  |  plant time ' + RB.fmtClock(frame.t_s) + (run.live ? '  |  ' + (run.asking !== null ? 'asking the model about tick ' + run.asking : 'running') : '');
    $('scrub').max = Math.max(run.last, 1); $('scrub').value = S.cursor;
    if (S.drawer === 'charts') { RB.drawChartSet(chartCv, run, S.cursor, RB.chartDefs(run.header.rods.map(function (r) { return r.kind; }))); }
    const key = run.rows.length + ':' + run.status + ':' + (run.doc ? 1 : 0) + ':' + run.jobId;
    if (key !== lastLog) { lastLog = key; RB.renderLog($('log'), run, RB.pick); }
    RB.markLog($('log'), frame.tick, S.selected, S.playing || lastCursor !== Math.floor(S.cursor));
    const dkey = (run.jobId || run.label) + ':' + S.selected + ':' + (run.doc ? 1 : 0);
    if (dkey !== lastDetail) {
      lastDetail = dkey;
      RB.renderDetail($('detail'), run.rows[S.selected], function () { RB.openDrawer('whatif'); });
    }
    narrate(run, frame);
  }

  function narrate(run, frame) {
    const caps = run.captions.filter(function (c) { return c.tick <= frame.tick; }).sort(function (a, b) { return a.tick - b.tick; });
    const cur = caps[caps.length - 1];
    $('caption').textContent = cur && frame.tick - cur.tick <= 6 ? cur.text : '';
    $('caption').style.display = $('caption').textContent ? '' : 'none';
    const key = (run.jobId || run.label) + ':' + caps.length + ':' + (cur ? cur.tick : -1);
    if (key !== lastCaption) {
      lastCaption = key; const box = $('captions'); box.replaceChildren();
      caps.forEach(function (c) {
        box.appendChild(RB.el('li', { class: c === cur ? 'now' : '', onclick: function () { S.playing = false; RB.seekTick(c.tick); } }, [RB.el('b', { text: 'tick ' + c.tick + ' ' }), c.text]));
      });
      if (!caps.length) { box.appendChild(RB.el('li', { class: 'muted', text: 'Captions appear as key moments happen.' })); }
    }
  }

  /* The tools open as one drawer over the room; the room, its dock and the header stay in place. */
  S.drawer = '';
  RB.openDrawer = function (name) {
    const pane = $('pane-' + name); if (!pane) { return; }
    S.drawer = name; lastCursor = -1;
    document.querySelectorAll('.pane').forEach(function (p) { p.style.display = p === pane ? '' : 'none'; });
    document.querySelectorAll('.toolbtn').forEach(function (b) { b.classList.toggle('on', b.dataset.drawer === name); });
    $('drawer-title').textContent = pane.dataset.title; $('drawer').hidden = false; $('stage').classList.add('drawer-open'); $('drawer').scrollTop = 0;
    RB.touch();
  };
  RB.closeDrawer = function () {
    S.drawer = ''; $('drawer').hidden = true; $('stage').classList.remove('drawer-open');
    document.querySelectorAll('.toolbtn').forEach(function (b) { b.classList.remove('on'); });
    RB.touch();
  };

  function renderTab(cursor) {
    const tab = S.drawer;
    if (tab === 'whatif') { RB.whatif.refresh(); }
    else if (tab === 'history') { RB.history.draw(); }
    else if (tab === 'dist') { RB.dist.draw(); }
    else if (tab === 'compare') { RB.compare.draw(cursor); }
  }

  const SLOW_S = 20, TIMEOUT_S = 120;
  RB.fmtWall = function (s) { return Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0') + (s < 60 ? '.' + Math.floor(s % 1 * 10) : ''); };

  function median(a) { const s = a.slice().sort(function (x, y) { return x - y; }); return s.length ? s[s.length >> 1] : null; }

  /* The three clocks of the header and the waiting-for-model state; the playhead clock never depends on either. */
  function clocks() {
    const run = S.main, frame = run && run.trace.length ? RB.frameAt(run, S.cursor) : null;
    $('clkSim').textContent = frame ? RB.fmtClock(frame.t_s) + (run.live ? ' (newest ' + RB.fmtClock(run.trace[run.last].t_s) + ')' : '') : '-';
    $('clkWall').textContent = run ? RB.fmtWall(run.wallSeconds()) + (run.live || run.origin === 'live' ? ' elapsed' : ' model time') : '-';
    const rows = run ? run.decisions.filter(function (d) { return d && d.kind === 'decision'; }) : [], box = $('clkDecBox');
    const lat = rows.map(function (d) { return d.latency_ms / 1000; }), gap = median(rows.slice(1).map(function (d, i) { return d.tick - rows[i].tick; }));
    const wait = run ? run.pendingFor() : 0;
    $('clkDec').textContent = rows.length ? (gap === null ? '' : 'every ' + gap + ' ticks, ') + 'latency last ' + lat[lat.length - 1].toFixed(1) + ' s, mean ' +
      (lat.reduce(function (s, v) { return s + v; }, 0) / lat.length).toFixed(1) + ' s' + (wait ? ', waiting ' + wait.toFixed(1) + ' s' : '') : (wait ? 'waiting ' + wait.toFixed(1) + ' s' : '-');
    box.className = 'clk' + (wait > TIMEOUT_S ? ' stuck' : wait > SLOW_S ? ' slow' : '');
    const p = $('pending');
    p.style.display = wait ? '' : 'none';
    p.className = 'pending' + (wait > SLOW_S ? ' loud' : '');
    p.textContent = wait ? (wait > TIMEOUT_S ? 'NO ANSWER FROM THE MODEL FOR ' : wait > SLOW_S ? 'MODEL SLOW: waiting for the answer to the decision at tick ' + run.asking + ', ' : 'Waiting for the model: decision at tick ' + run.asking + ', ') + wait.toFixed(1) + ' s' : '';
    if (wait > TIMEOUT_S) { $('banner').textContent = 'The model has not answered the decision at tick ' + run.asking + ' for ' + wait.toFixed(0) + ' s. The animation keeps running; stop the run or keep waiting.'; $('banner').style.display = ''; }
  }

  function loop(now) {
    const dt = Math.min((now - (loop.last || now)) / 1000, 0.25); loop.last = now;
    advance(dt);
    const run = S.main, frame = run && run.trace.length ? RB.frameAt(run, S.cursor) : null;
    const extra = { paused: !S.playing && !(run && run.live), highlight: new Set() };
    if (now - (loop.clocked || 0) > 200) { loop.clocked = now; clocks(); }
    if (run && frame) {
      const d = activeDecision(run, frame);
      if (d && d.applied_tick !== null && d.applied_tick !== undefined && frame.tick >= d.applied_tick && frame.tick - d.applied_tick <= 2) { extra.highlight = RB.movedRods(run, d); }
    }
    core.draw(run && run.header ? run.header.rods : [], frame, extra);
    if (S.dirty) {
      S.dirty = false; updateHeader(); renderState(); renderTab(S.cursor); frameHooks.forEach(function (fn) { fn(); });
      $('play').textContent = S.playing ? 'Pause' : 'Play'; lastCursor = Math.floor(S.cursor);
    }
    if (needFrame()) { schedule(); } else { sleeper = setTimeout(schedule, 250); }
  }

  /* The loop draws frames while something moves and otherwise wakes four times a second, so a still room costs almost nothing. */
  let raf = 0, sleeper = 0;
  function needFrame() { return S.dirty || S.playing || !!(S.main && S.main.live) || S.drawer === 'core' || S.drawer === 'compare'; }
  function schedule() {
    if (raf) { return; }
    clearTimeout(sleeper); sleeper = 0;
    raf = requestAnimationFrame(function (t) { raf = 0; loop(t); });
  }
  RB.touch = function () { S.dirty = true; schedule(); };

  RB.init = async function () {
    const saved = store('rbmk-theme');
    document.documentElement.dataset.theme = saved || (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    try { S.info = await RB.api('/api/info'); } catch (e) { $('banner').textContent = 'Cannot reach the server: ' + e.message; $('banner').style.display = ''; return; }
    core = new RB.CoreView($('core')); panel = new RB.Panel($('panel'));
    chartDefs = RB.chartDefs([]);
    chartDefs.forEach(function (d) { const cv = RB.el('canvas', { class: 'chart' }); chartCv[d.id] = cv; $('charts').appendChild(RB.el('div', { class: 'chartcell' }, [cv])); });
    $('legend').replaceChildren.apply($('legend'), Object.keys(RB.KINDS).map(function (k) {
      return RB.el('span', { class: 'lg' }, [RB.el('i', { style: 'background:' + RB.KINDS[k][1] }), RB.KINDS[k][0]]);
    }));
    $('toy').textContent = S.info.toy_notice + ' The room is an interpretive reconstruction from published descriptions, not a survey of any room.';
    buildPicker(); controls();
    $('toy').onclick = function () { $('toy').classList.toggle('open'); };
    RB.whatif.build($('pane-whatif')); RB.history.build($('pane-history')); RB.dist.build($('pane-dist')); RB.compare.build($('pane-compare'));
    document.querySelectorAll('.toolbtn').forEach(function (b) { b.onclick = function () { if (S.drawer === b.dataset.drawer) { RB.closeDrawer(); } else { RB.openDrawer(b.dataset.drawer); } }; });
    $('drawer-close').onclick = RB.closeDrawer;
    setMode('live'); RB.openDrawer('run');
    window.addEventListener('resize', RB.touch);
    schedule();
    setInterval(function () { if (S.main && S.main.live) { RB.touch(); } }, 500);
  };
  window.addEventListener('DOMContentLoaded', RB.init);
})(window.RB);
