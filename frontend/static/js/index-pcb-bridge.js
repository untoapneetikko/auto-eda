// Called when PCB iframe loads (or when switching to PCB tab) — re-send state
function _pcbFrameReady() {
  const frame = document.getElementById('pcb-frame');
  if (!frame?.contentWindow) return;
  const tab = openTabs.find(t => t.tabId === activeTabId);
  const projectId = tab?.projectId || editor.project?.id || null;
  try { frame.contentWindow.postMessage({ type: 'schematicDirty', projectId, isDirty: editor.dirty }, '*'); } catch(_) {}
}

// ── Unified PCB Layout entry point ──────────────────────────────────────────
// Single function called by the "→ PCB Layout" button.
// - If no linked PCB board exists: show Create dialog
// - If one exists: switch to PCB tab and send update
async function openPCBLayout() {
  const { components } = editor.project;
  if (!components.length) { alert('Schematic is empty — place some components first.'); return; }
  // Save schematic first so we have a project ID
  const id = await editor.saveProject();
  const tab = openTabs.find(t => t.tabId === activeTabId);
  if (tab) { tab.projectId = id; tab.dirty = false; tab.name = editor.project.name; tab.projectData = JSON.parse(JSON.stringify(editor.project)); }
  await loadProjects();
  _notifyPcbSync();
  // Check if a PCB board already exists for this project
  try {
    const res = await fetch('/api/pcb-boards?projectId=' + encodeURIComponent(id));
    const boards = await res.json();
    if (boards && boards.length > 0) {
      // Linked board exists — switch to PCB and update
      _doPCBImport(id);
    } else {
      // No linked board — auto-create one immediately, inheriting all components
      // from the schematic. Board size is estimated from component count.
      const n = components.filter(c => !['vcc','gnd','pwr'].includes(c.symType)).length;
      const boardW = n <= 5 ? 60 : n <= 15 ? 80 : n <= 30 ? 120 : 160;
      const boardH = n <= 5 ? 40 : n <= 15 ? 60 : n <= 30 ? 80 : 100;
      _doPCBImport(id, { boardW, boardH });
    }
  } catch(e) {
    console.error('openPCBLayout:', e);
    _doPCBImport(id, { boardW: 80, boardH: 60 });
  }
}

function closeCreatePCBModal() {
  document.getElementById('create-pcb-modal').style.display = 'none';
}

async function doCreatePCBLayout() {
  const w = parseFloat(document.getElementById('pcb-board-width').value) || 80;
  const h = parseFloat(document.getElementById('pcb-board-height').value) || 60;
  if (w < 10 || h < 10) { document.getElementById('create-pcb-status').textContent = 'Board dimensions too small.'; return; }
  const st = document.getElementById('create-pcb-status');
  st.textContent = 'Creating…';
  const id = openTabs.find(t => t.tabId === activeTabId)?.projectId || editor.project?.id;
  if (!id) { st.textContent = 'No project saved — try again.'; return; }
  closeCreatePCBModal();
  _doPCBImport(id, { boardW: w, boardH: h });
}

// Auto-load the saved PCB board for a project when the user switches to the
let _pcbLoadedProjectId = null; // track last project sent to PCB iframe
let _pcbImporting = false;      // re-entrancy guard for _doPCBImport

// PCB tab — fires only if a board already exists, so first-time creation still
// goes through the "→ PCB Layout" button flow.
async function _pcbAutoLoad(projectId) {
  if (!projectId) return;
  const frame = document.getElementById('pcb-frame');
  // Already loaded this project and schematic hasn't changed — just make the
  // canvas resize properly (it may have been sized while hidden).
  if (_pcbLoadedProjectId === projectId && !editor.dirty) {
    try { frame?.contentWindow?.postMessage({ type: 'tabVisible' }, '*'); } catch(_) {}
    return;
  }
  try {
    const r = await fetch('/api/pcb-boards?projectId=' + encodeURIComponent(projectId));
    const boards = await r.json();
    if (boards && boards.length > 0) {
      _doPCBImport(projectId);
      _pcbLoadedProjectId = projectId;
    } else {
      // No board yet — just ensure canvas is sized
      try { frame?.contentWindow?.postMessage({ type: 'tabVisible' }, '*'); } catch(_) {}
    }
  } catch(_) {}
}

