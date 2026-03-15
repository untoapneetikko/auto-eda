// ═══════════════════════════════════════════════════════════════
// SCHEMATIC IMPORTER — delegated to Python API
// ═══════════════════════════════════════════════════════════════
async function importSchematic(projectData, netlist, boardW, boardH) {
  const res = await fetch('/api/pcb/import-schematic', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      project: projectData,
      netlist: netlist || null,
      boardW: boardW || null,
      boardH: boardH || null,
    })
  });
  if (!res.ok) { let msg = 'Import failed'; try { const e = await res.json(); msg = e.detail || e.error || msg; } catch(_) { msg = res.statusText || msg; } throw new Error(msg); }
  return await res.json();
}


// ═══════════════════════════════════════════════════════════════
// AUTO PLACER — class body removed, see runAutoPlace() below
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// AUTO ROUTER — class body removed, see runAutoRoute() below
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// GERBER EXPORTER — class body removed, see exportGerber() / downloadGerberZip() below
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// KICAD PCB EXPORTER — class body removed, see exportKiCad() below
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// DRC — delegated to Python API
// ═══════════════════════════════════════════════════════════════
async function runDRC(board, dr, fpData) {
  const b = board || editor?.board;
  if (!b) return [];
  // Attach current design rules so backend uses them
  const payload = JSON.parse(JSON.stringify(b));
  if (typeof DR !== 'undefined') {
    payload.designRules = {
      clearance: DR.clearance, minTraceWidth: DR.minTraceWidth,
      edgeClearance: DR.edgeClearance, viaSize: DR.viaSize,
      viaDrill: DR.viaDrill, minAnnularRing: DR.minAnnularRing,
      drillClearance: DR.drillClearance,
    };
  }
  try {
    const res = await fetch('/api/pcb/drc', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    const violations = data.violations || data.errors || [];
    // Store globally for panel + canvas rendering
    window._drcViolations = violations;
    return violations;
  } catch(e) {
    console.error('DRC failed:', e);
    return [];
  }
}
