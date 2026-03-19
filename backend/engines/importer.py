"""Schematic-to-PCB importer engine — converts schematic JSON into initial PCB board layout.

Pure algorithm: takes dicts + directory paths, returns PCB board dict.
No Redis, no endpoints, no filesystem writes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .netlist import ic_layout, _SYMDEFS, _rotate_offset


# ── Footprint matching ────────────────────────────────────────────────────────

# Cache of available footprint file stems, built lazily.
_FOOTPRINT_STEMS: dict[str, str] = {}
_FOOTPRINT_STEMS_DIR: Path | None = None


def _build_footprint_stems(footprints_dir: Path) -> None:
    """Populate _FOOTPRINT_STEMS from the footprints directory (once per dir)."""
    global _FOOTPRINT_STEMS, _FOOTPRINT_STEMS_DIR
    if _FOOTPRINT_STEMS_DIR == footprints_dir and _FOOTPRINT_STEMS:
        return
    _FOOTPRINT_STEMS = {p.stem.lower(): p.stem for p in footprints_dir.glob("*.json")}
    _FOOTPRINT_STEMS_DIR = footprints_dir


def _match_package_to_footprint(package_types: list[str], footprints_dir: Path) -> str | None:
    """Try to resolve a library profile's package_types list to a footprint stem."""
    _build_footprint_stems(footprints_dir)
    for pkg in package_types:
        pkg = pkg.strip()
        bare = re.sub(r'\s*\(.*?\)', '', pkg).strip()

        semantic: list[str] = []

        m = re.match(r'(\d+)-pin\s+DIP', bare, re.IGNORECASE)
        if m:
            semantic.append(f"DIP-{m.group(1)}")

        m = re.match(r'(\d+)-pad\s+TQFP', bare, re.IGNORECASE)
        if m:
            semantic.append(f"TQFP-{m.group(1)}")

        m = re.match(r'(\d+)-[Pp]ad\s+(?:\S+\s+)?QFN', bare, re.IGNORECASE)
        if not m:
            m = re.match(r'(\d+)-[Pp]ad.*?QFN', bare, re.IGNORECASE)
        if m:
            n = m.group(1)
            semantic.append(f"QFN-{n}")
            for stem in _FOOTPRINT_STEMS:
                if stem.startswith(f"qfn-{n}-"):
                    semantic.append(_FOOTPRINT_STEMS[stem])

        m = re.match(r'H(TSSOP-\d+)', bare, re.IGNORECASE)
        if m:
            semantic.append(m.group(1))

        m = re.match(r'(\d+)-lead\s+MCLP', bare, re.IGNORECASE)
        if m:
            semantic.append(f"MCLP-{m.group(1)}")

        m = re.match(r'SO-(\d+)$', bare, re.IGNORECASE)
        if m:
            semantic.append(f"SOIC-{m.group(1)}")

        candidates = [
            pkg,
            bare,
            pkg.split()[0] if pkg.split() else '',
            re.sub(r'[A-Za-z]+$', '', bare).strip(),
        ] + semantic

        for c in candidates:
            if c and c.lower() in _FOOTPRINT_STEMS:
                return _FOOTPRINT_STEMS[c.lower()]
    return None


