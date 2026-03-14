// SVG-based symbol editor — clicking a pin selects it for editing.
class SymbolEditorSVG {
  constructor(svgEl) {
    this.svg = svgEl;
    this.profile = null;
    this.symType = 'ic';
    this.slug = null;
    this.selectedPin = null;
    this.zoom = 1; this.panX = 0; this.panY = 0;
    this._vg = null; this._panState = null;
    svgEl.style.cursor = 'default';
    svgEl.addEventListener('click', e => this._onClick(e));
    svgEl.addEventListener('wheel', e => this._wheel(e), { passive: false });
    svgEl.addEventListener('mousedown', e => this._mdPan(e));
    svgEl.addEventListener('mousemove', e => this._mmPan(e));
    svgEl.addEventListener('mouseup', () => { this._panState = null; });
    svgEl.addEventListener('mouseleave', () => { this._panState = null; });
  }

  load(slug, profile) {
    this.slug = slug;
    this.profile = profile;
    this.symType = detectSymbolType(profile);
    this.selectedPin = null;
    this._fitNext = true; // auto-fit on next render
    this._render();
  }

  _fit() {
    const lay = this._layout();
    const svgW = this.svg.clientWidth || 600;
    const svgH = this.svg.clientHeight || 400;
    const margin = 60;
    const cW = lay.BOX_W + 2 * lay.STUB + margin * 2;
    const cH = lay.BOX_H + margin * 2;
    this.zoom = Math.min(svgW / cW, svgH / cH, 2.5);
    this.panX = svgW / 2;
    this.panY = svgH / 2;
  }

  _wheel(e) {
    e.preventDefault();
    const r = this.svg.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const nz = Math.max(0.1, Math.min(20, this.zoom * f));
    this.panX = sx - (sx - this.panX) * (nz / this.zoom);
    this.panY = sy - (sy - this.panY) * (nz / this.zoom);
    this.zoom = nz;
    if (this._vg) this._vg.setAttribute('transform', `translate(${this.panX},${this.panY}) scale(${this.zoom})`);
  }

  _mdPan(e) {
    if (e.button === 1 || (e.button === 0 && (e.ctrlKey || e.metaKey))) {
      const r = this.svg.getBoundingClientRect();
      this._panState = { sx: e.clientX - r.left, sy: e.clientY - r.top, ox: this.panX, oy: this.panY };
      e.preventDefault();
    }
  }

  _mmPan(e) {
    if (!this._panState) return;
    const r = this.svg.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    this.panX = this._panState.ox + sx - this._panState.sx;
    this.panY = this._panState.oy + sy - this._panState.sy;
    if (this._vg) this._vg.setAttribute('transform', `translate(${this.panX},${this.panY}) scale(${this.zoom})`);
  }

  // ── SVG click → select pin by data-pin-idx attribute ──────────────────
  _onClick(e) {
    const pinEl = e.target.closest('[data-pin-idx]');
    if (pinEl) {
      const idx = parseInt(pinEl.dataset.pinIdx);
      const pin = this._sortedPins()[idx];
      if (pin) { this._selectPin(pin); return; }
    }
    // Click on empty area → deselect
    this.selectedPin = null;
    this._render();
    symEditorRenderList();
    const lv = document.getElementById('sym-list-view');
    const fv = document.getElementById('sym-form-view');
    if (lv) lv.style.display = 'flex';
    if (fv) fv.style.display = 'none';
  }

  _selectPin(pin) {
    this.selectedPin = pin;
    this._render();
    symEditorRenderList();
    this.fillForm(pin);
    const lv = document.getElementById('sym-list-view');
    const fv = document.getElementById('sym-form-view');
    if (lv) lv.style.display = 'none';
    if (fv) fv.style.display = 'flex';
    // For non-IC, hide the Remove button (fixed-port symbols)
    const rmBtn = document.getElementById('sym-remove-btn');
    if (rmBtn) rmBtn.style.display = (this.symType === 'ic') ? '' : 'none';
  }

  _TC(t) {
    return { power:'#fca5a5', gnd:'#94a3b8', input:'#86efac', output:'#93c5fd' }[t] || '#fcd34d';
  }

  _sortedPins() {
    return [...(this.profile?.pins || [])].sort((a, b) => (a.number || 0) - (b.number || 0));
  }

  _layout() {
    const STUB = 38, ROW_H = 30, PAD_Y = 18, BOX_W = 160;
    const pins = this._sortedPins();
    const N = pins.length;
    const half = Math.ceil(N / 2);
    const left = pins.slice(0, half);
    const right = [...pins.slice(half)].reverse();
    const rows = Math.max(left.length, right.length, 1);
    const BOX_H = rows * ROW_H + 2 * PAD_Y;
    return { pins, left, right, BOX_W, BOX_H, STUB, ROW_H, PAD_Y };
  }

  _render() {
    if (!this.profile) return;
    const lay = this._layout();
    const { BOX_W, BOX_H, STUB, ROW_H, PAD_Y, left, right, pins } = lay;
    const svgW = this.svg.clientWidth || 600;
    const svgH = this.svg.clientHeight || 400;
    this.svg.removeAttribute('viewBox');
    this.svg.style.height = Math.max(300, Math.min(BOX_H + 120, 600)) + 'px';
    if (this._fitNext) { this._fit(); this._fitNext = false; }
    const tc = t => this._TC(t);
    const allRows = [
      ...left.map((pin, i) => ({ pin, side: 'left', i })),
      ...right.map((pin, i) => ({ pin, side: 'right', i }))
    ];
    // Background (screen space)
    const ox = ((this.panX % 20) + 20) % 20, oy = ((this.panY % 20) + 20) % 20;
    let h = `<rect x="0" y="0" width="${svgW}" height="${svgH}" fill="#0a0c12"/>`;
    h += `<defs><pattern id="sym-eg" x="${ox}" y="${oy}" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="0" cy="0" r="0.8" fill="#1a1d2b"/></pattern></defs>`;
    h += `<rect x="0" y="0" width="${svgW}" height="${svgH}" fill="url(#sym-eg)"/>`;
    // Content group (world space, centered at 0,0)
    const halfW = (BOX_W + 2 * STUB) / 2;
    h += `<g transform="translate(${this.panX},${this.panY}) scale(${this.zoom})">`;
    // Row highlights
    allRows.forEach(({ pin, side, i }) => {
      const ry = -(BOX_H / 2 - PAD_Y) + i * ROW_H - ROW_H / 2;
      const rx = side === 'left' ? -halfW - 10 : 0;
      const isSel = this.selectedPin === pin;
      if (isSel) {
        h += `<rect x="${rx}" y="${ry}" width="${halfW + 10}" height="${ROW_H}" fill="rgba(99,102,241,0.30)"/>`;
        const barX = side === 'left' ? -halfW - 10 : halfW;
        h += `<rect x="${barX}" y="${ry}" width="3" height="${ROW_H}" fill="#818cf8"/>`;
      } else if (i % 2 === 0) {
        h += `<rect x="${rx}" y="${ry}" width="${halfW + 10}" height="${ROW_H}" fill="rgba(255,255,255,0.012)"/>`;
      }
    });
    // IC Box
    h += `<rect x="${-BOX_W / 2}" y="${-BOX_H / 2}" width="${BOX_W}" height="${BOX_H}" rx="5" fill="rgba(108,99,255,0.05)" stroke="#6c63ff" stroke-width="2"/>`;
    h += `<text x="0" y="${-BOX_H / 2 - 10}" text-anchor="middle" font-family="monospace" font-size="11" font-weight="bold" fill="#818cf8">${esc(this.profile.part_number || 'IC')}</text>`;
    h += `<text x="0" y="${BOX_H / 2 + 14}" text-anchor="middle" font-family="sans-serif" font-size="9" fill="#374151">${pins.length} pins</text>`;
    // Legend
    const types = [...new Set(pins.map(p => p.type).filter(Boolean))];
    let lx = -halfW;
    types.forEach(t => {
      h += `<rect x="${lx}" y="${BOX_H / 2 + 20}" width="7" height="7" fill="${tc(t)}"/>`;
      h += `<text x="${lx + 9}" y="${BOX_H / 2 + 27}" font-family="sans-serif" font-size="8" fill="#4b5563">${esc(t)}</text>`;
      lx += t.length * 5.5 + 20;
    });
    // Pins
    allRows.forEach(({ pin, side, i }) => {
      const y = -(BOX_H / 2 - PAD_Y) + i * ROW_H;
      const isL = side === 'left';
      const xBox = isL ? -BOX_W / 2 : BOX_W / 2;
      const xEnd = xBox + (isL ? -STUB : STUB);
      const color = tc(pin.type);
      const isSel = this.selectedPin === pin;
      const emphasis = isSel ? '#fff' : color;
      const pinIdx = pins.indexOf(pin);
      h += `<g data-pin-idx="${pinIdx}" style="cursor:pointer">`;
      h += `<rect x="${Math.min(xBox, xEnd) - 2}" y="${y - ROW_H / 2}" width="${Math.abs(xBox - xEnd) + 4 + BOX_W / 2}" height="${ROW_H}" fill="transparent"/>`;
      h += `<line x1="${xBox}" y1="${y}" x2="${xEnd}" y2="${y}" stroke="${emphasis}" stroke-width="${isSel ? 2.5 : 1.3}"/>`;
      h += `<circle cx="${xEnd}" cy="${y}" r="${isSel ? 5 : 3.5}" fill="${emphasis}"/>`;
      if (isSel) h += `<circle cx="${xEnd}" cy="${y}" r="9" fill="none" stroke="#818cf8" stroke-width="1.2"/>`;
      h += `<text x="${xEnd + (isL ? -5 : 5)}" y="${y + 3.5}" text-anchor="${isL ? 'end' : 'start'}" font-family="monospace" font-size="9" font-weight="${isSel ? 'bold' : 'normal'}" fill="${isSel ? '#a5b4fc' : '#4b5563'}">${pin.number ?? ''}</text>`;
      h += `<text x="${xBox + (isL ? 5 : -5)}" y="${y + 3.5}" text-anchor="${isL ? 'start' : 'end'}" font-family="monospace" font-size="9" font-weight="${isSel ? 'bold' : 'normal'}" fill="${emphasis}">${esc((pin.name || '').slice(0, 11))}</text>`;
      if (!isSel) h += `<text x="${xBox + (isL ? -6 : 6)}" y="${y + 3.5}" text-anchor="${isL ? 'end' : 'start'}" font-family="sans-serif" font-size="7" fill="rgba(75,85,99,0.7)">${esc((pin.type || '').slice(0, 6))}</text>`;
      if (pin.active === 'low') {
        const nm = (pin.name || '').slice(0, 11);
        const approxW = nm.length * 5.5;
        const tx = xBox + (isL ? 5 : -5 - approxW);
        h += `<line x1="${tx}" y1="${y - 7}" x2="${tx + approxW}" y2="${y - 7}" stroke="${emphasis}" stroke-width="1"/>`;
      }
      h += `</g>`;
    });
    if (pins.length === 0) {
      h += `<text x="0" y="6" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#374151">Use "+ L Pin" / "+ R Pin" to add pins</text>`;
    }
    h += `</g>`;
    this.svg.innerHTML = h;
    this._vg = this.svg.querySelector('g');
  }

