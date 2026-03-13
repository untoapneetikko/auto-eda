
// ═══════════════════════════════════════════════════════════════
// App bootstrap
// ═══════════════════════════════════════════════════════════════
let editor;

window.addEventListener('load',()=>{
  // If standalone (not embedded in iframe), show back link
  const isEmbedded = window.location.search.includes('embedded=1');
  // app=1  → main app PCB frame (shows board tabs, left panel)
  // embedded=1 without app=1 → Layout Example iframe (hides board tabs, left panel)
  const isAppFrame = window.location.search.includes('app=1');
  if(!isEmbedded && !isAppFrame && window.parent===window){
    const bl=document.getElementById('pcb-back-link');
    if(bl)bl.style.display='';
  }
  if(isEmbedded && !isAppFrame){
    document.body.classList.add('embedded');
  }
  const cv=document.getElementById('pcb-canvas');
  const wrap=document.getElementById('canvas-wrap');
  const canvasInner=document.getElementById('canvas-inner');
  function resize(){const el=canvasInner||wrap;cv.width=el.clientWidth;cv.height=el.clientHeight;if(editor)editor.render();}
  window.addEventListener('resize',resize);
  _loadDRStorage(); // restore persisted design rules before anything renders
  editor=new PCBEditor(cv);
  resize();
  // Auto-save DR whenever any field in the Design Rules tab changes (debounced)
  let _drSaveTimer=null;
  function _debouncedSaveDR(){clearTimeout(_drSaveTimer);_drSaveTimer=setTimeout(saveDRTab,400);}
  document.addEventListener('input', e=>{if(e.target.closest&&e.target.closest('#pcb-section-drc'))_debouncedSaveDR();});
  document.addEventListener('change',e=>{if(e.target.closest&&e.target.closest('#pcb-section-drc'))_debouncedSaveDR();});
  buildLayerPanel();
  updateWorkLayerBadge();
  editor.render();
  updateToolPanel('select');
  // In app-frame mode the parent sends importProject which sets _currentProjectId,
  // so don't load all boards at startup — wait for that message to scope the tabs.
  const _initTabLoad = isAppFrame ? Promise.resolve() : loadBoardTabs();
  _initTabLoad.then(()=>{
    // Restore last board from localStorage — standalone mode only.
    // In embedded/app-frame mode the parent page controls the board via importProject messages,
    // so we must not pre-load a stale board that would interfere with that flow.
    if(!isEmbedded && !isAppFrame && !editor.board){
      try{
        const saved=localStorage.getItem('pcb_last_board');
        if(saved){
          const parsed=JSON.parse(saved);
          const res=editor.load(parsed);
          if(res.ok){afterLoad();switchPcbSection('layout');}
        }
      }catch(_){}
    }
  });

  // postMessage API for embedding (e.g. Layout Example tab)
  window.addEventListener('message', e => {
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'tabVisible') {
      // Parent section became visible — re-size canvas and fit board
      resize();
      if (editor && editor.board) editor.render();
    }
    if (e.data.type === 'loadBoard' && e.data.board) {
      editor.hideBoardOutline = !!e.data.hideBoardOutline;
      const result = editor.load(e.data.board);
      if (result.ok) {
        editor.render();
        // Auto-run DRC so conflict markers and table appear immediately
        setTimeout(()=>{if(typeof runDRCTab==='function')runDRCTab();},100);
      }
    }
    if (e.data.type === 'getBoard') {
      // Parent requesting current board JSON (e.g. for save)
      const src = e.source || (window.parent !== window ? window.parent : null);
      if (src) src.postMessage({ type: 'boardData', board: editor.board }, '*');
    }
    if (e.data.type === 'importProject' && e.data.projectId) {
      // Guard: if the same project is already mid-import, ignore this retry message.
      // Without this, the 400ms retry from _doPCBImport fires a second concurrent
      // handler before the first finishes saving — causing two boards both named V1.
      if (_importingProjectId === e.data.projectId) return;
      _importingProjectId = e.data.projectId;
      _currentProjectId = e.data.projectId; // track the owning schematic
      _currentNetlist   = e.data.netlist || {};
      loadBoardTabs(_currentProjectId);     // scope tabs to this project only
      const opts = e.data.boardOpts || {};
      const src = e.source || (window.parent !== window ? window.parent : null);
      (async () => {
        try {
          // Look for an existing board linked to this project
          const existingRes = await fetch(`/api/pcb-boards?projectId=${encodeURIComponent(e.data.projectId)}`);
          const existingBoards = await existingRes.json();
          let bw = opts.boardW || 80;
          let bh = opts.boardH || 60;
          // If an existing board is found for this project, load it and merge any
          // new schematic components that aren't in the board yet — preserving all
          // existing component positions.
          if (existingBoards && existingBoards.length > 0) {
            const eb = existingBoards[0];
            const ebFull = await fetch(`/api/pcb-boards/${eb.id}`).then(r=>r.json());
            const loadRes = editor.load(ebFull);
            if (loadRes.ok) {
              _activeBoardId = eb.id;

              // ── Merge new schematic components into existing board ──────────
              // Run a fresh import to get what the schematic currently contains,
              // then diff against the loaded board — adding anything missing.
              try {
                const projRes = await fetch(`/api/projects/${e.data.projectId}`);
                const proj = await projRes.json();
                const boardW = editor.board.boardW || (editor.board.board && editor.board.board.width) || bw;
                const boardH = editor.board.boardH || (editor.board.board && editor.board.board.height) || bh;
                const freshBoard = await importSchematic(proj, e.data.netlist, boardW, boardH);

                if (freshBoard && freshBoard.components) {
                  // Always update schematicRefs to the current schematic state
                  editor.board.schematicRefs = freshBoard.components.map(c=>({ref:c.ref||c.id,value:c.value||'',footprint:c.footprint||''}));
                  const existingRefs = new Set((editor.board.components || []).map(c => c.ref || c.id));
                  const newComps = freshBoard.components.filter(c => !existingRefs.has(c.ref || c.id));

                  if (newComps.length > 0) {
                    // Place new components in a staging row to the right of the board
                    let stageX = boardW + 10;
                    let stageY = 5;
                    for (const nc of newComps) {
                      nc.x = stageX;
                      nc.y = stageY;
                      stageY += 12;
                      if (stageY > boardH) { stageY = 5; stageX += 20; }
                      editor.board.components.push(nc);
                    }
                    // Merge new groups
                    if (freshBoard.groups) {
                      editor.board.groups = editor.board.groups || [];
                      const existingGrpIds = new Set(editor.board.groups.map(g => g.id));
                      for (const g of freshBoard.groups) {
                        if (!existingGrpIds.has(g.id)) editor.board.groups.push(g);
                      }
                    }
                    // Refresh ratsnest nets
                    if (freshBoard.nets) editor.board.nets = freshBoard.nets;
                    await saveBoard();
                    console.log(`[importProject] merged ${newComps.length} new component(s) into existing board`);
                  }
                }
              } catch(mergeErr) {
                console.warn('[importProject] merge step failed (non-fatal):', mergeErr);
              }
              // ── End merge ──────────────────────────────────────────────────

              switchPcbSection('layout');
              afterLoad();
              await loadBoardTabs(_currentProjectId);
              _staleProjId = null;
              const _sb = document.getElementById('pcb-stale-banner');
              if (_sb) _sb.style.display = 'none';
              if (src) src.postMessage({ type: 'importProjectDone' }, '*');
              setTimeout(()=>{if(typeof runDRCTab==='function')runDRCTab();},200);
              return;
            }
          }
          // No saved board exists yet — do a fresh import from the schematic
          const r = await fetch(`/api/projects/${e.data.projectId}`);
          const proj = await r.json();
          const pcb = await importSchematic(proj, e.data.netlist, bw, bh);
          if (pcb) {
            // Store canonical component list so the UI can mark deleted ones as "missing"
            pcb.schematicRefs = (pcb.components||[]).map(c=>({ref:c.ref||c.id,value:c.value||'',footprint:c.footprint||''}));
            switchPcbSection('layout');
            await animateBoardLoad(pcb);
            // Stamp the project id so there's no cross-project contamination
            editor.board.projectId = e.data.projectId;
            const res = {ok: !!editor.board};
            if (res.ok) {
              afterLoad();
              await saveBoard(); // auto-save so board appears in tabs
              // Board is now in sync — clear stale banner
              _staleProjId=null;
              const _sb=document.getElementById('pcb-stale-banner');
              if(_sb)_sb.style.display='none';
              if (src) src.postMessage({ type: 'importProjectDone' }, '*');
              // Auto-run DRC so net-mismatch markers appear immediately
              setTimeout(()=>{if(typeof runDRCTab==='function')runDRCTab();},200);
            }
          }
        } catch(err) { console.error('importProject error', err); }
        finally { _importingProjectId = null; } // release guard so future imports work
      })();
    }
    if (e.data.type === 'schematicDirty') {
      const { projectId, isDirty } = e.data;
      _updateStaleBanner(projectId, isDirty);
    }
    if (e.data.type === 'projectClosed' && e.data.projectId === _currentProjectId) {
      // Parent schematic tab closed — clear all child boards
      _currentProjectId = null;
      _currentNetlist   = {};
      _pcbBoardsList    = [];
      _activeBoardId    = null;
      if (editor) { editor.board = null; editor.render(); }
      renderBoardTabs();
      const bi = document.getElementById('board-info');
      if (bi) bi.innerHTML = 'No board';
      const cl = document.getElementById('comp-list');
      if (cl) cl.innerHTML = '<div style="padding:8px;color:var(--text-muted);font-size:11px;">No board loaded</div>';
      rebuildCompList && rebuildCompList();
      updateBoardInfo && updateBoardInfo();
      updateInfoPanel && updateInfoPanel();
    }
    if (e.data.type === 'addComponent' && e.data.component && editor.board) {
      const c = e.data.component;
      // Place at centre of current viewport
      const vx = editor.snap(editor.cX(editor.canvas.width / 2));
      const vy = editor.snap(editor.cY(editor.canvas.height / 2));
      // Offset slightly so multiple adds don't stack exactly
      const n = (editor.board.components || []).length;
      const offX = (n % 5) * editor.gridSize * 2;
      const offY = Math.floor(n / 5) * editor.gridSize * 2;
      const comp = {
        id: c.ref + '_' + Date.now(),
        ref: c.ref, value: c.value || '',
        footprint: c.footprint || '',
        x: editor.snap(vx + offX), y: editor.snap(vy + offY),
        rotation: c.rotation || 0, layer: c.layer || 'F',
        pads: (c.pads || []).map(p => ({ ...p }))
      };
      (editor.board.components || (editor.board.components = [])).push(comp);
      editor.selectedComp = comp; editor.selectedTrace = null;
      editor._snapshot(); editor.render(); rebuildCompList(); updateInfoPanel();
      _notifyParentRefs();
    }
    if (e.data.type === 'selectComponent' && e.data.ref && editor.board) {
      const comp = (editor.board.components || []).find(c => c.ref === e.data.ref);
      if (comp) {
        editor.selectedComp = comp; editor.selectedTrace = null; editor.selectedVia = null; editor.selectedArea = null;
        // Pan canvas to centre on component
        editor.panX = editor.canvas.width / 2 - comp.x * editor.scale;
        editor.panY = editor.canvas.height / 2 - comp.y * editor.scale;
        editor.render(); updateInfoPanel();
        // Brief highlight flash — temporarily boost selectedComp outline
        const origSel = editor.selectedComp;
        editor._flashComp = comp; setTimeout(() => { editor._flashComp = null; editor.render(); }, 700);
      }
    }
  });
});

