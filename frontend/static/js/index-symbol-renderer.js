// ── Layout Example tab ──────────────────────────────────────────────────────
let _leBomData = []; // resolved BOM for sidebar
let _leNetAssign = {}; // { "compId.portKey" -> "NET_NAME" } — extracted from example_circuit wires
let _leProfileMap = {}; // slug -> profile, kept so leGetPadNet can look up IC pins
let _leBoard = null; // current board sent to the LE iframe — used for net auto-correct
let _leLoadedSlug = null; // which slug is currently loaded in the LE iframe

// Cache the board from the LE iframe's leBoardChanged notifications (fired after each drag).
// Also re-validate nets so the warning panel stays up-to-date after add/delete.
let _leNetCheckTimer = null;
window.addEventListener('message', e => {
  if (!e.data || e.data.type !== 'leBoardChanged' || !e.data.board) return;
  const frame = document.getElementById('le-frame');
  if (!frame || e.source !== frame.contentWindow) return;
  _leBoard = e.data.board;
  // Debounce net check — avoid running on every pixel of a drag
  clearTimeout(_leNetCheckTimer);
  _leNetCheckTimer = setTimeout(() => {
    if (_leBomData?.length && Object.keys(_leNetAssign).length) {
      const mm = leValidateNets(_leBoard, _leBomData);
      leShowNetWarning(mm);
      leUpdateTabBadge(mm.length);
    }
  }, 300);
});

// Toolbar Save button inside the PCB iframe posts leSave — trigger the real save.
window.addEventListener('message', e => {
  if (!e.data || e.data.type !== 'leSave') return;
  if (typeof leSaveLayout === 'function') leSaveLayout();
});

// Component → PCB footprint name.  Accepts either a slug string (legacy) or a component object.
// Resolution order: slug-based builtins → symType-based builtins → profile.footprint field
function leFootprintFor(compOrSlug, profileMap) {
  const slug    = typeof compOrSlug === 'string' ? compOrSlug : (compOrSlug?.slug || '');
  const symType = typeof compOrSlug === 'object' ? (compOrSlug?.symType || '') : '';
  // ── Builtin passives (slug or symType) ──────────────────────────────────
  if (slug === 'CAPACITOR' || slug === 'CAPACITOR_POL' || symType === 'capacitor' || symType === 'capacitor_pol') return '0402';
  if (slug === 'INDUCTOR'  || symType === 'inductor')   return '0402';
  if (slug === 'RESISTOR'  || symType === 'resistor')   return '0402';
  if (slug === 'LED'       || symType === 'led')        return '0402';
  if (slug === 'DIODE'     || symType === 'diode')      return 'SOD-123';
  // ── Discrete semiconductors / analog by symType ──────────────────────────
  if (symType === 'npn' || symType === 'pnp')           return 'SOT-23';
  if (symType === 'nmos' || symType === 'pmos')         return 'TO-220';
  if (symType === 'opamp')                              return 'SOIC-8';
  if (symType === 'amplifier') return (profileMap?.[slug]?.footprint) || 'MCLP-4';
  // ── IC / unknown: profile footprint field ───────────────────────────────
  return profileMap?.[slug]?.footprint || null;
}

// Fetch footprint pads JSON from server
const _fpCache = {};
async function leFetchFp(fpName) {
  if (!fpName) return null;
  if (_fpCache[fpName]) return _fpCache[fpName];
  const fp = await fetch(`/api/footprints/${encodeURIComponent(fpName)}`).then(r => r.ok ? r.json() : null).catch(() => null);
  if (fp) _fpCache[fpName] = fp;
  return fp;
}

// Resolve full BOM: schematic components → footprints + pads
// Excludes VCC/GND power symbols — they are schematic-only, not PCB components
// Extract net assignments from example_circuit wires using union-find (mirrors SchematicImporter.convert)
function leExtractNets(circuit, profileMap) {
  const SNAP = 12, PIN_STUB = 40, ROW_H = 20, BOX_W = 120, PAD_Y = 16;
  const comps = circuit.components || [], wires = circuit.wires || [];
  // Inject wire.net properties as virtual labels (mirrors _refreshNetOverlay) so
  // nets set via the wire net input are recognised by the union-find below.
  const labels = [...(circuit.labels || [])];
  for (const w of wires) {
    if (!w.net) continue;
    const pt = w.points?.[0];
    if (pt) labels.push({ id: '_wn_' + w.id, name: w.net, x: pt.x, y: pt.y });
  }
  const rotPort = (dx, dy, r) => r===1?[dy,-dx]:r===2?[-dx,-dy]:r===3?[-dy,dx]:[dx,dy];
  const ports = [];
  for (const c of comps) {
    if (c.symType === 'ic' || c.symType === 'amplifier') {
      const rawPins = (profileMap[c.slug])?.pins || [];
      const icPins = [...rawPins].sort((a,b) => (a.number||0)-(b.number||0));
      const half = Math.ceil(icPins.length/2);
      const leftPins = icPins.slice(0, half);
      const rightPins = [...icPins.slice(half)].reverse();
      const BOX_H = Math.max(leftPins.length, rightPins.length, 1)*ROW_H + 2*PAD_Y;
      leftPins.forEach((pin, i) => {
        const pdy = -(BOX_H/2 - PAD_Y) + i*ROW_H;
        const [rdx, rdy] = rotPort(-(BOX_W/2+PIN_STUB), pdy, c.rotation||0);
        ports.push({ id:`${c.id}.p${icPins.indexOf(pin)}`, compId:c.id, stype:c.symType, pinNum:pin.number, pname:pin.name||`P${pin.number}`, x:c.x+rdx, y:c.y+rdy });
      });
      rightPins.forEach((pin, i) => {
        const pdy = -(BOX_H/2 - PAD_Y) + i*ROW_H;
        const [rdx, rdy] = rotPort(BOX_W/2+PIN_STUB, pdy, c.rotation||0);
        ports.push({ id:`${c.id}.p${icPins.indexOf(pin)}`, compId:c.id, stype:c.symType, pinNum:pin.number, pname:pin.name||`P${pin.number}`, x:c.x+rdx, y:c.y+rdy });
      });
    } else {
      const def = SYMDEFS[c.symType] || { ports:[] };
      def.ports.forEach((p, i) => {
        const [rdx, rdy] = rotPort(p.dx, p.dy, c.rotation||0);
        ports.push({ id:`${c.id}.${i}`, compId:c.id, stype:c.symType, pinNum:null, pname:p.name, x:c.x+rdx, y:c.y+rdy });
      });
    }
  }
  const par = {};
  const find = id => { if (par[id] !== id) par[id] = find(par[id]); return par[id]; };
  const union = (a, b) => { par[find(a)] = find(b); };
  ports.forEach(p => par[p.id] = p.id);
  const wns = [];
  for (const w of wires) {
    const pts = w.points || []; if (pts.length < 2) continue;
    const ns = pts.map((_, i) => `w${w.id}.${i}`);
    ns.forEach(n => par[n] = n);
    for (let i = 0; i < ns.length-1; i++) union(ns[i], ns[i+1]);
    wns.push({ pts, ns });
  }
  for (const {pts,ns} of wns) for (let wi = 0; wi < pts.length; wi++) for (const p of ports)
    if (Math.hypot(pts[wi].x-p.x, pts[wi].y-p.y) <= SNAP) union(ns[wi], p.id);
  const lblNodes = {};
  for (const lbl of labels) {
    const ln = `lbl_${lbl.id}`; par[ln] = ln;
    for (const {pts,ns} of wns) for (let wi = 0; wi < pts.length; wi++)
      if (Math.hypot(pts[wi].x-lbl.x, pts[wi].y-lbl.y) <= SNAP) union(ln, ns[wi]);
    for (const p of ports) if (Math.hypot(p.x-lbl.x, p.y-lbl.y) <= SNAP) union(ln, p.id);
    if (!lblNodes[lbl.name]) lblNodes[lbl.name] = ln; else union(ln, lblNodes[lbl.name]);
  }
  // No Connect markers — ports at NC positions get net "NC"
  const ncSet = new Set();
  for (const nc of (circuit.noConnects || [])) {
    ncSet.add(`${nc.x},${nc.y}`);
  }
  const ncPortIds = new Set();
  if (ncSet.size > 0) {
    for (const p of ports) {
      for (const ncKey of ncSet) {
        const [nx, ny] = ncKey.split(',').map(Number);
        if (Math.hypot(p.x - nx, p.y - ny) <= SNAP * 1.5) { ncPortIds.add(p.id); break; }
      }
    }
  }

  const groups = {};
  for (const p of ports) { const r = find(p.id); (groups[r] || (groups[r]=[])).push(p); }
  const assign = {}; let auto = 1;
  for (const grp of Object.values(groups)) {
    if (!grp.length) continue;
    // If ALL ports in this group are NC-marked, assign "NC"
    const allNC = grp.every(p => ncPortIds.has(p.id));
    if (allNC && ncPortIds.size > 0) {
      for (const p of grp) { if (p.stype==='vcc'||p.stype==='gnd') continue; assign[p.id]='NC'; }
      continue;
    }
    let name = null;
    for (const p of grp) {
      if (p.stype === 'vcc') { const c = comps.find(x => x.id===p.compId); name = c?.value?.trim()||'VCC'; }
      else if (p.stype === 'gnd') { const c = comps.find(x => x.id===p.compId); if (!name) name = c?.value?.trim()||'GND'; }
    }
    if (!name) for (const lbl of labels) { const ln=`lbl_${lbl.id}`; if (par[ln]&&find(ln)===find(grp[0].id)){name=lbl.name;break;} }
    if (!name) name=`N${auto++}`;
    for (const p of grp) {
      if (p.stype==='vcc'||p.stype==='gnd') continue;
      // Individual NC-marked ports get "NC" even if they share a group with non-NC ports
      assign[p.id] = ncPortIds.has(p.id) ? 'NC' : name;
    }
  }
  return assign;
}

