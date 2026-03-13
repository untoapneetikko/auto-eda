// ── FootprintEditor — SVG-based interactive pad viewer/editor ──────────────
class FootprintEditor {
  constructor(svgEl) {
    this.svg = svgEl;
    this.zoom = 20;
    this.panX = 0; this.panY = 0;
    this.data = null;
    this.selected = -1;
    this.dragState = null;
    this.panState = null;
    this.onSelect = null; // callback(padIdx)
    this.onChange = null; // callback(padIdx)
    // ── Layer management (mirrors PCB editor layers) ──
    this.layers = {
      'Courtyard': { color: '#ffee00', visible: true,  displayName: 'Courtyard' },
      'F.Fab':     { color: '#888866', visible: false, displayName: 'Fab / Outline' },
      'F.Paste':   { color: '#aaaaaa', visible: true,  displayName: 'Solder Paste' },
      'F.Mask':    { color: '#1a7a2a', visible: true,  displayName: 'Solder Mask' },
      'F.Cu':      { color: '#cc6633', visible: true,  displayName: 'Top Copper' },
      'F.SilkS':   { color: '#cccccc', visible: true,  displayName: 'Silkscreen' },
    };
    // ── Paste / mask parametrisation ──
    this.maskExpansion = 0.10; // mm expansion beyond pad edge (each side)
    this.pasteGap      = 0.05; // mm reduction from pad edge (each side)
    // ── Active work layer (the layer currently being edited) ──
    this.workLayer = 'F.Cu';
    this._bind();
  }

  load(data) {
    this.data = data;
    this.selected = -1;
    this._fit();
    this._render();
  }

  _size() {
    const r = this.svg.getBoundingClientRect();
    return { w: r.width || 320, h: r.height || 260 };
  }

  _toS(wx, wy) {
    const { w, h } = this._size();
    return { x: wx * this.zoom + w/2 + this.panX, y: wy * this.zoom + h/2 + this.panY };
  }

  _toW(sx, sy) {
    const { w, h } = this._size();
    return { x: (sx - w/2 - this.panX) / this.zoom, y: (sy - h/2 - this.panY) / this.zoom };
  }

  _autoCY() {
    const pads = this.data?.pads;
    if (!pads?.length) return { x: -5, y: -5, w: 10, h: 10 };
    const xs = pads.flatMap(p => [p.x - (p.size_x||1)/2, p.x + (p.size_x||1)/2]);
    const ys = pads.flatMap(p => [p.y - (p.size_y||1)/2, p.y + (p.size_y||1)/2]);
    const minX = Math.min(...xs)-0.5, maxX = Math.max(...xs)+0.5;
    const minY = Math.min(...ys)-0.5, maxY = Math.max(...ys)+0.5;
    return { x: minX, y: minY, w: maxX-minX, h: maxY-minY };
  }

  _fit() {
    if (!this.data?.pads?.length) { this.panX = 0; this.panY = 0; this.zoom = 20; return; }
    const { w, h } = this._size();
    const cy = this.data.courtyard || this._autoCY();
    const pad = 36;
    this.zoom = Math.max(4, Math.min(80, Math.min((w-pad*2)/(cy.w||10), (h-pad*2)/(cy.h||10))));
    this.panX = 0; this.panY = 0;
  }

  _bind() {
    this.svg.addEventListener('wheel', e => {
      e.preventDefault();
      const f = e.deltaY < 0 ? 1.15 : 0.87;
      const r = this.svg.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const { w, h } = this._size();
      const wx = (mx - w/2 - this.panX) / this.zoom, wy = (my - h/2 - this.panY) / this.zoom;
      this.zoom = Math.max(4, Math.min(200, this.zoom * f));
      this.panX = mx - w/2 - wx * this.zoom;
      this.panY = my - h/2 - wy * this.zoom;
      this._render();
    }, { passive: false });

    this.svg.addEventListener('mousedown', e => {
      if (e.button === 1 || (e.button === 0 && e.ctrlKey)) {
        this.panState = { sx: e.clientX, sy: e.clientY, px: this.panX, py: this.panY };
        e.preventDefault(); return;
      }
      if (e.button !== 0) return;
      const r = this.svg.getBoundingClientRect();
      const sx = e.clientX - r.left, sy = e.clientY - r.top;
      const w = this._toW(sx, sy);
      let hit = -1;
      if (this.data?.pads) {
        for (let i = this.data.pads.length - 1; i >= 0; i--) {
          const pad = this.data.pads[i];
          const rx = (pad.size_x||1)/2 + 0.4/this.zoom*10, ry = (pad.size_y||1)/2 + 0.4/this.zoom*10;
          if (Math.abs(w.x - pad.x) <= rx && Math.abs(w.y - pad.y) <= ry) { hit = i; break; }
        }
      }
      this.selected = hit;
      if (hit >= 0) {
        const pad = this.data.pads[hit];
        this.dragState = { padIdx: hit, startSx: sx, startSy: sy, origX: pad.x, origY: pad.y };
      }
      this._render();
      if (this.onSelect) this.onSelect(this.selected);
    });

    this.svg.addEventListener('mousemove', e => {
      if (this.panState) {
        this.panX = this.panState.px + (e.clientX - this.panState.sx);
        this.panY = this.panState.py + (e.clientY - this.panState.sy);
        this._render(); return;
      }
      if (this.dragState) {
        const r = this.svg.getBoundingClientRect();
        const sx = e.clientX - r.left, sy = e.clientY - r.top;
        const ddx = (sx - this.dragState.startSx) / this.zoom;
        const ddy = (sy - this.dragState.startSy) / this.zoom;
        const pad = this.data.pads[this.dragState.padIdx];
        pad.x = Math.round((this.dragState.origX + ddx) * 100) / 100;
        pad.y = Math.round((this.dragState.origY + ddy) * 100) / 100;
        this._render();
        if (this.onChange) this.onChange(this.dragState.padIdx);
      }
    });

    const end = () => { this.panState = null; this.dragState = null; };
    this.svg.addEventListener('mouseup', end);
    this.svg.addEventListener('mouseleave', () => { this.panState = null; });
  }

