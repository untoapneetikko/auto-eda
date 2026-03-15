// ── Fetch profile respecting active version snapshot ─────────────────────────
// Always use this instead of fetch('/api/library/{slug}') directly so the user
// gets their activated version (V2 etc.) rather than the raw profile.json.
async function fetchProfile(slug) {
  try {
    const av = await fetch(`/api/library/${slug}/active_version`).then(r => r.json()).catch(() => null);
    if (av?.id) {
      const snap = await fetch(`/api/library/${slug}/history/${av.id}`).then(r => r.json()).catch(() => null);
      if (snap?.profile) return snap.profile;
    }
  } catch(_) {}
  return fetch(`/api/library/${slug}`, { cache: 'no-store' }).then(r => r.json());
}

// ── Schematic sub-section switching ───────────────────────────────────────
function switchSchSection(name) {
  ['schematic','auto','rules','export'].forEach(s => {
    const tb = document.getElementById('sch-toolbar-' + s);
    const btn = document.getElementById('sch-sub-' + s);
    if (tb) tb.style.display = s === name ? '' : 'none';
    if (btn) btn.classList.toggle('active', s === name);
  });
}

function switchAccSection(name) {
  ['schematic','auto','export'].forEach(s => {
    const tb = document.getElementById('acc-toolbar-' + s);
    const btn = document.getElementById('acc-sub-' + s);
    if (tb) tb.style.display = s === name ? 'flex' : 'none';
    if (btn) btn.classList.toggle('active', s === name);
  });
}

// ── Section switching ──────────────────────────────────────────────────────
function switchSection(name) {
  ['library','schematic','pcb','build'].forEach(s => {
    const sec = document.getElementById('section-' + s);
    const nav = document.getElementById('nav-' + s);
    if (sec) sec.classList.toggle('hidden', s !== name);
    if (nav) nav.classList.toggle('active', s === name);
  });
  try { localStorage.setItem('eda_active_section', name); } catch(_) {}
  if (name === 'schematic') {
    setTimeout(() => { editor._resize(); renderSchPalette(document.getElementById('sch-lib-search')?.value || ''); }, 50);
  }
  if (name === 'pcb') {
    const frame = document.getElementById('pcb-frame');
    if (!frame.src) frame.src = '/pcb.html?app=1';
    // Re-send current dirty state once frame is ready
    setTimeout(_pcbFrameReady, 800);
    // Auto-load the PCB for the active project so the user never needs to
    // click "→ PCB Layout" again after the board has been created once.
    const _autoProj = openTabs.find(t => t.tabId === activeTabId)?.projectId
                    || editor.project?.id || null;
    if (_autoProj) {
      _pcbAutoLoad(_autoProj);
    } else {
      // No project — just ensure canvas is correctly sized after becoming visible
      setTimeout(() => { try { frame?.contentWindow?.postMessage({ type: 'tabVisible' }, '*'); } catch(_) {} }, 100);
    }
  }
  if (name === 'build') {
    buildLoad();
  }
}


// kept for backward compat (PDF viewer / profile still uses showView)
function showView(name) {
  // In library section, just make sure the section is showing
  if (name === 'library') switchSection('library');
}

// ── Schematic palette ──────────────────────────────────────────────────────

// Category metadata: symType → { label, icon, order }
const _SCH_CATS = {
  vcc:          { label: 'Power',       icon: '⚡', order: 0 },
  gnd:          { label: 'Power',       icon: '⚡', order: 0 },
  resistor:     { label: 'Passives',    icon: '▭',  order: 1 },
  capacitor:    { label: 'Passives',    icon: '▭',  order: 1 },
  capacitor_pol:{ label: 'Passives',    icon: '▭',  order: 1 },
  inductor:     { label: 'Passives',    icon: '▭',  order: 1 },
  diode:        { label: 'Diodes',      icon: '▷',  order: 2 },
  led:          { label: 'Diodes',      icon: '▷',  order: 2 },
  npn:          { label: 'Transistors', icon: '◈',  order: 3 },
  pnp:          { label: 'Transistors', icon: '◈',  order: 3 },
  nmos:         { label: 'Transistors', icon: '◈',  order: 3 },
  pmos:         { label: 'Transistors', icon: '◈',  order: 3 },
  opamp:        { label: 'Op-Amps',     icon: '△',  order: 4 },
  amplifier:    { label: 'Op-Amps',     icon: '△',  order: 4 },
  ic:           { label: 'ICs',         icon: '▣',  order: 5 },
};

