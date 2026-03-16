function openDRC(){ switchPcbSection('drc'); }

// ── Side-panel DRC (right panel under Layers) ────────────────
let _drcSidePanelOpen = false;
function toggleDrcPanel(){
  _drcSidePanelOpen = !_drcSidePanelOpen;
  const el = document.getElementById('drc-side-list');
  if(el) el.style.display = _drcSidePanelOpen ? 'block' : 'none';
}

async function runSideDRC(){
  if(!editor?.board) return;
  const badge = document.getElementById('drc-side-badge');
  const list = document.getElementById('drc-side-list');
  if(badge) badge.textContent = '...';
  if(list) list.innerHTML = '<div style="font-size:10px;color:var(--text-muted);padding:4px 0;">Running...</div>';
  // Open panel if closed
  if(!_drcSidePanelOpen) toggleDrcPanel();

  const violations = await runDRC(editor.board, DR, {});
  _drcResults = violations;
  window._drcViolations = violations;

  const errs = violations.filter(v=>v.sev==='ERROR');
  const warns = violations.filter(v=>v.sev==='WARNING');

  // Update badge
  if(badge){
    if(!errs.length && !warns.length){
      badge.innerHTML = '<span style="color:#22c55e;">0</span>';
    } else {
      let t = '';
      if(errs.length) t += `<span style="color:#ef4444;font-weight:700;">${errs.length}E</span>`;
      if(warns.length) t += `<span style="color:#facc15;font-weight:600;margin-left:3px;">${warns.length}W</span>`;
      badge.innerHTML = t;
    }
  }

  // Render list
  if(list){
    if(!violations.length){
      list.innerHTML = '<div style="font-size:11px;color:#22c55e;padding:6px 0;font-weight:600;">All checks passed</div>';
    } else {
      // Group by category
      const cats = {};
      const catLabels = {clearance:'Clearance',unconnected:'Unconnected',trace:'Trace',
        via:'Via',bounds:'Bounds',refs:'References',holes:'Holes',net:'Net Integrity',courtyard:'Courtyard'};
      for(const v of violations){
        const c = v.cat||'other';
        (cats[c]||(cats[c]=[])).push(v);
      }
      let html = '';
      for(const[cat,items] of Object.entries(cats)){
        const hasErr = items.some(v=>v.sev==='ERROR');
        html += `<div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:${hasErr?'#ef4444':'#facc15'};margin-top:6px;margin-bottom:2px;">${catLabels[cat]||cat} (${items.length})</div>`;
        for(const v of items){
          const col = v.sev==='ERROR' ? '#ef4444' : '#facc15';
          const hasPos = v.x!=null && v.y!=null;
          const args = hasPos ? `${v.x},${v.y},'${v.sev||'ERROR'}',${v.x1??'undefined'},${v.y1??'undefined'},${v.x2??'undefined'},${v.y2??'undefined'}` : '';
          html += `<div class="drc-side-item" style="font-size:10px;padding:3px 4px;margin:1px 0;border-left:2px solid ${col};cursor:${hasPos?'pointer':'default'};border-radius:2px;color:var(--text-dim);line-height:1.3;word-break:break-word;" ${hasPos?`onclick="drcGoto(${args})"`:''}>${v.msg||v.message||v.type}</div>`;
        }
      }
      list.innerHTML = html;
    }
  }

  // Trigger canvas re-render to show markers
  editor.render();
  // Also update the full DRC tab if it's open
  renderDRCTabResults();
  renderConflictTable();
}

function _updateDrcSideBadge(violations){
  const badge=document.getElementById('drc-side-badge');
  if(!badge) return;
  const errs=(violations||[]).filter(v=>v.sev==='ERROR');
  const warns=(violations||[]).filter(v=>v.sev==='WARNING');
  if(!errs.length&&!warns.length) badge.innerHTML='<span style="color:#22c55e;">0</span>';
  else{
    let t='';
    if(errs.length) t+=`<span style="color:#ef4444;font-weight:700;">${errs.length}E</span>`;
    if(warns.length) t+=`<span style="color:#facc15;font-weight:600;margin-left:3px;">${warns.length}W</span>`;
    badge.innerHTML=t;
  }
}