// Internal: switch to PCB section and send importProject postMessage
async function _doPCBImport(projectId, boardOpts) {
  if (_pcbImporting) return; // prevent re-entrancy from switchSection → _pcbAutoLoad loop
  _pcbImporting = true;
  switchSection('pcb');
  const frame = document.getElementById('pcb-frame');
  // Pre-load IC profiles into profileCache so buildNetlist() gets real pin names, not placeholders
  const icComps = (editor.project?.components || []).filter(c => c.symType === 'ic' && c.slug && !profileCache[c.slug]);
  if (icComps.length) {
    await Promise.all(icComps.map(async c => {
      try { const r = await fetch(`/api/library/${c.slug}`); if (r.ok) profileCache[c.slug] = await r.json(); } catch(_) {}
    }));
  }
  // Compute authoritative netlist from this schematic so PCB importer uses exact same names
  let netlist = null;
  try {
    const r = await buildNetlist();
    // Convert namedNets array [{name, pins}, ...] to dict {name: pins, ...}
    const nn = r.namedNets || [];
    netlist = {};
    for (const n of nn) { if (n.name && Array.isArray(n.pins)) netlist[n.name] = n.pins; }
  } catch(_) {}
  let done = false;
  const onDone = (ev) => {
    if (ev.data?.type === 'importProjectDone') {
      done = true;
      window.removeEventListener('message', onDone);
      _pcbLoadedProjectId = projectId; // mark as loaded so next tab switch doesn't reimport
    }
  };
  window.addEventListener('message', onDone);
  let attempts = 0;
  const tryPost = () => {
    if (done) { _pcbImporting = false; return; }
    attempts++;
    try { frame.contentWindow?.postMessage({ type: 'importProject', projectId, boardOpts: boardOpts || null, netlist }, '*'); } catch(_) {}
    if (attempts < 20) {
      setTimeout(tryPost, 400);
    } else {
      _pcbImporting = false; // exhausted retries — release lock
    }
  };
  setTimeout(tryPost, 600);
}

// Listen for messages from PCB iframe
window.addEventListener('message', e => {
  if (e.data?.type === 'backToSchematic' && e.source === document.getElementById('pcb-frame')?.contentWindow) switchSection('schematic');
  if (e.data?.type === 'updatePCBRequest' && e.source === document.getElementById('pcb-frame')?.contentWindow) openPCBLayout();
  if (e.data?.type === 'boardRefsChanged' && Array.isArray(e.data.refs) && _leBomData.length) {
    const liveRefs = new Set(e.data.refs);
    let changed = false;
    _leBomData.forEach(c => {
      const ref = c.designator || c.id || '';
      const nowAdded = liveRefs.has(ref);
      if (c._leAdded !== nowAdded) { c._leAdded = nowAdded; changed = true; }
    });
    if (changed) leRenderBomPanel(_leBomData);
  }
});

// ── Design Bundle Export / Import (sharing) ───────────────────────────────────

async function exportDesignBundle() {
  if (!editor.project.id) {
    // Save first so we have a server-side copy
    await editor.saveProject();
  }
  if (!editor.project.components.length) { alert('Schematic is empty.'); return; }

  const res = await fetch(`/api/export-design/${editor.project.id}`);
  if (!res.ok) { alert('Export failed: ' + (await res.json()).error); return; }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (editor.project.name || 'design').replace(/[^a-zA-Z0-9_-]/g, '_') + '.schematic';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function importDesignBundle(input) {
  const file = input.files[0];
  if (!file) return;
  input.value = '';

  let bundle;
  try {
    bundle = JSON.parse(await file.text());
  } catch {
    alert('Invalid file — could not parse JSON.'); return;
  }
  if (bundle.format !== 'schematic-designer-v1') {
    alert('Unrecognised format: ' + bundle.format); return;
  }

  const res = await fetch('/api/import-design', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bundle)
  });
  const d = await res.json();
  if (!res.ok) { alert('Import failed: ' + d.error); return; }

  // Reload library in case new profiles were installed
  if (d.installed.length) {
    const lib = await fetch('/api/library').then(r => r.json());
    Object.assign(library, lib);
    renderSchPalette(document.getElementById('sch-lib-search')?.value || '');
  }

  // Load the imported project
  await openProject(d.project_id);

  const msg = d.installed.length
    ? `Imported! Installed ${d.installed.length} new component(s): ${d.installed.join(', ')}.`
    : 'Imported! All components were already in your library.';
  alert(msg);
}