function _schCatOf(p) {
  const t = (p.symbol_type || p.symType || 'ic').toLowerCase();
  return _SCH_CATS[t] || { label: 'ICs', icon: '▣', order: 5 };
}

// Collapsed-state persisted in localStorage key "schPalCollapsed"
function _schPalCollapsed() {
  try { return JSON.parse(localStorage.getItem('schPalCollapsed') || '{}'); } catch(_) { return {}; }
}
function _schPalToggle(label) {
  const s = _schPalCollapsed();
  s[label] = !s[label];
  localStorage.setItem('schPalCollapsed', JSON.stringify(s));
  renderSchPalette(document.getElementById('sch-lib-search')?.value || '');
}

// Shared palette-item row HTML
function _paletteItemH(p) {
  const slug = esc(p.slug);
  const name = esc(p.part_number || p.slug);
  const desc = esc(p.description || '');
  return `<div class="palette-item" style="position:relative;">
    <div onclick="placeFromPalette('${slug}')" style="cursor:pointer;">
      <div class="palette-item-name">${name}</div>
      <div class="palette-item-desc">${desc}</div>
    </div>
    <button onclick="event.stopPropagation();loadExampleFromSlug('${slug}')" title="Load example circuit"
      style="position:absolute;top:4px;right:4px;background:var(--accent-dim);border:1px solid var(--accent);color:var(--accent);border-radius:4px;width:20px;height:20px;font-size:13px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0;">⚡</button>
  </div>`;
}

function renderSchPalette(filter) {
  const el = document.getElementById('sch-palette');
  if (!el) return;

  const allParts = Object.values(library);

  // ── Flat search results ────────────────────────────────────────────────────
  if (filter && filter.trim()) {
    const q = filter.trim().toLowerCase();
    const parts = allParts.filter(p =>
      p.part_number?.toLowerCase().includes(q) ||
      p.description?.toLowerCase().includes(q) ||
      p.slug?.toLowerCase().includes(q)
    );
    if (!parts.length) {
      el.innerHTML = '<div style="padding:16px 8px;color:var(--text-muted);font-size:12px;text-align:center;">No components found</div>';
      return;
    }
    el.innerHTML = parts.map(_paletteItemH).join('');
    return;
  }

  // ── Category tree (no filter) ─────────────────────────────────────────────
  if (!allParts.length) {
    el.innerHTML = '<div style="padding:16px 8px;color:var(--text-muted);font-size:12px;text-align:center;">No components in library yet</div>';
    return;
  }

  // Group parts by category label, preserve insertion order within each group
  const groups = new Map(); // label → { icon, order, parts[] }
  for (const p of allParts) {
    const cat = _schCatOf(p);
    if (!groups.has(cat.label)) groups.set(cat.label, { icon: cat.icon, order: cat.order, parts: [] });
    groups.get(cat.label).parts.push(p);
  }
  // Sort categories by order, then alphabetically within
  const sorted = [...groups.entries()].sort((a, b) => a[1].order - b[1].order || a[0].localeCompare(b[0]));

  const collapsed = _schPalCollapsed();
  let h = '<div style="padding:4px 0 8px;">';
  for (const [label, { icon, parts }] of sorted) {
    const isOpen = !collapsed[label];
    const arrow = isOpen ? '▾' : '▸';
    const countBadge = `<span style="margin-left:auto;font-size:9px;background:var(--surface3,rgba(255,255,255,0.06));color:var(--text-muted);border-radius:8px;padding:1px 6px;font-weight:600;">${parts.length}</span>`;
    h += `<div>
      <div onclick="_schPalToggle('${esc(label)}')"
        style="display:flex;align-items:center;gap:5px;padding:4px 8px 4px 6px;cursor:pointer;user-select:none;
               font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
               color:var(--text-dim);background:var(--surface2,rgba(255,255,255,0.04));
               border-top:1px solid var(--border,rgba(255,255,255,0.08));
               transition:background 0.1s;"
        onmouseenter="this.style.background='var(--surface3,rgba(255,255,255,0.08))'"
        onmouseleave="this.style.background='var(--surface2,rgba(255,255,255,0.04))'">
        <span style="font-size:9px;opacity:0.6;width:10px;text-align:center;">${arrow}</span>
        <span style="opacity:0.7;">${esc(icon)}</span>
        <span>${esc(label)}</span>
        ${countBadge}
      </div>`;
    if (isOpen) {
      h += `<div style="padding:2px 0 4px;">`;
      for (const p of parts.sort((a, b) => (a.part_number||a.slug).localeCompare(b.part_number||b.slug))) {
        h += _paletteItemH(p);
      }
      h += `</div>`;
    }
    h += `</div>`;
  }
  h += '</div>';
  el.innerHTML = h;
}

