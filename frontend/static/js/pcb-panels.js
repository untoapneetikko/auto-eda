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
    d.onclick=()=>{if(nm.contentEditable==='true')return;editor.workLayer=name;buildLayerPanel();updateWorkLayerBadge();editor.render();};
    ul.appendChild(d);
  }
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
      const live=activeByRef.get(sr.ref);
      rows.push({comp:live||null,ref:sr.ref,value:sr.value,footprint:sr.footprint,missing:!live});
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
    if(row.comp){
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
    title.textContent='Select';
    content.innerHTML='<div style="font-size:11px;color:var(--text-muted);">Click an object to inspect</div>';
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
        <input class="ii" value="${(tr.width||0.25).toFixed(3)}"
          onchange="editor.selectedTrace.width=Math.max(0.05,parseFloat(this.value)||0.25);editor.render()"> mm</div>
      <div class="ir"><span class="il">Length</span><span class="iv">${segLen.toFixed(3)} mm</span></div>
      <div class="ir"><span class="il">Segs</span><span class="iv">${(tr.segments||[]).length}</span></div>
      <div style="margin-top:10px;display:flex;gap:5px;">
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

function rotSel(){if(editor.selectedComp){editor.selectedComp.rotation=((editor.selectedComp.rotation||0)+90)%360;updateInfoPanel();editor.render();}}
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