function drcGoto(x, y, sev, x1, y1, x2, y2){
  if(!editor) return;
  // Center the view on the error position and flash highlight
  const canvas = editor.canvas;
  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  editor.panX = cx - x * editor.scale - editor.offsetX;
  editor.panY = cy - y * editor.scale - editor.offsetY;
  // Store highlight with full info for render
  editor._drcHighlight = {x, y, sev: sev||'ERROR', x1, y1, x2, y2, time: Date.now()};
  editor.render();
  // Animate pulse for 2 seconds
  let _drcAnim;
  const animate = ()=>{
    if(!editor._drcHighlight) return;
    if(Date.now()-editor._drcHighlight.time > 2000){
      editor._drcHighlight = null; editor.render(); return;
    }
    editor.render();
    _drcAnim = requestAnimationFrame(animate);
  };
  _drcAnim = requestAnimationFrame(animate);
}

// ── Design Rules Tab ─────────────────────────────────────────
// Populate all DR inputs in the tab from the global DR object
function populateDRTab(){
  const m=(id,val)=>{const el=document.getElementById(id);if(el)el.value=val;};
  m('drt-min-trace',DR.minTraceWidth);
  m('drt-trace',DR.traceWidth);
  m('drt-clear',DR.clearance);
  m('drt-edge',DR.edgeClearance);
  m('drt-drill-clear',DR.drillClearance??0.25);
  m('drt-courtyard-clear',DR.packageGap??0.0);
  m('drt-via-size',DR.viaSize);
  m('drt-via-drill',DR.viaDrill);
  m('drt-annular',DR.minAnnularRing??0.15);
  m('drt-via-clear',DR.viaClearance??0.25);
  const tent=document.getElementById('drt-tented');if(tent)tent.checked=DR.tentedVias??true;
  const vip=document.getElementById('drt-via-in-pad');if(vip)vip.checked=DR.viaInPad??false;
  m('drt-board-thick',DR.boardThickness??1.6);
  m('drt-copper',DR.copperWeight);
  m('drt-silk',DR.silkscreenWidth??0.12);
  m('drt-corner-angle',DR.cornerAngle??90);
  m('drt-snap-radius',DR.snapRadius??2.0);
  updateDRTComputed();
}

// Update computed/informational fields
function updateDRTComputed(){
  const viaSize=parseFloat(document.getElementById('drt-via-size')?.value)||DR.viaSize;
  const viaDrill=parseFloat(document.getElementById('drt-via-drill')?.value)||DR.viaDrill;
  const annular=(viaSize-viaDrill)/2;
  const vc=document.getElementById('drt-via-computed');
  const viaClear=parseFloat(document.getElementById('drt-via-clear')?.value)||DR.viaClearance||0.25;
  const minAnnular=parseFloat(document.getElementById('drt-annular')?.value)||DR.minAnnularRing||0.15;
  if(vc){
    const annularOk=annular>=minAnnular;
    const annularStr=annularOk?(annular<0.2?' <span style="color:var(--yellow,#facc15)">caution</span>':' <span style="color:#22c55e">✓</span>'):`<span style="color:var(--red,#f87171)">⚠ too thin (min ${minAnnular}mm)</span>`;
    const clearOk=viaClear>=0.1;
    const clearStr=clearOk?' <span style="color:#22c55e">✓</span>':'<span style="color:var(--red,#f87171)">⚠ below 0.1mm</span>';
    vc.innerHTML=`Annular ring: <b>${annular.toFixed(3)}mm</b>${annularStr} &nbsp;|&nbsp; Via-to-via gap: <b>${viaClear.toFixed(2)}mm</b>${clearStr}`;
  }

  const thick=parseFloat(document.getElementById('drt-board-thick')?.value)||1.6;
  const drill=viaDrill;
  const aspect=thick/drill;
  const copper=parseFloat(document.getElementById('drt-copper')?.value)||1;
  // IPC-2221 current capacity: I = k * ΔT^0.44 * A^0.725, external layer k=0.048, A in sq-mils
  const traceW=parseFloat(document.getElementById('drt-min-trace')?.value)||DR.minTraceWidth;
  const h_mils=copper*1.4; // copper thickness in mils (1oz ≈ 1.4 mils)
  const a_sqmils=traceW*39.37*h_mils; // area in sq-mils
  const iMax_10=(0.048*Math.pow(10,0.44)*Math.pow(a_sqmils,0.725)).toFixed(2);
  const iMax_25=(0.048*Math.pow(25,0.44)*Math.pow(a_sqmils,0.725)).toFixed(2);
  const bc=document.getElementById('drt-board-computed');
  if(bc)bc.innerHTML=`Drill aspect ${aspect.toFixed(1)}:1 (board ${thick}mm, drill ${drill}mm)${aspect>10?' <span style="color:var(--red)">⚠ &gt;10:1</span>':' <span style="color:#22c55e">✓</span>'} &nbsp;·&nbsp; Min trace current cap: <b>${iMax_10}A</b> @ΔT=10°C, <b>${iMax_25}A</b> @ΔT=25°C`;
}

