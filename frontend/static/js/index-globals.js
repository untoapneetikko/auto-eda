let library = {};
let selectedSlug = null;
const profileCache = {};

// SVG/HTML text escaping helper
function esc(s) { return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

window._icLayoutCache = {};
async function prefetchICLayout(slug) {
  if (window._icLayoutCache[slug]) return;
  try {
    const data = await fetch(`/api/library/${slug}/layout`).then(r=>r.json());
    if (data && data.BOX_W) window._icLayoutCache[slug] = data;
  } catch(e) {}
}

// ── SSE live updates ───────────────────────────────────────────────────────
const evtSource = new EventSource('/api/events');

// ── Live indicator ────────────────────────────────────────────────────────────
function _setLive(state) {
  const el = document.getElementById('live-indicator');
  const lbl = document.getElementById('live-label');
  if (!el || !lbl) return;
  el.className = state;
  lbl.textContent = state === 'live' ? 'LIVE' : state === 'reconnecting' ? 'RECONNECTING…' : 'OFFLINE';
  if (state !== 'live') document.getElementById('live-ping').textContent = '';
}
async function _pingServer() {
  const t0 = performance.now();
  try {
    await fetch('/api/pipeline/agents', {cache: 'no-store'});
    const ms = Math.round(performance.now() - t0);
    const ping = document.getElementById('live-ping');
    if (ping) ping.textContent = ms + 'ms';
    _setLive('live');
  } catch(e) {
    _setLive('dead');
  }
}
evtSource.onopen  = () => { _setLive('live'); _pingServer(); };
evtSource.onerror = () => _setLive(evtSource.readyState === 0 ? 'reconnecting' : 'dead');
setInterval(_pingServer, 15000); // 5s was unnecessarily frequent; 15s is plenty

// Debounce library reloads triggered by SSE so rapid successive updates
// (e.g. bulk profile saves) don't fire one fetch per event.
let _libReloadTimer = null;
function _scheduleLibReload() {
  clearTimeout(_libReloadTimer);
  _libReloadTimer = setTimeout(() => loadLibrary(), 600);
}
evtSource.addEventListener('library_updated', () => _scheduleLibReload());
let _profileReloadTimer = null;
evtSource.addEventListener('profile_updated', e => {
  const { slug } = JSON.parse(e.data);
  _scheduleLibReload();
  if (selectedSlug === slug) {
    clearTimeout(_profileReloadTimer);
    _profileReloadTimer = setTimeout(() => {
      // Don't blow away the layout-example iframe while the user is actively working on it.
      // profile_updated fires after every leSaveLayout() PUT, which would destroy the iframe
      // and leave _leLoadedSlug pointing at a blank frame — silently breaking the next save.
      if (typeof currentProfileTab !== 'undefined' && currentProfileTab === 'layout-example' &&
          typeof _leLoadedSlug !== 'undefined' && _leLoadedSlug === slug) return;
      loadProfile(slug);
    }, 600);
  }
});
evtSource.addEventListener('pipeline_agent_started',   e => _handlePipelineSSE('pipeline_agent_started',   JSON.parse(e.data)));
evtSource.addEventListener('pipeline_agent_msg_start', e => _handlePipelineSSE('pipeline_agent_msg_start', JSON.parse(e.data)));
evtSource.addEventListener('pipeline_agent_chunk',     e => _handlePipelineSSE('pipeline_agent_chunk',     JSON.parse(e.data)));
evtSource.addEventListener('pipeline_agent_done',      e => _handlePipelineSSE('pipeline_agent_done',      JSON.parse(e.data)));

// ── Init ───────────────────────────────────────────────────────────────────
loadLibrary();

async function loadLibrary() {
  const res = await fetch('/api/library');
  library = await res.json();
  renderLibrary();
  renderSchPalette(document.getElementById('sch-lib-search')?.value || '');
}