def _pick_footprint(
    slug: str,
    sym_type: str,
    pin_count: int,
    package_types: list[str] | None = None,
    profile_footprint: str | None = None,
    *,
    footprints_dir: Path,
) -> dict | None:
    """Return footprint JSON dict for a schematic component, or None for power symbols."""
    slug_up = slug.upper()

    if sym_type in ("vcc", "gnd", "pwr", "power") or slug_up in ("GND", "VCC", "PWR", "POWER"):
        return None

    _build_footprint_stems(footprints_dir)

    fp_name: str | None = None
    if profile_footprint:
        fp_name = _FOOTPRINT_STEMS.get(profile_footprint.lower())

    GENERIC_SLUG_FP: dict[str, str] = {
        "RESISTOR":      "0402",
        "CAPACITOR":     "0402",
        "CAPACITOR_POL": "CAP-POL-5mm",
        "INDUCTOR":      "0603",
        "DIODE":         "DO-41",
        "LED":           "LED-5mm",
        "ZENER":         "SOD-123",
        "SCHOTTKY":      "SOD-123",
        "FUSE":          "0603",
        "FERRITE":       "0603",
        "CRYSTAL":       "0805",
        "TESTPOINT":     "0402",
    }
    if fp_name is None:
        fp_name = GENERIC_SLUG_FP.get(slug_up)

    if fp_name is None:
        fp_name = _match_package_to_footprint(package_types or [], footprints_dir)

    IC_SLUG_FP: dict[str, str] = {
        "AMS1117-3.3":    "SOT-223",
        "AP2112":         "SOT25",
        "ATMEGA328P":     "DIP-28",
        "BC547":          "TO-92",
        "BC557":          "TO-92",
        "DRV8833":        "TSSOP-16",
        "ESP32_WROOM_32": "DIP-40",
        "IRF540N":        "TO-220",
        "L298N":          "DIP-20",
        "LM358":          "DIP-8",
        "LM7805":         "TO-220",
        "NE555":          "DIP-8",
        "STM32F103C8":    "LQFP-48",
    }
    if fp_name is None:
        fp_name = IC_SLUG_FP.get(slug_up)

    SYMTYPE_FP: dict[str, str] = {
        "resistor":   "0402",
        "capacitor":  "0402",
        "inductor":   "0603",
        "diode":      "DO-41",
        "led":        "LED-5mm",
        "transistor": "SOT-23",
        "mosfet":     "SOT-23",
        "jfet":       "SOT-23",
        "zener":      "SOD-123",
        "schottky":   "SOD-123",
        "tvs":        "SOD-123",
        "ferrite":    "0603",
        "crystal":    "0805",
        "oscillator": "DIP-8",
        "switch":     "0402",
        "connector":  "DIP-8",
        "relay":      "DIP-8",
        "fuse":       "0603",
        "testpoint":  "0402",
    }
    if fp_name is None:
        fp_name = SYMTYPE_FP.get((sym_type or "").lower())

    if fp_name is None:
        if pin_count <= 8:
            fp_name = "DIP-8"
        elif pin_count <= 16:
            fp_name = "DIP-16"
        elif pin_count <= 20:
            fp_name = "DIP-20"
        elif pin_count <= 28:
            fp_name = "DIP-28"
        elif pin_count <= 40:
            fp_name = "DIP-40"
        else:
            fp_name = "LQFP-64"

    fp_path = footprints_dir / (fp_name + ".json")
    if fp_path.exists():
        try:
            return json.loads(fp_path.read_text("utf-8"))
        except Exception:
            pass
    return None