// Look up net for a given BOM component + footprint pad
function leGetPadNet(comp, pad) {
  if (comp.symType === 'ic' || comp.symType === 'amplifier') {
    // Must sort pins the same way leRefreshNetAssign and leExtractNets do
    const icPins = [...((_leProfileMap[comp.slug])?.pins || [])].sort((a, b) => (a.number || 0) - (b.number || 0));
    const pi = icPins.findIndex(pin => String(pin.number) === String(pad.number));
    if (pi >= 0 && _leNetAssign[`${comp.id}.p${pi}`]) return _leNetAssign[`${comp.id}.p${pi}`];
    const pi2 = icPins.findIndex(pin => pin.name === pad.name);
    if (pi2 >= 0 && _leNetAssign[`${comp.id}.p${pi2}`]) return _leNetAssign[`${comp.id}.p${pi2}`];
  } else {
    const padNum = parseInt(pad.number) - 1;
    if (!isNaN(padNum) && padNum >= 0 && _leNetAssign[`${comp.id}.${padNum}`]) return _leNetAssign[`${comp.id}.${padNum}`];
    const def = SYMDEFS[comp.symType] || { ports:[] };
    const pi = def.ports.findIndex(p => p.name === pad.name);
    if (pi >= 0 && _leNetAssign[`${comp.id}.${pi}`]) return _leNetAssign[`${comp.id}.${pi}`];
  }
  return '';
}

// Validate layout example nets against schematic-derived nets.
// Returns array of { ref, padNum, padName, expected, actual } mismatches.
function leValidateNets(board, bomData) {
  if (!board?.components?.length || !bomData?.length || !Object.keys(_leNetAssign).length) return [];
  const mismatches = [];
  for (const comp of board.components) {
    const bom = bomData.find(b => (b.designator || b.id) === comp.ref);
    if (!bom) continue; // extra component in layout not in schematic — skip
    for (const pad of comp.pads || []) {
      const expected = leGetPadNet(bom, pad) || '';
      const actual = (pad.net || '').trim();
      if (expected === actual) continue;
      // Ignore both-unconnected
      if (!expected && !actual) continue;
      mismatches.push({ ref: comp.ref, padNum: pad.number, padName: pad.name || pad.number, expected: expected || '(unconnected)', actual: actual || '(unconnected)' });
    }
  }
  return mismatches;
}

// Get the live circuit from appCircuitEditor with wire.net injected as virtual labels.
// Returns null if the editor isn't ready or has no content.
function leGetLiveCircuit() {
  if (!window.appCircuitEditor) return null;
  const p = appCircuitEditor.project;
  if (!p?.components?.length && !p?.wires?.length) return null;
  const labels = [...(p.labels || [])];
  for (const w of (p.wires || [])) {
    if (!w.net) continue;
    const pt = w.points?.[0];
    if (pt) labels.push({ id: '_wn_' + w.id, name: w.net, x: pt.x, y: pt.y, rotation: 0 });
  }
  return { components: p.components || [], wires: p.wires || [], labels, noConnects: p.noConnects || [] };
}

// Update the Layout Example tab button badge to reflect net mismatch count.
function leUpdateTabBadge(mismatchCount) {
  const btn = document.getElementById('tab-btn-layout-example');
  if (!btn) return;
  if (mismatchCount > 0) {
    btn.innerHTML = `Layout Example <span style="color:#f59e0b;font-size:10px;vertical-align:middle;" title="${mismatchCount} net mismatch${mismatchCount !== 1 ? 'es' : ''} — open to fix">⚠ ${mismatchCount}</span>`;
  } else {
    btn.textContent = 'Layout Example';
    btn.title = '';
  }
}

// Build _leNetAssign by calling /api/netlist — same backend the schematic editor uses.
// Converts the response {nodeId: "compId::portName"} back to the leGetPadNet key format.
async function leRefreshNetAssign(circuit) {
  if (!circuit?.components?.length) { _leNetAssign = {}; return; }
  const labels = [...(circuit.labels || [])];
  for (const w of (circuit.wires || [])) {
    if (!w.net) continue;
    const pt = w.points?.[0];
    if (pt) labels.push({ id: '_wn_' + w.id, name: w.net, x: pt.x, y: pt.y, rotation: 0 });
  }
  try {
    const res = await fetch('/api/netlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ components: circuit.components, wires: circuit.wires || [], labels, noConnects: circuit.noConnects || [] })
    });
    if (!res.ok) throw new Error('netlist error');
    const data = await res.json();
    const assign = {};
    const compMap = {};
    for (const c of circuit.components) compMap[c.id] = c;
    for (const net of (data.nets || [])) {
      for (const port of (net.ports || [])) {
        if (!port.nodeId) continue;
        const sep = port.nodeId.indexOf('::');
        if (sep < 0) continue;
        const cid = port.nodeId.slice(0, sep);
        const portName = port.nodeId.slice(sep + 2);
        const comp = compMap[cid];
        if (!comp) continue;
        if (comp.symType === 'ic' || comp.symType === 'amplifier') {
          const icPins = [...(_leProfileMap[comp.slug]?.pins || [])].sort((a, b) => (a.number || 0) - (b.number || 0));
          const pi = icPins.findIndex(pin => (pin.name || `P${pin.number}`) === portName);
          if (pi >= 0) assign[`${cid}.p${pi}`] = net.name;
        } else {
          const def = SYMDEFS[comp.symType] || { ports: [] };
          const pi = def.ports.findIndex(p => p.name === portName);
          if (pi >= 0) assign[`${cid}.${pi}`] = net.name;
        }
      }
    }
    _leNetAssign = assign;
  } catch (e) {
    _leNetAssign = leExtractNets(circuit, _leProfileMap); // fallback
  }
}

// Re-check layout example nets against the live Schematic Example nets.
// Updates the tab badge and, if the Layout Example tab is open, also refreshes the warning panel.
// Called after any net rename in appCircuitEditor.
async function leCheckNetsLive() {
  if (!_leBoard || !_leBomData?.length) return;
  if (!window.selectedSlug || _leLoadedSlug !== window.selectedSlug) return;
  const liveCircuit = leGetLiveCircuit();
  if (liveCircuit) await leRefreshNetAssign(liveCircuit);
  const mismatches = leValidateNets(_leBoard, _leBomData);
  leUpdateTabBadge(mismatches.length);
  if (typeof currentProfileTab !== 'undefined' && currentProfileTab === 'layout-example') {
    leShowNetWarning(mismatches);
  }
}

// Show or clear the net-mismatch table below the BOM strip
function leShowNetWarning(mismatches) {
  const panel = document.getElementById('le-net-warning');
  const table = document.getElementById('le-net-warning-list');
  if (!panel || !table) return;
  if (!mismatches.length) { panel.style.display = 'none'; return; }
  // Update count badge
  const countEl = document.getElementById('le-net-mm-count');
  if (countEl) countEl.textContent = `${mismatches.length} pad${mismatches.length !== 1 ? 's' : ''} have wrong nets`;
  const td = 'padding:2px 6px;border-bottom:1px solid rgba(239,68,68,0.1);white-space:nowrap;';
  const rows = mismatches.slice(0, 60).map((m, i) =>
    `<tr id="le-mm-row-${i}">` +
    `<td style="${td}color:#ef4444;">✕</td>` +
    `<td style="${td}color:#fca5a5;font-weight:600;">${esc(m.ref)}</td>` +
    `<td style="${td}color:var(--text-muted);">${esc(m.padName)}</td>` +
    `<td style="${td}color:#f87171;">${esc(m.actual)}</td>` +
    `<td style="${td}color:var(--text-muted);">→</td>` +
    `<td style="${td}color:#86efac;">${esc(m.expected)}</td>` +
    `<td style="${td}"><button onclick="leAutoCorrectPad(${JSON.stringify(m.ref)},${JSON.stringify(m.padNum)},${JSON.stringify(m.expected)},${i})" ` +
    `style="background:rgba(134,239,172,0.12);border:1px solid rgba(134,239,172,0.35);border-radius:3px;color:#86efac;padding:0 6px;font-size:10px;cursor:pointer;line-height:16px;">Fix</button></td>` +
    `</tr>`
  ).join('');
  const more = mismatches.length > 60
    ? `<tr><td colspan="7" style="${td}color:var(--text-muted);">… and ${mismatches.length - 60} more</td></tr>`
    : '';
  table.innerHTML = rows + more;
  panel.style.display = 'block';
}

