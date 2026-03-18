// ── Layer Panel ──────────────────────────────────────────────
function buildLayerPanel(){
  const ul=document.getElementById('layer-list');ul.innerHTML='';
  const wl=editor.workLayer;
  for(const[name,lyr]of Object.entries(editor.layers)){
    const isWork=name===wl;
    const d=document.createElement('div');
    d.className='layer-item'+(lyr.visible?'':' hidden')+(isWork?' work':'');

    // Color dot — click opens inline color picker
    const dot=document.createElement('div');
    dot.className='layer-dot'; dot.style.background=lyr.color;
    dot.title='Click to change color';
    const cp=document.createElement('input');
    cp.type='color'; cp.value=lyr.color;
    cp.style.cssText='position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;padding:0;border:none;';
    cp.oninput=e=>{e.stopPropagation();lyr.color=cp.value;dot.style.background=lyr.color;editor.render();updateWorkLayerBadge();};
    cp.onclick=e=>e.stopPropagation();
    dot.appendChild(cp);

    // Star (active indicator)
    const star=document.createElement('span');
    star.className='layer-work-star'; star.textContent=isWork?'★':'';

    // Name — double-click to rename
    const nm=document.createElement('span');
    nm.className='layer-name'; nm.textContent=lyr.displayName||name;
    nm.title='Double-click to rename';
    nm.ondblclick=e=>{
      e.stopPropagation();
      nm.contentEditable='true'; nm.focus();
      const range=document.createRange(); range.selectNodeContents(nm);
      const sel=window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      nm.onblur=nm.onkeydown=function(ev){
        if(ev.type==='keydown'&&ev.key!=='Enter'&&ev.key!=='Escape')return;
        if(ev.type==='keydown'&&ev.key==='Escape'){nm.textContent=lyr.displayName||name;}
        else{lyr.displayName=nm.textContent.trim()||name;}
        nm.contentEditable='false'; nm.onblur=nm.onkeydown=null;
        buildLayerPanel(); updateWorkLayerBadge();
        if(ev.preventDefault)ev.preventDefault();
      };
    };

    // Eye toggle
    const eye=document.createElement('span');
    eye.className='layer-eye'; eye.textContent=lyr.visible?'👁':'○';
    eye.title='Toggle visibility';
    eye.onclick=e=>{
      e.stopPropagation();
      lyr.visible=!lyr.visible;
      buildLayerPanel();
      editor.render();
      // Sync to 3D viewer if open
      const map3d={'F.Cu':'cu_top','B.Cu':'cu_bot','F.Mask':'sm_top','B.Mask':'sm_bot','F.Paste':'sp_top','B.Paste':'sp_bot'};
      const lid=map3d[name];
      if(lid&&typeof viewer3d!=='undefined'&&viewer3d){
        if(typeof _3dLayerVis!=='undefined') _3dLayerVis[lid]=lyr.visible;
        viewer3d.setLayerVisible(lid,lyr.visible);
        if(typeof build3DLayerPanel==='function') build3DLayerPanel();
      }
    };

    d.appendChild(dot); d.appendChild(star); d.appendChild(nm); d.appendChild(eye);
    d.onclick=()=>{
      if(nm.contentEditable==='true')return;
      if(name==='Vias'){lyr.visible=!lyr.visible;buildLayerPanel();editor.render();return;} // Vias: click toggles visibility only
      _setWorkLayer(name);
    };
    ul.appendChild(d);
  }
}

// Shared helper — sets work layer, syncs active flag, refreshes panel + badge
function _setWorkLayer(name){
  if(!editor?.layers[name])return;
  editor.workLayer=name;
  // Keep layers[k].active in sync so zone/area/draw tools always have a valid active layer
  for(const k of Object.keys(editor.layers)) editor.layers[k].active=(k===name);
  buildLayerPanel();
  updateWorkLayerBadge();
  editor.render();
}

// ── Toolbar layer-picker dropdown (works in both ?app=1 and ?embedded=1 modes) ──
function openLayerPickerDropdown(){
  // Remove any existing dropdown
  const old=document.getElementById('layer-picker-dropdown');
  if(old){old.remove();return;}
  const badge=document.getElementById('work-layer-badge');
  if(!badge||!editor)return;
  const rect=badge.getBoundingClientRect();
  const drop=document.createElement('div');
  drop.id='layer-picker-dropdown';
  drop.style.cssText=`position:fixed;top:${rect.bottom+4}px;left:${rect.left}px;z-index:9999;background:var(--surface,#1a1c2e);border:1px solid var(--border,#2e3250);border-radius:6px;box-shadow:0 4px 20px rgba(0,0,0,0.5);padding:4px;min-width:180px;`;
  for(const[name,lyr]of Object.entries(editor.layers)){
    const isWork=name===editor.workLayer;
    const row=document.createElement('div');
    row.style.cssText=`display:flex;align-items:center;gap:7px;padding:5px 8px;border-radius:4px;cursor:pointer;font-size:12px;font-weight:${isWork?'700':'400'};color:${isWork?lyr.color:'var(--text,#cdd6f4)'};background:${isWork?lyr.color+'22':'transparent'};`;
    row.innerHTML=`<span style="width:11px;height:11px;border-radius:2px;background:${lyr.color};flex-shrink:0;display:inline-block;"></span><span style="flex:1;">${lyr.displayName||name}</span>${isWork?'<span style="font-size:10px;">★</span>':''}`;
    row.onmouseenter=()=>row.style.background=lyr.color+'33';
    row.onmouseleave=()=>row.style.background=isWork?lyr.color+'22':'transparent';
    row.onclick=()=>{_setWorkLayer(name);drop.remove();};
    drop.appendChild(row);
  }
  document.body.appendChild(drop);
  // Dismiss on outside click
  const dismiss=e=>{if(!drop.contains(e.target)&&e.target!==badge){drop.remove();document.removeEventListener('mousedown',dismiss,true);}};
  setTimeout(()=>document.addEventListener('mousedown',dismiss,true),0);
}