  // Fill form inputs for the given pin
  fillForm(pin) {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
    set('spe-number', pin.number);
    set('spe-name',   pin.name || '');
    set('spe-type',   pin.type || 'input');
    set('spe-active', pin.active || '');
    set('spe-page',   pin.datasheet_page);
    set('spe-desc',   pin.description || '');
    set('spe-req',    pin.requirements || '');
  }

  updateSelectedPin(field, rawVal) {
    if (!this.selectedPin) return;
    let val = rawVal;
    if (field === 'number' || field === 'datasheet_page') val = rawVal === '' ? null : parseInt(rawVal);
    if (field === 'active' && rawVal === '') val = null;
    this.selectedPin[field] = val;
    this._render();
  }

  addPin(side) {
    if (!this.profile) return;
    this.profile.pins = this.profile.pins || [];
    const maxN = this.profile.pins.reduce((m, p) => Math.max(m, p.number || 0), 0);
    const newPin = { number: maxN + 1, name: 'P' + (maxN + 1), type: 'input',
      description: '', requirements: '', active: null, internal_pull: null,
      ambiguous: false, datasheet_page: null };
    if (side === 'right') {
      this.profile.pins.push(newPin);
    } else {
      const half = Math.ceil((this.profile.pins.length + 1) / 2);
      this.profile.pins.splice(Math.min(half - 1, this.profile.pins.length), 0, newPin);
      this.profile.pins.sort((a, b) => (a.number || 0) - (b.number || 0));
    }
    this.selectedPin = newPin;
    this._render();
  }

  removeSelectedPin() {
    if (!this.selectedPin || !this.profile) return;
    this.profile.pins = this.profile.pins.filter(p => p !== this.selectedPin);
    this.selectedPin = null;
    this._render();
  }

  async save() {
    if (!this.slug || !this.profile) return false;
    const res = await fetch(`/api/library/${this.slug}/pins`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pins: this.profile.pins })
    });
    return res.ok;
  }
}

let symEditor = null;

// ── Pin list (HTML rows in right panel) ────────────────────────────────────
function symEditorRenderList() {
  const list = document.getElementById('sym-pin-list');
  if (!list || !symEditor) return;
  const pins = symEditor._sortedPins();
  const TC = symEditor._TC.bind(symEditor);
  if (pins.length === 0) {
    list.innerHTML = '<div style="padding:16px 12px;font-size:12px;color:var(--text-muted);text-align:center;">No pins.<br>Use + L Pin / + R Pin above.</div>';
    return;
  }
  list.innerHTML = pins.map((pin, idx) => {
    const isSel = symEditor.selectedPin === pin;
    const color = TC(pin.type);
    return `<div onclick="symEditorSelectPin(${idx})"
      style="cursor:pointer;padding:5px 10px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:6px;
        background:${isSel ? 'rgba(99,102,241,0.22)' : 'transparent'};
        border-left:3px solid ${isSel ? '#818cf8' : 'transparent'};">
      <span style="font-family:monospace;font-size:10px;color:#4b5563;width:18px;text-align:right;flex-shrink:0;">${pin.number ?? ''}</span>
      <span style="font-family:monospace;font-size:11px;color:${isSel ? '#fff' : color};font-weight:${isSel ? '700' : '400'};flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${pin.name || '?'}</span>
      <span style="font-size:9px;color:#4b5563;flex-shrink:0;">${(pin.type || '').slice(0, 4)}</span>
    </div>`;
  }).join('');
}

function symEditorSelectPin(idx) {
  if (!symEditor) return;
  const pins = symEditor._sortedPins();
  symEditor.selectedPin = pins[idx] ?? null;
  symEditor._render();
  symEditorRenderList();
  if (symEditor.selectedPin) {
    symEditor.fillForm(symEditor.selectedPin);
    const lv = document.getElementById('sym-list-view');
    const fv = document.getElementById('sym-form-view');
    if (lv) lv.style.display = 'none';
    if (fv) fv.style.display = 'flex';
    // Hide remove btn for non-IC
    const rmBtn = document.getElementById('sym-remove-btn');
    if (rmBtn) rmBtn.style.display = symEditor.symType === 'ic' ? '' : 'none';
  }
}

function symEditorBackToList() {
  if (symEditor) { symEditor.selectedPin = null; symEditor._render(); }
  symEditorRenderList();
  symPanelTab('pins');
}

function symEditorAddPin(side) {
  if (!symEditor) return;
  symEditor.addPin(side);
  symEditorRenderList();
  if (symEditor.selectedPin) {
    symEditor.fillForm(symEditor.selectedPin);
    symPanelTab('pins');
    const lv = document.getElementById('sym-list-view');
    const fv = document.getElementById('sym-form-view');
    if (lv) lv.style.display = 'none';
    if (fv) fv.style.display = 'flex';
  }
}

function symEditorRemovePin() {
  if (!symEditor) return;
  symEditor.removeSelectedPin();
  symEditorBackToList();
}

async function symEditorSave() {
  if (!symEditor) return;
  const ok = await symEditor.save();
  if (ok) {
    const btn = document.querySelector('[onclick="symEditorSave()"]');
    if (btn) { const orig = btn.textContent; btn.textContent = '✓ Saved!'; setTimeout(() => btn.textContent = orig, 1500); }
    if (selectedSlug && profileCache[selectedSlug]) {
      profileCache[selectedSlug].pins = symEditor.profile.pins;
    }
    // Sync active version snapshot so reload shows updated pins
    const avId = typeof _symActiveVersionId !== 'undefined' ? _symActiveVersionId : null;
    const slug = symEditor.slug || selectedSlug;
    if (avId && slug) await fetch(`/api/library/${slug}/history/${avId}`, { method: 'PUT' }).catch(() => {});
  } else {
    alert('Save failed');
  }
}

function symEditorUpdatePin(field, value) {
  if (!symEditor || !symEditor.selectedPin) return;
  symEditor.updateSelectedPin(field, value);
  // Refresh the pin name/type in the list without closing form
  symEditorRenderList();
}

// ── Component Parameters auto-save (debounced) ──────────────────────────────
let _symParamTimer = null;