// Fix a single pad's net in _leBoard and reload iframe
// After any pad-net correction, rename traces whose endpoints touch the corrected pads.
// Builds a world-position → net map from all pads, then walks each trace segment:
// if an endpoint lands on a pad the trace inherits that pad's net.
function leFixTraceNets(board) {
  if (!board) return;
  const EPS = 0.4; // mm
  // Build pad world-position → net lookup (after pad nets have been corrected)
  const padPts = [];
  for (const c of (board.components || [])) {
    const r = (c.rotation || 0) * Math.PI / 180;
    for (const p of (c.pads || [])) {
      if (!p.net) continue;
      const wx = p.x * Math.cos(r) - p.y * Math.sin(r) + c.x;
      const wy = p.x * Math.sin(r) + p.y * Math.cos(r) + c.y;
      padPts.push({ x: wx, y: wy, net: p.net });
    }
  }
  for (const tr of (board.traces || [])) {
    outer: for (const s of (tr.segments || [])) {
      for (const pt of [s.start, s.end]) {
        if (!pt) continue;
        for (const pp of padPts) {
          if (Math.hypot(pt.x - pp.x, pt.y - pp.y) < EPS) {
            tr.net = pp.net;
            break outer; // stop as soon as we find the connected pad
          }
        }
      }
    }
  }
}

function leAutoCorrectPad(ref, padNum, expectedNet, rowIdx) {
  if (!_leBoard) return;
  // Sync current component positions from the live iframe into _leBoard so we
  // don't reset any moves the user made when we reload the board.
  const frame = document.getElementById('le-frame');
  const liveBoard = frame?.contentWindow?.pcbEditorInstance?.board;
  if (liveBoard?.components) {
    for (const lc of liveBoard.components) {
      const bc = _leBoard.components?.find(c => c.ref === lc.ref);
      if (bc) { bc.x = lc.x; bc.y = lc.y; bc.rotation = lc.rotation; }
    }
  }
  const comp = _leBoard.components?.find(c => c.ref === ref);
  if (!comp) return;
  const pad = comp.pads?.find(p => String(p.number) === String(padNum) || p.name === padNum);
  if (pad) pad.net = expectedNet;
  leFixTraceNets(_leBoard);
  // Dim the row to indicate fixed
  const row = document.getElementById(`le-mm-row-${rowIdx}`);
  if (row) row.style.opacity = '0.35';
  frame?.contentWindow?.postMessage({ type: 'loadBoard', board: _leBoard, hideBoardOutline: true }, '*');
}

// Fix all mismatched pads in _leBoard at once
function leAutoCorrectAllNets() {
  if (!_leBoard) return;
  // Sync current component positions from the live iframe into _leBoard so we
  // don't reset any moves the user made when we reload the board.
  const frame = document.getElementById('le-frame');
  const liveBoard = frame?.contentWindow?.pcbEditorInstance?.board;
  if (liveBoard?.components) {
    for (const lc of liveBoard.components) {
      const bc = _leBoard.components?.find(c => c.ref === lc.ref);
      if (bc) { bc.x = lc.x; bc.y = lc.y; bc.rotation = lc.rotation; }
    }
  }
  const mismatches = leValidateNets(_leBoard, _leBomData);
  let fixed = 0;
  for (const m of mismatches) {
    const comp = _leBoard.components?.find(c => c.ref === m.ref);
    const pad = comp?.pads?.find(p => String(p.number) === String(m.padNum) || p.name === m.padNum);
    if (pad) { pad.net = m.expected; fixed++; }
  }
  // Rebuild nets array from corrected pads
  const netMap = {};
  (_leBoard.components || []).forEach(c => (c.pads || []).forEach(p => {
    if (p.net) (netMap[p.net] || (netMap[p.net] = [])).push(`${c.ref}.${p.number}`);
  }));
  _leBoard.nets = Object.entries(netMap).map(([name, pads]) => ({ name, pads }));
  leFixTraceNets(_leBoard);
  frame?.contentWindow?.postMessage({ type: 'loadBoard', board: _leBoard, hideBoardOutline: true }, '*');
  const _mmAll = leValidateNets(_leBoard, _leBomData);
  leShowNetWarning(_mmAll);
  leUpdateTabBadge(_mmAll.length);
  // Brief confirmation in status
  if (fixed > 0) {
    const notesEl = document.getElementById('le-notes');
    if (notesEl) {
      const prev = notesEl.textContent;
      notesEl.style.display = 'block';
      notesEl.style.color = '#86efac';
      notesEl.textContent = `Corrected ${fixed} net${fixed !== 1 ? 's' : ''} from Schematic Example`;
      setTimeout(() => { notesEl.textContent = prev; notesEl.style.color = ''; }, 3000);
    }
  }
}

async function leResolveBom(schComponents, mainSlug, mainProfile) {
  const SKIP_TYPES = new Set(['vcc', 'gnd']);
  const physical = schComponents.filter(c => !SKIP_TYPES.has(c.symType));

  const profileMap = { [mainSlug]: mainProfile };
  // Pre-fetch profiles only for IC / amplifier types (passives & discretes use hardcoded footprints)
  const needsProfile = c => c.slug && !profileMap[c.slug] && (c.symType === 'ic' || c.symType === 'amplifier');
  await Promise.all(physical.filter(needsProfile).map(async c => {
    const cached = profileCache[c.slug];
    if (cached) { profileMap[c.slug] = cached; return; }
    const p = await fetch(`/api/library/${encodeURIComponent(c.slug)}`).then(r => r.ok ? r.json() : null).catch(() => null);
    if (p) { profileMap[c.slug] = p; profileCache[c.slug] = p; }
  }));
  // Fetch all footprints in parallel
  const bom = [];
  for (const comp of physical) {
    const fpName = leFootprintFor(comp, profileMap) || '0402';
    bom.push({ ...comp, fpName, fpData: null });
  }
  await Promise.all(bom.map(async entry => {
    entry.fpData = await leFetchFp(entry.fpName);
  }));
  _leProfileMap = profileMap; // make available to leGetPadNet
  return bom;
}

// Auto-place components onto a board: ICs at centre, passives/discretes in columns around them
function leAutoPlace(bom) {
  const PASSIVE_TYPES = new Set(['resistor','capacitor','capacitor_pol','inductor','led','diode',
                                  'npn','pnp','nmos','pmos']);
  const passives = bom.filter(c => PASSIVE_TYPES.has(c.symType));
  const ics = bom.filter(c => !PASSIVE_TYPES.has(c.symType));

  const total = bom.length;
  const boardW = Math.max(50, Math.ceil(total / 4) * 6 + 20);
  const boardH = 40;
  const cx = boardW / 2, cy = boardH / 2;

  const placed = [];

  // Place ICs at centre
  ics.forEach((c, i) => {
    placed.push({ comp: c, x: cx + (i - (ics.length-1)/2) * 8, y: cy });
  });

  // Passives: split half left, half right; 5 rows per column
  const ROWS = 5, PX = 3.0, PY = 2.5;
  const half = Math.ceil(passives.length / 2);

  passives.slice(0, half).forEach((c, i) => {
    const col = Math.floor(i / ROWS), row = i % ROWS;
    placed.push({ comp: c, x: cx - 12 - col * PX, y: cy - ((Math.min(half, ROWS)-1)*PY)/2 + row*PY });
  });

  passives.slice(half).forEach((c, i) => {
    const col = Math.floor(i / ROWS), row = i % ROWS;
    placed.push({ comp: c, x: cx + 12 + col * PX, y: cy - ((Math.min(passives.length-half, ROWS)-1)*PY)/2 + row*PY });
  });

  return { placed, boardW, boardH };
}

// Build a full PCB board JSON from BOM
function leBuildBoard(bom, title) {
  const { placed, boardW, boardH } = leAutoPlace(bom);
  const components = placed.map(({ comp, x, y }) => ({
    id: (comp.designator || comp.id || 'X') + '_le',
    ref: comp.designator || comp.id || 'X',
    value: comp.value || '',
    footprint: comp.fpName || '0402',
    x: Math.round(x * 100) / 100,
    y: Math.round(y * 100) / 100,
    rotation: 0, layer: 'F',
    pads: (comp.fpData?.pads || [{ number:'1', name:'A', x:-0.5, y:0, type:'smd', shape:'rect', size_x:0.6, size_y:0.6 },
                                  { number:'2', name:'B', x:0.5,  y:0, type:'smd', shape:'rect', size_x:0.6, size_y:0.6 }])
           .map(p => ({ ...p, net: leGetPadNet(comp, p) }))
  }));
  // Build nets array from pad assignments
  const netMap = {};
  components.forEach(c => (c.pads||[]).forEach(p => {
    if (p.net) (netMap[p.net] || (netMap[p.net]=[])).push(`${c.ref}.${p.number}`);
  }));
  const nets = Object.entries(netMap).map(([name, pads]) => ({ name, pads }));
  return {
    version: '1.0', title,
    board: { width: boardW, height: boardH, units: 'mm' },
    components, nets, traces: [], vias: []
  };
}

// Symbol-type display metadata
const _leTypeInfo = {
  resistor:     { label:'R',  color:'#c87533' },
  capacitor:    { label:'C',  color:'#3b82f6' },
  capacitor_pol:{ label:'C+', color:'#3b82f6' },
  inductor:     { label:'L',  color:'#a78bfa' },
  diode:        { label:'D',  color:'#f59e0b' },
  led:          { label:'LED',color:'#22c55e' },
  npn:          { label:'NPN',color:'#f59e0b' },
  pnp:          { label:'PNP',color:'#f59e0b' },
  nmos:         { label:'FET',color:'#f59e0b' },
  pmos:         { label:'FET',color:'#f59e0b' },
  ic:           { label:'IC', color:'#6c63ff' },
  amplifier:    { label:'AMP',color:'#6c63ff' },
  opamp:        { label:'OA', color:'#6c63ff' },
};
const _POWER_TYPES = new Set(['vcc','gnd']);