async function loadExampleFromSlug(slug) {
  const p = await fetchProfile(slug);
  profileCache[slug] = p;
  switchSection('schematic');
  requestAnimationFrame(() => editor.startPlaceGroup(p));
}

async function placeFromPalette(slug) {
  const p = await fetchProfile(slug);
  profileCache[slug] = p;
  editor.startPlace(slug, detectSymbolType(p));
}

function accShowPanel(mode) {
  const searchPanel = document.getElementById('acc-search-panel');
  if (!searchPanel) return;
  searchPanel.style.display = mode === 'search' ? 'flex' : 'none';
}

function accHandleSearch(val) {
  const searchPanel = document.getElementById('acc-search-panel');
  if (!searchPanel) return;
  searchPanel.style.display = 'flex';
  renderAccPalette(val);
}

function accHandleSearchBlur() {
  const inp = document.getElementById('acc-palette-search');
  if (inp && inp.value.trim()) return; // keep open if text present
  const searchPanel = document.getElementById('acc-search-panel');
  if (searchPanel) searchPanel.style.display = 'none';
}

// Save the example editor's current circuit back to the server
async function accSaveExample(btn) {
  if (!selectedSlug) {
    if (btn) { btn.textContent = '✕ No component'; setTimeout(() => { btn.textContent = '💾 Save'; }, 2000); }
    return;
  }
  // appCircuitEditor is initialized asynchronously — wait up to 3s for it
  if (!appCircuitEditor || appCircuitEditor.svg !== document.getElementById('app-circuit-canvas')) {
    if (btn) { btn.textContent = '⏳ Loading…'; btn.disabled = true; }
    await new Promise(resolve => {
      let tries = 0;
      const wait = setInterval(() => {
        tries++;
        if ((appCircuitEditor && appCircuitEditor.svg === document.getElementById('app-circuit-canvas')) || tries > 30) {
          clearInterval(wait);
          resolve();
        }
      }, 100);
    });
    if (!appCircuitEditor) {
      if (btn) { btn.textContent = '✕ Editor not ready'; btn.disabled = false; setTimeout(() => { btn.textContent = '💾 Save'; }, 2000); }
      return;
    }
  }
  const circuit = {
    components: appCircuitEditor.project.components,
    wires: appCircuitEditor.project.wires,
    labels: appCircuitEditor.project.labels || [],
    noConnects: appCircuitEditor.project.noConnects || []
  };
  // Guard: don't overwrite a non-empty saved circuit with an empty canvas.
  // This can happen if the editor re-initialises before the circuit is loaded.
  if (circuit.components.length === 0) {
    const stored = profileCache[selectedSlug]?.example_circuit;
    if (stored && (stored.components || []).length > 0) {
      if (btn) { btn.textContent = '✕ Canvas empty – not saved'; btn.disabled = false; setTimeout(() => { btn.textContent = '💾 Save'; }, 2500); }
      return;
    }
  }
  if (btn) { btn.textContent = '⏳ Saving…'; btn.disabled = true; }
  try {
    const res = await fetch(`/api/library/${encodeURIComponent(selectedSlug)}/example_circuit`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(circuit)
    });
    if (res.ok) {
      await _syncActiveSnapshot(); // keep active version snapshot in sync
      if (btn) { btn.textContent = '✓ Saved'; btn.disabled = false; setTimeout(() => { btn.textContent = '💾 Save'; }, 2000); }
    } else {
      if (btn) { btn.textContent = `✕ Error ${res.status}`; btn.disabled = false; setTimeout(() => { btn.textContent = '💾 Save'; }, 2000); }
    }
  } catch(e) {
    if (btn) { btn.textContent = '✕ Network error'; btn.disabled = false; setTimeout(() => { btn.textContent = '💾 Save'; }, 2000); }
  }
}

const _paletteTypeColors = {
  ic:'#6c63ff', amplifier:'#6c63ff', resistor:'#c87533', capacitor:'#3b82f6',
  capacitor_pol:'#3b82f6', inductor:'#a78bfa', led:'#22c55e', diode:'#f59e0b',
  npn:'#f59e0b', pnp:'#f59e0b', nmos:'#f59e0b', pmos:'#f59e0b',
  vcc:'#ef4444', gnd:'#6b7280'
};
const _paletteTypeLabels = {
  ic:'IC', amplifier:'AMP', resistor:'R', capacitor:'C', capacitor_pol:'C+',
  inductor:'L', led:'LED', diode:'D', npn:'NPN', pnp:'PNP', nmos:'FET', pmos:'FET',
  vcc:'VCC', gnd:'GND'
};

