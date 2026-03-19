// ── Upload ─────────────────────────────────────────────────────────────────
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file?.type === 'application/pdf') uploadFile(file);
});
fileInput.addEventListener('change', e => { if (e.target.files[0]) uploadFile(e.target.files[0]); });

async function uploadFile(file) {
  const status = document.getElementById('upload-status');
  const instructions = document.getElementById('parse-instructions');
  status.className = 'upload-status uploading';
  status.textContent = `Uploading ${file.name}...`;
  instructions.style.display = 'none';

  const form = new FormData();
  form.append('file', file);

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: form });
    const data = await res.json();

    if (!res.ok) throw new Error(data.error);

    status.className = 'upload-status success';
    status.textContent = `✓ Uploaded — ${data.charCount.toLocaleString()} chars extracted (${data.confidence} confidence)`;

    instructions.style.display = 'block';
    instructions.innerHTML = `<strong style="color:var(--text);">⏳ Parsing started automatically…</strong><div style="font-size:11px;color:var(--text-muted);margin-top:5px;">The datasheet parser is running. The component will appear in the library when complete.</div>`;

    await loadLibrary();
    selectPart(data.slug);

  } catch (err) {
    status.className = 'upload-status error';
    status.textContent = '✗ ' + err.message;
  }
}

function buildPrompt(slug) {
  return `parse datasheet ${slug}`;
}

async function copyPrompt(el) {
  await navigator.clipboard.writeText(el.textContent.trim());
  const orig = el.style.borderColor;
  el.style.borderColor = 'var(--green)';
  setTimeout(() => el.style.borderColor = orig, 1000);
}

// ── Library (tree view) ───────────────────────────────────────────────────
const _libTypeLabels = {
  ic:'ICs', amplifier:'Amplifiers', resistor:'Resistors', capacitor:'Capacitors',
  capacitor_pol:'Polar Capacitors', inductor:'Inductors', led:'LEDs', diode:'Diodes',
  npn:'NPN Transistors', pnp:'PNP Transistors', nmos:'N-FETs', pmos:'P-FETs',
  vcc:'Power Symbols', gnd:'Ground Symbols', opamp:'Op-Amps', connector:'Connectors',
};
const _libTypeOrder = ['ic','amplifier','opamp','resistor','capacitor','capacitor_pol','inductor','diode','led','npn','pnp','nmos','pmos','connector','vcc','gnd'];
let _libTreeOpen = {};   // persisted open/closed state per group

function renderLibrary(filter = '') {
  const list = document.getElementById('library-list');
  document.getElementById('lib-count').textContent = Object.keys(library).length;

  const parts = Object.values(library).filter(p => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return p.part_number?.toLowerCase().includes(q) ||
           p.description?.toLowerCase().includes(q) ||
           p.slug?.toLowerCase().includes(q);
  });

  if (parts.length === 0) {
    list.innerHTML = `<div style="color:var(--text-muted);font-size:12px;text-align:center;padding:20px;">No components yet.<br>Upload a datasheet PDF to start.</div>`;
    return;
  }

  // Group by symbol_type
  const groups = {};
  for (const p of parts) {
    const t = p.symbol_type || 'ic';
    (groups[t] = groups[t] || []).push(p);
  }
  // Sort each group alphabetically
  for (const k of Object.keys(groups)) {
    groups[k].sort((a, b) => (a.part_number || a.slug).localeCompare(b.part_number || b.slug));
  }

  // If searching, auto-open all groups
  if (filter) Object.keys(groups).forEach(k => { _libTreeOpen[k] = true; });

  // Render tree
  const orderedKeys = _libTypeOrder.filter(k => groups[k]);
  const extraKeys = Object.keys(groups).filter(k => !_libTypeOrder.includes(k)).sort();
  const allKeys = [...orderedKeys, ...extraKeys];

  let html = '';
  for (const type of allKeys) {
    const items = groups[type];
    const label = _libTypeLabels[type] || type.charAt(0).toUpperCase() + type.slice(1);
    const isOpen = _libTreeOpen[type] !== false; // default open
    html += `<div class="lib-tree-group">
      <div class="lib-tree-header ${isOpen ? 'open' : ''}" onclick="toggleLibGroup('${type}')">
        <span class="lib-tree-arrow">▶</span>
        <span>${label}</span>
        <span class="lib-tree-badge">${items.length}</span>
      </div>
      <div class="lib-tree-children" style="${isOpen ? '' : 'display:none;'}">
        ${items.map(p => {
          const name = p.part_number || p.slug;
          const active = selectedSlug === p.slug;
          return `<div class="lib-tree-item ${active ? 'active' : ''}" onclick="selectPart('${p.slug}')" oncontextmenu="event.preventDefault();showLibContextMenu(event,'${p.slug}')" title="${p.description || ''}">
            <span class="tree-part">${name}</span>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }
  list.innerHTML = html;
}

function toggleLibGroup(type) {
  _libTreeOpen[type] = _libTreeOpen[type] === false ? true : false;
  renderLibrary(document.getElementById('lib-search').value);
}

// ── Right-click context menu ─────────────────────────────────────────────
function showLibContextMenu(e, slug) {
  // Remove any existing menu
  const old = document.getElementById('lib-ctx-menu');
  if (old) old.remove();

  const allTypes = [..._libTypeOrder];
  // Add any extra types from current library
  Object.values(library).forEach(p => {
    const t = p.symbol_type || 'ic';
    if (!allTypes.includes(t)) allTypes.push(t);
  });

  const menu = document.createElement('div');
  menu.id = 'lib-ctx-menu';
  menu.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px;background:var(--surface,#1a1d27);border:1px solid var(--border,#2e3250);border-radius:6px;min-width:160px;z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,0.5);padding:4px 0;`;

  // Header
  const partName = library[slug]?.part_number || slug;
  menu.innerHTML = `<div style="padding:5px 12px;font-size:10px;font-weight:700;letter-spacing:.06em;color:var(--text-muted,#64748b);border-bottom:1px solid var(--border,#2e3250);margin-bottom:2px;">Move <span style="color:var(--accent,#6c63ff);font-family:monospace;">${partName}</span> to</div>`;

  for (const type of allTypes) {
    const label = _libTypeLabels[type] || type.charAt(0).toUpperCase() + type.slice(1);
    const current = library[slug]?.symbol_type || 'ic';
    const isCurrent = type === current;
    const row = document.createElement('div');
    row.style.cssText = `padding:5px 12px;font-size:12px;cursor:${isCurrent ? 'default' : 'pointer'};color:${isCurrent ? 'var(--accent,#6c63ff)' : 'var(--text,#e2e8f0)'};font-weight:${isCurrent ? '700' : '400'};display:flex;align-items:center;gap:6px;transition:background 0.1s;`;
    row.innerHTML = `${isCurrent ? '● ' : ''}${label}`;
    if (!isCurrent) {
      row.onmouseover = () => row.style.background = 'var(--surface2,#22263a)';
      row.onmouseout = () => row.style.background = '';
      row.onclick = () => { menu.remove(); movePartToCategory(slug, type); };
    }
    menu.appendChild(row);
  }

  document.body.appendChild(menu);

  // Dismiss on click outside
  const dismiss = (ev) => {
    if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('mousedown', dismiss, true); }
  };
  setTimeout(() => document.addEventListener('mousedown', dismiss, true), 0);
}