// Render the BOM sidebar — physical components only (no VCC/GND power symbols)
function leRenderBomPanel(bom) {
  const list = document.getElementById('le-bom-list');
  const countEl = document.getElementById('le-bom-count');
  if (!list) return;

  // Only show components that become real PCB parts
  const physical = bom.map((c, i) => ({ c, i })).filter(({ c }) => !_POWER_TYPES.has(c.symType));

  if (countEl) {
    const addedCount = physical.filter(({ c }) => c._leAdded).length;
    if (!physical.length) countEl.textContent = 'No components in example circuit';
    else if (addedCount === physical.length) countEl.textContent = `${physical.length} component${physical.length!==1?'s':''} in layout — click to highlight`;
    else if (addedCount) countEl.textContent = `${addedCount}/${physical.length} in layout — click to add or highlight`;
    else countEl.textContent = `${physical.length} component${physical.length!==1?'s':''} — click to add`;
  }

  if (!physical.length) {
    list.innerHTML = '<div style="padding:10px 6px;font-size:11px;color:var(--text-muted);">The example circuit has no components yet.</div>';
    return;
  }

  list.innerHTML = physical.map(({ c, i }) => {
    const des = esc(c.designator || c.id || '?');
    const val = esc(c.value || '—');
    const ti  = _leTypeInfo[c.symType] || _leTypeInfo.ic;
    const addedClass = c._leAdded ? ' le-added' : '';
    const addIcon = c._leAdded ? '✓' : '+';
    const tip = c._leAdded
      ? `${des} · ${val} — click to highlight in layout`
      : `${des} · ${val}${c.fpName?' · '+c.fpName:''} — click to add`;
    return `<div class="le-bom-row${addedClass}" id="le-row-${i}" onclick="leAddComponent(${i})"
      title="${tip}"
      style="flex-shrink:0;flex-direction:column;align-items:flex-start;padding:4px 7px;width:auto;min-width:56px;max-width:88px;gap:2px;">
      <div style="display:flex;align-items:center;gap:4px;width:100%;">
        <span style="font-family:monospace;font-size:11px;font-weight:700;color:var(--text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${des}</span>
        <span class="le-add-icon" style="font-size:10px;font-weight:700;color:${c._leAdded?'#22c55e':ti.color};">${addIcon}</span>
      </div>
      <div style="font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;">${val}</div>
    </div>`;
  }).join('');
}

// Add a BOM component to the PCB editor iframe (or select/highlight it if already in layout)
function leAddComponent(bomIdx) {
  const comp = _leBomData[bomIdx];
  const frame = document.getElementById('le-frame');
  if (!comp || !frame?.contentWindow) return;

  const ref = comp.designator || comp.id || 'X';
  const row = document.getElementById('le-row-' + bomIdx);

  if (comp._leAdded) {
    // Already in layout — pan to and select it in the PCB editor
    frame.contentWindow.postMessage({ type: 'selectComponent', ref }, '*');
    if (row) {
      row.style.boxShadow = '0 0 0 2px #6c63ffcc';
      setTimeout(() => { if (row) row.style.boxShadow = ''; }, 700);
    }
    return;
  }

  frame.contentWindow.postMessage({
    type: 'addComponent',
    component: {
      ref,
      value: comp.value || '',
      footprint: comp.fpName || '0402',
      pads: (comp.fpData?.pads || []).map(p => ({ ...p, net: leGetPadNet(comp, p) || '' })),
      rotation: 0, layer: 'F'
    }
  }, '*');

  comp._leAdded = true;
  if (row) {
    row.classList.add('le-added');
    const icon = row.querySelector('.le-add-icon');
    if (icon) icon.textContent = '✓';
    row.style.boxShadow = '0 0 0 2px #22c55e88';
    setTimeout(() => { if (row) row.style.boxShadow = ''; }, 600);
  }
}

function leSetLayerCount(n) {
  if (_leBoard) _leBoard.layerCount = n;
  // Re-render to check compatibility
  if (selectedSlug) {
    _leLoadedSlug = null; // force re-check
    renderLayoutExample(selectedSlug);
  }
}

async function leSaveLayout() {
  const slug = selectedSlug;
  if (!slug) { alert('No component selected.'); return; }

  const frame = document.getElementById('le-frame');
  const leBtn = frame?.contentWindow?.document?.getElementById('toolbar-le-save-btn');
  const btn = leBtn || document.getElementById('le-save-btn');
  const origText = btn ? btn.textContent : '💾 Save';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving…'; }

  try {
    // ── Step 1: get the live board JSON ──────────────────────────────────────
    if (!frame) throw new Error('Layout Example iframe not found — switch to the Layout Example tab first.');

    let board = frame.contentWindow?.pcbEditorInstance?.board ?? null;

    // Fallback 1: leBoardChanged cache (updated on every drag)
    if (!board) board = _leBoard ?? null;

    // Fallback 2: postMessage roundtrip
    if (!board) {
      board = await new Promise((resolve, reject) => {
        const t = setTimeout(() => {
          window.removeEventListener('message', _h);
          reject(new Error('Board not ready — open the Layout Example tab and wait for it to load'));
        }, 4000);
        function _h(e) {
          if (e.data?.type !== 'boardData') return;
          clearTimeout(t);
          window.removeEventListener('message', _h);
          resolve(e.data.board);
        }
        window.addEventListener('message', _h);
        frame.contentWindow?.postMessage({ type: 'getBoard' }, '*');
      });
    }

    if (!board) throw new Error('No board data available');

    // Embed stackup info so the layout example knows its layer requirements
    if (typeof DR !== 'undefined') {
      board.layerCount = DR.layerCount || 2;
      board.stackup = DR.stackup || null;
    }

    // ── Step 2: save to profile + snapshot + set active version ──────────────
    const res = await fetch(`/api/library/${encodeURIComponent(slug)}/layout_example`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(board)
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => String(res.status));
      throw new Error(`Server error ${res.status}: ${txt}`);
    }

    // ── Step 3: update local cache + UI ──────────────────────────────────────
    _leBoard = board;
    _leLoadedSlug = slug;

    if (btn) { btn.textContent = '✓ Saved'; }
    const notes = document.getElementById('le-notes');
    if (notes && !notes.textContent.includes('✓')) notes.textContent += ' · ✓ saved';
    if (typeof loadActiveVersionBadge === 'function') loadActiveVersionBadge(slug);

    setTimeout(() => {
      if (btn) { btn.disabled = false; btn.textContent = origText; }
    }, 2000);

  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = origText; }
    alert('Save failed: ' + e.message);
  }
}

