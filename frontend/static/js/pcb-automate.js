// ── Import Schematic ─────────────────────────────────────────
let selectedProjectId=null;
async function openImportModal(){
  document.getElementById('import-status').textContent='Loading projects…';
  openModal('import-modal');
  try{
    const r=await fetch('/api/projects');
    const projects=await r.json();
    const ul=document.getElementById('project-list');
    if(!projects.length){ul.innerHTML='<div style="color:var(--text-muted);font-size:12px;">No saved projects found. Create a schematic first.</div>';
      document.getElementById('import-status').textContent='';return;}
    ul.innerHTML='';selectedProjectId=null;
    for(const p of projects){
      const d=document.createElement('div');
      d.style.cssText='padding:7px 10px;border-radius:5px;cursor:pointer;border:1px solid var(--border);margin-bottom:5px;transition:all .15s;';
      d.innerHTML=`<div style="font-weight:700;font-size:12px;color:var(--text)">${p.name||p.id}</div><div style="font-size:11px;color:var(--text-muted);">${new Date(p.updated_at||0).toLocaleString()}</div>`;
      d.onclick=async()=>{
        selectedProjectId=p.id;
        ul.querySelectorAll('div').forEach(x=>{x.style.borderColor='var(--border)';x.style.background='';});
        d.style.borderColor='var(--accent)';d.style.background='var(--accent-dim)';
        // Preview example groups in this project
        try{
          const pr=await fetch(`/api/projects/${p.id}`);
          const pdata=await pr.json();
          const allComps=pdata.components||[];
          const egIds=new Set(allComps.filter(c=>c._exampleGroupId).map(c=>c._exampleGroupId));
          const egComps=allComps.filter(c=>c._exampleGroupId).length;
          const free=allComps.filter(c=>c.symType!=='vcc'&&c.symType!=='gnd'&&!c._exampleGroupId).length;
          const st=document.getElementById('import-status');
          if(egIds.size>0){
            st.innerHTML=`<span style="color:var(--accent)">✦ ${egIds.size} example group${egIds.size>1?'s':''} (${egComps} comps) — real layout_example data will be used</span>`+(free?` + ${free} other`:'');
          }else{
            const physCount=allComps.filter(c=>c.symType!=='vcc'&&c.symType!=='gnd').length;
            st.textContent=`${physCount} components — no example groups (standard grid layout)`;
          }
        }catch(_){}
      };
      ul.appendChild(d);
    }
    document.getElementById('import-status').textContent=`${projects.length} project(s) found`;
  }catch(e){document.getElementById('import-status').textContent='Error: '+e.message;}
}

async function doImport(){
  if(!selectedProjectId){document.getElementById('import-status').textContent='Select a project first';return;}
  document.getElementById('import-status').textContent='Converting…';
  try{
    const r=await fetch(`/api/projects/${selectedProjectId}`);
    const proj=await r.json();
    const bw=parseFloat(document.getElementById('imp-w').value)||40;
    const bh=parseFloat(document.getElementById('imp-h').value)||30;
    const st=document.getElementById('import-status');
    const pcb=await importSchematic(proj);
    if(!pcb){st.textContent='No physical components found';return;}
    const eg=pcb._exGroupCount||0,ec=pcb._exCompCount||0;
    delete pcb._exGroupCount;delete pcb._exCompCount;
    closeModal('import-modal');
    await animateBoardLoad(pcb);
    afterLoad();
    await saveBoard(); // auto-save so the board tab appears immediately
    // Flash placement summary in status bar
    const sb=document.getElementById('status-bar');
    if(sb&&eg>0){
      const msg=`✦ ${eg} example group${eg>1?'s':''} pre-arranged (${ec} comps)`;
      const prev=sb.textContent;sb.style.color='var(--accent)';sb.textContent=msg;
      setTimeout(()=>{sb.style.color='';sb.textContent=prev;},5000);
    }
  }catch(e){document.getElementById('import-status').textContent='Error: '+e.message;}
}

// ── Auto Route ────────────────────────────────────────────
async function runAutoRoute() {
  // board ID lives in editor.board.id (set after save) or _activeBoardId
  const boardId = editor?.board?.id || _activeBoardId;
  const btn = document.getElementById('btn-autoroute');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Routing…'; }
  try {
    let res;
    if (boardId) {
      await saveBoard();
      res = await fetch(`/api/pcb/${boardId}/autoroute`, { method: 'POST' });
    } else {
      res = await fetch('/api/pcb/autoroute', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ board: editor?.board || {} })
      });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || 'Autoroute failed');
    const traces = data.traces ?? data.board?.traces;
    if (traces && editor?.board) {
      editor.board.traces = traces;
      editor._snapshot();
      editor.render(); rebuildCompList(); updateBoardInfo();
    }
    const msg = `Routed ${data.routed ?? '?'}/${data.total ?? '?'} nets`;
    const status = document.getElementById('status-bar') || document.getElementById('route-status');
    if (status) status.textContent = msg;
    if (btn) { btn.disabled = false; btn.textContent = '⚡ Auto-Route'; }
  } catch(e) {
    alert('Autoroute failed: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = '⚡ Auto-Route'; }
  }
}