// ── AI Circuit Generator ──────────────────────────────────────────────────────
let _aiGenPollTimer = null;
let _aiGenTicketId = null;
let _aiGenSelectedSlugs = new Set();

async function openAIGenerateModal() {
  document.getElementById('ai-gen-modal').style.display = 'flex';
  document.getElementById('ai-gen-prompt-wrap').style.display = 'none';
  document.getElementById('ai-gen-status').style.display = 'none';
  document.getElementById('ai-gen-submit-btn').disabled = false;
  _aiGenSelectedSlugs = new Set();
  // Populate component grid from library
  const grid = document.getElementById('ai-gen-comp-grid');
  if (grid) {
    grid.innerHTML = '<span style="color:var(--text-muted);font-size:11px;">Loading library…</span>';
    try {
      const lib = await fetch('/api/library').then(r => r.json());
      if (!lib || !lib.length) {
        grid.innerHTML = '<span style="color:var(--text-muted);font-size:11px;">No components in library.</span>';
        return;
      }
      grid.innerHTML = lib.map(c => {
        const label = c.part_number || c.slug;
        const dot = c.builtin ? '#4b5563' : '#a78bfa';
        return `<div class="ai-gen-chip" data-slug="${esc(c.slug)}"
          onclick="aiGenToggleChip(this, '${esc(c.slug)}')"
          title="${esc(c.description || c.slug)}"
          style="display:inline-flex;align-items:center;gap:5px;padding:3px 8px;border-radius:12px;
                 border:1px solid var(--border);background:var(--surface);cursor:pointer;
                 font-size:11px;color:var(--text-dim);user-select:none;transition:all 0.12s;">
          <span style="width:7px;height:7px;border-radius:50%;background:${dot};flex-shrink:0;"></span>
          ${esc(label)}
        </div>`;
      }).join('');
    } catch(e) {
      grid.innerHTML = '<span style="color:var(--text-muted);font-size:11px;">Could not load library.</span>';
    }
  }
}

function aiGenToggleChip(el, slug) {
  if (_aiGenSelectedSlugs.has(slug)) {
    _aiGenSelectedSlugs.delete(slug);
    el.style.background = 'var(--surface)';
    el.style.borderColor = 'var(--border)';
    el.style.color = 'var(--text-dim)';
  } else {
    _aiGenSelectedSlugs.add(slug);
    el.style.background = 'rgba(124,58,237,0.18)';
    el.style.borderColor = '#7c3aed';
    el.style.color = '#a78bfa';
  }
}

function closeAIGenerateModal() {
  document.getElementById('ai-gen-modal').style.display = 'none';
  if (_aiGenPollTimer) { clearInterval(_aiGenPollTimer); _aiGenPollTimer = null; }
}

function _aiGenSetStatus(icon, msg, pct, showOpen) {
  document.getElementById('ai-gen-status').style.display = 'block';
  document.getElementById('ai-gen-status-icon').textContent = icon;
  document.getElementById('ai-gen-status-msg').textContent = msg;
  document.getElementById('ai-gen-progress-fill').style.width = pct + '%';
  document.getElementById('ai-gen-open-wrap').style.display = showOpen ? 'block' : 'none';
}

async function submitAIGenerate() {
  const desc = (document.getElementById('ai-gen-desc')?.value || '').trim();
  if (!desc) { alert('Please describe the circuit you want to build.'); return; }

  const btn = document.getElementById('ai-gen-submit-btn');
  btn.disabled = true;
  _aiGenSetStatus('\u23F3', 'Submitting to AI worker\u2026', 10, false);

  try {
    const res = await fetch('/api/generate-circuit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: desc,
        library_slugs: Array.from(_aiGenSelectedSlugs),
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to submit');
    }
    const data = await res.json();
    _aiGenTicketId = data.ticket_id;
    _aiGenSetStatus('\u23F3', `Generating\u2026 (ticket #${_aiGenTicketId})`, 20, false);

    // Start polling every 3 seconds
    if (_aiGenPollTimer) clearInterval(_aiGenPollTimer);
    _aiGenPollTimer = setInterval(_aiGenPoll, 3000);
  } catch(e) {
    _aiGenSetStatus('\u274C', `Error: ${e.message}`, 0, false);
    btn.disabled = false;
  }
}

