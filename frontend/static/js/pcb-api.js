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
  const boardId = editor?.board?.id || _activeBoardId;
  try {
    let res;
    if (boardId) {
      res = await fetch(`/api/pcb/${boardId}/drc`, { method: 'POST' });
    } else {
      res = await fetch('/api/pcb/drc', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ board: board || editor?.board || {} })
      });
    }
    const data = await res.json();
    const violations = data.violations || data.errors || [];
    const resultsEl = document.getElementById('drc-results') || document.getElementById('drc-output');
    if (resultsEl) {
      if (!violations.length) {
        resultsEl.innerHTML = '<div style="color:#22c55e;padding:8px;">✓ No DRC violations</div>';
      } else {
        resultsEl.innerHTML = violations.map(v =>
          `<div style="color:#ef4444;padding:4px 8px;border-left:3px solid #ef4444;margin:4px 0;">${v.type}: ${v.message}</div>`
        ).join('');
      }
    }
    return violations;
  } catch(e) {
    console.error('DRC failed:', e);
    return [];
  }
}
