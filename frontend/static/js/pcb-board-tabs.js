// ── Section switching (matches index.html pattern) ──────────────
function switchPcbSection(name){
  ['layout','auto','boards','nets','drc','export','3d'].forEach(s=>{
    const sec=document.getElementById('pcb-section-'+s);
    const nav=document.getElementById('pcb-nav-'+s);
    if(sec)sec.classList.toggle('hidden',s!==name);
    if(nav)nav.classList.toggle('active',s===name);
  });
  if(name==='boards')renderBoardsSection();
  if(name==='nets')renderNetsSection();
  if(name==='layout'&&editor)editor.render();
  if(name==='auto')_populateAutoTab();
  if(name==='drc'){populateDRTab();runDRCTab();}
  // Pause 3D render loop when leaving the 3D tab, resume when entering
  if(typeof viewer3d!=='undefined'&&viewer3d){
    if(name==='3d') viewer3d.resume(); else viewer3d.pause();
  }
}

// ── Populate Auto tab inputs from DR object ──
function _populateAutoTab(){
  const s=(id,v)=>{const el=document.getElementById(id);if(el)el.value=v;};
  s('auto-clearance', DR.packageGap??1.0);
  s('auto-corner', DR.cornerAngle??90);
  s('auto-trace-w', DR.traceWidth??0.25);
  s('auto-cu-clear', DR.clearance??0.2);
  s('auto-allow-vias', DR.allowVias!==false?'true':'false');
  s('auto-via-size', DR.viaSize??1.0);
  s('auto-via-drill', DR.viaDrill??0.6);
  s('auto-pwr-trace', DR.powerTraceWidth??0.4);
  s('auto-skip-nc', 'false');
}

// ── PCB Board Tabs (tied to saved boards, named after schematic projects) ──
let _pcbBoardsList = [];   // [{id, title, updated_at, component_count}]
let _activeBoardId = null; // currently loaded board id
let _currentProjectId = null; // project (schematic) that owns the current board
let _currentNetlist   = {};   // latest netlist received from parent (importProject)
let _importingProjectId = null; // guard: prevents concurrent importProject handlers creating duplicate boards

async function loadBoardTabs(projectId){
  // Only show boards that belong to the current schematic (parent).
  // A layout cannot exist without its parent schematic, so we never show
  // boards from other projects.
  const pid = projectId ?? _currentProjectId;
  try{
    const url = pid ? `/api/pcb-boards?projectId=${encodeURIComponent(pid)}` : '/api/pcb-boards';
    const r=await fetch(url);
    _pcbBoardsList=await r.json();
    _pcbBoardsList.sort((a,b)=>new Date(b.updated_at||0)-new Date(a.updated_at||0));
  }catch(_){ _pcbBoardsList=[]; }
  renderBoardTabs();
}

