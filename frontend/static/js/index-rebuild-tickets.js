function runCompatibilityCheck() {
  const { components, wires } = editor.project;
  const modal = document.getElementById('drc-modal');
  const out = document.getElementById('drc-results');
  if (!modal || !out) return;

  const issues = []; // { level: 'error'|'warn'|'ok', msg }
  const ok = (msg) => issues.push({ level:'ok', msg });
  const warn = (msg) => issues.push({ level:'warn', msg });
  const err = (msg) => issues.push({ level:'err', msg });

  if (!components.length) {
    out.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px;">Schematic is empty. Place some components first.</div>';
    modal.style.display = 'flex'; return;
  }

  // Build netlist
  const { portNodes, namedNets } = buildNetlist();

  // Net name → list of port nodes
  const netPortMap = new Map();
  for (const net of namedNets) {
    const nodesOnNet = portNodes.filter(pn => net.pins.includes(`${pn.designator}.${pn.portName}`));
    netPortMap.set(net.name, nodesOnNet);
  }

  // Component slug → profile
  const getProfile = (slug) => profileCache[slug] || {};

  // ── Check 1: VCC/power pins with no connection ──────────────────────────
  const connectedPins = new Set(portNodes.map(pn => `${pn.compId}::${pn.portName}`));
  for (const comp of components) {
    const profile = getProfile(comp.slug);
    const pins = profile.pins || [];
    for (const pin of pins) {
      if (pin.type === 'power' || pin.type === 'gnd') {
        const key = `${comp.id}::${pin.name}`;
        if (!connectedPins.has(key)) {
          err(`${comp.designator} (${comp.value || comp.slug}): pin "${pin.name}" [${pin.type}] appears unconnected.`);
        }
      }
    }
  }

  // ── Check 2: 5V ↔ 3.3V logic mismatch ──────────────────────────────────
  const V33_SLUGS = new Set(['ESP32_WROOM_32', 'AMS1117-3.3']);
  const V5_SLUGS  = new Set(['ATMEGA328P', 'LM7805', 'NE555', 'L298N', 'LM358', 'DRV8833', 'IRF540N', '2N2222', 'BC547']);
  const compVoltMap = new Map(); // compId → '3.3' | '5' | 'unknown'
  for (const comp of components) {
    const s = comp.slug;
    if (V33_SLUGS.has(s)) compVoltMap.set(comp.id, '3.3');
    else if (V5_SLUGS.has(s)) compVoltMap.set(comp.id, '5');
    else {
      const p = getProfile(s);
      const vr = (p.supply_voltage_range || '').toLowerCase();
      if (vr.includes('3.3') && !vr.includes('5.5')) compVoltMap.set(comp.id, '3.3');
      else if (vr.includes('5v') || vr.includes('4.5') || vr.includes('7v')) compVoltMap.set(comp.id, '5');
      else compVoltMap.set(comp.id, 'unknown');
    }
  }
  // Find nets that connect both 3.3V and 5V power pins
  for (const net of namedNets) {
    const v33comps = [], v5comps = [];
    for (const pin of net.pins) {
      const [des] = pin.split('.');
      const comp = components.find(c => c.designator === des);
      if (!comp) continue;
      const v = compVoltMap.get(comp.id) || 'unknown';
      if (v === '3.3') v33comps.push(des);
      else if (v === '5') v5comps.push(des);
    }
    if (v33comps.length && v5comps.length) {
      warn(`Net "${net.name}" connects 3.3V components (${v33comps.join(', ')}) to 5V components (${v5comps.join(', ')}). Logic level mismatch — add level shifter.`);
    }
  }

  // ── Check 3: ESP32 pin voltage ──────────────────────────────────────────
  for (const comp of components) {
    if (comp.slug !== 'ESP32_WROOM_32') continue;
    const pwr = portNodes.find(pn => pn.compId === comp.id && pn.portName === '3V3');
    if (pwr) {
      const net = namedNets.find(n => n.pins.includes(`${comp.designator}.3V3`));
      if (net) {
        const nm = net.name.toUpperCase();
        if (nm === 'VCC' || nm.includes('5V') || nm.includes('5.0')) {
          err(`${comp.designator} (ESP32): 3V3 pin connected to "${net.name}" — ESP32 is 3.3V ONLY, not 5V tolerant. Use AMS1117-3.3 or similar.`);
        }
      }
    }
  }

  // ── Check 4: I2C SDA/SCL without pull-ups ───────────────────────────────
  const I2C_NAMES = new Set(['SDA','SCL','PC4/ADC4/SDA','PC5/ADC5/SCL','GPIO21','GPIO22']);
  const i2cNets = new Set();
  for (const pn of portNodes) {
    if (I2C_NAMES.has(pn.portName) || pn.portName.toUpperCase().includes('SDA') || pn.portName.toUpperCase().includes('SCL')) {
      const net = namedNets.find(n => n.pins.includes(`${pn.designator}.${pn.portName}`));
      if (net) i2cNets.add(net.name);
    }
  }
  for (const netName of i2cNets) {
    const net = namedNets.find(n => n.name === netName);
    if (!net) continue;
    const hasResistor = net.pins.some(pin => {
      const [des] = pin.split('.');
      const comp = components.find(c => c.designator === des);
      return comp && comp.symType === 'resistor';
    });
    if (!hasResistor) {
      warn(`I2C net "${netName}" has no pull-up resistor. SDA/SCL require 4.7kΩ pull-ups to VCC.`);
    }
  }

  // ── Check 5: Components with required passives not present ───────────────
  const presentTypes = new Map();
  for (const comp of components) {
    const t = comp.symType;
    presentTypes.set(t, (presentTypes.get(t) || 0) + 1);
  }
  for (const comp of components) {
    const profile = getProfile(comp.slug);
    const req = profile.required_passives || [];
    if (!req.length) continue;
    for (const r of req) {
      const reqType = r.type; // 'capacitor', 'inductor', 'resistor'
      if (!presentTypes.has(reqType) || presentTypes.get(reqType) < 1) {
        warn(`${comp.designator} (${comp.value||comp.slug}) requires a ${r.value} ${r.type} (${r.placement}) but none found in schematic.`);
        break; // one warning per component is enough
      }
    }
  }

  // ── Check 6: Wires but no components, or vice-versa ─────────────────────
  if (wires.length && !components.length) err('Wires present but no components placed.');
  if (components.length && !wires.length && components.length > 1) warn('Multiple components placed but no wires connecting them.');

  // ── Summary ───────────────────────────────────────────────────────────────
  const errCount = issues.filter(i => i.level === 'err').length;
  const warnCount = issues.filter(i => i.level === 'warn').length;
  if (!errCount && !warnCount) ok(`All ${components.length} component(s) passed checks. No errors or warnings.`);

  const ICONS = { err: '🔴', warn: '🟡', ok: '🟢' };
  const COLORS = { err: '#f87171', warn: '#fbbf24', ok: '#4ade80' };
  let html = '';
  if (errCount || warnCount) {
    html += `<div style="margin-bottom:12px;padding:8px 12px;background:var(--surface2);border-radius:6px;font-size:11px;">`;
    if (errCount) html += `<span style="color:#f87171;font-weight:700;">🔴 ${errCount} error${errCount>1?'s':''}</span>  `;
    if (warnCount) html += `<span style="color:#fbbf24;font-weight:700;">🟡 ${warnCount} warning${warnCount>1?'s':''}</span>`;
    html += `</div>`;
  }
  for (const { level, msg } of issues) {
    html += `<div style="display:flex;gap:8px;margin-bottom:6px;padding:6px 10px;background:var(--surface2);border-radius:5px;border-left:3px solid ${COLORS[level]};">
      <span style="flex-shrink:0;">${ICONS[level]}</span>
      <span style="color:var(--text);font-size:11px;">${msg}</span>
    </div>`;
  }
  out.innerHTML = html;
  modal.style.display = 'flex';
}