async function _aiGenPoll() {
  if (!_aiGenTicketId) return;
  try {
    const data = await fetch('/api/gen-tickets').then(r => r.json());
    const ticket = (data.tickets || []).find(t => t.id === _aiGenTicketId);
    if (!ticket) return;
    const status = ticket.status;
    if (status === 'running') {
      _aiGenSetStatus('\u23F3', `AI is generating your circuit\u2026 (ticket #${_aiGenTicketId})`, 55, false);
    } else if (status === 'done') {
      clearInterval(_aiGenPollTimer); _aiGenPollTimer = null;
      _aiGenSetStatus('\u2705', 'Circuit generated! Open the latest project to view it.', 100, true);
      // Refresh projects list
      if (typeof loadProjects === 'function') loadProjects();
    } else if (status === 'error' || status === 'retracted') {
      clearInterval(_aiGenPollTimer); _aiGenPollTimer = null;
      const errMsg = ticket.error || status;
      _aiGenSetStatus('\u274C', `Generation failed: ${errMsg}`, 0, false);
      document.getElementById('ai-gen-submit-btn').disabled = false;
    }
  } catch(e) { /* network hiccup — keep polling */ }
}

async function aiGenOpenLatestProject() {
  // Load the most recently updated project and open it
  try {
    const projects = await fetch('/api/projects').then(r => r.json());
    if (!projects || !projects.length) { alert('No projects found.'); return; }
    const latest = projects[0]; // already sorted by updated_at desc
    if (typeof openProject === 'function') await openProject(latest.id);
  } catch(e) {
    alert('Could not open project: ' + e.message);
  }
}

async function buildAIPrompt() {
  const desc = document.getElementById('ai-gen-desc').value.trim();
  if (!desc) { alert('Please describe the circuit first.'); return; }

  // Fetch current library
  const res = await fetch('/api/library');
  const lib = await res.json();
  const compList = Object.values(lib).map(p =>
    `  - slug: "${p.slug}", part: "${p.part_number}", desc: "${(p.description||'').slice(0,80)}"`
  ).join('\n');

  // Grid and coordinate guidance
  const prompt = `You are a schematic layout generator. Generate a schematic circuit JSON for the following description and POST it to http://localhost:3030/api/projects using fetch().

## CIRCUIT DESCRIPTION
${desc}

## AVAILABLE COMPONENTS (use slugs exactly as shown)
${compList}

## OUTPUT FORMAT
POST to http://localhost:3030/api/projects with Content-Type: application/json:
{
  "name": "Circuit name",
  "components": [
    {
      "id": "c1",
      "slug": "COMPONENT_SLUG",
      "symType": "ic",
      "designator": "U1",
      "value": "part number or value",
      "x": 200,
      "y": 200,
      "rotation": 0
    }
  ],
  "wires": [
    {
      "id": "w1",
      "points": [{"x": 100, "y": 100}, {"x": 200, "y": 100}]
    }
  ],
  "labels": [
    {
      "id": "l1",
      "name": "VCC",
      "x": 150,
      "y": 80,
      "rotation": 0
    }
  ]
}

## LAYOUT RULES
- Grid: 20 units. All x/y coordinates MUST be multiples of 20.
- Spread components with 200-400 units between them horizontally, 150-300 units vertically.
- symType values: "ic", "resistor", "capacitor", "capacitor_pol", "inductor", "vcc", "gnd", "diode", "led", "npn", "nmos"
- For passives: use "resistor", "capacitor", etc. For ICs: use "ic". For power: use "vcc" or "gnd".
- Wires use polyline points — route through intermediate points for L-shapes.
- Use net labels (labels array) for power rails (VCC, GND, 3V3) instead of wiring every component to the same rail.
- Place decoupling capacitors close (within 60 units) to their associated IC power pins.
- designator: R1, R2, C1, C2, U1, U2, etc. — unique per component.

## IMPORTANT
After POSTing the project, the schematic app will load it automatically.
Only use slugs from the AVAILABLE COMPONENTS list above.
Generate wiring that makes electrical sense for the described circuit.

Execute the POST request now.`;

  document.getElementById('ai-gen-prompt').value = prompt;
  document.getElementById('ai-gen-prompt-wrap').style.display = 'flex';
}

// ── Compatibility / DRC Check ─────────────────────────────────────────────────
