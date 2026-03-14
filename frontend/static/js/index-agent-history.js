// ── Agent History ──────────────────────────────────────────────────────────
let _ahData = {};
let _ahSelectedEntry = null;
let _ahGroupCollapsed = {};

async function ahLoad() {
  const data = await fetch('/api/pipeline/agents/summaries').then(r => r.json()).catch(() => ({}));
  _ahData = data;
  _ahRenderTree();
}

function _ahFmtTs(ts) {
  try {
    // stem format: 2026-03-13T00-27-21-650937+00-00
    const iso = ts.replace(/^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2}).*$/, '$1T$2:$3:$4Z');
    const d = new Date(iso);
    if (!isNaN(d)) return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  } catch(e) {}
  return ts.slice(0, 16);
}

function _ahRenderTree() {
  const tree = document.getElementById('ah-tree');
  if (!tree) return;
  const agents = Object.keys(_ahData);
  if (!agents.length) {
    tree.innerHTML = `<div style="padding:20px 16px;text-align:center;color:var(--text-muted);">
      <div style="font-size:20px;opacity:.2;margin-bottom:6px;">📋</div>
      <div style="font-size:11px;">No history yet</div>
      <div style="font-size:10px;margin-top:4px;">Agents write summaries here after completing tasks.</div>
    </div>`;
    return;
  }
  tree.innerHTML = agents.map(agentName => {
    const entries = _ahData[agentName] || [];
    const collapsed = _ahGroupCollapsed[agentName] ? 'collapsed' : '';
    const agentState = _paState[agentName] || {};
    const icon = agentState.icon || '🤖';
    const label = agentState.label || agentName;
    const rows = entries.map(e => {
      const active = _ahSelectedEntry && _ahSelectedEntry.agent === agentName && _ahSelectedEntry.id === e.id;
      return `<div onclick="ahOpenDetail('${agentName}','${esc(e.id)}')"
        style="padding:4px 14px 4px 24px;cursor:pointer;display:flex;align-items:center;gap:6px;border-radius:4px;${active?'background:var(--surface2);':''}"
        class="ah-entry" id="ah-entry-${esc(e.id)}">
        <span style="font-size:10px;color:var(--text);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(e.title)}</span>
        <span style="font-size:9px;color:var(--text-muted);font-family:monospace;flex-shrink:0;">${_ahFmtTs(e.ts)}</span>
      </div>`;
    }).join('');
    return `<div class="pa-group ${collapsed}" id="ah-group-${agentName}">
      <div class="pa-group-hdr" onclick="_ahToggleGroup('${agentName}')">
        <span class="pa-group-chevron">▾</span>
        <span style="font-size:12px;">${icon}</span>
        <span class="pa-group-label">${esc(label)}</span>
        <span class="pa-group-count">${entries.length}</span>
      </div>
      <div class="pa-group-body" style="padding:2px 0;">${rows}</div>
    </div>`;
  }).join('');
}

function _ahToggleGroup(name) {
  _ahGroupCollapsed[name] = !_ahGroupCollapsed[name];
  const el = document.getElementById('ah-group-' + name);
  if (el) el.classList.toggle('collapsed', !!_ahGroupCollapsed[name]);
}

function ahOpenDetail(agentName, entryId) {
  const entries = _ahData[agentName] || [];
  const entry = entries.find(e => e.id === entryId);
  if (!entry) return;
  _ahSelectedEntry = { agent: agentName, ...entry };
  document.querySelectorAll('.ah-entry').forEach(el => el.style.background = '');
  const el = document.getElementById('ah-entry-' + entryId);
  if (el) el.style.background = 'var(--surface2)';
  const agentState = _paState[agentName] || {};
  document.getElementById('ah-detail-agent').textContent = agentState.label || agentName;
  document.getElementById('ah-detail-ts').textContent = _ahFmtTs(entry.ts);
  document.getElementById('ah-detail-body').textContent = entry.body;
  document.getElementById('ah-detail').style.display = '';
}

