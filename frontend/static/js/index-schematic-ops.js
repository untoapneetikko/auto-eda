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
  el.innerHTML = nets.map(n => {
    const col = editorRef._labelColor(n.name);
    return `<div class="sch-net-row">
      <div class="sch-net-dot" style="background:${col};"></div>
      <span class="sch-net-name" style="color:${col};" title="${esc(n.name)}">${esc(n.name)}</span>
      <span class="sch-net-pins">${n.ports.length}</span>
    </div>`;
  }).join('');
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

async function getBOM(projectId) {
  return fetch(`/api/projects/${projectId}/bom`).then(r=>r.json());
}

// ── PCB JSON Export ────────────────────────────────────────────────────────
async function buildNetlist() {
  const res = await fetch('/api/netlist', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      components: editor.project.components,
      wires: editor.project.wires,
      labels: editor.project.labels || []
    })
  });
  return await res.json();
}

// ── Net names overlay (IMP-027) ────────────────────────────────────────────
function toggleShowNets() {
  editor.showNets = !editor.showNets;
  const btn = document.getElementById('nets-toggle-btn');
  if (btn) btn.classList.toggle('active', !!editor.showNets);
  editor._render();
}

function computeNetOverlay(editorRef) {
  return editorRef._cachedNetOverlay || { nets: [], wireToNet: new Map() };
}

function exportPCBJson() {
  const { components } = editor.project;
  if (!components.length) {
    alert('Schematic is empty — place some components first.');
    return;
  }

  const { namedNets } = buildNetlist();

  // Build designator+portName → netName lookup
  const pinNetMap = new Map();
  for (const net of namedNets) {
    for (const pinRef of net.pins) {
      const dot = pinRef.indexOf('.');
      const des = pinRef.slice(0, dot), portName = pinRef.slice(dot + 1);
      const comp = components.find(c => c.designator === des);
      if (comp) pinNetMap.set(`${comp.id}::${portName}`, net.name);
    }
  }

  // Build output component list with per-pin net assignments
  const outComponents = components.map(comp => {
    const profile = profileCache[comp.slug] || {};
    const profilePins = profile.pins || [];
    const ports = editor._ports(comp);
    const pins = ports.map(port => {
      const pin = profilePins.find(p => (p.name || `P${p.number}`) === port.name);
      return {
        name: port.name,
        number: pin?.number ?? null,
        type: pin?.type ?? null,
        net: pinNetMap.get(`${comp.id}::${port.name}`) || null
      };
    });
    return {
      designator: comp.designator,
      slug: comp.slug,
      symbol_type: comp.symType,
      value: comp.value || '',
      part_number: profile.part_number || comp.slug,
      package: (profile.package_types || [])[0] || null,
      description: profile.description || null,
      pins,
      position: { x: comp.x, y: comp.y },
      rotation_quarters: comp.rotation || 0
    };
  });

  // Unconnected pins: non-power, non-gnd components with pins missing a net
  const unconnected = [];
  for (const c of outComponents) {
    if (c.symbol_type === 'vcc' || c.symbol_type === 'gnd') continue;
    for (const pin of c.pins) {
      if (!pin.net) unconnected.push(`${c.designator}.${pin.name}`);
    }
  }

  const pcbJson = {
    format: 'schematic-designer-pcb-v1',
    project_name: editor.project.name,
    generated_at: new Date().toISOString(),
    grid_units: editor.GRID,
    components: outComponents,
    nets: namedNets,
    unconnected_pins: unconnected,
    stats: {
      component_count: outComponents.length,
      net_count: namedNets.length,
      unconnected_count: unconnected.length
    }
  };

  const blob = new Blob([JSON.stringify(pcbJson, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (editor.project.name || 'schematic').replace(/[^a-z0-9_\-]/gi, '_') + '_pcb.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── PCB sync notification ────────────────────────────────────────────────────
// Called from _status() and after saveProject() — keeps PCB frame aware of
// whether the schematic is ahead of the last imported PCB board.
function _notifyPcbSync() {
  const frame = document.getElementById('pcb-frame');
  const tab = openTabs.find(t => t.tabId === activeTabId);