  // ── Per-layer pad shape renderer ─────────────────────────────────────────
  // Returns SVG markup for `pad` on the given layer string.
  _padLayer(pad, i, layer) {
    const s = this._toS(pad.x, pad.y);
    const lyr = this.layers[layer];
    if (!lyr) return '';

    if (layer === 'F.Mask') {
      // Expanded shape around every pad (solder mask opening)
      const exp = this.maskExpansion * this.zoom;
      const col = lyr.color;
      if (pad.type === 'thru_hole') {
        const r = Math.max(pad.size_x||1, pad.size_y||1)/2 * this.zoom + exp;
        return `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${r.toFixed(1)}" fill="${col}55"/>`;
      }
      const pw = (pad.size_x||1)*this.zoom + 2*exp;
      const ph = (pad.size_y||1)*this.zoom + 2*exp;
      if (pad.shape === 'circle') {
        return `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${(Math.min(pw,ph)/2).toFixed(1)}" fill="${col}55"/>`;
      }
      // Rounded rect for SMD mask openings (KiCad style)
      const rx = Math.min(3, pw*0.15);
      return `<rect x="${(s.x-pw/2).toFixed(1)}" y="${(s.y-ph/2).toFixed(1)}" width="${pw.toFixed(1)}" height="${ph.toFixed(1)}" rx="${rx.toFixed(1)}" fill="${col}55"/>`;
    }

    if (layer === 'F.Paste') {
      // Only SMD pads get solder paste
      if (pad.type === 'thru_hole') return '';
      const red = this.pasteGap * this.zoom;
      const pw = Math.max(2, (pad.size_x||1)*this.zoom - 2*red);
      const ph = Math.max(2, (pad.size_y||1)*this.zoom - 2*red);
      const col = lyr.color;
      if (pad.shape === 'circle') {
        return `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${(Math.min(pw,ph)/2).toFixed(1)}" fill="${col}99"/>`;
      }
      return `<rect x="${(s.x-pw/2).toFixed(1)}" y="${(s.y-ph/2).toFixed(1)}" width="${pw.toFixed(1)}" height="${ph.toFixed(1)}" fill="${col}99"/>`;
    }

    return '';
  }

  _padH(pad, i) {
    const s = this._toS(pad.x, pad.y);
    const pw = (pad.size_x||1)*this.zoom, ph = (pad.size_y||1)*this.zoom;
    const isSel = i === this.selected, isThru = pad.type === 'thru_hole';
    const lsz = Math.max(7, Math.min(11, this.zoom * 0.45));
    let h = '';
    if (isThru) {
      const r = Math.max(pw,ph)/2, dr = ((pad.drill||0.8)*this.zoom)/2;
      const fill = isSel ? '#ffcc44' : '#d4a017';
      h += `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${r.toFixed(1)}" fill="${fill}"/>`;
      h += `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${dr.toFixed(1)}" fill="#0d0f18"/>`;
      if (isSel) h += `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${(r+3).toFixed(1)}" fill="none" stroke="white" stroke-width="1.5"/>`;
    } else {
      const fill = isSel ? '#88aaff' : (i === 0 ? '#7766ee' : '#3a7bd5');
      if (pad.shape === 'circle') {
        h += `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="${(Math.min(pw,ph)/2).toFixed(1)}" fill="${fill}"/>`;
      } else {
        h += `<rect x="${(s.x-pw/2).toFixed(1)}" y="${(s.y-ph/2).toFixed(1)}" width="${pw.toFixed(1)}" height="${ph.toFixed(1)}" fill="${fill}"/>`;
      }
      if (isSel) h += `<rect x="${(s.x-pw/2-3).toFixed(1)}" y="${(s.y-ph/2-3).toFixed(1)}" width="${(pw+6).toFixed(1)}" height="${(ph+6).toFixed(1)}" fill="none" stroke="white" stroke-width="1.5"/>`;
    }
    h += `<text x="${s.x.toFixed(1)}" y="${s.y.toFixed(1)}" text-anchor="middle" dominant-baseline="central" font-family="monospace" font-weight="bold" font-size="${lsz}" fill="white">${pad.number||String(i+1)}</text>`;
    return h;
  }