def import_schematic(
    project: dict,
    netlist: dict,
    board_w: float = 100.0,
    board_h: float = 80.0,
    *,
    library_dir: Path,
    footprints_dir: Path,
    pcb_boards_dir: Path | None = None,
) -> dict:
    """Convert a schematic project JSON into an initial PCB board layout.

    Parameters
    ----------
    project : schematic project JSON dict
    netlist : {net_name: ["R1.1", "C1.2", ...], ...} or {}
    board_w, board_h : board dimensions in mm
    library_dir : path to library directory
    footprints_dir : path to footprints directory
    pcb_boards_dir : path to PCB boards directory (for board version counting)
    """
    schematic_comps: list[dict] = project.get("components", [])
    bw = board_w
    bh = board_h

    # ── Board title ──────────────────────────────────────────────────────────
    project_name = project.get("name") or "Design"
    project_id   = project.get("id", "")
    existing_count = 0
    if project_id and pcb_boards_dir is not None:
        try:
            existing_count = sum(
                1 for f in pcb_boards_dir.glob("*.json")
                if json.loads(f.read_text("utf-8")).get("projectId") == project_id
            )
        except Exception:
            pass
    board_title = f"{project_name} - V{existing_count + 1}"

    # ── Scale schematic positions -> board mm ────────────────────────────────
    xs = [float(c["x"]) for c in schematic_comps if isinstance(c.get("x"), (int, float))]
    ys = [float(c["y"]) for c in schematic_comps if isinstance(c.get("y"), (int, float))]

    if xs and ys:
        schem_cx = sum(xs) / len(xs)
        schem_cy = sum(ys) / len(ys)
        schem_w  = max(max(xs) - min(xs), 1.0)
        schem_h  = max(max(ys) - min(ys), 1.0)
        margin   = 0.15
        scale_x  = (bw * (1 - 2 * margin)) / schem_w
        scale_y  = (bh * (1 - 2 * margin)) / schem_h
    else:
        schem_cx = schem_cy = 0.0
        scale_x  = scale_y  = 1.0

    board_cx = bw / 2.0
    board_cy = bh / 2.0

    # ── Build pad->net lookup ────────────────────────────────────────────────
    pad_net: dict[str, str] = {}
    for net_name, pads_list in netlist.items():
        if isinstance(pads_list, list):
            for p in pads_list:
                pad_net[str(p)] = net_name.upper()

    _passive_port_map = {
        "P1": "1", "P2": "2", "+": "1", "-": "2",
        "A": "1", "K": "2", "IN": "1", "OUT": "2",
        "B": "1", "C": "2", "E": "3",
        "G": "1", "D": "2", "S": "3",
    }

    def _build_port_to_pad(ref: str, slug: str, sym_type: str) -> dict[str, str]:
        m: dict[str, str] = {}
        profile_path = library_dir / slug.upper() / "profile.json"
        if profile_path.exists():
            try:
                prof = json.loads(profile_path.read_text("utf-8"))
                for pin in prof.get("pins", []):
                    name = str(pin.get("name", ""))
                    num = str(pin.get("number", ""))
                    if name and num:
                        m[name] = num
            except Exception:
                pass
        for pname, pnum in _passive_port_map.items():
            if pname not in m:
                m[pname] = pnum
        return m

    _refs_seen: dict[str, tuple[str, str]] = {}
    for sc in project.get("components", []):
        ref = str(sc.get("designator", sc.get("ref", sc.get("id", ""))))
        slug = str(sc.get("slug", sc.get("symType", "")))
        sym_type = str(sc.get("symType", ""))
        if ref:
            _refs_seen[ref] = (slug, sym_type)

    _extra: dict[str, str] = {}
    for pad_ref, net_name in pad_net.items():
        dot = pad_ref.rfind(".")
        if dot < 0:
            continue
        ref = pad_ref[:dot]
        port_name = pad_ref[dot + 1:]
        if ref not in _refs_seen:
            continue
        slug, sym_type = _refs_seen[ref]
        port_map = _build_port_to_pad(ref, slug, sym_type)
        pad_num = port_map.get(port_name)
        if pad_num and pad_num != port_name:
            _extra[f"{ref}.{pad_num}"] = net_name
    pad_net.update(_extra)

    # ── Pre-load layout_example data ─────────────────────────────────────────
    le_groups: dict[str, dict] = {}
    eg_slugs: dict[str, str] = {}
    for sc in schematic_comps:
        eg_id  = sc.get("_exampleGroupId")
        eg_slug = sc.get("_exampleSlug", "")
        if eg_id and eg_slug:
            eg_slugs[eg_id] = eg_slug.upper()

    for eg_id, eg_slug in eg_slugs.items():
        profile_path = library_dir / eg_slug / "profile.json"
        if not profile_path.exists():
            continue
        try:
            eg_profile = json.loads(profile_path.read_text("utf-8"))
        except Exception:
            continue

        le       = eg_profile.get("layout_example") or {}
        le_comps = le.get("components") or []
        ec_comps = (eg_profile.get("example_circuit") or {}).get("components") or []
        if not le_comps:
            continue

        ec_pos_to_ref: dict[tuple, str] = {}
        for ec in ec_comps:
            ex  = ec.get("x")
            ey  = ec.get("y")
            ref = str(ec.get("ref", ec.get("designator", "")))
            if ex is not None and ey is not None and ref:
                ec_pos_to_ref[(round(float(ex)), round(float(ey)))] = ref

        le_ref_to_pos: dict[str, tuple] = {}
        le_anchor_ref: str | None = None
        le_anchor_pos: tuple | None = None
        _PASSIVE_FPS = {"0201", "0402", "0603", "0805", "1206", "2010", "2512",
                        "do-41", "sod-123", "sod123", "led-5mm"}
        for lc in le_comps:
            le_ref  = str(lc.get("ref", lc.get("id", "")))
            le_x    = float(lc.get("x", 0))
            le_y    = float(lc.get("y", 0))
            le_rot  = int(lc.get("rotation", 0))
            le_ref_to_pos[le_ref] = (le_x, le_y, le_rot)
            if le_anchor_ref is None:
                fp_lower = str(lc.get("footprint", "")).lower()
                if fp_lower not in _PASSIVE_FPS:
                    le_anchor_ref = le_ref
                    le_anchor_pos = (le_x, le_y)
        if le_anchor_ref is None and le_comps:
            lc = le_comps[0]
            le_anchor_ref = str(lc.get("ref", lc.get("id", "")))
            le_anchor_pos = (float(lc.get("x", 0)), float(lc.get("y", 0)))

        if le_anchor_ref is None:
            continue

        le_groups[eg_id] = {
            "eg_slug":       eg_slug,
            "le_ref_to_pos": le_ref_to_pos,
            "le_anchor_ref": le_anchor_ref,
            "le_anchor_pos": le_anchor_pos,
            "ec_pos_to_ref": ec_pos_to_ref,
            "traces":        le.get("traces") or [],
            "vias":          le.get("vias")   or [],
        }

    # Pre-compute anchor PCB positions
    anchor_pcb_pos: dict[str, tuple] = {}
    for sc in schematic_comps:
        eg_id = sc.get("_exampleGroupId")
        if not eg_id or eg_id not in le_groups:
            continue
        sc_slug = str(sc.get("slug", sc.get("symType", ""))).upper()
        if sc_slug != le_groups[eg_id]["eg_slug"]:
            continue
        sx = float(sc.get("x", 0))
        sy = float(sc.get("y", 0))
        ax = board_cx + (sx - schem_cx) * scale_x
        ay = board_cy + (sy - schem_cy) * scale_y
        ax = round(max(5.0, min(bw - 5.0, ax)), 2)
        ay = round(max(5.0, min(bh - 5.0, ay)), 2)
        anchor_pcb_pos[eg_id] = (ax, ay)

    # ── Build net mappings for each example group ────────────────────────────
    for eg_id, eg_info in le_groups.items():
        le_ref_to_sc_ref: dict[str, str] = {}
        for sc in schematic_comps:
            if sc.get("_exampleGroupId") != eg_id:
                continue
            ex_x = sc.get("_exampleX")
            ex_y = sc.get("_exampleY")
            sc_ref = str(sc.get("ref", sc.get("designator", "")))
            if ex_x is not None and ex_y is not None and sc_ref:
                le_ref = eg_info["ec_pos_to_ref"].get(
                    (round(float(ex_x)), round(float(ex_y))))
                if le_ref:
                    le_ref_to_sc_ref[le_ref] = sc_ref
        eg_info["le_ref_to_sc_ref"] = le_ref_to_sc_ref

        le_pad_to_int_net: dict[str, str] = {}
        try:
            _le_profile = json.loads(
                (library_dir / eg_info["eg_slug"] / "profile.json").read_text("utf-8"))
            le_comps_raw = (_le_profile.get("layout_example") or {}).get("components") or []
            le_nets_raw  = (_le_profile.get("layout_example") or {}).get("nets") or []
        except Exception:
            le_comps_raw = []
            le_nets_raw  = []

        for lc in le_comps_raw:
            lc_ref = str(lc.get("ref", lc.get("id", "")))
            for p in lc.get("pads", []):
                key = f'{lc_ref}.{p.get("number", "")}'
                le_pad_to_int_net[key] = str(p.get("net", ""))

        le_canonical_to_sc_net: dict[str, str] = {}
        le_int_net_to_canonical: dict[str, str] = {}
        for le_net in le_nets_raw:
            canonical = str(le_net.get("name", ""))
            for le_pad_ref in le_net.get("pads", []):
                int_net = le_pad_to_int_net.get(le_pad_ref, "")
                if int_net and canonical:
                    le_int_net_to_canonical.setdefault(int_net, canonical)

                parts = le_pad_ref.split(".", 1)
                if len(parts) == 2:
                    sc_ref = le_ref_to_sc_ref.get(parts[0])
                    if sc_ref:
                        sc_net = pad_net.get(f"{sc_ref}.{parts[1]}", "")
                        if sc_net and canonical not in le_canonical_to_sc_net:
                            le_canonical_to_sc_net[canonical] = sc_net

        le_int_net_to_sc: dict[str, str] = {}
        for int_net, canonical in le_int_net_to_canonical.items():
            sc_net = le_canonical_to_sc_net.get(canonical, canonical)
            le_int_net_to_sc[int_net] = sc_net
        for canonical, sc_net in le_canonical_to_sc_net.items():
            le_int_net_to_sc[canonical] = sc_net

        eg_info["le_int_net_to_sc"]   = le_int_net_to_sc
        eg_info["le_canonical_to_sc"] = le_canonical_to_sc_net
        eg_info["le_nets_raw"]        = le_nets_raw

    # ── Build PCB components ─────────────────────────────────────────────────
    pcb_comps: list[dict] = []
    for sc in schematic_comps:
        slug     = str(sc.get("slug", sc.get("symType", ""))).upper()
        sym_type = str(sc.get("symType", "")).lower()
        ref      = str(sc.get("ref", sc.get("designator", sc.get("id", ""))))
        value    = str(sc.get("value", ""))

        if sym_type in ("vcc", "gnd", "pwr", "power") or slug in ("GND", "VCC", "PWR", "POWER"):
            continue
        if not ref:
            continue

        profile: dict = {}
        profile_path = library_dir / slug / "profile.json"
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text("utf-8"))
            except Exception:
                pass

        pins: list[dict] = profile.get("pins", [])
        pin_count = len(pins)
        package_types: list[str] = profile.get("package_types", [])
        profile_footprint: str | None = profile.get("footprint") or None
        pin_by_num: dict[str, str] = {
            str(p.get("number", "")): str(p.get("name", "")) for p in pins
        }

        fp_data = _pick_footprint(slug, sym_type, pin_count, package_types, profile_footprint,
                                  footprints_dir=footprints_dir)

        pads: list[dict] = []
        if fp_data:
            for fp_pad in fp_data.get("pads", []):
                pad_num  = str(fp_pad.get("number", ""))
                pad_name = pin_by_num.get(pad_num, pad_num)
                pad_key  = f"{ref}.{pad_num}"
                pad_entry: dict = {
                    "number": pad_num,
                    "name":   pad_name,
                    "x":      float(fp_pad.get("x", 0)),
                    "y":      float(fp_pad.get("y", 0)),
                    "type":   fp_pad.get("type",  "smd"),
                    "shape":  fp_pad.get("shape", "rect"),
                    "size_x": float(fp_pad.get("size_x", 0.6)),
                    "size_y": float(fp_pad.get("size_y", 0.6)),
                    "net":    pad_net.get(pad_key, ""),
                }
                if "drill" in fp_pad:
                    pad_entry["drill"] = fp_pad["drill"]
                pads.append(pad_entry)
        elif pins:
            half = (pin_count - 1) / 2.0
            for i, pin in enumerate(pins):
                pad_num = str(pin.get("number", i + 1))
                pad_key = f"{ref}.{pad_num}"
                pads.append({
                    "number": pad_num,
                    "name":   str(pin.get("name", pad_num)),
                    "x":      round((i - half) * 2.54, 3),
                    "y":      0.0,
                    "type":   "thru_hole",
                    "shape":  "circle",
                    "size_x": 1.6,
                    "size_y": 1.6,
                    "drill":  0.8,
                    "net":    pad_net.get(pad_key, ""),
                })
        else:
            for i, pad_num in enumerate(["1", "2"]):
                pad_key = f"{ref}.{pad_num}"
                pads.append({
                    "number": pad_num,
                    "name":   pad_num,
                    "x":      -0.5 + i * 1.0,
                    "y":      0.0,
                    "type":   "smd",
                    "shape":  "rect",
                    "size_x": 0.6,
                    "size_y": 0.6,
                    "net":    pad_net.get(pad_key, ""),
                })

        # ── Determine PCB position ───────────────────────────────────────────
        rotation = int(sc.get("rotation", 0))
        eg_id    = sc.get("_exampleGroupId")
        eg_info  = le_groups.get(eg_id) if eg_id else None

        if eg_info and eg_id in anchor_pcb_pos:
            is_anchor = (slug == eg_info["eg_slug"])
            if is_anchor:
                bx, by = anchor_pcb_pos[eg_id]
                le_anchor_ref = eg_info["le_anchor_ref"]
                if le_anchor_ref in eg_info["le_ref_to_pos"]:
                    rotation = eg_info["le_ref_to_pos"][le_anchor_ref][2]
            else:
                ex_x = sc.get("_exampleX")
                ex_y = sc.get("_exampleY")
                le_ref = None
                if ex_x is not None and ex_y is not None:
                    le_ref = eg_info["ec_pos_to_ref"].get(
                        (round(float(ex_x)), round(float(ex_y)))
                    )
                if le_ref and le_ref in eg_info["le_ref_to_pos"]:
                    le_x, le_y, le_rot = eg_info["le_ref_to_pos"][le_ref]
                    le_ax, le_ay       = eg_info["le_anchor_pos"]
                    ax, ay             = anchor_pcb_pos[eg_id]
                    bx = round(ax + (le_x - le_ax), 2)
                    by = round(ay + (le_y - le_ay), 2)
                    bx = round(max(2.0, min(bw - 2.0, bx)), 2)
                    by = round(max(2.0, min(bh - 2.0, by)), 2)
                    rotation = le_rot
                else:
                    sx = float(sc.get("x", 0))
                    sy = float(sc.get("y", 0))
                    bx = board_cx + (sx - schem_cx) * scale_x
                    by = board_cy + (sy - schem_cy) * scale_y
                    bx = round(max(5.0, min(bw - 5.0, bx)), 2)
                    by = round(max(5.0, min(bh - 5.0, by)), 2)
        else:
            sx = float(sc.get("x", 0))
            sy = float(sc.get("y", 0))
            bx = board_cx + (sx - schem_cx) * scale_x
            by = board_cy + (sy - schem_cy) * scale_y
            bx = round(max(5.0, min(bw - 5.0, bx)), 2)
            by = round(max(5.0, min(bh - 5.0, by)), 2)

        comp_entry: dict = {
            "id":        ref,
            "ref":       ref,
            "value":     value,
            "footprint": fp_data.get("name", "") if fp_data else slug,
            "x":         bx,
            "y":         by,
            "rotation":  rotation,
            "layer":     "F",
            "pads":      pads,
        }
        if eg_id:
            comp_entry["groupId"] = eg_id
        pcb_comps.append(comp_entry)

    # ── Translate layout_example traces/vias ─────────────────────────────────
    pcb_traces: list[dict] = []
    pcb_vias:   list[dict] = []
    for eg_id, eg_info in le_groups.items():
        if eg_id not in anchor_pcb_pos:
            continue
        ax, ay    = anchor_pcb_pos[eg_id]
        le_ax, le_ay = eg_info["le_anchor_pos"]
        dx = ax - le_ax
        dy = ay - le_ay

        net_map = eg_info.get("le_int_net_to_sc", {})
        for trace in eg_info["traces"]:
            t = dict(trace)
            raw_net = t.get("net", "")
            t["net"] = net_map.get(raw_net, raw_net)
            t["groupId"] = eg_id
            if "x1" in t:
                t["x1"] = round(float(t["x1"]) + dx, 3)
                t["y1"] = round(float(t["y1"]) + dy, 3)
                t["x2"] = round(float(t["x2"]) + dx, 3)
                t["y2"] = round(float(t["y2"]) + dy, 3)
            if "points" in t:
                t["points"] = [
                    {"x": round(float(p["x"]) + dx, 3),
                     "y": round(float(p["y"]) + dy, 3)}
                    for p in t["points"]
                ]
            if "segments" in t:
                t["segments"] = [
                    {
                        "start": {"x": round(float(seg["start"]["x"]) + dx, 3),
                                  "y": round(float(seg["start"]["y"]) + dy, 3)},
                        "end":   {"x": round(float(seg["end"]["x"])   + dx, 3),
                                  "y": round(float(seg["end"]["y"])   + dy, 3)},
                    }
                    for seg in t["segments"]
                ]
            pcb_traces.append(t)

        for via in eg_info["vias"]:
            v = dict(via)
            v["x"]   = round(float(v.get("x", 0)) + dx, 3)
            v["y"]   = round(float(v.get("y", 0)) + dy, 3)
            raw_net  = v.get("net", "")
            v["net"] = net_map.get(raw_net, raw_net)
            v["groupId"] = eg_id
            pcb_vias.append(v)

    # ── Build nets list ──────────────────────────────────────────────────────
    _all_port_maps: dict[str, dict[str, str]] = {}
    for ref, (slug, sym_type) in _refs_seen.items():
        _all_port_maps[ref] = _build_port_to_pad(ref, slug, sym_type)

    def _translate_pad_ref(pad_ref: str) -> str:
        dot = pad_ref.rfind(".")
        if dot < 0:
            return pad_ref
        ref = pad_ref[:dot]
        port = pad_ref[dot + 1:]
        pm = _all_port_maps.get(ref, {})
        pad_num = pm.get(port, port)
        return f"{ref}.{pad_num}"

    pcb_nets_by_name: dict[str, set] = {}
    for net_name, pads_list in netlist.items():
        if isinstance(pads_list, list) and pads_list:
            translated = {_translate_pad_ref(str(p)) for p in pads_list}
            pcb_nets_by_name.setdefault(net_name, set()).update(translated)

    for eg_id, eg_info in le_groups.items():
        le_ref_to_sc_ref = eg_info.get("le_ref_to_sc_ref", {})
        canonical_to_sc  = eg_info.get("le_canonical_to_sc", {})
        for le_net in eg_info.get("le_nets_raw", []):
            canonical = str(le_net.get("name", ""))
            sc_net_name = canonical_to_sc.get(canonical, canonical)
            if not sc_net_name:
                continue
            for le_pad_ref in le_net.get("pads", []):
                parts = le_pad_ref.split(".", 1)
                if len(parts) == 2:
                    sc_ref = le_ref_to_sc_ref.get(parts[0])
                    if sc_ref:
                        pcb_nets_by_name.setdefault(sc_net_name, set()).add(
                            f"{sc_ref}.{parts[1]}")

    for comp in pcb_comps:
        ref = comp.get("ref", comp.get("id", ""))
        for pad in comp.get("pads", []):
            net = (pad.get("net") or "").strip()
            if net:
                pad_ref = f"{ref}.{pad.get('number', pad.get('name', ''))}"
                pcb_nets_by_name.setdefault(net, set()).add(pad_ref)

    pcb_nets: list[dict] = [
        {"name": name.upper(), "pads": sorted(pads)}
        for name, pads in pcb_nets_by_name.items()
        if pads
    ]

    # ── Build groups list ────────────────────────────────────────────────────
    pcb_groups_map: dict[str, dict] = {}
    for comp in pcb_comps:
        gid = comp.get("groupId")
        if gid:
            if gid not in pcb_groups_map:
                eg_info = le_groups.get(gid, {})
                pcb_groups_map[gid] = {
                    "id":      gid,
                    "name":    eg_info.get("eg_slug", gid),
                    "members": [],
                }
            pcb_groups_map[gid]["members"].append(comp["id"])
    pcb_groups = list(pcb_groups_map.values())

    layer_count = int(project.get("layerCount") or 2)

    return {
        "title":      board_title,
        "layerCount": layer_count,
        "board":      {"width": bw, "height": bh, "units": "mm"},
        "components": pcb_comps,
        "nets":       pcb_nets,
        "traces":     pcb_traces,
        "vias":       pcb_vias,
        "areas":      [],
        "groups":     pcb_groups,
    }
