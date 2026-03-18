function setTool(t){
  editor.tool=t;editor.routePoints=[];editor.routeNet=null;
  editor.zonePoints=[];editor.measureStart=null;
  editor.areaStart=null;editor._isAreaDrag=false;
  editor.drawPoints=[];
  editor._hoverComp=null;editor._hoverTrace=null;
  document.querySelectorAll('.tbtn[id^="tool-"]').forEach(b=>b.classList.remove('active'));
  const btn=document.getElementById('tool-'+t);if(btn)btn.classList.add('active');
  const cursors={select:'default',route:'crosshair',via:'cell',zone:'crosshair',measure:'crosshair',area:'crosshair',draw:'crosshair',text:'text'};
  document.getElementById('canvas-wrap').style.cursor=cursors[t]||'default';
  const areaOpts=document.getElementById('area-tool-opts');
  if(areaOpts)areaOpts.style.display=t==='area'?'flex':'none';
  const drawOpts=document.getElementById('draw-tool-opts');
  if(drawOpts){
    drawOpts.style.display=t==='draw'?'flex':'none';
    if(t==='draw'){
      // Sync layer selector to current work layer if it's in the list
      const lyrSel=document.getElementById('draw-layer-sel');
      if(lyrSel&&editor.workLayer){
        const opt=[...lyrSel.options].find(o=>o.value===editor.workLayer);
        if(opt)lyrSel.value=editor.workLayer;
      }
    }
  }
  const textOpts=document.getElementById('text-tool-opts');
  if(textOpts){
    textOpts.style.display=t==='text'?'flex':'none';
    if(t==='text'){
      const inp=document.getElementById('text-content-input');
      if(inp)setTimeout(()=>inp.focus(),50);
    }
  }
  updateToolPanel(t);
  updateInfoPanel();
  const routeOpts=document.getElementById('route-tool-opts');
  if(routeOpts){
    routeOpts.style.display=t==='route'?'flex':'none';
    if(t==='route'){
      // Populate net datalist from board nets
      const dl=document.getElementById('route-net-list');
      if(dl&&editor.board){
        const nets=[...new Set((editor.board.nets||[]).map(n=>n.name).filter(Boolean))].sort();
        dl.innerHTML=nets.map(n=>`<option value="${n}">`).join('');
      }
      // Reset net input (auto-detect from pad by default)
      const ni=document.getElementById('route-net-input');
      if(ni)ni.value='';
      if(editor)editor.routeNet=null;
    }
  }
  editor.render();
}

function fitBoard(){editor.fitBoard();}
function zoomIn(){editor.scale=Math.min(400,editor.scale*1.3);editor.render();}
function zoomOut(){editor.scale=Math.max(1,editor.scale*0.77);editor.render();}
function toggleRatsnest(){
  const lyr=editor.layers['Ratsnest'];
  lyr.visible=!lyr.visible;
  const btn=document.getElementById('btn-ratsnest');
  if(btn)btn.classList.toggle('active',lyr.visible);
  editor.render();
}
function loadFile(){document.getElementById('file-input').click();}
function onFileChosen(e){
  const f=e.target.files[0];if(!f)return;
  const r=new FileReader();
  r.onload=ev=>{
    const res=editor.load(ev.target.result);
    if(res.ok){afterLoad();}else{alert('Load error: '+res.error);}
  };
  r.readAsText(f);e.target.value='';
}
function loadExample(){
  const res=editor.load(EXAMPLE_PCB);
  if(res.ok)afterLoad();
}
// ── Save Board to server ──────────────────────────────────────
async function saveBoard(){
  if(!editor.board){alert('No board loaded');return;}
  const board=editor.board;
  if(!board.id)board.id=Date.now().toString(36)+Math.random().toString(36).slice(2,7);
  // Embed current stackup info so the board is self-describing
  board.layerCount=DR.layerCount||2;
  board.stackup=DR.stackup||null;
  // Keep _currentProjectId in sync with the board being saved
  if(board.projectId) _currentProjectId=board.projectId;
  try{
    const r=await fetch('/api/pcb-boards',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(board)});
    const d=await r.json();
    board.id=d.id;
    _activeBoardId=board.id;
    const btn=document.querySelector('.tbtn[onclick="saveBoard()"]');
    if(btn){btn.textContent='✓ Saved';setTimeout(()=>btn.textContent='💾 Save',1500);}
    await loadBoardTabs(_currentProjectId);
  }catch(e){alert('Save failed: '+e.message);}
}

