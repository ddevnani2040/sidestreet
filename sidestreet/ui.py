"""The map. Deliberately reads like a navigation app, not a dashboard."""

INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sidestreet — routing by what the cameras see</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: #0d0f13; color: #e8eaed; overflow: hidden;
    font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
  }
  #map { position: absolute; inset: 0; background: #0d0f13; }
  .leaflet-container { background: #0d0f13; }

  #panel {
    position: absolute; top: 0; left: 0; bottom: 0; width: 386px; z-index: 1000;
    background: #12151c; border-right: 1px solid #232733;
    display: flex; flex-direction: column; overflow: hidden;
  }
  .head { padding: 16px 18px 12px; border-bottom: 1px solid #232733; }
  .head h1 { margin: 0; font-size: 16px; letter-spacing: -0.01em; }
  .head p { margin: 3px 0 0; font-size: 12px; color: #8b93a5; }

  .trip { padding: 12px 18px; border-bottom: 1px solid #232733; }
  input {
    width: 100%; background: #1b1f28; color: #e8eaed; border: 1px solid #2b3040;
    border-radius: 7px; padding: 8px 10px; font: inherit; font-size: 13px;
    margin-bottom: 6px;
  }
  input::placeholder { color: #6f7889; }
  .saved {
    margin: 10px 18px 0; padding: 11px 13px; border-radius: 9px;
    background: #10261a; border: 1px solid #1e5c33;
  }
  .saved .big { font-size: 21px; font-weight: 700; color: #7ee2a0; }
  .saved .cap { font-size: 11.5px; color: #8fb79f; margin-top: 1px; }
  .saved.none { background: #1a1d24; border-color: #2b3040; }
  .saved.none .big { color: #b9c0cf; }
  .saved.none .cap { color: #8b93a5; }
  select, button {
    background: #1b1f28; color: #e8eaed; border: 1px solid #2b3040;
    border-radius: 7px; padding: 8px 10px; font: inherit; font-size: 13px;
  }
  select { width: 100%; }
  button { cursor: pointer; }
  button:hover { background: #232833; }
  button.go { background: #1f6feb; border-color: #1f6feb; color: #fff;
              width: 100%; margin-top: 8px; font-weight: 600; }
  button.go:hover { background: #2b7ff5; }
  button.go:disabled { opacity: .55; cursor: default; }
  button.find {
    width: 100%; margin-top: 6px; font-size: 12.5px; background: #1a2230;
    border-color: #2f3d55; color: #9fc3ff;
  }
  button.find:hover { background: #212c3d; }
  button.find:disabled { opacity: .55; cursor: default; }
  .picks { border-top: 1px solid #232733; max-height: 210px; overflow-y: auto; }
  .pick {
    padding: 9px 18px; border-bottom: 1px solid #1c2029; cursor: pointer;
    font-size: 12.5px;
  }
  .pick:hover { background: #161a22; }
  .pick .t { font-weight: 600; }
  .pick .d { color: #8b93a5; font-size: 11.5px; margin-top: 1px; }
  .pick.best { border-left: 3px solid #34d058; }

  .routes { overflow-y: auto; flex: 1; }
  .route {
    padding: 13px 18px; border-bottom: 1px solid #1c2029; cursor: pointer;
    border-left: 3px solid transparent;
  }
  .route:hover { background: #161a22; }
  .route.sel { background: #171d28; }
  .route.g { border-left-color: #8b93a5; }
  .route.s { border-left-color: #34d058; }
  .route .top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .route .who { font-size: 12px; font-weight: 700; letter-spacing: .04em;
                text-transform: uppercase; }
  .route.g .who { color: #8b93a5; }
  .route.s .who { color: #34d058; }
  .route .time { font-size: 19px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .route .via { font-size: 12.5px; color: #b9c0cf; margin-top: 2px; }
  .route .pills { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
  .pill {
    font-size: 11px; padding: 2px 8px; border-radius: 999px;
    background: #1b1f28; border: 1px solid #2b3040; color: #9aa3b4;
  }
  .pill.jam { background: #2a1416; border-color: #5c2226; color: #ff8b84; }
  .pill.mod { background: #2a2413; border-color: #5c4c1a; color: #f0d49a; }
  .pill.free { background: #12261a; border-color: #1e5c33; color: #7ee2a0; }

  .why {
    padding: 13px 18px; border-top: 1px solid #232733; background: #10141b;
    font-size: 12.5px; color: #c3cad8; line-height: 1.55;
  }
  .why b { color: #fff; }

  .cams { border-top: 1px solid #232733; max-height: 210px; overflow-y: auto; }
  .cam {
    display: flex; align-items: center; gap: 9px; padding: 7px 18px;
    font-size: 12.5px; cursor: pointer;
  }
  .cam:hover { background: #161a22; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .free { background: #34d058; } .moderate { background: #f2c94c; }
  .jammed { background: #ff5f56; } .unknown { background: #59616f; }
  .cam .lbl { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cam .n { color: #8b93a5; font-variant-numeric: tabular-nums; font-size: 11.5px; }

  .marker { border-radius: 50%; border: 2px solid #0d0f13;
            box-shadow: 0 0 0 1.5px rgba(255,255,255,.25); }
  .leaflet-popup-content-wrapper, .leaflet-popup-tip {
    background: #171b23; color: #e8eaed; border-radius: 10px;
  }
  .leaflet-popup-content { margin: 10px 12px; font-size: 12.5px; }
  .leaflet-popup-content img { width: 264px; border-radius: 6px; display: block;
                               margin: 7px 0 6px; background: #0a0c10; }
  .leaflet-popup-content button { width: 100%; font-size: 12px; padding: 5px; }
  .status { padding: 9px 18px; font-size: 11.5px; color: #6f7889;
            border-top: 1px solid #232733; }
  .spin { opacity: .55; }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <div class="head">
    <h1>Sidestreet</h1>
    <p>Google routes by predicted time. We route by what the cameras see.
    Covering every DOT camera between 14th and 72nd St, river to river.</p>
  </div>
  <div class="trip">
    <input id="from" placeholder="From — address or place">
    <input id="to" placeholder="To — address or place">
    <select id="trip">
      <option value="">— or pick a short demo route —</option>
      <option value="Columbus Circle, New York, NY|Union Square, New York, NY">Columbus Circle → Union Sq</option>
      <option value="Union Square, New York, NY|United Nations Headquarters, New York, NY">Union Sq → UN HQ</option>
      <option value="Grand Central Terminal, New York, NY|Union Square, New York, NY">Grand Central → Union Sq</option>
      <option value="Times Square, New York, NY|Gramercy Park, New York, NY">Times Sq → Gramercy</option>
      <option value="Grand Central Terminal, New York, NY|Javits Center, New York, NY">Grand Central → Javits</option>
      <option value="Union Square, New York, NY|Penn Station, New York, NY">Union Sq → Penn Station</option>
    </select>
    <button class="go" id="go">Get directions</button>
    <button class="find" id="find">⚡ Find the best demo route right now</button>
  </div>
  <div class="picks" id="picks"></div>
  <div id="saved"></div>
  <div class="routes" id="routes"></div>
  <div class="why" id="why">Pick a trip and hit directions.</div>
  <div class="cams" id="cams"></div>
  <div class="status" id="status">loading cameras…</div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const COLORS = {free:'#34d058', moderate:'#f2c94c', jammed:'#ff5f56', unknown:'#59616f'};
const map = L.map('map', {zoomControl:false}).setView([40.7570,-73.9800], 14);
// The covered region: every camera inside it is polled. Drawn so it is obvious
// where the app has evidence and where it does not.
const FOCUS = [[40.732,-74.015],[40.782,-73.950]];
L.rectangle(FOCUS, {color:'#3b4a63', weight:1, fill:false, dashArray:'4,6'}).addTo(map);
L.control.zoom({position:'bottomright'}).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution:'&copy; OpenStreetMap, &copy; CARTO', subdomains:'abcd', maxZoom:19
}).addTo(map);

let markers = {}, lines = [], data = null, selected = null;

async function loadCameras() {
  const d = await (await fetch('/api/density')).json();
  for (const c of d.cameras) {
    if (markers[c.id]) { map.removeLayer(markers[c.id]); }
    const m = L.marker([c.lat, c.lon], {icon: L.divIcon({
      className:'', iconSize:[13,13],
      html:`<div class="marker" style="width:13px;height:13px;background:${COLORS[c.level]}"></div>`
    })}).addTo(map);
    m.bindPopup(popupHtml(c), {minWidth:264});
    m.on('popupopen', () => wirePopup(c));
    markers[c.id] = m;
  }
  const s = d.summary;
  document.getElementById('status').textContent =
    `${s.cameras} cameras · ${s.jammed} jammed · ${s.moderate} moderate · ` +
    `${s.free} free · poll #${s.poll_count}`;
}

function popupHtml(c) {
  return `<b>${c.label}</b><br>
    <span style="color:${COLORS[c.level]}">${c.level}</span>
    · ${c.vehicles === null ? '—' : c.vehicles + ' vehicles'}
    ${c.simulated ? ' · <b>SIMULATED</b>' : ''}
    <img src="/api/cameras/${c.id}/image?t=${Date.now()}">
    <button data-jam="${c.id}">${c.simulated ? 'Clear simulation' : 'Simulate jam'}</button>`;
}

function wirePopup(c) {
  const b = document.querySelector(`[data-jam="${c.id}"]`);
  if (!b) return;
  b.onclick = async () => {
    b.disabled = true;
    await fetch(`/api/simulate/${c.id}?level=${c.simulated ? 'clear' : 'jammed'}`,
                {method:'POST'});
    map.closePopup();
    await loadCameras();
    if (data) route();   // rerouting on a jam is the whole point
  };
}

async function route() {
  const from = document.getElementById('from').value.trim();
  const to = document.getElementById('to').value.trim();
  const preset = document.getElementById('trip').value;
  let qs;
  if (from && to) {
    qs = `origin=${encodeURIComponent(from)}&destination=${encodeURIComponent(to)}`;
  } else if (preset) {
    const [o, dst] = preset.split('|');
    document.getElementById('from').value = o;
    document.getElementById('to').value = dst;
    qs = `origin=${encodeURIComponent(o)}&destination=${encodeURIComponent(dst)}`;
  } else {
    document.getElementById('why').textContent =
      'Enter a from and to address, or pick a preset.';
    return;
  }
  const btn = document.getElementById('go');
  btn.disabled = true; btn.textContent = 'Routing…';
  document.getElementById('routes').classList.add('spin');
  try {
    const r = await fetch(`/api/route?${qs}`);
    const j = await r.json();
    if (j.detail) { document.getElementById('why').textContent = 'Error: ' + j.detail; return; }
    data = j; draw();
  } finally {
    btn.disabled = false; btn.textContent = 'Get directions';
    document.getElementById('routes').classList.remove('spin');
  }
}

function draw() {
  lines.forEach(l => map.removeLayer(l)); lines = [];
  const g = data.google, s = data.sidestreet;
  const same = !data.diverted;

  // Google underneath, dashed; Sidestreet on top, solid green.
  lines.push(L.polyline(g.path, {color:'#8b93a5', weight:7, opacity:.75,
                                 dashArray:'1,9', lineCap:'round'}).addTo(map));
  if (!same)
    lines.push(L.polyline(s.path, {color:'#34d058', weight:5, opacity:.95}).addTo(map));

  document.getElementById('routes').innerHTML =
    card('g','Google Maps', g) + (same ? '' : card('s','Sidestreet', s));

  const sv = document.getElementById('saved');
  if (data.diverted && data.saved_min > 0.2) {
    const extra = data.extra_distance_min;
    sv.innerHTML = `<div class="saved">
      <div class="big">Side streets save ~${data.saved_min} min</div>
      <div class="cap">${extra > 0
          ? extra + ' min longer to drive, but past ' +
            (g.jammed - s.jammed) + ' fewer jammed blocks'
          : Math.abs(extra) + ' min shorter <em>and</em> past ' +
            (g.jammed - s.jammed) + ' fewer jammed blocks'}
        · modelled from camera density, not measured</div></div>`;
  } else {
    sv.innerHTML = `<div class="saved none"><div class="big">No better route</div>
      <div class="cap">Google's line is the one the cameras like too</div></div>`;
  }
  document.getElementById('why').innerHTML = '<b>Why:</b> ' + data.explanation;
  showCams(same ? g : s);
  map.fitBounds(L.polyline(g.path.concat(s.path)).getBounds(), {
    paddingTopLeft:[406,60], paddingBottomRight:[60,60], maxZoom:15});
}

function card(cls, who, r) {
  const pills = [
    r.jammed ? `<span class="pill jam">${r.jammed} jammed</span>` : '',
    r.moderate ? `<span class="pill mod">${r.moderate} moderate</span>` : '',
    (!r.jammed && !r.moderate) ? `<span class="pill free">all clear</span>` : '',
    `<span class="pill">${r.camera_count} cameras</span>`,
  ].join('');
  return `<div class="route ${cls}" data-r="${cls}">
    <div class="top"><span class="who">${who}</span>
      <span class="time">${r.duration_min} min</span></div>
    <div class="via">via ${r.description || r.label}</div>
    <div class="pills">${pills}</div></div>`;
}

function showCams(r) {
  document.getElementById('cams').innerHTML = r.cameras.map(c =>
    `<div class="cam" data-c="${c.id}">
       <i class="dot ${c.level}"></i>
       <span class="lbl">${c.label}</span>
       <span class="n">${c.vehicles === null ? '—' : c.vehicles + 'v'}</span>
     </div>`).join('');
  for (const el of document.querySelectorAll('[data-c]')) {
    el.onclick = () => {
      const m = markers[el.dataset.c];
      if (m) { map.setView(m.getLatLng(), 16); m.openPopup(); }
    };
  }
}

document.getElementById('find').onclick = findBest;

async function findBest() {
  const b = document.getElementById('find');
  b.disabled = true; b.textContent = 'Scanning 14 trips…';
  const box = document.getElementById('picks');
  box.innerHTML = '<div class="pick"><div class="d">Checking live density on every candidate trip…</div></div>';
  try {
    const j = await (await fetch('/api/best?limit=6')).json();
    if (j.detail) { box.innerHTML = `<div class="pick"><div class="d">Error: ${j.detail}</div></div>`; return; }
    box.innerHTML = j.trips.map((t, i) => `
      <div class="pick ${t.strictly_better ? 'best' : ''}" data-i="${i}">
        <div class="t">${t.from} → ${t.to}${t.strictly_better ? '  ✓ fewer jams AND not slower' : ''}</div>
        <div class="d">Google ${t.google_min}m · ${t.google_jammed} jammed of ${t.cameras} cams
          → ours ${t.pick_min}m · ${t.pick_jammed} jammed</div>
      </div>`).join('') ||
      '<div class="pick"><div class="d">No diversions right now — try again in a minute.</div></div>';
    for (const el of box.querySelectorAll('[data-i]')) {
      el.onclick = () => {
        const t = j.trips[+el.dataset.i];
        document.getElementById('from').value = t.origin;
        document.getElementById('to').value = t.destination;
        document.getElementById('trip').value = '';
        route();
      };
    }
  } finally {
    b.disabled = false; b.textContent = '⚡ Find the best demo route right now';
  }
}

document.getElementById('go').onclick = route;
document.getElementById('trip').onchange = route;
for (const id of ['from','to'])
  document.getElementById(id).addEventListener('keydown', e => {
    if (e.key === 'Enter') route();
  });

loadCameras().then(route);
setInterval(loadCameras, 20000);
</script>
</body>
</html>
"""
