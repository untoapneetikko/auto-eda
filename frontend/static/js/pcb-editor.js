// ═══════════════════════════════════════════════════════════════
// PCBEditor
// ═══════════════════════════════════════════════════════════════
class PCBEditor {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.board = null;
    this.scale = 8; this.offsetX = 60; this.offsetY = 60;
    this.panX = 0; this.panY = 0;
    this.gridSize = 0.5;
    this.tool = 'select';
    this.selectedComp = null;
    this.selectedTrace = null;
    this.selectedVia = null;
    this.selectedPad = null; // {comp, pad}
    this.selectedComps = []; // multi-select (Ctrl+click)
    this._hoverTrace = null;
    this._hoverComp = null;
    // Click-cycle: remember last click pos + last picked object for cycling
    this._lastClickMx = null; this._lastClickMy = null; this._lastClickObj = null;
    this.routePoints = []; this.routeNet = null; this.routeLayer = 'F.Cu';
    this._elbowMode = 0; // 0 = H/diag first, 1 = V/diag first; toggle with '/'
    this.zonePoints = []; this.zoneNet = null;
    this.measureStart = null;
    this.areaStart = null; this.areaNet = 'GND'; this.areaLayer = 'F.Cu';
    this.selectedArea = null;
    this._isAreaDrag = false;
    this.drawPoints = []; this.drawLayer = 'Edge.Cuts'; this.drawWidth = 0.05;
    this.selectedDrawing = null;
    this._routeError = null;
    this._mx = 0; this._my = 0; this._mxPx = null; this._myPx = null;
    this._isDragVia = false;
    // Undo/redo history
    this._history = []; this._historyIdx = -1;
    this.workLayer = 'F.Cu'; // active work layer — rendered on top
    this.layers = {
      'F.Cu':      { color:'#cc6633', visible:true, active:true,  displayName:'Top Copper' },
      'B.Cu':      { color:'#4466ee', visible:true, active:false, displayName:'Bottom Copper' },
      'F.Mask':    { color:'#1a7a2a', visible:true, active:false, displayName:'Top Solder Mask' },
      'B.Mask':    { color:'#0e5c1e', visible:true, active:false, displayName:'Bottom Solder Mask' },
      'F.Paste':   { color:'#aaaaaa', visible:true, active:false, displayName:'Top Solder Paste' },
      'B.Paste':   { color:'#888888', visible:true, active:false, displayName:'Bottom Solder Paste' },
      'F.SilkS':   { color:'#cccccc', visible:true, active:false, displayName:'Top Silk' },
      'B.SilkS':   { color:'#777777', visible:true, active:false, displayName:'Bottom Silk' },
      'Vias':      { color:'#88aacc', visible:true, active:false, displayName:'Vias (Multi-layer)' },
      'Edge.Cuts': { color:'#ffee00', visible:true, active:false, displayName:'Board Outline' },
      'Ratsnest':  { color:'#337733', visible:true, active:false, displayName:'Ratsnest' },
    };
    this._isPan=false; this._panS=null;
    this._isBoxSel=false;this._boxSelStart=null;this._boxSelEnd=null;
    this._isDrag=false; this._dragC=null; this._dragOff={x:0,y:0};
    this._isDragTrace=false; this._dragTrace=null; this._dragTraceSegIdx=-1;
    this._dragTraceOrigSeg=null; this._dragTraceOrigAll=null; this._dragTraceOff={x:0,y:0};
    this._traceDragViolations=[];
    this._lastMoveMs=0; // mousemove throttle timestamp
    this._init();
  }

  mmX(mm){return mm*this.scale+this.offsetX+this.panX;}
  mmY(mm){return mm*this.scale+this.offsetY+this.panY;}
  cX(px){return(px-this.offsetX-this.panX)/this.scale;}
  cY(py){return(py-this.offsetY-this.panY)/this.scale;}
  snap(v){return Math.round(v/this.gridSize)*this.gridSize;}

  load(json){
    try{
      const d=typeof json==='string'?JSON.parse(json):json;
      if(!d.board||!d.components)throw new Error('Missing board or components');
      // Normalize: move outline-based areas into zones so _drawZones handles them
      if(d.areas){
        if(!d.zones) d.zones=[];
        const kept=[];
        for(const a of d.areas){
          if(a.outline&&a.outline.length>=3){
            d.zones.push({layer:a.layer||'F.Cu',net:a.net||'',points:a.outline});
          } else {
            kept.push(a);
          }
        }
        d.areas=kept;
      }
      this.board=d; this.selectedComp=null; this.routePoints=[];
      this.zonePoints=[]; this.fitBoard();
      return{ok:true};
    }catch(e){return{ok:false,error:e.message};}
  }

  _snapshot(){
    if(!this.board)return;
    const snap=JSON.stringify(this.board); // keep as string — structuredClone can fail on circular refs
    // Truncate forward history on new action
    this._history=this._history.slice(0,this._historyIdx+1);
    this._history.push(snap);
    if(this._history.length>80)this._history.shift();
    this._historyIdx=this._history.length-1;
    // Debounced auto-persist: save to localStorage (standalone) and auto-save to
    // server (embedded/saved boards) so edits survive a page refresh without
    // requiring an explicit Save click.
    clearTimeout(this._lsPersistTimer);
    this._lsPersistTimer=setTimeout(()=>{
      try{localStorage.setItem('pcb_last_board',snap);}catch(_){}
      // Auto-save to server if this board was already saved (has an id).
      // Uses the global saveBoard() defined in the page scripts.
      if(this.board?.id && typeof saveBoard==='function') saveBoard();
    },3000);
  }

  undo(){
    if(this._historyIdx<=0)return;
    this._historyIdx--;
    this.board=JSON.parse(this._history[this._historyIdx]);
    this.selectedComp=null;this.selectedTrace=null;this.selectedVia=null;this.selectedPad=null;this.selectedComps=[];
    this.render();if(typeof rebuildCompList==='function')rebuildCompList();if(typeof updateInfoPanel==='function')updateInfoPanel();
  }

  redo(){
    if(this._historyIdx>=this._history.length-1)return;
    this._historyIdx++;
    this.board=JSON.parse(this._history[this._historyIdx]);
    this.selectedComp=null;this.selectedTrace=null;this.selectedVia=null;this.selectedPad=null;this.selectedComps=[];
    this.render();if(typeof rebuildCompList==='function')rebuildCompList();if(typeof updateInfoPanel==='function')updateInfoPanel();
  }

  render(){
    if(this._renderPending)return;
    this._renderPending=true;
    requestAnimationFrame(()=>{this._renderPending=false;this._renderNow();});
  }
  _renderNow(){
    const ctx=this.ctx,W=this.canvas.width,H=this.canvas.height;
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle='#080808'; ctx.fillRect(0,0,W,H);
    if(!this.board){
      ctx.fillStyle='rgba(255,255,255,0.1)'; ctx.font='14px system-ui';
      ctx.textAlign='center';
      ctx.fillText('Import a schematic project or load PCB JSON to begin',W/2,H/2-8);
      ctx.font='12px system-ui'; ctx.fillStyle='rgba(255,255,255,0.05)';
      ctx.fillText('Click "Import Schematic" or "💡 Example" in the toolbar',W/2,H/2+14);
      ctx.textAlign='left'; return;
    }
    if(this.layers['Edge.Cuts'].visible && !this.hideBoardOutline) this._drawBoard();
    this._drawGrid();
    // Work layer: active layer is rendered on top at full alpha; others dimmed
    const wl=this.workLayer||'F.Cu';
    const wIsCu=wl==='F.Cu'||wl==='B.Cu';
    const wSide=(wl==='B.Cu')?'B':'F'; // copper side for work layer (default F)
    const otherCu=wSide==='F'?'B.Cu':'F.Cu';
    const otherSide=wSide==='F'?'B':'F';
    const otherSilk=wSide==='F'?'B.SilkS':'F.SilkS';
    const workSilk=wSide==='F'?'F.SilkS':'B.SilkS';
    const _safe=(fn)=>{try{fn();}catch(e){console.error('[PCBEditor render]',e);}};
    if(wIsCu){
      // Draw non-work copper + pads + silk at reduced alpha
      this.ctx.globalAlpha=0.45;
      _safe(()=>{if(this.layers[otherCu].visible) this._drawTraces(otherCu);});
      _safe(()=>{if(this.layers[otherSilk].visible) this._drawSilk(otherSide);});
      _safe(()=>this._drawPads(otherSide));
      this.ctx.globalAlpha=1.0;
      // Zones & areas (board-wide)
      _safe(()=>this._drawZones());
      _safe(()=>this._drawAreas());
      // Vias always fully visible
      _safe(()=>this._drawVias());
      // Work layer copper + pads + silk on top at full alpha
      this.ctx.globalAlpha=1.0;
      _safe(()=>{if(this.layers[wl].visible) this._drawTraces(wl);});
      _safe(()=>this._drawPads(wSide));
      _safe(()=>{if(this.layers[workSilk].visible) this._drawSilk(wSide);});
    } else {
      // Non-copper work layer: draw everything normally, work layer content is on top by draw order
      _safe(()=>{if(this.layers['B.Cu'].visible) this._drawTraces('B.Cu');});
      _safe(()=>{if(this.layers['F.Cu'].visible) this._drawTraces('F.Cu');});
      _safe(()=>this._drawZones()); _safe(()=>this._drawAreas()); _safe(()=>this._drawVias());
      this.ctx.globalAlpha=1.0;
      _safe(()=>this._drawPads());
      _safe(()=>{if(this.layers['B.SilkS'].visible) this._drawSilk('B');});
      _safe(()=>{if(this.layers['F.SilkS'].visible) this._drawSilk('F');});
    }
    if(this.layers['Ratsnest'].visible) this._drawRatsnest();
    this._drawGroups();
    this._drawDrawings();
    if(this.tool==='route'&&this.routePoints.length>0) this._drawActiveRoute();
    if(this.tool==='route') this._drawSnapIndicator();
    if(this.tool==='zone'&&this.zonePoints.length>0) this._drawActiveZone();
    if(this.tool==='measure'&&this.measureStart) this._drawMeasure();
    if(this.tool==='area'&&this.areaStart) this._drawActiveArea();
    if(this.tool==='draw'&&this.drawPoints.length>0) this._drawActiveDrawing();
    if(this._hoverComp&&!this.selectedComps.includes(this._hoverComp)) this._drawHoverComp(this._hoverComp);
    for(const sc of this.selectedComps) this._drawSel(sc);
    if(this.selectedComp&&!this.selectedComps.includes(this.selectedComp)) this._drawSel(this.selectedComp);
    if(this.selectedPad) this._drawSelPad(this.selectedPad);
    if(this._routeError){
      ctx.fillStyle='rgba(239,68,68,0.92)';ctx.font='bold 12px monospace';
      ctx.textAlign='center';
      ctx.fillText('⊘ '+this._routeError,W/2,H-20);
      ctx.textAlign='left';
    }
    // Net conflict markers (NET_CONFLICT, NET_MISMATCH, TRACE_CROSSING) from last DRC run
    const _CONFLICT_TYPES=new Set(['NET_CONFLICT','NET_MISMATCH','TRACE_CROSSING']);
    if(_drcResults?.length){
      ctx.setLineDash([]);
      for(const e of _drcResults){
        if(!_CONFLICT_TYPES.has(e.type)||e.x==null)continue;
        const cx=this.mmX(e.x),cy=this.mmY(e.y),s=6;
        // Filled circle background
        ctx.fillStyle='rgba(239,68,68,0.18)';
        ctx.beginPath();ctx.arc(cx,cy,s+3,0,Math.PI*2);ctx.fill();
        // Ring
        ctx.strokeStyle='rgba(239,68,68,0.5)';ctx.lineWidth=1;
        ctx.beginPath();ctx.arc(cx,cy,s+3,0,Math.PI*2);ctx.stroke();
        // X cross
        ctx.strokeStyle='rgba(239,68,68,0.95)';ctx.lineWidth=2;
        ctx.beginPath();ctx.moveTo(cx-s,cy-s);ctx.lineTo(cx+s,cy+s);ctx.stroke();
        ctx.beginPath();ctx.moveTo(cx+s,cy-s);ctx.lineTo(cx-s,cy+s);ctx.stroke();
      }
    }
    // Draw real-time clearance violations during trace drag
    if(this._isDragTrace&&this._traceDragViolations.length>0){
      ctx.save(); ctx.setLineDash([]);
      for(const v of this._traceDragViolations){
        if(v.isPad){
          const cx=this.mmX(v.px),cy=this.mmY(v.py),r=Math.max(6,v.padR*this.scale+3);
          ctx.strokeStyle='rgba(239,68,68,0.8)'; ctx.lineWidth=2;
          ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.stroke();
          ctx.fillStyle='rgba(239,68,68,0.15)';
          ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.fill();
        } else {
          ctx.strokeStyle='rgba(239,68,68,0.8)'; ctx.lineWidth=Math.max(2,(v.w||0.25)*this.scale)+4;
          ctx.beginPath();
          ctx.moveTo(this.mmX(v.ax),this.mmY(v.ay));
          ctx.lineTo(this.mmX(v.bx),this.mmY(v.by));
          ctx.stroke();
        }
      }
      // Violation label
      ctx.fillStyle='rgba(239,68,68,0.92)'; ctx.font='bold 11px monospace';
      ctx.textAlign='center';
      ctx.fillText(`⚠ Clearance violation (${this._traceDragViolations.length})`,W/2,H-20);
      ctx.textAlign='left';
      ctx.restore();
    }
    if(this._isBoxSel&&this._boxSelStart&&this._boxSelEnd){
      const bx1=Math.min(this._boxSelStart.mx,this._boxSelEnd.mx);
      const by1=Math.min(this._boxSelStart.my,this._boxSelEnd.my);
      const bx2=Math.max(this._boxSelStart.mx,this._boxSelEnd.mx);
      const by2=Math.max(this._boxSelStart.my,this._boxSelEnd.my);
      ctx.strokeStyle='rgba(99,102,241,0.9)';ctx.lineWidth=1;ctx.setLineDash([4,3]);
      ctx.strokeRect(bx1,by1,bx2-bx1,by2-by1);
      ctx.fillStyle='rgba(99,102,241,0.08)';ctx.fillRect(bx1,by1,bx2-bx1,by2-by1);
      ctx.setLineDash([]);
    }
  }

  _drawBoard(){
    const ctx=this.ctx,b=this.board.board;
    const x=this.mmX(0),y=this.mmY(0),w=b.width*this.scale,h=b.height*this.scale;
    ctx.fillStyle='#0d1f0d'; ctx.fillRect(x,y,w,h);
    ctx.strokeStyle=this.layers['Edge.Cuts'].color;
    ctx.lineWidth=1.5; ctx.setLineDash([]); ctx.strokeRect(x,y,w,h);
    ctx.fillStyle='rgba(255,255,0,0.4)'; ctx.font='10px monospace'; ctx.textAlign='left';
    ctx.fillText(`${b.width}×${b.height}mm`,x+3,y+12);
  }

  _drawDrawings(){
    const ctx=this.ctx;
    for(const d of(this.board?.drawings||[])){
      if(!d.points||d.points.length<2)continue;
      const lyr=d.layer||'Edge.Cuts';
      if(!this.layers[lyr]?.visible)continue;
      const col=this.layers[lyr]?.color||'#ffee00';
      const isSel=d===this.selectedDrawing;
      ctx.strokeStyle=isSel?'#ffffff':col;
      ctx.lineWidth=Math.max(1,((d.width||0.05)*this.scale));
      ctx.lineCap='round'; ctx.lineJoin='round'; ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(this.mmX(d.points[0].x),this.mmY(d.points[0].y));
      for(let i=1;i<d.points.length;i++)
        ctx.lineTo(this.mmX(d.points[i].x),this.mmY(d.points[i].y));
      if(d.closed)ctx.closePath();
      ctx.stroke();
      // Draw endpoint dots
      ctx.fillStyle=isSel?'rgba(255,255,255,0.6)':col+'80';
      for(const p of d.points){
        ctx.beginPath();ctx.arc(this.mmX(p.x),this.mmY(p.y),3,0,Math.PI*2);ctx.fill();
      }
    }
    ctx.lineCap='butt'; ctx.lineJoin='miter';
  }

  _drawActiveDrawing(){
    const ctx=this.ctx,pts=this.drawPoints;
    if(!pts.length)return;
    const lyr=this.drawLayer||'Edge.Cuts';
    const col=this.layers[lyr]?.color||'#ffee00';
    ctx.strokeStyle=col;
    ctx.lineWidth=Math.max(1,(this.drawWidth||0.05)*this.scale);
    ctx.lineCap='round'; ctx.lineJoin='round'; ctx.setLineDash([3,2]);
    ctx.beginPath();
    ctx.moveTo(this.mmX(pts[0].x),this.mmY(pts[0].y));
    for(let i=1;i<pts.length;i++) ctx.lineTo(this.mmX(pts[i].x),this.mmY(pts[i].y));
    // Live cursor segment
    ctx.lineTo(this.mmX(this._mx),this.mmY(this._my));
    ctx.stroke();
    ctx.setLineDash([]);
    // Dots for placed points
    ctx.fillStyle=col;
    for(const p of pts){
      ctx.beginPath();ctx.arc(this.mmX(p.x),this.mmY(p.y),3,0,Math.PI*2);ctx.fill();
    }
    // Snap indicator at cursor
    ctx.strokeStyle=col+'bb'; ctx.lineWidth=1;
    ctx.strokeRect(this.mmX(this._mx)-4,this.mmY(this._my)-4,8,8);
    ctx.lineCap='butt'; ctx.lineJoin='miter';
  }

  _drawGrid(){
    const ctx=this.ctx,b=this.board.board,s=this.gridSize;
    const ds=this.scale>=10?1:0.5;
    ctx.fillStyle='rgba(255,255,255,0.07)';
    for(let gx=0;gx<=b.width+s;gx+=s)
      for(let gy=0;gy<=b.height+s;gy+=s)
        ctx.fillRect(this.mmX(gx)-ds,this.mmY(gy)-ds,ds*2,ds*2);
  }

  _drawTraces(layer){
    const ctx=this.ctx;
    ctx.lineCap='round'; ctx.lineJoin='round'; ctx.setLineDash([]);
    for(const tr of(this.board.traces||[])){
      if((tr.layer||'F.Cu')!==layer)continue;
      const sel=this.selectedTrace===tr;
      const hov=this._hoverTrace===tr&&!sel;
      const isDragging=this._isDragTrace&&this._dragTrace===tr;
      const dragSegIdx=isDragging?this._dragTraceSegIdx:-1;
      const w=Math.max(1,(tr.width||tr.width_mm||0.25)*this.scale);
      const segs=tr.segments||[];
      // Draw path helper (optionally skip one segment)
      const drawPath=(skipIdx=-1)=>{
        const cAngle=DR?.cornerAngle??90;
        ctx.beginPath();
        let prevEndX=null,prevEndY=null;
        for(let si=0;si<segs.length;si++){
          if(si===skipIdx){prevEndX=prevEndY=null;continue;}
          const seg=segs[si];
          const sx=this.mmX(seg.start.x),sy=this.mmY(seg.start.y);
          const ex=this.mmX(seg.end.x),ey=this.mmY(seg.end.y);
          const gapped=prevEndX===null
            ||Math.abs(sx-prevEndX)>0.5||Math.abs(sy-prevEndY)>0.5;
          if(gapped)ctx.moveTo(sx,sy);
          // Find next valid connected segment for corner rounding
          let nsi=-1;
          for(let j=si+1;j<segs.length;j++){if(j!==skipIdx){nsi=j;break;}}
          if(nsi>=0){
            const ns=segs[nsi];
            const nsx=this.mmX(ns.start.x),nsy=this.mmY(ns.start.y);
            if(Math.abs(ex-nsx)<0.5&&Math.abs(ey-nsy)<0.5){
              const nx2=this.mmX(ns.end.x),ny2=this.mmY(ns.end.y);
              const dx1=ex-sx,dy1=ey-sy,dx2=nx2-ex,dy2=ny2-ey;
              const len1=Math.hypot(dx1,dy1),len2=Math.hypot(dx2,dy2);
              if(len1>0&&len2>0){
                // Interior angle at this corner (180=straight, 90=right angle, 0=hairpin)
                const dot=Math.max(-1,Math.min(1,(dx1*dx2+dy1*dy2)/(len1*len2)));
                const intDeg=180-Math.acos(dot)*180/Math.PI;
                // Round if corner is tighter than allowed angle, or cAngle=0 (always round)
                if(cAngle===0||intDeg<cAngle){
                  // rFrac scales with how far below the threshold this corner is
                  const rFrac=cAngle===0?1:Math.min(1,(cAngle-intDeg)/cAngle);
                  // Use full shorter segment as radius so rounding is always clearly visible
                  const r=rFrac*Math.min(len1,len2)*0.49;
                  if(r>0){ctx.arcTo(ex,ey,nx2,ny2,r);prevEndX=ex;prevEndY=ey;continue;}
                }
              }
            }
          }
          ctx.lineTo(ex,ey);
          prevEndX=ex;prevEndY=ey;
        }
        ctx.stroke();
      };
      const drawSeg=(si)=>{
        const seg=segs[si];
        ctx.beginPath();
        ctx.moveTo(this.mmX(seg.start.x),this.mmY(seg.start.y));
        ctx.lineTo(this.mmX(seg.end.x),this.mmY(seg.end.y));
        ctx.stroke();
      };
      if(sel||isDragging){
        const _lc=this.layers[layer].color;
        const _bright=this._lightenColor(_lc,0.35);
        ctx.strokeStyle=_bright+'33'; ctx.lineWidth=w+10; drawPath();
        ctx.strokeStyle=_bright; ctx.lineWidth=w; drawPath();
        // Extra highlight on the dragged segment
        if(isDragging&&dragSegIdx>=0&&dragSegIdx<segs.length){
          const hasViolations=this._traceDragViolations.length>0;
          ctx.strokeStyle=hasViolations?'rgba(239,68,68,0.5)':'rgba(108,255,108,0.4)';
          ctx.lineWidth=w+14; drawSeg(dragSegIdx);
          ctx.strokeStyle=hasViolations?'#ef4444':'#6fff6f';
          ctx.lineWidth=w; drawSeg(dragSegIdx);
        }
      } else if(hov){
        ctx.strokeStyle='rgba(255,255,255,0.15)'; ctx.lineWidth=w+6; drawPath();
        ctx.strokeStyle=this.layers[layer].color+'cc'; ctx.lineWidth=w; drawPath();
      } else {
        ctx.strokeStyle=this.layers[layer].color; ctx.lineWidth=w; drawPath();
      }
      // Draw net label at midpoint when zoomed in or trace is selected/hovered
      if(tr.net && (sel || hov || this.scale >= 12)){
        const segs=tr.segments||[];
        if(segs.length>0){
          // Find midpoint segment
          const mid=segs[Math.floor(segs.length/2)];
          const mx=this.mmX((mid.start.x+mid.end.x)/2);
          const my=this.mmY((mid.start.y+mid.end.y)/2);
          const fs=Math.max(8,Math.min(11,w*0.7+7));
          ctx.save();
          ctx.font=`bold ${fs}px monospace`;
          ctx.textAlign='center'; ctx.textBaseline='middle';
          ctx.fillStyle='rgba(0,0,0,0.65)';
          const tw=ctx.measureText(tr.net).width;
          ctx.fillRect(mx-tw/2-2,my-fs/2-1,tw+4,fs+2);
          ctx.fillStyle= sel?this._lightenColor(this.layers[layer].color,0.35): this.layers[layer].color;
          ctx.fillText(tr.net,mx,my);
          ctx.restore();
        }
      }
    }
  }

  _drawZones(){
    const ctx=this.ctx;
    for(const z of(this.board.zones||[])){
      const cl=z.clearance!=null?z.clearance:(DR.clearance||0.2);
      if(!z.points||z.points.length<3)continue;
      const lyr=z.layer||'F.Cu';
      if(!this.layers[lyr]?.visible)continue;
      const col=this.layers[lyr].color;
      // Polygon bounding box for early rejection tests
      let zx1=Infinity,zy1=Infinity,zx2=-Infinity,zy2=-Infinity;
      for(const pt of z.points){
        zx1=Math.min(zx1,pt.x);zy1=Math.min(zy1,pt.y);
        zx2=Math.max(zx2,pt.x);zy2=Math.max(zy2,pt.y);
      }
      // Use an offscreen canvas sized to just this zone's screen bounding box.
      // This avoids the evenodd "phantom copper" bug: with evenodd clipping,
      // two overlapping clearance shapes flip back to filled. With destination-out
      // compositing, overlapping clearances simply erase — always correct.
      const margin=Math.ceil((cl+2)*this.scale)+2;
      const bx1=Math.floor(this.mmX(zx1))-margin;
      const by1=Math.floor(this.mmY(zy1))-margin;
      const bx2=Math.ceil(this.mmX(zx2))+margin;
      const by2=Math.ceil(this.mmY(zy2))+margin;
      const bw=Math.max(1,bx2-bx1),bh=Math.max(1,by2-by1);
      const off=document.createElement('canvas');
      off.width=bw; off.height=bh;
      const oc=off.getContext('2d');
      // Translate so that board coords map correctly into the small canvas
      oc.translate(-bx1,-by1);
      // ── Step 1: fill the zone polygon ────────────────────────────────────
      oc.beginPath();
      oc.moveTo(this.mmX(z.points[0].x),this.mmY(z.points[0].y));
      for(let i=1;i<z.points.length;i++)
        oc.lineTo(this.mmX(z.points[i].x),this.mmY(z.points[i].y));
      oc.closePath();
      oc.fillStyle=col+'33';
      oc.fill();
      // ── Step 2: cut clearance areas via destination-out ──────────────────
      // Overlapping cutouts stay empty (they don't re-fill like evenodd would).
      // Clearance applies to every copper object NOT on the same net as this zone,
      // including pads/vias with no net at all.
      oc.globalCompositeOperation='destination-out';
      oc.fillStyle='rgba(0,0,0,1)';
      oc.beginPath();
      // — Pad clearances (only pads on the same layer as this zone) —
      const zoneIsF=lyr.startsWith('F');
      for(const c of(this.board?.components||[])){
        const compIsF=(c.layer||'F')!=='B';
        const rot=(c.rotation||0)*Math.PI/180;
        const cosR=Math.cos(rot),sinR=Math.sin(rot);
        for(const p of(c.pads||[])){
          // Skip only if the pad is on the EXACT same net as the zone
          if(p.net&&p.net===z.net)continue;
          // Skip pads on a different layer (through-hole pads exist on both)
          const isThru=p.type==='thru_hole';
          if(!isThru&&compIsF!==zoneIsF)continue;
          const{px,py}=this._padWorld(c,p);
          const hpx=(p.size_x||1.6)/2,hpy=(p.size_y||1.6)/2;
          const maxhp=Math.max(hpx,hpy);
          if(px<zx1-maxhp-cl||px>zx2+maxhp+cl||py<zy1-maxhp-cl||py>zy2+maxhp+cl)continue;
          const psx=this.mmX(px),psy=this.mmY(py);
          if(p.shape==='rect'||p.shape==='square'){
            const hw=(hpx+cl)*this.scale,hh=(hpy+cl)*this.scale;
            const c0x=psx+(-hw)*cosR-(-hh)*sinR, c0y=psy+(-hw)*sinR+(-hh)*cosR;
            const c1x=psx+(+hw)*cosR-(-hh)*sinR, c1y=psy+(+hw)*sinR+(-hh)*cosR;
            const c2x=psx+(+hw)*cosR-(+hh)*sinR, c2y=psy+(+hw)*sinR+(+hh)*cosR;
            const c3x=psx+(-hw)*cosR-(+hh)*sinR, c3y=psy+(-hw)*sinR+(+hh)*cosR;
            oc.moveTo(c0x,c0y);
            oc.lineTo(c1x,c1y);
            oc.lineTo(c2x,c2y);
            oc.lineTo(c3x,c3y);
            oc.closePath();
          }else{
            const r=(maxhp+cl)*this.scale;
            oc.moveTo(psx+r,psy);
            oc.arc(psx,psy,r,0,Math.PI*2);
          }
        }
      }
      // — Via clearances (vias span all layers, always apply) —
      for(const v of(this.board?.vias||[])){
        if(v.net&&v.net===z.net)continue;
        if(v.x<zx1-cl||v.x>zx2+cl||v.y<zy1-cl||v.y>zy2+cl)continue;
        const r=((v.size||DR.viaSize||1.0)/2+cl)*this.scale;
        const vsx=this.mmX(v.x),vsy=this.mmY(v.y);
        oc.moveTo(vsx+r,vsy);
        oc.arc(vsx,vsy,r,0,Math.PI*2);
      }
      oc.fill(); // erase pad + via clearance shapes in one pass
      // — Trace clearances (only traces on the same layer as this zone) —
      oc.strokeStyle='rgba(0,0,0,1)';
      oc.lineCap='round';
      for(const tr of(this.board?.traces||[])){
        if(tr.net&&tr.net===z.net)continue;
        // Skip traces on different layer
        const trIsF=(tr.layer||'F.Cu').startsWith('F');
        if(trIsF!==zoneIsF)continue;
        const tw=(tr.width||tr.width_mm||DR.traceWidth||0.25)+cl*2;
        oc.lineWidth=tw*this.scale;
        for(const seg of(tr.segments||[])){
          if(!seg||!seg.start||!seg.end)continue;
          oc.beginPath();
          oc.moveTo(this.mmX(seg.start.x),this.mmY(seg.start.y));
          oc.lineTo(this.mmX(seg.end.x),this.mmY(seg.end.y));
          oc.stroke();
        }
      }
      // ── Step 3: composite the zone tile onto the main canvas ─────────────
      ctx.drawImage(off,bx1,by1);
      // Border (drawn on main canvas so outline is always sharp)
      ctx.strokeStyle=col+'99';
      ctx.lineWidth=1; ctx.setLineDash([4,2]);
      ctx.beginPath();
      ctx.moveTo(this.mmX(z.points[0].x),this.mmY(z.points[0].y));
      for(let i=1;i<z.points.length;i++)
        ctx.lineTo(this.mmX(z.points[i].x),this.mmY(z.points[i].y));
      ctx.closePath(); ctx.stroke(); ctx.setLineDash([]);
    }
  }

  _drawVias(){
    if(this.layers['Vias']&&!this.layers['Vias'].visible)return;
    const ctx=this.ctx;
    const viasList=Array.isArray(this.board.vias)?this.board.vias:[];
    for(const v of viasList){
      const cx=this.mmX(v.x),cy=this.mmY(v.y);
      const or=Math.max(3,(v.size||DR.viaSize||1.0)/2*this.scale);
      const ir=Math.max(1,(v.drill||DR.viaDrill||0.6)/2*this.scale);
      const sel=this.selectedVia===v;
      const netCol=v.net?this._netCol(v.net):'#88aacc';
      // Selection halo
      if(sel){ctx.strokeStyle='rgba(255,255,255,0.5)';ctx.lineWidth=3;ctx.beginPath();ctx.arc(cx,cy,or+4,0,Math.PI*2);ctx.stroke();}
      // Outer copper ring (net-colored)
      ctx.fillStyle=sel?'#ccddee':netCol; ctx.beginPath();
      ctx.arc(cx,cy,or,0,Math.PI*2); ctx.fill();
      // Border ring for contrast
      ctx.strokeStyle='rgba(255,255,255,0.35)';ctx.lineWidth=1;ctx.beginPath();ctx.arc(cx,cy,or,0,Math.PI*2);ctx.stroke();
      // Drill hole
      ctx.fillStyle='#0a0a0a'; ctx.beginPath();
      ctx.arc(cx,cy,ir,0,Math.PI*2); ctx.fill();
      // Cross-hair inside drill to mark via
      if(or>=4){
        ctx.strokeStyle='rgba(255,255,255,0.3)';ctx.lineWidth=0.5;
        ctx.beginPath();ctx.moveTo(cx-ir*0.6,cy);ctx.lineTo(cx+ir*0.6,cy);ctx.stroke();
        ctx.beginPath();ctx.moveTo(cx,cy-ir*0.6);ctx.lineTo(cx,cy+ir*0.6);ctx.stroke();
      }
      // Net label
      if(v.net&&(sel||this.scale>=10)){
        const fs=Math.max(7,Math.min(11,this.scale*0.6));
        ctx.fillStyle='rgba(255,255,255,0.85)';ctx.font=`bold ${fs}px monospace`;
        ctx.textAlign='center';ctx.fillText(v.net,cx,cy-or-3);ctx.textAlign='left';
      }
    }
  }

  _drawPads(sideFilter){
    const ctx=this.ctx;
    ctx.beginPath(); // reset any stale path from prior draw calls
    for(const comp of(this.board.components||[])){
      const side=comp.layer==='B'?'B':'F';
      if(sideFilter&&side!==sideFilter)continue;
      const rot=(comp.rotation||0)*Math.PI/180;
      for(const pad of(comp.pads||[])){
        const{px,py}=this._padWorld(comp,pad);
        const sx=this.mmX(px),sy=this.mmY(py);
        const pw=Math.max(3,(pad.size_x||1.6)*this.scale);
        const ph=Math.max(3,(pad.size_y||1.6)*this.scale);
        const back=comp.layer==='B';
        const col=pad.net?this._netCol(pad.net):(back?this.layers['B.Cu'].color:this.layers['F.Cu'].color);
        ctx.save(); ctx.translate(sx,sy); ctx.rotate(rot);
        ctx.fillStyle=col; ctx.strokeStyle='rgba(0,0,0,0.5)'; ctx.lineWidth=0.5;
        if(pad.shape==='rect'){ctx.fillRect(-pw/2,-ph/2,pw,ph);ctx.strokeRect(-pw/2,-ph/2,pw,ph);}
        else{ctx.beginPath();ctx.ellipse(0,0,pw/2,ph/2,0,0,Math.PI*2);ctx.fill();ctx.stroke();}
        if(pad.type==='thru_hole'&&pad.drill){
          const dr=Math.max(1,pad.drill/2*this.scale);
          ctx.fillStyle='#0a0a0a'; ctx.beginPath();
          ctx.arc(0,0,dr,0,Math.PI*2); ctx.fill();
        }
        ctx.restore();
        if(this.scale>=14){
          ctx.fillStyle='rgba(255,255,255,0.65)';
          ctx.font=`${Math.max(6,this.scale*0.75)}px monospace`;
          ctx.textAlign='center'; ctx.textBaseline='middle';
          ctx.fillText(pad.number,sx,sy); ctx.textBaseline='alphabetic';
        }
      }
    }
    ctx.textAlign='left';
  }

  /** Lighten a hex color by amount (0–1). Returns hex string. */
  _lightenColor(hex,amt){
    let r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
    r=Math.min(255,Math.round(r+(255-r)*amt));
    g=Math.min(255,Math.round(g+(255-g)*amt));
    b=Math.min(255,Math.round(b+(255-b)*amt));
    return'#'+(r<<16|g<<8|b).toString(16).padStart(6,'0');
  }

  _padWorld(comp,pad){
    const r=(comp.rotation||0)*Math.PI/180;
    return{px:pad.x*Math.cos(r)-pad.y*Math.sin(r)+comp.x,
           py:pad.x*Math.sin(r)+pad.y*Math.cos(r)+comp.y};
  }

  _compBBox(comp){
    if(!comp.pads||!comp.pads.length)return null;
    let mn=[Infinity,Infinity],mx=[-Infinity,-Infinity];
    for(const p of comp.pads){
      const r=(comp.rotation||0)*Math.PI/180;
      const px=p.x*Math.cos(r)-p.y*Math.sin(r),py=p.x*Math.sin(r)+p.y*Math.cos(r);
      const hw=(p.size_x||1.6)/2,hh=(p.size_y||1.6)/2;
      mn[0]=Math.min(mn[0],px-hw);mn[1]=Math.min(mn[1],py-hh);
      mx[0]=Math.max(mx[0],px+hw);mx[1]=Math.max(mx[1],py+hh);
    }
    return isFinite(mn[0])?{x1:mn[0],y1:mn[1],x2:mx[0],y2:mx[1]}:null;
  }

  _drawSilk(side){
    const ctx=this.ctx,lyr=side==='F'?'F.SilkS':'B.SilkS';
    ctx.strokeStyle=this.layers[lyr].color;
    ctx.fillStyle=this.layers[lyr].color;
    ctx.lineWidth=Math.max(0.5,0.12*this.scale); ctx.setLineDash([]);
    for(const c of(this.board.components||[])){
      if((c.layer||'F')!==side)continue;
      const bb=this._compBBox(c); if(!bb)continue;
      const m=0.4;
      const bx=this.mmX(c.x+bb.x1-m),by=this.mmY(c.y+bb.y1-m);
      const bw=(bb.x2-bb.x1+m*2)*this.scale,bh=(bb.y2-bb.y1+m*2)*this.scale;
      ctx.strokeRect(bx,by,bw,bh);
      const fs=Math.max(7,Math.min(11,0.9*this.scale));
      ctx.font=`bold ${fs}px monospace`; ctx.textAlign='center';
      ctx.fillText(c.ref||c.id,bx+bw/2,by-3);
      if(this.scale>=10){
        ctx.globalAlpha=0.5; ctx.font=`${fs-1}px monospace`;
        ctx.fillText(c.value||'',bx+bw/2,by+bh+fs+1); ctx.globalAlpha=1;
      }
      ctx.textAlign='left';
    }
  }

  // Union-Find helpers
  _ufFind(par,i){while(par[i]!==i)par[i]=par[par[i]],i=par[i];return i;}
  _ufUnion(par,a,b){par[this._ufFind(par,a)]=this._ufFind(par,b);}

  // Compute ratsnest: returns [{net,x1,y1,x2,y2}] for all unrouted connections
  computeRatsnest(){
    if(!this.board)return[];
    const netPads=this._collectNetPads();
    const result=[];
    const EPS=0.05; // mm tolerance for matching endpoints to pads

    for(const[net,pads]of Object.entries(netPads)){
      if(pads.length<2)continue;
      const n=pads.length;

      // Build trace connectivity: collect all trace node positions for this net,
      // union them by shared segment endpoints, then map pads onto trace node clusters.
      const traceNodes=[]; // [{x,y}]
      const tpar=[];       // union-find for trace nodes

      const tFind=i=>{while(tpar[i]!==i)tpar[i]=tpar[tpar[i]],i=tpar[i];return i;};
      const tUnion=(a,b)=>{tpar[tFind(a)]=tFind(b);};
      const tIdx={}; // key → index in traceNodes

      const getOrAdd=(x,y)=>{
        const k=`${x.toFixed(4)},${y.toFixed(4)}`;
        if(tIdx[k]===undefined){tIdx[k]=traceNodes.length;traceNodes.push({x,y});tpar.push(traceNodes.length-1);}
        return tIdx[k];
      };

      for(const tr of(this.board.traces||[])){
        if(tr.net!==net)continue;
        for(const seg of(tr.segments||[])){
          const a=getOrAdd(seg.start.x,seg.start.y);
          const b=getOrAdd(seg.end.x,seg.end.y);
          tUnion(a,b);
        }
      }
      // Also add vias on this net as connectors
      for(const v of(this.board.vias||[])){
        if(v.net!==net)continue;
        getOrAdd(v.x,v.y); // just register; vias connect F.Cu to B.Cu, treated as single point
      }

      // Now union pad indices: for each pad, find any trace node within EPS and union them
      // We map pads to a combined union-find (pads 0..n-1, trace nodes n..n+traceNodes.length-1)
      const total=n+traceNodes.length;
      const allPar=Array.from({length:total},(_,i)=>i);
      const aFind=i=>{while(allPar[i]!==i)allPar[i]=allPar[allPar[i]],i=allPar[i];return i;};
      const aUnion=(a,b)=>{allPar[aFind(a)]=aFind(b);};

      // Merge trace node clusters into allPar
      for(let i=0;i<traceNodes.length;i++){
        const root=tFind(i);
        if(root!==i)aUnion(n+i,n+root);
      }

      // Map each pad to trace nodes within EPS
      for(let pi=0;pi<n;pi++){
        for(let ti=0;ti<traceNodes.length;ti++){
          if(Math.hypot(pads[pi].x-traceNodes[ti].x,pads[pi].y-traceNodes[ti].y)<=EPS)
            aUnion(pi,n+ti);
        }
      }

      // Group pad indices by their cluster root (only care about pads 0..n-1)
      const clusters={};
      for(let i=0;i<n;i++){
        const r=aFind(i);
        (clusters[r]||(clusters[r]=[])).push(i);
      }
      const clusterList=Object.values(clusters);
      if(clusterList.length<=1)continue; // all pads already connected

      // Prim's MST over clusters: for each MST edge, pick closest actual pad pair
      const nc=clusterList.length;
      const inMST=new Array(nc).fill(false);
      inMST[0]=true;
      for(let step=0;step<nc-1;step++){
        let bestDist=Infinity,bestA=-1,bestB=-1,bestBCluster=-1;
        for(let a=0;a<nc;a++){
          if(!inMST[a])continue;
          for(let b=0;b<nc;b++){
            if(inMST[b])continue;
            for(const ia of clusterList[a]){
              for(const ib of clusterList[b]){
                const d=Math.hypot(pads[ia].x-pads[ib].x,pads[ia].y-pads[ib].y);
                if(d<bestDist){bestDist=d;bestA=ia;bestB=ib;bestBCluster=b;}
              }
            }
          }
        }
        if(bestBCluster!==-1)inMST[bestBCluster]=true;
        if(bestA!==-1&&bestB!==-1)
          result.push({net,x1:pads[bestA].x,y1:pads[bestA].y,x2:pads[bestB].x,y2:pads[bestB].y});
      }
    }
    return result;
  }

  _drawRatsnest(){
    const ctx=this.ctx;
    const lines=this.computeRatsnest();
    if(!lines.length)return;
    ctx.lineWidth=0.8; ctx.setLineDash([4,3]); ctx.lineCap='round';
    ctx.globalAlpha=0.75;
    for(const ln of lines){
      ctx.strokeStyle=this._ratsnestNetCol(ln.net);
      ctx.beginPath();
      ctx.moveTo(this.mmX(ln.x1),this.mmY(ln.y1));
      ctx.lineTo(this.mmX(ln.x2),this.mmY(ln.y2));
      ctx.stroke();
    }
    ctx.setLineDash([]); ctx.globalAlpha=1; ctx.lineCap='butt';
  }

  // Per-net color for ratsnest (brighter/more saturated than pad colors)
  _ratsnestNetCol(net){
    if(/^(GND|AGND|DGND|PGND)$/.test(net))return'#44ee77';
    if(/^(VCC|VDD|VIN|VBAT|VBUS|3V3|5V|12V|24V|AVCC|DVCC)/.test(net))return'#ff6666';
    let h=0;for(const c of net)h=(h*31+c.charCodeAt(0))&0xffff;
    return['#4fc3f7','#f9a825','#ce93d8','#80cbc4','#ff8a65','#aed581','#ffb74d'][h%7];
  }

  // Snap (ex,ey) so the segment from the last routePoint goes at an allowed angle.
  // DR.routeAngleStep controls: 45 = 8 dirs, 90 = 4 dirs (ortho), 0 = free-form.
  // Also enforces DR.minTraceAngle between consecutive segments.
  _clampRoutePoint(ex,ey){
    const pts=this.routePoints;
    if(pts.length<1)return{x:ex,y:ey};
    const cur=pts[pts.length-1];
    const EPS=0.001;
    const stepDeg=DR.routeAngleStep??45;

    // Snap direction to nearest allowed angle step
    let fdx=ex-cur.x, fdy=ey-cur.y;
    let fl=Math.hypot(fdx,fdy); if(fl<EPS)return{x:ex,y:ey};

    if(stepDeg>0){
      const stepRad=stepDeg*Math.PI/180;
      const ang=Math.atan2(fdy,fdx);
      const snapped=Math.round(ang/stepRad)*stepRad;
      fdx=Math.cos(snapped)*fl; fdy=Math.sin(snapped)*fl;
    }
    // else stepDeg===0: free-form, no snapping

    let rx=cur.x+fdx, ry=cur.y+fdy;

    // For 2+ points, also enforce min angle between consecutive segments
    if(pts.length>=2){
      const prev=pts[pts.length-2];
      const MIN_RAD=(DR.minTraceAngle??90)*Math.PI/180;
      const bx=prev.x-cur.x, by=prev.y-cur.y;
      const bl=Math.hypot(bx,by); if(bl<EPS)return{x:rx,y:ry};
      const bnx=bx/bl, bny=by/bl;
      const nx=rx-cur.x, ny=ry-cur.y;
      const nl=Math.hypot(nx,ny); if(nl<EPS)return{x:rx,y:ry};
      const nnx=nx/nl, nny=ny/nl;
      const dot=bnx*nnx+bny*nny;
      const cross=bnx*nny-bny*nnx;
      const phi=Math.atan2(cross,dot);
      if(Math.abs(phi)<MIN_RAD){
        const clampedPhi=phi>=0?MIN_RAD:-MIN_RAD;
        const cosPhi=Math.cos(clampedPhi), sinPhi=Math.sin(clampedPhi);
        const cdx=bnx*cosPhi-bny*sinPhi;
        const cdy=bnx*sinPhi+bny*cosPhi;
        rx=cur.x+cdx*nl; ry=cur.y+cdy*nl;
      }
    }
    return{x:rx,y:ry};
  }

  _drawActiveRoute(){
    const ctx=this.ctx,pts=this.routePoints;
    const layerCol=this.layers[this.routeLayer]?.color||'#cc6633';
    const w=Math.max(1,parseFloat(document.getElementById('route-width')?.value||0.25)*this.scale);

    // Check what's under the cursor right now (generous snap to help land on pads)
    const mx=this._mxPx,my=this._myPx;
    const _snapR=Math.max(20,(DR.snapRadius||2)*this.scale);
    const hitPad=this.getNearestPad(mx,my,_snapR);
    const hitVia=hitPad?null:this.getNearestVia(mx,my,_snapR);
    const hitArea=(hitPad||hitVia)?null:this.getAreaAt(mx,my);
    const destNet=(hitPad?.pad?.net)||(hitVia?.net)||(hitArea?.net)||null;
    const netConflict=destNet&&this.routeNet&&destNet!==this.routeNet;
    const netMatch=destNet&&(!this.routeNet||destNet===this.routeNet);

    // Snap endpoint to pad/via if net matches; use raw cursor for elbow target
    let ex=this._mx,ey=this._my;
    if(hitPad&&netMatch){ex=hitPad.x;ey=hitPad.y;}

    // Compute elbow path (L-route: two segments via corner point)
    const lastPt=pts[pts.length-1];
    const elbowPts=this._computeElbow(lastPt.x,lastPt.y,ex,ey);

    // Run A* avoidance on each elbow segment, concatenate results
    const _cacheKey=`${lastPt.x.toFixed(2)},${lastPt.y.toFixed(2)},${ex.toFixed(2)},${ey.toFixed(2)},${this.routeNet||''},${this._elbowMode}`;
    let avoidPath;
    if(this._avoidCache&&this._avoidCacheKey===_cacheKey){avoidPath=this._avoidCache;}
    else{
      avoidPath=[elbowPts[0]];
      for(let i=0;i<elbowPts.length-1;i++){
        const seg=this._routeAroundPads(elbowPts[i].x,elbowPts[i].y,elbowPts[i+1].x,elbowPts[i+1].y,this.routeNet||'');
        for(let j=1;j<seg.length;j++)avoidPath.push(seg[j]);
      }
      this._avoidCache=avoidPath;this._avoidCacheKey=_cacheKey;
    }
    // Validate: check if any segment in the avoidance path still crosses a foreign pad
    let pathBlocked=false, blockingPad=null;
    for(let i=0;i<avoidPath.length-1;i++){
      const hit=this._segHitsWrongPad(avoidPath[i].x,avoidPath[i].y,avoidPath[i+1].x,avoidPath[i+1].y,this.routeNet||'');
      if(hit){pathBlocked=true;blockingPad=hit;break;}
    }
    // Validate angles: all segments must be on valid angle steps
    let prevDir=null;
    if(pts.length>=2){
      const p1=pts[pts.length-2],p2=pts[pts.length-1];
      prevDir=Math.atan2(p2.y-p1.y,p2.x-p1.x);
    }
    if(!pathBlocked&&!this._validatePathAngles(avoidPath,prevDir)){pathBlocked=true;}
    // Store for use by click handler (null if blocked)
    this._lastAvoidPath=pathBlocked?null:avoidPath;

    const endPxX=this.mmX(ex),endPxY=this.mmY(ey);
    const col=netConflict||pathBlocked?'#ef4444':netMatch?'#22c55e':layerCol;

    // Draw the trace preview — committed points + avoidance path
    ctx.strokeStyle=col; ctx.lineWidth=w; ctx.lineCap='round'; ctx.setLineDash([3,2]);
    ctx.beginPath();
    ctx.moveTo(this.mmX(pts[0].x),this.mmY(pts[0].y));
    for(let i=1;i<pts.length;i++)ctx.lineTo(this.mmX(pts[i].x),this.mmY(pts[i].y));
    // Draw avoidance waypoints instead of straight line
    for(let i=1;i<avoidPath.length;i++)ctx.lineTo(this.mmX(avoidPath[i].x),this.mmY(avoidPath[i].y));
    ctx.stroke(); ctx.setLineDash([]);

    // Draw small dots on avoidance waypoints (if path was rerouted and valid)
    if(avoidPath.length>2&&!pathBlocked){
      ctx.fillStyle=col+'aa';
      for(let i=1;i<avoidPath.length-1;i++){
        const wx=this.mmX(avoidPath[i].x),wy=this.mmY(avoidPath[i].y);
        ctx.beginPath(); ctx.arc(wx,wy,3,0,Math.PI*2); ctx.fill();
      }
    }

    // Draw red X on the blocking pad
    if(blockingPad){
      const bx=this.mmX(blockingPad.px),by=this.mmY(blockingPad.py);
      const sz=8;
      ctx.strokeStyle='#ef4444'; ctx.lineWidth=3; ctx.lineCap='round';
      ctx.beginPath(); ctx.moveTo(bx-sz,by-sz); ctx.lineTo(bx+sz,by+sz); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(bx+sz,by-sz); ctx.lineTo(bx-sz,by+sz); ctx.stroke();
      ctx.fillStyle='#ef4444'; ctx.font='bold 10px monospace'; ctx.textAlign='left';
      ctx.fillText('✕ '+(blockingPad.pad.net||'?'),bx+sz+4,by-2);
    }

    // Endpoint indicator
    ctx.beginPath();
    ctx.arc(endPxX,endPxY,netMatch?6:4,0,Math.PI*2);
    ctx.strokeStyle=col; ctx.lineWidth=2; ctx.stroke();
    if(netMatch){ctx.fillStyle=col+'44';ctx.fill();}

    // Net label near cursor
    if(this.routeNet){
      ctx.fillStyle=col; ctx.font='bold 10px monospace';
      ctx.textAlign='left';
      ctx.fillText(this.routeNet,endPxX+8,endPxY-4);
    }
    if(netConflict){
      ctx.fillStyle='#ef4444'; ctx.font='bold 10px monospace';
      ctx.textAlign='left';
      ctx.fillText('✕ '+destNet,endPxX+8,endPxY+10);
    }
  }

  /** Draw a snap indicator ring + crosshair on the nearest pad/via when in route mode. */
  _drawSnapIndicator(){
    if(this._mxPx==null)return;
    const mx=this._mxPx,my=this._myPx;
    // Generous snap: at least 30px or 2mm in screen pixels
    const snapR=Math.max(20,(DR.snapRadius||2)*this.scale);
    const hit=this.getNearestPad(mx,my,snapR);
    const hv=hit?null:this.getNearestVia(mx,my,snapR);
    if(!hit&&!hv)return;
    const ctx=this.ctx;
    const px=hit?this.mmX(hit.x):this.mmX(hv.x);
    const py=hit?this.mmY(hit.y):this.mmY(hv.y);
    const netLabel=hit?(hit.pad.net||''):(hv.net||'');
    // Animated pulsing ring
    const t=Date.now()%1000/1000;
    const pulse=8+3*Math.sin(t*Math.PI*2);
    // Outer glow
    ctx.strokeStyle='rgba(96,165,250,0.3)'; ctx.lineWidth=4;
    ctx.beginPath();ctx.arc(px,py,pulse+2,0,Math.PI*2);ctx.stroke();
    // Main ring
    ctx.strokeStyle='rgba(96,165,250,0.9)'; ctx.lineWidth=2;
    ctx.beginPath();ctx.arc(px,py,pulse,0,Math.PI*2);ctx.stroke();
    // Crosshair
    const ch=pulse+5;
    ctx.strokeStyle='rgba(96,165,250,0.6)'; ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(px-ch,py);ctx.lineTo(px-pulse-1,py);ctx.stroke();
    ctx.beginPath();ctx.moveTo(px+pulse+1,py);ctx.lineTo(px+ch,py);ctx.stroke();
    ctx.beginPath();ctx.moveTo(px,py-ch);ctx.lineTo(px,py-pulse-1);ctx.stroke();
    ctx.beginPath();ctx.moveTo(px,py+pulse+1);ctx.lineTo(px,py+ch);ctx.stroke();
    // Center dot
    ctx.fillStyle='rgba(96,165,250,0.9)';
    ctx.beginPath();ctx.arc(px,py,2.5,0,Math.PI*2);ctx.fill();
    // Net label
    if(netLabel){
      ctx.fillStyle='rgba(96,165,250,0.95)'; ctx.font='bold 10px monospace'; ctx.textAlign='left';
      ctx.fillText(netLabel,px+pulse+6,py-2);
      ctx.textAlign='left';
    }
    // Request next frame for pulse animation
    if(!this._snapAnimFrame){
      this._snapAnimFrame=requestAnimationFrame(()=>{
        this._snapAnimFrame=null;
        if(this.tool==='route')this.render();
      });
    }
  }

  _drawActiveZone(){
    const ctx=this.ctx,pts=this.zonePoints;
    ctx.strokeStyle='#aaaaaa'; ctx.lineWidth=1; ctx.setLineDash([3,2]);
    ctx.beginPath();
    ctx.moveTo(this.mmX(pts[0].x),this.mmY(pts[0].y));
    for(let i=1;i<pts.length;i++)ctx.lineTo(this.mmX(pts[i].x),this.mmY(pts[i].y));
    ctx.lineTo(this.mmX(this._mx),this.mmY(this._my));
    ctx.stroke(); ctx.setLineDash([]);
  }

  _drawAreas(){
    const ctx=this.ctx;
    for(const a of(this.board?.areas||[])){
      const cl=a.clearance!=null?a.clearance:(DR.clearance||0.2);
      // Skip outline-based areas (handled by _drawZones after load normalization)
      if(a.outline||a.x1==null||a.x2==null)continue;
      const lyr=a.layer||'F.Cu';
      if(!this.layers[lyr]?.visible)continue;
      const col=this.layers[lyr].color;
      const x1=Math.min(a.x1,a.x2),y1=Math.min(a.y1,a.y2);
      const x2=Math.max(a.x1,a.x2),y2=Math.max(a.y1,a.y2);
      const sx=this.mmX(x1),sy=this.mmY(y1),ex=this.mmX(x2),ey=this.mmY(y2);
      const w=ex-sx,h=ey-sy;
      const sel=this.selectedArea===a;
      // Offscreen canvas: fill rectangle, then destination-out clearances.
      // Fixes evenodd "phantom copper" when two clearance shapes overlap.
      const margin=Math.ceil((cl+2)*this.scale)+2;
      const bx1=Math.floor(sx)-margin,by1=Math.floor(sy)-margin;
      const bx2=Math.ceil(ex)+margin,by2=Math.ceil(ey)+margin;
      const bw=Math.max(1,bx2-bx1),bh=Math.max(1,by2-by1);
      const off=document.createElement('canvas');
      off.width=bw; off.height=bh;
      const oc=off.getContext('2d');
      oc.translate(-bx1,-by1);
      // Step 1: fill area rectangle
      oc.fillStyle=col+(sel?'70':'40');
      oc.fillRect(sx,sy,w,h);
      // Step 2: cut clearance holes
      oc.globalCompositeOperation='destination-out';
      oc.fillStyle='rgba(0,0,0,1)';
      oc.beginPath();
      // — Pad clearances (only pads on the same layer as this area) —
      const areaIsF=lyr.startsWith('F');
      for(const c of(this.board?.components||[])){
        const compIsF=(c.layer||'F')!=='B';
        const rot=(c.rotation||0)*Math.PI/180;
        const cosR=Math.cos(rot),sinR=Math.sin(rot);
        for(const p of(c.pads||[])){
          if(p.net&&p.net===a.net)continue;
          // Skip pads on a different layer (through-hole pads exist on both)
          const isThru=p.type==='thru_hole';
          if(!isThru&&compIsF!==areaIsF)continue;
          const{px,py}=this._padWorld(c,p);
          const hpx=(p.size_x||1.6)/2,hpy=(p.size_y||1.6)/2;
          const maxhp=Math.max(hpx,hpy);
          if(px<x1-maxhp-cl||px>x2+maxhp+cl||py<y1-maxhp-cl||py>y2+maxhp+cl)continue;
          const psx=this.mmX(px),psy=this.mmY(py);
          if(p.shape==='rect'||p.shape==='square'){
            const hw=(hpx+cl)*this.scale,hh=(hpy+cl)*this.scale;
            const c0x=psx+(-hw)*cosR-(-hh)*sinR, c0y=psy+(-hw)*sinR+(-hh)*cosR;
            const c1x=psx+(+hw)*cosR-(-hh)*sinR, c1y=psy+(+hw)*sinR+(-hh)*cosR;
            const c2x=psx+(+hw)*cosR-(+hh)*sinR, c2y=psy+(+hw)*sinR+(+hh)*cosR;
            const c3x=psx+(-hw)*cosR-(+hh)*sinR, c3y=psy+(-hw)*sinR+(+hh)*cosR;
            oc.moveTo(c0x,c0y);
            oc.lineTo(c1x,c1y);
            oc.lineTo(c2x,c2y);
            oc.lineTo(c3x,c3y);
            oc.closePath();
          }else{
            const r=(maxhp+cl)*this.scale;
            oc.moveTo(psx+r,psy);
            oc.arc(psx,psy,r,0,Math.PI*2);
          }
        }
      }
      // — Via clearances (vias span all layers, always apply) —
      for(const v of(this.board?.vias||[])){
        if(v.net&&v.net===a.net)continue;
        if(v.x<x1-cl||v.x>x2+cl||v.y<y1-cl||v.y>y2+cl)continue;
        const r=((v.size||DR.viaSize||1.0)/2+cl)*this.scale;
        const vsx=this.mmX(v.x),vsy=this.mmY(v.y);
        oc.moveTo(vsx+r,vsy);
        oc.arc(vsx,vsy,r,0,Math.PI*2);
      }
      oc.fill();
      // — Trace clearances (only traces on the same layer) —
      oc.strokeStyle='rgba(0,0,0,1)';
      oc.lineCap='round';
      for(const tr of(this.board?.traces||[])){
        if(tr.net&&tr.net===a.net)continue;
        // Skip traces on different layer
        const trIsF=(tr.layer||'F.Cu').startsWith('F');
        if(trIsF!==areaIsF)continue;
        const tw=(tr.width||tr.width_mm||DR.traceWidth||0.25)+cl*2;
        oc.lineWidth=tw*this.scale;
        for(const seg of(tr.segments||[])){
          if(!seg||!seg.start||!seg.end)continue;
          oc.beginPath();
          oc.moveTo(this.mmX(seg.start.x),this.mmY(seg.start.y));
          oc.lineTo(this.mmX(seg.end.x),this.mmY(seg.end.y));
          oc.stroke();
        }
      }
      // Step 3: composite onto main canvas
      ctx.drawImage(off,bx1,by1);
      // Border
      ctx.strokeStyle=col+(sel?'ff':'99');
      ctx.lineWidth=sel?2:1;
      ctx.setLineDash(sel?[]:[4,2]);
      ctx.strokeRect(sx,sy,w,h);
      ctx.setLineDash([]);
      // Net label
      if(this.scale>=5&&w>20&&h>12){
        ctx.fillStyle=col+'cc';ctx.font=`bold ${Math.max(9,Math.min(12,w/6))}px monospace`;
        ctx.textAlign='center';
        ctx.fillText(a.net||'?',sx+w/2,sy+h/2+4);
        ctx.textAlign='left';
      }
    }
  }

  _drawActiveArea(){
    if(!this.areaStart)return;
    const ctx=this.ctx;
    const lyr=this.areaLayer||'F.Cu';
    const col=this.layers[lyr]?.color||'#cc6633';
    const sx=this.mmX(this.areaStart.x),sy=this.mmY(this.areaStart.y);
    const ex=this.mmX(this._mx),ey=this.mmY(this._my);
    const rx=Math.min(sx,ex),ry=Math.min(sy,ey),rw=Math.abs(ex-sx),rh=Math.abs(ey-sy);
    ctx.fillStyle=col+'22';ctx.strokeStyle=col+'bb';
    ctx.lineWidth=1;ctx.setLineDash([4,2]);
    ctx.fillRect(rx,ry,rw,rh);
    ctx.strokeRect(rx,ry,rw,rh);
    ctx.setLineDash([]);
  }

  /** Build a list of obstacle rectangles (foreign-net pads expanded by trace half-width + clearance). */
  _getObstacles(net){
    const tw=parseFloat(document.getElementById('route-width')?.value||DR.traceWidth)||0.25;
    const cl=DR.clearance||0.2;
    const margin=tw/2+cl;
    const obs=[];
    for(const c of(this.board?.components||[])){
      for(const p of(c.pads||[])){
        if(!p.net||p.net===net)continue;
        const{px,py}=this._padWorld(c,p);
        const hx=(p.size_x||1.6)/2+margin;
        const hy=(p.size_y||1.6)/2+margin;
        obs.push({x:px,y:py,hx,hy,pad:p,comp:c});
      }
    }
    return obs;
  }

  /** Snap an angle to the nearest allowed step (45°, 90°, or free). */
  _snapAngle(ang){
    const stepDeg=DR.routeAngleStep??45;
    if(stepDeg<=0)return ang;
    const stepRad=stepDeg*Math.PI/180;
    return Math.round(ang/stepRad)*stepRad;
  }

  /** Compute an L-route (elbow) from S to C using two segments at allowed angles.
   *  Returns [{x,y}, {x,y}, {x,y}] (start, elbow, end) or just [start,end] if direct.
   *  Toggle this._elbowMode with '/' to switch between H-first and V-first. */
  _computeElbow(sx,sy,ex,ey){
    const stepDeg=DR.routeAngleStep??45;
    const dx=ex-sx,dy=ey-sy;
    const dist=Math.hypot(dx,dy);
    if(dist<0.01)return[{x:sx,y:sy},{x:ex,y:ey}];
    if(stepDeg===0)return[{x:sx,y:sy},{x:ex,y:ey}]; // free-form: direct

    // Snap the overall direction to nearest allowed angle
    const rawAng=Math.atan2(dy,dx);
    const snappedAng=this._snapAngle(rawAng);

    // Check if cursor is already on-axis (single segment suffices)
    const angDiff=Math.abs(rawAng-snappedAng);
    if(angDiff<0.01)return[{x:sx,y:sy},{x:sx+Math.cos(snappedAng)*dist,y:sy+Math.sin(snappedAng)*dist}];

    if(stepDeg>=90){
      // Orthogonal: H then V, or V then H
      if(this._elbowMode===0)return[{x:sx,y:sy},{x:ex,y:sy},{x:ex,y:ey}];
      else return[{x:sx,y:sy},{x:sx,y:ey},{x:ex,y:ey}];
    }

    // 45° mode: diagonal + straight or straight + diagonal
    const adx=Math.abs(dx),ady=Math.abs(dy);
    if(Math.abs(adx-ady)<0.01)return[{x:sx,y:sy},{x:ex,y:ey}]; // pure 45° diagonal
    const diagLen=Math.min(adx,ady);
    const diagDx=Math.sign(dx)*diagLen,diagDy=Math.sign(dy)*diagLen;

    if(this._elbowMode===0){
      // Diagonal first, then straight
      return[{x:sx,y:sy},{x:sx+diagDx,y:sy+diagDy},{x:ex,y:ey}];
    } else {
      // Straight first, then diagonal
      const remDx=dx-diagDx,remDy=dy-diagDy;
      return[{x:sx,y:sy},{x:sx+remDx,y:sy+remDy},{x:ex,y:ey}];
    }
  }

  /** Check all angles in a path respect the minimum trace angle and step constraints.
   *  prevDir is the direction (radians) of the last committed segment, or null.
   *  Returns true if all angles are valid. */
  _validatePathAngles(path,prevDir){
    const stepDeg=DR.routeAngleStep??45;
    if(stepDeg<=0)return true; // free-form
    const stepRad=stepDeg*Math.PI/180;
    const minAng=(DR.minTraceAngle??stepDeg)*Math.PI/180;
    const EPS=0.02; // ~1° tolerance

    for(let i=0;i<path.length-1;i++){
      const dx=path[i+1].x-path[i].x, dy=path[i+1].y-path[i].y;
      const len=Math.hypot(dx,dy);
      if(len<0.01)continue;
      const ang=Math.atan2(dy,dx);

      // Check segment angle is on a valid step
      const snapped=this._snapAngle(ang);
      if(Math.abs(ang-snapped)>EPS)return false;

      // Check angle between consecutive segments
      if(i>0||prevDir!==null){
        const prev=i>0?Math.atan2(path[i].y-path[i-1].y,path[i].x-path[i-1].x):prevDir;
        if(prev!==null){
          // Angle between incoming and outgoing at this vertex
          let turn=Math.abs(ang-prev);
          if(turn>Math.PI)turn=2*Math.PI-turn;
          // turn is the exterior angle; the interior angle is PI - turn
          // We want the interior angle (bend angle) >= minAng
          // A U-turn (turn=PI) means interior=0 which is invalid
          // Straight (turn=0) means interior=PI which is fine
          if(turn>0.01 && (Math.PI-turn)<minAng-EPS)return false;
        }
      }
    }
    return true;
  }

  /** A* grid pathfinder: returns waypoints [start, ...intermediate, end] that
   *  avoid all foreign-net pads.  Uses a coarse grid for performance during
   *  live mouse-move preview. */
  _routeAroundPads(sx,sy,ex,ey,net){
    const GRID=DR.routingGrid||0.25;
    const obs=this._getObstacles(net);

    // Quick check: direct path clear?
    if(!this._segHitsWrongPad(sx,sy,ex,ey,net))return[{x:sx,y:sy},{x:ex,y:ey}];

    // Build bounding box with margin
    const margin=8*GRID;
    const minX=Math.min(sx,ex)-margin, maxX=Math.max(sx,ex)+margin;
    const minY=Math.min(sy,ey)-margin, maxY=Math.max(sy,ey)+margin;
    const cols=Math.ceil((maxX-minX)/GRID)+1;
    const rows=Math.ceil((maxY-minY)/GRID)+1;
    if(cols*rows>80000)return[{x:sx,y:sy},{x:ex,y:ey}]; // too large, skip

    // Build blocked cell set
    const blocked=new Set();
    for(const o of obs){
      const gx0=Math.floor((o.x-o.hx-minX)/GRID);
      const gx1=Math.ceil((o.x+o.hx-minX)/GRID);
      const gy0=Math.floor((o.y-o.hy-minY)/GRID);
      const gy1=Math.ceil((o.y+o.hy-minY)/GRID);
      for(let gx=Math.max(0,gx0);gx<=Math.min(cols-1,gx1);gx++)
        for(let gy=Math.max(0,gy0);gy<=Math.min(rows-1,gy1);gy++)
          blocked.add(gx+gy*cols);
    }

    const sg=Math.round((sx-minX)/GRID), sr=Math.round((sy-minY)/GRID);
    const eg=Math.round((ex-minX)/GRID), er=Math.round((ey-minY)/GRID);
    const startK=sg+sr*cols, endK=eg+er*cols;

    // Unblock start and end cells
    blocked.delete(startK); blocked.delete(endK);

    // Directions based on angle step
    const stepDeg=DR.routeAngleStep??45;
    let dirs;
    if(stepDeg>=90)dirs=[[1,0],[-1,0],[0,1],[0,-1]];
    else dirs=[[1,0],[-1,0],[0,1],[0,-1],[1,1],[1,-1],[-1,1],[-1,-1]];
    const SQRT2=1.414;

    // A* with Manhattan/Chebyshev heuristic
    const gScore=new Map(); gScore.set(startK,0);
    const came=new Map();
    const heur=(k)=>{const c=k%cols,r=(k-c)/cols;const dx=Math.abs(c-eg),dy=Math.abs(r-er);return stepDeg>=90?(dx+dy)*GRID:Math.max(dx,dy)*GRID;};
    // Min-heap using array (good enough for <80k cells)
    const open=[[heur(startK),startK]];
    const closed=new Set();
    let found=false;

    while(open.length>0){
      // Pop min
      let mi=0;for(let i=1;i<open.length;i++)if(open[i][0]<open[mi][0])mi=i;
      const[,cur]=open[mi]; open[mi]=open[open.length-1]; open.pop();
      if(cur===endK){found=true;break;}
      if(closed.has(cur))continue;
      closed.add(cur);
      const cc=cur%cols,cr=(cur-cc)/cols;
      const cg=gScore.get(cur);

      for(const[dx,dy]of dirs){
        const nc=cc+dx,nr=cr+dy;
        if(nc<0||nc>=cols||nr<0||nr>=rows)continue;
        const nk=nc+nr*cols;
        if(blocked.has(nk)||closed.has(nk))continue;
        const cost=(dx!==0&&dy!==0)?SQRT2:1;
        const ng=cg+cost;
        if(!gScore.has(nk)||ng<gScore.get(nk)){
          gScore.set(nk,ng);
          came.set(nk,cur);
          open.push([ng+heur(nk),nk]);
        }
      }
    }

    if(!found)return[{x:sx,y:sy},{x:ex,y:ey}]; // no path

    // Reconstruct
    const path=[];
    let k=endK;
    while(k!==undefined){path.push(k);k=came.get(k);}
    path.reverse();

    // Convert grid to world coords and simplify (remove collinear points)
    const raw=path.map(k=>{const c=k%cols,r=(k-c)/cols;return{x:minX+c*GRID,y:minY+r*GRID};});
    // Force exact start/end
    raw[0]={x:sx,y:sy}; raw[raw.length-1]={x:ex,y:ey};
    // Remove collinear intermediate points
    if(raw.length<=2)return raw;
    const simplified=[raw[0]];
    for(let i=1;i<raw.length-1;i++){
      const p=simplified[simplified.length-1],c=raw[i],n=raw[i+1];
      const dx1=c.x-p.x,dy1=c.y-p.y,dx2=n.x-c.x,dy2=n.y-c.y;
      if(Math.abs(dx1*dy2-dy1*dx2)>0.001)simplified.push(c); // not collinear
    }
    simplified.push(raw[raw.length-1]);
    return simplified;
  }

  /** Check if a single segment (sx,sy)->(ex,ey) crosses any pad not on `net`.
   *  Returns {pad, comp, px, py} or null. Uses trace width + DR clearance. */
  _segHitsWrongPad(sx,sy,ex,ey,net){
    const dx=ex-sx,dy=ey-sy,len2=dx*dx+dy*dy;
    const tw=parseFloat(document.getElementById('route-width')?.value||DR.traceWidth)||0.25;
    const cl=DR.clearance||0.2;
    for(const c of(this.board?.components||[])){
      for(const p of(c.pads||[])){
        // Skip pads on the same net; block ALL other pads (including unassigned)
        if(p.net&&net&&p.net===net)continue;
        const{px,py}=this._padWorld(c,p);
        // Expand pad by half-trace-width + clearance
        const hpx=(p.size_x||1.6)/2+tw/2+cl;
        const hpy=(p.size_y||1.6)/2+tw/2+cl;
        let t=len2>0?Math.max(0,Math.min(1,((px-sx)*dx+(py-sy)*dy)/len2)):0;
        const cx2=sx+t*dx,cy2=sy+t*dy;
        if(Math.abs(cx2-px)<hpx&&Math.abs(cy2-py)<hpy)
          return{pad:p,comp:c,px,py};
      }
    }
    return null;
  }

  /** Check all segments of a point list. Returns first blocking pad or null. */
  _traceHitsWrongNet(pts,net){
    for(let i=0;i<pts.length-1;i++){
      const hit=this._segHitsWrongPad(pts[i].x,pts[i].y,pts[i+1].x,pts[i+1].y,net);
      if(hit)return hit.pad;
    }
    return null;
  }

  _drawMeasure(){
    const ctx=this.ctx,s=this.measureStart;
    const ex=this._mx,ey=this._my;
    const d=Math.hypot(ex-s.x,ey-s.y);
    ctx.strokeStyle='#ffff00'; ctx.lineWidth=1; ctx.setLineDash([4,2]);
    ctx.beginPath();
    ctx.moveTo(this.mmX(s.x),this.mmY(s.y));
    ctx.lineTo(this.mmX(ex),this.mmY(ey));
    ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle='#ffff00'; ctx.font='12px monospace';
    ctx.fillText(`${d.toFixed(3)}mm`,this.mmX((s.x+ex)/2)+4,this.mmY((s.y+ey)/2)-4);
  }

  _startTraceDrag(trace,segIdx,mx,my){
    const seg=trace.segments[segIdx];
    this._isDragTrace=true;
    this._dragTrace=trace;
    this._dragTraceSegIdx=segIdx;
    this._dragTraceOrigSeg={start:{x:seg.start.x,y:seg.start.y},end:{x:seg.end.x,y:seg.end.y}};
    this._dragTraceOrigAll=trace.segments.map(s=>({start:{x:s.start.x,y:s.start.y},end:{x:s.end.x,y:s.end.y}}));
    const midX=(seg.start.x+seg.end.x)/2,midY=(seg.start.y+seg.end.y)/2;
    this._dragTraceOff={x:this.cX(mx)-midX,y:this.cY(my)-midY};
    this._traceDragViolations=[];
  }

  _checkTraceClearance(tr,segIdx){
    const violations=[];
    const seg=tr.segments[segIdx];
    const hw=(tr.width||0.25)/2;
    const cl=DR.clearance;
    function ptSeg(px,py,ax,ay,bx,by){
      const dx=bx-ax,dy=by-ay,l2=dx*dx+dy*dy;
      if(l2<1e-10)return Math.hypot(px-ax,py-ay);
      const t=Math.max(0,Math.min(1,((px-ax)*dx+(py-ay)*dy)/l2));
      return Math.hypot(px-(ax+t*dx),py-(ay+t*dy));
    }
    for(const other of(this.board.traces||[])){
      if(other===tr||other.net===tr.net)continue;
      const ohw=(other.width||0.25)/2;
      for(const oseg of(other.segments||[])){
        const d=Math.min(
          ptSeg(oseg.start.x,oseg.start.y,seg.start.x,seg.start.y,seg.end.x,seg.end.y),
          ptSeg(oseg.end.x,oseg.end.y,seg.start.x,seg.start.y,seg.end.x,seg.end.y),
          ptSeg(seg.start.x,seg.start.y,oseg.start.x,oseg.start.y,oseg.end.x,oseg.end.y),
          ptSeg(seg.end.x,seg.end.y,oseg.start.x,oseg.start.y,oseg.end.x,oseg.end.y)
        )-hw-ohw;
        if(d<cl) violations.push({ax:oseg.start.x,ay:oseg.start.y,bx:oseg.end.x,by:oseg.end.y,w:(ohw*2),isPad:false});
      }
    }
    for(const comp of(this.board.components||[])){
      for(const pad of(comp.pads||[])){
        if(pad.net===tr.net)continue;
        const{px,py}=this._padWorld(comp,pad);
        const padR=Math.max(pad.size_x||1.6,pad.size_y||1.6)/2;
        const d=ptSeg(px,py,seg.start.x,seg.start.y,seg.end.x,seg.end.y)-hw-padR;
        if(d<cl) violations.push({px,py,padR,isPad:true});
      }
    }
    return violations;
  }

  // Returns true if placing the dragged segment at (nsx,nsy)→(nex,ney) keeps
  // all junction angles with rubber-banded neighbours ≥ DR.minTraceAngle (default 90°).
  _checkTraceAngles(tr,si,nsx,nsy,nex,ney){
    const EPS=0.001,MIN_RAD=(DR.minTraceAngle??90)*Math.PI/180;
    const orig=this._dragTraceOrigSeg,origAll=this._dragTraceOrigAll;
    // Returns true if the opening angle between two "away" vectors is >= minTraceAngle
    const ok=(ax,ay,bx,by)=>{
      const la=Math.hypot(ax,ay),lb=Math.hypot(bx,by);
      if(la<EPS*10||lb<EPS*10)return true;
      return Math.acos(Math.max(-1,Math.min(1,(ax*bx+ay*by)/(la*lb))))>=MIN_RAD;
    };
    for(let i=0;i<tr.segments.length;i++){
      if(i===si)continue;
      const o=origAll[i];
      // Compute rubber-banded position of this adjacent segment
      let sx=o.start.x,sy=o.start.y,ex=o.end.x,ey=o.end.y;
      if(Math.abs(o.end.x-orig.start.x)<EPS&&Math.abs(o.end.y-orig.start.y)<EPS){ex=nsx;ey=nsy;}
      if(Math.abs(o.start.x-orig.start.x)<EPS&&Math.abs(o.start.y-orig.start.y)<EPS){sx=nsx;sy=nsy;}
      if(Math.abs(o.end.x-orig.end.x)<EPS&&Math.abs(o.end.y-orig.end.y)<EPS){ex=nex;ey=ney;}
      if(Math.abs(o.start.x-orig.end.x)<EPS&&Math.abs(o.start.y-orig.end.y)<EPS){sx=nex;sy=ney;}
      // adj.end → junction(nsx,nsy) → drag→nex,ney
      // away vectors: (sx-nsx,sy-nsy) and (nex-nsx,ney-nsy)
      if(Math.abs(o.end.x-orig.start.x)<EPS&&Math.abs(o.end.y-orig.start.y)<EPS)
        if(!ok(sx-nsx,sy-nsy,nex-nsx,ney-nsy))return false;
      // drag(nsx→nex) → junction(nex,ney) ← adj.start: (nsx-nex,nsy-ney) and (ex-nex,ey-ney)
      if(Math.abs(o.start.x-orig.end.x)<EPS&&Math.abs(o.start.y-orig.end.y)<EPS)
        if(!ok(nsx-nex,nsy-ney,ex-nex,ey-ney))return false;
      // Both adj.start and drag.start at same junction: away = (nex-nsx,ney-nsy) and (ex-nsx,ey-nsy)
      if(Math.abs(o.start.x-orig.start.x)<EPS&&Math.abs(o.start.y-orig.start.y)<EPS)
        if(!ok(nex-nsx,ney-nsy,ex-nsx,ey-nsy))return false;
      // Both adj.end and drag.end at same junction: away = (sx-nex,sy-ney) and (nsx-nex,nsy-ney)
      if(Math.abs(o.end.x-orig.end.x)<EPS&&Math.abs(o.end.y-orig.end.y)<EPS)
        if(!ok(sx-nex,sy-ney,nsx-nex,nsy-ney))return false;
    }
    return true;
  }

  _startDrag(primaryComp,mx,my){
    this._isDrag=true; this._dragC=primaryComp;
    this._dragOff={x:this.cX(mx)-primaryComp.x,y:this.cY(my)-primaryComp.y};
    // Snapshot offsets for all selected comps relative to primary
    this._dragCompOffsets=(this.selectedComps.length>1?this.selectedComps:[primaryComp]).map(c=>({
      comp:c, ox:c.x-primaryComp.x, oy:c.y-primaryComp.y
    }));
    // Snapshot pad world positions (all selected comps)
    this._dragPadSnap=[];
    for(const{comp}of this._dragCompOffsets)
      for(const p of(comp.pads||[])){
        const{px,py}=this._padWorld(comp,p);
        this._dragPadSnap.push({comp,px,py,number:p.number});
      }
  }

  // Rotate all selected components as a group by `deg` degrees CW around their collective centroid.
  // Also rotates grouped traces and vias with the same groupId(s).
  // Falls back to rotating just selectedComp if no multi-selection exists.
  rotateSelGroup(deg=90){
    const comps=this.selectedComps.length>1
      ? this.selectedComps
      : (this.selectedComp ? [this.selectedComp] : []);
    if(!comps.length)return;
    const rad=deg*Math.PI/180;
    const cos=Math.cos(rad), sin=Math.sin(rad);
    const rotPt=(px,py,cx,cy)=>{
      const dx=px-cx, dy=py-cy;
      return {x:cx+(dx*cos-dy*sin), y:cy+(dx*sin+dy*cos)};
    };
    // Mutate first, then snapshot — ensures the embedded leBoardChanged
    // notification (fired inside _snapshot) carries the post-rotation board.
    if(comps.length===1){
      // Single component — rotate in place, plus rotate grouped traces/vias around it
      const c=comps[0];
      const cx=c.x, cy=c.y;
      c.rotation=((c.rotation||0)+deg)%360;
      // Rotate grouped traces/vias around this component's center
      const grpId=c.groupId;
      if(grpId){
        for(const tr of(this.board.traces||[])){
          if(tr.groupId!==grpId)continue;
          for(const seg of(tr.segments||[])){
            const s=rotPt(seg.start.x,seg.start.y,cx,cy);
            const e=rotPt(seg.end.x,seg.end.y,cx,cy);
            seg.start.x=s.x;seg.start.y=s.y;
            seg.end.x=e.x;seg.end.y=e.y;
          }
        }
        for(const v of(this.board.vias||[])){
          if(v.groupId!==grpId)continue;
          const p=rotPt(v.x,v.y,cx,cy);
          v.x=p.x;v.y=p.y;
        }
      }
    } else {
      // Multi-selection — rotate positions around the group centroid
      const cx=comps.reduce((s,c)=>s+c.x,0)/comps.length;
      const cy=comps.reduce((s,c)=>s+c.y,0)/comps.length;
      // Collect all groupIds from the selected components
      const grpIds=new Set(comps.filter(c=>c.groupId).map(c=>c.groupId));
      for(const c of comps){
        const p=rotPt(c.x,c.y,cx,cy);
        c.x=p.x;c.y=p.y;
        c.rotation=((c.rotation||0)+deg)%360;
      }
      // Rotate traces and vias that belong to any of the selected groups
      if(grpIds.size){
        for(const tr of(this.board.traces||[])){
          if(!grpIds.has(tr.groupId))continue;
          for(const seg of(tr.segments||[])){
            const s=rotPt(seg.start.x,seg.start.y,cx,cy);
            const e=rotPt(seg.end.x,seg.end.y,cx,cy);
            seg.start.x=s.x;seg.start.y=s.y;
            seg.end.x=e.x;seg.end.y=e.y;
          }
        }
        for(const v of(this.board.vias||[])){
          if(!grpIds.has(v.groupId))continue;
          const p=rotPt(v.x,v.y,cx,cy);
          v.x=p.x;v.y=p.y;
        }
      }
    }
    this._snapshot();
    this.render();
    if(typeof updateInfoPanel==='function')updateInfoPanel();
  }

  _drawSel(comp){
    const bb=this._compBBox(comp); if(!bb)return;
    const m=1,ctx=this.ctx;
    ctx.strokeStyle='#6c63ff'; ctx.lineWidth=2; ctx.setLineDash([4,2]);
    ctx.strokeRect(this.mmX(comp.x+bb.x1-m),this.mmY(comp.y+bb.y1-m),(bb.x2-bb.x1+m*2)*this.scale,(bb.y2-bb.y1+m*2)*this.scale);
    ctx.setLineDash([]);
  }

  _drawHoverComp(comp){
    const bb=this._compBBox(comp); if(!bb)return;
    const m=1.2,ctx=this.ctx;
    ctx.strokeStyle='rgba(180,180,255,0.5)'; ctx.lineWidth=1.5; ctx.setLineDash([]);
    ctx.strokeRect(this.mmX(comp.x+bb.x1-m),this.mmY(comp.y+bb.y1-m),(bb.x2-bb.x1+m*2)*this.scale,(bb.y2-bb.y1+m*2)*this.scale);
    ctx.fillStyle='rgba(108,99,255,0.06)';
    ctx.fillRect(this.mmX(comp.x+bb.x1-m),this.mmY(comp.y+bb.y1-m),(bb.x2-bb.x1+m*2)*this.scale,(bb.y2-bb.y1+m*2)*this.scale);
  }

  // Remove group membership for all currently selected components
  ungroupSelected(){
    if(!this.board?.groups)return;
    const selIds=new Set(this.selectedComps.map(c=>c.id));
    if(!selIds.size)return;
    const affectedGrpIds=new Set(
      this.selectedComps.filter(c=>c.groupId).map(c=>c.groupId)
    );
    if(!affectedGrpIds.size)return;
    this._snapshot();
    for(const c of(this.board.components||[]))
      if(affectedGrpIds.has(c.groupId)&&selIds.has(c.id)) delete c.groupId;
    // Also remove groupId from traces and vias that belonged to affected groups
    for(const tr of(this.board.traces||[])) if(affectedGrpIds.has(tr.groupId)) delete tr.groupId;
    for(const v of(this.board.vias||[])) if(affectedGrpIds.has(v.groupId)) delete v.groupId;
    this.board.groups=this.board.groups.filter(g=>{
      if(!affectedGrpIds.has(g.id))return true;
      g.members=g.members.filter(id=>!selIds.has(id));
      return g.members.length>=2;
    });
    this.render();
  }

  groupSelected(name){
    if(!this.board)return;
    const comps=this.selectedComps.length>=2?this.selectedComps:(this.selectedComp?[this.selectedComp]:[]);
    if(comps.length<2){alert('Select at least 2 components to group.');return;}
    this._snapshot();
    const gid='grp_'+Math.random().toString(36).slice(2,9);
    const gname=name||('Group '+(((this.board.groups||[]).length)+1));
    this.board.groups=this.board.groups||[];
    // Remove comps from any previous group first
    const memberIds=comps.map(c=>c.id);
    for(const c of comps) delete c.groupId;
    this.board.groups=this.board.groups.filter(g=>{
      g.members=g.members.filter(id=>!memberIds.includes(id));
      return g.members.length>=2;
    });
    for(const c of comps) c.groupId=gid;
    this.board.groups.push({id:gid,name:gname,members:memberIds});
    // Auto-assign groupId to traces/vias that are geometrically inside the group bounding box
    {
      let bx1=Infinity,by1=Infinity,bx2=-Infinity,by2=-Infinity;
      for(const c of comps){
        const bb=this._compBBox(c);if(!bb)continue;
        bx1=Math.min(bx1,c.x+bb.x1);by1=Math.min(by1,c.y+bb.y1);
        bx2=Math.max(bx2,c.x+bb.x2);by2=Math.max(by2,c.y+bb.y2);
      }
      if(isFinite(bx1)){
        const m=2; // 2 mm margin
        bx1-=m;by1-=m;bx2+=m;by2+=m;
        for(const tr of(this.board.traces||[])){
          if(tr.groupId)continue;
          const allIn=(tr.segments||[]).length>0&&(tr.segments||[]).every(seg=>
            seg.start.x>=bx1&&seg.start.x<=bx2&&seg.start.y>=by1&&seg.start.y<=by2&&
            seg.end.x>=bx1&&seg.end.x<=bx2&&seg.end.y>=by1&&seg.end.y<=by2);
          if(allIn) tr.groupId=gid;
        }
        for(const v of(this.board.vias||[])){
          if(v.groupId)continue;
          if(v.x>=bx1&&v.x<=bx2&&v.y>=by1&&v.y<=by2) v.groupId=gid;
        }
      }
    }
    this.render();
  }

  _showCtxMenu(e){
    this._hideCtxMenu();
    const{mx,my}=this._cp(e);

    // ── Hit test ─────────────────────────────────────────────────────────────
    const hitComps   = this.getCompsAt(mx,my);
    const hitTraces  = this.getTracesAt(mx,my);
    const hitVia     = this.getViaAt(mx,my);
    const clickedComp  = hitComps[0]  || null;
    const clickedTrace = hitTraces[0]?.trace || null;
    const clickedVia   = hitVia || null;

    const isMultiSel = this.selectedComps.length >= 2;
    const items = [];

    // ── Helper: build & show the menu ────────────────────────────────────────
    const _flush = () => {
      if(!items.length) return;
      const menu = document.createElement('div');
      menu.className = 'sch-ctx-menu';
      menu.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px`;
      for(const item of items){
        if(item.sep){
          const d=document.createElement('div'); d.className='sch-ctx-sep'; menu.appendChild(d);
        } else if(item.header){
          const d=document.createElement('div');
          d.style.cssText='padding:4px 14px 2px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);';
          d.textContent=item.header; menu.appendChild(d);
        } else {
          const d=document.createElement('div');
          d.className='sch-ctx-item'+(item.danger?' danger':'');
          d.innerHTML=`<span>${item.label}</span>${item.kbd?`<span class="sch-ctx-kbd">${item.kbd}</span>`:''}`;
          d.addEventListener('click',()=>{ this._hideCtxMenu(); item.action(); });
          menu.appendChild(d);
        }
      }
      document.body.appendChild(menu);
      this._ctxMenuEl = menu;
      requestAnimationFrame(()=>{
        const r=menu.getBoundingClientRect(), vw=window.innerWidth, vh=window.innerHeight;
        if(r.right  > vw) menu.style.left = (e.clientX - r.width)  + 'px';
        if(r.bottom > vh) menu.style.top  = (e.clientY - r.height) + 'px';
      });
      setTimeout(()=>{
        this._ctxDismiss = ev => { if(!menu.contains(ev.target)) this._hideCtxMenu(); };
        document.addEventListener('mousedown', this._ctxDismiss);
      }, 0);
    };

    // ── Helper: delete a set of components ───────────────────────────────────
    const _deleteComps = (compsArr) => {
      this._snapshot();
      const ids = new Set(compsArr.map(c=>c.id));
      this.board.components = (this.board.components||[]).filter(c=>!ids.has(c.id));
      if(this.board.groups){
        this.board.groups.forEach(g=>{ g.members=g.members.filter(id=>!ids.has(id)); });
        this.board.groups = this.board.groups.filter(g=>g.members.length>=2);
      }
      this.selectedComp=null; this.selectedComps=[];
      this.render();
      if(typeof rebuildCompList==='function') rebuildCompList();
      if(typeof _notifyParentRefs==='function') _notifyParentRefs();
    };

    // ══════════════════════════════════════════════════════════════════════════
    // CASE 1 — Multi-select
    // ══════════════════════════════════════════════════════════════════════════
    if(isMultiSel){
      const selComps = this.selectedComps;
      const grpIds   = new Set(selComps.filter(c=>c.groupId).map(c=>c.groupId));
      const allSameGrp = grpIds.size===1 && selComps.every(c=>c.groupId===[...grpIds][0]);

      if(allSameGrp){
        const grp = (this.board.groups||[]).find(g=>g.id===[...grpIds][0]);
        if(grp){
          items.push({header:`"${grp.name}"`});
          items.push({label:'Rename Group\u2026', action:()=>{
            const n=prompt('Group name:',grp.name); if(n){grp.name=n;this.render();}
          }});
          items.push({sep:true});
          items.push({label:'Ungroup', kbd:'Ctrl+\u21e7+G', action:()=>this.ungroupSelected()});
          items.push({sep:true});
          items.push({label:'Delete Group', danger:true, action:()=>{
            this._snapshot();
            const ids=new Set(grp.members);
            this.board.components=(this.board.components||[]).filter(c=>!ids.has(c.id));
            this.board.groups=this.board.groups.filter(g=>g.id!==grp.id);
            this.selectedComp=null; this.selectedComps=[]; this.render();
            if(typeof rebuildCompList==='function') rebuildCompList();
            if(typeof _notifyParentRefs==='function') _notifyParentRefs();
          }});
        }
      } else {
        items.push({label:`Rotate ${selComps.length} components 90° CW`, kbd:'R', action:()=>this.rotateSelGroup(90)});
        items.push({sep:true});
        items.push({label:`Group ${selComps.length} components`, kbd:'Ctrl+G', action:()=>this.groupSelected()});
        if(grpIds.size>0)
          items.push({label:'Ungroup', kbd:'Ctrl+\u21e7+G', action:()=>this.ungroupSelected()});
        items.push({sep:true});
        items.push({label:`Delete ${selComps.length} components`, danger:true, action:()=>_deleteComps(selComps)});
      }
      _flush(); return;
    }

    // ══════════════════════════════════════════════════════════════════════════
    // CASE 2 — Single component (clicked or already selected)
    // ══════════════════════════════════════════════════════════════════════════
    const comp = clickedComp || this.selectedComp;
    if(comp){
      // Ensure it is selected
      if(this.selectedComp !== comp){
        this.selectedComp = comp;
        const grp = comp.groupId && (this.board?.groups||[]).find(g=>g.id===comp.groupId);
        this.selectedComps = grp ? (this.board.components||[]).filter(c=>grp.members.includes(c.id)) : [comp];
        this.render();
      }

      const grp = comp.groupId ? (this.board?.groups||[]).find(g=>g.id===comp.groupId) : null;
      const label = comp.ref || comp.id || 'Component';
      const val   = comp.value ? ` — ${comp.value}` : '';
      items.push({header: label + val});

      // Rotate / Flip
      items.push({label:'Rotate 90° CW', kbd:'R', action:()=>{
        this._snapshot();
        comp.rotation=((comp.rotation||0)+90)%360;
        this.render(); if(typeof updateInfoPanel==='function') updateInfoPanel();
      }});
      const onBack = (comp.layer||'F')==='B';
      items.push({label: onBack ? 'Flip to Front' : 'Flip to Back', action:()=>{
        this._snapshot();
        comp.layer = onBack ? 'F' : 'B';
        this.render(); if(typeof updateInfoPanel==='function') updateInfoPanel();
      }});

      // Group section
      items.push({sep:true});
      if(grp){
        items.push({header:`Group: "${grp.name}"`});
        items.push({label:'Rename Group\u2026', action:()=>{
          const n=prompt('Group name:',grp.name); if(n){grp.name=n;this.render();}
        }});
        items.push({label:'Ungroup', kbd:'Ctrl+\u21e7+G', action:()=>this.ungroupSelected()});
        items.push({sep:true});
        items.push({label:'Delete Group', danger:true, action:()=>{
          this._snapshot();
          const ids=new Set(grp.members);
          this.board.components=(this.board.components||[]).filter(c=>!ids.has(c.id));
          this.board.groups=(this.board.groups||[]).filter(g=>g.id!==grp.id);
          this.selectedComp=null; this.selectedComps=[]; this.render();
          if(typeof rebuildCompList==='function') rebuildCompList();
          if(typeof _notifyParentRefs==='function') _notifyParentRefs();
        }});
      } else {
        items.push({label:'Delete', danger:true, action:()=>_deleteComps([comp])});
      }
      if(grp){
        items.push({label:'Delete Component', danger:true, action:()=>_deleteComps([comp])});
      }
      _flush(); return;
    }

    // ══════════════════════════════════════════════════════════════════════════
    // CASE 3 — Trace
    // ══════════════════════════════════════════════════════════════════════════
    if(clickedTrace){
      this.selectedTrace = clickedTrace; this.render();
      items.push({header:'Trace'});
      items.push({label:'Delete Trace', danger:true, action:()=>{
        this._snapshot();
        this.board.traces=(this.board.traces||[]).filter(t=>t!==clickedTrace);
        this.selectedTrace=null; this.render();
      }});
      _flush(); return;
    }

    // ══════════════════════════════════════════════════════════════════════════
    // CASE 4 — Via
    // ══════════════════════════════════════════════════════════════════════════
    if(clickedVia){
      this.selectedVia = clickedVia; this.render();
      items.push({header:'Via'});
      items.push({label:'Delete Via', danger:true, action:()=>{
        this._snapshot();
        this.board.vias=(this.board.vias||[]).filter(v=>v!==clickedVia);
        this.selectedVia=null; this.render();
      }});
      _flush(); return;
    }
    // Empty space — nothing to show
  }

  _hideCtxMenu(){
    if(this._ctxMenuEl){this._ctxMenuEl.remove();this._ctxMenuEl=null;}
    if(this._ctxDismiss){document.removeEventListener('mousedown',this._ctxDismiss);this._ctxDismiss=null;}
  }

  _drawGroups(){
    const groups=this.board?.groups;
    if(!groups?.length)return;
    const ctx=this.ctx;
    const comps=this.board.components||[];
    // Determine which group (if any) is currently selected
    const selGrpId=this.selectedComp?.groupId||this.selectedVia?.groupId||this.selectedTrace?.groupId||null;
    for(const grp of groups){
      const members=comps.filter(c=>grp.members.includes(c.id));
      if(members.length<2)continue;
      // Compute bounding box over all member pads, grouped traces and grouped vias
      let x1=Infinity,y1=Infinity,x2=-Infinity,y2=-Infinity;
      for(const c of members){
        const bb=this._compBBox(c); if(!bb)continue;
        x1=Math.min(x1,c.x+bb.x1); y1=Math.min(y1,c.y+bb.y1);
        x2=Math.max(x2,c.x+bb.x2); y2=Math.max(y2,c.y+bb.y2);
      }
      for(const tr of(this.board.traces||[])){
        if(tr.groupId!==grp.id)continue;
        for(const seg of(tr.segments||[])){
          x1=Math.min(x1,seg.start.x,seg.end.x);y1=Math.min(y1,seg.start.y,seg.end.y);
          x2=Math.max(x2,seg.start.x,seg.end.x);y2=Math.max(y2,seg.start.y,seg.end.y);
        }
      }
      for(const v of(this.board.vias||[])){
        if(v.groupId!==grp.id)continue;
        x1=Math.min(x1,v.x);y1=Math.min(y1,v.y);
        x2=Math.max(x2,v.x);y2=Math.max(y2,v.y);
      }
      if(!isFinite(x1))continue;
      const pad=1.5;
      x1-=pad; y1-=pad; x2+=pad; y2+=pad;
      const sx=this.mmX(x1),sy=this.mmY(y1);
      const sw=(x2-x1)*this.scale,sh=(y2-y1)*this.scale;
      const isActive=grp.id===selGrpId;
      ctx.save();
      ctx.strokeStyle=isActive?'rgba(99,200,150,0.9)':'rgba(99,200,150,0.45)';
      ctx.lineWidth=isActive?2:1.5;
      ctx.setLineDash([6,4]);
      ctx.strokeRect(sx,sy,sw,sh);
      ctx.fillStyle=isActive?'rgba(99,200,150,0.08)':'rgba(99,200,150,0.03)';
      ctx.fillRect(sx,sy,sw,sh);
      ctx.setLineDash([]);
      // Label
      const fs=Math.max(8,Math.min(11,this.scale*0.9));
      ctx.font=`${fs}px monospace`;
      ctx.fillStyle=isActive?'rgba(99,200,150,0.95)':'rgba(99,200,150,0.6)';
      ctx.textBaseline='bottom';
      ctx.fillText(grp.name||grp.id,sx+3,sy-2);
      ctx.textBaseline='alphabetic';
      ctx.restore();
    }
  }

  _drawSelPad({comp,pad}){
    const{px,py}=this._padWorld(comp,pad);
    const sx=this.mmX(px),sy=this.mmY(py);
    const r=Math.max(5,Math.max(pad.size_x||1.6,pad.size_y||1.6)/2*this.scale+2);
    const ctx=this.ctx;
    ctx.strokeStyle='#ffffff'; ctx.lineWidth=2; ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(sx,sy,r,0,Math.PI*2); ctx.stroke();
    ctx.strokeStyle='rgba(108,99,255,0.8)'; ctx.lineWidth=1.5;
    ctx.beginPath(); ctx.arc(sx,sy,r+3,0,Math.PI*2); ctx.stroke();
    // Pin label
    if(pad.name||pad.number){
      ctx.fillStyle='#ffffff'; ctx.font='bold 10px monospace';
      ctx.textAlign='center'; ctx.textBaseline='bottom';
      ctx.fillText((pad.name||'')||(pad.number||''),sx,sy-r-2);
      ctx.textBaseline='alphabetic';
    }
  }

  _netCol(net){
    if(/^(GND|AGND|DGND|PGND)$/.test(net))return'#33bb55';
    if(/^(VCC|VDD|VIN|VBAT|VBUS|3V3|5V|12V|24V|AVCC|DVCC)/.test(net))return'#dd4444';
    let h=0;for(const c of net)h=(h*31+c.charCodeAt(0))&0xffff;
    return['#c87533','#b87333','#d4a56a','#cd7f32','#c68642'][h%5];
  }

  _collectNetPads(){
    const m={};
    for(const c of(this.board?.components||[]))
      for(const p of(c.pads||[])){
        if(!p.net)continue;
        const{px,py}=this._padWorld(c,p);
        (m[p.net]||(m[p.net]=[])).push({x:px,y:py,comp:c.id,pad:p.number});
      }
    return m;
  }

  getCompAt(mx,my){
    // Returns the smallest (most specific) component at this pixel position
    const hits=this.getCompsAt(mx,my);
    return hits.length?hits[0]:null;
  }

  getCompsAt(mx,my){
    // Returns ALL components at pixel position, sorted smallest bbox area first
    const m=0.1;
    const hits=[];
    for(const c of(this.board?.components||[])){
      const bb=this._compBBox(c); if(!bb)continue;
      if(mx>=this.mmX(c.x+bb.x1-m)&&mx<=this.mmX(c.x+bb.x2+m)&&
         my>=this.mmY(c.y+bb.y1-m)&&my<=this.mmY(c.y+bb.y2+m)){
        const area=(bb.x2-bb.x1)*(bb.y2-bb.y1);
        hits.push({c,area});
      }
    }
    hits.sort((a,b)=>a.area-b.area);
    return hits.map(h=>h.c);
  }

  getAreaAt(mx,my){
    const cx=this.cX(mx),cy=this.cY(my);
    for(const a of(this.board?.areas||[])){
      const x1=Math.min(a.x1,a.x2),y1=Math.min(a.y1,a.y2);
      const x2=Math.max(a.x1,a.x2),y2=Math.max(a.y1,a.y2);
      if(cx>=x1&&cx<=x2&&cy>=y1&&cy<=y2)return a;
    }
    return null;
  }

  getDrawingAt(mx,my){
    // Returns drawing whose polyline passes within ~5px of click position
    const THRESH=5/this.scale; // mm threshold
    const cx=this.cX(mx),cy=this.cY(my);
    for(const d of(this.board?.drawings||[])){
      const pts=d.points||[];
      const n=pts.length+(d.closed?1:0);
      for(let i=0;i<n-1;i++){
        const a=pts[i],b=pts[(i+1)%pts.length];
        const dx=b.x-a.x,dy=b.y-a.y;
        const len2=dx*dx+dy*dy;
        if(len2===0)continue;
        const t=Math.max(0,Math.min(1,((cx-a.x)*dx+(cy-a.y)*dy)/len2));
        const px=a.x+t*dx,py=a.y+t*dy;
        if(Math.hypot(cx-px,cy-py)<THRESH)return d;
      }
    }
    return null;
  }

  getPadAt(mx,my){
    const hits=this.getPadsAt(mx,my);
    return hits.length?hits[0]:null;
  }

  getPadsAt(mx,my){
    // All pads at pixel position, sorted closest-centre first
    const thr=6;
    const hits=[];
    for(const c of(this.board?.components||[]))
      for(const p of(c.pads||[])){
        const{px,py}=this._padWorld(c,p);
        const dist=Math.hypot(mx-this.mmX(px),my-this.mmY(py));
        if(dist<=Math.max(thr,(p.size_x||1.6)/2*this.scale))
          hits.push({comp:c,pad:p,x:px,y:py,dist});
      }
    hits.sort((a,b)=>a.dist-b.dist);
    return hits;
  }

  getViaAt(mx,my){
    for(const v of(this.board?.vias||[])){
      const r=Math.max(5,(v.size||1.0)/2*this.scale);
      if(Math.hypot(mx-this.mmX(v.x),my-this.mmY(v.y))<=r)return v;
    }
    return null;
  }

  /** Find nearest pad within pixelRadius, returns same shape as getPadAt or null. */
  getNearestPad(mx,my,pixelRadius){
    let best=null,bestDist=pixelRadius;
    for(const c of(this.board?.components||[]))
      for(const p of(c.pads||[])){
        const{px,py}=this._padWorld(c,p);
        const dist=Math.hypot(mx-this.mmX(px),my-this.mmY(py));
        if(dist<bestDist){bestDist=dist;best={comp:c,pad:p,x:px,y:py,dist};}
      }
    return best;
  }

  /** Find nearest via within pixelRadius, returns via object or null. */
  getNearestVia(mx,my,pixelRadius){
    let best=null,bestDist=pixelRadius;
    for(const v of(this.board?.vias||[])){
      const dist=Math.hypot(mx-this.mmX(v.x),my-this.mmY(v.y));
      if(dist<bestDist){bestDist=dist;best=v;bestDist=dist;}
    }
    return best;
  }

  getTraceAt(mx,my){
    // Returns first hit (for hover); use getTracesAt for all hits
    const hits=this.getTracesAt(mx,my);
    return hits.length?hits[0]:null;
  }

  getTracesAt(mx,my){
    const minW=6;
    const results=[];
    for(let i=0;i<(this.board?.traces||[]).length;i++){
      const tr=this.board.traces[i];
      const thr=Math.max(minW,(tr.width||0.25)/2*this.scale+2);
      for(let j=0;j<(tr.segments||[]).length;j++){
        const seg=tr.segments[j];
        const x1=this.mmX(seg.start.x),y1=this.mmY(seg.start.y);
        const x2=this.mmX(seg.end.x),  y2=this.mmY(seg.end.y);
        const dx=x2-x1,dy=y2-y1,len2=dx*dx+dy*dy;
        if(len2<0.001)continue;
        const t=Math.max(0,Math.min(1,((mx-x1)*dx+(my-y1)*dy)/len2));
        if(Math.hypot(mx-(x1+t*dx),my-(y1+t*dy))<=thr){
          // Only add each trace once
          if(!results.find(r=>r.trace===tr))
            results.push({trace:tr,traceIdx:i,segIdx:j});
          break;
        }
      }
    }
    return results;
  }

  fitBoard(){
    if(!this.board)return;
    // Canvas may be 0×0 if it was sized while the parent section was hidden — resize first
    if(!this.canvas.width || !this.canvas.height) resize();
    const W=this.canvas.width,H=this.canvas.height,b=this.board.board,mg=60;
    if(!W||!H)return; // still 0 (e.g. display:none parent), bail
    this.scale=Math.max(2,Math.min(60,Math.min((W-mg*2)/b.width,(H-mg*2)/b.height)));
    this.panX=0;this.panY=0;
    this.offsetX=(W-b.width*this.scale)/2;
    this.offsetY=(H-b.height*this.scale)/2;
    this.render();
  }

  exportJSON(){return JSON.stringify(this.board,null,2);}

  _cp(e){
    const r=this.canvas.getBoundingClientRect();
    return{mx:(e.clientX-r.left)*this.canvas.width/r.width,
           my:(e.clientY-r.top)*this.canvas.height/r.height};
  }

  _commitTrace(){
    if(this.routePoints.length<2){this.routePoints=[];return;}
    const badPad=this._traceHitsWrongNet(this.routePoints,this.routeNet||'');
    if(badPad){
      this._routeError=`Blocked: passes through ${badPad.name}(${badPad.net})`;
      this.render();
      setTimeout(()=>{this._routeError=null;this.render();},2500);
      return;
    }
    const segs=[];
    for(let i=0;i<this.routePoints.length-1;i++)
      segs.push({start:{x:this.routePoints[i].x,y:this.routePoints[i].y},
                 end:{x:this.routePoints[i+1].x,y:this.routePoints[i+1].y}});
    (this.board.traces||(this.board.traces=[])).push({
      net:this.routeNet||'',layer:this.routeLayer,
      width:parseFloat(document.getElementById('route-width').value)||DR.traceWidth,
      segments:segs
    });
    this.routePoints=[];this.routeNet=null;
    this._snapshot(); this.render();
  }

  _init(){
    const cv=this.canvas;
    cv.addEventListener('mousedown',e=>{
      if(e.button===1){e.preventDefault();this._isPan=true;this._panS={x:this._cp(e).mx-this.panX,y:this._cp(e).my-this.panY};return;}
      const{mx,my}=this._cp(e);
      const xmm=this.snap(this.cX(mx)),ymm=this.snap(this.cY(my));
      if(e.button===2){
        if(this.tool==='route'){this.routePoints=[];this.routeNet=null;this.render();}
        if(this.tool==='zone'){this.zonePoints=[];this.render();}
        if(this.tool==='area'){this.areaStart=null;this._isAreaDrag=false;this.render();}
        if(this.tool==='draw'){this.drawPoints=[];this.render();}
        return;
      }
      if(this.tool==='select'){
        // Gather ALL hittable elements at this position
        const hitComps=this.getCompsAt(mx,my);   // all comps, smallest first
        const hitPads=this.getPadsAt(mx,my);     // all pads, closest first
        const hitV=this.getViaAt(mx,my);
        const hitThs=this.getTracesAt(mx,my);    // all overlapping traces
        const hitA=this.getAreaAt(mx,my);
        const hitD=this.getDrawingAt(mx,my);
        // Build cycle list: comps first, then pads (only if parent comp is already selected), via, traces, area, drawing
        const candidates=[];
        for(const c of hitComps) candidates.push({type:'comp',obj:c});
        // Pads only available after their component is selected
        for(const h of hitPads)
          if(this.selectedComp===h.comp||this.selectedComps.includes(h.comp))
            candidates.push({type:'pad',obj:h});
        if(hitV) candidates.push({type:'via',obj:hitV});
        for(const th of hitThs) candidates.push({type:'trace',obj:th.trace,segIdx:th.segIdx});
        if(hitA) candidates.push({type:'area',obj:hitA});
        if(hitD) candidates.push({type:'drawing',obj:hitD});

        let picked=null;
        if(candidates.length===0){
          // Nothing here — start box selection
          this.selectedComp=null;this.selectedTrace=null;this.selectedArea=null;this.selectedVia=null;this.selectedDrawing=null;this.selectedComps=[];
          this._isBoxSel=true;this._boxSelStart={mx,my};this._boxSelEnd={mx,my};
          this._lastClickObj=null;
        } else {
          // Click-cycle: if clicking same spot again, advance to next candidate
          const CYCLE_DIST=8;
          const samePx=this._lastClickMx!==null&&Math.hypot(mx-this._lastClickMx,my-this._lastClickMy)<CYCLE_DIST;
          let nextIdx=0;
          if(samePx&&this._lastClickObj){
            const curIdx=candidates.findIndex(c=>c.obj===this._lastClickObj);
            nextIdx=(curIdx+1)%candidates.length;
          }
          picked=candidates[nextIdx];
          this._lastClickMx=mx;this._lastClickMy=my;this._lastClickObj=picked.obj;

          this.selectedTrace=null;this.selectedArea=null;this.selectedVia=null;this.selectedPad=null;this.selectedDrawing=null;

          if(e.ctrlKey||e.metaKey){
            // Ctrl+click: toggle component in multi-select
            if(picked.type==='comp'){
              const ci=this.selectedComps.indexOf(picked.obj);
              if(ci===-1)this.selectedComps.push(picked.obj);
              else this.selectedComps.splice(ci,1);
              this.selectedComp=this.selectedComps[this.selectedComps.length-1]||null;
              if(this.selectedComps.length>0) this._startDrag(this.selectedComps[0],mx,my);
            }
          } else {
            if(picked.type==='comp'&&this.selectedComps.length>1&&this.selectedComps.includes(picked.obj)){
              // Clicked a component that's already part of a multi-selection — drag all together
              this._startDrag(picked.obj,mx,my);
            } else {
            this.selectedComps=[];
            this.selectedComp=null;
            if(picked.type==='pad'){
              this.selectedPad={comp:picked.obj.comp,pad:picked.obj.pad};
            } else if(picked.type==='comp'){
              const c=picked.obj;
              this.selectedComp=c;
              // If component belongs to a group, select all group members together
              const grp=c.groupId&&this.board?.groups?.find(g=>g.id===c.groupId);
              if(grp){
                this.selectedComps=(this.board.components||[]).filter(m=>grp.members.includes(m.id));
              } else {
                this.selectedComps=[c];
              }
              this._startDrag(c,mx,my);
            } else if(picked.type==='via'){
              const _via=picked.obj;
              const _viaGrp=_via.groupId&&this.board?.groups?.find(g=>g.id===_via.groupId);
              if(_viaGrp){
                // Via belongs to a group — select all group members and drag the whole group
                const _repC=(this.board.components||[]).find(c=>_viaGrp.members.includes(c.id));
                if(_repC){
                  this.selectedComp=_repC;
                  this.selectedComps=(this.board.components||[]).filter(c=>_viaGrp.members.includes(c.id));
                  this._startDrag(_repC,mx,my);
                } else {
                  this.selectedVia=_via;this._isDragVia=true;
                  this._dragViaOff={x:this.cX(mx)-_via.x,y:this.cY(my)-_via.y};
                }
              } else {
                this.selectedVia=_via;
                this._isDragVia=true;
                this._dragViaOff={x:this.cX(mx)-_via.x,y:this.cY(my)-_via.y};
              }
            } else if(picked.type==='trace'){
              const _tr=picked.obj;
              const _trGrp=_tr.groupId&&this.board?.groups?.find(g=>g.id===_tr.groupId);
              if(_trGrp){
                // Trace belongs to a group — select all group members and drag the whole group
                const _repC=(this.board.components||[]).find(c=>_trGrp.members.includes(c.id));
                if(_repC){
                  this.selectedComp=_repC;
                  this.selectedComps=(this.board.components||[]).filter(c=>_trGrp.members.includes(c.id));
                  this._startDrag(_repC,mx,my);
                } else {
                  this.selectedTrace=_tr;
                  this._startTraceDrag(_tr,picked.segIdx??0,mx,my);
                  const rni=document.getElementById('route-net-input');
                  if(rni)rni.value=_tr.net||'';
                }
              } else {
                this.selectedTrace=_tr;
                this._startTraceDrag(_tr,picked.segIdx??0,mx,my);
                // Sync route net input to selected trace net
                const rni=document.getElementById('route-net-input');
                if(rni)rni.value=_tr.net||'';
              }
            } else if(picked.type==='area'){
              this.selectedArea=picked.obj;
            } else if(picked.type==='drawing'){
              this.selectedDrawing=picked.obj;
              this._isDragDrawing=true;
              this._dragDrawingStart={x:this.cX(mx),y:this.cY(my)};
              this._dragDrawingPts=picked.obj.points.map(p=>({x:p.x,y:p.y}));
            }
            } // end else (not multi-drag)
          }
        }
        this.render();updateInfoPanel();renderAreasPanel();
      } else if(this.tool==='route'){
        if(!this.board)return;
        if(this.routePoints.length===0){
          // Start: snap to nearby pad/via with generous radius, inherit net
          const _snapR=Math.max(20,(DR.snapRadius||2)*this.scale);
          const hit=this.getNearestPad(mx,my,_snapR);
          const hv=hit?null:this.getNearestVia(mx,my,_snapR);
          const sx=hit?hit.x:(hv?hv.x:xmm);
          const sy=hit?hit.y:(hv?hv.y:ymm);
          this.routeLayer=this.workLayer||'F.Cu';
          this.routeNet=(hit?.pad?.net)||(hv?.net)||null;
          this.routePoints=[{x:sx,y:sy}];
        } else {
          // Adding a point: check destination net (generous snap)
          const _snapR2=Math.max(20,(DR.snapRadius||2)*this.scale);
          const hit=this.getNearestPad(mx,my,_snapR2);
          const hv=hit?null:this.getNearestVia(mx,my,_snapR2);
          const ha=(hit||hv)?null:this.getAreaAt(mx,my);
          const destNet=(hit?.pad?.net)||(hv?.net)||(ha?.net)||null;

          // Block if destination has a conflicting net
          if(destNet&&this.routeNet&&destNet!==this.routeNet){
            this._routeError=`Net mismatch: ${destNet} ≠ ${this.routeNet}`;
            this.render();
            setTimeout(()=>{this._routeError=null;this.render();},2000);
            return;
          }

          // Snap to pad position if hitting a pad; use elbow routing
          let ex=hit?hit.x:xmm, ey=hit?hit.y:ymm;

          // Use avoidance path from preview (or compute fresh with elbow)
          const lastPt=this.routePoints[this.routePoints.length-1];
          let avoidPath=this._lastAvoidPath;
          this._lastAvoidPath=null;
          if(!avoidPath){
            const elbowPts=this._computeElbow(lastPt.x,lastPt.y,ex,ey);
            avoidPath=[elbowPts[0]];
            for(let i=0;i<elbowPts.length-1;i++){
              const seg=this._routeAroundPads(elbowPts[i].x,elbowPts[i].y,elbowPts[i+1].x,elbowPts[i+1].y,this.routeNet||'');
              for(let j=1;j<seg.length;j++)avoidPath.push(seg[j]);
            }
            // Validate the path — collisions
            for(let i=0;i<avoidPath.length-1;i++){
              const h2=this._segHitsWrongPad(avoidPath[i].x,avoidPath[i].y,avoidPath[i+1].x,avoidPath[i+1].y,this.routeNet||'');
              if(h2){avoidPath=null;break;}
            }
            // Validate angles
            if(avoidPath){
              let prevDir2=null;
              if(this.routePoints.length>=2){
                const p1=this.routePoints[this.routePoints.length-2],p2=this.routePoints[this.routePoints.length-1];
                prevDir2=Math.atan2(p2.y-p1.y,p2.x-p1.x);
              }
              if(!this._validatePathAngles(avoidPath,prevDir2))avoidPath=null;
            }
          }
          // Block if no valid path around obstacles
          if(!avoidPath){
            this._routeError='Blocked: no clear path around pads';
            this.render();
            setTimeout(()=>{this._routeError=null;this.render();},2000);
            return;
          }

          // Assign net from destination if not yet set
          if(!this.routeNet&&destNet) this.routeNet=destNet;

          // Push all avoidance waypoints (skip first which is lastPt)
          for(let i=1;i<avoidPath.length;i++) this.routePoints.push({x:avoidPath[i].x,y:avoidPath[i].y});

          // Auto-commit when landing on same-net pad/via/area, or double-click
          const sameNet=destNet&&(!this.routeNet||destNet===this.routeNet);
          if(sameNet||e.detail===2) this._commitTrace(); else this.render();
        }
      } else if(this.tool==='via'){
        if(!this.board)return;
        const hit=this.getPadAt(mx,my);
        (this.board.vias||(this.board.vias=[])).push({
          x:xmm,y:ymm,size:DR.viaSize,drill:DR.viaDrill,net:hit?.pad?.net||null});
        this._snapshot(); this.render();
      } else if(this.tool==='zone'){
        if(!this.board)return;
        if(e.detail===2){
          if(this.zonePoints.length>=3){
            const lyr=this.workLayer||'F.Cu';
            (this.board.zones||(this.board.zones=[])).push({
              layer:lyr,net:this.zoneNet||'GND',
              points:[...this.zonePoints]});
          }
          this.zonePoints=[];this.zoneNet=null;this._snapshot();this.render();
        } else {
          this.zonePoints.push({x:xmm,y:ymm}); this.render();
        }
      } else if(this.tool==='area'){
        if(!this.board)return;
        const inp=document.getElementById('area-net-input');
        if(inp)this.areaNet=inp.value.trim()||'GND';
        this.areaLayer=this.workLayer||'F.Cu';
        this.areaStart={x:xmm,y:ymm};
        this._isAreaDrag=true;
      } else if(this.tool==='measure'){
        if(!this.measureStart){this.measureStart={x:xmm,y:ymm};}
        else{this.measureStart=null; this.render();}
      } else if(this.tool==='draw'){
        if(!this.board)return;
        const lyrSel=document.getElementById('draw-layer-sel');
        const wSel=document.getElementById('draw-width-input');
        this.drawLayer=lyrSel?lyrSel.value:'Edge.Cuts';
        this.drawWidth=wSel?Math.max(0.01,parseFloat(wSel.value)||0.05):0.05;
        if(e.detail===2){
          // Double-click — finalize shape
          if(this.drawPoints.length>=2){
            const closeSel=document.getElementById('draw-close-chk');
            const closed=closeSel?closeSel.checked:false;
            (this.board.drawings||(this.board.drawings=[])).push({
              id:'d'+Date.now(),layer:this.drawLayer,width:this.drawWidth,
              points:[...this.drawPoints],closed});
            this._snapshot();
          }
          this.drawPoints=[];this.render();
        } else {
          this.drawPoints.push({x:xmm,y:ymm});this.render();
        }
      }
    });

    // Cache frequently-accessed DOM elements once at init time
    this._elStatusBar = document.getElementById('status-bar');
    this._elInfoX     = document.getElementById('info-x');
    this._elInfoY     = document.getElementById('info-y');
    this._statusRafPending = false;

    cv.addEventListener('mousemove',e=>{
      // Throttle to ~60fps — rAF guard batches renders but JS hit-testing still ran at 1000+fps
      const _t=performance.now();
      if(_t-this._lastMoveMs<14)return;
      this._lastMoveMs=_t;
      const{mx,my}=this._cp(e);
      this._mx=this.snap(this.cX(mx)); this._my=this.snap(this.cY(my));
      this._mxPx=mx; this._myPx=my;
      // Throttle status-bar text update to one rAF per frame — avoids DOM write at 1000fps
      if(!this._statusRafPending){
        this._statusRafPending=true;
        const xStr=this.cX(mx).toFixed(2).padStart(7), yStr=this.cY(my).toFixed(2).padStart(7);
        requestAnimationFrame(()=>{
          this._statusRafPending=false;
          if(this._elStatusBar) this._elStatusBar.textContent=`x:${xStr}  y:${yStr} mm`;
        });
      }
      if(this._isDrag&&this._dragC){
        const newPX=this.snap(this.cX(mx)-this._dragOff.x);
        const newPY=this.snap(this.cY(my)-this._dragOff.y);
        const dx=newPX-this._dragC.x, dy=newPY-this._dragC.y;
        if(dx||dy){
          // Move all selected comps
          const compsToMove=this._dragCompOffsets||[{comp:this._dragC,ox:0,oy:0}];
          for(const{comp,ox,oy}of compsToMove){
            comp.x=newPX+ox; comp.y=newPY+oy;
          }
          // If dragging a grouped component, translate ALL grouped traces and vias by dx/dy
          const _dragGrpId=this._dragC?.groupId||null;
          if(_dragGrpId){
            for(const tr of(this.board.traces||[])){
              if(tr.groupId!==_dragGrpId)continue;
              for(const seg of(tr.segments||[])){
                seg.start.x+=dx;seg.start.y+=dy;
                seg.end.x+=dx;seg.end.y+=dy;
              }
            }
            for(const v of(this.board.vias||[])){
              if(v.groupId!==_dragGrpId)continue;
              v.x+=dx;v.y+=dy;
            }
            // Keep pad-snap positions in sync so ratsnest stays correct
            if(this._dragPadSnap){for(const snap of this._dragPadSnap){snap.px+=dx;snap.py+=dy;}}
          } else {
            // Move trace endpoints connected to dragged pads (non-grouped drag)
            if(this._dragPadSnap){
              const EPS=0.15;
              for(const snap of this._dragPadSnap){
                const oldX=snap.px, oldY=snap.py;
                const newX=oldX+dx, newY=oldY+dy;
                snap.px=newX; snap.py=newY;
                for(const tr of(this.board.traces||[])){
                  for(const seg of(tr.segments||[])){
                    if(Math.abs(seg.start.x-oldX)<EPS&&Math.abs(seg.start.y-oldY)<EPS){seg.start.x=newX;seg.start.y=newY;}
                    if(Math.abs(seg.end.x-oldX)<EPS&&Math.abs(seg.end.y-oldY)<EPS){seg.end.x=newX;seg.end.y=newY;}
                  }
                }
              }
            }
          }
        }
        this.render();
        if(this._elInfoX) this._elInfoX.value=this._dragC.x.toFixed(2);
        if(this._elInfoY) this._elInfoY.value=this._dragC.y.toFixed(2);
      } else if(this._isDragVia&&this.selectedVia){
        this.selectedVia.x=this.snap(this.cX(mx)-this._dragViaOff.x);
        this.selectedVia.y=this.snap(this.cY(my)-this._dragViaOff.y);
        this.render();
      } else if(this._isDragDrawing&&this.selectedDrawing){
        const dx=this.snap(this.cX(mx))-this.snap(this._dragDrawingStart.x);
        const dy=this.snap(this.cY(my))-this.snap(this._dragDrawingStart.y);
        const pts=this._dragDrawingPts;
        this.selectedDrawing.points=pts.map(p=>({x:p.x+dx,y:p.y+dy}));
        this.render();
      } else if(this._isDragTrace&&this._dragTrace){
        const tr=this._dragTrace,si=this._dragTraceSegIdx;
        const orig=this._dragTraceOrigSeg,origAll=this._dragTraceOrigAll;
        const curX=this.snap(this.cX(mx)),curY=this.snap(this.cY(my));
        const origMidX=(orig.start.x+orig.end.x)/2,origMidY=(orig.start.y+orig.end.y)/2;
        let dx=curX-this._dragTraceOff.x-origMidX;
        let dy=curY-this._dragTraceOff.y-origMidY;
        // Enforce angle rule: constrain drag to perpendicular axis of the
        // segment so its angle is preserved (45° stays 45°, H stays H, etc.)
        const segDx=orig.end.x-orig.start.x,segDy=orig.end.y-orig.start.y;
        const segLen=Math.hypot(segDx,segDy);
        if(segLen>0.01){
          const segAng=Math.atan2(segDy,segDx);
          const perpX=-Math.sin(segAng),perpY=Math.cos(segAng);
          const proj=dx*perpX+dy*perpY;
          dx=this.snap(proj*perpX);
          dy=this.snap(proj*perpY);
        }
        const nsx=orig.start.x+dx,nsy=orig.start.y+dy;
        const nex=orig.end.x+dx,ney=orig.end.y+dy;
        const EPS=0.001;
        for(let i=0;i<tr.segments.length;i++){
          const s=tr.segments[i],o=origAll[i];
          if(i===si){
            s.start.x=nsx;s.start.y=nsy;s.end.x=nex;s.end.y=ney;
          } else {
            s.start.x=o.start.x;s.start.y=o.start.y;s.end.x=o.end.x;s.end.y=o.end.y;
            if(Math.abs(o.end.x-orig.start.x)<EPS&&Math.abs(o.end.y-orig.start.y)<EPS){s.end.x=nsx;s.end.y=nsy;}
            if(Math.abs(o.start.x-orig.start.x)<EPS&&Math.abs(o.start.y-orig.start.y)<EPS){s.start.x=nsx;s.start.y=nsy;}
            if(Math.abs(o.end.x-orig.end.x)<EPS&&Math.abs(o.end.y-orig.end.y)<EPS){s.end.x=nex;s.end.y=ney;}
            if(Math.abs(o.start.x-orig.end.x)<EPS&&Math.abs(o.start.y-orig.end.y)<EPS){s.start.x=nex;s.start.y=ney;}
          }
        }
        // Enforce minimum angle rule (DR.minTraceAngle, default 90°) — reject move if it would create a sharp angle
        if(!this._checkTraceAngles(tr,si,nsx,nsy,nex,ney)){
          // Restore segments from origAll (undo the tentative update above)
          for(let i=0;i<tr.segments.length;i++){
            tr.segments[i].start.x=origAll[i].start.x;tr.segments[i].start.y=origAll[i].start.y;
            tr.segments[i].end.x=origAll[i].end.x;tr.segments[i].end.y=origAll[i].end.y;
          }
          this.render();
          return;
        }
        this._traceDragViolations=this._checkTraceClearance(tr,si);
        this.render();
      } else if(this._isPan){
        this.panX=mx-this._panS.x; this.panY=my-this._panS.y; this.render();
      } else if(this._isBoxSel&&this._boxSelStart){
        this._boxSelEnd={mx,my};this.render();
      } else if(this.tool==='route'||
                (this.tool==='zone'&&this.zonePoints.length>0)||
                (this.tool==='measure'&&this.measureStart)||
                (this.tool==='area'&&this.areaStart)||
                (this.tool==='draw')){
        this.render();
      } else if(this.tool==='select'){
        const th=this.getTraceAt(mx,my);
        const newHovTr=th?th.trace:null;
        const newHovC=this.getCompAt(mx,my)||null; // already returns smallest
        let needRender=false;
        if(newHovTr!==this._hoverTrace){this._hoverTrace=newHovTr;needRender=true;}
        if(newHovC!==this._hoverComp){this._hoverComp=newHovC;needRender=true;}
        if(needRender)this.render();
        const wrap=document.getElementById('canvas-wrap');
        if(wrap) wrap.style.cursor=this._isDragTrace?'grabbing':(newHovTr||newHovC)?'pointer':'default';
      }
    });

    cv.addEventListener('mouseup',e=>{
      const wasDragging=this._isDrag||this._isDragVia||this._isDragDrawing||this._isDragTrace;
      this._isDrag=false; this._dragC=null; this._isPan=false; this._isDragVia=false;
      this._isDragDrawing=false; this._dragDrawingStart=null; this._dragDrawingPts=null;
      this._isDragTrace=false; this._dragTrace=null; this._traceDragViolations=[];
      if(wasDragging) this._snapshot(); // snapshot after move
      if(this._isBoxSel&&this._boxSelStart&&this._boxSelEnd){
        const bx1=Math.min(this._boxSelStart.mx,this._boxSelEnd.mx);
        const by1=Math.min(this._boxSelStart.my,this._boxSelEnd.my);
        const bx2=Math.max(this._boxSelStart.mx,this._boxSelEnd.mx);
        const by2=Math.max(this._boxSelStart.my,this._boxSelEnd.my);
        // Only treat as box select if dragged more than 4px
        if(bx2-bx1>4||by2-by1>4){
          this.selectedComps=[];this.selectedComp=null;
          for(const c of(this.board?.components||[])){
            const cx=this.mmX(c.x),cy=this.mmY(c.y);
            if(cx>=bx1&&cx<=bx2&&cy>=by1&&cy<=by2) this.selectedComps.push(c);
          }
        }
        this._isBoxSel=false;this._boxSelStart=null;this._boxSelEnd=null;
        this.render();
        if(typeof updateInfoPanel==='function')updateInfoPanel();
        return;
      }
      if(this._isAreaDrag&&this.areaStart&&this.board){
        const{mx,my}=this._cp(e);
        const ex=this.snap(this.cX(mx)),ey=this.snap(this.cY(my));
        if(Math.abs(ex-this.areaStart.x)>this.gridSize||Math.abs(ey-this.areaStart.y)>this.gridSize){
          (this.board.areas||(this.board.areas=[])).push({
            id:'area_'+Date.now().toString(36),
            net:this.areaNet||'GND',
            layer:this.areaLayer||'F.Cu',
            x1:this.areaStart.x,y1:this.areaStart.y,x2:ex,y2:ey
          });
          this._snapshot();
          setTimeout(renderAreasPanel,0);
        }
        this.areaStart=null;this._isAreaDrag=false;
        this.render();
      }
    });
    cv.addEventListener('contextmenu',e=>{e.preventDefault();this._showCtxMenu(e);});

    cv.addEventListener('wheel',e=>{
      e.preventDefault();
      const{mx,my}=this._cp(e);
      const f=e.deltaY>0?0.88:1.13;
      const old=this.scale;
      this.scale=Math.max(1,Math.min(400,this.scale*f));
      const r=this.scale/old;
      // Pivot at mouse: keep world point under cursor fixed after zoom
      // panX_new = (mx - offsetX)*(1 - r) + r*panX
      this.panX=(mx-this.offsetX)*(1-r)+r*this.panX;
      this.panY=(my-this.offsetY)*(1-r)+r*this.panY;
      this.render();
    },{passive:false});

    window.addEventListener('keydown',e=>{
      if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT')return;
      const k=e.key.toLowerCase();
      if(k==='s')setTool('select');
      else if(k==='w')setTool('route');   // W = trace
      else if(k==='r'&&(editor.selectedComp||editor.selectedComps?.length)){  // R = rotate selected (group-aware)
        editor.rotateSelGroup(90);
      }
      else if(k==='v')setTool('via');
      else if(k==='z'&&!e.ctrlKey&&!e.metaKey)setTool('zone');
      else if(k==='a')setTool('area');
      else if(k==='d'&&!e.ctrlKey&&!e.metaKey)setTool('draw');
      else if(k==='m')setTool('measure');
      else if(k==='f')editor.fitBoard();
      else if(k==='enter'&&editor.tool==='draw'&&editor.drawPoints.length>=2){
        // Enter key finishes a drawing (like double-click)
        e.preventDefault();
        const closeSel=document.getElementById('draw-close-chk');
        const closed=closeSel?closeSel.checked:false;
        if(editor.board){
          (editor.board.drawings||(editor.board.drawings=[])).push({
            id:'d'+Date.now(),layer:editor.drawLayer||'Edge.Cuts',
            width:editor.drawWidth||0.05,points:[...editor.drawPoints],closed});
          editor._snapshot();
        }
        editor.drawPoints=[];editor.render();
      }
      else if(k==='/'&&editor.tool==='route'&&editor.routePoints.length>0){
        e.preventDefault();
        editor._elbowMode=editor._elbowMode?0:1;
        editor._avoidCache=null;editor._avoidCacheKey=null; // invalidate cache
        editor.render();
      }
      else if(k==='escape'){
        // Close any open PCB modal first
        const pcbModals=['import-modal','boards-modal','layer-modal','drc-modal','gerber-modal'];
        for(const mid of pcbModals){const m=document.getElementById(mid);if(m&&m.classList.contains('open')){
          if(typeof closeModal==='function')closeModal(mid);return;
        }}
        editor.routePoints=[];editor.routeNet=null;
        editor.zonePoints=[];editor.measureStart=null;
        editor.areaStart=null;editor._isAreaDrag=false;
        editor.drawPoints=[];
        if(editor.tool!=='select'){
          // Any non-select tool: Esc returns to select
          setTool('select');
        } else {
          // In select mode: Esc clears selection
          editor.selectedComp=null;editor.selectedTrace=null;editor.selectedVia=null;
          editor.selectedPad=null;editor.selectedComps=[];editor.selectedDrawing=null;
          editor._hoverTrace=null;editor._hoverComp=null;editor._lastClickObj=null;
          editor.render();updateInfoPanel();
        }
      }
      else if(k==='g'&&(editor.selectedComp||editor.selectedComps?.length)){
        editor.rotateSelGroup(90);
      }
      else if((k==='delete'||k==='backspace')&&(editor.selectedComp||editor.selectedTrace||editor.selectedArea||editor.selectedVia||editor.selectedDrawing)){
        if(editor.selectedComp){
          const idx=editor.board.components.indexOf(editor.selectedComp);
          if(idx!==-1)editor.board.components.splice(idx,1);
          editor.selectedComp=null; rebuildCompList(); _notifyParentRefs();
        } else if(editor.selectedTrace){
          const idx=(editor.board.traces||[]).indexOf(editor.selectedTrace);
          if(idx!==-1)editor.board.traces.splice(idx,1);
          editor.selectedTrace=null;
        } else if(editor.selectedVia){
          const idx=(editor.board.vias||[]).indexOf(editor.selectedVia);
          if(idx!==-1)editor.board.vias.splice(idx,1);
          editor.selectedVia=null;
        } else if(editor.selectedArea){
          const idx=(editor.board.areas||[]).indexOf(editor.selectedArea);
          if(idx!==-1)editor.board.areas.splice(idx,1);
          editor.selectedArea=null; renderAreasPanel();
        } else if(editor.selectedDrawing){
          const idx=(editor.board.drawings||[]).indexOf(editor.selectedDrawing);
          if(idx!==-1)editor.board.drawings.splice(idx,1);
          editor.selectedDrawing=null;
        }
        editor._snapshot(); editor.render();updateInfoPanel();
      }
      else if((k==='z'&&(e.ctrlKey||e.metaKey))&&!e.shiftKey){
        e.preventDefault(); editor.undo();
      }
      else if((k==='z'&&(e.ctrlKey||e.metaKey)&&e.shiftKey)||(k==='y'&&(e.ctrlKey||e.metaKey))){
        e.preventDefault(); editor.redo();
      }
      else if(k==='g'&&(e.ctrlKey||e.metaKey)&&e.shiftKey){
        e.preventDefault(); editor.ungroupSelected();
      }
      else if(k==='g'&&(e.ctrlKey||e.metaKey)&&!e.shiftKey){
        e.preventDefault(); editor.groupSelected();
      }
    });
  }
}