function _escT(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

// Deterministic project color — same algorithm as index.html so colors match across both editors
function _tabColor(id){
  if(!id)return'#6c63ff';
  let h=0;
  for(let i=0;i<id.length;i++)h=(h*31+id.charCodeAt(i))&0x7fffffff;
  return`hsl(${(h*137.508)%360|0},65%,58%)`;
}

function renderBoardTabs(){
  const bar=document.getElementById('pcb-board-tabs');
  if(!bar)return;
  if(!_pcbBoardsList.length){
    bar.innerHTML=`<span style="font-size:11px;color:var(--text-muted);padding:0 12px;display:flex;align-items:center;height:28px;">No saved boards — import a schematic and save</span>`
      +`<button class="pcb-board-tab-new" onclick="switchPcbSection('boards')" title="Open boards list">+</button>`;
    return;
  }

  // Detect title collisions so duplicates get a disambiguation suffix
  const titleCounts={};
  _pcbBoardsList.forEach(b=>{const n=b.title||'Untitled';titleCounts[n]=(titleCounts[n]||0)+1;});

  bar.innerHTML=_pcbBoardsList.map(b=>{
    const isActive=b.id===_activeBoardId;
    const rawTitle=b.title||'Untitled';
    const colorId=b.projectId||b.id; // prefer projectId so color matches the schematic tab
    const color=_tabColor(colorId);

    const suffix=titleCounts[rawTitle]>1
      ?` <span style="font-size:9px;color:var(--text-muted);font-weight:400;">·${colorId.slice(-4)}</span>`
      :'';
    const nm=_escT(rawTitle)+suffix;
    const tooltip=titleCounts[rawTitle]>1?`${rawTitle}  (ID: …${colorId.slice(-6)})`:rawTitle;

    // Hide ✕ when scoped to a project — boards stay open as long as schematic is open
    const closeBtn = _currentProjectId
      ? ''
      : `<span class="pcb-board-tab-close" onclick="event.stopPropagation();closeBoardTab('${b.id}')" title="Close tab (board stays saved)">✕</span>`;
    return `<div class="pcb-board-tab${isActive?' active':''}" onclick="switchBoardTab('${b.id}')" title="${_escT(tooltip)}" style="border-left:3px solid ${color};padding-left:9px;">
      <span class="pcb-board-tab-name">${nm}</span>
      ${closeBtn}
    </div>`;
  }).join('')
  +`<button class="pcb-board-tab-new" onclick="newBoardVersion()" title="New layout version for this project">+</button>`;
}

async function switchBoardTab(boardId){
  if(boardId===_activeBoardId)return;
  try{
    const r=await fetch(`/api/pcb-boards/${boardId}`);
    if(!r.ok){alert('Board not found');return;}
    const board=await r.json();
    const res=editor.load(board);
    if(res.ok){
      _activeBoardId=boardId;
      // Keep project filter in sync with the board we just switched to
      if(board.projectId) _currentProjectId=board.projectId;
      afterLoad();
      switchPcbSection('layout');
      renderBoardTabs();
    }else{alert('Load error: '+res.error);}
  }catch(e){alert('Load failed: '+e.message);}
}

function closeBoardTab(boardId){
  // Blocked while a parent schematic is open — boards must stay visible
  if(_currentProjectId) return;
  // Remove from the tab bar only — board stays saved on the server
  _pcbBoardsList=_pcbBoardsList.filter(b=>b.id!==boardId);
  if(_activeBoardId===boardId){
    _activeBoardId=null;
    if(editor){editor.board=null;editor.render();}
    const bi=document.getElementById('board-info');
    if(bi)bi.innerHTML='No board';
    const cl=document.getElementById('comp-list');
    if(cl)cl.innerHTML='<div style="padding:8px;color:var(--text-muted);font-size:11px;">No board loaded</div>';
    rebuildCompList&&rebuildCompList();updateBoardInfo&&updateBoardInfo();updateInfoPanel&&updateInfoPanel();
  }
  renderBoardTabs();
}

async function newBoardVersion(){
  const btn = document.getElementById('btn-autoplace');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Creating…'; }
  switchPcbSection('layout');

  try {
    let board;

    if (_currentProjectId) {
      // Import schematic components fresh — backend calculates correct V{n} title
      const bw = 100, bh = 80;
      const projRes = await fetch(`/api/projects/${_currentProjectId}`);
      if (!projRes.ok) throw new Error('Could not load project');
      const proj = await projRes.json();
      board = await importSchematic(proj, _currentNetlist, bw, bh);
      if (!board) throw new Error('No physical components in schematic');
      // Backend already sets board.title to "{project} - V{n+1}" — don't override it
      board.projectId = _currentProjectId;
    } else {
      board = {
        title: 'Design - V1',
        board: { width: 100, height: 80, units: 'mm' },
        components: [], nets: [], traces: [], vias: [], areas: [],
      };
    }

    // Save to get an ID, then load
    const saveRes = await fetch('/api/pcb-boards', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(board),
    });
    const saved = await saveRes.json();
    board.id = saved.id;
    await animateBoardLoad(board);
    _activeBoardId = board.id;
    afterLoad();
    await loadBoardTabs(_currentProjectId);

    // Auto-place components
    if (board.components && board.components.length) {
      if (btn) btn.textContent = '⏳ Placing…';
      const apRes = await fetch(`/api/pcb/${board.id}/autoplace`, { method: 'POST' });
      const apData = await apRes.json();
      if (apRes.ok && apData.components && editor?.board) {
        editor.board.components = apData.components;
        editor.render(); rebuildCompList && rebuildCompList(); updateBoardInfo && updateBoardInfo();
        await saveBoard();
      }
    }
  } catch(e) {
    alert('Could not create board version: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🎯 Auto Place'; }
  }
}