async function movePartToCategory(slug, newType) {
  try {
    const res = await fetch(`/api/library/${encodeURIComponent(slug)}/symbol_type`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol_type: newType })
    });
    if (!res.ok) throw new Error('Failed to update');
    // Update local cache and re-render
    if (library[slug]) library[slug].symbol_type = newType;
    renderLibrary(document.getElementById('lib-search')?.value || '');
  } catch (e) {
    alert('Move failed: ' + e.message);
  }
}

// ── Import Sources menu ──────────────────────────────────────────────────
function toggleImportMenu() {
  const m = document.getElementById('import-sources-menu');
  m.style.display = m.style.display === 'none' ? 'block' : 'none';
}
document.addEventListener('click', e => {
  const menu = document.getElementById('import-sources-menu');
  const zone = document.getElementById('import-drop-zone');
  if (menu && zone && !menu.contains(e.target) && !zone.contains(e.target)) {
    menu.style.display = 'none';
  }
});

async function importEagleLbr(input) {
  const file = input.files?.[0];
  if (!file) return;
  input.value = '';
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/import-eagle', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Import failed');
    await loadLibrary();
    alert(`Eagle import done: ${data.installed.length} component(s) imported, ${data.skipped.length} skipped (already exist).`);
    if (data.installed.length) selectPart(data.installed[0]);
  } catch (err) {
    alert('Eagle import error: ' + err.message);
  }
}

document.getElementById('lib-search').addEventListener('input', e => renderLibrary(e.target.value));

async function selectPart(slug) {
  selectedSlug = slug;
  _leLoadedSlug = null; // force Layout Example reload for the new component
  renderLibrary(document.getElementById('lib-search').value);
  await loadProfile(slug);
}

async function loadProfile(slug) {
  if (!slug || slug === 'undefined' || slug === 'null') return;
  // New component selected — reset tab-loaded tracking so each tab re-fetches fresh data
  if (slug !== selectedSlug || Object.keys(_tabLoadedForSlug).length === 0) _tabLoadedForSlug = {};
  // Load from the active version snapshot so the user always sees their last
  // explicitly saved state (not a potentially-drifted profile.json from Generate).
  let profile = null;
  let activeVersion = null;
  try {
    activeVersion = await fetch(`/api/library/${slug}/active_version`).then(r => r.json()).catch(() => null);
    if (activeVersion && activeVersion.id) {
      const snap = await fetch(`/api/library/${slug}/history/${activeVersion.id}`).then(r => r.json()).catch(() => null);
      if (snap && snap.profile) profile = snap.profile;
    }
  } catch(_) {}
  if (!profile) {
    const res = await fetch(`/api/library/${slug}`, { cache: 'no-store' });
    profile = await res.json();
  }
  renderProfile(profile, activeVersion);
}

// ── Param field helper (compact, used inside right panel) ───────────────────
function _symParamField(id, label, value, placeholder, fontFamily, readonly) {
  const ro = readonly ? 'disabled' : '';
  const roStyle = readonly ? 'opacity:0.5;cursor:not-allowed;' : '';
  const ff = fontFamily ? `font-family:${fontFamily};` : '';
  const onInput = readonly ? '' : `oninput="symParamAutoSave()"`;
  return `<div style="display:flex;flex-direction:column;gap:2px;">
    <label style="font-size:9px;color:var(--text-muted);font-weight:600;text-transform:uppercase;letter-spacing:.05em;">${label}</label>
    <input id="${id}" type="text" value="${esc(value)}" placeholder="${placeholder}" ${ro} ${onInput}
      style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:3px 6px;font-size:11px;${ff}box-sizing:border-box;${roStyle}">
  </div>`;
}

// Track the active version ID so params/pin saves can sync the snapshot
let _symActiveVersionId = null;

