// ── Instantiate editor ─────────────────────────────────────────────────────
const editor = new SchematicEditor(document.getElementById('schematic-canvas'));
editor.newProject('Untitled');
_addTab(editor.project, false);

// Patch _render to keep info panel in sync with selection
const _editorRenderOrig = editor._render.bind(editor);
editor._render = function() {
  _editorRenderOrig();
  if (typeof updateSchInfoPanel !== 'function') return;
  if (editor.selected?.type === 'comp') {
    const comp = editor.project.components.find(c => c.id === editor.selected.id);
    updateSchInfoPanel(comp || null);
  } else if (editor.selected?.type === 'wire') {
    const wire = editor.project.wires.find(w => w.id === editor.selected.id);
    updateSchWireInfoPanel(wire || null);
  } else if (editor.selected?.type === 'label') {
    const lbl = (editor.project.labels||[]).find(l => l.id === editor.selected.id);
    updateSchLabelInfoPanel(lbl || null);
  } else {
    _showSchProjectPanel();
  }
  renderSchNets(editor, 'sch-nets-list', 'sch-nets-count');
};

// ── Net listing ───────────────────────────────────────────────────────────
async function _schNetClick(listId, countId, netName) {
  const ed = listId.startsWith('acc') ? appCircuitEditor : editor;
  if (!ed) return;
  ed._highlightedNet = ed._highlightedNet === netName ? null : netName;
  // Always ensure overlay is fresh so port coords are available for highlighting
  if (ed._highlightedNet) {
    if (!ed._cachedNetOverlay) await ed._refreshNetOverlay();
    else ed._render(); // render immediately with existing overlay
  } else {
    ed._render(); // toggled off
  }
  renderSchNets(ed, listId, countId);
}

function renderSchNets(editorRef, listId, countId) {
  const el = document.getElementById(listId);
  if (!el) return;
  let nets = [];
  try { ({ nets } = computeNetOverlay(editorRef)); } catch(e) {}
  const cnt = document.getElementById(countId);
  if (cnt) cnt.textContent = nets.length ? nets.length : '';
  if (!nets.length) {
    el.innerHTML = '<div style="padding:8px 10px;font-size:11px;color:var(--text-muted);">No nets yet.</div>';
    return;
  }
  const hn = editorRef._highlightedNet;

  // Broad power net detection — matches GND, VCC, VDD, +3.3V, 3V3, 5V, VBUS, etc.
  const _isPower = name => /gnd|vcc|vdd|vss|vee|vbus|vref|vbat|pwr|power|avcc|avdd|dvcc|dvdd|\bv\d|\d+v\d*|3v3|5v0|\+[\d.]+v/i.test(name.trim());

  const power  = nets.filter(n =>  _isPower(n.name));
  const signal = nets.filter(n => !_isPower(n.name));

  const _header = label => `<div style="font-size:9px;font-weight:700;color:var(--text-muted);letter-spacing:.07em;text-transform:uppercase;padding:5px 8px 2px;">${label}</div>`;

  // Power nets → compact chips that wrap (stacked tightly)
  const _chip = n => {
    const col = editorRef._labelColor(n.name);
    const active = hn === n.name;
    const outline = active ? `outline:2px solid #facc15;` : '';
    return `<div title="${esc(n.name)} (${n.ports.length} pins)" style="cursor:pointer;display:inline-flex;align-items:center;gap:3px;padding:2px 6px;border-radius:4px;background:rgba(255,255,255,0.05);border:1px solid ${col}33;${outline}max-width:100%;overflow:hidden;"
      data-net="${esc(n.name)}" data-list="${esc(listId)}" data-count="${esc(countId)}"
      onclick="_schNetClick(this.dataset.list,this.dataset.count,this.dataset.net)">
      <div style="width:6px;height:6px;border-radius:50%;background:${col};flex-shrink:0;"></div>
      <span style="color:${col};font-size:9px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(n.name)}</span>
    </div>`;
  };

  // Signal nets → slim rows
  const _row = n => {
    const col = editorRef._labelColor(n.name);
    const active = hn === n.name;
    const bg = active ? 'background:rgba(250,204,21,0.13);' : '';
    const border = active ? 'border-left:3px solid #facc15;padding-left:5px;' : 'border-left:3px solid transparent;padding-left:5px;';
    return `<div class="sch-net-row" style="cursor:pointer;${bg}${border}" data-net="${esc(n.name)}" data-list="${esc(listId)}" data-count="${esc(countId)}" onclick="_schNetClick(this.dataset.list,this.dataset.count,this.dataset.net)" title="${esc(n.name)}">
      <div class="sch-net-dot" style="background:${col};flex-shrink:0;"></div>
      <span class="sch-net-name" style="color:${col};" title="${esc(n.name)}">${esc(n.name)}</span>
      <span class="sch-net-pins">${n.ports.length}</span>
    </div>`;
  };

  let html = '';
  if (power.length) {
    html += _header('Power');
    html += `<div style="display:flex;flex-wrap:wrap;gap:3px;padding:2px 6px 6px;">` + power.map(_chip).join('') + `</div>`;
  }
  if (signal.length) {
    if (power.length) html += `<div style="height:1px;background:var(--border);margin:2px 0;opacity:0.4;"></div>`;
    html += _header('Signals');
    html += signal.map(_row).join('');
  }
  el.innerHTML = html;
}