function symParamAutoSave() {
  clearTimeout(_symParamTimer);
  _symParamTimer = setTimeout(_symParamFlush, 700);
  const st = document.getElementById('sym-param-status');
  if (st) { st.textContent = '…'; st.style.opacity = '1'; st.style.color = 'var(--text-muted)'; }
}

async function _symParamFlush() {
  const slug = symEditor?.slug || selectedSlug;
  if (!slug) return;
  const get = id => document.getElementById(id)?.value?.trim() ?? '';
  const params = {
    part_number:  get('sym-param-partnum'),
    manufacturer: get('sym-param-mfr'),
    value:        get('sym-param-value'),
    designator:   get('sym-param-des'),
    description:  get('sym-param-desc'),
  };
  const st = document.getElementById('sym-param-status');
  const res = await fetch(`/api/library/${slug}/params`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params)
  });
  if (res.ok) {
    if (profileCache[slug]) Object.assign(profileCache[slug], params);
    if (symEditor?.profile) Object.assign(symEditor.profile, params);
    if (symEditor) symEditor._render();
    // Sync active version snapshot so reload shows the updated params
    const avId = typeof _symActiveVersionId !== 'undefined' ? _symActiveVersionId : null;
    if (avId) await fetch(`/api/library/${slug}/history/${avId}`, { method: 'PUT' }).catch(() => {});
    if (st) { st.textContent = '✓'; st.style.color = '#22c55e'; st.style.opacity = '1'; setTimeout(() => { st.style.opacity = '0'; }, 1200); }
  } else {
    if (st) { st.textContent = '✗'; st.style.color = '#ef4444'; st.style.opacity = '1'; }
  }
}

// ── Symbol rendering — SVG-based editor ────────────────────────────────────
function renderSymbolWithEditor(profile) {
  if (selectedSlug && profile) profileCache[selectedSlug] = profile;
  const svgEl = document.getElementById('symbol-canvas');
  if (!svgEl) return;
  if (!symEditor || symEditor.svg !== svgEl) symEditor = new SymbolEditorSVG(svgEl);
  symEditor.load(selectedSlug || '', profile);
  symEditorRenderList();
  symPanelTab('pins');
}

// ── Symbol panel tab switching ───────────────────────────────────────────────
function symPanelTab(tab) {
  const lv = document.getElementById('sym-list-view');
  const fv = document.getElementById('sym-form-view');
  if (lv) lv.style.display = 'flex';
  if (fv) fv.style.display = 'none';
  symEditorRenderList();
}

// ── Symbol type picker ───────────────────────────────────────────────────────
const SYM_TYPES = [
  { key:'ic', label:'IC Box' },
];

let symPickedType = null;  // pending type selection (not yet saved)

function symRenderTypePicker() {
  const grid = document.getElementById('sym-type-grid');
  if (!grid) return;
  const current = symEditor ? detectSymbolType(symEditor.profile || {}) : 'ic';
  const picked = symPickedType || current;

  grid.innerHTML = SYM_TYPES.map(t =>
    `<div class="sym-type-card${t.key === picked ? ' picked' : ''}"
          onclick="symEditorPickType('${t.key}')"
          title="${t.label}">
       <svg width="60" height="42" viewBox="-60 -35 120 70" style="background:#0a0c12;border-radius:3px;display:block;">
         <defs><pattern id="stt-${t.key}" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="0" cy="0" r="0.8" fill="#1e2030"/></pattern></defs>
         <rect x="-60" y="-35" width="120" height="70" fill="url(#stt-${t.key})"/>
         <rect x="-28" y="-18" width="56" height="36" rx="3" fill="rgba(108,99,255,0.07)" stroke="#6c63ff" stroke-width="1.5"/>
         <text x="0" y="4" text-anchor="middle" font-family="monospace" font-size="9" font-weight="bold" fill="#818cf8">IC</text>
       </svg>
       <span>${t.label}</span>
     </div>`
  ).join('');
}

function symEditorPickType(type) {
  symPickedType = type;
  // Preview on main canvas immediately
  if (symEditor && symEditor.profile) {
    symEditor.symType = type;
    symEditor._render();
    symEditorRenderList();
  }
  // Re-render picker to show selection
  symRenderTypePicker();
}

async function symEditorSaveType() {
  const type = symPickedType || (symEditor ? detectSymbolType(symEditor.profile || {}) : null);
  if (!type || !symEditor?.slug) return;
  const res = await fetch(`/api/library/${symEditor.slug}/symbol_type`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ symbol_type: type })
  });
  if (res.ok) {
    // Persist locally
    if (symEditor.profile) symEditor.profile.symbol_type = type;
    if (selectedSlug && profileCache[selectedSlug]) profileCache[selectedSlug].symbol_type = type;
    symPickedType = null;
    const btn = document.querySelector('[onclick="symEditorSaveType()"]');
    if (btn) { const orig = btn.textContent; btn.textContent = '✓ Saved!'; setTimeout(() => btn.textContent = orig, 1500); }
    symRenderTypePicker();
  } else {
    alert('Save failed');
  }
}

// ── App circuit using SchematicEditor ───────────────────────────────────────
let appCircuitEditor = null;

// ── Schematic Example BOM placement tracking ────────────────────────────────
function refreshAccBomRows() {
  const tbody = document.getElementById('acc-bom-tbody');
  if (!tbody) return;
  const comps = appCircuitEditor?.project?.components || [];
  // Count how many of each slug are present on the schematic
  const slugCounts = {};
  for (const c of comps) {
    const key = c.slug || '';
    if (key) slugCounts[key] = (slugCounts[key] || 0) + 1;
  }
  // Walk rows top-to-bottom, consuming one instance per matching slug
  const slugConsumed = {};
  for (const row of tbody.querySelectorAll('tr[data-slug]')) {
    const slug = row.dataset.slug;
    if (!slug) continue;
    slugConsumed[slug] = slugConsumed[slug] || 0;
    const placed = slugConsumed[slug] < (slugCounts[slug] || 0);
    if (placed) slugConsumed[slug]++;
    row.classList.toggle('acc-bom-placed', placed);
    const icon = row.querySelector('.acc-bom-icon');
    if (icon) icon.textContent = placed ? '✓' : '+';
  }
}

function renderAppCircuitWithEditor(profile) {
  if (selectedSlug && profile) profileCache[selectedSlug] = profile;
  const svgEl = document.getElementById('app-circuit-canvas');
  if (!svgEl) return;
  const isNew = !appCircuitEditor || appCircuitEditor.svg !== svgEl;
  if (isNew) {
    if (appCircuitEditor) appCircuitEditor.destroy();
    appCircuitEditor = new SchematicEditor(svgEl, { labelInputId: 'acc-label-input' });
    // Patch _render to keep the acc info panel in sync
    const _accRenderOrig = appCircuitEditor._render.bind(appCircuitEditor);
    appCircuitEditor._render = function() {
      _accRenderOrig();
      if (typeof updateAccInfoPanel !== 'function') return;
      if (appCircuitEditor.selected?.type === 'comp') {
        const comp = appCircuitEditor.project.components.find(c => c.id === appCircuitEditor.selected.id);
        updateAccInfoPanel(comp || null);
      } else if (appCircuitEditor.selected?.type === 'wire') {
        const wire = appCircuitEditor.project.wires.find(w => w.id === appCircuitEditor.selected.id);
        updateAccWireInfoPanel(wire || null);
      } else if (appCircuitEditor.selected?.type === 'label') {
        const lbl = (appCircuitEditor.project.labels||[]).find(l => l.id === appCircuitEditor.selected.id);
        updateAccLabelInfoPanel(lbl || null);
      } else {
        updateAccInfoPanel(null);
      }
      if (typeof renderSchNets === 'function') renderSchNets(appCircuitEditor, 'acc-nets-list', 'acc-nets-count');
      refreshAccBomRows();
    };
  } else {
    appCircuitEditor._render();
  }
  appCircuitEditor.loadExample(profile);
  // Sync BOM placed-state after the example loads
  setTimeout(refreshAccBomRows, 50);
}

// ── PDF panel (right side) ──────────────────────────────────────────────────
function updatePdfPanel(p) {
  const panel = document.getElementById('pdf-panel');
  const frame = document.getElementById('pdf-frame');
  if (!panel || !frame) return;
  if (p && p.filename && p.filename !== 'builtin') {
    frame.src = `/api/library/${selectedSlug}/pdf`;
    panel.style.display = 'flex';
    // reset page hint
    const hint = document.getElementById('pdf-page-hint');
    if (hint) hint.textContent = 'Click a pin or passive to jump to its page';
    const box = document.getElementById('pdf-ref-highlight');
    if (box) box.classList.remove('show');
  } else {
    panel.style.display = 'none';
  }
}

function closePdfPanel() {
  const panel = document.getElementById('pdf-panel');
  if (panel) panel.style.display = 'none';
}