// Render the compact component palette in the example schematic tab
function renderAccPalette(filter) {
  const el = document.getElementById('acc-palette');
  if (!el) return;
  if (!library || !Object.keys(library).length) {
    el.innerHTML = '<div style="padding:8px 10px;font-size:10px;color:var(--text-muted);">Loading…</div>';
    setTimeout(() => renderAccPalette(filter), 300);
    return;
  }
  const parts = Object.values(library).filter(p =>
    !filter || p.part_number?.toLowerCase().includes(filter.toLowerCase()) ||
    p.description?.toLowerCase().includes(filter.toLowerCase())
  ).slice(0, 30);
  if (!parts.length) {
    el.innerHTML = '<div style="padding:8px 10px;font-size:10px;color:var(--text-muted);">No results.</div>';
    return;
  }
  el.innerHTML = parts.map(p => {
    const st = detectSymbolType(p);
    const col = _paletteTypeColors[st] || '#6c63ff';
    const lbl = _paletteTypeLabels[st] || (st||'IC').slice(0,4).toUpperCase();
    const name = p.part_number || p.slug || '';
    const desc = (p.description || '').slice(0, 34);
    return `<div onclick="accPlaceComponent('${p.slug}');const _i=document.getElementById('acc-palette-search');if(_i)_i.value='';accShowPanel('nets');"
        title="${(p.description||'').slice(0,80)}"
        style="display:flex;align-items:center;gap:6px;padding:5px 10px 5px 7px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,0.04);border-left:3px solid transparent;transition:background 0.1s;"
        onmouseover="this.style.background='var(--surface2)'" onmouseout="this.style.background=''">
      <span style="font-size:9px;font-weight:700;color:${col};background:${col}22;border-radius:3px;padding:1px 4px;flex-shrink:0;">${lbl}</span>
      <div style="display:flex;flex-direction:column;gap:1px;min-width:0;flex:1;overflow:hidden;">
        <span style="font-family:monospace;font-size:10px;font-weight:700;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${name}</span>
        <span style="font-size:9px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${desc}</span>
      </div>
    </div>`;
  }).join('');
}

async function accPlaceComponent(slug) {
  const p = await fetchProfile(slug);
  profileCache[slug] = p;
  if (appCircuitEditor) appCircuitEditor.startPlace(slug, detectSymbolType(p));
}

// ── PDF viewer ─────────────────────────────────────────────────────────────
// Panel is now on the right side — managed by updatePdfPanel() and closePdfPanel()
function showPdfRef(page, label, text, rowEl) {
  document.querySelectorAll('.ref-selected').forEach(el => el.classList.remove('ref-selected'));
  if (rowEl) rowEl.classList.add('ref-selected');

  const panel = document.getElementById('pdf-panel');
  const frame = document.getElementById('pdf-frame');
  const hint = document.getElementById('pdf-page-hint');
  const box = document.getElementById('pdf-ref-highlight');
  const labelEl = document.getElementById('pdf-ref-label');
  const textEl = document.getElementById('pdf-ref-text');

  if (frame) {
    const newSrc = `/api/library/${selectedSlug}/pdf#page=${page}`;
    // Force reload if already on same URL (browser won't reload same src)
    if (frame.src.split('#')[0] === location.origin + `/api/library/${selectedSlug}/pdf` && frame.src !== newSrc) {
      frame.src = newSrc;
    } else if (frame.src !== newSrc) {
      frame.src = newSrc;
    } else {
      frame.src = ''; requestAnimationFrame(() => { frame.src = newSrc; });
    }
  }
  if (hint) hint.textContent = `Page ${page}`;
  if (panel) panel.style.display = 'flex';

  if (box && labelEl && textEl) {
    labelEl.textContent = `${label} — p.${page}`;
    textEl.textContent = text || '';
    box.classList.add('show');
  }
}

// ── Projects & Tabs ─────────────────────────────────────────────────────────
let projects = [];
// Each tab: { tabId, projectId, name, projectData, dirty }
let openTabs = [];
let activeTabId = null;
let _tabSeq = 0;

function _newTabId() { return 'tab-' + (++_tabSeq); }