// ── SVG / PNG Export ───────────────────────────────────────────────────────
function exportSVG() {
  const { components, wires } = editor.project;
  if (!components.length && !wires.length) { alert('Schematic is empty.'); return; }
  const svg = editor.exportSVGString(false);
  const blob = new Blob([svg], { type: 'image/svg+xml' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (editor.project.name || 'schematic').replace(/[^\w\s-]/g, '') + '.svg';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function exportPNG() {
  const { components, wires } = editor.project;
  if (!components.length && !wires.length) { alert('Schematic is empty.'); return; }
  const svg = editor.exportSVGString(false);
  const { w, h } = editor._contentBBox();
  const scale = 2; // 2x for sharpness
  const canvas = document.createElement('canvas');
  canvas.width = w * scale; canvas.height = h * scale;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  const img = new Image();
  img.onload = () => {
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const a = document.createElement('a');
    a.href = canvas.toDataURL('image/png');
    a.download = (editor.project.name || 'schematic').replace(/[^\w\s-]/g, '') + '.png';
    a.click();
  };
  img.onerror = () => alert('PNG export failed. Try SVG export instead.');
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

// ── BOM Export ─────────────────────────────────────────────────────────────
function exportBOM() {
  const comps = editor.project.components;
  if (!comps.length) { alert('Schematic is empty.'); return; }
  const groups = {};
  comps.forEach(c => {
    const key = [c.symType, c.value || '', c.partNumber || ''].join('|');
    if (!groups[key]) groups[key] = { symType: c.symType, value: c.value || '', partNumber: c.partNumber || '', refs: [] };
    groups[key].refs.push(c.designator || '?');
  });
  const rows = [['Designator', 'Quantity', 'Value', 'Part Number', 'Symbol Type']];
  Object.values(groups).sort((a, b) => a.symType.localeCompare(b.symType)).forEach(g => {
    rows.push(['"' + g.refs.sort().join(',') + '"', g.refs.length, g.value, g.partNumber, g.symType]);
  });
  const csv = rows.map(r => r.join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (editor.project.name || 'schematic').replace(/[^\w\s-]/g, '') + '-bom.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

async function buildNetlist() {
  const res = await fetch('/api/netlist', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      components: editor.project.components,
      wires: editor.project.wires,
      labels: editor.project.labels || [],
      noConnects: editor.project.noConnects || []
    })
  });
  return await res.json();
}

function computeNetOverlay(editorRef) {
  return editorRef._cachedNetOverlay || { nets: [], wireToNet: new Map() };
}

// ── Power Budget ────────────────────────────────────────────────────────────
function showPowerBudget() {
  const comps = editor.project.components;
  const skipTypes = new Set(['vcc', 'gnd', 'wire', 'label', 'nc', 'power', 'pwr_flag']);

  const rows = [];
  let grandTotal = 0;

  comps.forEach(c => {
    const symType = (c.symType || '').toLowerCase();
    const slug = (c.slug || c.symType || '').toUpperCase();

    // Skip power symbols, GND, wire/label primitives
    if (skipTypes.has(symType)) return;
    if (symType === 'vcc' || symType === 'gnd') return;

    // Look up profile in window.library
    const profile = (window.library && window.library[slug]) ? window.library[slug] : null;
    const currentMa = profile && typeof profile.typical_current_ma === 'number'
      ? profile.typical_current_ma
      : 0;

    // Determine voltage rail hint from supply_voltage_range
    let rail = '—';
    if (profile && profile.supply_voltage_range) {
      const svr = profile.supply_voltage_range;
      if (/3\.3/i.test(svr)) rail = '3.3V';
      else if (/5v|5\.0/i.test(svr)) rail = '5V';
      else if (/12v/i.test(svr)) rail = '12V';
      else if (/1\.8/i.test(svr)) rail = '1.8V';
      else rail = svr.split(/[,;]/)[0].trim().substring(0, 16);
    }

    grandTotal += currentMa;
    rows.push({ name: slug, designator: c.designator || '?', currentMa, rail });
  });

  // Build modal HTML
  const rowsHtml = rows.length
    ? rows.map(r => `
      <tr>
        <td style="padding:5px 10px;border-bottom:1px solid #1e2130;">${esc(r.name)}</td>
        <td style="padding:5px 10px;border-bottom:1px solid #1e2130;font-family:monospace;">${esc(r.designator)}</td>
        <td style="padding:5px 10px;border-bottom:1px solid #1e2130;text-align:right;font-family:monospace;">${r.currentMa > 0 ? r.currentMa.toFixed(3).replace(/\.?0+$/, '') : '—'}</td>
        <td style="padding:5px 10px;border-bottom:1px solid #1e2130;color:#94a3b8;">${esc(r.rail)}</td>
      </tr>`).join('')
    : `<tr><td colspan="4" style="padding:12px 10px;color:#6b7280;text-align:center;">No components placed.</td></tr>`;

  const totalHtml = rows.length ? `
    <tr style="font-weight:700;background:#0d0f17;">
      <td colspan="2" style="padding:6px 10px;">Total</td>
      <td style="padding:6px 10px;text-align:right;font-family:monospace;color:#facc15;">${grandTotal.toFixed(1)} mA</td>
      <td></td>
    </tr>` : '';

  const html = `
    <div id="power-budget-overlay" style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:10000;display:flex;align-items:center;justify-content:center;" onclick="if(event.target===this)document.getElementById('power-budget-overlay').remove()">
      <div style="background:#0f1120;border:1px solid #2a2d3a;border-radius:10px;min-width:480px;max-width:680px;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 16px 48px rgba(0,0,0,0.7);">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid #1e2130;">
          <span style="font-size:14px;font-weight:700;color:#e2e8f0;">&#9889; Power Budget</span>
          <button onclick="document.getElementById('power-budget-overlay').remove()" style="background:none;border:none;color:#6b7280;cursor:pointer;font-size:18px;line-height:1;padding:0 4px;">&times;</button>
        </div>
        <div style="overflow-y:auto;flex:1;">
          <table style="width:100%;border-collapse:collapse;font-size:12px;color:#cbd5e1;">
            <thead>
              <tr style="background:#0d0f17;color:#6b7280;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">
                <th style="padding:6px 10px;text-align:left;font-weight:600;">Component</th>
                <th style="padding:6px 10px;text-align:left;font-weight:600;">Ref</th>
                <th style="padding:6px 10px;text-align:right;font-weight:600;">Typ. Current (mA)</th>
                <th style="padding:6px 10px;text-align:left;font-weight:600;">Rail</th>
              </tr>
            </thead>
            <tbody>${rowsHtml}</tbody>
            <tfoot>${totalHtml}</tfoot>
          </table>
        </div>
        <div style="padding:10px 16px;border-top:1px solid #1e2130;font-size:11px;color:#6b7280;font-style:italic;">
          Estimate only. Actual draw depends on operating conditions.
        </div>
      </div>
    </div>`;

  // Remove any existing overlay then insert
  const existing = document.getElementById('power-budget-overlay');
  if (existing) existing.remove();
  document.body.insertAdjacentHTML('beforeend', html);
}

// ── Compatibility Check ───────────────────────────────────────────────────────
async function runCompatibilityCheck() {
  const modal = document.getElementById('drc-modal');
  const results = document.getElementById('drc-results');
  if (!modal || !results) { alert('Compatibility check UI not available.'); return; }

  results.innerHTML = '<div style="color:var(--text-muted);padding:8px 0;">Running checks\u2026</div>';
  modal.style.display = 'flex';

  const issues = [];

  const allComps = editor.project.components || [];
  const comps = allComps.filter(c => c.symType !== 'vcc' && c.symType !== 'gnd');

  if (!comps.length) {
    results.innerHTML = '<div style="color:var(--green,#4ade80);font-size:13px;">&#10003; No components placed \u2014 nothing to check.</div>';
    return;
  }

  // ── Fetch net overlay (use cached or re-fetch) ───────────────────────────
  let nets = [];
  try {
    if (editor._cachedNetOverlay && editor._cachedNetOverlay.nets.length) {
      nets = editor._cachedNetOverlay.nets;
    } else {
      await editor._refreshNetOverlay();
      nets = (editor._cachedNetOverlay && editor._cachedNetOverlay.nets) || [];
    }
  } catch(e) {
    issues.push({ sev: 'WARN', msg: 'Could not compute nets \u2014 wire connectivity checks skipped.' });
  }

  // Build compId \u2192 component map
  const compById = {};
  allComps.forEach(c => { compById[c.id] = c; });

  // Build netName \u2192 [{compId, portName}] from nodeId "compId::portName"
  const netPorts = {};
  nets.forEach(net => {
    netPorts[net.name] = (net.ports || []).map(p => {
      const parts = (p.nodeId || '').split('::');
      return { compId: parts[0], portName: parts[1] || p.portName || '' };
    });
  });

  // ── Fetch detailed profiles for all non-trivial components ────────────────
  const profileCache = {};
  const passiveSymTypes = new Set(['resistor', 'capacitor', 'capacitor_pol', 'inductor']);
  const passiveSlugs = new Set(['RESISTOR', 'CAPACITOR', 'INDUCTOR', 'CAPACITOR_POL']);

  const slugsToFetch = [...new Set(comps.map(c => c.slug).filter(Boolean))];
  await Promise.all(slugsToFetch.map(async slug => {
    // Start with basic library entry
    if (window.library && window.library[slug]) profileCache[slug] = window.library[slug];
    // Fetch full profile (has required_passives, supply_voltage_range, etc.)
    try {
      const r = await fetch('/api/library/' + encodeURIComponent(slug));
      if (r.ok) profileCache[slug] = await r.json();
    } catch(_) {}
  }));

  // ── CHECK 1: Missing mandatory passives ──────────────────────────────────
  const hasPassiveType = type => {
    if (type === 'resistor')  return allComps.some(c => passiveSymTypes.has(c.symType) && c.symType === 'resistor' || passiveSlugs.has(c.slug) && c.slug === 'RESISTOR');
    if (type === 'capacitor') return allComps.some(c => (c.symType === 'capacitor' || c.symType === 'capacitor_pol') || (c.slug === 'CAPACITOR' || c.slug === 'CAPACITOR_POL'));
    if (type === 'inductor')  return allComps.some(c => c.symType === 'inductor' || c.slug === 'INDUCTOR');
    return false;
  };

  comps.forEach(comp => {
    const profile = profileCache[comp.slug];
    if (!profile || !Array.isArray(profile.required_passives)) return;
    const label = comp.designator || comp.slug || comp.symType;
    profile.required_passives.forEach(rp => {
      const reason = (rp.reason || '').toLowerCase();
      if (!(reason.includes('required') || reason.includes('mandatory') || reason.includes('must'))) return;
      const type = (rp.type || '').toLowerCase();
      if (!hasPassiveType(type)) {
        issues.push({
          sev: 'WARN',
          msg: `<strong>${esc(label)}</strong>: requires a ${esc(rp.type || type)} (${esc(rp.value || '')}) \u2014 "${esc(rp.placement || rp.reason || '')}". None found in schematic.`
        });
      }
    });
  });

  // ── CHECK 2: VCC net voltage conflict ────────────────────────────────────
  const vccNets = nets.filter(n => /^(vcc|3v3|\+3\.?3v?|\+5v?|5v|3\.3v|vdd|pwr)/i.test(n.name));
  const vccVoltages = {}; // netName \u2192 voltage number
  vccNets.forEach(net => {
    const name = net.name;
    if (/3\.?3|3v3/i.test(name)) vccVoltages[name] = 3.3;
    else if (/\b5v?\b|5\.0/i.test(name)) vccVoltages[name] = 5.0;
  });

  const knownVoltages = [...new Set(Object.values(vccVoltages))];
  if (knownVoltages.includes(3.3) && knownVoltages.includes(5.0)) {
    const v33nets = Object.entries(vccVoltages).filter(([,v]) => v === 3.3).map(([n]) => n);
    const v5nets  = Object.entries(vccVoltages).filter(([,v]) => v === 5.0).map(([n]) => n);
    // Warn if any IC component sits on both domains
    const compsOn33 = new Set((v33nets.flatMap(n => netPorts[n] || [])).map(p => p.compId));
    const compsOn5  = new Set((v5nets.flatMap(n => netPorts[n] || [])).map(p => p.compId));
    compsOn33.forEach(id => {
      if (compsOn5.has(id)) {
        const c = compById[id];
        const lbl = c ? (c.designator || c.slug) : id;
        issues.push({ sev: 'WARN', msg: `<strong>${esc(lbl)}</strong>: connected to both 3.3V and 5V supply nets \u2014 check for power domain conflict.` });
      }
    });
    issues.push({ sev: 'INFO', msg: `Mixed supply voltages present: 3.3V net(s) [${v33nets.map(esc).join(', ')}] and 5V net(s) [${v5nets.map(esc).join(', ')}]. Ensure level-shifting on IO crossing domains.` });
  }

  // ── CHECK 3: IO voltage mismatch on signal nets ───────────────────────────
  // Infer nominal IO voltage from supply_voltage_range for each component
  const compIoVoltage = {};
  comps.forEach(comp => {
    const profile = profileCache[comp.slug];
    if (!profile) return;
    const hint = (profile.supply_voltage_range || '') + ' ' + (profile.description || '');
    if (/3\.3\s*v|3v3/i.test(hint) && !/5\s*v/i.test(hint)) compIoVoltage[comp.id] = 3.3;
    else if (/5\s*v/i.test(hint) && !/3\.3\s*v|3v3/i.test(hint))  compIoVoltage[comp.id] = 5.0;
    else if (/1\.8\s*v/i.test(hint) && !/3\.3\s*v|5\s*v/i.test(hint)) compIoVoltage[comp.id] = 1.8;
  });

  const powerNetRe = /^(vcc|gnd|3v3|\+3\.?3v?|\+5v?|5v|3\.3v|vdd|pwr|ground|agnd|dgnd)/i;
  nets.forEach(net => {
    if (powerNetRe.test(net.name)) return;
    const ports = (netPorts[net.name] || []).filter(p => {
      const c = compById[p.compId];
      return c && c.symType !== 'vcc' && c.symType !== 'gnd';
    });
    const voltages = [...new Set(ports.map(p => compIoVoltage[p.compId]).filter(Boolean))];
    if (voltages.length > 1) {
      const names = [...new Set(ports.map(p => {
        const c = compById[p.compId];
        return esc(c ? (c.designator || c.slug) : p.compId);
      }))];
      issues.push({
        sev: 'WARN',
        msg: `Net <strong>${esc(net.name)}</strong>: components with differing IO voltages (${voltages.join('V / ')}V) \u2014 [${names.join(', ')}]. Level-shifting may be required.`
      });
    }
  });

  // ── CHECK 4: Large unlabeled nets (3+ IC pins, auto-generated name) ───────
  nets.forEach(net => {
    if (!/^N\d+$/.test(net.name)) return; // only auto-named nets
    const icPorts = (netPorts[net.name] || []).filter(p => {
      const c = compById[p.compId];
      return c && c.symType !== 'vcc' && c.symType !== 'gnd';
    });
    if (icPorts.length >= 3) {
      const names = [...new Set(icPorts.map(p => {
        const c = compById[p.compId];
        return esc(c ? (c.designator || c.slug) : p.compId);
      }))];
      issues.push({
        sev: 'INFO',
        msg: `Unnamed net connects ${icPorts.length} pins across [${names.join(', ')}] \u2014 consider adding a net label for readability.`
      });
    }
  });

  // ── Render results ────────────────────────────────────────────────────────
  if (!issues.length) {
    results.innerHTML = `<div style="display:flex;align-items:center;gap:10px;padding:12px 0;">
      <span style="font-size:22px;color:var(--green,#4ade80);">&#10003;</span>
      <span style="font-size:13px;color:var(--green,#4ade80);font-weight:600;">No compatibility issues found.</span>
    </div>
    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">
      Checked ${comps.length} component(s): mandatory passives, VCC voltage conflicts, IO voltage mismatches, unlabeled nets with 3+ pins.
    </div>`;
    return;
  }

  const warns = issues.filter(i => i.sev === 'WARN');
  const infos  = issues.filter(i => i.sev === 'INFO');
  const summary = `<div style="font-size:11px;color:var(--text-muted);margin-bottom:10px;">${warns.length} warning(s), ${infos.length} info(s) across ${comps.length} component(s).</div>`;

  const renderIssue = issue => {
    const isWarn = issue.sev === 'WARN';
    const color  = isWarn ? '#f59e0b' : '#60a5fa';
    const icon   = isWarn ? '\u26a0 WARNING' : '\u2139 INFO';
    return `<div style="display:flex;gap:10px;padding:7px 0;border-bottom:1px solid var(--border,#1e2130);">
      <span style="color:${color};font-weight:700;font-size:11px;min-width:80px;flex-shrink:0;">${icon}</span>
      <span style="font-size:12px;line-height:1.5;">${issue.msg}</span>
    </div>`;
  };

  results.innerHTML = summary
    + warns.map(renderIssue).join('')
    + (infos.length ? `<div style="margin-top:10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--text-muted);padding-bottom:4px;">Info</div>` : '')
    + infos.map(renderIssue).join('');
}

// ── PCB sync notification ────────────────────────────────────────────────────
// Called from _status() and after saveProject() — keeps PCB frame aware of
// whether the schematic is ahead of the last imported PCB board.
function _notifyPcbSync() {
  const frame = document.getElementById('pcb-frame');
  const tab = openTabs.find(t => t.tabId === activeTabId);
  const projectId = tab?.projectId || editor.project?.id || null;
  const isDirty = editor.dirty;

  // Update "Update PCB" button visibility
  const btn = document.getElementById('btn-update-pcb');
  if (btn) btn.style.display = (isDirty && projectId) ? '' : 'none';

  // Update PCB nav tab badge
  const navPcb = document.getElementById('nav-pcb');
  if (navPcb) {
    navPcb.innerHTML = isDirty && projectId
      ? '🔲 PCB <span style="color:#f59e0b;font-size:10px;">⚠</span>'
      : '🔲 PCB';
  }

  // Notify PCB iframe if loaded
  if (frame?.contentWindow && frame.src) {
    try { frame.contentWindow.postMessage({ type: 'schematicDirty', projectId, isDirty }, '*'); } catch(_) {}
  }
}

// ── AI Circuit Generator ─────────────────────────────────────────────────────
function showCircuitGenerator() {
  const modal = document.getElementById('ai-gen-modal');
  if (modal) modal.style.display = 'flex';
}

async function runCircuitGenerator() {
  const description = (document.getElementById('ai-gen-description')?.value || '').trim();
  if (!description) { alert('Please describe the circuit.'); return; }
  const apiKey = (document.getElementById('ai-gen-apikey')?.value || '').trim();
  const btn = document.getElementById('ai-gen-btn');
  const status = document.getElementById('ai-gen-status');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Generating…'; }
  if (status) status.textContent = 'Calling AI…';
  try {
    const res = await fetch('/api/generate-circuit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, api_key: apiKey })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Generation failed');
    const sch = data.schematic;
    // Load into editor
    if (sch.components) {
      sch.components.forEach(c => {
        if (!c.id) c.id = 'c' + Math.random().toString(36).slice(2,8);
        editor.project.components.push(c);
      });
    }
    if (sch.wires) {
      sch.wires.forEach(w => {
        if (!w.id) w.id = 'w' + Math.random().toString(36).slice(2,8);
        editor.project.wires.push(w);
      });
    }
    if (sch.netLabels) {
      if (!editor.project.netLabels) editor.project.netLabels = [];
      sch.netLabels.forEach(n => {
        if (!n.id) n.id = 'n' + Math.random().toString(36).slice(2,8);
        editor.project.netLabels.push(n);
      });
    }
    editor.render();
    document.getElementById('ai-gen-modal').style.display = 'none';
    if (status) status.textContent = '';
  } catch(e) {
    if (status) status.textContent = '❌ ' + e.message;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⚡ Generate'; }
  }
}