// ── Auto Place ───────────────────────────────────────────────
async function runAutoPlace() {
  const btn = document.getElementById('btn-autoplace');
  const hasTraces = (editor?.board?.traces || []).some(t => (t.segments || []).length > 0);
  if (btn) { btn.disabled = true; btn.textContent = hasTraces ? '⏳ Optimizing…' : '⏳ Placing…'; }
  try {
    const boardId = editor?.board?.id || _activeBoardId;
    let res;
    const clearance = DR.packageGap ?? 1.0;
    if (boardId) {
      await saveBoard();
      res = await fetch(`/api/pcb/${boardId}/autoplace?clearance_mm=${clearance}`, { method: 'POST' });
    } else {
      res = await fetch('/api/pcb/autoplace', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ board: editor?.board || {}, clearance_mm: clearance })
      });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || 'Auto-place failed');
    if (data.components && editor?.board) {
      editor.board.components = data.components;
      // Trace-aware mode: backend also returns updated traces with
      // endpoints snapped to the new component positions.
      if (data.traces) {
        editor.board.traces = data.traces;
      }
      editor._snapshot();
      editor.render(); rebuildCompList(); updateBoardInfo();
    }
  } catch(e) {
    alert('Auto-place failed: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '📐 Auto-Place'; }
  }
}

// ── Place from Schematic ──────────────────────────────────────
// Iterative legalization: push overlapping components apart while
// keeping them as close to their current (schematic-derived) positions
// as possible. Uses DR.packageGap and DR.edgeClearance.
function _cyHalfExtents(comp, fpData) {
  const fp = fpData && fpData[comp.footprint];
  let hw, hh;
  if (fp?.courtyard) { const cy = fp.courtyard; hw = cy.w / 2; hh = cy.h / 2; }
  else {
    hw = 1.5; hh = 1.5;
    for (const p of (comp.pads || [])) {
      hw = Math.max(hw, Math.abs(p.x) + (p.size_x || 0.5));
      hh = Math.max(hh, Math.abs(p.y) + (p.size_y || 0.5));
    }
  }
  const rot = Math.abs((comp.rotation || 0)) % 180;
  return (rot > 45 && rot < 135) ? {hw: hh, hh: hw} : {hw, hh};
}
async function legalize(comps, fpData, W, H) {
  const grid = editor.gridSize || 0.5;
  const snapV = v => Math.round(v / grid) * grid;
  const edge = DR.edgeClearance ?? 0.5;
  const cl = DR.packageGap ?? 0;
  const MAX_ITER = 120;
  for (let iter = 0; iter < MAX_ITER; iter++) {
    let moved = false;
    for (let i = 0; i < comps.length; i++) {
      const a = comps[i];
      const {hw: ahw, hh: ahh} = _cyHalfExtents(a, fpData);
      for (let j = i + 1; j < comps.length; j++) {
        const b = comps[j];
        const {hw: bhw, hh: bhh} = _cyHalfExtents(b, fpData);
        const ox = Math.min(a.x + ahw + cl, b.x + bhw) - Math.max(a.x - ahw - cl, b.x - bhw);
        const oy = Math.min(a.y + ahh + cl, b.y + bhh) - Math.max(a.y - ahh - cl, b.y - bhh);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        const push = Math.min(ox, oy) / 2 + grid;
        if (ox <= oy) {
          if (a.x <= b.x) { a.x -= push; b.x += push; } else { a.x += push; b.x -= push; }
        } else {
          if (a.y <= b.y) { a.y -= push; b.y += push; } else { a.y += push; b.y -= push; }
        }
        a.x = snapV(Math.max(ahw + edge, Math.min(W - ahw - edge, a.x)));
        a.y = snapV(Math.max(ahh + edge, Math.min(H - ahh - edge, a.y)));
        b.x = snapV(Math.max(bhw + edge, Math.min(W - bhw - edge, b.x)));
        b.y = snapV(Math.max(bhh + edge, Math.min(H - bhh - edge, b.y)));
      }
    }
    if (!moved) break;
    if (iter % 15 === 14) await new Promise(r => setTimeout(r, 0));
  }
}

