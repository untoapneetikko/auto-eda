"""Design Rule Check engine — comprehensive DRC with x/y coordinates for violations.

Pure algorithm: takes board dict, returns violations dict. No filesystem access.
"""
from __future__ import annotations

import math


def run_drc(board: dict) -> dict:  # noqa: C901
    """Comprehensive Design Rule Check with x/y coordinates for each violation."""
    dr              = board.get("designRules", {})
    min_clearance   = float(dr.get("clearance", 0.2))
    min_trace_w     = float(dr.get("minTraceWidth", 0.15))
    edge_clearance  = float(dr.get("edgeClearance", 0.5))
    via_size_def    = float(dr.get("viaSize", 1.0))
    via_drill_def   = float(dr.get("viaDrill", 0.6))
    min_annular     = float(dr.get("minAnnularRing", 0.15))
    bw = float(board.get("board", {}).get("width",  board.get("width",  200)))
    bh = float(board.get("board", {}).get("height", board.get("height", 200)))

    violations: list[dict] = []
    comps  = board.get("components", [])
    traces = board.get("traces", [])
    vias_l = board.get("vias", [])
    nets   = board.get("nets", [])

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _pw(comp, pad):
        r = math.radians(float(comp.get("rotation", 0)))
        px, py = float(pad.get("x", 0)), float(pad.get("y", 0))
        return (round(px*math.cos(r)-py*math.sin(r)+float(comp.get("x",0)),4),
                round(px*math.sin(r)+py*math.cos(r)+float(comp.get("y",0)),4))

    def _ptsd(px, py, ax, ay, bx, by):
        dx, dy = bx-ax, by-ay
        lsq = dx*dx+dy*dy
        if lsq < 1e-8: return math.hypot(px-ax, py-ay)
        t = max(0.0, min(1.0, ((px-ax)*dx+(py-ay)*dy)/lsq))
        return math.hypot(px-(ax+t*dx), py-(ay+t*dy))

    def _ssd(a, b):
        ax1,ay1,ax2,ay2 = a; bx1,by1,bx2,by2 = b
        best, rx, ry = float('inf'), (ax1+bx1)/2, (ay1+by1)/2
        for px,py,sx1,sy1,sx2,sy2 in [
            (ax1,ay1,bx1,by1,bx2,by2),(ax2,ay2,bx1,by1,bx2,by2),
            (bx1,by1,ax1,ay1,ax2,ay2),(bx2,by2,ax1,ay1,ax2,ay2)]:
            d = _ptsd(px,py,sx1,sy1,sx2,sy2)
            if d < best: best,rx,ry = d,px,py
        for t in (0.25, 0.5, 0.75):
            mx2,my2 = ax1+t*(ax2-ax1), ay1+t*(ay2-ay1)
            d = _ptsd(mx2,my2,bx1,by1,bx2,by2)
            if d < best: best,rx,ry = d,mx2,my2
        return best, rx, ry

    def _pt_rect_dist(px, py, rx, ry, hw, hh, rot_rad):
        """Distance from point to rotated rectangle (center rx,ry, half-sizes hw,hh)."""
        cs, sn = math.cos(-rot_rad), math.sin(-rot_rad)
        lx = (px - rx)*cs - (py - ry)*sn
        ly = (px - rx)*sn + (py - ry)*cs
        cx = max(-hw, min(hw, lx))
        cy = max(-hh, min(hh, ly))
        return math.hypot(lx - cx, ly - cy)

    def _is_nc(net_name: str) -> bool:
        if not net_name:
            return False
        u = net_name.upper().strip()
        if u in ("NC", "N/C", "NO_CONNECT", "NOCONNECT"):
            return True
        if u.startswith("NC_") or u.startswith("NC-") or u.startswith("NC "):
            return True
        if u.startswith("NC(") or u.startswith("NC ("):
            return True
        return False

    # ── Build pad lookup ──────────────────────────────────────────────────────
    pad_lk: dict[str, dict] = {}
    all_pads: list[dict] = []
    for comp in comps:
        cref = comp.get("ref", comp.get("id", ""))
        clyr = "F" if comp.get("layer","F") in ("F","F.Cu") else "B"
        comp_rot = math.radians(float(comp.get("rotation", 0)))
        for pad in comp.get("pads", []):
            wx, wy = _pw(comp, pad)
            sx = float(pad.get("size_x", pad.get("width", 1.0)))
            sy = float(pad.get("size_y", pad.get("height", 1.0)))
            e = {"x":wx,"y":wy,"net":(pad.get("net","") or "").upper(),
                 "ref":cref,"pad":str(pad.get("number","")),
                 "name":(pad.get("name","") or "").upper(),
                 "layer":clyr,"th":pad.get("type","smd")=="through_hole",
                 "hw":sx/2,"hh":sy/2,"rot":comp_rot,
                 "r":max(sx,sy)/2}
            pad_lk[f"{cref}.{e['pad']}"] = e
            all_pads.append(e)

    # ── Flatten segments ──────────────────────────────────────────────────────
    all_segs: list[dict] = []
    for tr in traces:
        tn = (tr.get("net","") or "").upper(); tl = tr.get("layer","F.Cu")
        tw = float(tr.get("width",0.25))
        for seg in tr.get("segments", []):
            s,e2 = seg.get("start",{}), seg.get("end",{})
            all_segs.append({"x1":float(s.get("x",0)),"y1":float(s.get("y",0)),
                             "x2":float(e2.get("x",0)),"y2":float(e2.get("y",0)),
                             "net":tn,"layer":tl,"width":tw})

    # == 1: Trace width ==
    for sg in all_segs:
        if 0 < sg["width"] < min_trace_w:
            violations.append({"type":"TRACE_WIDTH","cat":"trace","sev":"ERROR",
                "msg":f"Trace '{sg['net']}' width {sg['width']:.3f}mm < min {min_trace_w}mm",
                "x":round((sg["x1"]+sg["x2"])/2,2),"y":round((sg["y1"]+sg["y2"])/2,2),"net":sg["net"]})

    # == 2: Out of bounds ==
    for sg in all_segs:
        for px,py in [(sg["x1"],sg["y1"]),(sg["x2"],sg["y2"])]:
            if px<0 or py<0 or px>bw or py>bh:
                violations.append({"type":"OUT_OF_BOUNDS","cat":"bounds","sev":"ERROR",
                    "msg":f"Trace '{sg['net']}' outside board at ({px:.2f},{py:.2f})",
                    "x":round(px,2),"y":round(py,2),"net":sg["net"]})
    for v in vias_l:
        vx,vy = float(v.get("x",0)),float(v.get("y",0))
        if vx<0 or vy<0 or vx>bw or vy>bh:
            violations.append({"type":"OUT_OF_BOUNDS","cat":"bounds","sev":"ERROR",
                "msg":f"Via outside board at ({vx:.2f},{vy:.2f})",
                "x":round(vx,2),"y":round(vy,2),"net":v.get("net","")})

    # == 3: Edge clearance ==
    for sg in all_segs:
        hw = sg["width"]/2
        for px,py in [(sg["x1"],sg["y1"]),(sg["x2"],sg["y2"])]:
            de = min(px, py, bw-px, bh-py) - hw
            if 0<=px<=bw and 0<=py<=bh and de < edge_clearance:
                violations.append({"type":"EDGE_CLEARANCE","cat":"clearance","sev":"WARNING",
                    "msg":f"Trace '{sg['net']}' {de:.2f}mm from edge (min {edge_clearance}mm)",
                    "x":round(px,2),"y":round(py,2),"net":sg["net"]})
                break

    # == 4: Unconnected nets ==
    npads: dict[str,list[dict]] = {}
    for ne in nets:
        nn = (ne.get("name","") or "").upper()
        if not nn or _is_nc(nn): continue
        pl = [pad_lk[str(pk)] for pk in ne.get("pads",[]) if str(pk) in pad_lk]
        if len(pl)>=2: npads[nn]=pl

    # ── Copper pour / zone connectivity ─────────────────────────────────────
    _zones = list(board.get("zones", []))
    for _a in board.get("areas", []):
        if _a.get("outline") and len(_a.get("outline", [])) >= 3:
            _zones.append({"layer": _a.get("layer", "F.Cu"), "net": _a.get("net", ""), "points": _a["outline"]})
        elif _a.get("net"):
            ax1 = min(float(_a.get("x1", 0)), float(_a.get("x2", bw)))
            ay1 = min(float(_a.get("y1", 0)), float(_a.get("y2", bh)))
            ax2 = max(float(_a.get("x1", 0)), float(_a.get("x2", bw)))
            ay2 = max(float(_a.get("y1", 0)), float(_a.get("y2", bh)))
            _zones.append({"layer": _a.get("layer", "F.Cu"), "net": _a.get("net", ""),
                           "points": [{"x":ax1,"y":ay1},{"x":ax2,"y":ay1},{"x":ax2,"y":ay2},{"x":ax1,"y":ay2}]})

    def _pt_in_poly(px, py, poly):
        n = len(poly)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = float(poly[i].get("x",0)), float(poly[i].get("y",0))
            xj, yj = float(poly[j].get("x",0)), float(poly[j].get("y",0))
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
                inside = not inside
            j = i
        return inside

    _pour_connected_nets: set[str] = set()
    for zone in _zones:
        znet = (zone.get("net", "") or "").upper()
        if not znet or znet not in npads:
            continue
        poly = zone.get("points", [])
        if len(poly) < 3:
            continue
        all_inside = all(_pt_in_poly(p["x"], p["y"], poly) for p in npads[znet])
        if all_inside:
            _pour_connected_nets.add(znet)

    EPS = 0.05
    for nn, pl in npads.items():
        if nn in _pour_connected_nets:
            continue
        n = len(pl)
        tn_nodes: list[tuple] = []; tp_arr: list[int] = []; ni_map: dict[str,int] = {}
        def _tf2(i, _tp=tp_arr):
            while _tp[i]!=i: _tp[i]=_tp[_tp[i]]; i=_tp[i]
            return i
        def _tu2(a,b, _tp=tp_arr): _tp[_tf2(a,_tp)]=_tf2(b,_tp)
        def _ga2(x,y, _ni=ni_map, _tn=tn_nodes, _tp=tp_arr):
            k=f"{x:.4f},{y:.4f}"
            if k not in _ni: _ni[k]=len(_tn); _tn.append((x,y)); _tp.append(len(_tn)-1)
            return _ni[k]
        for sg in all_segs:
            if sg["net"]!=nn: continue
            _tu2(_ga2(sg["x1"],sg["y1"]), _ga2(sg["x2"],sg["y2"]))
        for v in vias_l:
            if v.get("net")!=nn: continue
            _ga2(float(v.get("x",0)),float(v.get("y",0)))
        tot = n+len(tn_nodes)
        ap2 = list(range(tot))
        def _af2(i, _ap=ap2):
            while _ap[i]!=i: _ap[i]=_ap[_ap[i]]; i=_ap[i]
            return i
        def _aun2(a,b, _ap=ap2): _ap[_af2(a,_ap)]=_af2(b,_ap)
        for i in range(len(tn_nodes)):
            r2=_tf2(i)
            if r2!=i: _aun2(n+i,n+r2)
        for pi in range(n):
            for ti in range(len(tn_nodes)):
                if math.hypot(pl[pi]["x"]-tn_nodes[ti][0],pl[pi]["y"]-tn_nodes[ti][1])<=EPS:
                    _aun2(pi,n+ti)
        cl2: dict[int,list[int]] = {}
        for i in range(n): cl2.setdefault(_af2(i),[]).append(i)
        cls2 = list(cl2.values())
        if len(cls2)<=1: continue
        inm2=[False]*len(cls2); inm2[0]=True
        for _ in range(len(cls2)-1):
            bd2,ba2,bb2,bc3=float('inf'),-1,-1,-1
            for ci in range(len(cls2)):
                if not inm2[ci]: continue
                for cj in range(len(cls2)):
                    if inm2[cj]: continue
                    for ia in cls2[ci]:
                        for ib in cls2[cj]:
                            d2=math.hypot(pl[ia]["x"]-pl[ib]["x"],pl[ia]["y"]-pl[ib]["y"])
                            if d2<bd2: bd2,ba2,bb2,bc3=d2,ia,ib,cj
            if bc3!=-1: inm2[bc3]=True
            if ba2!=-1:
                pa,pb=pl[ba2],pl[bb2]
                violations.append({"type":"UNCONNECTED","cat":"unconnected","sev":"ERROR",
                    "msg":f"Net '{nn}': {pa['ref']}.{pa['pad']} \u2194 {pb['ref']}.{pb['pad']} not connected",
                    "x":round((pa["x"]+pb["x"])/2,2),"y":round((pa["y"]+pb["y"])/2,2),"net":nn,
                    "x1":round(pa["x"],2),"y1":round(pa["y"],2),
                    "x2":round(pb["x"],2),"y2":round(pb["y"],2)})

    # == 5: Copper clearance ==
    seen_p: set = set()
    for i,sa in enumerate(all_segs):
        for j in range(i+1,len(all_segs)):
            sb=all_segs[j]
            if sa["layer"]!=sb["layer"]: continue
            if sa["net"] and sb["net"] and sa["net"]==sb["net"]: continue
            mg=min_clearance+max(sa["width"],sb["width"])
            if (min(sa["x1"],sa["x2"])-mg>max(sb["x1"],sb["x2"]) or
                max(sa["x1"],sa["x2"])+mg<min(sb["x1"],sb["x2"]) or
                min(sa["y1"],sa["y2"])-mg>max(sb["y1"],sb["y2"]) or
                max(sa["y1"],sa["y2"])+mg<min(sb["y1"],sb["y2"])): continue
            d3,mx3,my3=_ssd((sa["x1"],sa["y1"],sa["x2"],sa["y2"]),
                            (sb["x1"],sb["y1"],sb["x2"],sb["y2"]))
            rq=min_clearance+sa["width"]/2+sb["width"]/2
            if d3<rq:
                pk2=(min(i,j),max(i,j))
                if pk2 in seen_p: continue
                seen_p.add(pk2)
                violations.append({"type":"CLEARANCE","cat":"clearance","sev":"ERROR",
                    "msg":f"Traces '{sa['net']}'/'{sb['net']}' clearance {d3:.3f}mm < {rq:.3f}mm",
                    "x":round(mx3,2),"y":round(my3,2)})

    # == 6: Trace-to-pad clearance ==
    def _pt_inside_pad(px, py, pad):
        return _pt_rect_dist(px, py, pad["x"], pad["y"], pad["hw"], pad["hh"], pad["rot"]) < 0.01

    def _clip_t_for_endpoint(sg, t_end, same_pads):
        ex = sg["x1"] + t_end*(sg["x2"]-sg["x1"])
        ey = sg["y1"] + t_end*(sg["y2"]-sg["y1"])
        best_t = t_end
        for sp in same_pads:
            if not _pt_inside_pad(ex, ey, sp):
                continue
            t_in, t_out = t_end, 1.0 - t_end
            for _ in range(12):
                t_mid = (t_in + t_out) / 2
                mx = sg["x1"] + (t_mid if t_end==0 else 1-t_mid)*(sg["x2"]-sg["x1"])
                my = sg["y1"] + (t_mid if t_end==0 else 1-t_mid)*(sg["y2"]-sg["y1"])
                if _pt_inside_pad(mx, my, sp):
                    t_in = t_mid
                else:
                    t_out = t_mid
            if t_end == 0:
                best_t = max(best_t, t_out)
            else:
                best_t = min(best_t, 1.0 - t_out)
        return best_t

    def _seg_rect_dist_clipped(sg, pad, same_net_pads):
        ax,ay,bx,by = sg["x1"],sg["y1"],sg["x2"],sg["y2"]
        dx,dy = bx-ax, by-ay
        lsq = dx*dx+dy*dy
        if lsq < 1e-8:
            return _pt_rect_dist(ax, ay, pad["x"], pad["y"], pad["hw"], pad["hh"], pad["rot"])
        t_lo = _clip_t_for_endpoint(sg, 0.0, same_net_pads)
        t_hi = _clip_t_for_endpoint(sg, 1.0, same_net_pads)
        if t_lo >= t_hi:
            return float('inf')
        t_closest = max(0.0, min(1.0, ((pad["x"]-ax)*dx+(pad["y"]-ay)*dy)/lsq))
        t_closest = max(t_lo, min(t_hi, t_closest))
        best = float('inf')
        for t in set([t_lo, t_closest, t_hi,
                      max(t_lo, t_closest-0.1), min(t_hi, t_closest+0.1)]):
            sx2 = ax + t*dx
            sy2 = ay + t*dy
            d = _pt_rect_dist(sx2, sy2, pad["x"], pad["y"], pad["hw"], pad["hh"], pad["rot"])
            if d < best: best = d
        return best

    net_to_pads: dict[str, list[dict]] = {}
    for pad in all_pads:
        if pad["net"]:
            net_to_pads.setdefault(pad["net"], []).append(pad)

    for sg in all_segs:
        sl="F" if sg["layer"].startswith("F") else "B"
        hw_tr = sg["width"] / 2
        same_pads = net_to_pads.get(sg["net"], [])
        for pad in all_pads:
            if not pad["th"] and pad["layer"]!=sl: continue
            if sg["net"] and pad["net"] and sg["net"]==pad["net"]: continue
            if _is_nc(pad["net"]) or _is_nc(pad.get("name","")): continue
            margin = min_clearance + hw_tr + pad["r"] + 0.5
            if (abs((sg["x1"]+sg["x2"])/2 - pad["x"]) > margin + abs(sg["x2"]-sg["x1"])/2 or
                abs((sg["y1"]+sg["y2"])/2 - pad["y"]) > margin + abs(sg["y2"]-sg["y1"])/2):
                continue
            best_d = _seg_rect_dist_clipped(sg, pad, same_pads)
            rq2 = min_clearance + hw_tr
            if best_d < rq2:
                violations.append({"type":"PAD_CLEARANCE","cat":"clearance","sev":"ERROR",
                    "msg":f"Trace '{sg['net']}' too close to {pad['ref']}.{pad['pad']} ('{pad['net']}') \u2014 {best_d:.3f}mm",
                    "x":round(pad["x"],2),"y":round(pad["y"],2)})

    # == 7: Via annular ring ==
    for v in vias_l:
        vs=float(v.get("size",via_size_def)); vd=float(v.get("drill",via_drill_def))
        ring=(vs-vd)/2
        if ring<min_annular:
            violations.append({"type":"ANNULAR_RING","cat":"via","sev":"ERROR",
                "msg":f"Via annular ring {ring:.3f}mm < min {min_annular}mm",
                "x":round(float(v.get("x",0)),2),"y":round(float(v.get("y",0)),2),
                "net":v.get("net","")})

    # == 8: Duplicate references ==
    rseen: dict[str,bool] = {}
    for comp in comps:
        ref=comp.get("ref",comp.get("id",""))
        if ref in rseen:
            violations.append({"type":"DUPLICATE_REF","cat":"refs","sev":"ERROR",
                "msg":f"Duplicate reference '{ref}'",
                "x":round(float(comp.get("x",0)),2),"y":round(float(comp.get("y",0)),2)})
        else: rseen[ref]=True

    # == 9: Unassigned pins ==
    asgn: set[str] = set()
    for ne in nets:
        for pk3 in ne.get("pads",[]): asgn.add(str(pk3))
    for pad in all_pads:
        pk4=f"{pad['ref']}.{pad['pad']}"
        if _is_nc(pad["net"]) or _is_nc(pad.get("name","")): continue
        if pk4 not in asgn and not pad["net"]:
            violations.append({"type":"UNASSIGNED_PIN","cat":"unconnected","sev":"WARNING",
                "msg":f"Pin {pk4} has no net assignment",
                "x":round(pad["x"],2),"y":round(pad["y"],2)})

    # == 10: Net conflict ==
    for i,pa in enumerate(all_pads):
        for j in range(i+1,len(all_pads)):
            pb=all_pads[j]
            if not pa["th"] and not pb["th"] and pa["layer"]!=pb["layer"]: continue
            if pa["net"] and pb["net"] and pa["net"]!=pb["net"] and not _is_nc(pa["net"]) and not _is_nc(pa.get("name","")) and not _is_nc(pb["net"]) and not _is_nc(pb.get("name","")):
                d_a2b = _pt_rect_dist(pa["x"],pa["y"],pb["x"],pb["y"],pb["hw"],pb["hh"],pb["rot"])
                d_b2a = _pt_rect_dist(pb["x"],pb["y"],pa["x"],pa["y"],pa["hw"],pa["hh"],pa["rot"])
                overlap_thresh = min(pa["hw"],pa["hh"],pb["hw"],pb["hh"]) * 0.5
                if d_a2b < overlap_thresh or d_b2a < overlap_thresh:
                    violations.append({"type":"NET_CONFLICT","cat":"net","sev":"ERROR",
                        "msg":f"{pa['ref']}.{pa['pad']} ({pa['net']}) / {pb['ref']}.{pb['pad']} ({pb['net']}) overlap",
                        "x":round((pa["x"]+pb["x"])/2,2),"y":round((pa["y"]+pb["y"])/2,2)})

    return {"violations": violations, "passed": len(violations) == 0}