// ── Pin Editor ─────────────────────────────────────────────────────────────
function closePinEditor() {
  const modal = document.getElementById('pin-editor-modal');
  if (modal) modal.style.display = 'none';
}

function renderPinEditorRows(pins) {
  const tbody = document.getElementById('pin-editor-tbody');
  const PIN_TYPES = ['input','output','power','gnd','passive','bidirectional'];
  tbody.innerHTML = pins.map((pin, i) => `
    <tr data-i="${i}">
      <td><input type="number" value="${pin.number ?? ''}" oninput="peUpdate(${i},'number',this.value)"
        style="width:38px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;"></td>
      <td><input type="text" value="${(pin.name||'').replace(/"/g,'&quot;')}" oninput="peUpdate(${i},'name',this.value)"
        style="width:72px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;font-family:monospace;"></td>
      <td><select onchange="peUpdate(${i},'type',this.value)"
        style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;">
        ${PIN_TYPES.map(t => `<option value="${t}"${pin.type===t?' selected':''}>${t}</option>`).join('')}
      </select></td>
      <td><input type="text" value="${(pin.description||'').replace(/"/g,'&quot;')}" oninput="peUpdate(${i},'description',this.value)"
        style="width:200px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;"></td>
      <td><button onclick="peRemove(${i})" title="Remove pin"
        style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);border-radius:3px;color:#ef4444;padding:2px 7px;cursor:pointer;font-size:11px;">✕</button></td>
    </tr>`).join('');
}

function peUpdate(i, field, val) {
  const profile = profileCache[selectedSlug];
  if (!profile || !profile.pins[i]) return;
  profile.pins[i][field] = field === 'number' ? (val === '' ? null : parseInt(val)) : val;
}

function peRemove(i) {
  const profile = profileCache[selectedSlug];
  if (!profile) return;
  profile.pins.splice(i, 1);
  renderPinEditorRows(profile.pins);
}

function peAddPin() {
  const profile = profileCache[selectedSlug];
  if (!profile) return;
  const maxNum = profile.pins.reduce((m, p) => Math.max(m, p.number || 0), 0);
  profile.pins.push({ number: maxNum + 1, name: 'NEW', type: 'input', description: '', requirements: '', datasheet_page: null, active: null, internal_pull: null, ambiguous: false });
  renderPinEditorRows(profile.pins);
}

async function savePins() {
  const profile = profileCache[selectedSlug];
  if (!profile) return;
  const res = await fetch(`/api/library/${selectedSlug}/pins`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pins: profile.pins })
  });
  if (!res.ok) { alert('Save failed'); return; }
  closePinEditor();
  requestAnimationFrame(() => renderSymbolWithEditor(profile));
}

// ── Schematic Info Panel ────────────────────────────────────────────────────
let infoCanvas = null;
let _infoSlug = null;

function _schInfoTitle(t) {
  const el = document.getElementById('sch-info-title');
  if (el) el.textContent = t;
}

// ── Component list (shown when nothing is selected) ────────────────────────
const _compTypeColors = {
  ic:'#6c63ff', amplifier:'#6c63ff', resistor:'#c87533', capacitor:'#3b82f6',
  capacitor_pol:'#3b82f6', inductor:'#a78bfa', led:'#22c55e', diode:'#f59e0b',
  npn:'#f59e0b', pnp:'#f59e0b', nmos:'#f59e0b', pmos:'#f59e0b',
  vcc:'#ef4444', gnd:'#6b7280'
};
const _compTypeLabels = {
  ic:'IC', amplifier:'AMP', resistor:'R', capacitor:'C', capacitor_pol:'C+',
  inductor:'L', led:'LED', diode:'D', npn:'NPN', pnp:'PNP', nmos:'FET', pmos:'FET',
  vcc:'VCC', gnd:'GND'
};

function _compListRowHtml(c, onclickJs) {
  const col = _compTypeColors[c.symType] || '#6c63ff';
  const lbl = _compTypeLabels[c.symType] || (c.symType||'IC').slice(0,4).toUpperCase();
  const des = esc(c.designator || c.id || '?');
  const val = esc(c.value || c.slug || '');
  return `<div onclick="${onclickJs}" style="display:flex;align-items:center;gap:6px;padding:5px 10px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,0.04);transition:background 0.1s;" onmouseover="this.style.background='var(--surface2)'" onmouseout="this.style.background=''">
    <span style="font-size:10px;font-weight:700;color:var(--text);font-family:monospace;min-width:26px;">${des}</span>
    <span style="font-size:9px;font-weight:700;color:${col};background:${col}22;border-radius:3px;padding:1px 4px;flex-shrink:0;">${lbl}</span>
    <span style="font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="${val}">${val}</span>
  </div>`;
}

function _schSelectComp(id) {
  editor.selected = { type: 'comp', id };
  const comp = editor.project.components.find(c => c.id === id);
  if (comp) {
    editor.panX = editor._W() / 2 - comp.x * editor.zoom;
    editor.panY = editor._H() / 2 - comp.y * editor.zoom;
  }
  editor._render();
}

function _accSelectComp(id) {
  if (!appCircuitEditor) return;
  appCircuitEditor.selected = { type: 'comp', id };
  const comp = appCircuitEditor.project.components.find(c => c.id === id);
  if (comp) {
    appCircuitEditor.panX = appCircuitEditor._W() / 2 - comp.x * appCircuitEditor.zoom;
    appCircuitEditor.panY = appCircuitEditor._H() / 2 - comp.y * appCircuitEditor.zoom;
  }
  appCircuitEditor._render();
}

function _renderSchCompList() {
  const el = document.getElementById('sch-comp-list');
  if (!el) return;
  const comps = editor.project.components || [];
  if (!comps.length) {
    el.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-muted);">No components yet.</div>';
    return;
  }
  el.innerHTML = comps.map(c => _compListRowHtml(c, `_schSelectComp('${c.id}')`)).join('');
}

function _renderAccCompList() {
  const el = document.getElementById('acc-info-empty');
  if (!el || !appCircuitEditor) return;
  const comps = appCircuitEditor.project.components || [];
  if (!comps.length) {
    el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:10px;text-align:center;padding:12px;line-height:1.5;">Click a component<br>to see details</div>';
    return;
  }
  el.innerHTML = comps.map(c => _compListRowHtml(c, `_accSelectComp('${c.id}')`)).join('');
}

function _showSchProjectPanel() {
  const proj = document.getElementById('sch-info-project');
  const content = document.getElementById('sch-info-content');
  if (proj) { proj.style.display = 'flex'; const inp = document.getElementById('sch-proj-name'); if (inp && document.activeElement !== inp) inp.value = editor.project.name || ''; }
  if (content) content.style.display = 'none';
  _schInfoTitle('Project');
  _renderSchCompList();
}