// Snapshot current editor state into the active tab
function _snapshotActiveTab() {
  if (!activeTabId) return;
  const tab = openTabs.find(t => t.tabId === activeTabId);
  if (tab) {
    tab.projectData = JSON.parse(JSON.stringify(editor.project));
    tab.dirty = editor.dirty;
    tab.name = editor.project.name;
  }
}

// ── Project color — deterministic hue from project/tab ID ──────────────────
// Same function lives in pcb.html so colors match across both editors.
function _tabColor(id) {
  if (!id) return '#6c63ff';
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) & 0x7fffffff;
  // Use golden-ratio steps so consecutive IDs get well-separated hues
  return `hsl(${(h * 137.508) % 360 | 0}, 65%, 58%)`;
}

function renderTabs() {
  const bar = document.getElementById('project-tabs');
  if (!bar) return;

  // Detect name collisions — any name that appears on more than one tab
  const nameCounts = {};
  openTabs.forEach(t => { const n = (t.name || 'Untitled'); nameCounts[n] = (nameCounts[n] || 0) + 1; });

  bar.innerHTML = openTabs.map(t => {
    const isActive = t.tabId === activeTabId;
    const rawName  = t.name || 'Untitled';
    const colorId  = t.projectId || t.tabId; // use projectId when saved, tabId while unsaved
    const color    = _tabColor(colorId);

    // When names collide, append last-4 chars of the color-id as a disambiguator
    const suffix = nameCounts[rawName] > 1
      ? ` <span style="font-size:9px;color:var(--text-muted);font-weight:400;letter-spacing:.02em;">·${colorId.slice(-4)}</span>`
      : '';
    const dirtyDot = t.dirty ? ' <span style="color:var(--yellow)">●</span>' : '';
    const label = esc(rawName) + suffix + dirtyDot;

    const tooltip = nameCounts[rawName] > 1
      ? `${rawName}  (ID: …${colorId.slice(-6)})`
      : rawName;

    return `<div class="project-tab${isActive ? ' active' : ''}" onclick="switchTab('${t.tabId}')" title="${esc(tooltip)}" style="border-left:3px solid ${color};padding-left:9px;">
      <span class="project-tab-name">${label}</span>
      <span class="project-tab-close" onclick="event.stopPropagation();closeTab('${t.tabId}')" title="Close">✕</span>
    </div>`;
  }).join('') + `<button class="project-tab-new" onclick="newProject()" title="New project">+</button>`;
}

function switchTab(tabId) {
  if (tabId === activeTabId) return;
  _snapshotActiveTab();
  const tab = openTabs.find(t => t.tabId === tabId);
  if (!tab) return;
  activeTabId = tabId;
  editor.loadProject(JSON.parse(JSON.stringify(tab.projectData)));
  editor.dirty = tab.dirty;
  editor._status();
  _eagerLoadProfiles(tab.projectData);
}

function closeTab(tabId) {
  const tab = openTabs.find(t => t.tabId === tabId);
  if (!tab) return;
  if (tab.dirty && !confirm(`Close "${tab.name || 'Untitled'}" without saving?`)) return;
  const closedProjectId = tab.projectId;
  const idx = openTabs.indexOf(tab);
  openTabs.splice(idx, 1);
  if (activeTabId === tabId) {
    // Activate adjacent tab or open a blank
    const next = openTabs[Math.min(idx, openTabs.length - 1)];
    if (next) { activeTabId = next.tabId; editor.loadProject(JSON.parse(JSON.stringify(next.projectData))); editor.dirty = next.dirty; editor._status(); _eagerLoadProfiles(next.projectData); }
    else { activeTabId = null; editor.newProject('Untitled'); _addTab(editor.project, false); }
  } else { renderTabs(); }
  // Notify PCB iframe to close all child boards for this project
  if (closedProjectId) {
    const frame = document.getElementById('pcb-frame');
    if (frame?.contentWindow) {
      frame.contentWindow.postMessage({ type: 'projectClosed', projectId: closedProjectId }, '*');
    }
    if (_pcbLoadedProjectId === closedProjectId) _pcbLoadedProjectId = null;
  }
}

function _addTab(projectData, dirty) {
  const tab = { tabId: _newTabId(), projectId: projectData.id || null, name: projectData.name || 'Untitled', projectData: JSON.parse(JSON.stringify(projectData)), dirty: !!dirty };
  openTabs.push(tab);
  activeTabId = tab.tabId;
  renderTabs();
  return tab;
}

function _eagerLoadProfiles(data) {
  const slugsNeeded = [...new Set((data.components || [])
    .filter(c => c.symType === 'ic' && c.slug && !profileCache[c.slug])
    .map(c => c.slug))];
  for (const slug of slugsNeeded) {
    fetch(`/api/library/${slug}`).then(r => r.json()).then(p => { profileCache[slug] = p; editor._render(); }).catch(() => {});
  }
}