// ── Designator prefix map ────────────────────────────────────────────────────
const DESIGNATOR_PREFIX = {
  resistor:'R', capacitor:'C', capacitor_pol:'C', inductor:'L',
  diode:'D', led:'D', npn:'Q', pnp:'Q', nmos:'Q', pmos:'Q',
  opamp:'U', amplifier:'U', ic:'U', vcc:'VCC', gnd:'GND'
};

function getDesignatorPrefix(profile) {
  if (profile.designator) return profile.designator;
  const st = detectSymbolType(profile);
  return DESIGNATOR_PREFIX[st] || 'U';
}

// Inline edit designator badge
function editDesignator(el, slug) {
  if (!slug) return;
  const current = (profileCache[slug]?.designator || '');
  const input = document.createElement('input');
  input.value = current;
  input.style.cssText = 'width:60px;background:var(--bg);border:1px solid var(--accent);border-radius:3px;color:#a78bfa;padding:2px 5px;font-size:11px;font-family:monospace;';
  input.placeholder = 'R / U / Q …';
  el.replaceWith(input);
  input.focus(); input.select();

  const commit = async () => {
    const val = input.value.trim().toUpperCase();
    if (val) {
      await fetch(`/api/library/${slug}/designator`, {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ designator: val })
      });
      if (profileCache[slug]) profileCache[slug].designator = val;
    }
    // Restore badge
    const span = document.createElement('span');
    span.className = 'meta-tag desig-badge';
    span.title = 'Schematic designator prefix — edit inline';
    span.onclick = () => editDesignator(span, slug);
    span.style.cssText = 'cursor:pointer;color:#a78bfa;border-color:rgba(167,139,250,0.4);';
    span.textContent = val ? '🔖 ' + val : '+ Add designator';
    input.replaceWith(span);
  };
  input.addEventListener('blur', commit);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } });
}

