// The header, banner and replay bar of both the 3D room and the 2D views: one place says what the feed is.
export async function loadSpec(url = '../instruments.json') {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} answered HTTP ${res.status}`);
  return res.json();
}

export function setHeader(feed, $) {
  const badge = $('badge');
  const model = $('model');
  const banner = $('banner');
  banner.hidden = true;
  if (feed.status === 'loading') {
    badge.textContent = 'LOADING';
    badge.className = 'badge warn';
    return;
  }
  if (feed.status === 'error') {
    badge.textContent = 'NO FEED';
    badge.className = 'badge bad';
    model.textContent = 'model: none';
    $('scenario').textContent = '';
    banner.hidden = false;
    banner.textContent = `NO FEED. ${feed.error}. Every instrument is dark and nothing here is simulated. Run the demo's exporter and reload (see README).`;
    return;
  }
  const d = feed.data;
  const mode = feed.live ? (feed.stalled ? 'LIVE: STALLED' : feed.finished ? 'LIVE RUN FINISHED' : 'LIVE') : 'REPLAY';
  badge.textContent = mode;
  badge.className = `badge ${feed.live && !feed.stalled && !feed.finished ? 'live' : feed.stalled ? 'bad' : 'warn'}`;
  const id = feed.modelId;
  model.textContent = id ? `model: ${id}` : `model: none in this feed (controller: ${d.controller})`;
  $('scenario').textContent = `${d.scenario}  tick ${feed.row.tick}  t ${feed.row.t_s} s`;
  if (d.meta.model && id && d.meta.model !== id) {
    banner.hidden = false;
    banner.textContent = `The feed header names model ${d.meta.model} but its answers came from ${id}.`;
  }
  const slider = $('scrub');
  slider.max = d.trace.length - 1;
  slider.value = feed.index;
  $('replay').hidden = feed.live;
  $('playpause').textContent = feed.playing ? 'Pause' : 'Play';
}

export function bindReplay(feed, $, refresh) {
  $('playpause').onclick = () => { feed.playing = !feed.playing; refresh(); };
  $('scrub').oninput = (e) => { feed.playing = false; feed.setIndex(+e.target.value); };
  $('speed').onchange = (e) => { feed.speed = +e.target.value; };
}
