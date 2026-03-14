// ── SchematicEditor class (SVG-based) ──────────────────────────────────────
class SchematicEditor {
  constructor(svgEl, opts = {}) {
    this.svg = svgEl;
    this.GRID = 20;  // visual grid spacing
    this.SNAP = 10;  // snap resolution (half-grid so IC ports at odd-10 positions align)
    this.zoom = 1; this.panX = 0; this.panY = 0;
    this.project = { id: null, name: 'Untitled', components: [], wires: [] };
    this.dirty = false;
    this.tool = 'select';
    this.selected = null;
    this.multiSelected = new Set(); // IDs of components/labels in multi-select
    this.rubberState = null;        // {sx0,sy0,sx1,sy1} during rubber-band drag
    this._clickCycle = null;        // {sx,sy,items,idx} — repeated-click cycling for overlapping elements
    this.hoveredComp = null; this.hoveredPort = null;
    this.dragState = null;
    this.wirePoints = [];
    this.wireCursor = null;
    this.panState = null;
    this.placeSlug = null; this.placeSymType = null; this.placeCursor = null; this.placeRotation = 0;
    this.placeGroupData = null; // { circ, bboxCx, bboxCy, cursor } for example-circuit placement
    this.labelCursor = null;
    this.ncCursor = null;
    this.history = []; this.historyIdx = -1;
    this.showNets = false;
    this._cachedNetOverlay = null;
    this._highlightedNet = null;
    this._noEvents = !!opts.noEvents;
    this._isEmbedded = !!opts.labelInputId; // true for appCircuitEditor, false for main editor
    this.labelInputId = opts.labelInputId || 'label-name-input';
    this._initSVG();
    if (!this._noEvents) this._bind();
    if (!this._noEvents) window.addEventListener('resize', () => this._render());
  }

  // ── Setup ────────────────────────────────────────────────────────────────
  _initSVG() {
    this.svg.innerHTML = `
      <defs>
        <pattern id="se-gp" patternUnits="userSpaceOnUse">
          <circle r="0.8" fill="#1e2030"/>
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="#0a0c12"/>
      <rect width="100%" height="100%" fill="url(#se-gp)"/>
      <g id="se-view"></g>`;
    this._gp = this.svg.querySelector('#se-gp');
    this._vg = this.svg.querySelector('#se-view');
    this._resetView();
  }

  _W() { return this.svg.clientWidth || 800; }
  _H() { return this.svg.clientHeight || 600; }

  _resize() { this._render(); }

  _bind() {
    this.svg.addEventListener('mousedown', e => this._down(e));
    this.svg.addEventListener('mousemove', e => this._move(e));
    this.svg.addEventListener('mouseup',   e => this._up(e));
    this.svg.addEventListener('mouseleave', () => {
      // Clean up transient drag states when mouse leaves the canvas
      if (this.rubberState) { this.rubberState = null; this._render(); }
      if (this.panState) this.panState = null;
    });
    this.svg.addEventListener('dblclick',  e => this._dbl(e));
    this.svg.addEventListener('wheel', e => { e.preventDefault(); this._wheel(e); }, { passive: false });
    this.svg.addEventListener('contextmenu', e => { e.preventDefault(); this._showContextMenu(e); });
    this._boundKey = e => this._key(e);
    document.addEventListener('keydown', this._boundKey);
  }

  destroy() {
    if (this._boundKey) document.removeEventListener('keydown', this._boundKey);
    this._hideContextMenu();
  }

  // ── Project ───────────────────────────────────────────────────────────────
  newProject(name) {
    this.project = { id: null, name: name || 'Untitled', components: [], wires: [], labels: [], groups: [], noConnects: [] };
    this.dirty = false; this.selected = null;
    this._saveHist(); this._resetView(); this._render(); this._status();
  }

  loadProject(data) {
    this.project = data;
    if (!this.project.labels) this.project.labels = [];
    if (!this.project.groups) this.project.groups = [];
    if (!this.project.noConnects) this.project.noConnects = [];
    this.dirty = false; this.selected = null;
    this._autoConnectAll();
    this._saveHist(); this._fit(); this._render(); this._status();
    // Eagerly populate the net overlay so net panel/labels work immediately on load
    this._refreshNetOverlay();
  }