// ── Profile Render ─────────────────────────────────────────────────────────
function renderProfile(p, activeVersion) {
  _symActiveVersionId = activeVersion?.id || null;
  showView('library');
  // Rebuilding innerHTML destroys the le-frame iframe — mark it as unloaded so
  // leSaveLayout won't try to getBoard from a blank frame.
  if (typeof _leLoadedSlug !== 'undefined') _leLoadedSlug = null;
  const main = document.getElementById('view-library');

  if (p.status === 'pending_parse') {
    const prompt = `Read DATASHEET_PARSING_GUIDE.md first — it has the full schema, rules, and worked example (PMA3-83LNW+).

Then parse this component:
- Raw text: library/${p.slug}/raw_text.txt
- Write result to: library/${p.slug}/profile.json
- Preserve any existing human_corrections in the file
- Set status "parsed", parsed_at current ISO timestamp
- Keep existing filename/uploaded_at/page_count/raw_text_length values`;

    main.innerHTML = `
      <div class="pending-box">
        <h3>⏳ ${p.part_number || p.slug} — Awaiting Parse</h3>
        <p>
          PDF uploaded successfully. Text extracted: <strong>${(p.raw_text_length || 0).toLocaleString()} characters</strong>
          · Confidence: <strong>${p.confidence || '?'}</strong>
          ${p.extraction_note ? `<br><span style="color:var(--yellow);">⚠ ${p.extraction_note}</span>` : ''}
        </p>
        <p style="margin-top:10px;">Copy this prompt and paste it into your Claude Code session:</p>
        <div class="copy-prompt" onclick="copyText(this)" title="Click to copy">${prompt}</div>
        <div class="copy-hint">Click the box to copy · Claude Code will read the extracted text and write the full component profile · This page updates automatically when done.</div>
      </div>`;
    return;
  }

  const absMax = p.absolute_max || {};
  const pins = p.pins || [];
  const passives = p.required_passives || [];
  const mistakes = p.common_mistakes || [];
  const corrections = p.human_corrections || [];
  const _avLabel = activeVersion ? (activeVersion.label || `v${activeVersion.vNum}`) : null;
  const _avBadgeHtml = _avLabel
    ? `<span id="active-version-badge" style="display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;background:rgba(139,92,246,0.18);border:1.5px solid rgba(139,92,246,0.55);border-radius:5px;padding:2px 10px;color:#c4b5fd;font-family:monospace;letter-spacing:.04em;white-space:nowrap;">● ${_avLabel}</span>`
    : `<span id="active-version-badge" style="display:none;"></span>`;

  main.innerHTML = `
    <div class="profile-card" id="profile-card-root">
      <div class="profile-header">
        <div style="min-width:0;">
          <div class="profile-part" style="display:flex;align-items:center;gap:8px;">${p.part_number || p.slug}${_avBadgeHtml}</div>
        </div>
        <div class="actions-bar" style="position:relative;">
          <button class="btn btn-rebuild" id="rebuild-btn" onclick="queueRebuild()">✨ Generate</button>
          <button class="btn btn-secondary" id="history-btn" onclick="openHistoryPanel()" title="Browse and restore previous versions">📋 Versions</button>
          <button class="btn btn-secondary" onclick="exportProfile()">Export JSON</button>
          <button class="btn btn-danger" onclick="deletePart()">Delete</button>
          <!-- Generate popover -->
          <div id="rebuild-popover" style="display:none;position:absolute;top:calc(100% + 6px);left:0;z-index:9999;background:var(--bg-2,#1e1e2e);border:1px solid var(--border,#333);border-radius:8px;padding:12px;width:340px;box-shadow:0 8px 24px rgba(0,0,0,0.5);">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
              <div style="font-size:11px;font-weight:700;color:var(--text-muted);letter-spacing:.06em;text-transform:uppercase;">What to generate</div>
              <button onclick="closeRebuildPopover()" style="background:none;border:none;color:var(--text-muted);font-size:14px;cursor:pointer;padding:0 2px;line-height:1;">✕</button>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:10px;">
              <button class="gen-toggle" id="gen-t-symbol"    onclick="genToggle('symbol')">🔷 Symbol</button>
              <button class="gen-toggle" id="gen-t-schematic" onclick="genToggle('schematic')">📐 Schematic Example</button>
              <button class="gen-toggle" id="gen-t-footprint" onclick="genToggle('footprint')">📦 Footprint</button>
              <button class="gen-toggle" id="gen-t-layout"    onclick="genToggle('layout')">🗺 Layout Example</button>
            </div>
            <div style="font-size:11px;font-weight:700;color:var(--text-muted);margin-bottom:5px;letter-spacing:.06em;text-transform:uppercase;">Optional instructions</div>
            <textarea id="rebuild-notes" rows="3" placeholder="e.g. Add 2 missing bypass caps on the BIAS line. Use 100nF 0402." style="width:100%;box-sizing:border-box;background:var(--bg-1,#141420);border:1px solid var(--border,#333);border-radius:5px;color:var(--text,#e2e2e2);font-size:12px;padding:7px 9px;resize:vertical;outline:none;font-family:inherit;"></textarea>
            <div style="display:flex;gap:6px;margin-top:8px;justify-content:flex-end;">
              <button class="btn btn-rebuild" id="rebuild-queue-btn" style="font-size:11px;padding:4px 12px;" onclick="confirmRebuild()">Queue ↗</button>
            </div>
          </div>
          <!-- Versions popover -->
          <div id="history-popover" style="display:none;position:fixed;z-index:9999;background:var(--bg-2,#1e1e2e);border:1px solid var(--border,#333);border-radius:8px;padding:14px;width:460px;box-shadow:0 8px 32px rgba(0,0,0,0.6);">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
              <div style="font-size:11px;font-weight:700;color:var(--text-muted);letter-spacing:.06em;text-transform:uppercase;">Component Versions</div>
              <div style="display:flex;align-items:center;gap:8px;">
                <span id="history-slug-badge" style="font-size:10px;background:var(--surface2);border:1px solid var(--border);border-radius:3px;padding:1px 6px;color:var(--text-muted);font-family:monospace;"></span>
                <button onclick="document.getElementById('history-popover').style.display='none'" style="background:none;border:none;color:var(--text-muted);font-size:14px;cursor:pointer;padding:0 2px;line-height:1;">✕</button>
              </div>
            </div>
            <div style="display:flex;gap:6px;margin-bottom:10px;">
              <button id="history-save-btn" onclick="historySaveActive()" style="flex:1;background:rgba(139,92,246,0.12);border:1px solid rgba(139,92,246,0.35);border-radius:6px;color:#a78bfa;font-size:12px;font-weight:700;padding:7px 10px;cursor:pointer;">💾 Save</button>
              <button onclick="historyNewVersion()" style="flex:1;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:6px;color:#4ade80;font-size:12px;font-weight:700;padding:7px 10px;cursor:pointer;">+ New Version</button>
            </div>
            <div id="history-name-row" style="display:none;margin-bottom:10px;gap:6px;flex-direction:row;">
              <input id="history-save-label" type="text" placeholder="Version name…" style="flex:1;background:var(--bg-1,#141420);border:1px solid var(--border,#333);border-radius:5px;color:var(--text);font-size:11px;padding:5px 8px;outline:none;font-family:inherit;" onkeydown="if(event.key==='Enter')historySave();if(event.key==='Escape')historyCloseNameRow()">
              <button onclick="historySave()" style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.35);border-radius:5px;color:#4ade80;font-size:11px;font-weight:700;padding:5px 12px;cursor:pointer;white-space:nowrap;">Create</button>
              <button onclick="historyCloseNameRow()" style="background:none;border:1px solid var(--border);border-radius:5px;color:var(--text-muted);font-size:11px;padding:5px 8px;cursor:pointer;">✕</button>
            </div>
            <div id="history-list" style="max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:3px;">
              <div style="color:var(--text-muted);font-size:11px;padding:8px 0;">Loading…</div>
            </div>
          </div>
        </div>
      </div>

      <div class="profile-tabs">
        <button class="profile-tab active" id="tab-btn-datasheet" onclick="switchProfileTab('datasheet', this)">Symbol</button>
        <button class="profile-tab" id="tab-btn-schematic" onclick="switchProfileTab('schematic', this)">Schematic Example</button>
        <button class="profile-tab" id="tab-btn-footprint" onclick="switchProfileTab('footprint', this)">Footprint</button>
        <button class="profile-tab" id="tab-btn-layout-example" onclick="switchProfileTab('layout-example', this)">Layout Example</button>
      </div>

      <div id="tab-datasheet" class="tab-panel">
      <div class="profile-body">
        ${p.extraction_note ? `<div class="warning-banner">⚠ ${p.extraction_note} — Verify pin assignments before using in a circuit.</div>` : ''}

        <div>
          <!-- Symbol Editor toolbar -->
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;flex-wrap:wrap;gap:6px;">
            <div class="section-title" style="margin-bottom:0;">🔷 Symbol Editor</div>
            <div style="display:flex;gap:5px;flex-wrap:wrap;">
              ${p.builtin ? '' : `<button onclick="symEditorAddPin('left')" title="Add pin on left side" style="background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text-dim);padding:3px 9px;font-size:11px;cursor:pointer;">+ L Pin</button>
              <button onclick="symEditorAddPin('right')" title="Add pin on right side" style="background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text-dim);padding:3px 9px;font-size:11px;cursor:pointer;">+ R Pin</button>
              <button onclick="symEditorSave()" style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.4);border-radius:4px;color:#22c55e;padding:3px 10px;font-size:11px;font-weight:700;cursor:pointer;">💾 Save</button>`}
            </div>
          </div>
          <!-- Editor area: canvas + right panel (params + pin list / pin form) -->
          <div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;overflow:hidden;display:flex;min-height:300px;">
            <!-- Canvas -->
            <div style="flex:1;min-width:0;">
              <svg id="symbol-canvas" style="display:block;width:100%;min-height:200px;"></svg>
            </div>
            <!-- Right panel -->
            <div style="width:200px;flex-shrink:0;border-left:1px solid var(--border);display:flex;flex-direction:column;background:var(--surface);overflow:hidden;">

              <!-- PARAMETERS -->
              <div style="border-bottom:1px solid var(--border);flex-shrink:0;">
                <div style="padding:5px 10px;display:flex;align-items:center;justify-content:space-between;">
                  <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text-muted);">Parameters</span>
                  ${p.builtin ? `<span title="Built-in — read only" style="font-size:9px;color:#f59e0b;">🔒</span>` : `<span id="sym-param-status" style="font-size:9px;color:var(--text-muted);transition:opacity 0.4s;"></span>`}
                </div>
                <div style="padding:0 10px 8px;display:flex;flex-direction:column;gap:5px;">
                  ${_symParamField('sym-param-partnum', 'Part No.',     p.part_number||'',          'e.g. LM358', 'monospace', p.builtin)}
                  ${_symParamField('sym-param-mfr',     'Manufacturer', p.manufacturer||'',         'e.g. TI',    '',          p.builtin)}
                  ${_symParamField('sym-param-value',   'Value',        p.value||p.part_number||'', 'e.g. 10k',   'monospace', p.builtin)}
                  ${_symParamField('sym-param-des',     'Designator',   p.designator||'',           'e.g. U',     'monospace', p.builtin)}
                  ${_symParamField('sym-param-desc',    'Description',  p.description||'',          'Short desc…','',          p.builtin)}
                </div>
              </div>

              <!-- PIN LIST VIEW -->
              <div id="sym-list-view" style="display:flex;flex-direction:column;flex:1;overflow:hidden;">
                <div style="padding:7px 12px;border-bottom:1px solid var(--border);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text-muted);flex-shrink:0;">Pins${p.builtin ? ' — read only' : ' — click to edit'}</div>
                <div id="sym-pin-list" style="overflow-y:auto;flex:1;"></div>
                <div id="sym-nonic-hint" style="display:none;padding:8px 10px;font-size:10px;color:var(--text-muted);border-top:1px solid var(--border);line-height:1.5;">Click a pin in the canvas or list to view it</div>
              </div>

              <!-- PIN FORM VIEW (shown on pin click) -->
              <div id="sym-form-view" style="display:none;flex-direction:column;flex:1;overflow:hidden;">
                <div style="padding:7px 12px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;">
                  <button onclick="symEditorBackToList()" style="background:none;border:none;color:var(--accent);font-size:11px;cursor:pointer;padding:0;font-weight:600;">← Pins</button>
                  ${p.builtin ? '' : '<button id="sym-remove-btn" onclick="symEditorRemovePin()" style="background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.25);border-radius:3px;color:#ef4444;padding:2px 7px;font-size:10px;cursor:pointer;">✕ Remove</button>'}
                </div>
                <div style="overflow-y:auto;flex:1;padding:10px 12px;display:flex;flex-direction:column;gap:8px;">
                  <div style="display:flex;gap:7px;">
                    <div style="display:flex;flex-direction:column;gap:3px;">
                      <label style="font-size:10px;color:var(--text-muted);">Pin #</label>
                      <input id="spe-number" type="number" ${p.builtin?'readonly':''} oninput="symEditorUpdatePin('number',this.value)"
                        style="width:50px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:4px 6px;font-size:12px;font-family:monospace;${p.builtin?'opacity:0.6;':''}">
                    </div>
                    <div style="display:flex;flex-direction:column;gap:3px;flex:1;">
                      <label style="font-size:10px;color:var(--text-muted);">Name</label>
                      <input id="spe-name" type="text" ${p.builtin?'readonly':''} oninput="symEditorUpdatePin('name',this.value)"
                        style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:4px 6px;font-size:12px;font-family:monospace;${p.builtin?'opacity:0.6;':''}">
                    </div>
                  </div>
                  <div style="display:flex;flex-direction:column;gap:3px;">
                    <label style="font-size:10px;color:var(--text-muted);">Type</label>
                    <select id="spe-type" ${p.builtin?'disabled':''} onchange="symEditorUpdatePin('type',this.value)"
                      style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:4px 6px;font-size:12px;width:100%;${p.builtin?'opacity:0.6;':''}">
                      <option value="input">input</option><option value="output">output</option>
                      <option value="power">power</option><option value="gnd">gnd</option>
                      <option value="passive">passive</option><option value="bidirectional">bidirectional</option>
                    </select>
                  </div>
                  <div style="display:flex;gap:7px;">
                    <div style="display:flex;flex-direction:column;gap:3px;flex:1;">
                      <label style="font-size:10px;color:var(--text-muted);">Active</label>
                      <select id="spe-active" ${p.builtin?'disabled':''} onchange="symEditorUpdatePin('active',this.value||null)"
                        style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:4px 6px;font-size:11px;width:100%;${p.builtin?'opacity:0.6;':''}">
                        <option value="">—</option><option value="high">high</option><option value="low">low</option>
                      </select>
                    </div>
                    <div style="display:flex;flex-direction:column;gap:3px;width:52px;">
                      <label style="font-size:10px;color:var(--text-muted);">DS Page</label>
                      <input id="spe-page" type="number" ${p.builtin?'readonly':''} oninput="symEditorUpdatePin('datasheet_page',this.value)"
                        style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:4px 6px;font-size:12px;${p.builtin?'opacity:0.6;':''}">
                    </div>
                  </div>
                  <div style="display:flex;flex-direction:column;gap:3px;">
                    <label style="font-size:10px;color:var(--text-muted);">Description</label>
                    <textarea id="spe-desc" rows="3" ${p.builtin?'readonly':''} oninput="symEditorUpdatePin('description',this.value)"
                      style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:4px 6px;font-size:11px;resize:vertical;width:100%;line-height:1.4;${p.builtin?'opacity:0.6;':''}"></textarea>
                  </div>
                  <div style="display:flex;flex-direction:column;gap:3px;">
                    <label style="font-size:10px;color:var(--text-muted);">Requirements</label>
                    <textarea id="spe-req" rows="2" ${p.builtin?'readonly':''} oninput="symEditorUpdatePin('requirements',this.value)"
                      style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:4px 6px;font-size:11px;resize:vertical;width:100%;line-height:1.4;${p.builtin?'opacity:0.6;':''}"></textarea>
                  </div>
                </div>
              </div>

            </div>
          </div>
        </div>

        ${Object.keys(absMax).length ? `
        <div>
          <div class="section-title">⛔ Absolute Maximum Ratings</div>
          <div class="abs-max-grid">
            ${Object.entries(absMax).map(([k,v]) => `
              <div class="abs-max-item">
                <div class="abs-max-label">${k.replace(/_/g,' ')}</div>
                <div class="abs-max-value">${v}</div>
              </div>`).join('')}
          </div>
        </div>` : ''}

        ${pins.length ? `
        <div>
          <div class="section-title">📌 Pins (${pins.length})</div>
          <table class="pin-table">
            <thead><tr><th>#</th><th>Name</th><th>Type</th><th>Description & Requirements</th><th style="width:36px;text-align:center;color:var(--text-muted);font-size:10px;">p.</th></tr></thead>
            <tbody>
              ${pins.map(pin => `
                <tr onclick="showPdfRef(${pin.datasheet_page||1}, ${JSON.stringify(pin.name||'')}, ${JSON.stringify((pin.description||'')+(pin.requirements?'↳ '+pin.requirements:''))}, this)" style="cursor:pointer;" title="Click to jump to datasheet page ${pin.datasheet_page||'?'}">
                  <td class="pin-num">${pin.number??'—'}</td>
                  <td class="pin-name">${pin.name||'—'}
                    ${pin.active?`<span style="font-size:10px;color:var(--text-muted)"> /${pin.active}</span>`:''}
                    ${pin.internal_pull?`<span style="font-size:10px;color:var(--text-muted)"> pull-${pin.internal_pull}</span>`:''}
                  </td>
                  <td><span class="pin-type type-${(pin.type||'passive').toLowerCase()}">${pin.type||'passive'}</span></td>
                  <td>
                    ${pin.description||''}
                    ${pin.requirements?`<div class="pin-req">↳ ${pin.requirements}</div>`:''}
                    ${pin.ambiguous?`<div class="pin-ambiguous">⚠ Ambiguous — verify manually</div>`:''}
                  </td>
                  <td style="text-align:center;font-size:10px;font-family:monospace;color:${pin.datasheet_page?'var(--accent)':'var(--border)'};">${pin.datasheet_page?pin.datasheet_page:'—'}</td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>` : ''}

        ${passives.length ? `
        <div>
          <div class="section-title">🔧 Required Passives</div>
          <div class="passives-list">
            ${passives.map((pas,pi) => `
              <div class="passive-item" onclick="showPdfRef(${pas.datasheet_page||4}, ${JSON.stringify(pas.value+' '+pas.type)}, ${JSON.stringify(pas.placement+(pas.reason?'. '+pas.reason:''))}, this)" style="cursor:pointer;" title="Click to jump to datasheet page ${pas.datasheet_page||'?'}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
                  <div class="passive-value">${pas.type} ${pas.value}</div>
                  ${pas.datasheet_page?`<span style="font-size:10px;font-family:monospace;color:var(--accent);flex-shrink:0;">p.${pas.datasheet_page}</span>`:''}
                </div>
                <div class="passive-placement">📍 ${pas.placement}</div>
                ${pas.reason?`<div class="passive-reason">${pas.reason}</div>`:''}
              </div>`).join('')}
          </div>
        </div>` : ''}

        ${mistakes.length ? `
        <div>
          <div class="section-title">⚡ Common Mistakes & Warnings</div>
          <div class="mistakes-list">
            ${mistakes.map(m => `<div class="mistake-item">⚠ ${m}</div>`).join('')}
          </div>
        </div>` : ''}

        ${corrections.length ? `
        <div>
          <div class="section-title">✏ Human Corrections</div>
          <div class="corrections-list">
            ${corrections.map(c => `
              <div class="correction-item">
                <div style="font-size:10px;color:var(--text-muted);">${c.date}</div>
                <div><strong>Was:</strong> ${c.original}</div>
                <div><strong>Fixed:</strong> ${c.correction}</div>
                ${c.reason?`<div style="color:var(--text-muted);margin-top:3px;">${c.reason}</div>`:''}
              </div>`).join('')}
          </div>
        </div>` : ''}

        <div>
          <div class="section-title">Add Human Correction</div>
          <textarea id="corr-original" rows="2" placeholder="What was wrong (e.g. pin 4 connected to wrong net)"></textarea>
          <textarea id="corr-fix" rows="2" placeholder="Correction (e.g. pin 4 is BOOT — must be pulled high via 100k)" style="margin-top:6px;"></textarea>
          <textarea id="corr-reason" rows="1" placeholder="Reason (optional)" style="margin-top:6px;"></textarea>
          <button class="btn btn-secondary" style="margin-top:8px;background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.4);color:#22c55e;" onclick="saveCorrection()">Save Correction</button>
        </div>

        ${p.notes?`
        <div>
          <div class="section-title">📝 Notes</div>
          <div style="font-size:13px;color:var(--text-dim);line-height:1.6;">${p.notes}</div>
        </div>`:''}

        <div style="font-size:11px;color:var(--text-muted);border-top:1px solid var(--border);padding-top:12px;">
          Parsed ${p.parsed_at ? new Date(p.parsed_at).toLocaleString() : 'unknown'} · ${p.filename || ''}
        </div>
      </div>
      </div>

      <div id="tab-schematic" class="tab-panel" style="display:none;">
        <div class="profile-body">
          <div style="display:flex;align-items:stretch;">
            <!-- Nets panel — left side -->
            <div style="width:148px;flex-shrink:0;background:var(--surface);border:1px solid var(--border);border-right:none;border-radius:8px 0 0 8px;display:flex;flex-direction:column;overflow:hidden;position:relative;">
              <!-- Search bar in header position -->
              <div style="padding:5px 7px;border-bottom:1px solid var(--border);flex-shrink:0;position:relative;z-index:11;background:var(--surface);">
                <input id="acc-palette-search" type="text" placeholder="🔍 Search components…"
                  oninput="accHandleSearch(this.value)" onfocus="accHandleSearch(this.value)"
                  onblur="setTimeout(()=>accHandleSearchBlur(),150)"
                  class="library-search" style="margin-bottom:0;font-size:10px;padding:5px 7px;width:100%;box-sizing:border-box;"/>
              </div>
              <!-- Nets list with inline label -->
              <div id="acc-nets-list" style="overflow-y:auto;flex:1;font-size:10px;">
                <div style="padding:5px 10px 3px;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text-muted);display:flex;align-items:center;justify-content:space-between;">
                  <span>Nets</span><span id="acc-nets-count" style="font-size:10px;font-weight:600;"></span>
                </div>
                <div style="padding:2px 10px 6px;font-size:10px;color:var(--text-muted);">No nets yet.</div>
              </div>
              <!-- Search results — overlays nets when input focused/active -->
              <div id="acc-search-panel" style="display:none;position:absolute;top:0;left:0;right:0;bottom:0;background:var(--surface);flex-direction:column;z-index:10;padding-top:36px;">
                <div id="acc-palette" style="overflow-y:auto;flex:1;font-size:10px;"></div>
              </div>
            </div>
            <div style="flex:1;min-width:0;">
              <div style="background:var(--surface2);border:1px solid var(--border);border-left:none;border-right:none;border-radius:0;overflow:hidden;">
                <!-- Sub-nav tabs -->
                <nav style="display:flex;align-items:center;gap:4px;padding:0 8px;border-bottom:1px solid var(--border);background:var(--surface);height:30px;">
                  <button class="nav-tab active" id="acc-sub-schematic" onclick="switchAccSection('schematic')" style="font-size:12px;padding:4px 12px;">📋 Schematic</button>
                  <button class="nav-tab" id="acc-sub-auto" onclick="switchAccSection('auto')" style="font-size:12px;padding:4px 12px;">&#9881; Auto</button>
                  <button class="nav-tab" id="acc-sub-export" onclick="switchAccSection('export')" style="font-size:12px;padding:4px 12px;">📦 Export</button>
                  <div style="flex:1;"></div>
                  <button class="btn-save" onclick="accSaveExample(this)" title="Save" style="font-size:11px;padding:3px 8px;">&#128190; Save</button>
                </nav>
                <!-- Schematic toolbar -->
                <div class="editor-toolbar" id="acc-toolbar-schematic">
                  <button class="tool-btn active" data-acc-tool="select" onclick="appCircuitEditor?.setTool('select')" title="Select — S">&#9654; Select</button>
                  <button class="tool-btn" data-acc-tool="boxselect" onclick="appCircuitEditor?.setTool('boxselect')" title="Box Select — B">&#9633; Box</button>
                  <button class="tool-btn" data-acc-tool="wire" onclick="appCircuitEditor?.setTool('wire')" title="Wire — W">&#9135; Wire</button>
                  <button class="tool-btn" data-acc-tool="delete" onclick="appCircuitEditor?.setTool('delete')" title="Delete — D">&#10005; Delete</button>
                  <div class="toolbar-sep"></div>
                  <button class="tool-btn" onclick="appCircuitEditor?._rotateSelected()" title="Rotate — R">&#8635;</button>
                  <button class="tool-btn" onclick="appCircuitEditor?._fit()" title="Fit — F">&#9635;</button>
                  <button class="tool-btn" id="acc-btn-undo" onclick="appCircuitEditor?._undo()" title="Undo — Ctrl+Z" disabled style="opacity:0.35;cursor:default;">&#8630;</button>
                  <button class="tool-btn" id="acc-btn-redo" onclick="appCircuitEditor?._redo()" title="Redo — Ctrl+Y" disabled style="opacity:0.35;cursor:default;">&#8631;</button>
                  <div class="toolbar-sep"></div>
                  <button class="tool-btn" data-acc-tool="label" onclick="appCircuitEditor?.setTool('label')" title="Net Label — L">&#9657; Label</button>
                  <button class="tool-btn" data-acc-tool="nc" onclick="appCircuitEditor?.setTool('nc')" title="No Connect — X">&#10005; NC</button>
                </div>
                <!-- Auto toolbar -->
                <div class="editor-toolbar" id="acc-toolbar-auto" style="display:none;">
                  <button class="tool-btn" onclick="appCircuitEditor?._fit()" title="Fit view">&#9635; Fit View</button>
                  <div class="toolbar-sep"></div>
                  <button class="tool-btn" onclick="appCircuitEditor?.autoLabelNets()" title="Auto-name all unlabeled nets">&#9657; Auto Label Nets</button>
                  <button class="tool-btn" onclick="appCircuitEditor?.autoDesignators()" title="Auto-assign designators">&#9998; Auto Designators</button>
                </div>
                <!-- Export toolbar -->
                <div class="editor-toolbar" id="acc-toolbar-export" style="display:none;">
                  <button class="tool-btn" onclick="openExampleInEditor()" title="Open in full Schematic Editor">&#9998; Open in Editor</button>
                </div>
                <svg id="app-circuit-canvas" style="display:block;width:100%;height:420px;"></svg>
              </div>
            </div>
            <!-- Component info panel -->
            <div id="acc-info-panel" style="width:195px;flex-shrink:0;background:var(--surface);border:1px solid var(--border);border-left:none;border-radius:0 8px 8px 0;display:flex;flex-direction:column;overflow:hidden;">
              <div style="padding:7px 10px;border-bottom:1px solid var(--border);font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text-muted);">Component Info</div>
              <div id="acc-info-empty" style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:10px;text-align:center;padding:12px;line-height:1.5;">Click a component<br>to see details</div>
              <div id="acc-info-content" style="display:none;flex-direction:column;overflow-y:auto;flex:1;">
                <div style="padding:6px 6px 4px;">
                  <svg id="acc-info-canvas" style="width:100%;height:110px;display:block;background:var(--bg);border-radius:4px;border:1px solid var(--border);"></svg>
                </div>
                <div style="padding:0 8px 5px;">
                  <div id="acc-info-name" style="font-family:monospace;font-weight:700;font-size:11px;color:var(--accent);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"></div>
                  <div id="acc-info-desc" style="font-size:10px;color:var(--text-dim);margin-top:2px;line-height:1.4;"></div>
                </div>
                <div style="padding:0 8px;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:3px;">Pins</div>
                <div id="acc-info-pins" style="padding:0 8px 8px;font-size:10px;"></div>
              </div>
            </div>
          </div>

          ${(() => {
            const ecComps = (p.example_circuit?.components || []).filter(c => c.slug && c.slug !== 'GND' && c.slug !== 'VCC');
            if (!ecComps.length && !(p.required_passives||[]).length) return '';
            const rows = ecComps.length
              ? ecComps.map((c, i) => {
                  const typeLabel = c.symType === 'ic' ? 'IC' : (c.symType || 'passive');
                  const typeCls = c.symType === 'ic' ? 'type-output' : 'type-passive';
                  const dispVal = profileCache[c.slug]?.value || profileCache[c.slug]?.part_number || c.value || c.slug || '';
                  return `<tr onclick="accBomPlace(this.dataset.slug,this.dataset.st,this.dataset.val)" style="cursor:pointer;" title="Place ${c.designator||''} in schematic"
                      data-slug="${esc(c.slug)}" data-st="${esc(c.symType||'ic')}" data-val="" data-idx="${i}">
                    <td class="pin-num">${c.designator||''}</td>
                    <td><span class="pin-type ${typeCls}">${typeLabel}</span></td>
                    <td class="pin-name">${dispVal}</td>
                    <td><span class="acc-bom-status required">Required</span></td>
                    <td class="acc-bom-icon" style="width:20px;text-align:center;">+</td>
                  </tr>`;
                }).join('')
              : `<tr onclick="accBomPlace(this.dataset.slug,this.dataset.st,this.dataset.val)" style="cursor:pointer;"
                    data-slug="${esc(p.slug||p.part_number||'')}" data-st="ic" data-val="" data-idx="0">
                  <td class="pin-num">U1</td><td><span class="pin-type type-output">IC</span></td>
                  <td class="pin-name">${p.part_number||''}</td>
                  <td><span class="acc-bom-status required">Required</span></td>
                  <td class="acc-bom-icon" style="width:20px;text-align:center;">+</td>
                </tr>` + (p.required_passives||[]).map((pas,i) => {
                  const ref = pas.type==='capacitor'?'C':pas.type==='inductor'?'L':'R';
                  const slug = pas.type==='capacitor'?'CAPACITOR':pas.type==='inductor'?'INDUCTOR':'RESISTOR';
                  return `<tr onclick="accBomPlace(this.dataset.slug,this.dataset.st,this.dataset.val)" style="cursor:pointer;"
                      data-slug="${esc(slug)}" data-st="${esc(pas.type)}" data-val="${esc(pas.value||'')}" data-idx="${i+1}">
                    <td class="pin-num">${ref}${i+1}</td><td><span class="pin-type type-passive">${pas.type}</span></td>
                    <td class="pin-name">${pas.value}</td>
                    <td><span class="acc-bom-status required">Required</span></td>
                    <td class="acc-bom-icon" style="width:20px;text-align:center;">+</td>
                  </tr>`;
                }).join('');
            return `<div>
              <div class="section-title" style="display:flex;align-items:center;gap:8px;">🧩 Circuit Components <span style="font-size:10px;color:var(--text-muted);font-weight:400;text-transform:none;letter-spacing:0;">— click a row to place</span></div>
              <table class="pin-table"><thead><tr><th>Ref</th><th>Type</th><th>Value</th><th>Status</th><th></th></tr></thead><tbody id="acc-bom-tbody">${rows}</tbody></table>
            </div>`;
          })()}
        </div>
      </div>

      <div id="tab-footprint" class="tab-panel" style="display:none;">
        <div class="profile-body">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
            <div class="section-title" style="margin-bottom:0;">🔲 Footprint</div>
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
              <div id="fp-select-wrap" style="position:relative;display:inline-block;">
                <input id="fp-search" type="text" placeholder="— select footprint —" autocomplete="off"
                  style="background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:3px 8px;font-size:11px;width:220px;box-sizing:border-box;"
                  oninput="fpSearchInput(this.value)"
                  onfocus="fpSearchOpen()"
                  onblur="setTimeout(fpSearchClose,200)"
                />
                <div id="fp-dropdown" style="display:none;position:absolute;top:100%;left:0;z-index:999;background:var(--surface2);border:1px solid var(--border);border-radius:4px;max-height:260px;overflow-y:auto;min-width:max(100%,360px);box-shadow:0 4px 16px rgba(0,0,0,0.5);margin-top:2px;"></div>
              </div>
              <button onclick="fpSaveAssignment(this)" style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.4);border-radius:4px;color:#22c55e;padding:3px 10px;font-size:11px;font-weight:700;cursor:pointer;">💾 Assign</button>
              <button onclick="fpSavePads(this)" style="background:rgba(34,197,94,0.15);border:1px solid rgba(34,197,94,0.4);border-radius:4px;color:#22c55e;padding:3px 10px;font-size:11px;font-weight:600;cursor:pointer;">💾 Save Pads</button>
              <button onclick="fpGenerate(this)" style="background:var(--accent-dim);border:1px solid var(--accent);border-radius:4px;color:var(--accent);padding:3px 10px;font-size:11px;font-weight:600;cursor:pointer;">⚡ Generate</button>
            </div>
          </div>
          <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;">
            <!-- Footprint canvas -->
            <div style="flex:1;min-width:240px;">
              <div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;overflow:hidden;">
                <svg id="fp-svg" style="display:block;width:100%;height:260px;cursor:crosshair;"></svg>
              </div>
              <div style="margin-top:6px;font-size:10px;color:var(--text-muted);text-align:center;">All dimensions in mm · Click pad to select</div>
            </div>
            <!-- Pad table -->
            <div style="flex:1;min-width:200px;">
              <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:6px;">Pads</div>
              <div id="fp-pad-list" style="font-size:11px;"></div>
              <button onclick="fpAddPad()" style="margin-top:8px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text-dim);padding:3px 10px;font-size:11px;cursor:pointer;">+ Add Pad</button>
            </div>
            <!-- Layer panel (mirrors PCB layer management) -->
            <div style="min-width:160px;flex-shrink:0;">
              <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:6px;">Layers</div>
              <div id="fp-layer-list" style="display:flex;flex-direction:column;gap:1px;"></div>
              <!-- Paste / Mask parameters -->
              <div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px;">
                <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:6px;">Paste / Mask</div>
                <div style="display:flex;flex-direction:column;gap:5px;">
                  <label style="display:flex;justify-content:space-between;align-items:center;gap:6px;font-size:11px;color:var(--text-muted);">
                    <span title="Solder mask opening = pad + this value on each side">Mask exp.</span>
                    <span style="display:flex;align-items:center;gap:3px;">
                      <input id="fp-mask-exp" type="number" step="0.01" min="0" max="1" value="0.10"
                        onchange="if(fpEditor){fpEditor.maskExpansion=Math.max(0,parseFloat(this.value)||0.1);fpEditor._render();}"
                        style="width:52px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:1px 3px;font-size:10px;text-align:right;">
                      <span style="font-size:10px;">mm</span>
                    </span>
                  </label>
                  <label style="display:flex;justify-content:space-between;align-items:center;gap:6px;font-size:11px;color:var(--text-muted);">
                    <span title="Solder paste opening = pad − this value on each side (SMD only)">Paste gap</span>
                    <span style="display:flex;align-items:center;gap:3px;">
                      <input id="fp-paste-gap" type="number" step="0.01" min="0" max="1" value="0.05"
                        onchange="if(fpEditor){fpEditor.pasteGap=Math.max(0,parseFloat(this.value)||0.05);fpEditor._render();}"
                        style="width:52px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:1px 3px;font-size:10px;text-align:right;">
                      <span style="font-size:10px;">mm</span>
                    </span>
                  </label>
                </div>
              </div>
            </div>
          </div>
          <!-- Footprint metadata -->
          <div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;">
            <label style="font-size:11px;color:var(--text-muted);display:flex;flex-direction:column;gap:3px;">Name<input id="fp-name" type="text" style="background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);padding:3px 7px;font-size:11px;font-family:monospace;width:120px;"></label>
            <label style="font-size:11px;color:var(--text-muted);display:flex;flex-direction:column;gap:3px;">Description<input id="fp-desc" type="text" style="background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);padding:3px 7px;font-size:11px;width:220px;"></label>
          </div>
        </div>
      </div>

      <div id="tab-layout-example" class="tab-panel" style="display:none;">
        <div style="display:flex;flex-direction:column;height:calc(100vh - 160px);min-height:400px;">
          <div style="display:flex;align-items:center;gap:8px;padding:4px 10px;background:var(--surface);border-bottom:1px solid var(--border);flex-shrink:0;">
            <label style="font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;">Layers</label>
            <select id="le-layer-count" style="background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:11px;padding:2px 6px;cursor:pointer;"
              onchange="leSetLayerCount(parseInt(this.value))">
              <option value="2">2L</option><option value="4">4L</option><option value="6">6L</option><option value="8">8L</option>
            </select>
            <div id="le-notes" style="flex:1;font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></div>
            <button id="le-save-btn" onclick="leSaveLayout()" style="display:none;"></button>
          </div>
          <!-- body: PCB iframe on top, BOM strip below -->
          <div style="display:flex;flex-direction:column;flex:1;overflow:hidden;">
            <!-- PCB editor iframe -->
            <div style="flex:1;min-height:0;overflow:hidden;">
              <iframe id="le-frame" src="/pcb.html?embedded=1&_v=6" style="display:block;width:100%;height:100%;border:none;"></iframe>
            </div>
            <!-- BOM strip below canvas -->
            <div style="flex-shrink:0;background:var(--surface);border-top:1px solid var(--border);display:flex;flex-direction:column;max-height:140px;">
              <div style="padding:4px 10px 2px;display:flex;align-items:center;gap:8px;">
                <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--text-muted);">Components</div>
                <div id="le-bom-count" style="font-size:10px;color:var(--text-muted);flex:1;">Click to add to layout</div>
              </div>
              <div id="le-bom-list" style="overflow-x:auto;overflow-y:hidden;display:flex;flex-wrap:wrap;gap:4px;padding:4px 8px 6px;"></div>
            </div>
            <!-- Net mismatch table (shown below BOM when there are mismatches) -->
            <div id="le-net-warning" style="display:none;flex-shrink:0;background:var(--surface);border-top:2px solid rgba(239,68,68,0.6);max-height:160px;overflow-y:auto;">
              <div style="padding:5px 10px 3px;display:flex;align-items:center;gap:8px;position:sticky;top:0;background:var(--surface);z-index:1;flex-wrap:wrap;">
                <span style="color:#ef4444;font-size:12px;font-weight:700;">⚠ Net mismatch</span>
                <span id="le-net-mm-count" style="font-size:10px;color:#fca5a5;"></span>
                <button onclick="leAutoCorrectAllNets()" style="margin-left:auto;background:rgba(99,102,241,0.2);border:1px solid rgba(99,102,241,0.5);border-radius:4px;color:#a5b4fc;padding:3px 12px;font-size:11px;cursor:pointer;font-weight:700;white-space:nowrap;">Correct nets as per Schematic Example</button>
              </div>
              <table id="le-net-warning-list" style="width:100%;border-collapse:collapse;font-size:11px;font-family:monospace;"></table>
            </div>
          </div>
        </div>
      </div>

    </div>`;

  updatePdfPanel(p);
  requestAnimationFrame(() => renderSymbolWithEditor(p));
  // Restore previously active profile tab (e.g. after SSE-triggered re-render)
  if (currentProfileTab && currentProfileTab !== 'datasheet') {
    const tabBtn = document.getElementById('tab-btn-' + currentProfileTab);
    if (tabBtn) switchProfileTab(currentProfileTab, tabBtn);
  }
}

// ── Tab switching ──────────────────────────────────────────────────────────
let currentProfileTab = 'datasheet';
// Track which tabs have already been loaded for the current slug so that
// switching back to a tab doesn't wipe unsaved changes with server data.
let _tabLoadedForSlug = {};

function switchProfileTab(name, btn) {
  currentProfileTab = name;
  document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.profile-tab').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).style.display = 'block';
  btn.classList.add('active');
  const firstVisit = _tabLoadedForSlug[name] !== selectedSlug;
  if (name === 'schematic') {
    renderAccPalette(document.getElementById('acc-palette-search')?.value || '');
    if (firstVisit) {
      _tabLoadedForSlug['schematic'] = selectedSlug;
      requestAnimationFrame(() => {
        fetch(`/api/library/${selectedSlug}`, { cache: 'no-store' }).then(r => r.json()).then(p => renderAppCircuitWithEditor(p));
      });
    }
  }
  if (name === 'footprint') {
    if (firstVisit) {
      _tabLoadedForSlug['footprint'] = selectedSlug;
      requestAnimationFrame(() => initFootprintTab(selectedSlug));
    }
  }
  if (name === 'layout-example') {
    requestAnimationFrame(() => renderLayoutExample(selectedSlug));
  }
}