// ── Delete current board ──────────────────────────────────────
async function deleteCurrentBoard(){
  if(!editor.board){alert('No board loaded.');return;}
  const title=editor.board.title||'Untitled';
  const id=editor.board.id;
  if(!confirm(`Delete "${title}"?\n\nThis cannot be undone.`))return;
  if(id){
    try{await fetch(`/api/pcb-boards/${id}`,{method:'DELETE'});}catch(_){}
  }
  // Clear the editor
  editor.board=null;
  editor._history=[];editor._historyIdx=-1;
  try{localStorage.removeItem('pcb_last_board');}catch(_){}
  editor.selectedComp=null;editor.selectedTrace=null;editor.selectedArea=null;editor.selectedVia=null;
  editor.render();
  _activeBoardId=null;
  document.getElementById('board-info').textContent='No board';
  document.getElementById('comp-list').innerHTML='<div style="padding:8px;color:var(--text-muted);font-size:11px;">No board loaded</div>';
  updateInfoPanel();renderAreasPanel();
  await loadBoardTabs(_currentProjectId);
}

// ── Open Boards Modal ─────────────────────────────────────────
async function openBoardsModal(){
  openModal('boards-modal');
  const st=document.getElementById('boards-status');
  const list=document.getElementById('boards-list');
  st.textContent='Loading…';list.innerHTML='';
  try{
    const r=await fetch('/api/pcb-boards');
    const boards=await r.json();
    if(!boards.length){list.innerHTML='<div style="color:var(--text-muted);font-size:12px;">No saved boards yet. Import a schematic and click 💾 Save.</div>';st.textContent='';return;}
    boards.forEach(b=>{
      const d=document.createElement('div');
      d.style.cssText='display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:5px;border:1px solid var(--border);margin-bottom:5px;cursor:pointer;transition:all .15s;';
      d.innerHTML=`<div style="flex:1"><div style="font-weight:700;font-size:12px;">${b.title||b.id}</div><div style="font-size:11px;color:var(--text-muted);">${(b.component_count||0)} components · ${new Date(b.updated_at||0).toLocaleString()}</div></div><button class="btn" style="flex-shrink:0">Delete</button>`;
      d.querySelector('.btn').onclick=async e=>{e.stopPropagation();if(!confirm('Delete this board?'))return;await fetch(`/api/pcb-boards/${b.id}`,{method:'DELETE'});d.remove();};
      d.onclick=async()=>{
        try{
          const rr=await fetch(`/api/pcb-boards/${b.id}`);
          const board=await rr.json();
          const res=editor.load(board);
          if(res.ok){_activeBoardId=b.id;afterLoad();closeModal('boards-modal');}
          else alert('Load error: '+res.error);
        }catch(e){alert('Load failed: '+e.message);}
      };
      list.appendChild(d);
    });
    st.textContent=`${boards.length} board(s) saved`;
  }catch(e){st.textContent='Error: '+e.message;}
}

// Animate a converted board onto the canvas: LE comps snap in together,
// free comps appear one by one so the auto-placement is visually obvious.
async function animateBoardLoad(board){
  const leComps=(board.components||[]).filter(c=>c._fromLE);
  const freeComps=(board.components||[]).filter(c=>!c._fromLE);
  // Per-component delay: slower for small boards so it's clearly visible
  const n=freeComps.length;
  const delay=n<=6?150:n<=20?60:16;

  // Step 1 — load board shell with LE comps (pre-positioned, snap in instantly)
  const shell={...board,components:[...leComps],traces:[],vias:[],areas:[]};
  const r=editor.load(shell);
  if(!r.ok)return editor.load(board); // fallback: load full board normally
  editor.fitBoard();
  editor.render();
  await new Promise(res=>requestAnimationFrame(res));

  // Step 2 — free comps appear one by one
  const sb=document.getElementById('status-bar');
  for(let i=0;i<freeComps.length;i++){
    editor.board.components.push(freeComps[i]);
    if(sb)sb.textContent=`Placing ${freeComps[i].ref||freeComps[i].id} (${i+1}/${freeComps.length})`;
    editor.render();
    await new Promise(res=>setTimeout(res,delay));
  }

  // Step 3 — load full board with traces, vias, nets
  const finalRes=editor.load(board);
  if(finalRes.ok)editor.render();
}