async function renderLayoutExample(slug) {
  if (!slug) return;

  // If this component is already loaded in the iframe, don't re-fetch and reload —
  // that would wipe out any positions the user has changed since the last save.
  // The iframe already holds the live board state.
  if (_leLoadedSlug === slug && _leBoard) {
    const liveCircuit = leGetLiveCircuit();
    if (liveCircuit) await leRefreshNetAssign(liveCircuit);
    leRenderBomPanel(_leBomData);
    const _mmCached = leValidateNets(_leBoard, _leBomData);
    leShowNetWarning(_mmCached);
    leUpdateTabBadge(_mmCached.length);
    return;
  }

  const profile = await fetch(`/api/library/${slug}`, { cache: 'no-store' }).then(r => r.json()).catch(() => null);
  if (!profile) return;

  // Quick guard — bail before doing expensive BOM work if the tab isn't even rendered.
  if (!document.getElementById('le-frame')) return;
  const notesEl = document.getElementById('le-notes');

  const fpName = profile.footprint || '';
  const partNum = profile.part_number || slug;

  // Update header notes
  if (notesEl) {
    const pts = (profile.package_types || []).join(', ');
    notesEl.textContent = [pts, fpName].filter(Boolean).join(' · ');
  }

  // Resolve BOM and extract nets from the same circuit source so component IDs match.
  // Prefer the live appCircuitEditor state (captures wire.net edits not yet saved to server).
  const circuit = profile.example_circuit;
  const liveCircuit = leGetLiveCircuit();
  const activeCircuit = liveCircuit || circuit;
  const schComps = activeCircuit?.components || [];
  _leBomData = await leResolveBom(schComps, slug, profile);
  if (activeCircuit) await leRefreshNetAssign(activeCircuit); else _leNetAssign = {};
  // Reset added state
  _leBomData.forEach(c => { c._leAdded = false; });

  // Decide what board to show
  let board;
  if (profile.layout_example && profile.layout_example.components?.length) {
    // Saved layout takes priority
    board = profile.layout_example;
    if (notesEl) notesEl.textContent += ' · ✓ saved';
    // Mark each BOM component as added if its ref already appears in the saved layout
    const leRefs = new Set((profile.layout_example.components || []).map(c => c.ref));
    _leBomData.forEach(c => { c._leAdded = leRefs.has(c.designator || c.id || ''); });
    // ── Inherit nets from Schematic Example ─────────────────────────
    // Saved layouts may have stale nets from an older schematic version.
    // Silently correct all pad nets to match the live schematic.
    if (Object.keys(_leNetAssign).length) {
      let corrected = 0;
      for (const comp of board.components || []) {
        const bom = _leBomData.find(b => (b.designator || b.id) === comp.ref);
        if (!bom) continue;
        for (const pad of comp.pads || []) {
          const expected = leGetPadNet(bom, pad) || '';
          const actual = (pad.net || '').trim();
          if (expected && expected !== actual) { pad.net = expected; corrected++; }
        }
      }
      if (corrected) {
        // Rebuild nets array from corrected pads
        const netMap = {};
        (board.components || []).forEach(c => (c.pads || []).forEach(p => {
          if (p.net) (netMap[p.net] || (netMap[p.net] = [])).push(`${c.ref}.${p.number}`);
        }));
        board.nets = Object.entries(netMap).map(([name, pads]) => ({ name, pads }));
        leFixTraceNets(board);
        if (notesEl) notesEl.textContent += ` · ${corrected} net${corrected !== 1 ? 's' : ''} corrected from schematic`;
      }
    }
  } else if (_leBomData.length > 0) {
    // Build from schematic BOM
    board = leBuildBoard(_leBomData, partNum + ' Layout Example');
  } else {
    // Fallback: just the IC
    const fp = await leFetchFp(fpName);
    const pads = fp?.pads?.map(p => ({ ...p, net: '' })) || [];
    board = {
      version: '1.0', title: partNum + ' Layout Example',
      board: { width: 40, height: 30, units: 'mm' },
      components: [{ id: 'U1_le', ref: 'U1', value: partNum, footprint: fpName || '?', x: 20, y: 15, rotation: 0, layer: 'F', pads }],
      nets: [], traces: [], vias: []
    };
  }

  // Check layer count compatibility with the current project
  const _leBoardLayers = board.layerCount || 2;
  const _projLayers = (typeof editor !== 'undefined' && editor?.project?.layerCount) ? editor.project.layerCount : null;

  // Populate the LE layer count selector
  const leLcSel = document.getElementById('le-layer-count');
  if (leLcSel) leLcSel.value = _leBoardLayers;

  const mismatchEl = document.getElementById('le-layer-mismatch');
  const blockedOverlay = document.getElementById('le-blocked-overlay');
  const blockedMsg = document.getElementById('le-blocked-msg');
  const leFrameWrap = document.getElementById('le-frame')?.parentElement;

  if (_projLayers && _leBoardLayers !== _projLayers) {
    // BLOCK: layer mismatch — don't load into iframe
    if (mismatchEl) { mismatchEl.style.display = ''; mismatchEl.textContent = `⚠ Example is ${_leBoardLayers}L, project targets ${_projLayers}L`; }
    if (blockedOverlay) { blockedOverlay.style.display = 'flex'; }
    if (blockedMsg) blockedMsg.textContent = `This layout example requires ${_leBoardLayers} copper layers, but your project targets ${_projLayers} layers. They must match.`;
    if (leFrameWrap) leFrameWrap.style.display = 'none';
    leRenderBomPanel(_leBomData);
    _leBoard = board;
    _leLoadedSlug = slug;
    return;
  }

  // No mismatch — clear any previous block
  if (mismatchEl) mismatchEl.style.display = 'none';
  if (blockedOverlay) blockedOverlay.style.display = 'none';
  if (leFrameWrap) leFrameWrap.style.display = '';

  // Render BOM panel now that _leAdded flags are set
  leRenderBomPanel(_leBomData);

  // Validate nets: layout example must match example schematic
  const _mmNew = leValidateNets(board, _leBomData);
  leShowNetWarning(_mmNew);
  leUpdateTabBadge(_mmNew.length);

  // Re-fetch the current frame reference AFTER all awaits — if renderProfile() ran
  // during the async fetch it will have replaced the le-frame with a new element, so
  // the frame captured above is now a detached orphan.  Also abort if the user
  // navigated to a different component while we were fetching.
  if (slug !== selectedSlug) return;
  const liveFrame = document.getElementById('le-frame');
  if (!liveFrame) return;

  const send = () => {
    liveFrame.contentWindow?.postMessage({ type: 'loadBoard', board, hideBoardOutline: true }, '*');
    // Set these AFTER posting so leSaveLayout can't capture a stale board
    // from a previous component while the new loadBoard message is in flight.
    _leBoard = board;
    _leLoadedSlug = slug;
  };

  // Guard against the about:blank race: a freshly created iframe has readyState
  // 'complete' for its initial about:blank document BEFORE pcb.html has loaded.
  // Only call send() immediately when the frame is actually serving pcb.html.
  const isActuallyLoaded = liveFrame.contentDocument?.readyState === 'complete' &&
    liveFrame.contentWindow?.location?.href?.includes('/pcb');
  if (isActuallyLoaded) {
    send();
  } else {
    // Use addEventListener so we don't clobber any existing onload assignment and
    // the handler auto-removes itself after firing once.
    const onLoad = () => { liveFrame.removeEventListener('load', onLoad); send(); };
    liveFrame.addEventListener('load', onLoad);
  }
}

function drawAmplifierAppCircuit(ctx, W, H, p) {
  // Correct topology per datasheet Fig.1 p.4:
  // Signal: RF-IN → C2(10pF) → pad2 → [DUT] → pad8 → C3(100pF) → RF-OUT
  // Bias:   VDD → L2(39nH) → pad8  (DC injected via RF choke into pad8 = DC-IN)
  // Bypass: VDD node → C1(0.01µF) → GND
  // Return: pad2 → L1(18nH) → GND  (gate bias return, shunt inductor)
  // KEY: L2 connects to pad8 (right/output side), NOT pad2 (left/input side)

  const cx = W * 0.42, cy = H * 0.50;
  const tw = 120, th = 108;
  const lx = cx - tw/2, rx = cx + tw/2, ty = cy - th/2, by = cy + th/2;

  function wire(x1,y1,x2,y2,col='#4b5563') {
    ctx.strokeStyle=col; ctx.lineWidth=1.5;
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
  }
  function label(txt,x,y,col='#94a3b8',size=10,align='center') {
    ctx.fillStyle=col; ctx.font=`${size}px monospace`; ctx.textAlign=align; ctx.fillText(txt,x,y);
  }
  function compBox(x,y,w,h,ref,val,col) {
    ctx.fillStyle='rgba(15,17,23,0.7)'; ctx.strokeStyle=col; ctx.lineWidth=1.5;
    ctx.beginPath(); ctx.rect(x-w/2,y-h/2,w,h); ctx.fill(); ctx.stroke();
    ctx.fillStyle=col; ctx.font='bold 10px monospace'; ctx.textAlign='center';
    ctx.fillText(ref,x,y+2);
    ctx.fillStyle='#64748b'; ctx.font='9px sans-serif';
    ctx.fillText(val,x,y+13);
  }

  // ── Triangle (DUT) ──────────────────────────────────────────────────────
  ctx.beginPath(); ctx.moveTo(lx,ty); ctx.lineTo(rx,cy); ctx.lineTo(lx,by); ctx.closePath();
  ctx.fillStyle='rgba(108,99,255,0.1)'; ctx.fill();
  ctx.strokeStyle='#6c63ff'; ctx.lineWidth=2; ctx.stroke();
  ctx.fillStyle='#e2e8f0'; ctx.font='bold 11px monospace'; ctx.textAlign='center';
  ctx.fillText(p.part_number||'AMP', cx-8, cy-3);
  ctx.fillStyle='#4b5563'; ctx.font='9px sans-serif';
  ctx.fillText('PHEMT MMIC', cx-8, cy+9);
  // pad labels
  ctx.fillStyle='#22c55e'; ctx.font='8px monospace'; ctx.textAlign='left';
  ctx.fillText('pad2', lx+3, cy-3);
  ctx.fillStyle='#ef4444'; ctx.textAlign='right';
  ctx.fillText('pad8', rx-3, cy-3);

  // ── INPUT: RF-IN → C2(series) → pad2 ───────────────────────────────────
  const pad2x=lx, pad2y=cy, rfInX=lx-120;
  wire(rfInX, pad2y, rfInX+44, pad2y, '#94a3b8');
  compBox(rfInX+56, pad2y, 24, 30, 'C2', '10pF', '#3b82f6');
  wire(rfInX+68, pad2y, pad2x, pad2y, '#94a3b8');
  ctx.fillStyle='#22c55e'; ctx.font='bold 10px monospace'; ctx.textAlign='right';
  ctx.fillText('RF-IN', rfInX-4, pad2y+4);
  ctx.fillStyle='#64748b'; ctx.font='9px sans-serif'; ctx.fillText('50Ω', rfInX-4, pad2y+14);
  drawPinDot(ctx, rfInX, pad2y, '#22c55e');

  // ── L1 shunt: pad2 junction → L1(18nH) → GND (gate bias return) ────────
  const l1jx=lx-18, l1jy=pad2y;
  wire(pad2x, pad2y, l1jx, l1jy);                    // horizontal tap
  wire(l1jx, l1jy, l1jx, l1jy+20, '#94a3b8');
  compBox(l1jx, l1jy+34, 24, 28, 'L1', '18nH', '#a78bfa');
  wire(l1jx, l1jy+48, l1jx, l1jy+68, '#94a3b8');
  drawGndSymbol(ctx, l1jx, l1jy+68);
  label('GND', l1jx, l1jy+88, '#64748b', 9);
  drawPinDot(ctx, l1jx, l1jy, '#a78bfa');

  // ── OUTPUT: pad8 → C3(series DC block) → RF-OUT ────────────────────────
  const pad8x=rx, pad8y=cy, rfOutX=rx+120;
  wire(pad8x, pad8y, pad8x+38, pad8y, '#94a3b8');
  compBox(pad8x+50, pad8y, 24, 30, 'C3', '100pF', '#3b82f6');
  wire(pad8x+62, pad8y, rfOutX, pad8y, '#94a3b8');
  ctx.fillStyle='#3b82f6'; ctx.font='bold 10px monospace'; ctx.textAlign='left';
  ctx.fillText('RF-OUT', rfOutX+4, pad8y+4);
  ctx.fillStyle='#64748b'; ctx.font='9px sans-serif'; ctx.fillText('50Ω', rfOutX+4, pad8y+14);
  drawPinDot(ctx, rfOutX, pad8y, '#3b82f6');

  // ── BIAS: VDD → L2(39nH) → pad8 (DC-IN on same pin as RF-OUT) ──────────
  // L2 taps into the wire between pad8 and C3 — VDD enters through pad8
  const biasX = pad8x+18, biasY = pad8y;   // tap point on pad8 side of C3
  const l2topY = biasY - 95;
  wire(biasX, biasY, biasX, biasY-20, '#94a3b8');
  compBox(biasX, biasY-34, 24, 28, 'L2', '39nH', '#ef4444');
  wire(biasX, biasY-48, biasX, l2topY, '#94a3b8');
  drawPinDot(ctx, biasX, biasY, '#ef4444');          // junction dot

  // VDD node
  const vddY = l2topY;
  // C1 bypass → GND (to the right of L2)
  const c1x = biasX+52;
  wire(biasX, vddY, c1x, vddY, '#94a3b8');
  wire(c1x, vddY, c1x, vddY+20, '#94a3b8');
  compBox(c1x, vddY+34, 24, 28, 'C1', '0.01µF', '#f59e0b');
  wire(c1x, vddY+48, c1x, vddY+68, '#94a3b8');
  drawGndSymbol(ctx, c1x, vddY+68);
  label('GND', c1x, vddY+88, '#64748b', 9);

  // VDD arrow
  ctx.fillStyle='#ef4444';
  ctx.beginPath(); ctx.moveTo(biasX,vddY-22); ctx.lineTo(biasX-10,vddY-8); ctx.lineTo(biasX+10,vddY-8); ctx.closePath(); ctx.fill();
  ctx.fillStyle='#ef4444'; ctx.font='bold 11px monospace'; ctx.textAlign='center';
  ctx.fillText('VDD', biasX, vddY-26);
  ctx.fillStyle='#64748b'; ctx.font='9px sans-serif';
  ctx.fillText('5V or 6V', biasX, vddY-38);

  // ── GND paddle ─────────────────────────────────────────────────────────
  wire(lx+16, by, lx+16, by+38);
  drawGndSymbol(ctx, lx+16, by+38);
  label('GND (paddle)', lx+16, by+58, '#64748b', 9);

  // ── NC note ────────────────────────────────────────────────────────────
  ctx.fillStyle='#374151'; ctx.font='9px sans-serif'; ctx.textAlign='left';
  ctx.fillText('NC pads 1,3–10 → GND on PCB', 8, H-20);
  ctx.fillText('NC pads 11,12 → float (do NOT ground)', 8, H-10);

  // ── Title ──────────────────────────────────────────────────────────────
  label('Fig.1 — datasheet p.4  |  VDD bias enters via pad8 (DC-IN) through L2', W/2, 12, '#4b5563', 9);
}