  _render() {
    const { w, h } = this._size();
    this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    let out = `<rect width="${w}" height="${h}" fill="#0d0f18"/>`;
    // Grid
    const gmm = this.zoom < 15 ? 2 : 1;
    const gpx = gmm * this.zoom;
    const ox = ((w/2 + this.panX) % gpx + gpx) % gpx;
    const oy = ((h/2 + this.panY) % gpx + gpx) % gpx;
    for (let gx = ox; gx < w; gx += gpx)
      for (let gy = oy; gy < h; gy += gpx)
        out += `<circle cx="${gx.toFixed(0)}" cy="${gy.toFixed(0)}" r="0.7" fill="rgba(255,255,255,0.07)"/>`;
    if (!this.data?.pads?.length) {
      out += `<text x="${w/2}" y="${h/2}" text-anchor="middle" dominant-baseline="central" font-family="system-ui" font-size="13" fill="rgba(255,255,255,0.3)">No footprint loaded</text>`;
      this.svg.innerHTML = out; return;
    }
    // Crosshair
    const o = this._toS(0,0);
    out += `<line x1="${o.x-12}" y1="${o.y}" x2="${o.x+12}" y2="${o.y}" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>`;
    out += `<line x1="${o.x}" y1="${o.y-12}" x2="${o.x}" y2="${o.y+12}" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>`;

    const cy = this.data.courtyard || this._autoCY();
    const cs = this._toS(cy.x, cy.y);
    // Active layer = full opacity; all others dimmed so the work layer stands out clearly
    const lop = name => (this.workLayer === name ? 1.0 : 0.35);

    // ── Layer: Courtyard ──────────────────────────────────────────────────
    if (this.layers['Courtyard']?.visible) {
      const cCol = this.layers['Courtyard'].color;
      out += `<g opacity="${lop('Courtyard')}"><rect x="${cs.x.toFixed(1)}" y="${cs.y.toFixed(1)}" width="${(cy.w*this.zoom).toFixed(1)}" height="${(cy.h*this.zoom).toFixed(1)}" fill="none" stroke="${cCol}55" stroke-width="1" stroke-dasharray="4,3"/></g>`;
    }

    // ── Layer: F.Fab (component outline — solid courtyard) ────────────────
    if (this.layers['F.Fab']?.visible) {
      const fCol = this.layers['F.Fab'].color;
      out += `<g opacity="${lop('F.Fab')}"><rect x="${cs.x.toFixed(1)}" y="${cs.y.toFixed(1)}" width="${(cy.w*this.zoom).toFixed(1)}" height="${(cy.h*this.zoom).toFixed(1)}" fill="none" stroke="${fCol}66" stroke-width="0.8"/></g>`;
    }

    // ── Layer: F.Cu (copper pads) ─────────────────────────────────────────
    if (this.layers['F.Cu']?.visible) {
      out += `<g opacity="${lop('F.Cu')}">`;
      for (let i = 0; i < this.data.pads.length; i++) out += this._padH(this.data.pads[i], i);
      out += '</g>';
    }

    // ── Layer: F.Mask (solder mask opening — expanded, on top of copper) ───
    if (this.layers['F.Mask']?.visible) {
      out += `<g opacity="${lop('F.Mask')}">`;
      for (let i = 0; i < this.data.pads.length; i++)
        out += this._padLayer(this.data.pads[i], i, 'F.Mask');
      out += '</g>';
    }

    // ── Layer: F.Paste (solder paste — reduced, topmost overlay) ───────────
    if (this.layers['F.Paste']?.visible) {
      out += `<g opacity="${lop('F.Paste')}">`;
      for (let i = 0; i < this.data.pads.length; i++)
        out += this._padLayer(this.data.pads[i], i, 'F.Paste');
      out += '</g>';
    }

    // ── Layer: F.SilkS (silkscreen — pin 1 marker) ────────────────────────
    if (this.layers['F.SilkS']?.visible) {
      const p1 = this.data.pads[0], p1s = this._toS(p1.x, p1.y);
      const ay = p1s.y - (p1.size_y||1)*this.zoom/2 - 9;
      const silkCol = this.layers['F.SilkS'].color;
      out += `<g opacity="${lop('F.SilkS')}"><polygon points="${p1s.x-5},${ay-7} ${p1s.x+5},${ay-7} ${p1s.x},${ay-1}" fill="${silkCol}cc"/></g>`;
    }

    // Dims (always shown)
    const xs = this.data.pads.flatMap(p => [p.x-(p.size_x||1)/2, p.x+(p.size_x||1)/2]);
    const ys = this.data.pads.flatMap(p => [p.y-(p.size_y||1)/2, p.y+(p.size_y||1)/2]);
    const sx = (Math.max(...xs)-Math.min(...xs)).toFixed(2), sy2 = (Math.max(...ys)-Math.min(...ys)).toFixed(2);
    out += `<text x="6" y="${h-6}" font-family="system-ui" font-size="9" fill="rgba(255,255,255,0.4)">${sx} × ${sy2} mm · ${this.data.pads.length} pads · mask+${(this.maskExpansion||0.1).toFixed(2)} paste-${(this.pasteGap||0.05).toFixed(2)}</text>`;
    this.svg.innerHTML = out;
  }
}