function afterLoad(){
  rebuildCompList();updateBoardInfo();buildLayerPanel();updateWorkLayerBadge();
  editor.selectedComp=null;editor.selectedTrace=null;editor.selectedArea=null;
  editor._history=[];editor._historyIdx=-1;editor._snapshot(); // fresh history on load
  updateInfoPanel();renderAreasPanel();
  // Sync active board tab to the loaded board
  if(editor.board?.id) _activeBoardId=editor.board.id;
  renderBoardTabs();
  // Re-evaluate stale banner for the newly loaded board
  if(_staleProjId!=null) _updateStaleBanner(_staleProjId,true);
  // Persist immediately so refresh restores this board
  try{localStorage.setItem('pcb_last_board',JSON.stringify(editor.board));}catch(_){}
}

// ── Schematic staleness tracking ─────────────────────────────────────────────
// Tracks which projectId the schematic told us is dirty
let _staleProjId=null;

function _updateStaleBanner(projectId,isDirty){
  _staleProjId=isDirty?projectId:null;
  const banner=document.getElementById('pcb-stale-banner');
  if(!banner)return;
  const boardProjId=editor?.board?.projectId||null;
  const show=isDirty&&projectId&&boardProjId&&projectId===boardProjId;
  banner.style.display=show?'flex':'none';
}

// Banner "Update PCB" button — asks the parent page to re-import
function _pcbRequestUpdate(){
  const src=window.parent!==window?window.parent:null;
  if(src)src.postMessage({type:'updatePCBRequest'},'*');
  // Also hide banner optimistically
  const banner=document.getElementById('pcb-stale-banner');
  if(banner)banner.style.display='none';
}

function renderAreasPanel(){
  const panel=document.getElementById('areas-panel');
  if(!panel)return;
  const areas=(editor.board?.areas||[]);
  const zones=(editor.board?.zones||[]);
  const all=[...areas.map(a=>({type:'area',obj:a})),...zones.map(z=>({type:'zone',obj:z}))];
  if(!all.length){
    panel.innerHTML='<div style="color:var(--text-muted);font-size:11px;padding:4px 2px;">No copper areas.<br>Use ▦ Area tool to draw.</div>';
    return;
  }
  panel.innerHTML='';
  for(const {type,obj:a} of all){
    const isArea=type==='area';
    const net=a.net||'?';
    const layer=a.layer||'F.Cu';
    const col=editor._netCol(net);
    const sel=editor.selectedArea===a;
    const hidden=a._hidden;
    let dim='';
    if(isArea){
      dim=`${Math.abs(a.x2-a.x1).toFixed(1)}×${Math.abs(a.y2-a.y1).toFixed(1)}mm`;
    } else {
      dim=`${(a.points||[]).length}pts`;
    }
    const d=document.createElement('div');
    d.className='area-item'+(sel?' sel':'')+(hidden?' hidden-area':'');
    d.innerHTML=
      `<span class="area-btn" title="${hidden?'Show':'Hide'}" data-action="eye">${hidden?'○':'👁'}</span>`+
      `<span class="area-net" style="color:${col}" title="${net}">${net}</span>`+
      `<span class="area-lyr">${layer}</span>`+
      `<span class="area-dim">${dim}</span>`+
      `<span style="flex:1;"></span>`+
      `<span class="area-btn" data-action="delete" title="Delete" style="color:#ef4444;">✕</span>`;
    d.onclick=(e)=>{
      const act=e.target.closest('[data-action]')?.dataset.action;
      if(act==='eye'){
        e.stopPropagation();
        a._hidden=!a._hidden;
        editor.render();renderAreasPanel();
        return;
      }
      if(act==='delete'){
        e.stopPropagation();
        if(isArea){
          const idx=(editor.board.areas||[]).indexOf(a);
          if(idx!==-1)editor.board.areas.splice(idx,1);
        } else {
          const idx=(editor.board.zones||[]).indexOf(a);
          if(idx!==-1)editor.board.zones.splice(idx,1);
        }
        if(editor.selectedArea===a)editor.selectedArea=null;
        editor._snapshot();editor.render();renderAreasPanel();updateInfoPanel();
        return;
      }
      editor.selectedArea=a;editor.selectedComp=null;editor.selectedTrace=null;
      editor.render();updateInfoPanel();renderAreasPanel();
    };
    panel.appendChild(d);
  }
}
function exportJSON(){
  if(!editor.board){alert('No board loaded');return;}
  dl(editor.exportJSON(),(editor.board.title||'pcb')+'.json','application/json');
}
function dl(content,filename,mime){
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([content],{type:mime}));
  a.download=filename;a.click();
}