function updateSchInfoPanel(comp) {
  const proj = document.getElementById('sch-info-project');
  const content = document.getElementById('sch-info-content');
  if (!comp) {
    _showSchProjectPanel(); _infoSlug = null; return;
  }
  if (proj) proj.style.display = 'none';
  _schInfoTitle('Component Info');
  // Only skip re-render if showing THIS SAME component and user is typing in it
  const focused = document.activeElement;
  if (focused && content?.contains(focused) && content.dataset.selId === comp.id) return;
  content.dataset.selId = comp.id; content.dataset.selType = 'comp';
  content.style.display = 'flex';
  _infoSlug = comp.slug || null;

  // Draw symbol (SVG)
  const infoSvg = document.getElementById('sch-info-canvas');
  if (infoSvg) {
    const fakeComp = { ...comp, rotation: 0 };
    const symType = comp.symType || 'ic';
    const isIC = symType === 'ic' || !SYMDEFS[symType];
    const lay = isIC ? editor._icLayout(comp.slug||'') : editor._def(symType);
    const lw = (lay.BOX_W || lay.w || 120) + 2 * (lay.PIN_STUB || 0) + 40;
    const lh = (lay.BOX_H || lay.h || 80) + 30;
    const W = 200, H = 160;
    const s = Math.min(W / lw, H / lh, isIC ? 1.0 : 1.4) * 0.85;
    let h = `<defs><pattern id="si-gp" width="16" height="16" patternUnits="userSpaceOnUse"><circle cx="0" cy="0" r="0.7" fill="#1e2030"/></pattern></defs>`;
    h += `<rect width="100%" height="100%" fill="#0a0c12"/>`;
    h += `<rect width="100%" height="100%" fill="url(#si-gp)"/>`;
    h += `<g transform="translate(${W/2},${H/2}) scale(${s})">`;
    h += editor._symH(fakeComp, false, false);
    const ports = isIC ? lay.ports : lay.ports;
    for (const p of ports) h += `<circle cx="${p.dx}" cy="${p.dy}" r="3" fill="rgba(96,165,250,0.4)"/>`;
    h += `</g>`;
    infoSvg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    infoSvg.innerHTML = h;
  }

  // Name + description (inline editable fields)
  const profile = comp.slug ? (profileCache[comp.slug] || library[comp.slug]) : null;
  const isPassive = ['resistor','capacitor','capacitor_pol','inductor','diode','led'].includes(comp.symType);
  const cid = esc(comp.id);
  const inputStyle = 'width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:3px 7px;font-size:11px;font-family:monospace;';
  document.getElementById('sch-info-name').innerHTML = `
    <div style="display:flex;flex-direction:column;gap:5px;">
      <div>
        <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;">Designator</div>
        <input value="${esc(comp.designator||'')}" onchange="schInfoUpdate('${cid}','designator',this.value)" style="${inputStyle}color:var(--accent);font-weight:700;">
      </div>
      <div>
        <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;">Value</div>
        <input value="${esc(comp.value||'')}" placeholder="e.g. 100nF, 10kΩ" onchange="schInfoUpdate('${cid}','value',this.value)" style="${inputStyle}">
      </div>
      <div>
        <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;">Part Number</div>
        <input value="${esc(comp.partNumber||comp.slug||'')}" placeholder="Manufacturer P/N" onchange="schInfoUpdate('${cid}','partNumber',this.value)" style="${inputStyle}color:var(--text-dim);font-size:10px;">
      </div>
    </div>`;
  document.getElementById('sch-info-desc').textContent = (profile?.description || '').slice(0, 120);

  // Build per-pin net map from netlist
  const pinNetMap = {};
  try {
    const { nets } = computeNetOverlay(editor);
    for (const net of nets) {
      for (const pn of net.ports) pinNetMap[pn.nodeId] = net.name;
    }
  } catch(e) {}
  const compPorts = editor._ports(comp);

  // For passives: show value + per-pin nets; for ICs: show full pin list with nets
  const pinsEl = document.getElementById('sch-info-pins');
  const pins = profile?.pins || [];
  const typeColor = { power:'#fca5a5', gnd:'#94a3b8', input:'#86efac', output:'#93c5fd' };
  const netInput = (nodeId, currentNet, portName) => {
    const val = currentNet === '—' ? '' : esc(currentNet);
    const cid2 = esc(comp.id); const pn2 = esc(portName);
    return `<input value="${val}" placeholder="net" title="Pin net — Enter to rename"
      style="width:70px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:${currentNet!=='—'?'#6c63ff':'var(--text-muted)'};font-size:9px;font-family:monospace;padding:1px 4px;"
      onkeydown="if(event.key==='Enter'){schPinRenameNet('${cid2}','${pn2}',this.value.trim());this.blur();}"
      onblur="if(this.value.trim()!=='${val}')schPinRenameNet('${cid2}','${pn2}',this.value.trim());">`;
  };
  if (isPassive) {
    const portRows = compPorts.map(port => {
      const net = pinNetMap[`${comp.id}::${port.name}`] || '—';
      return `<div style="display:flex;gap:4px;align-items:center;padding:2px 0;border-bottom:1px solid rgba(46,50,80,0.4);">
        <span style="font-family:monospace;font-weight:700;color:#fcd34d;flex:1;">${port.name}</span>
        ${netInput(`${comp.id}::${port.name}`, net, port.name)}
      </div>`;
    }).join('');
    pinsEl.innerHTML = `<div style="display:flex;flex-direction:column;gap:6px;padding:4px 0;">
      <div style="font-size:10px;color:var(--text-muted);">Value: <span style="color:var(--text);font-family:monospace;">${comp.value || '<i>not set</i>'}</span></div>
      ${comp.partNumber ? `<div style="font-size:10px;color:var(--text-muted);">Part: <span style="color:var(--text);font-family:monospace;font-size:9px;">${comp.partNumber}</span></div>` : ''}
    </div>${portRows}`;
  } else {
    const maxShow = 20;
    const pinsToShow = pins.length ? pins.slice(0, maxShow) : compPorts.map(p => ({ name: p.name, number: null, type: null }));
    pinsEl.innerHTML = pinsToShow.map(p => {
      const net = pinNetMap[`${comp.id}::${p.name}`] || '—';
      return `<div style="display:flex;gap:4px;align-items:center;padding:2px 0;border-bottom:1px solid rgba(46,50,80,0.4);">
        <span style="color:#4b5563;font-family:monospace;min-width:16px;">${p.number??''}</span>
        <span style="font-family:monospace;font-weight:700;color:${typeColor[p.type]||'#fcd34d'};flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${p.name||''}</span>
        ${netInput(`${comp.id}::${p.name}`, net, p.name)}
      </div>`;
    }).join('') + (pins.length > maxShow ? `<div style="color:var(--text-muted);padding:4px 0;font-size:10px;">+${pins.length-maxShow} more…</div>` : '');
  }

  document.getElementById('sch-info-goto-btn').style.display = comp.slug && library[comp.slug] ? 'block' : 'none';

  // If IC and profile not in cache yet, fetch it
  if (comp.symType === 'ic' && comp.slug && !profileCache[comp.slug]) {
    fetch(`/api/library/${comp.slug}`).then(r => r.json()).then(p => {
      profileCache[comp.slug] = p;
      if (editor.selected?.id === comp.id) updateSchInfoPanel(comp);
    }).catch(() => {});
  }
}

// ── Rename net for a schematic pin ──────────────────────────────────────────
// Finds labels on the same net as compId::portName and renames them.
// If no label exists, places a new label at the port's world position.
function schPinRenameNet(compId, portName, newNet) {
  if (!newNet) return;
  const ed = editor;
  const comp = ed.project.components.find(c => c.id === compId);
  if (!comp) return;
  const port = ed._ports(comp).find(p => p.name === portName);
  if (!port) return;

  const SNAP = 12;
  const snap = v => Math.round(v / SNAP) * SNAP;
  const ptKey = (x, y) => `${snap(x)},${snap(y)}`;
  const parent = {};
  const find = k => { if (!(k in parent)) parent[k] = k; if (parent[k] !== k) parent[k] = find(parent[k]); return parent[k]; };
  const union = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) parent[ra] = rb; };

  const portId = `${compId}::${portName}`;
  find(portId);
  const ptMap = new Map();
  const addPt = (k, id) => { if (!ptMap.has(k)) ptMap.set(k, []); ptMap.get(k).push(id); };
  addPt(ptKey(port.x, port.y), portId);

  for (const w of (ed.project.wires || [])) {
    if (!w.points?.length) continue;
    const base = `wire::${w.id}::0`; find(base);
    addPt(ptKey(w.points[0].x, w.points[0].y), base);
    for (let i = 1; i < w.points.length; i++) {
      const wid = `wire::${w.id}::${i}`; find(wid); union(base, wid);
      addPt(ptKey(w.points[i].x, w.points[i].y), wid);
    }
  }
  for (const ids of ptMap.values()) for (let i = 1; i < ids.length; i++) union(ids[0], ids[i]);

  const portRoot = find(portId);

  // Find labels connected to same root
  const labels = ed.project.labels || (ed.project.labels = []);
  let renamed = 0;
  for (const lbl of labels) {
    const lk = ptKey(lbl.x, lbl.y);
    if (ptMap.get(lk)?.some(id => find(id) === portRoot)) { lbl.name = newNet; renamed++; }
  }

  // If no label found, place one at the port position
  if (!renamed) {
    labels.push({ id: 'lbl_' + Date.now(), x: port.x, y: port.y, name: newNet });
  }

  ed.dirty = true;
  ed.render();
  updateSchInfoPanel(comp);
}