async function loadProjects() {
  const res = await fetch('/api/projects');
  projects = await res.json();
  renderProjects();
}

function renderProjects() {
  const list = document.getElementById('projects-dropdown-list');
  if (!list) return;
  if (!projects.length) {
    list.innerHTML = '<div style="color:var(--text-muted);font-size:11px;text-align:center;padding:12px;">No projects yet.<br>Click + to start.</div>';
    return;
  }
  const openIds = new Set(openTabs.map(t => t.projectId).filter(Boolean));
  list.innerHTML = projects.map(p => `
    <div class="project-item ${openIds.has(p.id) ? 'active' : ''}" onclick="openProject('${p.id}')">
      <div class="project-item-name">${esc(p.name || 'Untitled')}</div>
      <div class="project-item-meta">${p.component_count} components · ${p.updated_at ? new Date(p.updated_at).toLocaleDateString() : ''}</div>
    </div>`).join('');
}

function toggleProjectsDropdown(e) {
  const dd = document.getElementById('projects-dropdown');
  const btn = document.getElementById('projects-dropdown-btn');
  if (!dd.classList.contains('hidden')) { dd.classList.add('hidden'); return; }
  loadProjects();
  const rect = btn.getBoundingClientRect();
  dd.style.top = (rect.bottom + 4) + 'px';
  dd.style.left = rect.left + 'px';
  dd.classList.remove('hidden');
  setTimeout(() => {
    function outside(ev) { if (!dd.contains(ev.target) && ev.target !== btn) { dd.classList.add('hidden'); document.removeEventListener('mousedown', outside); } }
    document.addEventListener('mousedown', outside);
  }, 0);
}

function newProject() {
  const name = prompt('Project name:', 'New Schematic');
  if (!name) return;
  _snapshotActiveTab();
  editor.newProject(name);
  _addTab(editor.project, false);
  switchSection('schematic');
  document.getElementById('projects-dropdown')?.classList.add('hidden');
}

async function loadLatestProject() {
  await loadProjects();
  if (!projects.length) { alert('No saved projects yet.'); return; }
  await openProject(projects[0].id);
}

async function openProject(id) {
  // If already open in a tab, switch to it
  const existing = openTabs.find(t => t.projectId === id);
  if (existing) { switchTab(existing.tabId); document.getElementById('projects-dropdown')?.classList.add('hidden'); switchSection('schematic'); return; }
  const res = await fetch(`/api/projects/${id}`);
  const data = await res.json();
  _snapshotActiveTab();
  editor.loadProject(data);
  const tab = _addTab(data, false);
  tab.projectId = id;
  activeTabId = tab.tabId;
  switchSection('schematic');
  document.getElementById('projects-dropdown')?.classList.add('hidden');
  renderTabs();
  _eagerLoadProfiles(data);
}

async function saveProject() {
  const id = await editor.saveProject();
  // Update active tab
  const tab = openTabs.find(t => t.tabId === activeTabId);
  if (tab) { tab.projectId = id; tab.dirty = false; tab.name = editor.project.name; tab.projectData = JSON.parse(JSON.stringify(editor.project)); }
  await loadProjects();
  _notifyPcbSync(); // clear stale banner after save
}

async function deleteProject(id) {
  if (!confirm('Delete this project?')) return;
  await fetch(`/api/projects/${id}`, { method: 'DELETE' });
  const tab = openTabs.find(t => t.projectId === id);
  if (tab) closeTab(tab.tabId);
  await loadProjects();
}

// Open example circuit from datasheet tab → imports into current schematic
function openExampleInEditor() {
  if (!selectedSlug) return;
  fetch(`/api/library/${selectedSlug}`).then(r => r.json()).then(p => {
    profileCache[selectedSlug] = p;
    switchSection('schematic');
    requestAnimationFrame(() => editor.startPlaceGroup(p));
  });
}

loadProjects();