// ── Footprint Layer Panel ────────────────────────────────────────────────────
// Mirrors pcb-panels.js buildLayerPanel() but targets #fp-layer-list
function buildFPLayerPanel() {
  const ul = document.getElementById('fp-layer-list');
  if (!ul || !fpEditor) return;
  ul.innerHTML = '';
  const wl = fpEditor.workLayer;
  for (const [name, lyr] of Object.entries(fpEditor.layers)) {
    const isWork = name === wl;
    const d = document.createElement('div');
    d.style.cssText = 'display:flex;align-items:center;gap:5px;padding:3px 4px;border-radius:3px;user-select:none;font-size:11px;cursor:pointer;' +
      (isWork ? `background:${lyr.color}22;outline:1px solid ${lyr.color}55;` : '') +
      (lyr.visible ? '' : 'opacity:0.45;');

    // Color dot with inline color picker
    const dot = document.createElement('div');
    dot.style.cssText = `width:12px;height:12px;border-radius:2px;flex-shrink:0;position:relative;cursor:pointer;background:${lyr.color};`;
    const cp = document.createElement('input');
    cp.type = 'color'; cp.value = lyr.color;
    cp.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;padding:0;border:none;';
    cp.oninput = e => { e.stopPropagation(); lyr.color = cp.value; dot.style.background = lyr.color; if (fpEditor) fpEditor._render(); buildFPLayerPanel(); };
    cp.onclick = e => e.stopPropagation();
    dot.appendChild(cp);

    // Star — active work layer indicator (mirrors PCB editor)
    const star = document.createElement('span');
    star.textContent = isWork ? '★' : '';
    star.style.cssText = `font-size:9px;color:${lyr.color};flex-shrink:0;width:9px;`;

    // Layer name
    const nm = document.createElement('span');
    nm.textContent = lyr.displayName || name;
    nm.style.cssText = `flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:${isWork ? lyr.color : 'var(--text)'};font-weight:${isWork ? '700' : '400'};`;

    // Eye toggle
    const eye = document.createElement('span');
    eye.textContent = lyr.visible ? '👁' : '○';
    eye.style.cssText = 'font-size:11px;color:var(--text-muted);cursor:pointer;flex-shrink:0;';
    eye.title = 'Toggle visibility';
    eye.onclick = e => {
      e.stopPropagation();
      lyr.visible = !lyr.visible;
      buildFPLayerPanel();
      if (fpEditor) fpEditor._render();
    };

    // Row click → set as work layer
    d.onclick = () => {
      if (nm.contentEditable === 'true') return;
      fpEditor.workLayer = name;
      buildFPLayerPanel();
      fpEditor._render();
    };

    d.appendChild(dot); d.appendChild(star); d.appendChild(nm); d.appendChild(eye);
    ul.appendChild(d);
  }
}

// ── Footprint Tool ──────────────────────────────────────────────────────────
let _fpData = null;       // current footprint object {name,description,pads,courtyard}
let _fpSlug = null;       // profile slug this footprint belongs to
let _fpSelectedPad = -1;  // index of selected pad
let fpEditor = null;

async function initFootprintTab(slug) {
  _fpSlug = slug;
  _fpSelectedPad = -1;
  // Create or reuse fpEditor
  const svgEl = document.getElementById('fp-svg');
  if (svgEl && (!fpEditor || fpEditor.svg !== svgEl)) {
    fpEditor = new FootprintEditor(svgEl);
    fpEditor.onSelect = (idx) => { _fpSelectedPad = idx; fpRenderPadList(); };
    fpEditor.onChange = (idx) => { fpSyncPadToTable(idx); };
  }
  // Sync paste/mask inputs with current editor values
  const maskInp = document.getElementById('fp-mask-exp');
  if (maskInp && fpEditor) maskInp.value = fpEditor.maskExpansion.toFixed(2);
  const pasteInp = document.getElementById('fp-paste-gap');
  if (pasteInp && fpEditor) pasteInp.value = fpEditor.pasteGap.toFixed(2);
  buildFPLayerPanel();
  // Load footprint list into select
  const sel = document.getElementById('fp-select');
  if (!sel) return;
  const list = await fetch('/api/footprints').then(r => r.json()).catch(() => []);
  sel.innerHTML = '<option value="">— select footprint —</option>' +
    list.map(f => `<option value="${esc(f.name)}">${esc(f.name)} — ${esc(f.description||'')}</option>`).join('');
  // Load profile to see if footprint already assigned
  const profile = await fetch(`/api/library/${slug}`).then(r => r.json()).catch(() => null);
  if (!profile) return;
  const assigned = profile.footprint || '';
  if (assigned) {
    sel.value = assigned;
    await loadFootprint(assigned);
  } else {
    _fpData = null;
    if (fpEditor) fpEditor.load(null);
    fpRenderPadList();
  }
}

async function loadFootprint(name) {
  if (!name) { _fpData = null; if (fpEditor) fpEditor.load(null); fpRenderPadList(); return; }
  _fpData = await fetch(`/api/footprints/${encodeURIComponent(name)}`).then(r => r.json()).catch(() => null);
  if (_fpData) {
    const ni = document.getElementById('fp-name'); if (ni) ni.value = _fpData.name || '';
    const di = document.getElementById('fp-desc'); if (di) di.value = _fpData.description || '';
  }
  if (fpEditor) { fpEditor.data = _fpData; fpEditor._fit(); fpEditor._render(); }
  fpRenderPadList();
  buildFPLayerPanel();
}