// ── Embedded editor (datasheet/example view) info panel ─────────────────────
function updateAccInfoPanel(comp) {
  const emptyEl = document.getElementById('acc-info-empty');
  const contentEl = document.getElementById('acc-info-content');
  if (!emptyEl || !contentEl) return;
  if (!comp) {
    emptyEl.style.display = 'flex'; contentEl.style.display = 'none';
    _renderAccCompList();
    return;
  }
  // Only skip re-render if showing THIS SAME component and user is typing in it
  const focused = document.activeElement;
  if (focused && contentEl.contains(focused) && contentEl.dataset.selId === comp.id) return;
  contentEl.dataset.selId = comp.id;
  emptyEl.style.display = 'none'; contentEl.style.display = 'flex';

  // Symbol preview
  const infoSvg = document.getElementById('acc-info-canvas');
  if (infoSvg && appCircuitEditor) {
    const fakeComp = { ...comp, rotation: 0 };
    const isIC = comp.symType === 'ic' || !SYMDEFS[comp.symType];
    const lay = isIC ? appCircuitEditor._icLayout(comp.slug||'') : appCircuitEditor._def(comp.symType);
    const lw = (lay.BOX_W || lay.w || 120) + 2 * (lay.PIN_STUB || 0) + 40;
    const lh = (lay.BOX_H || lay.h || 80) + 30;
    const W = 183, H = 110;
    const s = Math.min(W / lw, H / lh, isIC ? 1.0 : 1.4) * 0.85;
    let h = `<rect width="100%" height="100%" fill="#0a0c12"/>`;
    h += `<g transform="translate(${W/2},${H/2}) scale(${s})">`;
    h += appCircuitEditor._symH(fakeComp, false, false);
    for (const p of lay.ports) h += `<circle cx="${p.dx}" cy="${p.dy}" r="3" fill="rgba(96,165,250,0.4)"/>`;
    h += `</g>`;
    infoSvg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    infoSvg.innerHTML = h;
  }

  // Name and description
  const profile = comp.slug ? (profileCache[comp.slug] || library[comp.slug]) : null;
  const cid = comp.id;
  const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
  document.getElementById('acc-info-name').innerHTML = `
    <div style="display:flex;flex-direction:column;gap:5px;padding-bottom:4px;">
      <div><div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px;">Designator</div>
        <input value="${esc(comp.designator||'')}" onchange="accInfoUpdate('${cid}','designator',this.value)" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--accent);padding:3px 6px;font-size:11px;font-family:monospace;font-weight:700;box-sizing:border-box;"></div>
      <div><div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px;">Value</div>
        <input value="${esc(comp.value||'')}" placeholder="e.g. 100nF, 10kΩ" onchange="accInfoUpdate('${cid}','value',this.value)" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:3px 6px;font-size:11px;font-family:monospace;box-sizing:border-box;"></div>
      <div><div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px;">Part Number</div>
        <input value="${esc(comp.partNumber||comp.slug||'')}" placeholder="Manufacturer P/N" onchange="accInfoUpdate('${cid}','partNumber',this.value)" style="width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:3px 6px;font-size:11px;font-family:monospace;box-sizing:border-box;"></div>
    </div>`;
  document.getElementById('acc-info-desc').textContent = (profile?.description || '').slice(0, 80);

  // Per-pin net assignments
  const pinNetMap = {};
  try {
    const { nets } = computeNetOverlay(appCircuitEditor);
    for (const net of nets) for (const pn of net.ports) pinNetMap[pn.nodeId] = net.name;
  } catch(e) {}
  const compPorts = appCircuitEditor._ports(comp);
  const pins = profile?.pins || [];
  const typeColor = { power:'#fca5a5', gnd:'#94a3b8', input:'#86efac', output:'#93c5fd' };
  const pinsToShow = pins.length ? pins.slice(0, 18) : compPorts.map(p => ({ name: p.name, number: null, type: null }));
  document.getElementById('acc-info-pins').innerHTML = pinsToShow.map(p => {
    const net = pinNetMap[`${comp.id}::${p.name}`] || '—';
    return `<div style="display:flex;gap:3px;align-items:center;padding:2px 0;border-bottom:1px solid rgba(46,50,80,0.4);">
      <span style="color:#4b5563;font-family:monospace;min-width:14px;font-size:9px;">${p.number??''}</span>
      <span style="font-family:monospace;font-weight:700;color:${typeColor[p.type]||'#fcd34d'};flex:1;font-size:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${p.name||''}</span>
      <span style="font-size:8px;color:#6c63ff;background:rgba(108,99,255,0.12);border-radius:3px;padding:1px 3px;white-space:nowrap;">${net}</span>
    </div>`;
  }).join('') + (pins.length > 18 ? `<div style="color:var(--text-muted);padding:3px 0;font-size:9px;">+${pins.length-18} more…</div>` : '');

  // Lazy-load profile if not cached
  if (comp.symType === 'ic' && comp.slug && !profileCache[comp.slug]) {
    fetch(`/api/library/${comp.slug}`).then(r => r.json()).then(p => {
      profileCache[comp.slug] = p;
      if (appCircuitEditor?.selected?.id === comp.id) updateAccInfoPanel(comp);
    }).catch(() => {});
  }
}

function schInfoUpdate(compId, field, value) {
  const comp = editor.project.components.find(c => c.id === compId);
  if (!comp) return;
  comp[field] = value;
  editor.dirty = true;
  editor._saveHist();
  editor._render();
  editor._status();
}

function accInfoUpdate(compId, field, value) {
  if (!appCircuitEditor) return;
  const comp = appCircuitEditor.project.components.find(c => c.id === compId);
  if (!comp) return;
  comp[field] = value;
  appCircuitEditor.dirty = true;
  appCircuitEditor._saveHist();
  appCircuitEditor._render();
}

function accLabelUpdate(labelId, field, value) {
  if (!appCircuitEditor) return;
  const lbl = (appCircuitEditor.project.labels||[]).find(l => l.id === labelId);
  if (!lbl) return;
  lbl[field] = value;
  appCircuitEditor.dirty = true;
  appCircuitEditor._saveHist();
  appCircuitEditor._render();
}

function updateAccLabelInfoPanel(label) {
  const emptyEl = document.getElementById('acc-info-empty');
  const contentEl = document.getElementById('acc-info-content');
  if (!label) { emptyEl.style.display = 'flex'; contentEl.style.display = 'none'; return; }
  const focused = document.activeElement;
  if (focused && contentEl?.contains(focused) && contentEl.dataset.selId === label.id) return;
  contentEl.dataset.selId = label.id; contentEl.dataset.selType = 'label';
  emptyEl.style.display = 'none'; contentEl.style.display = 'flex';
  const infoSvg = document.getElementById('acc-info-canvas');
  if (infoSvg) {
    const name = esc(label.name || '');
    const nc = `hsl(${([...label.name||''].reduce((h,c)=>(h*31+c.charCodeAt(0))&0xffff,0)*137+30)%360},65%,55%)`;
    infoSvg.setAttribute('viewBox', '0 0 200 110');
    infoSvg.innerHTML = `<rect width="200" height="110" fill="#0a0c12"/>
      <line x1="60" y1="55" x2="94" y2="55" stroke="${nc}" stroke-width="1.5"/>
      <circle cx="96" cy="55" r="4" fill="${nc}"/>
      <text x="104" y="59" font-family="monospace" font-size="12" fill="${nc}" font-weight="bold">${name}</text>`;
  }
  const lid = esc(label.id);
  const inputStyle = 'width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--accent);padding:2px 5px;font-size:10px;font-family:monospace;font-weight:700;';
  document.getElementById('acc-info-name').innerHTML = `
    <div>
      <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;margin-bottom:2px;">Net / Label Name</div>
      <input value="${esc(label.name||'')}" onchange="accLabelUpdate('${lid}','name',this.value)" style="${inputStyle}">
    </div>`;
  document.getElementById('acc-info-desc').textContent = 'Net label';
  document.getElementById('acc-info-pins').innerHTML = `<div style="color:var(--text-muted);font-size:10px;">Edit name to rename all connected nets.</div>`;
}