// Read inputs and apply to DR
function saveDRTab(){
  const g=(id,fb)=>parseFloat(document.getElementById(id)?.value)||fb;
  DR.minTraceWidth=g('drt-min-trace',0.15);
  DR.traceWidth=g('drt-trace',0.25);
  DR.clearance=g('drt-clear',0.2);
  DR.edgeClearance=g('drt-edge',0.5);
  DR.drillClearance=g('drt-drill-clear',0.25);
  DR.packageGap=g('drt-courtyard-clear',0.0);
  DR.viaSize=g('drt-via-size',1.0);
  DR.viaDrill=g('drt-via-drill',0.6);
  DR.minAnnularRing=g('drt-annular',0.15);
  DR.viaClearance=g('drt-via-clear',0.25);
  DR.tentedVias=document.getElementById('drt-tented')?.checked??true;
  DR.viaInPad=document.getElementById('drt-via-in-pad')?.checked??false;
  DR.boardThickness=g('drt-board-thick',1.6);
  DR.copperWeight=g('drt-copper',1.0);
  DR.silkscreenWidth=g('drt-silk',0.12);
  DR.cornerAngle=Math.min(90,Math.max(0,g('drt-corner-angle',90)));
  DR.snapRadius=Math.max(0.5,g('drt-snap-radius',2.0));
  // Sync layer modal inputs too
  const sm=(id,v)=>{const el=document.getElementById(id);if(el)el.value=v;};
  sm('dr-min-trace',DR.minTraceWidth);sm('dr-trace',DR.traceWidth);
  sm('dr-clear',DR.clearance);sm('dr-via-size',DR.viaSize);
  sm('dr-via-drill',DR.viaDrill);sm('dr-edge',DR.edgeClearance);sm('dr-copper',DR.copperWeight);
  // Update route width toolbar
  const rw=document.getElementById('route-width');if(rw)rw.value=DR.traceWidth;
  updateDRTComputed();
  _saveDRStorage();
  if(typeof editor!=='undefined'&&editor)editor.render();
  // Re-run DRC with a short debounce so rapid typing doesn't hammer it
  clearTimeout(_drDebounce);
  _drDebounce=setTimeout(runDRCTab,600);
}
let _drDebounce;