async function onFootprintSelect(name) {
  await loadFootprint(name);
}

async function fpSaveAssignment(btn) {
  const sel = document.getElementById('fp-select');
  if (!sel || !_fpSlug) return;
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
  await fetch(`/api/library/${_fpSlug}/footprint`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ footprint: sel.value || null })
  });
  if (btn) { btn.textContent = '✓ Assigned'; setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1500); }
}

async function fpSavePads(btn) {
  if (!_fpData) return;
  const ni = document.getElementById('fp-name'); if (ni) _fpData.name = ni.value.trim();
  const di = document.getElementById('fp-desc'); if (di) _fpData.description = di.value.trim();
  const name = (_fpData.name || 'custom').replace(/[^a-zA-Z0-9_\-]/g, '_');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
  await fetch(`/api/footprints/${encodeURIComponent(name)}`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(_fpData)
  });
  if (btn) { btn.textContent = '✓ Saved'; setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1500); }
}

async function buildFootprintPrompt(slug, profile) {
  const res = await fetch('/api/gen-tickets/build-prompt', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({type:'footprint', slug, profile})
  });
  return (await res.json()).prompt;
}

// Convert _leBomData + _leNetAssign into the required_passives format used by _computeLayoutJSON.
// Returns array of { type, value, placement, designator }.
function _bomToPassives(bomData, netAssign, profileMap) {
  const IC_TYPES = new Set(['ic','amplifier','opamp']);
  const SKIP_TYPES = new Set(['vcc','gnd']);
  const passives = bomData.filter(c => !IC_TYPES.has(c.symType) && !SKIP_TYPES.has(c.symType));
  const ic = bomData.find(c => IC_TYPES.has(c.symType));

  // Build net → IC pad number map from netAssign
  const icPadByNet = {};
  if (ic) {
    const rawPins = (profileMap[ic.slug])?.pins || [];
    const sortedPins = [...rawPins].sort((a,b) => (a.number||0)-(b.number||0));
    sortedPins.forEach((pin, pi) => {
      const net = netAssign[ic.id + '.p' + pi];
      if (net && !icPadByNet[net]) icPadByNet[net] = String(pin.number);
    });
  }

  function awayPrefix(net) {
    if (!net) return '';
    if (/vdd|vcc/i.test(net)) return 'vdd supply ';
    if (/rf.*out|dc.*in/i.test(net)) return 'rf out trace ';
    if (/rf.*src|rf.*source/i.test(net)) return 'series rf source ';
    return '';
  }

  return passives.map(c => {
    const n0 = netAssign[c.id + '.0'] || ''; // port 0 → P1
    const n1 = netAssign[c.id + '.1'] || ''; // port 1 → P2
    const icPad0 = icPadByNet[n0];
    const icPad1 = icPadByNet[n1];
    const icPad = icPad0 || icPad1 || null;
    const awayNet = icPad0 ? n1 : icPad1 ? n0 : null;
    let placement;
    if (icPad) {
      placement = awayPrefix(awayNet) + 'pad ' + icPad;
    } else if (/vdd|vcc/i.test(n0 + n1)) {
      placement = 'vdd bypass to GND';
    } else {
      placement = n0 || n1 || 'bypass';
    }
    const type = c.symType === 'capacitor_pol' ? 'capacitor' : c.symType;
    return { type, value: c.value || '', placement, designator: c.designator };
  });
}