async function runPlaceFromSchematic(){
  if(!editor.board){alert('No board loaded');return;}
  const board=editor.board;
  const comps=board.components||[];
  if(!comps.length){alert('No components to place');return;}

  const btn=document.getElementById('btn-place-schematic');
  const orig=btn.textContent;
  btn.textContent='⏳ Loading…';btn.disabled=true;

  try{
    // 1. Resolve project: use linked projectId if available, otherwise auto-detect
    //    by fetching all projects and picking the one whose designators best match
    //    the board's component refs.
    let project=null;
    if(board.projectId){
      const r=await fetch(`/api/projects/${encodeURIComponent(board.projectId)}`);
      if(r.ok)project=await r.json();
    }
    if(!project){
      btn.textContent='⏳ Finding project…';
      const listR=await fetch('/api/projects');
      const list=listR.ok?await listR.json():[];
      const boardRefs=new Set((board.components||[]).map(c=>c.ref).filter(Boolean));
      // Fetch all projects in parallel and score by matching designators
      const scored=await Promise.all(list.map(async p=>{
        try{
          const r=await fetch(`/api/projects/${encodeURIComponent(p.id)}`);
          if(!r.ok)return null;
          const data=await r.json();
          const matches=(data.components||[]).filter(c=>boardRefs.has(c.designator)).length;
          return{data,matches};
        }catch(_){return null;}
      }));
      const best=scored.filter(Boolean).sort((a,b)=>b.matches-a.matches)[0];
      if(!best||best.matches===0)
        throw new Error('No matching schematic project found.\nMake sure the schematic has the same component designators as this board (U1, C1 …).');
      project=best.data;
    }
    const schComps=project.components||[];

    // 2. Build designator → {x, y, rotation} from Library Schematic Example positions
    //    _exampleX/_exampleY are the positions within the component's example circuit
    //    (a child of the Library Component) — these reflect the real application topology.
    //    Fall back to main-schematic x/y if _exampleX is absent.
    //    rotation is stored as quarter-turns (0-3); multiply by 90 for PCB degrees.
    const schMap=new Map();
    for(const sc of schComps){
      if(!sc.designator)continue;
      schMap.set(sc.designator,{
        x: sc._exampleX??sc.x,
        y: sc._exampleY??sc.y,
        rotation: (sc.rotation??0)*90,
      });
    }

    // 3. Fetch footprint courtyard data
    const fpData={};
    const fpNames=[...new Set(comps.map(c=>c.footprint).filter(Boolean))];
    btn.textContent='⏳ Footprints…';
    await Promise.all(fpNames.map(async fp=>{
      try{const fr=await fetch(`/api/footprints/${encodeURIComponent(fp)}`);if(fr.ok)fpData[fp]=await fr.json();}catch(_){}
    }));

    // 4. Map schematic positions to PCB comps
    //    Scale schematic pixels → mm (same constant as importer)
    const SCH_TO_MM=0.075;
    const W=board.board?.width||100, H=board.board?.height||80;
    const edge=DR.edgeClearance??0.5;
    const snap=v=>Math.round(v/(editor.gridSize||0.5))*(editor.gridSize||0.5);

    const matched=[]; // {comp, sx, sy, rotation} in scaled mm before centering
    for(const comp of comps){
      const sch=schMap.get(comp.ref);
      if(!sch)continue;
      matched.push({comp, sx:sch.x*SCH_TO_MM, sy:sch.y*SCH_TO_MM, rotation:sch.rotation});
    }
    if(!matched.length)throw new Error('No PCB components matched schematic designators.\nCheck that designators (U1, C1 …) match between schematic and board.');

    // 5. Compute bounding box of schematic-derived positions
    let mnX=1e9,mxX=-1e9,mnY=1e9,mxY=-1e9;
    for(const{sx,sy}of matched){
      if(sx<mnX)mnX=sx;if(sx>mxX)mxX=sx;
      if(sy<mnY)mnY=sy;if(sy>mxY)mxY=sy;
    }
    const schW=Math.max(mxX-mnX,1), schH=Math.max(mxY-mnY,1);
    const schCX=(mnX+mxX)/2, schCY=(mnY+mxY)/2;
    const boardCX=W/2, boardCY=H/2;

    // Scale to fit board (with edge clearance + 5mm component-body margin)
    // Never scale up more than ×3 so loosely-placed schematics don't scatter too far
    const fitMargin=edge+5;
    const scale=Math.min(3, (W-2*fitMargin)/schW, (H-2*fitMargin)/schH);

    // 6. Apply positions and rotations
    // Schematic rotation (quarter-turns × 90°) maps directly to PCB footprint rotation
    editor._snapshot();
    for(const{comp,sx,sy,rotation}of matched){
      comp.x=snap(boardCX+(sx-schCX)*scale);
      comp.y=snap(boardCY+(sy-schCY)*scale);
      comp.rotation=rotation;
    }
    // Components with no schematic match keep their existing positions
    const matchedIds=new Set(matched.map(m=>m.comp.id));
    for(const comp of comps){
      if(!matchedIds.has(comp.id)){comp.x=snap(comp.x);comp.y=snap(comp.y);}
    }

    // 7. Legalize — push apart any courtyard overlaps
    btn.textContent='⏳ Legalising…';
    await legalize(comps,fpData,W,H);

    // 8. Clear stale traces and commit
    board.traces=[];
    editor._snapshot();
    editor.render();rebuildCompList();updateBoardInfo();
    const unmatched=comps.length-matched.length;
    btn.textContent=`✓ ${matched.length} placed${unmatched?` (${unmatched} unmatched)`:''}`;btn.disabled=false;
    setTimeout(()=>btn.textContent=orig,2500);
  }catch(e){
    editor.undo();
    btn.textContent=orig;btn.disabled=false;
  }
}

// ── DRC modal (quick toolbar shortcut) ───────────────────────