function ahCloseDetail() {
  _ahSelectedEntry = null;
  document.getElementById('ah-detail').style.display = 'none';
  document.querySelectorAll('.ah-entry').forEach(el => el.style.background = '');
}

// ── Library Version History panel ────────────────────────────────────────────
let _historyVersionCount = 0;

async function loadActiveVersionBadge(slug) {
  if (!slug || slug === 'undefined' || slug === 'null') return;
  const badge = document.getElementById('active-version-badge');
  if (!badge) return;
  const av = await fetch(`/api/library/${slug}/active_version`).then(r => r.json()).catch(() => null);
  if (av) {
    const name = av.label || `v${av.vNum}`;
    badge.textContent = `● ${name}`;
    badge.style.cssText = 'display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;background:rgba(139,92,246,0.18);border:1.5px solid rgba(139,92,246,0.55);border-radius:5px;padding:2px 10px;color:#c4b5fd;font-family:monospace;letter-spacing:.04em;white-space:nowrap;';
  } else {
    badge.style.display = 'none';
  }
}
async function openHistoryPanel() {
  if (!selectedSlug) return;
  const pop = document.getElementById('history-popover');
  const btn = document.getElementById('history-btn');
  const badge = document.getElementById('history-slug-badge');
  if (badge) badge.textContent = selectedSlug;
  // Position near button, but keep within viewport
  if (btn) {
    const r = btn.getBoundingClientRect();
    const pw = 460;
    let left = r.left;
    if (left + pw > window.innerWidth - 10) left = window.innerWidth - pw - 10;
    pop.style.left = left + 'px';
    pop.style.top = (r.bottom + 6) + 'px';
  }
  pop.style.display = 'block';
  historyCloseNameRow();
  await _loadHistoryList();
  setTimeout(() => document.addEventListener('click', _historyPopoverOutside, { once: true }), 10);
}

function _historyPopoverOutside(e) {
  const pop = document.getElementById('history-popover');
  if (pop && !pop.contains(e.target) && e.target.id !== 'history-btn') pop.style.display = 'none';
}

function historyNewVersion() {
  const row = document.getElementById('history-name-row');
  if (!row) return;
  row.style.display = 'flex';
  const inp = document.getElementById('history-save-label');
  if (inp) {
    inp.value = `v${_historyVersionCount + 1}`;
    inp.focus();
    inp.select();
  }
}

function historyCloseNameRow() {
  const row = document.getElementById('history-name-row');
  if (row) row.style.display = 'none';
}

async function _loadHistoryList() {
  const list = document.getElementById('history-list');
  list.innerHTML = '<div style="color:var(--text-muted);font-size:11px;padding:8px 0;">Loading…</div>';
  const versions = await fetch(`/api/library/${selectedSlug}/history`).then(r => r.json()).catch(() => []);
  _historyVersionCount = versions.length;
  if (!versions.length) {
    list.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px 0;text-align:center;">No saved versions yet.<br><span style="font-size:10px;opacity:.7;">Click "+ New Version" to checkpoint this component, or run a Generate.</span></div>';
    return;
  }
  // Use event delegation — never embed IDs/labels directly inside onclick strings
  list.onclick = e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.action === 'activate') historyActivate(id, Number(btn.dataset.vnum));
    if (btn.dataset.action === 'delete')   historyDelete(id);
  };
  list.innerHTML = versions.map((v, i) => {
    const isAuto = !v.label || v.label.startsWith('auto');
    const labelHtml = v.label
      ? `<span style="color:${isAuto ? 'var(--text-muted)' : 'var(--text)'};">${escapeHtml(v.label)}</span>`
      : '<span style="color:var(--text-muted);font-style:italic;">auto-save</span>';
    const when = new Date(v.saved_at).toLocaleString();
    const vNum = versions.length - i;
    return `<div style="display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:5px;background:var(--surface2);">
      <div style="flex:1;min-width:0;">
        <div style="font-size:11px;font-weight:600;color:var(--text);">v${vNum} — ${labelHtml}</div>
        <div style="font-size:10px;color:var(--text-muted);margin-top:1px;">${when}</div>
      </div>
      <button data-action="activate" data-id="${escapeHtml(v.id)}" data-vnum="${vNum}" style="background:rgba(139,92,246,0.12);border:1px solid rgba(139,92,246,0.35);border-radius:4px;color:#a78bfa;padding:2px 8px;font-size:10px;font-weight:700;cursor:pointer;flex-shrink:0;" title="Activate this version of the component">Activate</button>
      <button data-action="delete" data-id="${escapeHtml(v.id)}" style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:4px;color:#f87171;padding:2px 6px;font-size:10px;cursor:pointer;flex-shrink:0;" title="Delete this snapshot">✕</button>
    </div>`;
  }).join('');
}