// Pre-compute PCB layout JSON entirely in JavaScript so generated tickets only need 2 curl calls.
function _computeLayoutJSON(partNum, fpName, fpPads, passives) {
  const r4 = v => Math.round(v * 10000) / 10000;
  const CY = {
    'QFN-12-3x3': [2.0, 2.0], 'QFN-12-2x2': [1.5, 1.5],
    'QFN-16-3x3': [2.45, 2.45], 'QFN-20-4x4': [2.45, 2.45],
    'QFN-24-4x4': [2.45, 2.45], 'QFN-28-4x4': [2.45, 2.45],
    'QFN-32-5x5': [3.0, 3.0], 'QFN-40-6x6': [3.5, 3.5],
    'MCLP-4': [1.4, 1.2], 'MCLP-6': [1.4, 1.2], 'MCLP-8': [1.4, 1.1], 'MCLP-12': [1.6, 1.4],
    'DFN-6-1.8x1.8': [1.3, 1.225], 'DFN-8-2x2': [1.4, 1.325],
    'SOT-23': [1.6, 1.1], 'SOT-23-5': [1.7, 1.5], 'SOIC-8': [4.0, 2.8],
    '0201': [0.54, 0.3], '0402': [1.0, 0.5], '0603': [1.5, 0.6], '0805': [1.8, 0.9],
  };
  const icCY = CY[fpName] || [2.5, 2.5];
  const pCY = CY['0402'];
  // Top/bottom passives are placed rotation=90, so their world half-Y = pCY[0] (long axis), half-X = pCY[1]
  // Left/right passives are rotation=0, so world half-X = pCY[0], half-Y = pCY[1]
  const minDY = icCY[1] + pCY[0] + 0.25; // IC half-Y + rotated passive half-Y (pCY[0]) + gap
  const minDX = icCY[0] + pCY[0] + 0.25; // IC half-X + passive half-X (pCY[0]) + gap
  const padPos = {};
  for (const p of fpPads) padPos[String(p.number)] = p;

  function padSide(num) {
    const p = padPos[String(num)];
    if (!p) return null;
    if (Math.abs(p.y) >= Math.abs(p.x)) return p.y > 0 ? 'bottom' : 'top';
    return p.x > 0 ? 'right' : 'left';
  }
  function extractPad(placement) {
    const m = placement.match(/pad\s*(\d+)/i);
    return m ? m[1] : null;
  }

  let lCount = 0, cCount = 0, rCount = 0;
  const items = passives.map(p => {
    let ref;
    if (p.designator) { ref = p.designator; }
    else if (p.type === 'inductor') ref = `L${++lCount}`;
    else if (p.type === 'capacitor') ref = `C${++cCount}`;
    else ref = `R${++rCount}`;
    const padNum = extractPad(p.placement);
    let side = padNum ? padSide(padNum) : null;
    if (!side) {
      if (/vdd|vcc|supply|bypass/i.test(p.placement)) side = 'top';
      else if (/input|rf[\s_-]*in/i.test(p.placement)) side = 'bottom';
      else side = 'top';
    }
    return { ...p, ref, padNum, side };
  });

  const bySide = { top: [], bottom: [], left: [], right: [] };
  for (const item of items) bySide[item.side].push(item);
  const cx = 10, cy = 8;
  // For top/bottom passives (rotation=90), world X-extent is pCY[1]=0.5mm — min gap = 2*0.5+0.25=1.25mm
  // For left/right passives (rotation=0), world Y-extent is pCY[1]=0.5mm — same min gap
  const H_SPACING = r4(2 * pCY[1] + 0.25 + 0.1); // 1.35mm — just above minimum for rotated 0402
  const V_SPACING = r4(2 * pCY[1] + 0.25 + 0.1); // 1.35mm

  function placeGroup(group, side) {
    return group.map((p, i) => {
      const offset = (i - (group.length - 1) / 2);
      if (side === 'top')    return { ...p, x: r4(cx + offset * H_SPACING), y: r4(cy - minDY), rotation: 90 };
      if (side === 'bottom') return { ...p, x: r4(cx + offset * H_SPACING), y: r4(cy + minDY), rotation: 90 };
      if (side === 'left')   return { ...p, x: r4(cx - minDX), y: r4(cy + offset * V_SPACING), rotation: 0 };
      return                        { ...p, x: r4(cx + minDX), y: r4(cy + offset * V_SPACING), rotation: 0 };
    });
  }

  const placed = [
    ...placeGroup(bySide.top, 'top'),
    ...placeGroup(bySide.bottom, 'bottom'),
    ...placeGroup(bySide.left, 'left'),
    ...placeGroup(bySide.right, 'right'),
  ];

  function icNetName(num) {
    const p = padPos[String(num)];
    if (!p || !p.name || /^NC/i.test(p.name)) return 'GND';
    return p.name.replace(/[^A-Za-z0-9_]/g, '_');
  }

  const passiveComponents = placed.map(p => {
    const icNet = p.padNum ? icNetName(p.padNum) : 'GND';
    let net1 = 'GND', net2 = 'GND';
    // The pad facing toward the IC gets the IC signal net
    if      (p.side === 'top')    { net2 = icNet; }
    else if (p.side === 'bottom') { net1 = icNet; }
    else if (p.side === 'left')   { net2 = icNet; }
    else                          { net1 = icNet; }
    // The pad facing away: override GND if it's a supply or external signal
    const away = p.side === 'top' ? 'net1' : p.side === 'bottom' ? 'net2' : p.side === 'left' ? 'net1' : 'net2';
    let awayNet = 'GND';
    if (/vdd|vcc|supply/i.test(p.placement)) awayNet = 'VDD';
    else if (/rf.out.*trace|output.*trace/i.test(p.placement)) awayNet = 'RF_OUT';
    else if (/series.*rf|rf.*source|rf.*input/i.test(p.placement)) awayNet = 'RF_SRC';
    if (away === 'net1') net1 = awayNet; else net2 = awayNet;

    return {
      id: p.ref, ref: p.ref, value: p.value, footprint: '0402',
      x: p.x, y: p.y, rotation: p.rotation, layer: 'F',
      pads: [
        { number: '1', name: '1', x: -0.5, y: 0, type: 'smd', shape: 'rect', size_x: 0.6, size_y: 0.6, net: net1 },
        { number: '2', name: '2', x: 0.5, y: 0, type: 'smd', shape: 'rect', size_x: 0.6, size_y: 0.6, net: net2 },
      ]
    };
  });

  const u1Pads = fpPads.map(p => ({
    number: String(p.number), name: p.name || '',
    x: p.x, y: p.y, type: p.type || 'smd', shape: p.shape || 'rect',
    size_x: p.size_x || 0.3, size_y: p.size_y || 0.7,
    net: (() => { const n = p.name || ''; if (/float/i.test(n)) return ''; if (!n || /^NC/i.test(n)) return 'GND'; return n.replace(/[^A-Za-z0-9_]/g, '_'); })()
  }));
  const u1 = { id: 'U1', ref: 'U1', value: partNum, footprint: fpName, x: cx, y: cy, rotation: 0, layer: 'F', pads: u1Pads };

  // Build nets
  const netMap = {};
  for (const pad of u1Pads) { if (pad.net && pad.net !== '') { (netMap[pad.net] = netMap[pad.net] || []).push(`U1.${pad.number}`); } }
  for (const comp of passiveComponents) {
    for (const pad of comp.pads) { if (pad.net) { (netMap[pad.net] = netMap[pad.net] || []).push(`${comp.id}.${pad.number}`); } }
  }
  const nets = Object.entries(netMap).map(([name, pads]) => ({ name, pads }));

  // Build traces: connect passive's IC-facing pad to the matching IC pad
  const traceMap = {};
  for (const p of placed) {
    const icNet = p.padNum ? icNetName(p.padNum) : null;
    if (!icNet || icNet === 'GND') continue;
    const icPad = u1Pads.find(pad => pad.net === icNet);
    if (!icPad) continue;
    const ipWx = r4(cx + icPad.x), ipWy = r4(cy + icPad.y);
    let ppWx, ppWy;
    if (p.rotation === 90) {
      ppWx = p.x;
      ppWy = p.side === 'top' ? r4(p.y + 0.5) : r4(p.y - 0.5);
    } else {
      ppWy = p.y;
      ppWx = p.side === 'left' ? r4(p.x + 0.5) : r4(p.x - 0.5);
    }
    const segs = Math.abs(ppWx - ipWx) < 0.01
      ? [{ start: {x: ipWx, y: ipWy}, end: {x: ppWx, y: ppWy} }]
      : [{ start: {x: ipWx, y: ipWy}, end: {x: ipWx, y: ppWy} }, { start: {x: ipWx, y: ppWy}, end: {x: ppWx, y: ppWy} }];
    if (!traceMap[icNet]) traceMap[icNet] = { net: icNet, segments: [] };
    traceMap[icNet].segments.push(...segs);
  }
  const traces = Object.values(traceMap);

  // Board bounds + 1mm margin, shifted to origin
  // For top/bottom passives (rotation=90): world half-X = pCY[1], world half-Y = pCY[0]
  // For left/right passives (rotation=0): world half-X = pCY[0], world half-Y = pCY[1]
  const allX = [cx - icCY[0], cx + icCY[0],
    ...placed.map(p => p.rotation === 90 ? p.x - pCY[1] : p.x - pCY[0]),
    ...placed.map(p => p.rotation === 90 ? p.x + pCY[1] : p.x + pCY[0])];
  const allY = [cy - icCY[1], cy + icCY[1],
    ...placed.map(p => p.rotation === 90 ? p.y - pCY[0] : p.y - pCY[1]),
    ...placed.map(p => p.rotation === 90 ? p.y + pCY[0] : p.y + pCY[1])];
  const bx0 = Math.min(...allX) - 1, by0 = Math.min(...allY) - 1;
  const bw = r4(Math.max(...allX) + 1 - bx0), bh = r4(Math.max(...allY) + 1 - by0);
  const sx = -bx0, sy = -by0;

  function sh(c) { return { ...c, x: r4(c.x + sx), y: r4(c.y + sy) }; }
  const components = [sh(u1), ...passiveComponents.map(sh)];
  for (const t of traces) {
    for (const seg of t.segments) {
      seg.start = { x: r4(seg.start.x + sx), y: r4(seg.start.y + sy) };
      seg.end   = { x: r4(seg.end.x   + sx), y: r4(seg.end.y   + sy) };
    }
  }
  return { version: '1.0', title: `${partNum} Layout Example`, board: { width: bw, height: bh, units: 'mm' }, components, nets, traces, vias: [] };
}