function drawPassiveUsage(ctx, W, H, p, type) {
  // Show the component symbol + typical usage note
  const cx = W/2, cy = H/2 - 20;
  if (type === 'resistor')      drawResistor(ctx, W, cy + 20, p);
  else if (type === 'capacitor')     drawCapacitor(ctx, W, cy + 40, p, false);
  else if (type === 'capacitor_pol') drawCapacitor(ctx, W, cy + 40, p, true);
  else if (type === 'inductor')      drawInductor(ctx, W, cy + 20, p);
  else if (type === 'vcc')           drawVcc(ctx, W, cy + 30, p);
  else if (type === 'gnd')           drawGndStandalone(ctx, W, cy + 30, p);

  ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Generic component — add to circuit by pairing with other parts', cx, H - 16);
}

function drawGenericAppCircuit(ctx, W, H, p) {
  const passives = p.required_passives || [];
  const pins = p.pins || [];

  // Draw IC in centre
  const cx = W/2 - 40, cy = H/2;
  const bw = 100, bh = Math.max(80, pins.length * 22 + 20);
  const lx = cx - bw/2, rx = cx + bw/2, ty = cy - bh/2;

  ctx.fillStyle = 'rgba(108,99,255,0.08)'; ctx.strokeStyle = '#6c63ff'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.roundRect(lx, ty, bw, bh, 4); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#e2e8f0'; ctx.font = 'bold 11px monospace'; ctx.textAlign = 'center';
  ctx.fillText(p.part_number||'IC', cx, cy+4);

  // Draw passives to the right
  passives.slice(0,4).forEach((pas, i) => {
    const py = ty + 30 + i * 50;
    const px = rx + 100;
    ctx.strokeStyle = '#4b5563'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(rx, py); ctx.lineTo(px-18, py); ctx.stroke();
    ctx.fillStyle = 'rgba(245,158,11,0.08)'; ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.rect(px-18, py-12, 36, 24); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#f59e0b'; ctx.font = 'bold 10px monospace'; ctx.textAlign = 'center';
    ctx.fillText(pas.value, px, py+4);
    ctx.fillStyle = '#64748b'; ctx.font = '9px sans-serif';
    ctx.fillText(pas.type.slice(0,3).toUpperCase(), px, py-16);
  });

  ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('Application circuit — see datasheet for full detail', W/2, H-10);
}

function detectSymbolType(p) {
  if (p.symbol_type) return p.symbol_type;
  const desc = (p.description || '').toLowerCase();
  const part = (p.part_number || '').toLowerCase();
  if (/amplifier|lna|mmic|gain|low.noise.amp/.test(desc + part)) return 'amplifier';
  if (/op.?amp|operational/.test(desc + part)) return 'opamp';
  return 'ic';
}

// colours
const C = {
  body:    '#6c63ff',
  bodyFill:'rgba(108,99,255,0.08)',
  pin:     '#94a3b8',
  pinPower:'#ef4444',
  pinGnd:  '#64748b',
  pinIn:   '#22c55e',
  pinOut:  '#3b82f6',
  pinBi:   '#f59e0b',
  text:    '#e2e8f0',
  muted:   '#64748b',
  bg:      '#22263a',
};

function pinColor(type) {
  return { power:'#ef4444', gnd:'#64748b', input:'#22c55e', output:'#3b82f6',
           bidirectional:'#f59e0b', passive:'#94a3b8' }[type] || '#94a3b8';
}

// ── Resistor ───────────────────────────────────────────────────────────────
function drawResistor(ctx, W, H, profile) {
  const cx = W/2, cy = H/2;
  const bw = 80, bh = 34, stub = 50;
  // body
  ctx.fillStyle = C.bodyFill; ctx.strokeStyle = C.body; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.rect(cx - bw/2, cy - bh/2, bw, bh); ctx.fill(); ctx.stroke();
  // stubs
  ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx - bw/2 - stub, cy); ctx.lineTo(cx - bw/2, cy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx + bw/2, cy); ctx.lineTo(cx + bw/2 + stub, cy); ctx.stroke();
  drawPinDot(ctx, cx - bw/2 - stub, cy, '#94a3b8');
  drawPinDot(ctx, cx + bw/2 + stub, cy, '#94a3b8');
  // labels
  ctx.fillStyle = C.text; ctx.font = 'bold 13px monospace'; ctx.textAlign = 'center';
  ctx.fillText('R', cx, cy + 5);
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif';
  ctx.fillText('1', cx - bw/2 - stub - 8, cy + 4);
  ctx.fillText('2', cx + bw/2 + stub + 8, cy + 4);
  ctx.fillStyle = C.text; ctx.font = '11px sans-serif';
  ctx.fillText('Resistor', cx, cy - bh/2 - 14);
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif';
  ctx.fillText('Ω  ·  0402/0603/0805/THT', cx, cy + bh/2 + 18);
}