// After any tab-level save writes to profile.json, call this to keep the active
// version snapshot in sync so that Activate doesn't revert the user's changes.
async function _syncActiveSnapshot() {
  if (!selectedSlug) return;
  const av = await fetch(`/api/library/${selectedSlug}/active_version`).then(r => r.json()).catch(() => null);
  if (av && av.id) {
    await fetch(`/api/library/${selectedSlug}/history/${av.id}`, { method: 'PUT' }).catch(() => {});
  }
}

// Flush the CURRENTLY ACTIVE tab's editor → profile.json before snapshotting.
// Only flushes the tab that is open right now — stale editors from previously
// visited tabs are intentionally skipped to avoid overwriting profile.json with
// data that predates the last version activation or Generate.
async function _flushEditorsToProfile() {
  if (!selectedSlug) return;
  // 1. Schematic Example — only flush if user is currently on that tab
  if (currentProfileTab === 'schematic') {
    const svgEl = document.getElementById('app-circuit-canvas');
    if (appCircuitEditor && appCircuitEditor.svg === svgEl) {
      await fetch(`/api/library/${encodeURIComponent(selectedSlug)}/example_circuit`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          components: appCircuitEditor.project.components,
          wires:      appCircuitEditor.project.wires,
          labels:     appCircuitEditor.project.labels || []
        })
      }).catch(() => {});
    }
  }
  // 2. Layout Example — only flush if user is currently on that tab
  if (currentProfileTab === 'layout-example') {
    const frame = document.getElementById('le-frame');
    if (frame && frame.contentWindow && _leLoadedSlug === selectedSlug) {
      try {
        const board = await new Promise((resolve, reject) => {
          const t = setTimeout(() => reject(new Error('timeout')), 2000);
          const h = e => {
            if (e.data?.type !== 'boardData') return;
            if (e.source !== frame.contentWindow) return; // ignore boardData from other iframes (e.g. pcb-frame)
            clearTimeout(t); window.removeEventListener('message', h); resolve(e.data.board);
          };
          window.addEventListener('message', h);
          frame.contentWindow.postMessage({ type: 'getBoard' }, '*');
        });
        if (board) {
          await fetch(`/api/library/${encodeURIComponent(selectedSlug)}/layout_example`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(board)
          }).catch(() => {});
        }
      } catch (e) { /* iframe not ready — skip silently */ }
    }
  }
}