async function buildLayoutPrompt(slug, profile) {
  const res = await fetch('/api/gen-tickets/build-prompt', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({type:'layout', slug, profile})
  });
  return (await res.json()).prompt;
}

async function fpGenerate(btn) {
  if (!_fpSlug) _fpSlug = selectedSlug;
  if (!_fpSlug) return;
  const orig = btn?.textContent || '';
  if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
  try {
    const res = await fetch(`/api/library/${_fpSlug}/generate-footprint`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    if (!res.ok) {
      if (btn) { btn.textContent = orig; btn.disabled = false; }
      const profile = profileCache[_fpSlug] || await fetch(`/api/library/${_fpSlug}`).then(r => r.json()).catch(() => ({}));
      await gtCreateTicket('footprint', _fpSlug, profile);
      return;
    }
    const { footprint, method, confidence } = await res.json();
    _fpData = footprint;
    const ni = document.getElementById('fp-name'); if (ni) ni.value = _fpData.name || '';
    const di = document.getElementById('fp-desc'); if (di) di.value = _fpData.description || '';
    const sel = document.getElementById('fp-select');
    if (sel) {
      // Add to dropdown if not present
      if (![...sel.options].some(o => o.value === _fpData.name)) {
        const opt = document.createElement('option'); opt.value = _fpData.name; opt.textContent = _fpData.name + ' (generated)'; sel.appendChild(opt);
      }
      sel.value = _fpData.name;
    }
    if (fpEditor) { fpEditor.data = _fpData; fpEditor._fit(); fpEditor._render(); }
    fpRenderPadList();
    if (btn) { btn.textContent = `✓ ${method} · ${confidence}`; setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2500); }
  } catch(e) {
    if (btn) { btn.textContent = orig; btn.disabled = false; }
    const profile = profileCache[_fpSlug] || await fetch(`/api/library/${_fpSlug}`).then(r => r.json()).catch(() => ({}));
    await gtCreateTicket('footprint', _fpSlug, profile);
  }
}