// ── Capacitor ──────────────────────────────────────────────────────────────
function drawCapacitor(ctx, W, H, profile, polarized) {
  const cx = W/2, cy = H/2;
  const gap = 8, plateW = 60, stub = 50;
  // top plate
  ctx.strokeStyle = C.body; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(cx - plateW/2, cy - gap); ctx.lineTo(cx + plateW/2, cy - gap); ctx.stroke();
  // bottom plate — curved if polarized
  if (polarized) {
    ctx.beginPath();
    ctx.arc(cx, cy - gap + 28, 20, Math.PI + 0.4, -0.4);
    ctx.stroke();
  } else {
    ctx.beginPath(); ctx.moveTo(cx - plateW/2, cy + gap); ctx.lineTo(cx + plateW/2, cy + gap); ctx.stroke();
  }
  // stubs
  ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx, cy - gap); ctx.lineTo(cx, cy - gap - stub); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx, cy + gap); ctx.lineTo(cx, cy + gap + stub); ctx.stroke();
  drawPinDot(ctx, cx, cy - gap - stub, polarized ? '#ef4444' : '#94a3b8');
  drawPinDot(ctx, cx, cy + gap + stub, polarized ? '#64748b' : '#94a3b8');
  // + label for polarized
  if (polarized) {
    ctx.fillStyle = '#ef4444'; ctx.font = 'bold 14px sans-serif'; ctx.textAlign = 'left';
    ctx.fillText('+', cx + plateW/2 + 6, cy - gap + 5);
  }
  // pin labels
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(polarized ? '+ (1)' : '1', cx + 16, cy - gap - stub + 4);
  ctx.fillText(polarized ? '- (2)' : '2', cx + 16, cy + gap + stub + 12);
  // name
  ctx.fillStyle = C.text; ctx.font = '11px sans-serif';
  ctx.fillText(polarized ? 'Capacitor (polarized)' : 'Capacitor', cx, cy - gap - stub - 16);
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif';
  ctx.fillText(polarized ? 'µF range  ·  electrolytic / tantalum' : 'pF–µF range  ·  ceramic / film', cx, cy + gap + stub + 26);
}

// ── Inductor ───────────────────────────────────────────────────────────────
function drawInductor(ctx, W, H, profile) {
  const cx = W/2, cy = H/2;
  const r = 13, bumps = 4, stub = 50;
  const totalW = bumps * r * 2;
  const sx = cx - totalW/2;
  ctx.strokeStyle = C.body; ctx.lineWidth = 2;
  // arcs
  for (let i = 0; i < bumps; i++) {
    ctx.beginPath();
    ctx.arc(sx + r + i * r * 2, cy, r, Math.PI, 0, false);
    ctx.stroke();
  }
  // stubs
  ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(sx - stub, cy); ctx.lineTo(sx, cy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(sx + totalW, cy); ctx.lineTo(sx + totalW + stub, cy); ctx.stroke();
  drawPinDot(ctx, sx - stub, cy, '#94a3b8');
  drawPinDot(ctx, sx + totalW + stub, cy, '#94a3b8');
  // labels
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('1', sx - stub - 8, cy + 4);
  ctx.fillText('2', sx + totalW + stub + 8, cy + 4);
  ctx.fillStyle = C.text; ctx.font = '11px sans-serif';
  ctx.fillText('Inductor', cx, cy - r - 18);
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif';
  ctx.fillText('nH–mH range  ·  0402/0603/THT', cx, cy + r + 22);
}

// ── VCC power symbol ───────────────────────────────────────────────────────
function drawVcc(ctx, W, H, profile) {
  const cx = W/2, cy = H/2 + 20;
  const stub = 50;
  ctx.strokeStyle = '#ef4444'; ctx.lineWidth = 2;
  // vertical line up
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - stub); ctx.stroke();
  // arrow head (up)
  ctx.fillStyle = '#ef4444';
  ctx.beginPath();
  ctx.moveTo(cx, cy - stub - 16);
  ctx.lineTo(cx - 12, cy - stub);
  ctx.lineTo(cx + 12, cy - stub);
  ctx.closePath(); ctx.fill();
  // stub down (connection point)
  ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy + 20); ctx.stroke();
  drawPinDot(ctx, cx, cy + 20, '#94a3b8');
  // label
  ctx.fillStyle = '#ef4444'; ctx.font = 'bold 16px monospace'; ctx.textAlign = 'center';
  ctx.fillText('VCC', cx, cy - stub - 24);
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif';
  ctx.fillText('Power rail — all same-name symbols are connected', cx, cy + 44);
}

// ── GND symbol (standalone) ────────────────────────────────────────────────
function drawGndStandalone(ctx, W, H, profile) {
  const cx = W/2, cy = H/2 - 20;
  const stub = 40;
  ctx.strokeStyle = '#64748b'; ctx.lineWidth = 2;
  // vertical stub down
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy + stub); ctx.stroke();
  // three horizontal lines
  [[20, 0], [14, 7], [8, 14]].forEach(([hw, dy]) => {
    ctx.beginPath(); ctx.moveTo(cx - hw, cy + stub + dy); ctx.lineTo(cx + hw, cy + stub + dy); ctx.stroke();
  });
  // connection dot at top
  ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(cx, cy - 20); ctx.lineTo(cx, cy); ctx.stroke();
  drawPinDot(ctx, cx, cy - 20, '#94a3b8');
  // label
  ctx.fillStyle = '#64748b'; ctx.font = 'bold 16px monospace'; ctx.textAlign = 'center';
  ctx.fillText('GND', cx, cy - 30);
  ctx.fillStyle = C.muted; ctx.font = '10px sans-serif';
  ctx.fillText('0V reference — all GND symbols are connected', cx, cy + stub + 36);
}

// ── Amplifier triangle ─────────────────────────────────────────────────────
function drawAmplifier(ctx, W, H, profile) {
  const cx = W / 2, cy = H / 2;
  const tw = 160, th = 140; // triangle width, height
  const lx = cx - tw/2, rx = cx + tw/2;
  const ty = cy - th/2, by = cy + th/2;
  const STUB = 40, FONT = 11;

  // Triangle body
  ctx.beginPath();
  ctx.moveTo(lx, ty);
  ctx.lineTo(rx, cy);
  ctx.lineTo(lx, by);
  ctx.closePath();
  ctx.fillStyle = C.bodyFill;
  ctx.fill();
  ctx.strokeStyle = C.body;
  ctx.lineWidth = 2;
  ctx.stroke();

  // Part label inside
  ctx.fillStyle = C.text;
  ctx.font = 'bold 12px monospace';
  ctx.textAlign = 'center';
  ctx.fillText(profile.part_number || '', cx - 12, cy - 8);
  ctx.font = '10px sans-serif';
  ctx.fillStyle = C.muted;
  ctx.fillText(profile.manufacturer || '', cx - 12, cy + 8);

  // Categorise pins
  const pins = profile.pins || [];
  const rfIn  = pins.find(p => /rf.?in\b/i.test(p.name) && !/out/i.test(p.name));
  const rfOut = pins.find(p => /rf.?out/i.test(p.name));
  // VDD must be a dedicated power pin — not the combined RF-OUT & DC-IN pin
  const vdd   = pins.find(p => /^(vdd|vcc|supply)/i.test(p.name) && p.type === 'power');
  const gnd   = pins.find(p => /gnd|ground|paddle/i.test(p.name));
  const nc    = pins.filter(p => /^nc/i.test(p.name));

  ctx.lineWidth = 1.5;
  ctx.font = `${FONT}px monospace`;

  // RF-IN — left side, vertically centred
  if (rfIn) {
    const y = cy;
    ctx.strokeStyle = pinColor(rfIn.type);
    ctx.beginPath(); ctx.moveTo(lx - STUB, y); ctx.lineTo(lx, y); ctx.stroke();
    drawPinDot(ctx, lx - STUB, y, pinColor(rfIn.type));
    ctx.fillStyle = C.text; ctx.textAlign = 'right';
    ctx.fillText(rfIn.name, lx - STUB - 6, y + 4);
    ctx.fillStyle = C.muted; ctx.font = '9px sans-serif';
    ctx.fillText(String(rfIn.number), lx - STUB - 6, y + 14);
    ctx.font = `${FONT}px monospace`;
  }

  // RF-OUT — right side
  if (rfOut) {
    const y = cy;
    ctx.strokeStyle = pinColor(rfOut.type);
    ctx.beginPath(); ctx.moveTo(rx, y); ctx.lineTo(rx + STUB, y); ctx.stroke();
    drawPinDot(ctx, rx + STUB, y, pinColor(rfOut.type));
    ctx.fillStyle = C.text; ctx.textAlign = 'left';
    ctx.fillText(rfOut.name, rx + STUB + 6, y + 4);
    ctx.fillStyle = C.muted; ctx.font = '9px sans-serif';
    ctx.fillText(String(rfOut.number), rx + STUB + 6, y + 14);
    ctx.font = `${FONT}px monospace`;
  }

  // VDD — top centre of left edge
  if (vdd) {
    const x = lx + 30, y = ty;
    ctx.strokeStyle = pinColor(vdd.type);
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y - STUB); ctx.stroke();
    drawPinDot(ctx, x, y - STUB, pinColor(vdd.type));
    ctx.fillStyle = C.text; ctx.textAlign = 'center';
    ctx.fillText(vdd.name, x, y - STUB - 8);
    ctx.fillStyle = C.muted; ctx.font = '9px sans-serif';
    ctx.fillText(String(vdd.number), x, y - STUB - 18);
    ctx.font = `${FONT}px monospace`;
  }

  // GND — bottom
  if (gnd) {
    const x = lx + 30, y = by;
    ctx.strokeStyle = pinColor(gnd.type);
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y + STUB); ctx.stroke();
    drawGndSymbol(ctx, x, y + STUB);
    ctx.fillStyle = C.text; ctx.textAlign = 'center';
    ctx.fillText(gnd.name, x, y + STUB + 22);
    ctx.font = `${FONT}px monospace`;
  }

  // NC summary
  if (nc.length) {
    ctx.fillStyle = C.muted;
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(`NC: pins ${nc.map(p=>p.number).join(', ')}`, cx - 12, by + 22);
  }

  legend(ctx, W, H, profile);
}