function updateWorkLayerBadge(){
  const wl=editor.workLayer; const lyr=editor.layers[wl]; if(!lyr)return;
  const name=lyr.displayName||wl;
  // toolbar badge
  const badge=document.getElementById('work-layer-badge');
  if(badge){badge.textContent=name;badge.style.color=lyr.color;badge.style.borderColor=lyr.color+'66';badge.style.background=lyr.color+'18';}
  // right-panel active badge
  const ab=document.getElementById('active-layer-badge');
  if(ab){ab.textContent=name;ab.style.color=lyr.color;ab.style.borderColor=lyr.color+'66';ab.style.background=lyr.color+'18';}
}

// ── Comp List ────────────────────────────────────────────────
let _lastSelCiEl=null; // track last .ci highlight — avoids querySelectorAll on every click
function rebuildCompList(){
  const ul=document.getElementById('comp-list');ul.innerHTML='';
  _lastSelCiEl=null;
  const comps=editor.board?.components||[];
  const schRefs=editor.board?.schematicRefs||null;

  // Build lookup by ref for quick presence check
  const activeByRef=new Map(comps.map(c=>[c.ref||c.id,c]));

  // Determine the full ordered list to display:
  // schematicRefs (if present) + any board components not in schematicRefs
  const rows=[];
  if(schRefs&&schRefs.length){
    for(const sr of schRefs){
      const ref=sr.ref||sr.id;
      const live=activeByRef.get(ref);
      rows.push({comp:live||null,ref,value:sr.value||'',footprint:sr.footprint||'',schRef:sr,missing:!live});
    }
    // Also add any components present on board but not in schematicRefs (manually added)
    const schRefSet=new Set(schRefs.map(s=>s.ref));
    for(const c of comps){
      if(!schRefSet.has(c.ref||c.id)) rows.push({comp:c,ref:c.ref||c.id,value:c.value||'',footprint:c.footprint||'',missing:false});
    }
  } else {
    for(const c of comps) rows.push({comp:c,ref:c.ref||c.id,value:c.value||'',footprint:c.footprint||'',missing:false});
  }

  const totalVisible=rows.filter(r=>!r.missing).length;
  const totalMissing=rows.filter(r=>r.missing).length;
  const countLabel=rows.length?(totalMissing?`(${totalVisible}/${rows.length})`:`(${rows.length})`):'';
  document.getElementById('comp-count').textContent=countLabel;

  if(!rows.length){ul.innerHTML='<div style="padding:8px;color:var(--text-muted);font-size:11px;">No components</div>';return;}

  for(const row of rows){
    const d=document.createElement('div');
    d.className='ci'+(row.missing?' ci-missing':'');
    if(row.comp) d.id='ci-'+row.comp.id;
    d.innerHTML=row.missing
      ? `<span class="ci-ref ci-ref-missing">${esc(row.ref)}</span><div style="flex:1;overflow:hidden"><div class="ci-val" style="color:var(--text-muted);text-decoration:line-through">${esc(row.value)}</div><div class="ci-fp" style="color:var(--text-muted)">${esc(row.footprint)}</div></div><span class="ci-missing-badge">missing</span>`
      : `<span class="ci-ref">${esc(row.ref)}</span><div style="flex:1;overflow:hidden"><div class="ci-val">${esc(row.value)}</div><div class="ci-fp">${esc(row.footprint)}</div></div>`;
    if(row.missing){
      d.title='Click to restore this component';
      d.onclick=()=>{
        // Restore: clone the original schRef component data, place it in staging area
        const restored=JSON.parse(JSON.stringify(row.schRef));
        const b=editor.board.board||{};
        const boardW=b.width||80, boardH=b.height||60;
        // Find a free staging Y slot to the right of the board
        const stageX=boardW+10;
        const usedY=new Set((editor.board.components||[]).filter(c=>c.x>boardW).map(c=>Math.round(c.y)));
        let sy=5;
        while(usedY.has(sy))sy+=12;
        restored.x=stageX; restored.y=sy;
        editor._snapshot();
        editor.board.components.push(restored);
        editor.selectedComp=restored; editor.selectedComps=[restored];
        // Pan to show the restored component
        editor.panX=editor.canvas.width/2-restored.x*editor.scale-editor.offsetX;
        editor.panY=editor.canvas.height/2-restored.y*editor.scale-editor.offsetY;
        editor.render();
        rebuildCompList();
        updateInfoPanel();
        if(typeof _notifyParentRefs==='function') _notifyParentRefs();
      };
    } else {
      const c=row.comp;
      d.onclick=()=>{
        if(_lastSelCiEl) _lastSelCiEl.classList.remove('sel');
        d.classList.add('sel'); _lastSelCiEl=d;
        editor.selectedComp=c;
        editor.panX=editor.canvas.width/2-c.x*editor.scale-editor.offsetX;
        editor.panY=editor.canvas.height/2-c.y*editor.scale-editor.offsetY;
        editor.render();
        updateInfoPanel();
      };
    }
    ul.appendChild(d);
  }
}