function updateAccWireInfoPanel(wire) {
  const emptyEl = document.getElementById('acc-info-empty');
  const contentEl = document.getElementById('acc-info-content');
  if (!wire) { emptyEl.style.display = 'flex'; contentEl.style.display = 'none'; return; }
  // Don't clobber while user is typing
  const focused = document.activeElement;
  if (focused && focused.id === 'acc-wire-net-input' && contentEl?.dataset.selId === wire.id) return;
  emptyEl.style.display = 'none'; contentEl.style.display = 'flex';
  contentEl.dataset.selId = wire.id; contentEl.dataset.selType = 'wire';

  const infoSvg = document.getElementById('acc-info-canvas');
  if (infoSvg) {
    infoSvg.setAttribute('viewBox', '0 0 183 110');
    infoSvg.innerHTML = `<rect width="183" height="110" fill="#0a0c12"/>
      <line x1="18" y1="55" x2="165" y2="55" stroke="#818cf8" stroke-width="2.5" stroke-linecap="round"/>
      <circle cx="18" cy="55" r="4" fill="#818cf8"/><circle cx="165" cy="55" r="4" fill="#818cf8"/>
      <text x="91" y="51" text-anchor="middle" font-family="monospace" font-size="9" fill="#4b5563">wire</text>`;
  }

  // Read net name directly from project.labels (always live, no stale cache)
  const netName = _getWireNetName(appCircuitEditor, wire.id);
  const wireId = wire.id;
  const inputStyle = 'width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--accent);padding:3px 6px;font-size:11px;font-family:monospace;font-weight:700;box-sizing:border-box;';
  document.getElementById('acc-info-name').innerHTML = `
    <div>
      <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;margin-bottom:4px;">Net Name</div>
      <input id="acc-wire-net-input" value="${esc(netName)}" placeholder="Unnamed net…"
        style="${inputStyle}"
        onkeydown="if(event.key==='Enter')this.blur();"
        onblur="var _v=this.value.trim();if(_v!=='${esc(netName)}')setAccWireNetName('${esc(wireId)}',_v);">
      <div style="font-size:9px;color:var(--text-muted);margin-top:3px;">Enter to apply · spreads to all connected wires</div>
    </div>`;
  document.getElementById('acc-info-desc').textContent = `${wire.points?.length || 0}-point wire`;

  // Show connected pins — computed locally (no API needed) so it's always current
  const typeColor = { power:'#fca5a5', gnd:'#94a3b8', input:'#86efac', output:'#93c5fd' };
  let html = '';
  try {
    const connPins = _getConnectedPins(appCircuitEditor, wire.id);
    if (connPins.length) {
      html += `<div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;margin-bottom:4px;">Connected Pins</div>`;
      for (const { comp, portName } of connPins) {
        const profile = comp.slug ? (profileCache[comp.slug] || library[comp.slug]) : null;
        const pin = profile?.pins?.find(pi => pi.name === portName);
        html += `<div style="display:flex;gap:4px;align-items:center;padding:3px 0;border-bottom:1px solid rgba(46,50,80,0.4);">
          <span style="font-family:monospace;font-weight:700;color:var(--accent);font-size:10px;min-width:24px;">${esc(comp.designator||'')}</span>
          <span style="font-family:monospace;color:${typeColor[pin?.type]||'#fcd34d'};flex:1;font-size:10px;">${esc(portName||'')}</span>
          ${pin?.type ? `<span style="font-size:8px;color:${typeColor[pin.type]};background:rgba(0,0,0,0.3);border-radius:3px;padding:1px 3px;">${pin.type}</span>` : ''}
        </div>`;
      }
    }
  } catch(e) {}
  if (!html) html = `<div style="color:var(--text-muted);font-size:10px;padding:4px 0;">No pins connected yet.</div>`;
  document.getElementById('acc-info-pins').innerHTML = html;
}

function accBomPlace(slug, symType, value) {
  const doPlace = () => {
    const ed = appCircuitEditor;
    if (!ed) return;
    // Always use fetchProfile so active version value is in profileCache; ignore baked-in value
    fetchProfile(slug).then(p => {
      profileCache[slug] = p;
      ed.placeValue = null; // let _place() use profileCache active version value
      ed.startPlace(slug, symType);
    }).catch(() => {
      ed.placeValue = value || null;
      ed.startPlace(slug, symType);
    });
  };

  if (currentProfileTab !== 'schematic') {
    const btn = document.getElementById('tab-btn-schematic');
    if (btn) switchProfileTab('schematic', btn);
    const waitForEditor = (tries) => {
      if (appCircuitEditor) { doPlace(); return; }
      if (tries > 0) requestAnimationFrame(() => waitForEditor(tries - 1));
    };
    requestAnimationFrame(() => waitForEditor(30));
  } else if (!appCircuitEditor) {
    fetchProfile(selectedSlug).then(p => {
      profileCache[selectedSlug] = p;
      renderAppCircuitWithEditor(p);
      requestAnimationFrame(doPlace);
    });
  } else {
    doPlace();
  }
}

function gotoDatasheet() {
  if (!_infoSlug) return;
  selectedSlug = _infoSlug;
  switchSection('library');
  loadProfile(_infoSlug);
  document.querySelectorAll('.part-item').forEach(el => {
    el.classList.toggle('active', el.dataset.slug === _infoSlug);
  });
}

function updateSchWireInfoPanel(wire) {
  const proj = document.getElementById('sch-info-project');
  const content = document.getElementById('sch-info-content');
  if (!wire) { _showSchProjectPanel(); return; }
  // Don't clobber panel while user is typing in the net input for this wire
  const focused = document.activeElement;
  if (focused && focused.id === 'wire-net-input' && content?.dataset.selId === wire.id) return;
  content.dataset.selId = wire.id; content.dataset.selType = 'wire';
  _schInfoTitle('Wire');
  if (proj) proj.style.display = 'none'; content.style.display = 'flex';
  _infoSlug = null;

  // Draw a tiny wire icon
  const infoSvg = document.getElementById('sch-info-canvas');
  if (infoSvg) {
    infoSvg.setAttribute('viewBox', '0 0 200 160');
    infoSvg.innerHTML = `<rect width="200" height="160" fill="#0a0c12"/>
      <line x1="20" y1="80" x2="180" y2="80" stroke="#818cf8" stroke-width="2.5" stroke-linecap="round"/>
      <circle cx="20" cy="80" r="5" fill="#818cf8"/><circle cx="180" cy="80" r="5" fill="#818cf8"/>
      <text x="100" y="76" text-anchor="middle" font-family="monospace" font-size="9" fill="#4b5563">wire</text>`;
  }

  // Read net name directly from project.labels (always live, no stale cache)
  const netName = _getWireNetName(editor, wire.id);

  const wireId = wire.id;
  const inputStyle = 'width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--accent);padding:4px 8px;font-size:12px;font-family:monospace;font-weight:700;box-sizing:border-box;';
  document.getElementById('sch-info-name').innerHTML = `
    <div style="display:flex;flex-direction:column;gap:5px;">
      <div>
        <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Net Name</div>
        <input id="wire-net-input" value="${esc(netName)}" placeholder="Unnamed net…"
          style="${inputStyle}"
          onkeydown="if(event.key==='Enter')this.blur();"
          onblur="var _v=this.value.trim();if(_v!=='${esc(netName)}')setWireNetName('${esc(wireId)}',_v);">
        <div style="font-size:9px;color:var(--text-muted);margin-top:3px;">Enter to apply · spreads to all connected wires</div>
      </div>
    </div>`;
  document.getElementById('sch-info-desc').textContent = `${wire.points?.length || 0} points`;
  document.getElementById('sch-info-goto-btn').style.display = 'none';

  // Show connected pins — computed locally (no API needed) so it's always current
  const typeColor = { power:'#fca5a5', gnd:'#94a3b8', input:'#86efac', output:'#93c5fd' };
  let html = '';
  try {
    const connPins = _getConnectedPins(editor, wireId);
    if (connPins.length) {
      html += `<div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;margin-bottom:4px;">Connected Pins</div>`;
      for (const { comp, portName } of connPins) {
        const profile = comp.slug ? (profileCache[comp.slug] || library[comp.slug]) : null;
        const pin = profile?.pins?.find(pi => pi.name === portName);
        html += `<div style="display:flex;gap:4px;align-items:center;padding:3px 0;border-bottom:1px solid rgba(46,50,80,0.4);">
          <span style="font-family:monospace;font-weight:700;color:var(--accent);font-size:10px;min-width:28px;">${esc(comp.designator||'')}</span>
          <span style="font-family:monospace;font-weight:700;color:${typeColor[pin?.type]||'#fcd34d'};flex:1;font-size:10px;">${esc(portName||'')}</span>
          ${pin?.type ? `<span style="font-size:8px;color:${typeColor[pin.type]};background:rgba(0,0,0,0.3);border-radius:3px;padding:1px 3px;">${pin.type}</span>` : ''}
        </div>`;
      }
    }
  } catch(e) {}
  if (!html) html = `<div style="color:var(--text-muted);font-size:10px;padding:4px 0;">No pins connected yet.</div>`;
  document.getElementById('sch-info-pins').innerHTML = html;
}