  async saveProject() {
    const res = await fetch('/api/projects', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(this.project)
    });
    const d = await res.json();
    this.project.id = d.id; this.dirty = false; this._status();
    return d.id;
  }

  loadExample(profile) {
    const circ = JSON.parse(JSON.stringify(profile.example_circuit || buildExampleCircuit(profile)));
    // Normalize wires: support both {points:[]} and legacy {x1,y1,x2,y2} formats
    circ.wires = (circ.wires || []).map(w => {
      if (!w.points && (w.x1 !== undefined)) {
        return { id: w.id || ('w_' + Math.random().toString(36).slice(2)), points: [{x: w.x1, y: w.y1}, {x: w.x2, y: w.y2}] };
      }
      return w;
    });
    // Tag every component with its example origin so PCB importer can use template layout
    const _egId = 'eg_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 4);
    const _egSlug = profile.slug || '';
    circ.components.forEach(c => {
      c._exampleGroupId = _egId; c._exampleSlug = _egSlug;
      c._exampleX = c.x; c._exampleY = c.y;
    });
    this.project.components = circ.components;
    this.project.wires = circ.wires;
    this.project.labels = circ.labels || [];
    this.project.name = (profile.part_number || 'Component') + ' — Application Circuit';
    this._autoConnectAll();
    this.dirty = true; this._saveHist(); this._fit(); this._render(); this._status();
    // Eagerly populate the net overlay so net list and highlights work immediately on open
    this._refreshNetOverlay();
  }

  importExample(profile) {
    // Deep copy so we don't mutate the cached profile data
    const circ = JSON.parse(JSON.stringify(profile.example_circuit || buildExampleCircuit(profile)));
    if (!circ.components.length) return;
    // Normalize wires: support both {points:[]} and legacy {x1,y1,x2,y2} formats
    circ.wires = (circ.wires || []).map(w => {
      if (!w.points && (w.x1 !== undefined)) {
        return { id: w.id || ('w_' + Math.random().toString(36).slice(2)), points: [{x: w.x1, y: w.y1}, {x: w.x2, y: w.y2}] };
      }
      return w;
    });
    // Tag with example origin BEFORE applying placement offset
    const _egId = 'eg_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 4);
    const _egSlug = profile.slug || '';
    circ.components.forEach(c => {
      c._exampleGroupId = _egId; c._exampleSlug = _egSlug;
      c._exampleX = c.x; c._exampleY = c.y;
    });
    // Find offset to place the example below / to the right of existing content
    let offsetX = 0, offsetY = 0;
    if (this.project.components.length > 0) {
      const maxY = Math.max(...this.project.components.map(c => c.y));
      offsetY = maxY + 160;
    }
    // Shift new components, wires and labels
    const minX = Math.min(...circ.components.map(c => c.x));
    const minY = Math.min(...circ.components.map(c => c.y));
    const dx = offsetX - minX + 100;
    const dy = offsetY - minY;
    circ.components.forEach(c => { c.x += dx; c.y += dy; });
    circ.wires.forEach(w => { w.points.forEach(p => { p.x += dx; p.y += dy; }); });
    (circ.labels || []).forEach(l => { l.x += dx; l.y += dy; });
    // Re-assign designators to avoid collisions with existing components
    const takenDesigs = new Set(this.project.components.map(c => c.designator));
    circ.components.forEach(c => {
      const m = c.designator?.match(/^([A-Za-z]+)(\d+)$/);
      if (!m) return;
      const pre = m[1];
      const usedNums = new Set([...takenDesigs].filter(d => d?.startsWith(pre) && /^\d+$/.test(d.slice(pre.length))).map(d => parseInt(d.slice(pre.length))));
      let n = 1; while (usedNums.has(n)) n++;
      c.designator = pre + n;
      takenDesigs.add(c.designator);
    });
    // New IDs to avoid collisions
    circ.components.forEach(c => { c.id = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5); });
    circ.wires.forEach(w => { w.id = 'w' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5); });
    (circ.labels || []).forEach(l => { l.id = 'l' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5); });
    // Merge
    this.project.components.push(...circ.components);
    this.project.wires.push(...circ.wires);
    if (!this.project.labels) this.project.labels = [];
    this.project.labels.push(...(circ.labels || []));
    this._autoConnectAll();
    this.dirty = true; this._saveHist(); this._fit(); this._render(); this._status();
  }

  // ── Coords ────────────────────────────────────────────────────────────────
  _toS(x, y) { return { x: x * this.zoom + this.panX, y: y * this.zoom + this.panY }; }
  _toW(sx, sy) { return { x: (sx - this.panX) / this.zoom, y: (sy - this.panY) / this.zoom }; }

  // Returns the world-space position of a component's designator/value label center
  _labelWorldPos(comp) {
    const isIC = comp.symType === 'ic' || !SYMDEFS[comp.symType];
    const lay = isIC ? this._icLayout(comp.slug || '') : this._def(comp.symType);
    const autoLy = isIC ? -(lay.BOX_H / 2 + 9) : -(lay.h / 2 + 11);
    const lx = comp.labelOffsetX || 0;
    const ly = autoLy + (comp.labelOffsetY || 0);
    const rot = (comp.rotation || 0) * 90 * Math.PI / 180;
    const cosR = Math.cos(rot), sinR = Math.sin(rot);
    return { x: comp.x + lx * cosR - ly * sinR, y: comp.y + lx * sinR + ly * cosR };
  }
  _snap(v) { return Math.round(v / this.SNAP) * this.SNAP; }
  _snapPt(x, y) { return { x: this._snap(x), y: this._snap(y) }; }
  _evPos(e) { const r = this.svg.getBoundingClientRect(); return { sx: e.clientX - r.left, sy: e.clientY - r.top }; }

  // ── Symbol defs ──────────────────────────────────────────────────────────
  _def(t) { return SYMDEFS[t] || SYMDEFS.ic; }

  // Dynamic IC layout from cached profile (DIP-style: left half top→bottom, right half bottom→top)
  _icLayout(slug) {
    if (window._icLayoutCache && window._icLayoutCache[slug]) return window._icLayoutCache[slug];
    const PIN_STUB = 40, ROW_H = 20, PAD_Y = 16, BOX_W = 120;
    const profile = profileCache[slug];
    const pins = (profile && profile.pins)
      ? [...profile.pins].sort((a, b) => (a.number || 0) - (b.number || 0))
      : [];
    const N = pins.length || 4;
    const half = Math.ceil(N / 2);
    const leftPins = pins.slice(0, half);
    const rightPins = [...pins.slice(half)].reverse(); // DIP: right side top→bottom shows high numbers first
    const maxRows = Math.max(leftPins.length, rightPins.length, 1);
    const BOX_H = maxRows * ROW_H + 2 * PAD_Y;
    const ports = [];
    leftPins.forEach((pin, i) => {
      const y = -(BOX_H / 2 - PAD_Y) + i * ROW_H;
      ports.push({ dx: -(BOX_W / 2 + PIN_STUB), dy: y, name: pin.name || `P${pin.number}` });
    });
    rightPins.forEach((pin, i) => {
      const y = -(BOX_H / 2 - PAD_Y) + i * ROW_H;
      ports.push({ dx: BOX_W / 2 + PIN_STUB, dy: y, name: pin.name || `P${pin.number}` });
    });
    return { w: BOX_W + 2 * PIN_STUB, h: BOX_H, ports, leftPins, rightPins, BOX_W, BOX_H, PIN_STUB, ROW_H, PAD_Y };
  }

  _rotPt(dx, dy, rot) {
    // Must match SVG rotate(rot*90): new_px = px·cosθ − py·sinθ, new_py = px·sinθ + py·cosθ
    // θ=90°  → (−py, px)  i.e. { dx: −dy, dy:  dx }
    // θ=180° → (−px,−py)  i.e. { dx: −dx, dy: −dy }
    // θ=270° → ( py,−px)  i.e. { dx:  dy, dy: −dx }
    switch ((rot || 0) % 4) {
      case 1: return { dx: -dy, dy:  dx };
      case 2: return { dx: -dx, dy: -dy };
      case 3: return { dx:  dy, dy: -dx };
      default: return { dx, dy };
    }
  }

  _ports(comp) {
    const isIC = comp.symType === 'ic' || !SYMDEFS[comp.symType];
    const ports = isIC ? this._icLayout(comp.slug || '').ports : this._def(comp.symType).ports;
    return ports.map((p, i) => {
      const r = this._rotPt(p.dx, p.dy, comp.rotation || 0);
      return { x: comp.x + r.dx, y: comp.y + r.dy, name: p.name, idx: i };
    });
  }

  _bbox(comp) {
    const isIC = comp.symType === 'ic' || !SYMDEFS[comp.symType];
    const d = isIC ? this._icLayout(comp.slug || '') : this._def(comp.symType);
    const rot = (comp.rotation || 0) % 2;
    const w = rot ? d.h : d.w, h = rot ? d.w : d.h;
    return { x: comp.x - w / 2, y: comp.y - h / 2, w, h };
  }

  // ── Hit tests ────────────────────────────────────────────────────────────
  _hitComp(sx, sy) {
    const p = this._toW(sx, sy);
    for (let i = this.project.components.length - 1; i >= 0; i--) {
      const c = this.project.components[i], b = this._bbox(c), pad = 8 / this.zoom;
      if (p.x >= b.x - pad && p.x <= b.x + b.w + pad && p.y >= b.y - pad && p.y <= b.y + b.h + pad) return c;
    }
    return null;
  }

  _hitPort(sx, sy, r = 14) {
    const p = this._toW(sx, sy), rd = r / this.zoom;
    for (const c of this.project.components) {
      for (const pt of this._ports(c)) {
        if (Math.hypot(pt.x - p.x, pt.y - p.y) <= rd) return { comp: c, port: pt };
      }
    }
    return null;
  }

  _hitWire(sx, sy) {
    const p = this._toW(sx, sy), th = 6 / this.zoom;
    for (let i = this.project.wires.length - 1; i >= 0; i--) {
      const w = this.project.wires[i];
      for (let j = 0; j < w.points.length - 1; j++) {
        if (this._nearSeg(p, w.points[j], w.points[j + 1], th)) return w;
      }
    }
    return null;
  }

  _nearSeg(p, a, b, t) {
    const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
    if (l2 === 0) return Math.hypot(p.x - a.x, p.y - a.y) <= t;
    const k = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2));
    return Math.hypot(p.x - a.x - k * dx, p.y - a.y - k * dy) <= t;
  }

  // ── Events ───────────────────────────────────────────────────────────────
  _down(e) {
    const { sx, sy } = this._evPos(e);
    if (e.button === 1 || (e.button === 0 && (e.ctrlKey || e.metaKey))) {
      this.panState = { sx, sy, ox: this.panX, oy: this.panY }; e.preventDefault(); return;
    }
    if (e.button !== 0) return;
    this.rubberState = null; // always clear any leftover rubber-band on new mousedown

    if (this.tool === 'place' && this.placeSlug) { this._place(sx, sy); return; }

    if (this.tool === 'placeGroup' && this.placeGroupData) { this._placeGroup(sx, sy); return; }

    if (this.tool === 'label') { this._placeLabel(sx, sy); return; }

    if (this.tool === 'nc') { this._placeNC(sx, sy); return; }

    if (this.tool === 'wire') {
      const snap = this._snapPortOrGrid(sx, sy);
      if (this.wirePoints.length === 0) {
        this.wirePoints = [snap];
      } else {
        const last = this.wirePoints[this.wirePoints.length - 1];
        this.wirePoints.push({ x: snap.x, y: last.y }, snap); // L-route elbow
        if (snap.isPort) { this._finishWire(); return; } // auto-complete on port/endpoint
      }
      this._render(); return;
    }

    if (this.tool === 'delete') {
      const nc = this._hitNC(sx, sy); if (nc) { this._delNC(nc.id); return; }
      const c = this._hitComp(sx, sy); if (c) { this._delComp(c.id); return; }
      const l = this._hitLabel(sx, sy); if (l) { this._delLabel(l.id); return; }
      const w = this._hitWire(sx, sy); if (w) { this._delWire(w.id); return; }
      return;
    }

    // Check for component label drag handle (the ● dot shown when a comp is selected)
    if (this.tool === 'select' && this.selected?.type === 'comp') {
      const selComp = this.project.components.find(c => c.id === this.selected.id);
      const st = selComp?.symType || 'ic';
      if (selComp && st !== 'vcc' && st !== 'gnd') {
        const lp = this._labelWorldPos(selComp);
        const TOL = 10 / this.zoom;
        const wp = this._toW(sx, sy);
        if (Math.hypot(wp.x - lp.x, wp.y - lp.y) < TOL) {
          this.dragState = { type: 'compLabel', id: selComp.id, sx, sy, origLX: selComp.labelOffsetX || 0, origLY: selComp.labelOffsetY || 0 };
          this._render(); return;
        }
      }
    }

    // NC marker: click selects it so Delete key can remove it
    const ncHit = this._hitNC(sx, sy);
    if (ncHit && this.tool === 'select') {
      this.multiSelected.clear();
      this.selected = { type: 'nc', id: ncHit.id };
      this._render(); return;
    }

    // Hit resolution: wire precision (6px) beats loose component bbox, EXCEPT when
    // the click is within PORT_R world-units of a component port — in that case the
    // component wins so GND/VCC/etc. remain draggable even when a wire endpoint
    // sits exactly on the port.
    const labelHit = this._hitLabel(sx, sy);
    const compHit  = !labelHit ? this._hitComp(sx, sy) : null;
    const wireHit  = !labelHit ? this._hitWire(sx, sy) : null;
    let comp = null;
    if (compHit) {
      if (!wireHit) {
        comp = compHit; // only component, no wire
      } else {
        // Both hit: prefer component only when click is very close to one of its ports
        const PORT_R = 12 / this.zoom;
        const wp = this._toW(sx, sy);
        const nearPort = this._ports(compHit).some(p => Math.hypot(p.x - wp.x, p.y - wp.y) < PORT_R);
        comp = nearPort ? compHit : null;
      }
    }
    const wireFirst = wireHit && !comp ? wireHit : null;
    const hitId = labelHit?.id || comp?.id;

    // Shift+click: toggle item in/out of multi-select
    if (e.shiftKey) {
      if (hitId) {
        if (this.multiSelected.has(hitId)) this.multiSelected.delete(hitId);
        else this.multiSelected.add(hitId);
        this.selected = null; this._render(); return;
      }
      const wireHit = this._hitWire(sx, sy);
      if (wireHit) {
        if (this.multiSelected.has(wireHit.id)) this.multiSelected.delete(wireHit.id);
        else this.multiSelected.add(wireHit.id);
        this.selected = null; this._render(); return;
      }
    }

    // Named group: clicking any member auto-selects all group members
    if (!e.shiftKey) {
      const wireForGrp = !hitId ? this._hitWire(sx, sy) : null;
      const anyHitId = hitId || wireForGrp?.id;
      if (anyHitId) {
        const namedGrp = this._getGroupByMember(anyHitId);
        if (namedGrp) {
          const allInMulti = namedGrp.members.every(mid => this.multiSelected.has(mid));
          if (!allInMulti) {
            this.multiSelected.clear();
            for (const mid of namedGrp.members) this.multiSelected.add(mid);
            this.selected = null;
            this.dragState = { type: 'group', sx, sy, items: this._buildGroupDragItems() };
            this._render(); return;
          }
          // group already fully selected — fall through to existing drag code
        }
      }
    }

    // Click on item already in multi-select → start group drag
    const wireHitForGroup = !hitId ? this._hitWire(sx, sy) : null;
    const groupHitId = hitId || wireHitForGroup?.id;
    if (groupHitId && this.multiSelected.has(groupHitId)) {
      this.dragState = { type: 'group', sx, sy, items: this._buildGroupDragItems() };
      this._render(); return;
    }

    if (labelHit) {
      this.multiSelected.clear();
      this.selected = { type: 'label', id: labelHit.id };
      const TOL = this.GRID * 1.5;
      const labelWires = [];
      for (const w of this.project.wires) {
        if (!w.points || w.points.length < 2) continue;
        const p0 = w.points[0], pN = w.points[w.points.length - 1];
        const isDot = Math.hypot(p0.x - pN.x, p0.y - pN.y) < 0.5;
        const d1 = Math.hypot(p0.x - labelHit.x, p0.y - labelHit.y);
        const d2 = Math.hypot(pN.x - labelHit.x, pN.y - labelHit.y);
        // Dot-wire at label: stretch it into a real wire.
        if (isDot && d1 < TOL) { labelWires.push({ id: w.id, end: 0, otherPtOrig: { x: pN.x, y: pN.y } }); continue; }
        if (d1 < TOL) labelWires.push({ id: w.id, end: 0 });
        if (d2 < TOL) labelWires.push({ id: w.id, end: -1 });
      }
      this.dragState = { id: labelHit.id, sx, sy, origX: labelHit.x, origY: labelHit.y, isLabel: true, labelWires };
      this._render(); return;
    }
    if (comp) {
      this.multiSelected.clear();
      this.selected = { type: 'comp', id: comp.id };
      const ports = this._ports(comp);
      const TOL = 0.5; // exact: only capture endpoints that are on the same coordinate as the port
      const connectedWires = [];
      for (let pi = 0; pi < ports.length; pi++) {
        const port = ports[pi];
        for (const w of this.project.wires) {
          if (!w.points || w.points.length < 2) continue;
          const p0 = w.points[0], pN = w.points[w.points.length - 1];
          const d1 = Math.hypot(p0.x - port.x, p0.y - port.y);
          const d2 = Math.hypot(pN.x - port.x, pN.y - port.y);
          const isDot = Math.hypot(p0.x - pN.x, p0.y - pN.y) < 0.5;
          // Dot-wire (T-junction marker): stretch it — one end stays, other follows component.
          if (isDot && d1 < TOL) { connectedWires.push({ id: w.id, end: 0, portIdx: pi, dx: 0, dy: 0, otherPtOrig: { x: pN.x, y: pN.y } }); continue; }
          // If BOTH endpoints are within TOL the port is near the wire's midpoint — skip.
          if (d1 < TOL && d2 < TOL) continue;
          // Skip pass-through bus stubs.
          if (d1 < TOL && !this._isPassthroughEndpoint(w.id, p0, port, TOL)) connectedWires.push({ id: w.id, end: 0, portIdx: pi, dx: p0.x - port.x, dy: p0.y - port.y, otherPtOrig: { x: pN.x, y: pN.y } });
          if (d2 < TOL && !this._isPassthroughEndpoint(w.id, pN, port, TOL)) connectedWires.push({ id: w.id, end: -1, portIdx: pi, dx: pN.x - port.x, dy: pN.y - port.y, otherPtOrig: { x: p0.x, y: p0.y } });
        }
      }
      this.dragState = { id: comp.id, sx, sy, origX: comp.x, origY: comp.y, connectedWires };
      this._render(); return;
    }
    const wire = this._hitWire(sx, sy);
    if (wire) { this.multiSelected.clear(); this.selected = { type: 'wire', id: wire.id }; this._render(); return; }
    // Empty space: start rubber-band selection
    this.multiSelected.clear(); this.selected = null;
    this.rubberState = { sx0: sx, sy0: sy, sx1: sx, sy1: sy };
    this._render();
  }

  _move(e) {
    const { sx, sy } = this._evPos(e);
    if (this.panState) {
      this.panX = this.panState.ox + sx - this.panState.sx;
      this.panY = this.panState.oy + sy - this.panState.sy;
      this._render(); return;
    }
    if (this.rubberState) {
      if (e.buttons === 0) { this.rubberState = null; this._render(); return; }
      this.rubberState.sx1 = sx; this.rubberState.sy1 = sy;
      this._render(); return;
    }
    if (this.dragState) {
      if (this.dragState.type === 'compLabel') {
        const comp = this.project.components.find(c => c.id === this.dragState.id);
        if (comp) {
          const w0 = this._toW(this.dragState.sx, this.dragState.sy);
          const w1 = this._toW(sx, sy);
          const ddxW = w1.x - w0.x, ddyW = w1.y - w0.y;
          // Convert world delta to component local space (inverse rotation)
          const rot = (comp.rotation || 0) * 90 * Math.PI / 180;
          const cosR = Math.cos(rot), sinR = Math.sin(rot);
          comp.labelOffsetX = (this.dragState.origLX || 0) + ddxW * cosR + ddyW * sinR;
          comp.labelOffsetY = (this.dragState.origLY || 0) - ddxW * sinR + ddyW * cosR;
        }
        this._render(); return;
      }
      if (this.dragState.type === 'group') {
        const w0 = this._toW(this.dragState.sx, this.dragState.sy);
        const w1 = this._toW(sx, sy);
        const ddx = this._snap(w1.x - w0.x), ddy = this._snap(w1.y - w0.y);
        for (const item of this.dragState.items) {
          if (item.type === 'comp') {
            const comp = this.project.components.find(c => c.id === item.id);
            if (!comp) continue;
            comp.x = item.origX + ddx; comp.y = item.origY + ddy;
            const ports = this._ports(comp);
            for (const cw of item.connectedWires) {
              const port = ports[cw.portIdx];
              if (!port) continue;
              const wire = this.project.wires.find(w => w.id === cw.id);
              if (!wire) continue;
              if (cw.end === 0) { wire.points[0].x = port.x + cw.dx; wire.points[0].y = port.y + cw.dy; }
              else { const last = wire.points[wire.points.length-1]; last.x = port.x + cw.dx; last.y = port.y + cw.dy; }
            }
          } else if (item.type === 'label') {
            const lbl = (this.project.labels||[]).find(l => l.id === item.id);
            if (!lbl) continue;
            lbl.x = item.origX + ddx; lbl.y = item.origY + ddy;
            for (const cw of (item.labelWires||[])) {
              const wire = this.project.wires.find(w => w.id === cw.id);
              if (!wire) continue;
              if (cw.end === 0) { wire.points[0].x = lbl.x; wire.points[0].y = lbl.y; }
              else { wire.points[wire.points.length-1].x = lbl.x; wire.points[wire.points.length-1].y = lbl.y; }
              if (cw.otherPtOrig) {
                const otherPt = cw.end === 0 ? wire.points[wire.points.length - 1] : wire.points[0];
                otherPt.x = cw.otherPtOrig.x; otherPt.y = cw.otherPtOrig.y;
                this._rerouteWire90(wire);
              }
            }
          } else if (item.type === 'wire') {
            const wire = this.project.wires.find(w => w.id === item.id);
            if (!wire) continue;
            for (let pi = 0; pi < wire.points.length; pi++) {
              wire.points[pi].x = item.origPoints[pi].x + ddx;
              wire.points[pi].y = item.origPoints[pi].y + ddy;
            }
          }
        }
        this.dirty = true; this._render(); return;
      }
      const w0 = this._toW(this.dragState.sx, this.dragState.sy);
      const w1 = this._toW(sx, sy);
      if (this.dragState.isLabel) {
        const lbl = (this.project.labels || []).find(l => l.id === this.dragState.id);
        if (lbl) {
          const rawX = this.dragState.origX + w1.x - w0.x;
          const rawY = this.dragState.origY + w1.y - w0.y;
          const snapped = this._snapPortOrGrid(
            (rawX * this.zoom) + this.panX,
            (rawY * this.zoom) + this.panY
          );
          lbl.x = snapped.x;
          lbl.y = snapped.y;
          // Move connected wire endpoints to follow the label's pin
          for (const cw of (this.dragState.labelWires || [])) {
            const wire = this.project.wires.find(w => w.id === cw.id);
            if (!wire) continue;
            if (cw.end === 0) { wire.points[0].x = lbl.x; wire.points[0].y = lbl.y; }
            else { wire.points[wire.points.length - 1].x = lbl.x; wire.points[wire.points.length - 1].y = lbl.y; }
            // Lock the junction end of a dot-wire so it doesn't drift
            if (cw.otherPtOrig) {
              const otherPt = cw.end === 0 ? wire.points[wire.points.length - 1] : wire.points[0];
              otherPt.x = cw.otherPtOrig.x; otherPt.y = cw.otherPtOrig.y;
              this._rerouteWire90(wire);
            }
          }
          this.dirty = true; this._render();
        }
        return;
      }
      const comp = this.project.components.find(c => c.id === this.dragState.id);
      if (comp) {
        comp.x = this._snap(this.dragState.origX + w1.x - w0.x);
        comp.y = this._snap(this.dragState.origY + w1.y - w0.y);
        // Move connected wire endpoints to follow ports; lock the "other end" to its original pin
        if (this.dragState.connectedWires?.length) {
          const ports = this._ports(comp);
          for (const cw of this.dragState.connectedWires) {
            const port = ports[cw.portIdx];
            if (!port) continue;
            const wire = this.project.wires.find(w => w.id === cw.id);
            if (!wire) continue;
            if (cw.end === 0) {
              wire.points[0].x = port.x + cw.dx; wire.points[0].y = port.y + cw.dy;
            } else {
              const last = wire.points[wire.points.length - 1];
              last.x = port.x + cw.dx; last.y = port.y + cw.dy;
            }
            // Explicitly hold the other endpoint to its original position so it
            // cannot drift if another cw entry for the same wire runs out of order.
            if (cw.otherPtOrig) {
              const otherAlsoMoved = this.dragState.connectedWires.some(c => c.id === cw.id && c !== cw);
              if (!otherAlsoMoved) {
                const otherPt = cw.end === 0 ? wire.points[wire.points.length - 1] : wire.points[0];
                otherPt.x = cw.otherPtOrig.x; otherPt.y = cw.otherPtOrig.y;
              }
            }
          }
          // Enforce 90° routing on all stretched wires (after all endpoints are set)
          const _seen = new Set();
          for (const cw of this.dragState.connectedWires) {
            if (_seen.has(cw.id)) continue;
            _seen.add(cw.id);
            const w = this.project.wires.find(ww => ww.id === cw.id);
            if (w) this._rerouteWire90(w);
          }
        }
        this.dirty = true; this._render();
      }
      return;
    }
    if (this.tool === 'wire' && this.wirePoints.length > 0) {
      this.wireCursor = this._snapPortOrGrid(sx, sy); this._render(); return;
    }
    if (this.tool === 'place') {
      const w = this._toW(sx, sy); this.placeCursor = this._snapPt(w.x, w.y); this._render(); return;
    }
    if (this.tool === 'placeGroup' && this.placeGroupData) {
      const w = this._toW(sx, sy); this.placeGroupData.cursor = this._snapPt(w.x, w.y); this._render(); return;
    }
    if (this.tool === 'label') {
      this.labelCursor = this._snapPortOrGrid(sx, sy); this._render(); return;
    }
    if (this.tool === 'nc') {
      const w = this._toW(sx, sy); this.ncCursor = this._snapNearPort(w.x, w.y); this._render(); return;
    }
    const prevHC = this.hoveredComp, prevHP = this.hoveredPort;
    this.hoveredComp = this._hitComp(sx, sy);
    this.hoveredPort = this._hitPort(sx, sy);
    if (this.hoveredComp !== prevHC || this.hoveredPort !== prevHP) this._render();
    const w = this._toW(sx, sy), s = this._snapPt(w.x, w.y);
    const el = document.getElementById('editor-status-coords');
    if (el) el.textContent = `${s.x}, ${s.y}`;
  }

  _buildGroupDragItems() {
    const items = [], TOL = 0.5; // exact: same-coordinate only
    for (const id of this.multiSelected) {
      const comp = this.project.components.find(c => c.id === id);
      if (comp) {
        const ports = this._ports(comp);
        const connectedWires = [];
        for (let pi = 0; pi < ports.length; pi++) {
          const port = ports[pi];
          for (const w of this.project.wires) {
            if (!w.points || w.points.length < 2) continue;
            const p0 = w.points[0], pN = w.points[w.points.length - 1];
            const d1 = Math.hypot(p0.x - port.x, p0.y - port.y);
            const d2 = Math.hypot(pN.x - port.x, pN.y - port.y);
            const isDot = Math.hypot(p0.x - pN.x, p0.y - pN.y) < 0.5;
            // Dot-wire (T-junction marker): stretch it — one end stays, other follows component.
            if (isDot && d1 < TOL) { connectedWires.push({ id: w.id, end: 0, portIdx: pi, dx: 0, dy: 0, otherPtOrig: { x: pN.x, y: pN.y } }); continue; }
            // If BOTH endpoints are within TOL the port is near the wire's midpoint — skip.
            if (d1 < TOL && d2 < TOL) continue;
            // Skip pass-through bus stubs.
            if (d1 < TOL && !this._isPassthroughEndpoint(w.id, p0, port, TOL)) connectedWires.push({ id: w.id, end: 0, portIdx: pi, dx: p0.x - port.x, dy: p0.y - port.y, otherPtOrig: { x: pN.x, y: pN.y } });
            if (d2 < TOL && !this._isPassthroughEndpoint(w.id, pN, port, TOL)) connectedWires.push({ id: w.id, end: -1, portIdx: pi, dx: pN.x - port.x, dy: pN.y - port.y, otherPtOrig: { x: p0.x, y: p0.y } });
          }
        }
        items.push({ type: 'comp', id, origX: comp.x, origY: comp.y, connectedWires });
        continue;
      }
      const lbl = (this.project.labels || []).find(l => l.id === id);
      if (lbl) {
        const labelWires = [];
        for (const w of this.project.wires) {
          if (!w.points || w.points.length < 2) continue;
          const p0 = w.points[0], pN = w.points[w.points.length - 1];
          const isDot = Math.hypot(p0.x - pN.x, p0.y - pN.y) < 0.5;
          const d1 = Math.hypot(p0.x - lbl.x, p0.y - lbl.y);
          const d2 = Math.hypot(pN.x - lbl.x, pN.y - lbl.y);
          if (isDot && d1 < TOL) { labelWires.push({ id: w.id, end: 0, otherPtOrig: { x: pN.x, y: pN.y } }); continue; }
          if (d1 < TOL) labelWires.push({ id: w.id, end: 0 });
          if (d2 < TOL) labelWires.push({ id: w.id, end: -1 });
        }
        items.push({ type: 'label', id, origX: lbl.x, origY: lbl.y, labelWires });
        continue;
      }
      const wire = this.project.wires.find(w => w.id === id);
      if (wire) {
        items.push({ type: 'wire', id, origPoints: wire.points.map(p => ({ x: p.x, y: p.y })) });
      }
    }
    return items;
  }

  _up(e) {
    if (this.panState) { this.panState = null; return; }
    // Finalise rubber-band: select all items inside the rect
    if (this.rubberState) {
      const p0 = this._toW(this.rubberState.sx0, this.rubberState.sy0);
      const p1 = this._toW(this.rubberState.sx1, this.rubberState.sy1);
      const rx0 = Math.min(p0.x, p1.x), rx1 = Math.max(p0.x, p1.x);
      const ry0 = Math.min(p0.y, p1.y), ry1 = Math.max(p0.y, p1.y);
      this.multiSelected.clear();
      for (const c of this.project.components) {
        const b = this._bbox(c);
        const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
        if (cx >= rx0 && cx <= rx1 && cy >= ry0 && cy <= ry1) this.multiSelected.add(c.id);
      }
      for (const l of (this.project.labels || [])) {
        if (l.x >= rx0 && l.x <= rx1 && l.y >= ry0 && l.y <= ry1) this.multiSelected.add(l.id);
      }
      for (const w of this.project.wires) {
        if (!w.points?.length) continue;
        const p0 = w.points[0], pN = w.points[w.points.length - 1];
        if (p0.x >= rx0 && p0.x <= rx1 && p0.y >= ry0 && p0.y <= ry1 &&
            pN.x >= rx0 && pN.x <= rx1 && pN.y >= ry0 && pN.y <= ry1) {
          this.multiSelected.add(w.id);
        }
      }
      this.rubberState = null;
      this._render(); return;
    }
    if (this.dragState) {
      if (this.dragState.type === 'compLabel') {
        this.dragState = null;
        this._saveHist();
        return;
      }
      if (this.dragState.type === 'group') {
        this.dragState = null;
        this._autoConnectAll();
        this._saveHist();
        this._render(); return;
      }
      const compId = this.dragState.id;
      const cWires = this.dragState.connectedWires || [];
      this.dragState = null;
      const comp = this.project.components.find(c => c.id === compId);
      if (comp) {
        // Snap the "other end" of each stretched wire to the nearest port or label it
        // was touching — this ensures the free endpoint holds precisely to its pin.
        const TOL = 0.5; // exact: snap only to ports at same coordinate
        for (const cw of cWires) {
          const wire = this.project.wires.find(w => w.id === cw.id);
          if (!wire || !cw.otherPtOrig) continue;
          const otherAlsoMoved = cWires.some(c => c.id === cw.id && c !== cw);
          if (otherAlsoMoved) continue; // both ends connected to same comp — skip
          const otherIdx = cw.end === 0 ? wire.points.length - 1 : 0;
          const otherPt = wire.points[otherIdx];
          // Try snapping to the nearest component port
          let snapped = false;
          for (const other of this.project.components) {
            if (other.id === compId) continue;
            for (const port of this._ports(other)) {
              if (Math.hypot(port.x - otherPt.x, port.y - otherPt.y) < TOL) {
                otherPt.x = port.x; otherPt.y = port.y; snapped = true; break;
              }
            }
            if (snapped) break;
          }
          // Try snapping to the nearest label pin
          if (!snapped) {
            for (const lbl of (this.project.labels || [])) {
              if (Math.hypot(lbl.x - otherPt.x, lbl.y - otherPt.y) < TOL) {
                otherPt.x = lbl.x; otherPt.y = lbl.y; break;
              }
            }
          }
        }
        this._autoConnectPorts(comp, true); // fromDrag=true: create port-to-port wires
      }
      this._saveHist();
      return;
    }
  }

  _dbl(e) {
    if (this.tool === 'wire' && this.wirePoints.length > 0) { this._finishWire(); return; }
    if (this.tool === 'select') {
      const { sx, sy } = this._evPos(e);
      const lbl = this._hitLabel(sx, sy);
      if (lbl) { this._editLabel(lbl); return; }
    }
  }

  _editComp(comp) {
    openCompEditModal(comp, this);
  }

  _editLabel(lbl) {
    openLabelEditModal(lbl, this);
  }

  _wheel(e) {
    const { sx, sy } = this._evPos(e);
    const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const nz = Math.max(0.08, Math.min(10, this.zoom * f));
    this.panX = sx - (sx - this.panX) * (nz / this.zoom);
    this.panY = sy - (sy - this.panY) * (nz / this.zoom);
    this.zoom = nz;
    this._render();
    if (!this._isEmbedded) {
      const el = document.getElementById('editor-status-zoom');
      if (el) el.textContent = Math.round(this.zoom * 100) + '%';
    }
  }

  _key(e) {
    // offsetParent is HTMLElement-only; use getBoundingClientRect instead
    const r = this.svg.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return; // editor not visible
    const k = e.key;
    if (k === 'Escape') { this._cancel(); e.target.blur?.(); return; }
    if (['INPUT','TEXTAREA'].includes(e.target.tagName)) return;
    else if (k === 's' || k === 'S') this.setTool('select');
    else if (k === 'b' || k === 'B') this.setTool('boxselect');
    else if (k === 'w' || k === 'W') this.setTool('wire');
    else if (k === 'd' || k === 'D') this.setTool('delete');
    else if (k === 'l' || k === 'L') this.setTool('label');
    else if (k === 'x' || k === 'X') this.setTool('nc');
    else if (k === 'r' || k === 'R') {
      if (this.tool === 'place') { this.placeRotation = (this.placeRotation + 1) % 4; this._render(); }
      else this._rotateSelected();
    }
    else if (k === 'f' || k === 'F') this._fit();
    else if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); this._delSelected(); }
    else if ((k === 'z' || k === 'Z') && (e.ctrlKey || e.metaKey) && e.shiftKey) { e.preventDefault(); this._redo(); }
    else if (k === 'z' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this._undo(); }
    else if (k === 'y' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this._redo(); }
    else if ((k === 'g' || k === 'G') && (e.ctrlKey || e.metaKey) && e.shiftKey) { e.preventDefault(); this.ungroupSelected(); }
    else if ((k === 'g' || k === 'G') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.groupSelected(); }
    else if (k === 'c' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this._copySelected(); }
    else if (k === 'v' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this._pasteClipboard(); }
  }

  // ── Tools ─────────────────────────────────────────────────────────────────
  setTool(t) {
    // Reset in-progress state (no _cancel() call to avoid recursion)
    this.wirePoints = []; this.wireCursor = null;
    if (t !== 'place') { this.placeSlug = null; this.placeCursor = null; this.placeValue = null; }
    if (t !== 'placeGroup') { this.placeGroupData = null; }
    if (t !== 'label') { this.labelCursor = null; }
    if (t !== 'nc') { this.ncCursor = null; }
    this.multiSelected.clear(); this.rubberState = null;
    this.tool = t;
    const cursors = { select: 'default', boxselect: 'crosshair', wire: 'crosshair', delete: 'not-allowed', place: 'copy', placeGroup: 'copy', label: 'crosshair', nc: 'crosshair' };
    this.svg.style.cursor = cursors[t] || 'default';
    if (this._isEmbedded) {
      document.querySelectorAll('[data-acc-tool]').forEach(b => b.classList.toggle('active', b.dataset.accTool === t));
    } else {
      document.querySelectorAll('.tool-btn[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === t));
      const el = document.getElementById('editor-status-tool');
      if (el) el.textContent = t.toUpperCase();
    }
  }

  startPlace(slug, symType) {
    this.placeRotation = 0;
    this.placeSlug = slug; this.placeSymType = symType; this.setTool('place');
  }

  startPlaceGroup(profile) {
    const circ = JSON.parse(JSON.stringify(profile.example_circuit || buildExampleCircuit(profile)));
    if (!circ.components.length) return;
    // Refresh component values from active version profileCache
    circ.components.forEach(c => {
      const pc = (typeof profileCache !== 'undefined') && profileCache[c.slug];
      if (pc) c.value = pc.value || pc.part_number || c.value;
    });
    const bboxCx = circ.components.reduce((s, c) => s + c.x, 0) / circ.components.length;
    const bboxCy = circ.components.reduce((s, c) => s + c.y, 0) / circ.components.length;
    this.placeGroupData = { circ, bboxCx, bboxCy, cursor: null, slug: profile.slug || '' };
    this.setTool('placeGroup');
  }

  _cancel() {
    this.wirePoints = []; this.wireCursor = null;
    this.placeSlug = null; this.placeCursor = null;
    this.placeGroupData = null;
    this.labelCursor = null;
    this.ncCursor = null;
    this.multiSelected.clear(); this.rubberState = null;
    this.selected = null;
    this._hideContextMenu();
    if (this.tool !== 'select') this.setTool('select');
    this._render();
  }

  // ── Component ops ─────────────────────────────────────────────────────────
  _place(sx, sy) {
    const w = this._toW(sx, sy), s = this._snapPt(w.x, w.y);
    const comp = {
      id: 'c' + Date.now().toString(36),
      slug: this.placeSlug, symType: this.placeSymType,
      designator: this._autoRef(this.placeSymType),
      value: this.placeValue != null ? this.placeValue : (profileCache[this.placeSlug]?.value || profileCache[this.placeSlug]?.part_number || library[this.placeSlug]?.part_number || ''),
      x: s.x, y: s.y, rotation: this.placeRotation
    };
    this._saveHist();
    this.project.components.push(comp);
    this._autoConnectPorts(comp, true); // fromDrag=true: create port-to-port wires on placement
    this.selected = { type: 'comp', id: comp.id };
    this.dirty = true; this._render(); this._status();
  }

  _placeGroup(sx, sy) {
    const { circ, bboxCx, bboxCy } = this.placeGroupData;
    const w = this._toW(sx, sy), s = this._snapPt(w.x, w.y);
    const dx = s.x - bboxCx, dy = s.y - bboxCy;
    // Deep-copy and tag with example origin BEFORE applying offset
    const placed = JSON.parse(JSON.stringify(circ));
    const _egId = 'eg_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 4);
    const _egSlug = this.placeGroupData.slug || '';
    placed.components.forEach(c => {
      c._exampleGroupId = _egId; c._exampleSlug = _egSlug;
      c._exampleX = c.x; c._exampleY = c.y; // template coords before placement offset
    });
    placed.components.forEach(c => { c.x += dx; c.y += dy; });
    placed.wires.forEach(w => { w.points.forEach(p => { p.x += dx; p.y += dy; }); });
    (placed.labels || []).forEach(l => { l.x += dx; l.y += dy; });
    // Re-assign designators to avoid collisions
    const takenDesigs = new Set(this.project.components.map(c => c.designator));
    placed.components.forEach(c => {
      const m = c.designator?.match(/^([A-Za-z]+)(\d+)$/);
      if (!m) return;
      const pre = m[1];
      const used = new Set([...takenDesigs].filter(d => d?.startsWith(pre) && /^\d+$/.test(d.slice(pre.length))).map(d => parseInt(d.slice(pre.length))));
      let n = 1; while (used.has(n)) n++;
      c.designator = pre + n; takenDesigs.add(c.designator);
    });
    // New IDs
    placed.components.forEach(c => { c.id = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2,5); });
    placed.wires.forEach(w => { w.id = 'w' + Date.now().toString(36) + Math.random().toString(36).slice(2,5); });
    (placed.labels || []).forEach(l => { l.id = 'l' + Date.now().toString(36) + Math.random().toString(36).slice(2,5); });
    this.project.components.push(...placed.components);
    this.project.wires.push(...placed.wires);
    if (!this.project.labels) this.project.labels = [];
    // Deduplicate label names: if "RFIN" already exists, rename to "RFIN2", "RFIN3", etc.
    const takenLabels = new Set(this.project.labels.map(l => l.name));
    (placed.labels || []).forEach(l => {
      if (l.name && takenLabels.has(l.name)) {
        const base = l.name.replace(/\d+$/, '');
        let n = 2; while (takenLabels.has(base + n)) n++;
        l.name = base + n;
      }
      takenLabels.add(l.name);
    });
    this.project.labels.push(...(placed.labels || []));
    this._autoConnectAll();
    this.dirty = true; this._saveHist(); this._render(); this._status();
    this.setTool('select');
  }

  _groupGhostH() {
    const { circ, bboxCx, bboxCy, cursor } = this.placeGroupData;
    if (!cursor) return '';
    const dx = cursor.x - bboxCx, dy = cursor.y - bboxCy;
    let h = `<g opacity="0.45">`;
    for (const w of (circ.wires || [])) {
      if (w.points?.length < 2) continue;
      const pts = w.points.map(p => `${p.x+dx},${p.y+dy}`).join(' ');
      h += `<polyline points="${pts}" fill="none" stroke="#4b9cd3" stroke-width="1.5" stroke-linecap="round"/>`;
    }
    for (const c of circ.components) {
      const fc = { ...c, x: c.x + dx, y: c.y + dy };
      h += `<g transform="translate(${fc.x},${fc.y}) rotate(${(fc.rotation||0)*90})">${this._symH(fc,false,false)}</g>`;
    }
    h += `</g>`;
    return h;
  }

  // Run _autoConnectPorts for every component — call after loading a full circuit
  _autoConnectAll() {
    for (const comp of this.project.components) this._autoConnectPorts(comp);
  }

  // Returns true when `wire` is a zero-length dot-wire whose position lies on the
  // interior of another (non-dot) wire segment — i.e. it is a T-junction marker,
  // NOT a real endpoint-to-endpoint connection.  Such wires must not be dragged
  // with a component because the component is connected to the wire body, not
  // to an endpoint, and the real wire should stay in place.
  _isDotAtInterior(wire) {
    const pts = wire.points;
    if (!pts || pts.length < 2) return false;
    const p0 = pts[0], pN = pts[pts.length - 1];
    // Must be a dot-wire (both ends at same position)
    if (p0.x !== pN.x || p0.y !== pN.y) return false;
    const x = p0.x, y = p0.y;
    const TOL = this.SNAP * 1.2;
    for (const w of this.project.wires) {
      if (w === wire) continue;
      const wpts = w.points;
      if (!wpts || wpts.length < 2) continue;
      // Skip other dot-wires
      if (wpts[0].x === wpts[wpts.length - 1].x && wpts[0].y === wpts[wpts.length - 1].y) continue;
      for (let j = 0; j < wpts.length - 1; j++) {
        const a = wpts[j], b = wpts[j + 1];
        if (a.x === b.x && a.y === b.y) continue;
        const onH = a.y === b.y && Math.abs(y - a.y) < TOL &&
                    x > Math.min(a.x, b.x) + TOL && x < Math.max(a.x, b.x) - TOL;
        const onV = a.x === b.x && Math.abs(x - a.x) < TOL &&
                    y > Math.min(a.y, b.y) + TOL && y < Math.max(a.y, b.y) - TOL;
        if (onH || onV) return true;
      }
    }
    return false;
  }

  // Returns true when the endpoint `endPt` of `wire` is part of a pass-through bus at `port`.
  // A pass-through bus means another wire also has an endpoint at `port` and extends in the
  // OPPOSITE direction — i.e. together the two stubs form a straight wire through the component.
  // In that case the component is sitting on the wire's center, not at a true terminus,
  // so the stubs should NOT be dragged with the component.
  // wireId: id of the wire being tested. endPt: {x,y} of the endpoint at the port.
  // Returns true if another wire at the same port points in the opposite direction,
  // meaning this endpoint is part of a collinear pass-through bus — don't drag it.
  _isPassthroughEndpoint(wireId, endPt, port, TOL) {
    // Find the wire and determine which end is at the port, get the far end
    const wire = this.project.wires.find(w => w.id === wireId);
    if (!wire || !wire.points || wire.points.length < 2) return false;
    const wPts = wire.points;
    const isFirst = Math.hypot(wPts[0].x - endPt.x, wPts[0].y - endPt.y) < 0.5;
    const farPt = isFirst ? wPts[wPts.length - 1] : wPts[0];
    // Direction from port outward along this wire
    const dx = farPt.x - port.x, dy = farPt.y - port.y;
    const len = Math.hypot(dx, dy);
    if (len < TOL) return false; // dot-wire — handled by _isDotAtInterior
    for (const w of this.project.wires) {
      if (w.id === wireId) continue;
      if (!w.points || w.points.length < 2) continue;
      const p0 = w.points[0], pN = w.points[w.points.length - 1];
      let otherFar = null;
      if (Math.hypot(p0.x - port.x, p0.y - port.y) < TOL) otherFar = pN;
      else if (Math.hypot(pN.x - port.x, pN.y - port.y) < TOL) otherFar = p0;
      if (!otherFar) continue;
      const odx = otherFar.x - port.x, ody = otherFar.y - port.y;
      const olen = Math.hypot(odx, ody);
      if (olen < TOL) continue;
      // Dot product < -0.9 → opposite directions → collinear pass-through → don't drag
      if ((dx * odx + dy * ody) / (len * olen) < -0.9) return true;
    }
    return false;
  }

  // Reshape a wire so every segment is horizontal or vertical (L-route, horizontal-first).
  _rerouteWire90(wire) {
    if (!wire.points || wire.points.length < 2) return;
    const a = wire.points[0], b = wire.points[wire.points.length - 1];
    if (a.x === b.x || a.y === b.y) {
      wire.points = [{x:a.x,y:a.y},{x:b.x,y:b.y}];
      return;
    }
    wire.points = [{x:a.x,y:a.y},{x:b.x,y:a.y},{x:b.x,y:b.y}];
  }

  // Create a wire when this component's port lands on another port OR on a wire segment body.
  // fromDrag=true  → port was explicitly dragged/placed onto another port → create real wire.
  // fromDrag=false → called on load/undo/redo → only add T-junction dots, never create
  //                  port-to-port wires (avoids spurious connections between nearby symbols).
  _autoConnectPorts(comp, fromDrag = false) {
    const EXACT = 0.5; // ports must be at the exact same coordinate to auto-connect
    const TOL_TJUNC = this.SNAP * 0.8; // looser only for T-junction body detection
    const mkWire = (x1, y1, x2, y2) => {
      const already = this.project.wires.some(w => {
        if (!w.points?.length) return false;
        const a = w.points[0], b = w.points[w.points.length - 1];
        return (Math.hypot(a.x-x1,a.y-y1)<EXACT && Math.hypot(b.x-x2,b.y-y2)<EXACT)
            || (Math.hypot(a.x-x2,a.y-y2)<EXACT && Math.hypot(b.x-x1,b.y-y1)<EXACT);
      });
      if (!already) this.project.wires.push({
        id: 'cw' + Date.now().toString(36) + Math.random().toString(36).slice(2,5),
        points: [{x:x1,y:y1},{x:x2,y:y2}]
      });
    };
    for (const myPort of this._ports(comp)) {
      // ── Port-to-port: real 2-point wire — only when explicitly dragged/placed ──
      if (fromDrag) {
        for (const other of this.project.components) {
          if (other.id === comp.id) continue;
          for (const otherPort of this._ports(other)) {
            if (Math.hypot(myPort.x - otherPort.x, myPort.y - otherPort.y) >= EXACT) continue;
            const connected = this.project.wires.some(w => {
              if (!w.points?.length) return false;
              const a = w.points[0], b = w.points[w.points.length - 1];
              return (Math.hypot(a.x-myPort.x,a.y-myPort.y)<EXACT && Math.hypot(b.x-otherPort.x,b.y-otherPort.y)<EXACT)
                  || (Math.hypot(b.x-myPort.x,b.y-myPort.y)<EXACT && Math.hypot(a.x-otherPort.x,a.y-otherPort.y)<EXACT);
            });
            if (!connected) mkWire(myPort.x, myPort.y, otherPort.x, otherPort.y);
          }
        }
      }
      // ── Port-on-wire-segment (T-junction) — dot-wire marker ──────────────
      for (const wire of this.project.wires) {
        if (!wire.points?.length) continue;
        const pts = wire.points;
        if (pts.some(p => Math.hypot(p.x - myPort.x, p.y - myPort.y) < EXACT)) continue;
        for (let j = 0; j < pts.length - 1; j++) {
          const a = pts[j], b = pts[j + 1];
          if (a.x === b.x && a.y === b.y) continue;
          const onH = a.y === b.y && Math.abs(myPort.y - a.y) < TOL_TJUNC &&
                      myPort.x > Math.min(a.x, b.x) + TOL_TJUNC && myPort.x < Math.max(a.x, b.x) - TOL_TJUNC;
          const onV = a.x === b.x && Math.abs(myPort.x - a.x) < TOL_TJUNC &&
                      myPort.y > Math.min(a.y, b.y) + TOL_TJUNC && myPort.y < Math.max(a.y, b.y) - TOL_TJUNC;
          if (onH || onV) { mkWire(myPort.x, myPort.y, myPort.x, myPort.y); break; }
        }
      }
    }
  }

  _autoRef(t) {
    // Use DESIGNATOR_PREFIX map; for ICs, honour the profile's designator field
    let p = DESIGNATOR_PREFIX[t] || 'X';
    if ((t === 'ic' || !SYMDEFS[t]) && this.placeSlug) {
      const prof = profileCache[this.placeSlug] || (typeof library !== 'undefined' && library[this.placeSlug]);
      if (prof?.designator) p = prof.designator;
    }
    const used = new Set(this.project.components
      .filter(c => c.designator?.startsWith(p) && c.designator.length > p.length)
      .map(c => parseInt(c.designator.slice(p.length))).filter(n => !isNaN(n)));
    let n = 1; while (used.has(n)) n++;
    return p + n;
  }

  _delComp(id) {
    this._saveHist();
    this.project.components = this.project.components.filter(c => c.id !== id);
    if (this.selected?.id === id) this.selected = null;
    this.dirty = true; this._render(); this._status();
  }

  _delWire(id) {
    this._saveHist();
    this.project.wires = this.project.wires.filter(w => w.id !== id);
    if (this.selected?.id === id) this.selected = null;
    this.dirty = true; this._render(); this._status();
  }

  _copySelected() {
    const ids = this.multiSelected.size > 0 ? this.multiSelected
      : this.selected ? new Set([this.selected.id]) : new Set();
    if (!ids.size) return;
    const comps  = this.project.components.filter(c => ids.has(c.id));
    const labels = (this.project.labels || []).filter(l => ids.has(l.id));
    const wires  = this.project.wires.filter(w => ids.has(w.id));
    if (!comps.length && !labels.length && !wires.length) return;
    this._schClipboard = JSON.parse(JSON.stringify({ comps, labels, wires }));
  }

  _pasteClipboard() {
    if (!this._schClipboard) return;
    const { comps, labels, wires } = this._schClipboard;
    if (!comps.length && !labels.length && !wires.length) return;
    this._saveHist();
    const OFFSET = this.GRID * 4; // paste offset so it doesn't land on top
    const idMap = {};
    const newComps = comps.map(c => {
      const nc = JSON.parse(JSON.stringify(c));
      nc.id = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      idMap[c.id] = nc.id;
      nc.x += OFFSET; nc.y += OFFSET;
      return nc;
    });
    const newLabels = labels.map(l => {
      const nl = JSON.parse(JSON.stringify(l));
      nl.id = 'l' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      idMap[l.id] = nl.id;
      nl.x += OFFSET; nl.y += OFFSET;
      return nl;
    });
    const newWires = wires.map(w => {
      const nw = JSON.parse(JSON.stringify(w));
      nw.id = 'w' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      idMap[w.id] = nw.id;
      nw.points = nw.points.map(p => ({ x: p.x + OFFSET, y: p.y + OFFSET }));
      return nw;
    });
    this.project.components.push(...newComps);
    this.project.labels = (this.project.labels || []);
    this.project.labels.push(...newLabels);
    this.project.wires.push(...newWires);
    // Select the pasted items
    this.multiSelected.clear();
    [...newComps, ...newLabels, ...newWires].forEach(x => this.multiSelected.add(x.id));
    this.selected = null;
    this.dirty = true; this._render(); this._status();
  }

  _delSelected() {
    if (this.multiSelected.size > 0) {
      this._saveHist();
      this.project.components = this.project.components.filter(c => !this.multiSelected.has(c.id));
      this.project.labels = (this.project.labels || []).filter(l => !this.multiSelected.has(l.id));
      this.project.wires = this.project.wires.filter(w => !this.multiSelected.has(w.id));
      this.project.noConnects = (this.project.noConnects || []).filter(nc => !this.multiSelected.has(nc.id));
      this.multiSelected.clear(); this.selected = null;
      // Clean up groups whose members were deleted
      if (this.project.groups) {
        for (const grp of this.project.groups) {
          grp.members = grp.members.filter(mid =>
            this.project.components.some(c => c.id === mid) ||
            this.project.wires.some(w => w.id === mid) ||
            (this.project.labels || []).some(l => l.id === mid)
          );
        }
        this.project.groups = this.project.groups.filter(g => g.members.length > 0);
      }
      this.dirty = true; this._render(); this._status(); return;
    }
    if (!this.selected) return;
    if (this.selected.type === 'comp') this._delComp(this.selected.id);
    else if (this.selected.type === 'label') this._delLabel(this.selected.id);
    else if (this.selected.type === 'nc') this._delNC(this.selected.id);
    else this._delWire(this.selected.id);
  }

  _placeLabel(sx, sy) {
    const name = document.getElementById(this.labelInputId)?.value?.trim() || 'NET';
    const s = this._snapPortOrGrid(sx, sy);
    const lbl = { id: 'l' + Date.now().toString(36), name, x: s.x, y: s.y, rotation: 0 };
    this._saveHist();
    if (!this.project.labels) this.project.labels = [];
    this.project.labels.push(lbl);
    this.selected = { type: 'label', id: lbl.id };
    this.dirty = true; this._render(); this._status();
  }

  _hitLabel(sx, sy) {
    const w = this._toW(sx, sy);
    for (const l of (this.project.labels || [])) {
      const tw = (l.name||'?').length * 7 + 12;
      // Hit: dot area + text area (text extends right from dot)
      if (Math.hypot(w.x - l.x, w.y - l.y) <= 8) return l;
      if (w.x >= l.x && w.x <= l.x + tw && w.y >= l.y - 8 && w.y <= l.y + 8) return l;
    }
    return null;
  }

  _delLabel(id) {
    this._saveHist();
    this.project.labels = (this.project.labels || []).filter(l => l.id !== id);
    if (this.selected?.id === id) this.selected = null;
    this.dirty = true; this._render(); this._status();
  }

  _rotateSelected() {
    if (this.multiSelected.size > 0) {
      this._saveHist();
      for (const id of this.multiSelected) {
        const c = this.project.components.find(c => c.id === id);
        if (c) { this._rotateComp(c); continue; }
        const lbl = (this.project.labels || []).find(l => l.id === id);
        if (lbl) lbl.rotation = ((lbl.rotation || 0) + 1) % 4;
      }
      this.dirty = true; this._render(); return;
    }
    if (!this.selected) return;
    if (this.selected.type === 'label') {
      const l = (this.project.labels || []).find(l => l.id === this.selected.id);
      if (l) { l.rotation = ((l.rotation || 0) + 1) % 4; this.dirty = true; this._render(); }
      return;
    }
    if (this.selected.type !== 'comp') return;
    const c = this.project.components.find(c => c.id === this.selected.id);
    if (!c) return;
    this._saveHist();
    this._rotateComp(c);
    this.dirty = true; this._render();
  }

  // Rotate component 90° CW: move exactly-connected wire endpoints to new port
  // positions, then reroute each affected wire to strict 90° segments.
  _rotateComp(c) {
    const oldPorts = this._ports(c);
    c.rotation = ((c.rotation || 0) + 1) % 4;
    const newPorts = this._ports(c);
    const affected = new Set();
    for (let pi = 0; pi < oldPorts.length && pi < newPorts.length; pi++) {
      const op = oldPorts[pi], np = newPorts[pi];
      for (const w of this.project.wires) {
        if (!w.points?.length) continue;
        const p0 = w.points[0], pN = w.points[w.points.length - 1];
        if (Math.hypot(p0.x - op.x, p0.y - op.y) < 0.5) { p0.x = np.x; p0.y = np.y; affected.add(w); }
        if (Math.hypot(pN.x - op.x, pN.y - op.y) < 0.5) { pN.x = np.x; pN.y = np.y; affected.add(w); }
      }
    }
    for (const w of affected) this._rerouteWire90(w);
  }

  // ── Wire ops ──────────────────────────────────────────────────────────────
  _hitWireEndpoint(sx, sy, r = 14) {
    const p = this._toW(sx, sy), rd = r / this.zoom;
    for (const w of this.project.wires) {
      if (!w.points || w.points.length < 2) continue;
      const p0 = w.points[0], pN = w.points[w.points.length - 1];
      if (Math.hypot(p0.x - p.x, p0.y - p.y) <= rd) return { x: p0.x, y: p0.y };
      if (Math.hypot(pN.x - p.x, pN.y - p.y) <= rd) return { x: pN.x, y: pN.y };
    }
    return null;
  }

  _hitWireSegment(sx, sy, r = 12) {
    // Returns a grid-snapped point on a wire segment body (not at endpoints)
    const p = this._toW(sx, sy), rd = r / this.zoom;
    for (const wire of this.project.wires) {
      const pts = wire.points;
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i], b = pts[i + 1];
        if (a.x === b.x && a.y === b.y) continue; // skip dot-wires
        let cx, cy;
        if (a.y === b.y) { // horizontal segment
          const minX = Math.min(a.x, b.x), maxX = Math.max(a.x, b.x);
          const gx = this._snap(p.x);
          if (Math.abs(p.y - a.y) <= rd && gx > minX && gx < maxX && Math.abs(p.y - a.y) + Math.abs(p.x - gx) <= rd) {
            cx = gx; cy = a.y;
          }
        } else if (a.x === b.x) { // vertical segment
          const minY = Math.min(a.y, b.y), maxY = Math.max(a.y, b.y);
          const gy = this._snap(p.y);
          if (Math.abs(p.x - a.x) <= rd && gy > minY && gy < maxY && Math.abs(p.x - a.x) + Math.abs(p.y - gy) <= rd) {
            cx = a.x; cy = gy;
          }
        }
        if (cx !== undefined) return { x: cx, y: cy };
      }
    }
    return null;
  }

  _snapPortOrGrid(sx, sy) {
    // Grid-only snap — no auto-jump to nearby pins.
    // isPort is set only when the snapped grid position lands exactly on a pin
    // so the wire auto-completes on intentional placement, not accidental hover.
    const p = this._toW(sx, sy);
    const s = this._snapPt(p.x, p.y);
    // Check snapped position against all component pins
    for (const c of this.project.components) {
      for (const pt of this._ports(c)) {
        if (Math.hypot(s.x - pt.x, s.y - pt.y) < 0.5)
          return { x: pt.x, y: pt.y, isPort: true };
      }
    }
    // Check snapped position against wire endpoints
    for (const w of this.project.wires) {
      if (!w.points?.length) continue;
      const p0 = w.points[0], pN = w.points[w.points.length - 1];
      if (Math.hypot(s.x - p0.x, s.y - p0.y) < 0.5) return { x: p0.x, y: p0.y, isPort: true };
      if (Math.hypot(s.x - pN.x, s.y - pN.y) < 0.5) return { x: pN.x, y: pN.y, isPort: true };
    }
    // Check net labels
    for (const lbl of (this.project.labels || [])) {
      if (Math.hypot(lbl.x - s.x, lbl.y - s.y) < 0.5)
        return { x: lbl.x, y: lbl.y, isPort: true };
    }
    return { ...s, isPort: false };
  }

  _finishWire() {
    if (this.wirePoints.length < 2) { this.wirePoints = []; this._render(); return; }
    this._saveHist();
    // Deduplicate consecutive identical points
    const pts = this.wirePoints.filter((p, i) => i === 0 || p.x !== this.wirePoints[i-1].x || p.y !== this.wirePoints[i-1].y);
    if (pts.length >= 2) {
      this.project.wires.push({ id: 'w' + Date.now().toString(36), points: pts });
      this.dirty = true;
    }
    this.wirePoints = []; this.wireCursor = null;
    this._render(); this._status();
  }

  // ── Undo/redo ─────────────────────────────────────────────────────────────
  _saveHist() {
    // Debounce net overlay refresh — max one network call per 600 ms so rapid
    // drags / edits don't flood /api/netlist and freeze the UI.
    clearTimeout(this._netRefreshTimer);
    this._netRefreshTimer = setTimeout(() => this._refreshNetOverlay(), 600);
    const s = JSON.stringify({ c: this.project.components, w: this.project.wires, l: this.project.labels || [], g: this.project.groups || [], n: this.project.noConnects || [] });
    this.history = this.history.slice(0, this.historyIdx + 1);
    this.history.push(s);
    if (this.history.length > 60) this.history.shift();
    this.historyIdx = this.history.length - 1;
    this._updateUndoRedo();
  }

  async _refreshNetOverlay() {
    try {
      const res = await fetch('/api/netlist', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          components: this.project.components,
          wires: this.project.wires,
          labels: this.project.labels || [],
          noConnects: this.project.noConnects || []
        })
      });
      const data = await res.json();
      this._cachedNetOverlay = {
        nets: data.nets || [],
        wireToNet: new Map(Object.entries(data.wireToNet || {}))
      };
      // Re-render so the net list panel and any active highlight update immediately.
      this._render();
    } catch(e) {}
  }

  _undo() {
    if (this.historyIdx <= 0) return;
    this.historyIdx--;
    const s = JSON.parse(this.history[this.historyIdx]);
    this.project.components = s.c; this.project.wires = s.w; this.project.labels = s.l || []; this.project.groups = s.g || []; this.project.noConnects = s.n || [];
    this.selected = null; this.multiSelected?.clear();
    this._autoConnectAll();
    this._render(); this._status(); this._updateUndoRedo();
    this._histFlash('Undo');
    // Refresh net overlay after undo so net labels/colours stay in sync
    clearTimeout(this._netRefreshTimer);
    this._netRefreshTimer = setTimeout(() => this._refreshNetOverlay(), 600);
  }

  _redo() {
    if (this.historyIdx >= this.history.length - 1) return;
    this.historyIdx++;
    const s = JSON.parse(this.history[this.historyIdx]);
    this.project.components = s.c; this.project.wires = s.w; this.project.labels = s.l || []; this.project.groups = s.g || []; this.project.noConnects = s.n || [];
    this.selected = null; this.multiSelected?.clear();
    this._autoConnectAll();
    this._render(); this._status(); this._updateUndoRedo();
    this._histFlash('Redo');
    // Refresh net overlay after redo so net labels/colours stay in sync
    clearTimeout(this._netRefreshTimer);
    this._netRefreshTimer = setTimeout(() => this._refreshNetOverlay(), 600);
  }

  _updateUndoRedo() {
    const undoId = this._isEmbedded ? 'acc-btn-undo' : 'btn-undo';
    const redoId = this._isEmbedded ? 'acc-btn-redo' : 'btn-redo';
    const canUndo = this.historyIdx > 0;
    const canRedo = this.historyIdx < this.history.length - 1;
    const set = (id, enabled) => {
      const b = document.getElementById(id);
      if (!b) return;
      b.disabled = !enabled;
      b.style.opacity = enabled ? '' : '0.35';
      b.style.cursor = enabled ? '' : 'default';
    };
    set(undoId, canUndo);
    set(redoId, canRedo);
    if (!this._isEmbedded) {
      const hist = document.getElementById('editor-status-hist');
      if (hist) hist.textContent = canUndo ? `${this.historyIdx} step${this.historyIdx !== 1 ? 's' : ''}` : '';
    }
  }

  _histFlash(label) {
    if (this._isEmbedded) return;
    const el = document.getElementById('editor-status-hist');
    if (!el) return;
    el.textContent = label;
    el.style.color = label === 'Undo' ? '#fbbf24' : '#34d399';
    clearTimeout(this._histFlashTimer);
    this._histFlashTimer = setTimeout(() => {
      el.style.color = '#6c63ff';
      this._updateUndoRedo(); // restore step count
    }, 900);
  }

  // ── View ──────────────────────────────────────────────────────────────────
  _resetView() { this.zoom = 1; this.panX = this._W() / 2 - 300; this.panY = this._H() / 2 - 200; }

  _fit() {
    const cs = this.project.components;
    if (!cs.length) { this._resetView(); this._render(); return; }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const c of cs) {
      const b = this._bbox(c);
      x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y);
      x1 = Math.max(x1, b.x + b.w); y1 = Math.max(y1, b.y + b.h);
    }
    const pad = 80, cw = this._W(), ch = this._H();
    this.zoom = Math.min(cw / (x1 - x0 + pad * 2), ch / (y1 - y0 + pad * 2), 2.5);
    this.panX = cw / 2 - ((x0 + x1) / 2) * this.zoom;
    this.panY = ch / 2 - ((y0 + y1) / 2) * this.zoom;
    this._render();
  }

  // ── Status ────────────────────────────────────────────────────────────────
  _status() {
    if (this._isEmbedded) return; // don't corrupt the main editor's status bar
    const cc = document.getElementById('editor-status-components');
    const wc = document.getElementById('editor-status-wires');
    const sv = document.getElementById('btn-save');
    if (cc) cc.textContent = this.project.components.length + ' components';
    if (wc) wc.textContent = this.project.wires.length + ' wires';
    if (sv) sv.classList.toggle('dirty', this.dirty);
    renderTabs();
    _notifyPcbSync();
  }

  // ── Render ────────────────────────────────────────────────────────────────
  _render() {
    // Schedule at most one DOM update per animation frame — prevents rebuilding
    // the entire SVG innerHTML multiple times per frame during mousemove drags.
    if (this._renderPending) return;
    this._renderPending = true;
    requestAnimationFrame(() => {
      this._renderPending = false;
      this._renderNow();
    });
  }

  _renderNow() {
    if (!this._gp || !this._vg) { this._initSVG(); return; }
    const gs = this.GRID * this.zoom;
    const ox = ((this.panX % gs) + gs) % gs;
    const oy = ((this.panY % gs) + gs) % gs;
    this._gp.setAttribute('x', ox); this._gp.setAttribute('y', oy);
    this._gp.setAttribute('width', gs); this._gp.setAttribute('height', gs);
    const dot = this._gp.querySelector('circle');
    if (dot) { dot.setAttribute('cx', gs); dot.setAttribute('cy', gs); }
    this._vg.setAttribute('transform', `translate(${this.panX},${this.panY}) scale(${this.zoom})`);
    this._hlPortKeysCache = undefined; // invalidate per-render cache
    this._hlWireSetCache = undefined;
    let h = '';
    for (const grp of (this.project.groups || [])) h += this._groupH(grp);
    for (const w of this.project.wires) h += this._wH(w);
    if (this.wirePoints.length > 0) h += this._wpH();
    for (const c of this.project.components) h += this._cH(c);
    for (const l of (this.project.labels || [])) h += this._lblH(l, this.selected?.id === l.id || this.multiSelected.has(l.id));
    if (this.tool === 'place' && this.placeCursor) h += this._ghH();
    if (this.tool === 'placeGroup' && this.placeGroupData?.cursor) h += this._groupGhostH();
    if (this.tool === 'label' && this.labelCursor) h += this._lblGhostH();
    if (this.tool === 'nc' && this.ncCursor) h += this._ncGhostH();
    h += this._ncH();
    h += this._jH();
    h += this._drcH();
    if (this.dragState?.connectedWires?.length) h += this._dragAnchorH();
    if (this.rubberState) h += this._rubberH();
    this._vg.innerHTML = h;
  }

  // Render green "anchor" rings at the pinned (free) ends of wires being stretched
  // during a component drag — gives clear visual feedback that those endpoints are held.
  _dragAnchorH() {
    let h = '';
    const seen = new Set();
    const cWires = this.dragState.connectedWires;
    for (const cw of cWires) {
      if (!cw.otherPtOrig) continue;
      // Skip wires where BOTH ends are connected to the dragged component
      if (cWires.some(c => c.id === cw.id && c !== cw)) continue;
      const key = `${cw.otherPtOrig.x},${cw.otherPtOrig.y}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const { x, y } = cw.otherPtOrig;
      h += `<circle cx="${x}" cy="${y}" r="5.5" fill="none" stroke="#4ade80" stroke-width="1.5" opacity="0.85"/>`;
      h += `<circle cx="${x}" cy="${y}" r="2" fill="#4ade80" opacity="0.9"/>`;
    }
    return h;
  }

  // Returns a Set of wire IDs on the highlighted net via BFS from net port positions.
  // Cached per render pass. Does not require _cachedNetOverlay.wireToNet.
  _highlightedWireSet() {
    if (this._hlWireSetCache !== undefined) return this._hlWireSetCache;
    const portKeys = this._hlPortKeys();
    if (!portKeys) return (this._hlWireSetCache = null);
    const wires = this.project.wires || [];
    // Build endpoint → [wireId] map for BFS
    const endpointMap = new Map(); // "x,y" → Set<wireId>
    for (const w of wires) {
      if (!w.points || w.points.length < 2) continue;
      for (const pt of [w.points[0], w.points[w.points.length - 1]]) {
        const k = `${Math.round(pt.x)},${Math.round(pt.y)}`;
        if (!endpointMap.has(k)) endpointMap.set(k, new Set());
        endpointMap.get(k).add(w.id);
      }
    }
    // BFS starting from all highlighted net port positions
    const visited = new Set(); // wire IDs
    const frontier = new Set(portKeys); // coordinate keys to expand
    const seen = new Set(portKeys);
    while (frontier.size) {
      const next = new Set();
      for (const key of frontier) {
        const wids = endpointMap.get(key) || [];
        for (const wid of wids) {
          if (visited.has(wid)) continue;
          visited.add(wid);
          const w = wires.find(w => w.id === wid);
          if (!w) continue;
          for (const pt of [w.points[0], w.points[w.points.length - 1]]) {
            const k = `${Math.round(pt.x)},${Math.round(pt.y)}`;
            if (!seen.has(k)) { seen.add(k); next.add(k); }
          }
        }
      }
      frontier.clear();
      for (const k of next) frontier.add(k);
    }
    return (this._hlWireSetCache = visited.size ? visited : null);
  }

  // Returns a Set of "x,y" strings for every port on the highlighted net, or null.
  // Result is cached for the duration of one render pass.
  _hlPortKeys() {
    if (this._hlPortKeysCache !== undefined) return this._hlPortKeysCache;
    const hn = this._highlightedNet;
    if (!hn) return (this._hlPortKeysCache = null);
    const keys = new Set();
    if (this._cachedNetOverlay?.nets) {
      const net = this._cachedNetOverlay.nets.find(n => n.name === hn);
      if (net) {
        // Add backend-reported port coords (may use int() truncation)
        for (const p of net.ports) keys.add(`${Math.round(p.x)},${Math.round(p.y)}`);
        // Also add exact frontend world-space port coords to handle any int/float mismatch
        // Find components whose ports are within 2px of a net port position
        const netPts = net.ports.map(p => ({ x: p.x, y: p.y }));
        for (const comp of (this.project.components || [])) {
          for (const fp of this._ports(comp)) {
            if (netPts.some(np => Math.abs(fp.x - np.x) <= 2 && Math.abs(fp.y - np.y) <= 2)) {
              keys.add(`${Math.round(fp.x)},${Math.round(fp.y)}`);
            }
          }
        }
        // Also add label positions with matching net name
        for (const lbl of (this.project.labels || [])) {
          if (lbl.name === hn) keys.add(`${Math.round(lbl.x)},${Math.round(lbl.y)}`);
        }
      }
    }
    // Fallback without overlay: seed from label positions and power symbols
    if (!keys.size) {
      for (const lbl of (this.project.labels || []))
        if (lbl.name === hn) keys.add(`${Math.round(lbl.x)},${Math.round(lbl.y)}`);
      for (const comp of (this.project.components || []))
        if ((comp.symType === 'vcc' && hn === 'VCC') || (comp.symType === 'gnd' && hn === 'GND'))
          for (const fp of this._ports(comp)) keys.add(`${Math.round(fp.x)},${Math.round(fp.y)}`);
    }
    return (this._hlPortKeysCache = keys.size ? keys : null);
  }

  _lblH(lbl, sel) {
    const name = lbl.name || '?';
    const nc = this._labelColor(name);
    const hn = this._highlightedNet;
    const hlKeys = hn ? this._hlPortKeys() : null;
    const onNet = hn && (name === hn || hlKeys?.has(`${Math.round(lbl.x)},${Math.round(lbl.y)}`));
    const opStr = (hn && !sel && !onNet) ? ' opacity="0.15"' : '';
    const r = (sel || onNet) ? 4 : 3;
    const ring = sel
      ? `<circle cx="${lbl.x}" cy="${lbl.y}" r="7" fill="none" stroke="${nc}" stroke-width="1" opacity="0.4"/>`
      : onNet
        ? `<circle cx="${lbl.x}" cy="${lbl.y}" r="9" fill="rgba(250,204,21,0.12)" stroke="#facc15" stroke-width="1.5" opacity="0.9"/>`
        : '';
    return `<g class="se-label" data-id="${esc(lbl.id)}" style="cursor:pointer;"${opStr}>
      ${ring}
      <circle cx="${lbl.x}" cy="${lbl.y}" r="${r}" fill="${nc}"/>
      <text x="${lbl.x + 6}" y="${lbl.y + 4}" font-family="monospace" font-size="9" font-weight="bold" fill="${nc}">${esc(name)}</text>
    </g>`;
  }

  _lblGhostH() {
    const { x, y, isPort } = this.labelCursor;
    const name = document.getElementById(this.labelInputId)?.value || 'NET';
    const nc = this._labelColor(name);
    const snapRing = isPort
      ? `<circle cx="${x}" cy="${y}" r="7" fill="none" stroke="${nc}" stroke-width="1.5" opacity="0.7"/>`
      : '';
    return `<g opacity="0.6">
      ${snapRing}
      <circle cx="${x}" cy="${y}" r="3" fill="${nc}"/>
      <text x="${x + 6}" y="${y + 4}" font-family="monospace" font-size="9" font-weight="bold" fill="${nc}">${esc(name)}</text>
    </g>`;
  }

  // ── No-Connect (NC) marker helpers ────────────────────────────────────────
  // Snap to nearest component port within tolerance; fallback to grid snap
  _snapNearPort(wx, wy) {
    const TOL = this.SNAP * 2.5;
    let best = null, bestD = TOL;
    for (const comp of this.project.components) {
      for (const port of this._ports(comp)) {
        const d = Math.hypot(port.x - wx, port.y - wy);
        if (d < bestD) { bestD = d; best = { x: port.x, y: port.y }; }
      }
    }
    return best || this._snapPt(wx, wy);
  }

  _placeNC(sx, sy) {
    const w = this._toW(sx, sy);
    const pos = this._snapNearPort(w.x, w.y);
    if (!this.project.noConnects) this.project.noConnects = [];
    // Avoid placing duplicate NC on same position
    if (this.project.noConnects.some(nc => Math.hypot(nc.x - pos.x, nc.y - pos.y) < this.SNAP * 0.5)) return;
    this._saveHist();
    this.project.noConnects.push({ id: 'nc' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), x: pos.x, y: pos.y });
    this.dirty = true; this._render(); this._status();
  }

  _ncXPath(x, y, r, sw) {
    return `<line x1="${x-r}" y1="${y-r}" x2="${x+r}" y2="${y+r}" stroke="#ef4444" stroke-width="${sw}" stroke-linecap="round" pointer-events="none"/>` +
           `<line x1="${x+r}" y1="${y-r}" x2="${x-r}" y2="${y+r}" stroke="#ef4444" stroke-width="${sw}" stroke-linecap="round" pointer-events="none"/>`;
  }

  _ncH() {
    if (!this.project.noConnects?.length) return '';
    const r = 5 / this.zoom;
    const sw = 1.8 / this.zoom;
    const selId = this.selected?.type === 'nc' ? this.selected.id : null;
    return this.project.noConnects.map(nc => {
      const ring = nc.id === selId
        ? `<circle cx="${nc.x}" cy="${nc.y}" r="${8/this.zoom}" fill="none" stroke="#818cf8" stroke-width="${1/this.zoom}" opacity="0.7"/>`
        : '';
      return `<g class="se-nc" data-id="${esc(nc.id)}" style="cursor:pointer;">${ring}${this._ncXPath(nc.x, nc.y, r, sw)}</g>`;
    }).join('');
  }

  _ncGhostH() {
    const { x, y } = this.ncCursor;
    const r = 5 / this.zoom;
    const sw = 1.8 / this.zoom;
    return `<g opacity="0.55">${this._ncXPath(x, y, r, sw)}</g>`;
  }

  _hitNC(sx, sy) {
    const w = this._toW(sx, sy);
    const TOL = 8 / this.zoom;
    return (this.project.noConnects || []).find(nc => Math.hypot(nc.x - w.x, nc.y - w.y) < TOL) || null;
  }

  _delNC(id) {
    this._saveHist();
    this.project.noConnects = (this.project.noConnects || []).filter(nc => nc.id !== id);
    this.dirty = true; this._render(); this._status();
  }

  _rubberH() {
    const p0 = this._toW(this.rubberState.sx0, this.rubberState.sy0);
    const p1 = this._toW(this.rubberState.sx1, this.rubberState.sy1);
    const x = Math.min(p0.x, p1.x), y = Math.min(p0.y, p1.y);
    const w = Math.abs(p1.x - p0.x), h = Math.abs(p1.y - p0.y);
    const sw = 1 / this.zoom, da = `${4/this.zoom},${3/this.zoom}`;
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="rgba(108,99,255,0.08)" stroke="#6c63ff" stroke-width="${sw}" stroke-dasharray="${da}" opacity="0.9"/>`;
  }

  _labelColor(name) {
    let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) & 0xffff;
    return `hsl(${(h * 137 + 30) % 360},65%,55%)`;
  }

  _wH(wire) {
    if (wire.points.length < 2) return '';
    const sel = this.selected?.id === wire.id || this.multiSelected.has(wire.id);
    const pts = wire.points.map(p => `${p.x},${p.y}`).join(' ');
    let c, sw, opStr = '';
    if (sel) {
      c = '#818cf8'; sw = 2.5;
    } else if (this._highlightedNet) {
      const hlWires = this._highlightedWireSet();
      if (hlWires?.has(wire.id)) { c = '#facc15'; sw = 2.8; }
      else { c = '#4b9cd3'; sw = 1.8; opStr = ' opacity="0.2"'; }
    } else if (this.showNets) {
      // Color each wire by its assigned net so every wire visually shows it has a net
      const wNet = this._cachedNetOverlay?.wireToNet?.get(wire.id);
      c = wNet ? this._labelColor(wNet) : '#4b9cd3'; sw = 1.8;
    } else {
      c = '#4b9cd3'; sw = 1.8;
    }
    return `<polyline class="se-wire" data-id="${esc(wire.id)}" points="${pts}" fill="none" stroke="${c}" stroke-width="${sw}" stroke-linejoin="round" stroke-linecap="round"${opStr}/>`;
  }

  _wpH() {
    const pts = [...this.wirePoints];
    let snap = '';
    if (this.wireCursor) {
      const last = pts[pts.length - 1];
      pts.push({ x: this.wireCursor.x, y: last.y }, this.wireCursor);
      if (this.wireCursor.isPort) {
        snap = `<circle cx="${this.wireCursor.x}" cy="${this.wireCursor.y}" r="5" fill="none" stroke="#4ade80" stroke-width="1.5" opacity="0.9"/>`;
      }
    }
    return `<polyline points="${pts.map(p=>`${p.x},${p.y}`).join(' ')}" fill="none" stroke="#60a5fa" stroke-width="1.5" stroke-dasharray="4,3" stroke-linecap="round"/>` + snap;
  }

  _cH(comp) {
    const sel = this.selected?.id === comp.id || this.multiSelected.has(comp.id), hov = this.hoveredComp?.id === comp.id;
    const rot = (comp.rotation || 0) * 90;
    const showPorts = hov || this.tool === 'wire' || this.tool === 'place';
    const isIC = comp.symType === 'ic' || !SYMDEFS[comp.symType];
    const lay = isIC ? this._icLayout(comp.slug || '') : this._def(comp.symType);
    const ports = lay.ports;

    // Net highlight: check if any world-space port of this component is on the highlighted net
    const hn = this._highlightedNet;
    let onNet = false;
    if (hn && !sel) {
      const hlKeys = this._hlPortKeys();
      if (hlKeys) onNet = this._ports(comp).some(p => hlKeys.has(`${Math.round(p.x)},${Math.round(p.y)}`));
    }
    const opStr = (hn && !sel && !onNet) ? ' opacity="0.15"' : '';

    let ph = '';
    if (showPorts) for (const p of ports)
      ph += `<circle cx="${p.dx}" cy="${p.dy}" r="3.5" fill="${hov ? '#60a5fa' : 'rgba(96,165,250,0.4)'}"/>`;
    // Net highlight ring (yellow glow behind the symbol when component is on the active net)
    let hlRing = '';
    if (onNet) {
      const hw = lay.w / 2 + 5, hh = lay.h / 2 + 5;
      hlRing = `<rect x="${-hw}" y="${-hh}" width="${hw*2}" height="${hh*2}" fill="rgba(250,204,21,0.12)" stroke="#facc15" stroke-width="1.5" opacity="0.9"/>`;
    }
    // Selection box
    let selBox = '';
    if (sel) {
      const hw = lay.w / 2 + 6, hh = lay.h / 2 + 6;
      selBox = `<rect x="${-hw}" y="${-hh}" width="${hw*2}" height="${hh*2}" fill="none" stroke="#818cf8" stroke-width="1.2" stroke-dasharray="4,3" opacity="0.8"/>`;
    }
    // Label drag handle
    let labelHandle = '';
    const t = comp.symType || 'ic';
    if (sel && t !== 'vcc' && t !== 'gnd') {
      const autoLy = isIC ? -(lay.BOX_H / 2 + 9) : -(lay.h / 2 + 11);
      const lox = comp.labelOffsetX || 0, loy = autoLy + (comp.labelOffsetY || 0);
      labelHandle = `<circle cx="${lox}" cy="${loy}" r="4" fill="#818cf8" opacity="0.5" style="cursor:move"/>`;
    }
    return `<g class="se-comp" data-id="${esc(comp.id)}" transform="translate(${comp.x},${comp.y}) rotate(${rot})"${opStr}>${hlRing}${selBox}${labelHandle}${this._symH(comp,sel,hov)}${ph}</g>`;
  }

  _ghH() {
    const { x, y } = this.placeCursor;
    const fc = { slug:this.placeSlug, symType:this.placeSymType, designator:'?', value: profileCache[this.placeSlug]?.value || profileCache[this.placeSlug]?.part_number || library[this.placeSlug]?.part_number || '', rotation: this.placeRotation };
    const rot = this.placeRotation * 90;
    return `<g transform="translate(${x},${y}) rotate(${rot})" opacity="0.45">${this._symH(fc,false,false)}</g>`;
  }

  _jH() {
    const map = new Map();
    const bump = k => map.set(k, (map.get(k) || 0) + 1);
    for (const wire of this.project.wires)
      for (let i = 0; i < wire.points.length; i++)
        bump(`${wire.points[i].x},${wire.points[i].y}`);

    // T-junction detection: wire endpoint lies on interior of another wire's segment
    const TOL = this.SNAP * 0.8;
    for (const w1 of this.project.wires) {
      const pts1 = w1.points;
      for (const endPt of [pts1[0], pts1[pts1.length - 1]]) {
        for (const w2 of this.project.wires) {
          if (w2 === w1) continue;
          const pts2 = w2.points;
          for (let j = 0; j < pts2.length - 1; j++) {
            const a = pts2[j], b = pts2[j + 1];
            if (a.x === b.x && a.y === b.y) continue;
            const onH = a.y === b.y && Math.abs(endPt.y - a.y) < TOL &&
                        endPt.x > Math.min(a.x, b.x) + TOL && endPt.x < Math.max(a.x, b.x) - TOL;
            const onV = a.x === b.x && Math.abs(endPt.x - a.x) < TOL &&
                        endPt.y > Math.min(a.y, b.y) + TOL && endPt.y < Math.max(a.y, b.y) - TOL;
            if (onH || onV) map.set(`${endPt.x},${endPt.y}`, 2); // force dot
          }
        }
      }
    }

    let h = '';
    for (const [key, cnt] of map) {
      if (cnt < 2) continue;
      const [x, y] = key.split(',').map(Number);
      h += `<circle cx="${x}" cy="${y}" r="4" fill="#4b9cd3"/>`;
    }
    return h;
  }

  // ── Net overlay (IMP-027) ─────────────────────────────────────────────────
  _netOverlayH() {
    const overlay = computeNetOverlay(this);
    const { nets, wireToNet } = overlay;
    if (!nets.length && !wireToNet?.size) return '';
    const POWER_SYM = new Set(['vcc','gnd']);
    let h = '';
    const labeledNets = new Set();

    // Label each net — prefer a non-power port anchor; fall back to any port
    // (this ensures VCC/GND-only nets still get a label)
    for (const net of nets) {
      const anchor = net.ports.find(p => !POWER_SYM.has(p.symType)) || net.ports[0];
      if (!anchor) continue; // wire-only net — handled below via wireToNet
      const nc = this._labelColor(net.name);
      h += `<text x="${anchor.x+4}" y="${anchor.y-6}" font-family="monospace" font-size="7" fill="${nc}" opacity="0.85" pointer-events="none">${esc(net.name)}</text>`;
      labeledNets.add(net.name);
    }

    // Wire-only clusters: label once at wire midpoint if net not yet shown
    if (wireToNet?.size) {
      for (const wire of this.project.wires) {
        const netName = wireToNet.get(wire.id);
        if (!netName || labeledNets.has(netName)) continue;
        const pts = wire.points || [];
        if (!pts.length) continue;
        const mid = pts[Math.floor((pts.length - 1) / 2)];
        const nc = this._labelColor(netName);
        h += `<text x="${mid.x+4}" y="${mid.y-6}" font-family="monospace" font-size="7" fill="${nc}" opacity="0.85" pointer-events="none">${esc(netName)}</text>`;
        labeledNets.add(netName);
      }
    }

    return h;
  }

  // ── Symbol HTML generators ────────────────────────────────────────────────
  _symH(comp, sel, hov) {
    const t = comp.symType || 'ic', s = sel || hov;
    let body = '';
    switch (t) {
      case 'resistor':      body = this._sResH(s); break;
      case 'capacitor':     body = this._sCapH(s, false); break;
      case 'capacitor_pol': body = this._sCapH(s, true); break;
      case 'inductor':      body = this._sIndH(s); break;
      case 'vcc':           body = this._sVccH(comp, s); break;
      case 'gnd':           body = this._sGndH(s); break;
      case 'amplifier':     body = this._sAmpH(comp, s); break;
      case 'opamp':         body = this._sOpAmpH(s); break;
      case 'diode':         body = this._sDiodeH(s, false); break;
      case 'led':           body = this._sDiodeH(s, true); break;
      case 'npn':           body = this._sBJTH(s, true); break;
      case 'pnp':           body = this._sBJTH(s, false); break;
      case 'nmos':          body = this._sMOSH(s, true); break;
      case 'pmos':          body = this._sMOSH(s, false); break;
      default:              return this._sICH(comp, s);
    }
    if (t !== 'vcc' && t !== 'gnd') {
      const def = this._def(t), ly = -(def.h / 2 + 11), lc = s ? '#818cf8' : '#94a3b8';
      // Counter-rotate text so it stays horizontal regardless of component rotation
      const cr = -((comp.rotation || 0) % 4) * 90;
      const lox = comp.labelOffsetX || 0, loy = comp.labelOffsetY || 0;
      body += `<text x="${lox}" y="${ly+loy}" text-anchor="middle" font-family="monospace" font-size="9" font-weight="bold" fill="${lc}" transform="rotate(${cr},${lox},${ly+loy})">${esc(comp.designator||'')}</text>`;
      if (comp.value) body += `<text x="${lox}" y="${ly+loy+10}" text-anchor="middle" font-family="sans-serif" font-size="8" fill="#4b5563" transform="rotate(${cr},${lox},${ly+loy+10})">${esc(comp.value)}</text>`;
    }
    return body;
  }

  _sResH(s) {
    const c = s?'#818cf8':'#94a3b8', bf = s?'rgba(129,140,248,0.1)':'rgba(148,163,184,0.05)';
    return `<line x1="-30" y1="0" x2="-12" y2="0" stroke="${c}" stroke-width="1.5"/>
      <rect x="-12" y="-8" width="24" height="16" fill="${bf}" stroke="${c}" stroke-width="1.5"/>
      <line x1="12" y1="0" x2="30" y2="0" stroke="${c}" stroke-width="1.5"/>`;
  }

  _sCapH(s, pol) {
    const c = s?'#818cf8':'#94a3b8';
    let h = `<line x1="0" y1="-20" x2="0" y2="-5" stroke="${c}" stroke-width="1.8"/>
      <line x1="-12" y1="-5" x2="12" y2="-5" stroke="${c}" stroke-width="1.8"/>`;
    h += pol ? `<path d="M-12,5 Q0,12 12,5" fill="none" stroke="${c}" stroke-width="1.8"/>` :
               `<line x1="-12" y1="5" x2="12" y2="5" stroke="${c}" stroke-width="1.8"/>`;
    h += `<line x1="0" y1="${pol?12:5}" x2="0" y2="20" stroke="${c}" stroke-width="1.8"/>`;
    if (pol) h += `<text x="-14" y="-2" text-anchor="end" font-family="sans-serif" font-size="9" fill="${c}">+</text>`;
    return h;
  }

  _sIndH(s) {
    const c = s?'#818cf8':'#a78bfa';
    return `<line x1="-40" y1="0" x2="-24" y2="0" stroke="${c}" stroke-width="1.5"/>
      <path d="M-24,0 A8,8 0 0,1 -8,0 A8,8 0 0,1 8,0 A8,8 0 0,1 24,0" fill="none" stroke="${c}" stroke-width="1.5"/>
      <line x1="24" y1="0" x2="40" y2="0" stroke="${c}" stroke-width="1.5"/>`;
  }

  _sVccH(comp, s) {
    const c = s?'#818cf8':'#ef4444';
    // Port at dy=20 (bottom). Stub from port up to triangle base.
    return `<line x1="0" y1="20" x2="0" y2="5" stroke="${c}" stroke-width="1.5"/>
      <polygon points="0,-10 -11,5 11,5" fill="${c}"/>
      <text x="0" y="-14" text-anchor="middle" font-family="monospace" font-size="8" font-weight="bold" fill="${c}">${esc(comp.value||'VCC')}</text>`;
  }

  _sGndH(s) {
    const c = s?'#818cf8':'#64748b';
    // Port at dy=-20 (top). Stub from port down to bars.
    return `<line x1="0" y1="-20" x2="0" y2="0" stroke="${c}" stroke-width="1.5"/>
      <line x1="-14" y1="0" x2="14" y2="0" stroke="${c}" stroke-width="1.5"/>
      <line x1="-9" y1="6" x2="9" y2="6" stroke="${c}" stroke-width="1.5"/>
      <line x1="-4" y1="12" x2="4" y2="12" stroke="${c}" stroke-width="1.5"/>`;
  }

  _sAmpH(comp, s) {
    const c = s?'#818cf8':'#8b5cf6', bf = s?'rgba(129,140,248,0.1)':'rgba(139,92,246,0.08)';
    return `<polygon points="-50,-40 50,0 -50,40" fill="${bf}" stroke="${c}" stroke-width="2"/>
      <line x1="0" y1="20" x2="0" y2="40" stroke="${c}" stroke-width="1.5"/>
      <text x="-6" y="4" text-anchor="middle" font-family="monospace" font-size="8" font-weight="bold" fill="${c}">${esc(comp.designator||'U')}</text>`;
  }

  _sOpAmpH(s) {
    const c = s?'#818cf8':'#8b5cf6', bf = s?'rgba(129,140,248,0.1)':'rgba(139,92,246,0.08)';
    return `<polygon points="-50,-40 50,0 -50,40" fill="${bf}" stroke="${c}" stroke-width="2"/>
      <text x="-42" y="-12" text-anchor="start" font-family="sans-serif" font-size="11" font-weight="bold" fill="${s?c:'#22c55e'}">+</text>
      <text x="-42" y="28" text-anchor="start" font-family="sans-serif" font-size="11" font-weight="bold" fill="${s?c:'#ef4444'}">−</text>`;
  }

  _sDiodeH(s, isLed) {
    const c = s?'#818cf8':(isLed?'#f59e0b':'#94a3b8');
    const bf = s?'rgba(129,140,248,0.15)':(isLed?'rgba(245,158,11,0.12)':'rgba(148,163,184,0.08)');
    let h = `<line x1="-30" y1="0" x2="-14" y2="0" stroke="${c}" stroke-width="1.5"/>
      <line x1="14" y1="0" x2="30" y2="0" stroke="${c}" stroke-width="1.5"/>
      <polygon points="-14,0 14,-12 14,12" fill="${bf}" stroke="${c}" stroke-width="1.5"/>
      <line x1="14" y1="-12" x2="14" y2="12" stroke="${c}" stroke-width="1.5"/>`;
    if (isLed) h += `<line x1="-4" y1="-18" x2="8" y2="-26" stroke="${c}" stroke-width="1.2"/>
      <line x1="-10" y1="-14" x2="2" y2="-22" stroke="${c}" stroke-width="1.2"/>
      <polygon points="8,-26 4,-23 5,-28" fill="${c}"/>
      <polygon points="2,-22 -2,-19 -1,-24" fill="${c}"/>`;
    return h;
  }

  _sBJTH(s, isNPN) {
    const c = s?'#818cf8':'#22c55e';
    const [ex,ey,dx,dy] = isNPN ? [20,25,-18,-6] : [20,-25,-18,6];
    return `<line x1="-14" y1="-20" x2="-14" y2="20" stroke="${c}" stroke-width="1.5"/>
      <line x1="-30" y1="0" x2="-14" y2="0" stroke="${c}" stroke-width="1.5"/>
      <line x1="-14" y1="-12" x2="20" y2="-25" stroke="${c}" stroke-width="1.5"/>
      <line x1="-14" y1="12" x2="20" y2="25" stroke="${c}" stroke-width="1.5"/>
      <polygon points="${ex},${ey} ${ex+dx},${ey+dy+4} ${ex+dx+5},${ey+dy-2}" fill="${c}"/>`;
  }

  _sMOSH(s, isN) {
    const c = s?'#818cf8':'#3b82f6', ax = isN?-14:-8, dir = isN?1:-1;
    return `<line x1="-20" y1="-18" x2="-20" y2="18" stroke="${c}" stroke-width="1.5"/>
      <line x1="-30" y1="0" x2="-20" y2="0" stroke="${c}" stroke-width="1.5"/>
      <line x1="-14" y1="-18" x2="-14" y2="18" stroke="${c}" stroke-width="1.5"/>
      <polyline points="-14,-10 20,-10 20,-25" fill="none" stroke="${c}" stroke-width="1.5"/>
      <line x1="-14" y1="0" x2="20" y2="0" stroke="${c}" stroke-width="1.5"/>
      <polyline points="-14,10 20,10 20,25" fill="none" stroke="${c}" stroke-width="1.5"/>
      <polygon points="${ax},0 ${ax-dir*8},-4 ${ax-dir*8},4" fill="${c}"/>`;
  }

  _sICH(comp, s) {
    const lay = this._icLayout(comp.slug || '');
    const { BOX_W, BOX_H, PIN_STUB, ROW_H, PAD_Y, leftPins, rightPins } = lay;
    const bc = s?'#818cf8':'#6c63ff', bf = s?'rgba(129,140,248,0.08)':'rgba(108,99,255,0.05)';
    const ptc = t => ({power:'#fca5a5',gnd:'#94a3b8',input:'#86efac',output:'#93c5fd'}[t]||'#fcd34d');
    let h = `<rect x="${-BOX_W/2}" y="${-BOX_H/2}" width="${BOX_W}" height="${BOX_H}" rx="4" fill="${bf}" stroke="${bc}" stroke-width="1.5"/>`;
    const lox = comp.labelOffsetX || 0, loy = comp.labelOffsetY || 0;
    h += `<text x="${lox}" y="${-BOX_H/2-9+loy}" text-anchor="middle" font-family="monospace" font-size="9" font-weight="bold" fill="${bc}">${esc(comp.designator||'IC')}</text>`;
    if (comp.value) h += `<text x="${lox}" y="${-BOX_H/2+loy}" text-anchor="middle" font-family="sans-serif" font-size="7" fill="#4b5563">${esc((comp.value||'').slice(0,14))}</text>`;
    leftPins.forEach((pin, i) => {
      const y = -(BOX_H/2-PAD_Y)+i*ROW_H, xBox = -BOX_W/2, tc = ptc(pin.type);
      h += `<line x1="${xBox-PIN_STUB}" y1="${y}" x2="${xBox}" y2="${y}" stroke="${tc}" stroke-width="1.2"/>`;
      h += `<text x="${xBox-PIN_STUB-3}" y="${y+2.5}" text-anchor="end" font-family="monospace" font-size="6" fill="#4b5563">${pin.number??''}</text>`;
      h += `<text x="${xBox+4}" y="${y+3}" text-anchor="start" font-family="monospace" font-size="7" font-weight="bold" fill="${tc}">${esc((pin.name||'').slice(0,9))}</text>`;
    });
    rightPins.forEach((pin, i) => {
      const y = -(BOX_H/2-PAD_Y)+i*ROW_H, xBox = BOX_W/2, tc = ptc(pin.type);
      h += `<line x1="${xBox}" y1="${y}" x2="${xBox+PIN_STUB}" y2="${y}" stroke="${tc}" stroke-width="1.2"/>`;
      h += `<text x="${xBox+PIN_STUB+3}" y="${y+2.5}" text-anchor="start" font-family="monospace" font-size="6" fill="#4b5563">${pin.number??''}</text>`;
      h += `<text x="${xBox-4}" y="${y+3}" text-anchor="end" font-family="monospace" font-size="7" font-weight="bold" fill="${tc}">${esc((pin.name||'').slice(0,9))}</text>`;
    });
    return h;
  }

  // ── Groups ────────────────────────────────────────────────────────────────
  _getGroupByMember(id) {
    return (this.project.groups || []).find(g => g.members.includes(id)) || null;
  }

  _groupBBox(group) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, found = false;
    for (const mid of group.members) {
      const comp = this.project.components.find(c => c.id === mid);
      if (comp) {
        const b = this._bbox(comp);
        minX = Math.min(minX, b.x); minY = Math.min(minY, b.y);
        maxX = Math.max(maxX, b.x + b.w); maxY = Math.max(maxY, b.y + b.h);
        found = true; continue;
      }
      const wire = this.project.wires.find(w => w.id === mid);
      if (wire) {
        for (const p of wire.points) {
          minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
          maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y);
          found = true;
        }
        continue;
      }
      const lbl = (this.project.labels || []).find(l => l.id === mid);
      if (lbl) {
        minX = Math.min(minX, lbl.x - 4); minY = Math.min(minY, lbl.y - 8);
        maxX = Math.max(maxX, lbl.x + 80); maxY = Math.max(maxY, lbl.y + 8);
        found = true;
      }
    }
    if (!found) return null;
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  _groupH(group) {
    const bbox = this._groupBBox(group);
    if (!bbox) return '';
    const isSelected = group.members.some(mid => this.multiSelected.has(mid));
    const c = isSelected ? '#6c63ff' : '#3d4268';
    const pad = 14;
    const sw = 1.2 / this.zoom;
    const da = `${6/this.zoom},${3/this.zoom}`;
    const fs = Math.max(7, Math.min(11, 9 / this.zoom));
    return `<rect x="${bbox.x-pad}" y="${bbox.y-pad}" width="${bbox.w+pad*2}" height="${bbox.h+pad*2}" fill="${isSelected?'rgba(108,99,255,0.07)':'rgba(61,66,104,0.04)'}" stroke="${c}" stroke-width="${sw}" stroke-dasharray="${da}" rx="6" pointer-events="none"/>` +
      `<text x="${bbox.x-pad+3}" y="${bbox.y-pad-3}" font-family="monospace" font-size="${fs}" fill="${c}" opacity="0.75" pointer-events="none">${esc(group.name || 'Group')}</text>`;
  }

  // DRC: highlight unconnected pins with a small orange marker
  _drcH() {
    const TOL = this.SNAP * 1.5;
    // Build a set of NC-marked port positions for fast lookup
    const ncPositions = new Set((this.project.noConnects || []).map(nc => `${nc.x},${nc.y}`));
    let h = '';
    for (const comp of this.project.components) {
      if (comp.symType === 'vcc' || comp.symType === 'gnd') continue;
      for (const port of this._ports(comp)) {
        // Skip ports that have a No-Connect marker
        if (ncPositions.has(`${Math.round(port.x)},${Math.round(port.y)}`)) continue;
        // Check if any wire endpoint lands on this port
        const connected = this.project.wires.some(w => {
          if (!w.points?.length) return false;
          const a = w.points[0], b = w.points[w.points.length - 1];
          return Math.hypot(a.x - port.x, a.y - port.y) < TOL ||
                 Math.hypot(b.x - port.x, b.y - port.y) < TOL;
        }) || this.project.components.some(other => {
          if (other.id === comp.id) return false;
          return this._ports(other).some(op => Math.hypot(op.x - port.x, op.y - port.y) < TOL);
        });
        if (!connected) {
          const r = 3.5 / this.zoom;
          h += `<circle cx="${port.x}" cy="${port.y}" r="${r}" fill="none" stroke="#f59e0b" stroke-width="${1.2/this.zoom}" opacity="0.7" pointer-events="none"/>`;
        }
      }
    }
    return h;
  }

  groupSelected() {
    if (this.multiSelected.size < 2) {
      this._showFlash('Select 2+ elements to group');
      return;
    }
    this._saveHist();
    if (!this.project.groups) this.project.groups = [];
    const members = [...this.multiSelected];
    // Remove these items from any existing groups
    for (const grp of this.project.groups) {
      grp.members = grp.members.filter(mid => !members.includes(mid));
    }
    this.project.groups = this.project.groups.filter(g => g.members.length > 0);
    const grpNum = this.project.groups.length + 1;
    const grpId = 'grp_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 4);
    this.project.groups.push({ id: grpId, name: 'Group ' + grpNum, members });
    this.dirty = true;
    this._render();
    this._showFlash('Grouped ' + members.length + ' elements');
  }

  ungroupSelected() {
    if (!this.project.groups?.length) return;
    const toRemove = new Set();
    for (const grp of this.project.groups) {
      if (grp.members.some(mid => this.multiSelected.has(mid))) toRemove.add(grp.id);
    }
    if (this.selected?.id) {
      const grp = this._getGroupByMember(this.selected.id);
      if (grp) toRemove.add(grp.id);
    }
    if (!toRemove.size) { this._showFlash('Nothing to ungroup'); return; }
    this._saveHist();
    this.project.groups = this.project.groups.filter(g => !toRemove.has(g.id));
    this.dirty = true;
    this._render();
    this._showFlash('Ungrouped');
  }

  _renameGroup(grp) {
    const newName = prompt('Group name:', grp.name || 'Group');
    if (newName !== null && newName.trim()) {
      this._saveHist();
      grp.name = newName.trim();
      this.dirty = true;
      this._render();
    }
  }

  _showFlash(msg) {
    if (this._isEmbedded) return;
    const el = document.getElementById('editor-status-hist');
    if (!el) return;
    el.textContent = msg;
    el.style.color = '#34d399';
    clearTimeout(this._histFlashTimer);
    this._histFlashTimer = setTimeout(() => { el.style.color = '#6c63ff'; this._updateUndoRedo(); }, 1400);
  }

  // ── Context menu ──────────────────────────────────────────────────────────
  _showContextMenu(e) {
    this._hideContextMenu();
    const { sx, sy } = this._evPos(e);
    const labelHit = this._hitLabel(sx, sy);
    const compHit = !labelHit ? this._hitComp(sx, sy) : null;
    const wireHit = !compHit && !labelHit ? this._hitWire(sx, sy) : null;
    const hitId = labelHit?.id || compHit?.id;
    let items = [];

    if (this.multiSelected.size > 1) {
      const grps = [...this.multiSelected].map(mid => this._getGroupByMember(mid)).filter(Boolean);
      const uniqueGrps = [...new Map(grps.map(g => [g.id, g])).values()];
      if (uniqueGrps.length === 1 && grps.length === this.multiSelected.size) {
        const grp = uniqueGrps[0];
        items.push({ header: `"${grp.name}"` });
        items.push({ label: 'Rename Group\u2026', action: () => this._renameGroup(grp) });
        items.push({ sep: true });
        items.push({ label: 'Ungroup', kbd: 'Ctrl+\u21e7+G', action: () => this.ungroupSelected() });
        items.push({ sep: true });
        items.push({ label: 'Delete Group', danger: true, action: () => this._delSelected() });
      } else {
        items.push({ label: `Group ${this.multiSelected.size} elements`, kbd: 'Ctrl+G', action: () => this.groupSelected() });
        if (uniqueGrps.length > 0) items.push({ label: 'Ungroup', kbd: 'Ctrl+\u21e7+G', action: () => this.ungroupSelected() });
        items.push({ sep: true });
        items.push({ label: 'Delete Selection', danger: true, action: () => this._delSelected() });
      }
    } else if (hitId || wireHit) {
      const anyId = hitId || wireHit?.id;
      const grp = anyId ? this._getGroupByMember(anyId) : null;
      if (grp) {
        this.multiSelected.clear();
        for (const mid of grp.members) this.multiSelected.add(mid);
        this.selected = null;
        this._render();
        items.push({ header: `"${grp.name}"` });
        items.push({ label: 'Rename Group\u2026', action: () => this._renameGroup(grp) });
        items.push({ sep: true });
        items.push({ label: 'Ungroup', kbd: 'Ctrl+\u21e7+G', action: () => this.ungroupSelected() });
        items.push({ sep: true });
        items.push({ label: 'Delete Group', danger: true, action: () => this._delSelected() });
      } else if (compHit) {
        this.multiSelected.clear(); this.selected = { type: 'comp', id: compHit.id }; this._render();
        items.push({ label: 'Edit Component\u2026', action: () => this._editComp(compHit) });
        items.push({ label: 'Rotate', action: () => this._rotateSelected() });
        items.push({ sep: true });
        items.push({ label: 'Delete', danger: true, action: () => this._delComp(compHit.id) });
      } else if (labelHit) {
        this.multiSelected.clear(); this.selected = { type: 'label', id: labelHit.id }; this._render();
        items.push({ label: 'Edit Label\u2026', action: () => this._editLabel(labelHit) });
        items.push({ sep: true });
        items.push({ label: 'Delete', danger: true, action: () => this._delLabel(labelHit.id) });
      } else if (wireHit) {
        this.multiSelected.clear(); this.selected = { type: 'wire', id: wireHit.id }; this._render();
        items.push({ label: 'Delete Wire', danger: true, action: () => this._delWire(wireHit.id) });
      }
    } else {
      this._cancel(); return;
    }

    if (!items.length) return;
    const menu = document.createElement('div');
    menu.className = 'sch-ctx-menu';
    // Keep menu inside viewport
    const vw = window.innerWidth, vh = window.innerHeight;
    let mx = e.clientX, my = e.clientY;
    menu.style.left = mx + 'px'; menu.style.top = my + 'px';
    for (const item of items) {
      if (item.sep) {
        const d = document.createElement('div'); d.className = 'sch-ctx-sep'; menu.appendChild(d);
      } else if (item.header) {
        const d = document.createElement('div');
        d.style.cssText = 'padding:4px 14px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);';
        d.textContent = item.header; menu.appendChild(d);
      } else {
        const d = document.createElement('div');
        d.className = 'sch-ctx-item' + (item.danger ? ' danger' : '');
        d.innerHTML = `<span>${esc(item.label)}</span>${item.kbd ? `<span class="sch-ctx-kbd">${esc(item.kbd)}</span>` : ''}`;
        d.addEventListener('click', () => { this._hideContextMenu(); item.action(); });
        menu.appendChild(d);
      }
    }
    document.body.appendChild(menu);
    this._ctxMenu = menu;
    // Adjust if off-screen
    requestAnimationFrame(() => {
      const r = menu.getBoundingClientRect();
      if (r.right > vw) menu.style.left = (mx - r.width) + 'px';
      if (r.bottom > vh) menu.style.top = (my - r.height) + 'px';
    });
    setTimeout(() => {
      this._ctxMenuDismiss = ev => { if (!menu.contains(ev.target)) this._hideContextMenu(); };
      document.addEventListener('mousedown', this._ctxMenuDismiss);
    }, 0);
  }

  _hideContextMenu() {
    if (this._ctxMenu) { this._ctxMenu.remove(); this._ctxMenu = null; }
    if (this._ctxMenuDismiss) { document.removeEventListener('mousedown', this._ctxMenuDismiss); this._ctxMenuDismiss = null; }
  }

  _contentBBox(pad = 50) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const c of this.project.components) {
      const lay = (c.symType === 'ic' || !SYMDEFS[c.symType]) ? this._icLayout(c.slug || '') : null;
      const hw = lay ? lay.BOX_W / 2 + lay.PIN_STUB + 20 : (SYMDEFS[c.symType]?.w || 60) / 2 + 20;
      const hh = lay ? lay.BOX_H / 2 + 20 : (SYMDEFS[c.symType]?.h || 60) / 2 + 20;
      minX = Math.min(minX, c.x - hw); minY = Math.min(minY, c.y - hh);
      maxX = Math.max(maxX, c.x + hw); maxY = Math.max(maxY, c.y + hh);
    }
    for (const w of this.project.wires)
      for (const p of w.points) {
        minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
        maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y);
      }
    for (const l of (this.project.labels || []))  {
      minX = Math.min(minX, l.x - 40); minY = Math.min(minY, l.y - 14);
      maxX = Math.max(maxX, l.x + 40); maxY = Math.max(maxY, l.y + 14);
    }
    if (!isFinite(minX)) return { x: 0, y: 0, w: 400, h: 300 };
    return { x: minX - pad, y: minY - pad, w: maxX - minX + 2*pad, h: maxY - minY + 2*pad };
  }

  exportSVGString(darkBg = false) {
    const { x, y, w, h } = this._contentBBox();
    const bg = darkBg ? '#0d0f18' : '#ffffff';
    let out = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x} ${y} ${w} ${h}" width="${w}" height="${h}">`;
    out += `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${bg}"/>`;
    for (const wire of this.project.wires) out += this._wH(wire);
    for (const c of this.project.components) out += this._cH(c);
    for (const l of (this.project.labels || [])) out += this._lblH(l, false);
    out += this._jH();
    out += `</svg>`;
    return out;
  }

}