function fpAddPad() {
  if (!_fpData) _fpData = { name: 'custom', description: '', pads: [], courtyard: { x: -3, y: -2, w: 6, h: 4 } };
  _fpData.pads.push({ number: String(_fpData.pads.length + 1), x: 0, y: 0, type: 'smd', shape: 'rect', size_x: 1.0, size_y: 1.0 });
  if (fpEditor) { fpEditor.data = _fpData; fpEditor._render(); }
  fpRenderPadList();
}

function fpDeletePad(idx) {
  if (!_fpData) return;
  _fpData.pads.splice(idx, 1);
  _fpSelectedPad = -1;
  if (fpEditor) { fpEditor.selected = -1; fpEditor._render(); }
  fpRenderPadList();
}

function fpUpdatePad(idx, field, val) {
  if (!_fpData || !_fpData.pads[idx]) return;
  const num = ['x','y','size_x','size_y','drill'].includes(field);
  _fpData.pads[idx][field] = num ? parseFloat(val) : val;
  if (fpEditor) fpEditor._render();
}

function fpSyncPadToTable(idx) {
  // Update the X/Y inputs in the pad table row to reflect dragged position
  if (!_fpData?.pads[idx]) return;
  const pad = _fpData.pads[idx];
  const rows = document.querySelectorAll('#fp-pad-list tbody tr');
  if (rows[idx]) {
    const inputs = rows[idx].querySelectorAll('input[type=number]');
    if (inputs[0]) inputs[0].value = pad.x;
    if (inputs[1]) inputs[1].value = pad.y;
  }
}

function fpRenderPadList() {
  _fpSelectedPad = fpEditor ? fpEditor.selected : _fpSelectedPad;
  const el = document.getElementById('fp-pad-list');
  if (!el) return;
  if (!_fpData || !_fpData.pads.length) { el.innerHTML = '<div style="color:var(--text-muted);font-size:11px;">No pads. Select a footprint or add pads.</div>'; return; }
  el.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:10px;">
    <thead><tr style="color:var(--text-muted);">
      <th style="text-align:left;padding:2px 4px;">#</th>
      <th style="text-align:left;padding:2px 4px;">X mm</th>
      <th style="text-align:left;padding:2px 4px;">Y mm</th>
      <th style="text-align:left;padding:2px 4px;">Type</th>
      <th style="text-align:left;padding:2px 4px;">Wx</th>
      <th style="text-align:left;padding:2px 4px;">Wy</th>
      <th></th>
    </tr></thead>
    <tbody>${_fpData.pads.map((pad, i) => `
      <tr style="background:${i===_fpSelectedPad?'rgba(108,99,255,0.15)':'transparent'};cursor:pointer;" onclick="_fpSelectedPad=${i};if(fpEditor){fpEditor.selected=${i};fpEditor._render();}fpRenderPadList();">
        <td style="padding:2px 4px;font-weight:700;color:var(--accent);">${esc(pad.number||String(i+1))}</td>
        <td style="padding:2px 4px;"><input type="number" step="0.01" value="${pad.x}" onchange="fpUpdatePad(${i},'x',this.value)" style="width:52px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:1px 3px;font-size:10px;"></td>
        <td style="padding:2px 4px;"><input type="number" step="0.01" value="${pad.y}" onchange="fpUpdatePad(${i},'y',this.value)" style="width:52px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:1px 3px;font-size:10px;"></td>
        <td style="padding:2px 4px;">
          <select onchange="fpUpdatePad(${i},'type',this.value)" style="background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:1px 2px;font-size:10px;">
            <option value="smd" ${pad.type==='smd'?'selected':''}>smd</option>
            <option value="thru_hole" ${pad.type==='thru_hole'?'selected':''}>thru</option>
          </select>
        </td>
        <td style="padding:2px 4px;"><input type="number" step="0.01" value="${pad.size_x||1}" onchange="fpUpdatePad(${i},'size_x',this.value)" style="width:44px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:1px 3px;font-size:10px;"></td>
        <td style="padding:2px 4px;"><input type="number" step="0.01" value="${pad.size_y||1}" onchange="fpUpdatePad(${i},'size_y',this.value)" style="width:44px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:3px;padding:1px 3px;font-size:10px;"></td>
        <td style="padding:2px 4px;"><button onclick="fpDeletePad(${i})" style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:12px;" title="Delete">✕</button></td>
      </tr>`).join('')}
    </tbody></table>`;
}