// Presets
function applyDRPreset(name){
  const presets={
    standard:{minTraceWidth:0.2,traceWidth:0.25,clearance:0.2,edgeClearance:0.5,
      drillClearance:0.25,viaSize:1.0,viaDrill:0.6,minAnnularRing:0.2,viaClearance:0.25,tentedVias:true,viaInPad:false,
      boardThickness:1.6,copperWeight:1,silkscreenWidth:0.12,cornerAngle:90,routeAngleStep:45,snapRadius:2.0},
    tight:{minTraceWidth:0.15,traceWidth:0.15,clearance:0.15,edgeClearance:0.3,
      drillClearance:0.2,viaSize:0.8,viaDrill:0.4,minAnnularRing:0.15,viaClearance:0.15,tentedVias:true,viaInPad:false,
      boardThickness:1.6,copperWeight:1,silkscreenWidth:0.1,cornerAngle:45,routeAngleStep:45,snapRadius:1.5},
    RF:{minTraceWidth:0.15,traceWidth:0.15,clearance:0.2,edgeClearance:0.5,
      drillClearance:0.25,viaSize:0.8,viaDrill:0.4,minAnnularRing:0.15,viaClearance:0.2,tentedVias:true,viaInPad:false,
      boardThickness:0.5,copperWeight:1,silkscreenWidth:0.1,cornerAngle:0,routeAngleStep:45,snapRadius:1.5},
    power:{minTraceWidth:0.5,traceWidth:0.5,clearance:0.25,edgeClearance:1.0,
      drillClearance:0.3,viaSize:1.2,viaDrill:0.8,minAnnularRing:0.2,viaClearance:0.3,tentedVias:false,viaInPad:true,
      boardThickness:1.6,copperWeight:2,silkscreenWidth:0.15,cornerAngle:90,routeAngleStep:90,snapRadius:2.5},
  };
  const p=presets[name];if(!p)return;
  Object.assign(DR,p);
  populateDRTab();
  _saveDRStorage();
  runDRCTab();
}

// DRC results storage for filter re-render
let _drcResults=[];
async function runDRCTab(){
  if(!editor.board){
    document.getElementById('drc-tab-results').innerHTML=
      '<div style="padding:20px;color:var(--text-muted);text-align:center;">No board loaded — go to Layout and load or import a board first.</div>';
    document.getElementById('drc-tab-summary').innerHTML='';
    ['clearance','unconnected','trace','via','bounds','refs','holes','net','courtyard'].forEach(c=>{
      const el=document.getElementById('drc-check-'+c);if(el){el.className='drc-check-badge';}
    });
    return;
  }
  const btn=document.getElementById('drc-tab-run-btn');
  if(btn){btn.textContent='⏳ Running…';btn.disabled=true;}
  // Fetch footprint data for courtyard checks
  const fpData={};
  const fpNames=[...new Set((editor.board.components||[]).map(c=>c.footprint).filter(Boolean))];
  await Promise.all(fpNames.map(async fp=>{
    try{const r=await fetch(`/api/footprints/${encodeURIComponent(fp)}`);if(r.ok)fpData[fp]=await r.json();}catch(_){}
  }));
  _drcResults=await runDRC(editor.board,DR,fpData);
  window._drcViolations=_drcResults;
  renderDRCTabResults();
  renderConflictTable();
  // Update side panel badge too
  _updateDrcSideBadge(_drcResults);
  editor.render();
  if(btn){btn.textContent='▶ Run DRC';btn.disabled=false;}
}

function renderConflictTable(){
  const panel=document.getElementById('conflict-panel');
  const tbody=document.getElementById('ct-tbody');
  const countEl=document.getElementById('ct-count');
  if(!panel||!tbody)return;
  const CONFLICT_TYPES=new Set(['NET_CONFLICT','NET_MISMATCH','TRACE_CROSSING']);
  const conflicts=(_drcResults||[]).filter(e=>CONFLICT_TYPES.has(e.type));
  if(!conflicts.length){panel.classList.add('empty');return;}
  panel.classList.remove('empty');
  countEl.textContent=`${conflicts.length} issue${conflicts.length!==1?'s':''}`;
  const FIX={
    NET_CONFLICT:'Re-place components so pads don\'t overlap, or verify both belong to the same net.',
    NET_MISMATCH:'Reroute the trace to connect to the correct net pad, or fix the net assignment in the schematic.',
    TRACE_CROSSING:'Reroute one of the crossing traces to a different layer (add via) or take a different path.'
  };
  tbody.innerHTML=conflicts.map(e=>`
    <tr>
      <td><span class="ct-type ${e.type}">${e.type.replace('_',' ')}</span></td>
      <td style="color:var(--text-dim);max-width:260px;">${e.msg}</td>
      <td style="color:var(--text-muted);max-width:200px;">${FIX[e.type]||'Review and correct manually.'}</td>
    </tr>`).join('');
}