// ── Symbol definitions ─────────────────────────────────────────────────────
const SYMDEFS = {
  resistor:     { w:60,  h:20, ports:[{dx:-30,dy:0,name:'P1'},{dx:30,dy:0,name:'P2'}] },
  capacitor:    { w:40,  h:40, ports:[{dx:0,dy:-20,name:'P1'},{dx:0,dy:20,name:'P2'}] },
  capacitor_pol:{ w:40,  h:40, ports:[{dx:0,dy:-20,name:'+'},{dx:0,dy:20,name:'-'}] },
  inductor:     { w:80,  h:20, ports:[{dx:-40,dy:0,name:'P1'},{dx:40,dy:0,name:'P2'}] },
  vcc:          { w:30,  h:40, ports:[{dx:0,dy:20,name:'VCC'}] },
  gnd:          { w:30,  h:44, ports:[{dx:0,dy:-20,name:'GND'}] },
  amplifier:    { w:100, h:80, ports:[{dx:-50,dy:0,name:'IN'},{dx:50,dy:0,name:'OUT'},{dx:0,dy:40,name:'GND'}] },
  opamp:        { w:100, h:80, ports:[{dx:-50,dy:-20,name:'+'},{dx:-50,dy:20,name:'-'},{dx:50,dy:0,name:'OUT'}] },
  diode:        { w:60,  h:20, ports:[{dx:-30,dy:0,name:'A'},{dx:30,dy:0,name:'K'}] },
  led:          { w:60,  h:20, ports:[{dx:-30,dy:0,name:'A'},{dx:30,dy:0,name:'K'}] },
  npn:          { w:60,  h:70, ports:[{dx:-30,dy:0,name:'B'},{dx:20,dy:-25,name:'C'},{dx:20,dy:25,name:'E'}] },
  pnp:          { w:60,  h:70, ports:[{dx:-30,dy:0,name:'B'},{dx:20,dy:-25,name:'E'},{dx:20,dy:25,name:'C'}] },
  nmos:         { w:60,  h:70, ports:[{dx:-30,dy:0,name:'G'},{dx:20,dy:-25,name:'D'},{dx:20,dy:25,name:'S'}] },
  ic:           { w:120, h:80, ports:[] }, // fallback; real IC defs use _icLayout()
};