// ── Op-Amp triangle ────────────────────────────────────────────────────────
function drawOpAmp(ctx, W, H, profile) {
  const cx = W/2, cy = H/2;
  const tw = 150, th = 130;
  const lx = cx - tw/2, rx = cx + tw/2, ty = cy - th/2, by = cy + th/2;
  const STUB = 40;

  ctx.beginPath();
  ctx.moveTo(lx, ty); ctx.lineTo(rx, cy); ctx.lineTo(lx, by); ctx.closePath();
  ctx.fillStyle = C.bodyFill; ctx.fill();
  ctx.strokeStyle = C.body; ctx.lineWidth = 2; ctx.stroke();

  ctx.fillStyle = C.text; ctx.font = 'bold 12px monospace'; ctx.textAlign = 'center';
  ctx.fillText(profile.part_number || '', cx - 10, cy + 4);

  const pins = profile.pins || [];
  const inPos = pins.find(p => /\+|non.?inv/i.test(p.name));
  const inNeg = pins.find(p => /-|inv/i.test(p.name));
  const out   = pins.find(p => /out/i.test(p.name));
  const vpos  = pins.find(p => /v\+|vcc|vdd|vs\+/i.test(p.name));
  const vneg  = pins.find(p => /v-|vss|vs-/i.test(p.name));

  ctx.lineWidth = 1.5; ctx.font = '11px monospace';

  if (inPos) {
    const y = cy - 28;
    ctx.strokeStyle = pinColor(inPos.type);
    ctx.beginPath(); ctx.moveTo(lx - STUB, y); ctx.lineTo(lx, y); ctx.stroke();
    drawPinDot(ctx, lx - STUB, y, pinColor(inPos.type));
    ctx.fillStyle = '#22c55e'; ctx.textAlign = 'left'; ctx.font = 'bold 13px sans-serif';
    ctx.fillText('+', lx + 8, y + 5);
    ctx.font = '11px monospace'; ctx.fillStyle = C.text; ctx.textAlign = 'right';
    ctx.fillText(inPos.name, lx - STUB - 6, y + 4);
  }
  if (inNeg) {
    const y = cy + 28;
    ctx.strokeStyle = pinColor(inNeg.type);
    ctx.beginPath(); ctx.moveTo(lx - STUB, y); ctx.lineTo(lx, y); ctx.stroke();
    drawPinDot(ctx, lx - STUB, y, pinColor(inNeg.type));
    ctx.fillStyle = '#ef4444'; ctx.textAlign = 'left'; ctx.font = 'bold 13px sans-serif';
    ctx.fillText('−', lx + 8, y + 5);
    ctx.font = '11px monospace'; ctx.fillStyle = C.text; ctx.textAlign = 'right';
    ctx.fillText(inNeg.name, lx - STUB - 6, y + 4);
  }
  if (out) {
    const y = cy;
    ctx.strokeStyle = pinColor(out.type);
    ctx.beginPath(); ctx.moveTo(rx, y); ctx.lineTo(rx + STUB, y); ctx.stroke();
    drawPinDot(ctx, rx + STUB, y, pinColor(out.type));
    ctx.fillStyle = C.text; ctx.textAlign = 'left'; ctx.font = '11px monospace';
    ctx.fillText(out.name, rx + STUB + 6, y + 4);
  }
  if (vpos) {
    const x = cx - 10, y = ty;
    ctx.strokeStyle = '#ef4444';
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y - STUB); ctx.stroke();
    drawPinDot(ctx, x, y - STUB, '#ef4444');
    ctx.fillStyle = C.text; ctx.textAlign = 'center'; ctx.font = '11px monospace';
    ctx.fillText(vpos.name, x, y - STUB - 8);
  }
  if (vneg) {
    const x = cx - 10, y = by;
    ctx.strokeStyle = C.muted;
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x, y + STUB); ctx.stroke();
    drawGndSymbol(ctx, x, y + STUB);
    ctx.fillStyle = C.text; ctx.textAlign = 'center'; ctx.font = '11px monospace';
    ctx.fillText(vneg.name, x, y + STUB + 22);
  }
  legend(ctx, W, H, profile);
}

// ── Generic IC rectangle ────────────────────────────────────────────────────
function drawIC(ctx, W, H, profile) {
  const pins = profile.pins || [];
  const STUB = 36, PIN_SPACING = 28, MIN_H = 80, FONT = 11;

  // Split pins: left = input/power/gnd/passive, right = output/bidirectional
  const leftPins  = pins.filter(p => ['input','power','gnd','passive'].includes(p.type));
  const rightPins = pins.filter(p => ['output','bidirectional'].includes(p.type));
  // fallback: split evenly
  const lp = leftPins.length  || Math.ceil(pins.length / 2);
  const rp = rightPins.length || Math.floor(pins.length / 2);

  const bodyH = Math.max(MIN_H, Math.max(lp, rp) * PIN_SPACING + 40);
  const bodyW = Math.min(200, Math.max(120, Math.max(
    ...pins.map(p => (p.name||'').length) ) * 7 + 20));

  const lx = W/2 - bodyW/2, rx = W/2 + bodyW/2;
  const ty = H/2 - bodyH/2, by = H/2 + bodyH/2;

  // Body
  ctx.fillStyle = C.bodyFill;
  ctx.strokeStyle = C.body;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.roundRect(lx, ty, bodyW, bodyH, 4);
  ctx.fill(); ctx.stroke();

  // Part name
  ctx.fillStyle = C.text;
  ctx.font = 'bold 12px monospace';
  ctx.textAlign = 'center';
  ctx.fillText(profile.part_number || '', W/2, ty + 18);
  ctx.font = '9px sans-serif'; ctx.fillStyle = C.muted;
  ctx.fillText(profile.manufacturer || '', W/2, ty + 30);

  // Left pins
  const lCount = leftPins.length || Math.ceil(pins.length/2);
  const lPins  = leftPins.length ? leftPins : pins.slice(0, lCount);
  const lStep  = bodyH / (lPins.length + 1);
  lPins.forEach((pin, i) => {
    const y = ty + lStep * (i + 1);
    const col = pinColor(pin.type);
    ctx.strokeStyle = col; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(lx - STUB, y); ctx.lineTo(lx, y); ctx.stroke();
    drawPinDot(ctx, lx - STUB, y, col);
    ctx.fillStyle = C.text; ctx.font = `${FONT}px monospace`; ctx.textAlign = 'right';
    ctx.fillText(pin.name || '', lx - STUB - 5, y + 4);
    ctx.fillStyle = C.muted; ctx.font = '9px sans-serif';
    ctx.fillText(String(pin.number ?? ''), lx + 6, y + 4);
  });

  // Right pins
  const rPins = rightPins.length ? rightPins : pins.slice(lCount);
  const rStep = bodyH / (rPins.length + 1);
  rPins.forEach((pin, i) => {
    const y = ty + rStep * (i + 1);
    const col = pinColor(pin.type);
    ctx.strokeStyle = col; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(rx, y); ctx.lineTo(rx + STUB, y); ctx.stroke();
    drawPinDot(ctx, rx + STUB, y, col);
    ctx.fillStyle = C.text; ctx.font = `${FONT}px monospace`; ctx.textAlign = 'left';
    ctx.fillText(pin.name || '', rx + STUB + 5, y + 4);
    ctx.fillStyle = C.muted; ctx.font = '9px sans-serif';
    ctx.fillText(String(pin.number ?? ''), rx - 6, y + 4);
    ctx.textAlign = 'right';
  });

  legend(ctx, W, H, profile);
}

// ── Helpers ────────────────────────────────────────────────────────────────
function drawPinDot(ctx, x, y, color) {
  ctx.beginPath();
  ctx.arc(x, y, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function drawGndSymbol(ctx, x, y) {
  ctx.strokeStyle = C.pinGnd; ctx.lineWidth = 1.5;
  [0, 1, 2].forEach(i => {
    const w = 14 - i * 4;
    ctx.beginPath();
    ctx.moveTo(x - w, y + i * 5);
    ctx.lineTo(x + w, y + i * 5);
    ctx.stroke();
  });
}

function legend(ctx, W, H, profile) {
  const types = [...new Set((profile.pins||[]).map(p => p.type).filter(Boolean))];
  const map = { power:'#ef4444', gnd:'#64748b', input:'#22c55e',
                output:'#3b82f6', bidirectional:'#f59e0b', passive:'#94a3b8' };
  let x = 12, y = H - 14;
  ctx.font = '10px sans-serif';
  types.forEach(t => {
    ctx.fillStyle = map[t] || '#94a3b8';
    ctx.fillRect(x, y - 8, 10, 10);
    ctx.fillStyle = '#64748b';
    ctx.fillText(t, x + 13, y);
    x += ctx.measureText(t).width + 28;
  });
}

// ── Symbol Editor Canvas ────────────────────────────────────────────────────
// Handles both IC (DIP layout) and non-IC (schematic symbol) rendering.