// ── Info Panel ───────────────────────────────────────────────
function updateToolPanel(tool){
  const title=document.getElementById('tool-params-title');
  const content=document.getElementById('tool-params-content');
  if(!title||!content) return;
  const labels={select:'Select',route:'Trace',via:'Via',area:'Area',zone:'Zone',measure:'Measure'};
  title.textContent=labels[tool]||tool;
  title.style.display='';
  const sec=document.getElementById('tool-params-section');
  if(sec) sec.style.display='';
  if(tool==='via'){
    content.innerHTML=`
      <div class="ir"><span class="il">Via ⌀</span>
        <input class="ii" value="${DR.viaSize.toFixed(2)}"
          onchange="DR.viaSize=Math.max(0.3,parseFloat(this.value)||1.0);document.getElementById('via-size-input').value=DR.viaSize;"> mm</div>
      <div class="ir"><span class="il">Hole ⌀</span>
        <input class="ii" value="${DR.viaDrill.toFixed(2)}"
          onchange="DR.viaDrill=Math.max(0.1,parseFloat(this.value)||0.6);document.getElementById('via-drill-input').value=DR.viaDrill;"> mm</div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:6px;">Click board to place</div>`;
  } else if(tool==='route'){
    const nets=[...new Set((editor.board?.nets||[]).map(n=>n.name).filter(Boolean))].sort();
    content.innerHTML=`
      <div class="ir"><span class="il">Width</span>
        <input class="ii" value="${(DR.traceWidth||0.25).toFixed(2)}"
          onchange="DR.traceWidth=Math.max(0.05,parseFloat(this.value)||0.25);document.getElementById('route-width').value=DR.traceWidth;"> mm</div>
      <div class="ir"><span class="il">Net</span>
        <input class="ii" style="width:75px;font-family:monospace;" placeholder="auto" list="tp-net-list"
          value="${editor.routeNet||''}"
          oninput="editor.routeNet=this.value.trim()||null;">
        <datalist id="tp-net-list">${nets.map(n=>`<option value="${n}">`).join('')}</datalist></div>
      <div class="ir"><span class="il">Layer</span><span class="iv">${editor.workLayer||'F.Cu'}</span></div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:6px;">Click pad to start</div>`;
  } else if(tool==='select'){
    title.style.display='none';
    content.innerHTML='';
    // Hide the entire section when in select mode
    const sec=document.getElementById('tool-params-section');
    if(sec) sec.style.display='none';
  } else {
    content.innerHTML=`<div style="font-size:11px;color:var(--text-muted);">${labels[tool]||tool} active</div>`;
  }
}