// ── Example circuit builder ────────────────────────────────────────────────
function buildExampleCircuit(profile) {
  const slug = profile.slug || Object.keys(library).find(k => library[k].part_number === profile.part_number) || '';
  const type = detectSymbolType(profile);

  if (type === 'amplifier') {
    // PMA3-83LNW+ application circuit (Fig.1 p.4) — grid=20
    //
    // Port positions (verified by rotation math):
    //   U1 amp  at (320,240) rot=0: IN=(270,240)  OUT=(370,240)  GND=(320,280)
    //   C2 cap  at (160,240) rot=1: P2=(140,240)  P1=(180,240)    [input coupling 10pF]
    //   L1 ind  at (220,340) rot=1: P1=(220,310)  P2=(220,370)    [shunt 18nH, P1=top]
    //   C3 cap  at (460,240) rot=1: P2=(440,240)  P1=(480,240)    [output DC block 100pF]
    //   L2 ind  at (400,140) rot=1: P1=(400,110)  P2=(400,170)    [RF choke 39nH, P1=top]
    //   C1 cap  at (480,140) rot=0: P1=(480,120)  P2=(480,160)    [VDD bypass 0.01µF]
    //   VDD1 vcc at (400,60) port=(400,70)   VDD2 vcc at (480,60) port=(480,70)
    //   GND1 at (220,440) port=(220,430)     GND2 at (320,360) port=(320,350)
    //   GND3 at (480,220) port=(480,210)
    //
    // Key junctions (3-way, show dot):
    //   Signal input node (220,240): C2.P1→ + →U1.IN + ↓L1.P1
    //   Bias/output node  (400,240): U1.OUT→ + →C3.P2 + ↑L2.P2
    const comps = [
      { id:'u1',   slug, symType:'amplifier', designator:'U1',  value:profile.part_number||'', x:320, y:240, rotation:0 },
      { id:'c2',   slug:'CAPACITOR', symType:'capacitor',  designator:'C2',  value:'10pF',    x:160, y:240, rotation:1 },
      { id:'l1',   slug:'INDUCTOR',  symType:'inductor',   designator:'L1',  value:'18nH',    x:220, y:340, rotation:1 },
      { id:'c3',   slug:'CAPACITOR', symType:'capacitor',  designator:'C3',  value:'100pF',   x:460, y:240, rotation:1 },
      { id:'l2',   slug:'INDUCTOR',  symType:'inductor',   designator:'L2',  value:'39nH',    x:400, y:140, rotation:1 },
      { id:'c1',   slug:'CAPACITOR', symType:'capacitor',  designator:'C1',  value:'0.01µF',  x:480, y:140, rotation:0 },
      { id:'vdd1', slug:'VCC', symType:'vcc', designator:'VDD', value:'5V/6V', x:400, y:60,  rotation:0 },
      { id:'vdd2', slug:'VCC', symType:'vcc', designator:'VDD', value:'5V/6V', x:480, y:60,  rotation:0 },
      { id:'gnd1', slug:'GND', symType:'gnd', designator:'GND', value:'',      x:220, y:440, rotation:0 },
      { id:'gnd2', slug:'GND', symType:'gnd', designator:'GND', value:'',      x:320, y:360, rotation:0 },
      { id:'gnd3', slug:'GND', symType:'gnd', designator:'GND', value:'',      x:480, y:220, rotation:0 },
    ];
    const wires = [
      // RF-IN stub → C2.P2
      { id:'w1', points:[{x:80,y:240},{x:140,y:240}] },
      // C2.P1 → signal junction (220,240)
      { id:'w2', points:[{x:180,y:240},{x:220,y:240}] },
      // signal junction → U1.IN  [3-way junction at (220,240)]
      { id:'w3', points:[{x:220,y:240},{x:270,y:240}] },
      // signal junction ↓ L1.P1 (shunt)
      { id:'w4', points:[{x:220,y:240},{x:220,y:310}] },
      // L1.P2 ↓ GND1
      { id:'w5', points:[{x:220,y:370},{x:220,y:430}] },
      // U1.OUT → bias/output junction (400,240)
      { id:'w6', points:[{x:370,y:240},{x:400,y:240}] },
      // bias junction → C3.P2  [3-way junction at (400,240)]
      { id:'w7', points:[{x:400,y:240},{x:440,y:240}] },
      // L2.P2 ↓ bias junction
      { id:'w8', points:[{x:400,y:170},{x:400,y:240}] },
      // C3.P1 → RF-OUT stub
      { id:'w9', points:[{x:480,y:240},{x:580,y:240}] },
      // VDD1 ↓ L2.P1
      { id:'w10', points:[{x:400,y:70},{x:400,y:110}] },
      // VDD2 ↓ C1.P1
      { id:'w11', points:[{x:480,y:70},{x:480,y:120}] },
      // C1.P2 ↓ GND3
      { id:'w12', points:[{x:480,y:160},{x:480,y:210}] },
      // U1.GND ↓ GND2
      { id:'w13', points:[{x:320,y:280},{x:320,y:350}] },
    ];
    // Net labels for RF port stubs
    const labels = [
      { id:'lbl1', name:'RF_IN',  x:80,  y:240, rotation:0 },
      { id:'lbl2', name:'RF_OUT', x:580, y:240, rotation:0 },
    ];
    return { components: comps, wires, labels };
  }

  // Generic: place IC + passives in a simple layout
  const passives = profile.required_passives || [];
  const comps = [
    { id:'u1', slug, symType: type === 'opamp' ? 'opamp' : 'ic', designator:'U1', value:profile.part_number||'', x:300, y:200, rotation:0 }
  ];
  const wires = [];
  const prefixes = { capacitor:'C', inductor:'L', resistor:'R' };
  passives.slice(0, 8).forEach((pas, i) => {
    const st = pas.type === 'inductor' ? 'inductor' : (pas.type === 'capacitor' ? 'capacitor' : 'resistor');
    const px = 460, py = 80 + i * 80;
    const prefix = prefixes[pas.type] || 'X';
    comps.push({ id:`p${i}`, slug: st.toUpperCase(), symType: st, designator:`${prefix}${i+1}`, value: pas.value||'', x: px, y: py, rotation:0 });
    wires.push({ id:`wp${i}`, points:[{x:340,y:200},{x:px-20,y:200},{x:px-20,y:py},{x:px-20,y:py}] });
  });
  return { components: comps, wires };
}

// ── Restore last active section on page load ──────────────────────────────
(function _restoreSection() {
  try {
    const saved = localStorage.getItem('eda_active_section');
    if (saved && ['library','schematic','pcb','build'].includes(saved)) {
      // Defer so all DOM + scripts are ready
      setTimeout(() => switchSection(saved), 0);
    }
  } catch(_) {}
})();

// ── Warn before unload if there are unsaved changes ───────────────────────
// Global dirty flag — set by any editor that modifies state.
// SchematicEditor sets this via _status(), PCB editor via _snapshot().
window._edaDirty = false;

window.addEventListener('beforeunload', function(e) {
  if (window._edaDirty) {
    e.preventDefault();
    e.returnValue = 'You have unsaved changes. Are you sure you want to leave?';
    return e.returnValue;
  }
});