// Save button — flush editors then overwrite the active version's snapshot in place
async function historySaveActive() {
  if (!selectedSlug) return;
  await _flushEditorsToProfile();
  const av = await fetch(`/api/library/${selectedSlug}/active_version`).then(r => r.json()).catch(() => null);
  if (av) {
    const res = await fetch(`/api/library/${selectedSlug}/history/${av.id}`, { method: 'PUT' });
    if (!res.ok) { alert('Failed to save'); return; }
    showGenToast(`💾 Saved to "${av.label || 'v' + av.vNum}"`);
  } else {
    // No active version — auto-create "checkpoint"
    const r = await fetch(`/api/library/${selectedSlug}/history/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: 'checkpoint' })
    });
    if (!r.ok) { alert('Failed to save'); return; }
    const d = await r.json();
    await fetch(`/api/library/${selectedSlug}/history/${d.id}/activate`, { method: 'POST' });
    showGenToast('💾 Saved as "checkpoint"');
  }
  await _loadHistoryList();
  loadActiveVersionBadge(selectedSlug);
}

// New Version button — flush editors then create a fresh named snapshot
async function historySave() {
  if (!selectedSlug) return;
  await _flushEditorsToProfile();
  const input = document.getElementById('history-save-label');
  const label = input ? input.value.trim() : '';
  const res = await fetch(`/api/library/${selectedSlug}/history/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label })
  });
  if (!res.ok) { alert('Failed to create version'); return; }
  const data = await res.json();
  historyCloseNameRow();
  // Make the new snapshot the active version
  await fetch(`/api/library/${selectedSlug}/history/${data.id}/activate`, { method: 'POST' });
  await _loadHistoryList();
  loadActiveVersionBadge(selectedSlug);
}

async function historyActivate(id, vNum) {
  const res = await fetch(`/api/library/${selectedSlug}/history/${id}/activate`, { method: 'POST' });
  if (!res.ok) { alert('Failed to restore version'); return; }
  const data = await res.json();
  document.getElementById('history-popover').style.display = 'none';
  _leLoadedSlug = null; // force layout-example iframe to reload with activated profile's layout
  const profile = await fetch(`/api/library/${selectedSlug}`).then(r => r.json());
  profileCache[selectedSlug] = profile;
  renderProfile(profile);
  loadActiveVersionBadge(selectedSlug);
  const name = data.label || `v${data.vNum}`;
  showGenToast(`🕐 ${name} restored for ${selectedSlug}`);
}

async function historyDelete(id) {
  const res = await fetch(`/api/library/${selectedSlug}/history/${id}`, { method: 'DELETE' });
  if (!res.ok) { alert('Failed to delete version'); return; }
  await _loadHistoryList();
  loadActiveVersionBadge(selectedSlug);
}

const GT_TAB_LABEL = {
  footprint: 'Component Layout',
  example:   'Schematic Example',
  layout:    'Layout Example',
  datasheet: 'Profile Rebuild',
};

let _genToastTimer = null;
function showGenToast(msg) {
  const el = document.getElementById('gen-toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('visible');
  clearTimeout(_genToastTimer);
  _genToastTimer = setTimeout(() => el.classList.remove('visible'), 3500);
}

async function gtCreateTicket(type, slug, profile, rawText = null, extraNotes = '') {
  const partNum = profile.part_number || slug;
  const tabLabel = GT_TAB_LABEL[type] || type;
  const title = `Library → Component → ${partNum} → ${tabLabel}`;
  let basePrompt = type === 'footprint' ? buildFootprintPrompt(slug, profile)
                 : type === 'example'   ? await buildExampleRebuildPrompt(slug, profile, rawText)
                 : type === 'layout'    ? await buildLayoutPrompt(slug, profile)
                 : type === 'datasheet' ? await buildRebuildPrompt(slug, profile, rawText)
                 : '';
  if (extraNotes) basePrompt += `\n\nADDITIONAL INSTRUCTIONS FROM USER:\n${extraNotes}`;
  // Create ticket first to get the ID
  const res = await fetch('/api/gen-tickets', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ type, slug, title, prompt: basePrompt, status: 'pending' })
  });
  const ticket = await res.json();
  // Patch prompt to include ticket close instruction
  const fullPrompt = basePrompt + `\n\nWHEN DONE — mark this ticket complete:\nPUT http://localhost:8000/api/gen-tickets/${ticket.id}\nBody: { "status": "done" }`;
  await fetch(`/api/gen-tickets/${ticket.id}`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ prompt: fullPrompt })
  });
  showGenToast(`🎫 GT-${String(ticket.id).padStart(3,'0')} — ${title}`);
  // Refresh Agent History tab if it's currently open
  if (document.getElementById('agents-drawer')?.classList.contains('open')) {
    await ahLoad();
  }
}