function renderDRCTabResults(){
  const errs=_drcResults;
  const showWarn=document.getElementById('drc-show-warnings')?.checked!==false;
  const visible=errs.filter(e=>e.sev==='ERROR'||(showWarn&&e.sev==='WARNING'));
  const errCount=errs.filter(e=>e.sev==='ERROR').length;
  const warnCount=errs.filter(e=>e.sev==='WARNING').length;
  const passCount=9-new Set(errs.map(e=>e.cat)).size; // 9 check categories
  // Summary
  const sum=document.getElementById('drc-tab-summary');
  if(sum){
    if(!errCount&&!warnCount)
      sum.innerHTML='<span style="color:#22c55e;font-weight:700;">✓ All checks passed</span>';
    else
      sum.innerHTML=(errCount?`<span style="color:var(--red);font-weight:700;">● ${errCount} error${errCount!==1?'s':''}</span> `:'')
        +(warnCount?`<span style="color:var(--yellow);">● ${warnCount} warning${warnCount!==1?'s':''}</span>`:'');
  }
  // Update category badges
  const cats={clearance:0,unconnected:0,trace:0,via:0,bounds:0,refs:0,holes:0,net:0,courtyard:0};
  const catSev={};
  for(const e of errs){
    if(!e.cat)continue;
    cats[e.cat]=(cats[e.cat]||0)+1;
    if(e.sev==='ERROR')catSev[e.cat]='fail';
    else if(!catSev[e.cat])catSev[e.cat]='warn';
  }
  for(const cat of Object.keys(cats)){
    const el=document.getElementById('drc-check-'+cat);if(!el)continue;
    if(cats[cat]===0)el.className='drc-check-badge pass';
    else el.className='drc-check-badge '+(catSev[cat]||'warn');
  }
  // Group results by category
  const groups={};
  const catLabels={clearance:'Clearances',unconnected:'Unconnected Nets',trace:'Trace Width / Geometry',
    via:'Via Geometry',bounds:'Board Bounds',refs:'Component References',holes:'Drill / Holes',net:'Net Integrity',courtyard:'Package Gap'};
  for(const e of visible){
    const c=e.cat||'other';
    (groups[c]||(groups[c]=[])).push(e);
  }
  const res=document.getElementById('drc-tab-results');
  if(!visible.length){
    res.innerHTML='<div class="drc-item drc-ok" style="margin:16px;">✓ No issues found — design looks clean!</div>';
    return;
  }
  let html='';
  for(const[cat,items]of Object.entries(groups)){
    html+=`<div class="drc-group-header">${catLabels[cat]||cat} <span style="font-weight:400;color:var(--text-muted);">(${items.length})</span></div>`;
    for(const e of items){
      const hasPos=e.x!=null&&e.y!=null;
      const args2=hasPos?`${e.x},${e.y},'${e.sev||'ERROR'}',${e.x1??'undefined'},${e.y1??'undefined'},${e.x2??'undefined'},${e.y2??'undefined'}`:'';
      html+=`<div class="drc-item ${e.sev==='ERROR'?'drc-error':'drc-warn'}" style="${hasPos?'cursor:pointer;':''}" ${hasPos?`onclick="drcGoto(${args2});switchPcbSection('layout')"`:''}><span style="font-size:10px;font-weight:700;letter-spacing:.04em;opacity:.7;">${e.type}</span> ${e.msg||e.message}</div>`;
    }
  }
  res.innerHTML=html;
}