function updateInfoPanel(){
  // Keep tool-params-section in sync with selection
  updateToolPanel(editor?.tool||'select');
  const panel=document.getElementById('info-panel');
  const c=editor.selectedComp, tr=editor.selectedTrace, ar=editor.selectedArea, v=editor.selectedVia;
  const sp=editor.selectedPad;

  // Multi-selection (box-select or Ctrl+click, no single primary comp shown)
  const mc=editor.selectedComps||[];
  if(mc.length>1&&!c&&!tr&&!ar&&!v&&!sp){
    panel.innerHTML=`
      <div style="margin-bottom:10px;">
        <div style="font-family:monospace;font-size:14px;font-weight:700;color:var(--accent);">${mc.length} components</div>
        <div style="font-size:11px;color:var(--text-muted);">Multi-selection</div>
      </div>
      <div style="margin-top:8px;display:flex;gap:5px;flex-wrap:wrap;">
        <button class="btn" onclick="rotSel()" title="Rotate group 90° CW (R)">↻ Rotate</button>
        <button class="btn" style="color:var(--red)" onclick="editor.selectedComps.forEach(c=>{const i=(editor.board.components||[]).indexOf(c);if(i!==-1)editor.board.components.splice(i,1);});editor.selectedComps=[];editor.selectedComp=null;editor._snapshot();editor.render();rebuildCompList&&rebuildCompList();updateInfoPanel();">✕ Del all</button>
      </div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:7px;line-height:1.6;">R to rotate • Del to delete • Drag to move</div>`;
    return;
  }

  // Pad selected — show pin parameters
  if(sp&&!c&&!tr&&!ar&&!v){
    const{comp,pad}=sp;
    const{px,py}=editor._padWorld(comp,pad);
    panel.innerHTML=`
      <div style="margin-bottom:8px;">
        <div style="font-size:11px;color:var(--text-muted);">Pin — ${comp.ref||comp.id}</div>
        <div style="font-family:monospace;font-size:15px;font-weight:700;color:var(--accent);">${pad.name||pad.number||'?'}</div>
      </div>
      <div class="ir"><span class="il">#</span><span class="iv">${pad.number||'—'}</span></div>
      <div class="ir"><span class="il">Name</span>
        <input class="ii" style="width:80px" value="${esc(pad.name||'')}"
          onchange="editor.selectedPad.pad.name=this.value;editor.render()"></div>
      <div class="ir"><span class="il">Net</span>
        <input class="ii" style="width:80px" value="${esc(pad.net||'')}"
          onchange="editor.selectedPad.pad.net=this.value.trim();editor.render()"></div>
      <div class="ir"><span class="il">Type</span><span class="iv" style="font-size:10px;">${pad.type||'smd'}</span></div>
      <div class="ir"><span class="il">Shape</span><span class="iv" style="font-size:10px;">${pad.shape||'rect'}</span></div>
      <div class="ir"><span class="il">W</span><input class="ii" value="${(pad.size_x||1.6).toFixed(3)}"
        onchange="editor.selectedPad.pad.size_x=Math.max(0.05,parseFloat(this.value)||1.6);editor.render()"> mm</div>
      <div class="ir"><span class="il">H</span><input class="ii" value="${(pad.size_y||1.6).toFixed(3)}"
        onchange="editor.selectedPad.pad.size_y=Math.max(0.05,parseFloat(this.value)||1.6);editor.render()"> mm</div>
      <div class="ir"><span class="il">X</span><span class="iv">${px.toFixed(3)} mm</span></div>
      <div class="ir"><span class="il">Y</span><span class="iv">${py.toFixed(3)} mm</span></div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:7px;">Click again to cycle to component</div>`;
    return;
  }

  if(v&&!c&&!tr&&!ar){
    const vSize=v.size||1.0, vDrill=v.drill||0.6;
    const annular=(vSize-vDrill)/2;
    const minAnnular=DR.minAnnularRing||0.15;
    const annularOk=annular>=minAnnular;
    const annularCol=annularOk?'var(--green,#4ade80)':'var(--red,#f87171)';
    panel.innerHTML=`
      <div style="margin-bottom:10px;">
        <div style="font-family:monospace;font-size:14px;font-weight:700;color:#aabbcc;">Via</div>
        <div style="font-size:11px;color:var(--text-muted);">Through-hole • F.Cu → B.Cu</div>
      </div>
      <div class="ir"><span class="il">Net</span>
        <input class="ii" style="width:80px" value="${esc(v.net||'')}"
          onchange="editor._snapshot();editor.selectedVia.net=this.value.trim();editor.render()"></div>
      <div class="ir"><span class="il">Outer ⌀</span>
        <input class="ii" value="${vSize.toFixed(3)}"
          onchange="editor._snapshot();editor.selectedVia.size=Math.max(0.3,parseFloat(this.value)||1.0);editor.render();updateInfoPanel()"> mm</div>
      <div class="ir"><span class="il">Hole ⌀</span>
        <input class="ii" value="${vDrill.toFixed(3)}"
          onchange="editor._snapshot();editor.selectedVia.drill=Math.max(0.1,parseFloat(this.value)||0.6);editor.render();updateInfoPanel()"> mm</div>
      <div class="ir"><span class="il">Annular</span>
        <span class="iv" style="color:${annularCol};font-weight:600;">${annular.toFixed(3)} mm${annularOk?'':' ⚠'}</span></div>
      <div class="ir"><span class="il">X</span>
        <input class="ii" value="${(v.x||0).toFixed(3)}"
          onchange="editor._snapshot();editor.selectedVia.x=parseFloat(this.value)||0;editor.render()"> mm</div>
      <div class="ir"><span class="il">Y</span>
        <input class="ii" value="${(v.y||0).toFixed(3)}"
          onchange="editor._snapshot();editor.selectedVia.y=parseFloat(this.value)||0;editor.render()"> mm</div>
      <div style="margin-top:10px;display:flex;gap:5px;flex-wrap:wrap;">
        <button class="btn" onclick="DR.viaSize=editor.selectedVia.size;DR.viaDrill=editor.selectedVia.drill;document.getElementById('via-size-input').value=DR.viaSize;document.getElementById('via-drill-input').value=DR.viaDrill;" title="Copy these dimensions to the via placement defaults">⊙ Set default</button>
        <button class="btn" style="color:var(--red)" onclick="delVia()">✕ Del</button>
      </div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:7px;line-height:1.6;">Del to delete • Esc deselect</div>`;
    return;
  }
  if(ar&&!c&&!tr){
    const col=editor._netCol(ar.net);
    const w=Math.abs(ar.x2-ar.x1).toFixed(3),h=Math.abs(ar.y2-ar.y1).toFixed(3);
    panel.innerHTML=`
      <div style="margin-bottom:10px;">
        <div style="font-family:monospace;font-size:13px;font-weight:700;color:${col};">Copper Pour</div>
        <div style="font-size:11px;color:var(--text-dim);">Net: ${ar.net||'—'}</div>
      </div>
      <div class="ir"><span class="il">Net</span><input class="ii" style="width:90px;" value="${ar.net||''}" onchange="editor.selectedArea.net=this.value.trim();editor.render();renderAreasPanel();"> </div>
      <div class="ir"><span class="il">Layer</span><span class="iv">${ar.layer||'F.Cu'}</span></div>
      <div class="ir"><span class="il">Clearance</span>
        <input class="ii" value="${(ar.clearance!=null?ar.clearance:DR.clearance||0.2).toFixed(3)}"
          onchange="editor._snapshot();editor.selectedArea.clearance=Math.max(0,parseFloat(this.value)||0.2);editor.render();updateInfoPanel()"> mm</div>
      <div class="ir"><span class="il">Width</span><span class="iv">${w} mm</span></div>
      <div class="ir"><span class="il">Height</span><span class="iv">${h} mm</span></div>
      <div style="margin-top:10px;display:flex;gap:5px;">
        <button class="btn" onclick="editor.render()">⟳ Refresh</button>
        <button class="btn" style="color:var(--red)" onclick="delArea()">✕ Del</button>
      </div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:7px;">Del to delete area</div>`;
    return;
  }
  const dw=editor.selectedDrawing;
  if(dw&&!c&&!tr&&!ar&&!v){
    const layerOpts=['Edge.Cuts','F.SilkS','B.SilkS','F.Cu','B.Cu','F.Fab','B.Fab'].map(l=>`<option${dw.layer===l?' selected':''}>${l}</option>`).join('');
    panel.innerHTML=`
      <div style="margin-bottom:8px;">
        <div style="font-family:monospace;font-size:14px;font-weight:700;color:#88ccaa;">Drawing</div>
      </div>
      <div class="ir"><span class="il">Layer</span>
        <select style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);font-size:11px;padding:2px 4px;"
          onchange="editor.selectedDrawing.layer=this.value;editor.render();updateInfoPanel()">
          ${layerOpts}</select></div>
      <div class="ir"><span class="il">Width</span>
        <input class="ii" value="${(dw.width||0.1).toFixed(3)}"
          onchange="editor.selectedDrawing.width=Math.max(0.01,parseFloat(this.value)||0.1);editor.render()"> mm</div>
      <div class="ir"><span class="il">Closed</span>
        <input type="checkbox"${dw.closed?' checked':''} onchange="editor.selectedDrawing.closed=this.checked;editor.render()"></div>
      <div class="ir"><span class="il">Points</span><span class="iv">${(dw.points||[]).length}</span></div>
      <div style="margin-top:10px;">
        <button class="btn" style="color:var(--red)" onclick="delDrawing()">✕ Del</button>
      </div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:7px;">Drag to move • Del to delete</div>`;
    return;
  }
  if(!c && !tr){panel.innerHTML='<div class="ip-empty">Click a component<br>or trace to inspect</div>';return;}
  if(tr && !c){
    const lyr=tr.layer||'F.Cu';
    const segLen=(tr.segments||[]).reduce((s,seg)=>s+Math.hypot(seg.end.x-seg.start.x,seg.end.y-seg.start.y),0);
    // ── Coplanar Waveguide impedance estimate ──
    const _cpwZ0=(()=>{
      const w=tr.widths?.length?tr.widths[0]:(tr.width||0.25); // trace width mm
      const g=typeof DR!=='undefined'?DR.clearance:0.2;        // gap to ground mm
      const h=typeof DR!=='undefined'?DR.boardThickness:1.6;   // substrate mm
      const er=4.5; // FR-4 dielectric constant
      const t=(typeof DR!=='undefined'&&DR.copperWeight||1.0)*0.035; // copper thickness mm
      // Effective width (compensate for copper thickness)
      const we=w+1.25*t/Math.PI*(1+Math.log(4*Math.PI*w/t));
      // k parameter for CPW
      const k=we/(we+2*g);
      const kp=Math.sqrt(1-k*k);
      // K(k)/K(k') ratio via Hilberg approximation
      const _Kratio=k=>{
        if(k<1e-10)return 0;
        if(k>1-1e-10)return Infinity;
        const kp=Math.sqrt(1-k*k);
        if(k<=1/Math.SQRT2){
          return Math.PI/Math.log(2*(1+Math.sqrt(kp))/(1-Math.sqrt(kp)));
        }else{
          return Math.log(2*(1+Math.sqrt(k))/(1-Math.sqrt(k)))/Math.PI;
        }
      };
      // Ground-backed CPW: also account for substrate height
      const k1=Math.tanh(Math.PI*we/(4*h))/Math.tanh(Math.PI*(we+2*g)/(4*h));
      const k1p=Math.sqrt(1-k1*k1);
      const Kk=_Kratio(k);
      const Kk1=_Kratio(k1);
      // Effective dielectric
      const q=Kk>0?(_Kratio(kp)*Kk1)/(_Kratio(k1p)*Kk):0;
      const eeff=1+(er-1)/2*q;
      // Z0
      const denom=Kk+Kk1;
      if(denom<1e-10)return null;
      const Z0=60*Math.PI/(Math.sqrt(eeff)*denom);
      return{Z0,eeff,w,g,h,er,t};
    })();
    const cpwHtml=_cpwZ0?`
      <div style="margin-top:8px;padding:6px 8px;background:rgba(99,102,241,0.08);border:1px solid rgba(99,102,241,0.25);border-radius:5px;">
        <div style="font-size:9px;font-weight:700;color:#818cf8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">CPW Impedance (est.)</div>
        <div class="ir"><span class="il">Z₀</span><span class="iv" style="color:#a5b4fc;font-weight:700;">${_cpwZ0.Z0.toFixed(1)} Ω</span></div>
        <div class="ir"><span class="il">εeff</span><span class="iv">${_cpwZ0.eeff.toFixed(2)}</span></div>
        <div class="ir"><span class="il">Gap</span><span class="iv">${_cpwZ0.g.toFixed(3)} mm</span></div>
        <div class="ir"><span class="il">h</span><span class="iv">${_cpwZ0.h.toFixed(1)} mm</span></div>
        <div class="ir"><span class="il">εr</span><span class="iv">${_cpwZ0.er}</span></div>
        <div class="ir"><span class="il">Cu</span><span class="iv">${(_cpwZ0.t*1000).toFixed(0)} µm</span></div>
      </div>`:'';
    panel.innerHTML=`
      <div style="margin-bottom:10px;">
        <div style="font-family:monospace;font-size:14px;font-weight:700;color:var(--accent);">Trace</div>
      </div>
      <div class="ir"><span class="il">Net</span>
        <input class="ii" style="width:80px" value="${esc(tr.net||'')}"
          onchange="editor.selectedTrace.net=this.value.trim();editor.render()"></div>
      <div class="ir"><span class="il">Layer</span>
        <select style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);font-size:11px;padding:2px 4px;"
          onchange="editor.selectedTrace.layer=this.value;editor.render()">
          <option${lyr==='F.Cu'?' selected':''}>F.Cu</option>
          <option${lyr==='B.Cu'?' selected':''}>B.Cu</option>
        </select></div>
      <div class="ir"><span class="il">Width</span>
        ${tr.widths?.length?
          `<span class="iv" style="font-size:10px;">${tr.widths[0].toFixed(3)} → ${tr.widths[tr.widths.length-1].toFixed(3)} mm (taper)</span>`:
          `<input class="ii" value="${(tr.width||0.25).toFixed(3)}"
            onchange="editor.selectedTrace.width=Math.max(0.05,parseFloat(this.value)||0.25);delete editor.selectedTrace.widths;editor.render();updateInfoPanel()" > mm`
        }</div>
      <div class="ir"><span class="il">Length</span><span class="iv">${segLen.toFixed(3)} mm</span></div>
      <div class="ir"><span class="il">Segs</span><span class="iv">${(tr.segments||[]).length}</span></div>
      ${cpwHtml}
      <div style="margin-top:10px;display:flex;gap:5px;flex-wrap:wrap;">
        <button class="btn" onclick="fitToPad()" title="Set trace width to match the pad it connects to">⊢ Fit to pad</button>
        <button class="btn" style="color:var(--red)" onclick="delTrace()">✕ Del</button>
      </div>
      <div style="font-size:10px;color:var(--text-muted);margin-top:7px;line-height:1.6;">Del to delete • Esc deselect</div>`;
    return;
  }
  const nets=[...new Set((c.pads||[]).map(p=>p.net).filter(Boolean))];
  const nc=n=>n==='GND'||/AGND|DGND/.test(n)?'net-gnd':/^(VCC|VDD|VIN|5V|3V3|12V)/.test(n)?'net-pwr':'net-sig';
  panel.innerHTML=`
    <div style="margin-bottom:10px;">
      <div style="font-family:monospace;font-size:15px;font-weight:700;color:var(--accent);">${c.ref||c.id}</div>
      <div style="font-size:12px;color:var(--text-dim);">${c.value||'—'}</div>
      <div style="font-size:11px;color:var(--text-muted);">${c.footprint||''}</div>
    </div>
    <div class="ir"><span class="il">X</span><input class="ii" id="info-x" value="${(c.x||0).toFixed(2)}" onchange="editor.selectedComp.x=parseFloat(this.value)||0;editor.render()"> mm</div>
    <div class="ir"><span class="il">Y</span><input class="ii" id="info-y" value="${(c.y||0).toFixed(2)}" onchange="editor.selectedComp.y=parseFloat(this.value)||0;editor.render()"> mm</div>
    <div class="ir"><span class="il">Rot</span><input class="ii" id="info-rot" value="${c.rotation||0}" onchange="editor.selectedComp.rotation=parseFloat(this.value)||0;editor.render()"> °</div>
    <div class="ir"><span class="il">Layer</span>
      <select class="ii" style="width:80px;" onchange="editor.selectedComp.layer=this.value;editor.render()">
        <option value="F" ${(c.layer||'F')==='F'?'selected':''}>Top (F)</option>
        <option value="B" ${c.layer==='B'?'selected':''}>Bottom (B)</option>
      </select></div>
    <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin-bottom:3px;margin-top:6px;">Pads / Nets</div>
    ${(c.pads||[]).map((p,pi)=>`<div class="pad-row" style="display:flex;align-items:center;gap:3px;padding:2px 0;border-bottom:1px solid rgba(46,50,80,0.35);">
      <span class="pr-num" style="min-width:18px;">${p.number}</span>
      <span class="pr-name" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;">${esc(p.name||'')}</span>
      <input value="${esc(p.net||'')}" placeholder="net" title="Pad net"
        style="width:72px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:${p.net?'#6c63ff':'var(--text-muted)'};font-size:9px;font-family:monospace;padding:1px 4px;"
        onchange="editor.selectedComp.pads[${pi}].net=this.value.trim();this.style.color=this.value?'#6c63ff':'var(--text-muted)';editor.render();">
    </div>`).join('')}
    <div style="margin-top:10px;display:flex;gap:5px;flex-wrap:wrap;">
      <button class="btn" onclick="rotSel()">↻</button>
      <button class="btn" onclick="flipSel()">⇅ Flip</button>
      <button class="btn" style="color:var(--red)" onclick="delSel()">✕ Del</button>
    </div>
    <div style="font-size:10px;color:var(--text-muted);margin-top:7px;line-height:1.6;">Drag to move • G rotate<br>Del to delete</div>`;
}