async function renderBoardsSection(){
  const list=document.getElementById('boards-inline-list');
  list.innerHTML='<div style="color:var(--text-muted);font-size:13px;">Loading…</div>';
  try{
    const r=await fetch('/api/pcb-boards');
    const boards=await r.json();
    if(!boards.length){
      list.innerHTML='<div style="color:var(--text-muted);font-size:13px;padding:20px 0;">No saved boards yet.<br>Import a schematic and click 💾 Save in the Layout tab.</div>';
      return;
    }
    list.innerHTML='';
    for(const b of boards){
      const d=document.createElement('div');
      d.className='board-item';
      d.innerHTML=`<div class="board-item-info">
        <div class="board-item-title">${b.title||b.id}</div>
        <div class="board-item-meta">${b.component_count||0} components · ${new Date(b.updated_at||0).toLocaleString()}</div>
      </div>
      <button class="tbtn danger" data-del="${b.id}" title="Delete">🗑</button>
      <button class="tbtn primary" data-load="${b.id}">Load →</button>`;
      d.querySelector('[data-del]').onclick=async e=>{
        e.stopPropagation();
        if(!confirm('Delete this board?'))return;
        await fetch(`/api/pcb-boards/${b.id}`,{method:'DELETE'});
        d.remove();
      };
      const loadFn=async()=>{
        try{
          const rr=await fetch(`/api/pcb-boards/${b.id}`);
          const board=await rr.json();
          const res=editor.load(board);
          if(res.ok){_activeBoardId=b.id;afterLoad();switchPcbSection('layout');}
          else alert('Load error: '+res.error);
        }catch(e){alert('Load failed: '+e.message);}
      };
      d.querySelector('[data-load]').onclick=e=>{e.stopPropagation();loadFn();};
      list.appendChild(d);
    }
  }catch(e){list.innerHTML=`<div style="color:var(--red);font-size:12px;">Error: ${e.message}</div>`;}
}

function renderNetsSection(){
  const wrap=document.getElementById('nets-content');
  if(!editor||!editor.board){
    wrap.innerHTML='<div style="color:var(--text-muted);font-size:13px);">Load a board to view nets.</div>';
    return;
  }
  const nets=editor.board.nets||[];
  if(!nets.length){wrap.innerHTML='<div style="color:var(--text-muted);">No nets in this board.</div>';return;}
  const padCount={};
  for(const c of(editor.board.components||[]))
    for(const p of(c.pads||[]))if(p.net)(padCount[p.net]=(padCount[p.net]||0)+1);
  wrap.innerHTML=`<table class="nets-table">
    <thead><tr><th>Net</th><th style="text-align:right;">Pads</th><th>Connected Pins</th></tr></thead>
    <tbody>${nets.map(n=>`<tr>
      <td><span style="font-family:monospace;font-weight:700;color:${editor._netCol(n.name)}">${n.name}</span></td>
      <td style="text-align:right;color:var(--text-muted);">${padCount[n.name]||0}</td>
      <td style="color:var(--text-dim);font-size:11px;">${(n.pads||[]).slice(0,10).join(', ')}${(n.pads||[]).length>10?' …':''}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

