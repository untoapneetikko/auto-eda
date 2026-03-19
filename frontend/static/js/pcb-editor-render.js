// ═══════════════════════════════════════════════════════════════
// PCBEditor — Rendering Methods (prototype mixin)
// Split from pcb-editor.js for maintainability.
// Loaded AFTER pcb-editor.js which defines the PCBEditor class.
// All _draw* methods and _renderNow are defined here.
// ═══════════════════════════════════════════════════════════════

PCBEditor.prototype._renderNow = function(){
    const ctx=this.ctx,W=this.canvas.width,H=this.canvas.height;
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle='#080808'; ctx.fillRect(0,0,W,H);
    if(!this.board){
      ctx.fillStyle='rgba(255,255,255,0.1)'; ctx.font='14px system-ui';
      ctx.textAlign='center';
      ctx.fillText('Import a schematic project or load PCB JSON to begin',W/2,H/2-8);
      ctx.font='12px system-ui'; ctx.fillStyle='rgba(255,255,255,0.05)';
      ctx.fillText('Click "Import Schematic" or "\u{1F4A1} Example" in the toolbar',W/2,H/2+14);
      ctx.textAlign='left'; return;
    }
    if(this.layers['Edge.Cuts'].visible && !this.hideBoardOutline) this._drawBoard();
    this._drawGrid();
    // Work layer: active layer is rendered on top at full alpha; others dimmed
    const wl=this.workLayer||'F.Cu';
    const wIsCu=wl.endsWith('.Cu');
    const wSide=(wl==='B.Cu')?'B':'F'; // copper side for work layer (default F)
    const otherSilk=wSide==='F'?'B.SilkS':'F.SilkS';
    const workSilk=wSide==='F'?'F.SilkS':'B.SilkS';
    const _safe=(fn)=>{try{fn();}catch(e){console.error('[PCBEditor render]',e);}};
    // Collect all copper layer keys
    const allCu=Object.keys(this.layers).filter(k=>k.endsWith('.Cu'));
    if(wIsCu){
      // Draw non-work copper layers at reduced alpha
      this.ctx.globalAlpha=0.45;
      for(const cu of allCu){
        if(cu===wl)continue;
        _safe(()=>{if(this.layers[cu]?.visible) this._drawTraces(cu);});
      }
      _safe(()=>{if(this.layers[otherSilk]?.visible) this._drawSilk(wSide==='F'?'B':'F');});
      _safe(()=>this._drawPads(wSide==='F'?'B':'F'));
      this.ctx.globalAlpha=1.0;
      // Zones & areas (board-wide)
      _safe(()=>this._drawZones());
      _safe(()=>this._drawAreas());
      // Work layer copper + pads + silk on top at full alpha
      this.ctx.globalAlpha=1.0;
      _safe(()=>{if(this.layers[wl]?.visible) this._drawTraces(wl);});
      _safe(()=>this._drawPads(wSide));
      _safe(()=>{if(this.layers[workSilk]?.visible) this._drawSilk(wSide);});
      // Vias rendered last — multilayer, always on top of everything
      _safe(()=>this._drawVias());
    } else {
      // Non-copper work layer: draw everything normally
      for(const cu of allCu){
        _safe(()=>{if(this.layers[cu]?.visible) this._drawTraces(cu);});
      }
      _safe(()=>this._drawZones()); _safe(()=>this._drawAreas());
      this.ctx.globalAlpha=1.0;
      _safe(()=>this._drawPads());
      _safe(()=>{if(this.layers['B.SilkS']?.visible) this._drawSilk('B');});
      _safe(()=>{if(this.layers['F.SilkS']?.visible) this._drawSilk('F');});
      _safe(()=>this._drawVias());
    }
    if(this.layers['Ratsnest'].visible) this._drawRatsnest();
    this._drawDRCMarkers();
    this._drawGroups();
    this._drawDrawings();
    this._drawTexts();
    if(this.tool==='text') this._drawTextPreview();
    if(this.tool==='route'&&this.routePoints.length>0) this._drawActiveRoute();
    if(this.tool==='route') this._drawSnapIndicator();
    if(this.tool==='zone'&&this.zonePoints.length>0) this._drawActiveZone();
    if(this.tool==='measure'&&this.measureStart) this._drawMeasure();
    if(this.tool==='area'&&this.areaStart) this._drawActiveArea();
    if(this.tool==='draw'&&this.drawPoints.length>0) this._drawActiveDrawing();
    // Via ghost preview when in via tool — full-size preview follows cursor
    if(this.tool==='via'){
      const gx=this.mmX(this._mx),gy=this.mmY(this._my);
      const gor=Math.max(4,(DR.viaSize||1.0)/2*this.scale);
      const gir=Math.max(1.5,(DR.viaDrill||0.6)/2*this.scale);
      // Outer copper ring
      ctx.globalAlpha=0.75;
      ctx.fillStyle='#88aacc';ctx.beginPath();ctx.arc(gx,gy,gor,0,Math.PI*2);ctx.fill();
      // White border ring
      ctx.strokeStyle='rgba(255,255,255,0.6)';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(gx,gy,gor,0,Math.PI*2);ctx.stroke();
      // Drill hole
      ctx.fillStyle='#0a0a0a';ctx.beginPath();ctx.arc(gx,gy,gir,0,Math.PI*2);ctx.fill();
      // Crosshair inside
      if(gor>=4){
        ctx.strokeStyle='rgba(255,255,255,0.5)';ctx.lineWidth=0.5;
        ctx.beginPath();ctx.moveTo(gx-gir*0.6,gy);ctx.lineTo(gx+gir*0.6,gy);ctx.stroke();
        ctx.beginPath();ctx.moveTo(gx,gy-gir*0.6);ctx.lineTo(gx,gy+gir*0.6);ctx.stroke();
      }
      // Dashed selection ring to make it really visible
      ctx.strokeStyle='#6c63ff';ctx.lineWidth=1.5;ctx.setLineDash([3,2]);
      ctx.beginPath();ctx.arc(gx,gy,gor+4,0,Math.PI*2);ctx.stroke();ctx.setLineDash([]);
      ctx.globalAlpha=1.0;
    }
    if(this._hoverComp&&!this.selectedComps.includes(this._hoverComp)) this._drawHoverComp(this._hoverComp);
    for(const sc of this.selectedComps) this._drawSel(sc);
    if(this.selectedComp&&!this.selectedComps.includes(this.selectedComp)) this._drawSel(this.selectedComp);
    if(this.selectedVia) this._drawSelVia(this.selectedVia);
    if(this.selectedPad) this._drawSelPad(this.selectedPad);
    if(this._routeError){
      ctx.fillStyle='rgba(239,68,68,0.92)';ctx.font='bold 12px monospace';
      ctx.textAlign='center';
      ctx.fillText('\u2298'+' '+this._routeError,W/2,H-20);
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
      ctx.fillText(`\u26A0 Clearance violation (${this._traceDragViolations.length})`,W/2,H-20);
      ctx.textAlign='left';
      ctx.restore();
    }
    // Component-drag violation markers (trace crossing foreign-net pad)
    if(this._isDrag&&this._compDragViolations&&this._compDragViolations.length>0){
      ctx.save(); ctx.setLineDash([]);
      for(const v of this._compDragViolations){
        const cx=this.mmX(v.x),cy=this.mmY(v.y);
        const r=Math.max(8,(Math.max(v.pad?.size_x||1.6,v.pad?.size_y||1.6)/2)*this.scale+4);
        ctx.strokeStyle='rgba(239,68,68,0.9)'; ctx.lineWidth=2.5;
        ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(cx-r*0.6,cy-r*0.6); ctx.lineTo(cx+r*0.6,cy+r*0.6); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(cx+r*0.6,cy-r*0.6); ctx.lineTo(cx-r*0.6,cy+r*0.6); ctx.stroke();
      }
      ctx.fillStyle='rgba(239,68,68,0.92)'; ctx.font='bold 12px monospace';
      ctx.textAlign='center';
      ctx.fillText(`\u26A0 Trace crosses foreign pad (${this._compDragViolations.length})`,W/2,H-20);
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
};

PCBEditor.prototype._drawBoard = function(){
    const ctx=this.ctx,b=this.board.board;
    const ox=b.ox||0, oy=b.oy||0;
    const x=this.mmX(ox),y=this.mmY(oy),w=b.width*this.scale,h=b.height*this.scale;
    ctx.fillStyle='#0d1f0d'; ctx.fillRect(x,y,w,h);
    ctx.strokeStyle=this.layers['Edge.Cuts'].color;
    ctx.lineWidth=1.5; ctx.setLineDash([]); ctx.strokeRect(x,y,w,h);
    ctx.fillStyle='rgba(255,255,0,0.4)'; ctx.font='10px monospace'; ctx.textAlign='left';
    ctx.fillText(`${b.width.toFixed(1)}\u00D7${b.height.toFixed(1)}mm`,x+3,y+12);
    // Draw drag handles when Edge.Cuts is active layer
    if(this.workLayer==='Edge.Cuts'&&this.tool==='select'){
      const hs=5; // handle size px
      ctx.fillStyle='rgba(250,204,21,0.7)';
      // Corner handles
      for(const[hx,hy]of[[x,y],[x+w,y],[x,y+h],[x+w,y+h]]){
        ctx.fillRect(hx-hs,hy-hs,hs*2,hs*2);
      }
      // Edge midpoint handles
      ctx.fillStyle='rgba(250,204,21,0.4)';
      for(const[hx,hy]of[[x+w/2,y],[x+w/2,y+h],[x,y+h/2],[x+w,y+h/2]]){
        ctx.fillRect(hx-hs/2*1.5,hy-hs/2*1.5,hs*1.5,hs*1.5);
      }
    }
};

PCBEditor.prototype._drawGrid = function(){
    const ctx=this.ctx,b=this.board.board,s=this.gridSize;
    const ds=this.scale>=10?1:0.5;
    ctx.fillStyle='rgba(255,255,255,0.07)';
    for(let gx=0;gx<=b.width+s;gx+=s)
      for(let gy=0;gy<=b.height+s;gy+=s)
        ctx.fillRect(this.mmX(gx)-ds,this.mmY(gy)-ds,ds*2,ds*2);
};

PCBEditor.prototype._drawTraces = function(layer){
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
      // Tapered trace (widths[] array): render as smooth filled polygon
      if(tr.widths&&tr.widths.length===segs.length){
        const lc=this.layers[layer].color;
        const bright=this._lightenColor(lc,0.35);
        const N=segs.length;
        // Width at each of the N+1 polyline points
        const pts=[segs[0].start,...segs.map(s=>s.end)];
        const ptW=[tr.widths[0]];
        for(let i=1;i<N;i++) ptW.push((tr.widths[i-1]+tr.widths[i])/2);
        ptW.push(tr.widths[N-1]);
        // Canvas-space unit tangents per segment, then averaged at junctions
        const sTan=segs.map(s=>{
          const dx=this.mmX(s.end.x)-this.mmX(s.start.x);
          const dy=this.mmY(s.end.y)-this.mmY(s.start.y);
          const l=Math.hypot(dx,dy)||1; return{x:dx/l,y:dy/l};
        });
        const pTan=[sTan[0]];
        for(let i=1;i<N;i++){
          const tx=sTan[i-1].x+sTan[i].x,ty=sTan[i-1].y+sTan[i].y;
          const l=Math.hypot(tx,ty)||1; pTan.push({x:tx/l,y:ty/l});
        }
        pTan.push(sTan[N-1]);
        // Left/right outline: left normal = CW rotation of canvas tangent = (ty, -tx)
        const lP=[],rP=[];
        for(let i=0;i<=N;i++){
          const cx=this.mmX(pts[i].x),cy=this.mmY(pts[i].y);
          const t=pTan[i],hw=ptW[i]/2*this.scale;
          lP.push({x:cx+t.y*hw,y:cy-t.x*hw});
          rP.push({x:cx-t.y*hw,y:cy+t.x*hw});
        }
        const drawTaper=()=>{
          ctx.beginPath();
          ctx.moveTo(lP[0].x,lP[0].y);
          for(let i=1;i<=N;i++) ctx.lineTo(lP[i].x,lP[i].y);
          // End cap: arc from lP[N] forward around to rP[N]
          const ecx=this.mmX(pts[N].x),ecy=this.mmY(pts[N].y);
          const er=Math.max(0.5,ptW[N]/2*this.scale);
          let ea1=Math.atan2(lP[N].y-ecy,lP[N].x-ecx);
          let ea2=Math.atan2(rP[N].y-ecy,rP[N].x-ecx);
          if(ea2<ea1) ea2+=2*Math.PI;
          ctx.arc(ecx,ecy,er,ea1,ea2,false);
          // Right side backward
          for(let i=N-1;i>=0;i--) ctx.lineTo(rP[i].x,rP[i].y);
          // Start cap: arc from rP[0] backward around to lP[0]
          const scx=this.mmX(pts[0].x),scy=this.mmY(pts[0].y);
          const sr=Math.max(0.5,ptW[0]/2*this.scale);
          let sa1=Math.atan2(rP[0].y-scy,rP[0].x-scx);
          let sa2=Math.atan2(lP[0].y-scy,lP[0].x-scx);
          if(sa2<sa1) sa2+=2*Math.PI;
          ctx.arc(scx,scy,sr,sa1,sa2,false);
          ctx.closePath();
        };
        if(sel||isDragging){
          ctx.save();ctx.shadowBlur=12;ctx.shadowColor=bright;
          ctx.fillStyle=bright;drawTaper();ctx.fill();ctx.restore();
          ctx.fillStyle=bright+'55';drawTaper();ctx.fill();
        } else if(hov){
          ctx.fillStyle='rgba(255,255,255,0.12)';drawTaper();ctx.fill();
          ctx.fillStyle=lc+'cc';drawTaper();ctx.fill();
        } else {
          ctx.fillStyle=lc;drawTaper();ctx.fill();
        }
        continue;
      }
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
        // Extra highlight on the whole trace when dragging
        if(isDragging){
          const hasViolations=this._traceDragViolations.length>0;
          ctx.strokeStyle=hasViolations?'rgba(239,68,68,0.5)':'rgba(108,255,108,0.4)';
          ctx.lineWidth=w+14; drawPath();
          ctx.strokeStyle=hasViolations?'#ef4444':'#6fff6f';
          ctx.lineWidth=w; drawPath();
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
};

PCBEditor.prototype._drawZones = function(){
    const ctx=this.ctx;
    for(const z of(this.board.zones||[])){
      if(z._hidden)continue;
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
      // Step 1: fill the zone polygon
      oc.beginPath();
      oc.moveTo(this.mmX(z.points[0].x),this.mmY(z.points[0].y));
      for(let i=1;i<z.points.length;i++)
        oc.lineTo(this.mmX(z.points[i].x),this.mmY(z.points[i].y));
      oc.closePath();
      oc.fillStyle=col+'33';
      oc.fill();
      // Step 2: cut clearance areas via destination-out
      oc.globalCompositeOperation='destination-out';
      oc.fillStyle='rgba(0,0,0,1)';
      oc.beginPath();
      // Pad clearances (only pads on the same layer as this zone)
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
      // Via clearances (vias span all layers, always apply)
      for(const v of(this.board?.vias||[])){
        if(v.net&&v.net===z.net)continue;
        if(v.x<zx1-cl||v.x>zx2+cl||v.y<zy1-cl||v.y>zy2+cl)continue;
        const r=((v.size||DR.viaSize||1.0)/2+cl)*this.scale;
        const vsx=this.mmX(v.x),vsy=this.mmY(v.y);
        oc.moveTo(vsx+r,vsy);
        oc.arc(vsx,vsy,r,0,Math.PI*2);
      }
      oc.fill(); // erase pad + via clearance shapes in one pass
      // Trace clearances (only traces on the same layer as this zone)
      oc.strokeStyle='rgba(0,0,0,1)';
      oc.lineCap='round';
      for(const tr of(this.board?.traces||[])){
        if(tr.net&&tr.net===z.net)continue;
        // Skip traces on different layer
        const trIsF=(tr.layer||'F.Cu').startsWith('F');
        if(trIsF!==zoneIsF)continue;
        const tw=Math.max(...(tr.widths&&tr.widths.length?tr.widths:[tr.width||tr.width_mm||DR.traceWidth||0.25]))+cl*2;
        oc.lineWidth=tw*this.scale;
        for(const seg of(tr.segments||[])){
          if(!seg||!seg.start||!seg.end)continue;
          oc.beginPath();
          oc.moveTo(this.mmX(seg.start.x),this.mmY(seg.start.y));
          oc.lineTo(this.mmX(seg.end.x),this.mmY(seg.end.y));
          oc.stroke();
        }
      }
      // Step 3: composite the zone tile onto the main canvas
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
};

PCBEditor.prototype._drawAreas = function(){
    const ctx=this.ctx;
    for(const a of(this.board?.areas||[])){
      if(a._hidden)continue;
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
      // Pad clearances (only pads on the same layer as this area)
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
      // Via clearances (vias span all layers, always apply)
      for(const v of(this.board?.vias||[])){
        if(v.net&&v.net===a.net)continue;
        if(v.x<x1-cl||v.x>x2+cl||v.y<y1-cl||v.y>y2+cl)continue;
        const r=((v.size||DR.viaSize||1.0)/2+cl)*this.scale;
        const vsx=this.mmX(v.x),vsy=this.mmY(v.y);
        oc.moveTo(vsx+r,vsy);
        oc.arc(vsx,vsy,r,0,Math.PI*2);
      }
      oc.fill();
      // Trace clearances (only traces on the same layer)
      oc.strokeStyle='rgba(0,0,0,1)';
      oc.lineCap='round';
      for(const tr of(this.board?.traces||[])){
        if(tr.net&&tr.net===a.net)continue;
        // Skip traces on different layer
        const trIsF=(tr.layer||'F.Cu').startsWith('F');
        if(trIsF!==areaIsF)continue;
        const tw=Math.max(...(tr.widths&&tr.widths.length?tr.widths:[tr.width||tr.width_mm||DR.traceWidth||0.25]))+cl*2;
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
};

PCBEditor.prototype._drawVias = function(){
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
};

PCBEditor.prototype._drawPads = function(sideFilter){
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
};

PCBEditor.prototype._drawSilk = function(side){
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
};

PCBEditor.prototype._drawRatsnest = function(){
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
};

PCBEditor.prototype._drawDRCMarkers = function(){
    if(!this._drcHighlight) return;
    const ctx = this.ctx;
    const h = this._drcHighlight;
    const elapsed = Date.now() - h.time;
    const pulse = 0.5 + 0.5*Math.sin(elapsed*0.008);
    const hx = this.mmX(h.x), hy = this.mmY(h.y);
    const isErr = !h.sev || h.sev==='ERROR';
    const col = isErr ? 'rgba(239,68,68,' : 'rgba(250,204,21,';
    ctx.save();
    // Pulsing outer ring
    const hr = Math.max(12, 2*this.scale);
    ctx.strokeStyle = col+(0.4+pulse*0.6)+')'; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(hx, hy, hr+pulse*6, 0, Math.PI*2); ctx.stroke();
    // Inner circle + X cross
    const r = Math.max(8, 1.2*this.scale);
    ctx.strokeStyle = col+'0.9)'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(hx, hy, r, 0, Math.PI*2); ctx.stroke();
    if(isErr){
      const d = r*0.6;
      ctx.beginPath(); ctx.moveTo(hx-d,hy-d); ctx.lineTo(hx+d,hy+d); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(hx+d,hy-d); ctx.lineTo(hx-d,hy+d); ctx.stroke();
    }
    // For UNCONNECTED: dashed line between the two pads
    if(h.x1!=null && h.y1!=null){
      ctx.strokeStyle = col+'0.5)'; ctx.lineWidth = 1.5;
      ctx.setLineDash([4,3]);
      ctx.beginPath();
      ctx.moveTo(this.mmX(h.x1),this.mmY(h.y1));
      ctx.lineTo(this.mmX(h.x2),this.mmY(h.y2));
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();
};

PCBEditor.prototype._drawGroups = function(){
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
};

PCBEditor.prototype._drawDrawings = function(){
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
};

PCBEditor.prototype._drawTexts = function(){
    const ctx=this.ctx;
    for(const t of(this.board?.texts||[])){
      const lyr=t.layer||'F.SilkS';
      if(!this.layers[lyr]?.visible)continue;
      const col=this.layers[lyr]?.color||'#cccccc';
      const sz=Math.max(1,(t.size||1)*this.scale);
      const isSel=t===this._selectedText;
      ctx.fillStyle=isSel?'#facc15':col;
      ctx.font=`${isSel?'bold ':''}${sz}px monospace`;
      ctx.textAlign='left'; ctx.textBaseline='top';
      const px=this.mmX(t.x), py=this.mmY(t.y);
      ctx.save();
      ctx.translate(px,py);
      if(t.rotation) ctx.rotate(-t.rotation*Math.PI/180);
      ctx.fillText(t.text||'',0,0);
      ctx.restore();
    }
};

PCBEditor.prototype._drawTextPreview = function(){
    const inp=document.getElementById('text-content-input');
    const txt=inp?.value;
    if(!txt)return;
    const lyrSel=document.getElementById('text-layer-sel');
    const lyr=lyrSel?.value||'F.SilkS';
    const col=this.layers[lyr]?.color||'#cccccc';
    const szMm=parseFloat(document.getElementById('text-size-input')?.value)||1.0;
    const sz=Math.max(1,szMm*this.scale);
    const ctx=this.ctx;
    ctx.globalAlpha=0.5;
    ctx.fillStyle=col;
    ctx.font=`${sz}px monospace`;
    ctx.textAlign='left'; ctx.textBaseline='top';
    ctx.fillText(txt,this.mmX(this._mx),this.mmY(this._my));
    ctx.globalAlpha=1.0;
};

PCBEditor.prototype._drawActiveRoute = function(){
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
    // Check if any segment crosses a foreign pad (warning, not blocking)
    let pathWarning=false, blockingPad=null;
    for(let i=0;i<avoidPath.length-1;i++){
      const hit=this._segHitsWrongPad(avoidPath[i].x,avoidPath[i].y,avoidPath[i+1].x,avoidPath[i+1].y,this.routeNet||'');
      if(hit){pathWarning=true;blockingPad=hit;break;}
    }
    // Validate angles: only angle violations actually block placement
    let angleBlocked=false;
    let prevDir=null;
    if(pts.length>=2){
      const p1=pts[pts.length-2],p2=pts[pts.length-1];
      prevDir=Math.atan2(p2.y-p1.y,p2.x-p1.x);
    }
    if(!this._validatePathAngles(avoidPath,prevDir)){angleBlocked=true;}
    // Store for use by click handler (null only if angle-blocked)
    this._lastAvoidPath=angleBlocked?null:avoidPath;

    const endPxX=this.mmX(ex),endPxY=this.mmY(ey);
    const col=netConflict?'#ef4444':pathWarning?'#f59e0b':netMatch?'#22c55e':layerCol;

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
      ctx.fillText('\u2715 '+(blockingPad.pad.net||'?'),bx+sz+4,by-2);
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
      ctx.fillText('\u2715 '+destNet,endPxX+8,endPxY+10);
    }
};

PCBEditor.prototype._drawActiveDrawing = function(){
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
};

PCBEditor.prototype._drawActiveArea = function(){
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
};

PCBEditor.prototype._drawActiveZone = function(){
    const ctx=this.ctx,pts=this.zonePoints;
    ctx.strokeStyle='#aaaaaa'; ctx.lineWidth=1; ctx.setLineDash([3,2]);
    ctx.beginPath();
    ctx.moveTo(this.mmX(pts[0].x),this.mmY(pts[0].y));
    for(let i=1;i<pts.length;i++)ctx.lineTo(this.mmX(pts[i].x),this.mmY(pts[i].y));
    ctx.lineTo(this.mmX(this._mx),this.mmY(this._my));
    ctx.stroke(); ctx.setLineDash([]);
};

PCBEditor.prototype._drawSnapIndicator = function(){
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
};

PCBEditor.prototype._drawMeasure = function(){
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
};

PCBEditor.prototype._drawHoverComp = function(comp){
    const bb=this._compBBox(comp); if(!bb)return;
    const m=1.2,ctx=this.ctx;
    ctx.strokeStyle='rgba(180,180,255,0.5)'; ctx.lineWidth=1.5; ctx.setLineDash([]);
    ctx.strokeRect(this.mmX(comp.x+bb.x1-m),this.mmY(comp.y+bb.y1-m),(bb.x2-bb.x1+m*2)*this.scale,(bb.y2-bb.y1+m*2)*this.scale);
    ctx.fillStyle='rgba(108,99,255,0.06)';
    ctx.fillRect(this.mmX(comp.x+bb.x1-m),this.mmY(comp.y+bb.y1-m),(bb.x2-bb.x1+m*2)*this.scale,(bb.y2-bb.y1+m*2)*this.scale);
};

PCBEditor.prototype._drawSel = function(comp){
    const bb=this._compBBox(comp); if(!bb)return;
    const m=1,ctx=this.ctx;
    ctx.strokeStyle='#6c63ff'; ctx.lineWidth=2; ctx.setLineDash([4,2]);
    ctx.strokeRect(this.mmX(comp.x+bb.x1-m),this.mmY(comp.y+bb.y1-m),(bb.x2-bb.x1+m*2)*this.scale,(bb.y2-bb.y1+m*2)*this.scale);
    ctx.setLineDash([]);
};

PCBEditor.prototype._drawSelVia = function(v){
    const ctx=this.ctx;
    const cx=this.mmX(v.x),cy=this.mmY(v.y);
    const or=Math.max(3,(v.size||DR.viaSize||1.0)/2*this.scale)+6;
    // Dashed selection ring (matches component selection style)
    ctx.strokeStyle='#6c63ff'; ctx.lineWidth=2; ctx.setLineDash([4,2]);
    ctx.beginPath(); ctx.arc(cx,cy,or,0,Math.PI*2); ctx.stroke();
    ctx.setLineDash([]);
    // Subtle fill highlight
    ctx.fillStyle='rgba(108,99,255,0.10)';
    ctx.beginPath(); ctx.arc(cx,cy,or,0,Math.PI*2); ctx.fill();
};

PCBEditor.prototype._drawSelPad = function({comp,pad}){
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
};