function updateSchLabelInfoPanel(label) {
  const proj = document.getElementById('sch-info-project');
  const content = document.getElementById('sch-info-content');
  if (!label) { _showSchProjectPanel(); return; }
  // Only skip re-render if showing THIS SAME label and user is typing in it
  const focused = document.activeElement;
  if (focused && content?.contains(focused) && content.dataset.selId === label.id) return;
  content.dataset.selId = label.id; content.dataset.selType = 'label';
  _schInfoTitle('Net Label');
  if (proj) proj.style.display = 'none'; content.style.display = 'flex';
  _infoSlug = null;

  // Draw net label icon
  const infoSvg = document.getElementById('sch-info-canvas');
  if (infoSvg) {
    const name = esc(label.name || '');
    const nc = `hsl(${([...label.name||''].reduce((h,c)=>(h*31+c.charCodeAt(0))&0xffff,0)*137+30)%360},65%,55%)`;
    infoSvg.setAttribute('viewBox', '0 0 200 160');
    infoSvg.innerHTML = `<rect width="200" height="160" fill="#0a0c12"/>
      <line x1="50" y1="80" x2="94" y2="80" stroke="${nc}" stroke-width="2"/>
      <circle cx="96" cy="80" r="5" fill="${nc}"/>
      <text x="106" y="84" font-family="monospace" font-size="13" fill="${nc}" font-weight="bold">${name}</text>`;
  }

  // Net connectivity
  let nets = [];
  try { ({ nets } = computeNetOverlay(editor)); } catch(e) {}
  const net = nets.find(n => n.name === label.name);

  const lid = esc(label.id);
  const inputStyle = 'width:100%;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:3px 7px;font-size:11px;font-family:monospace;';
  // Net name = label name (they are the same value)
  document.getElementById('sch-info-name').innerHTML = `
    <div style="display:flex;flex-direction:column;gap:5px;">
      <div>
        <div style="font-size:9px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;">Net / Label Name</div>
        <input value="${esc(label.name||'')}" onchange="schLabelUpdate('${lid}','name',this.value)" style="${inputStyle}color:var(--accent);font-weight:700;">
      </div>
    </div>`;

  const sameLabels = (editor.project.labels||[]).filter(l => l.name === label.name);
  document.getElementById('sch-info-desc').textContent = net
    ? `${net.ports.filter(p => !p.nodeId?.startsWith('wire::')).length} pin connection${net.ports.filter(p=>!p.nodeId?.startsWith('wire::')).length!==1?'s':''}` : 'No connections';
  document.getElementById('sch-info-goto-btn').style.display = 'none';

  const pinsEl = document.getElementById('sch-info-pins');
  const typeColor = { power:'#fca5a5', gnd:'#94a3b8', input:'#86efac', output:'#93c5fd' };

  let html = '';
  if (sameLabels.length > 1) {
    html += `<div style="font-size:10px;color:#c4b5fd;background:rgba(108,99,255,0.12);border-radius:4px;padding:4px 6px;margin-bottom:6px;">${sameLabels.length} labels named "${esc(label.name)}" — all connected</div>`;
  }
  if (net) {
    const pinPorts = net.ports.filter(p => !p.nodeId?.startsWith('wire::'));
    if (pinPorts.length) {
      for (const port of pinPorts) {
        const [compId, pinName] = (port.nodeId||'').split('::');
        const comp = editor.project.components.find(c => c.id === compId);
        if (!comp) continue;
        const profile = comp.slug ? (profileCache[comp.slug] || library[comp.slug]) : null;
        const pin = profile?.pins?.find(pi => pi.name === pinName);
        html += `<div style="display:flex;gap:4px;align-items:center;padding:3px 0;border-bottom:1px solid rgba(46,50,80,0.4);">
          <span style="font-family:monospace;font-weight:700;color:var(--accent);font-size:10px;min-width:28px;">${esc(comp.designator||'')}</span>
          <span style="font-family:monospace;font-weight:700;color:${typeColor[pin?.type]||'#fcd34d'};flex:1;font-size:10px;">${esc(pinName||'')}</span>
          ${pin?.type ? `<span style="font-size:8px;color:${typeColor[pin.type]};background:rgba(0,0,0,0.3);border-radius:3px;padding:1px 3px;">${pin.type}</span>` : ''}
        </div>`;
      }
    } else {
      html += `<div style="color:var(--text-muted);font-size:10px;padding:4px 0;">No component pins connected yet.</div>`;
    }
  } else {
    html += `<div style="color:var(--text-muted);font-size:10px;padding:4px 0;">No connections found.</div>`;
  }
  pinsEl.innerHTML = html;
}

function schLabelUpdate(labelId, field, value) {
  const lbl = (editor.project.labels||[]).find(l => l.id === labelId);
  if (!lbl) return;
  lbl[field] = value;
  editor.dirty = true;
  editor._saveHist();
  editor._render();
}

// ── Wire net name helpers ──────────────────────────────────────────────────
// Read net name directly from project.labels (bypasses stale API cache).
// Checks labels at wire endpoints first, then falls back to the cached overlay
// for wires that inherit a name transitively through connected wires/junctions.
// Build endpoint→[wireId] adjacency map for wire graph traversal
function _buildWireEpMap(project) {
  const epMap = new Map();
  for (const w of (project.wires || [])) {
    if (!w.points?.length) continue;
    for (const pt of [w.points[0], w.points[w.points.length - 1]]) {
      const k = `${pt.x},${pt.y}`;
      if (!epMap.has(k)) epMap.set(k, []);
      epMap.get(k).push(w.id);
    }
  }
  return epMap;
}

// Find the net name for a wire by BFS-traversing the connected wire graph.
// Checks wire.net property first, then labels at endpoints, then cache fallback.
function _getWireNetName(editorRef, wireId) {
  const project = editorRef.project;
  const lblMap = new Map();
  for (const lbl of (project.labels || [])) lblMap.set(`${lbl.x},${lbl.y}`, lbl.name);

  const epMap = _buildWireEpMap(project);
  const visited = new Set([wireId]);
  const queue = [wireId];
  while (queue.length) {
    const wid = queue.shift();
    const w = (project.wires || []).find(ww => ww.id === wid);
    if (!w?.points?.length) continue;
    // wire.net (set by the net name input) takes highest priority
    if (w.net) return w.net;
    for (const pt of [w.points[0], w.points[w.points.length - 1]]) {
      const k = `${pt.x},${pt.y}`;
      if (lblMap.has(k)) return lblMap.get(k);
      for (const nid of (epMap.get(k) || [])) {
        if (!visited.has(nid)) { visited.add(nid); queue.push(nid); }
      }
    }
  }
  // Fallback: cache (handles names assigned via component power pins, etc.)
  try { const n = computeNetOverlay(editorRef).wireToNet.get(wireId); if (n) return n; } catch(_) {}
  return '';
}

// Return component pins (array of {comp, portName, portX, portY}) connected to any
// wire in the group reachable from wireId. Uses coordinate matching — no API needed.
function _getConnectedPins(editorRef, wireId) {
  const connectedIds = _getConnectedWireIds(editorRef.project, wireId);
  // Collect all points of connected wires
  const ptSet = new Set();
  for (const wid of connectedIds) {
    const w = (editorRef.project.wires || []).find(ww => ww.id === wid);
    if (!w?.points?.length) continue;
    for (const pt of w.points) ptSet.add(`${pt.x},${pt.y}`);
  }
  const result = [];
  for (const comp of (editorRef.project.components || [])) {
    const ports = editorRef._ports(comp);
    for (const port of ports) {
      if (ptSet.has(`${port.x},${port.y}`)) {
        result.push({ comp, portName: port.name });
      }
    }
  }
  return result;
}

// Return all wire IDs reachable from wireId through shared endpoints.
function _getConnectedWireIds(project, wireId) {
  const epMap = _buildWireEpMap(project);
  const visited = new Set([wireId]);
  const queue = [wireId];
  while (queue.length) {
    const wid = queue.shift();
    const w = (project.wires || []).find(ww => ww.id === wid);
    if (!w?.points?.length) continue;
    for (const pt of [w.points[0], w.points[w.points.length - 1]]) {
      for (const nid of (epMap.get(`${pt.x},${pt.y}`) || [])) {
        if (!visited.has(nid)) { visited.add(nid); queue.push(nid); }
      }
    }
  }
  return visited;
}

// Apply new net name to a wire, spreading across the entire connected net.
function _applyWireNetName(editorRef, wireId, newName) {
  if (!editorRef.project.labels) editorRef.project.labels = [];
  const oldName = _getWireNetName(editorRef, wireId);
  if (newName === oldName) return;

  editorRef._saveHist();

  // Store net name directly on wire objects (no visible label placed on canvas).
  // _refreshNetOverlay sends these as virtual labels so the API can net them.
  const connectedIds = _getConnectedWireIds(editorRef.project, wireId);
  for (const wid of connectedIds) {
    const w = (editorRef.project.wires || []).find(ww => ww.id === wid);
    if (!w) continue;
    if (newName) w.net = newName;
    else delete w.net;
  }

  // Also rename/remove any explicit labels placed manually via the label tool
  if (oldName && editorRef.project.labels.some(l => l.name === oldName)) {
    if (newName) {
      editorRef.project.labels.forEach(l => { if (l.name === oldName) l.name = newName; });
    } else {
      editorRef.project.labels = editorRef.project.labels.filter(l => l.name !== oldName);
    }
  }

  editorRef.dirty = true;
  editorRef._cachedNetOverlay = null;
  editorRef._render();
  editorRef._refreshNetOverlay();
  // If the edited editor is the Schematic Example, re-check Layout Example net sync
  if (window.appCircuitEditor && editorRef === window.appCircuitEditor) {
    if (typeof leCheckNetsLive === 'function') leCheckNetsLive();
  }
}

function setWireNetName(wireId, rawName) {
  _applyWireNetName(editor, wireId, (rawName || '').trim());
}

function setAccWireNetName(wireId, rawName) {
  if (appCircuitEditor) _applyWireNetName(appCircuitEditor, wireId, (rawName || '').trim());
}