// ── Rebuild → shows popover with optional notes, then creates a ticket ────────
// gen-toggle state: set of active type keys
const _genSelected = new Set();

// Map tab name → default toggle key
const _TAB_TO_GEN = { 'datasheet': 'symbol', 'schematic': 'schematic', 'footprint': 'footprint', 'layout-example': 'layout' };

function genToggle(type) {
  if (_genSelected.has(type)) _genSelected.delete(type);
  else _genSelected.add(type);
  _genUpdateToggles();
}

function _genUpdateToggles() {
  ['symbol','schematic','footprint','layout'].forEach(t => {
    const el = document.getElementById('gen-t-' + t);
    if (el) el.classList.toggle('active', _genSelected.has(t));
  });
  const qBtn = document.getElementById('rebuild-queue-btn');
  if (qBtn) qBtn.disabled = _genSelected.size === 0;
}

function queueRebuild() {
  if (!selectedSlug) return;
  // Pre-select the current tab's type
  _genSelected.clear();
  const defaultType = _TAB_TO_GEN[currentProfileTab] || 'symbol';
  _genSelected.add(defaultType);
  _genUpdateToggles();
  const pop = document.getElementById('rebuild-popover');
  const notes = document.getElementById('rebuild-notes');
  if (pop) {
    if (notes) notes.value = '';
    pop.style.display = 'block';
    if (notes) notes.focus();
    setTimeout(() => document.addEventListener('click', _rebuildPopoverOutside, { once: true }), 10);
  }
}

function _rebuildPopoverOutside(e) {
  const pop = document.getElementById('rebuild-popover');
  if (pop && !pop.contains(e.target) && e.target.id !== 'rebuild-btn') {
    pop.style.display = 'none';
  }
}

function closeRebuildPopover() {
  const pop = document.getElementById('rebuild-popover');
  if (pop) pop.style.display = 'none';
}

async function confirmRebuild() {
  const pop = document.getElementById('rebuild-popover');
  const notes = document.getElementById('rebuild-notes');
  const extraNotes = notes ? notes.value.trim() : '';
  if (pop) pop.style.display = 'none';
  if (!selectedSlug || _genSelected.size === 0) return;

  const btn = document.getElementById('rebuild-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Queuing…'; }

  try {
    const freshProfile = await fetch(`/api/library/${selectedSlug}`).then(r => r.json());
    profileCache[selectedSlug] = freshProfile;
    const rawText = await fetch(`/api/library/${selectedSlug}/raw`).then(r => r.ok ? r.text() : null).catch(() => null);

    // Queue one ticket per selected type
    const types = [..._genSelected];
    for (const type of types) {
      // 'symbol' maps to 'datasheet' ticket type
      const ticketType = type === 'symbol' ? 'datasheet' : type;
      await gtCreateTicket(ticketType, selectedSlug, freshProfile, rawText, extraNotes);
    }
    if (btn) {
      btn.textContent = `✓ ${types.length} ticket${types.length > 1 ? 's' : ''} queued`;
      setTimeout(() => { btn.disabled = false; btn.textContent = '✨ Generate'; }, 2500);
    }
  } catch(e) {
    if (btn) { btn.disabled = false; btn.textContent = '✨ Generate'; }
    alert('Failed to create ticket: ' + e.message);
  }
}

async function buildExampleRebuildPrompt(slug, profile, rawText = null) {
  const res = await fetch('/api/gen-tickets/build-prompt', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({type:'example', slug, profile, rawText})
  });
  return (await res.json()).prompt;
}

async function buildRebuildPrompt(slug, profile, rawText) {
  const res = await fetch('/api/gen-tickets/build-prompt', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({type:'datasheet', slug, profile, rawText})
  });
  return (await res.json()).prompt;
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