// ── Layer Manager ────────────────────────────────────────────
function openLayerManager(){
  // Populate layer table
  const tbody=document.getElementById('layer-table-body');tbody.innerHTML='';
  for(const[name,lyr]of Object.entries(editor.layers)){
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input value="${lyr.displayName||name}" style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text-dim);padding:2px 5px;font-size:11px;width:110px;" onchange="editor.layers['${name}'].displayName=this.value.trim()||'${name}';buildLayerPanel();updateWorkLayerBadge();"></td>
      <td><input type="color" value="${lyr.color}" onchange="editor.layers['${name}'].color=this.value;buildLayerPanel();editor.render()"></td>
      <td><input type="checkbox" ${lyr.visible?'checked':''} onchange="editor.layers['${name}'].visible=this.checked;buildLayerPanel();editor.render()"></td>
      <td><input type="radio" name="active-layer" ${lyr.active?'checked':''} onchange="Object.values(editor.layers).forEach(l=>l.active=false);editor.layers['${name}'].active=true;editor.routeLayer='${name}';editor.workLayer='${name}';buildLayerPanel();updateWorkLayerBadge();editor.render();"></td>
      <td style="font-size:11px;color:var(--text-muted);font-family:monospace;">${name}</td>`;
    tbody.appendChild(tr);
  }
  // Populate DR fields
  document.getElementById('dr-min-trace').value=DR.minTraceWidth;
  document.getElementById('dr-trace').value=DR.traceWidth;
  document.getElementById('dr-clear').value=DR.clearance;
  document.getElementById('dr-via-size').value=DR.viaSize;
  document.getElementById('dr-via-drill').value=DR.viaDrill;
  document.getElementById('dr-edge').value=DR.edgeClearance;
  document.getElementById('dr-copper').value=DR.copperWeight;
  openModal('layer-modal');
}
function saveLayerRules(){
  DR.minTraceWidth=parseFloat(document.getElementById('dr-min-trace').value)||0.15;
  DR.traceWidth=parseFloat(document.getElementById('dr-trace').value)||0.25;
  DR.clearance=parseFloat(document.getElementById('dr-clear').value)||0.2;
  DR.viaSize=parseFloat(document.getElementById('dr-via-size').value)||1.0;
  DR.viaDrill=parseFloat(document.getElementById('dr-via-drill').value)||0.6;
  DR.edgeClearance=parseFloat(document.getElementById('dr-edge').value)||0.5;
  DR.copperWeight=parseFloat(document.getElementById('dr-copper').value)||1.0;
  document.getElementById('route-width').value=DR.traceWidth;
  document.getElementById('via-size-input').value=DR.viaSize;
  document.getElementById('via-drill-input').value=DR.viaDrill;
  closeModal('layer-modal');
}

// ── Gerber ───────────────────────────────────────────────────
async function exportGerber() {
  const boardId = editor?.board?.id || _activeBoardId;
  if (!boardId) { alert('Save the board first before exporting.'); return; }
  const res = await fetch(`/api/pcb/${boardId}/export/gerber`);
  if (!res.ok) { alert('Gerber export failed: ' + (await res.text())); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (editor?.board?.title || editor?.board?.name || 'board') + '_gerbers.zip';
  a.click();
  URL.revokeObjectURL(url);
}
async function downloadGerberZip() {
  return exportGerber();
}

// ── KiCad Export ─────────────────────────────────────────────
async function exportKiCad() {
  const boardId = editor?.board?.id || _activeBoardId;
  if (!boardId) { alert('Save the board first before exporting.'); return; }
  const res = await fetch(`/api/pcb/${boardId}/export/kicad`);
  if (!res.ok) { alert('KiCad export failed: ' + (await res.text())); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (editor?.board?.title || editor?.board?.name || 'board') + '.kicad_pcb';
  a.click();
  URL.revokeObjectURL(url);
}