function rotSel(){editor.rotateSelGroup(90);}
function flipSel(){if(editor.selectedComp){editor.selectedComp.layer=editor.selectedComp.layer==='B'?'F':'B';updateInfoPanel();editor.render();}}
function _notifyParentRefs(){
  const src=window.parent!==window?window.parent:null;
  if(!src||!editor?.board)return;
  const refs=(editor.board.components||[]).map(c=>c.ref);
  src.postMessage({type:'boardRefsChanged',refs},'*');
}
function delSel(){
  if(!editor.selectedComp||!editor.board)return;
  const i=editor.board.components.indexOf(editor.selectedComp);
  if(i!==-1)editor.board.components.splice(i,1);
  editor.selectedComp=null;editor.render();rebuildCompList();updateInfoPanel();
  _notifyParentRefs();
}
function delTrace(){
  if(!editor.selectedTrace||!editor.board)return;
  const i=(editor.board.traces||[]).indexOf(editor.selectedTrace);
  if(i!==-1)editor.board.traces.splice(i,1);
  editor.selectedTrace=null;editor.render();updateInfoPanel();
}
function fitToPad(){
  const tr=editor.selectedTrace;
  if(!tr||!editor.board)return;
  const segs=tr.segments||[];
  if(segs.length===0)return;
  const p0=segs[0].start, pN=segs[segs.length-1].end;

  // Find closest same-net pad to a given endpoint
  function findPad(ep){
    let best=null,bestD=Infinity;
    for(const comp of(editor.board.components||[])){
      for(const pad of(comp.pads||[])){
        if((pad.net||'')!==(tr.net||''))continue;
        const{px,py}=editor._padWorld(comp,pad);
        const d=Math.hypot(px-ep.x,py-ep.y);
        if(d<bestD){bestD=d;best=pad;}
      }
    }
    return best;
  }
  // Perpendicular pad width given the approach segment direction
  function padW(pad,seg){
    const dx=Math.abs(seg.end.x-seg.start.x),dy=Math.abs(seg.end.y-seg.start.y);
    return dx>=dy?(pad.size_y||pad.size_x||0.25):(pad.size_x||pad.size_y||0.25);
  }

  const padStart=findPad(p0), padEnd=findPad(pN);
  if(!padStart&&!padEnd)return;
  const w1=padStart?padW(padStart,segs[0]):(tr.width||0.25);
  const w2=padEnd?padW(padEnd,segs[segs.length-1]):(tr.width||0.25);

  // If widths are the same just set it directly
  if(Math.abs(w1-w2)<0.001){
    editor._snapshot();
    tr.width=Math.max(DR.minTraceWidth||0.15,w1);
    editor.render();updateInfoPanel();return;
  }

  // Build cumulative-length table along the polyline
  const cumLen=[0];
  for(const seg of segs)cumLen.push(cumLen[cumLen.length-1]+Math.hypot(seg.end.x-seg.start.x,seg.end.y-seg.start.y));
  const totalLen=cumLen[cumLen.length-1];
  if(totalLen<0.001)return;

  // Interpolate a world point at normalised position t∈[0,1]
  function interpPt(t){
    const tgt=t*totalLen;
    for(let i=0;i<segs.length;i++){
      if(tgt<=cumLen[i+1]){
        const f=(tgt-cumLen[i])/(cumLen[i+1]-cumLen[i]);
        const s=segs[i];
        return{x:s.start.x+f*(s.end.x-s.start.x),y:s.start.y+f*(s.end.y-s.start.y)};
      }
    }
    return{...segs[segs.length-1].end};
  }

  // Klopfenstein taper profile (Bessel I0 integral, A=2.0)
  // Φ(u,A) = ∫₀ᵘ I₀(A√(1−s²))ds, normalised by Φ(1,A)=sinh(A)/A
  function _I0(x){
    const ax=Math.abs(x);
    if(ax<3.75){const t=x/3.75,t2=t*t;return 1+t2*(3.5156229+t2*(3.0899424+t2*(1.2067492+t2*(0.2659732+t2*(0.0360768+t2*0.0045813)))));}
    const t=3.75/ax;return(Math.exp(ax)/Math.sqrt(ax))*(0.39894228+t*(0.01328592+t*(0.00225319+t*(-0.00157565+t*(0.00916281+t*(-0.02057706+t*(0.02635537+t*(-0.01647633+t*0.00392377))))))));
  }
  function _klop(t,w1,w2,A){
    const u=2*t-1,M=80,du=u/M;
    let phi=0;
    for(let i=0;i<=M;i++){const s=i*du;phi+=(i===0||i===M?1:i%2===0?2:4)*_I0(A*Math.sqrt(Math.max(0,1-s*s)));}
    phi*=du/3;
    const phi1=A>1e-6?Math.sinh(A)/A:1;
    const pn=phi1>1e-10?phi/phi1:u;
    return Math.exp(0.5*(Math.log(w1)+Math.log(w2))+0.5*(Math.log(w2)-Math.log(w1))*pn);
  }
  const A=2.0;
  function taperW(t){return Math.max(DR.minTraceWidth||0.15,_klop(t,w1,w2,A));}

  // Resample into N equal-length segments stored on a single trace with a widths array
  const N=20;
  const pts=[];
  for(let i=0;i<=N;i++)pts.push(interpPt(i/N));

  const newSegs=[], newWidths=[];
  for(let i=0;i<N;i++){
    newSegs.push({start:{...pts[i]},end:{...pts[i+1]}});
    newWidths.push(parseFloat(taperW((i+0.5)/N).toFixed(4)));
  }

  editor._snapshot();
  tr.segments=newSegs;
  tr.widths=newWidths;
  tr.width=newWidths[0]; // keep width field valid for DRC/clearance fallback
  editor.render();updateInfoPanel();
}
function delVia(){
  if(!editor.selectedVia||!editor.board)return;
  const i=(editor.board.vias||[]).indexOf(editor.selectedVia);
  if(i!==-1)editor.board.vias.splice(i,1);
  editor.selectedVia=null;editor.render();updateInfoPanel();
}
function delArea(){
  if(!editor.selectedArea||!editor.board)return;
  const i=(editor.board.areas||[]).indexOf(editor.selectedArea);
  if(i!==-1)editor.board.areas.splice(i,1);
  editor.selectedArea=null;editor.render();renderAreasPanel();updateInfoPanel();
}
function delDrawing(){
  if(!editor.selectedDrawing||!editor.board)return;
  const i=(editor.board.drawings||[]).indexOf(editor.selectedDrawing);
  if(i!==-1)(editor.board.drawings||[]).splice(i,1);
  editor.selectedDrawing=null;editor._snapshot();editor.render();updateInfoPanel();
}

function updateBoardInfo(){
  if(!editor.board){document.getElementById('board-info').textContent='No board';return;}
  const b=editor.board;
  const colorId=b.projectId||b.id;
  const color=_tabColor(colorId);
  document.getElementById('board-info').innerHTML=
    `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
      <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};flex-shrink:0;" title="Project color (matches schematic tab)"></span>
      <b style="color:var(--text)">${b.title||'Untitled'}</b>
    </div>`+
    `${b.board.width}×${b.board.height}mm<br>`+
    `${b.components?.length||0} comps · ${b.nets?.length||0} nets<br>`+
    `${b.traces?.length||0} traces · ${b.vias?.length||0} vias · ${b.areas?.length||0} areas`;
}

