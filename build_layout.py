#!/usr/bin/env python3
import sys
import json
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding='utf-8')

# ── Load footprint ────────────────────────────────────────────────────────────
with open("C:/Users/jimi/auto-eda/frontend/static/pcb/footprints/TC358870XBG.json", encoding="utf-8") as f:
    footprint = json.load(f)

# pad lookup: number -> pad dict
pad_by_number = {p["number"]: p for p in footprint["pads"]}

# ── Load profile pin list ─────────────────────────────────────────────────────
with open("C:/Users/jimi/auto-eda/frontend/static/library/TC358870XBG/profile.json", encoding="utf-8") as f:
    profile = json.load(f)

# Build pin-number -> net name from profile pins list
# Profile has a "pins" list with "number" and "name" fields
pin_net = {}
for pin in profile.get("pins", []):
    pin_net[pin["number"]] = pin["name"]

# Fall back to footprint pad names for any missing
for p in footprint["pads"]:
    if p["number"] not in pin_net:
        pin_net[p["number"]] = p["name"]

print("Pin net mapping sample:", list(pin_net.items())[:5])

# ── IC position ───────────────────────────────────────────────────────────────
IC_X, IC_Y = 25.0, 25.0

# Build U1 pads from footprint
u1_pads = []
for p in footprint["pads"]:
    u1_pads.append({
        "number": p["number"],
        "x": p["x"],
        "y": p["y"],
        "type": p["type"],
        "shape": p["shape"],
        "size_x": p["size_x"],
        "size_y": p["size_y"],
        "net": pin_net.get(p["number"], p["name"])
    })

# ── Helper for cap / resistor pads ───────────────────────────────────────────
def passive_pads(net1, net2, dx=0.5):
    return [
        {"number":"1","x":-dx,"y":0.0,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.6,"net":net1},
        {"number":"2","x": dx,"y":0.0,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.6,"net":net2},
    ]

# ── Components ────────────────────────────────────────────────────────────────
components = [
    {"id":"U1","ref":"U1","slug":"TC358870XBG","value":"TC358870XBG",
     "x":IC_X,"y":IC_Y,"rotation":0,"layer":"F","pads":u1_pads},

    # Decoupling caps (left side)
    {"id":"C1","ref":"C1","slug":"CAPACITOR","value":"100nF","x":18.5,"y":22.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDD33_HDMI","GND")},
    {"id":"C2","ref":"C2","slug":"CAPACITOR_POL","value":"10uF","x":18.5,"y":23.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDD33_HDMI","GND")},
    {"id":"C3","ref":"C3","slug":"CAPACITOR","value":"100nF","x":18.5,"y":24.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDD11_HDMI","GND")},
    {"id":"C4","ref":"C4","slug":"CAPACITOR_POL","value":"10uF","x":18.5,"y":25.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDD11_HDMI","GND")},
    {"id":"C5","ref":"C5","slug":"CAPACITOR","value":"100nF","x":18.5,"y":26.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDC11","GND")},
    {"id":"C6","ref":"C6","slug":"CAPACITOR_POL","value":"1uF","x":18.5,"y":27.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDC11","GND")},

    # Decoupling caps (right side)
    {"id":"C7","ref":"C7","slug":"CAPACITOR","value":"100nF","x":31.5,"y":22.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDIO33","GND")},
    {"id":"C8","ref":"C8","slug":"CAPACITOR","value":"100nF","x":31.5,"y":23.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDIO18","GND")},
    {"id":"C9","ref":"C9","slug":"CAPACITOR_POL","value":"10uF","x":31.5,"y":24.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDIO18","GND")},
    {"id":"C10","ref":"C10","slug":"CAPACITOR","value":"100nF","x":31.5,"y":26.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDD12_MIPI0","GND")},
    {"id":"C11","ref":"C11","slug":"CAPACITOR","value":"100nF","x":31.5,"y":27.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDD12_MIPI1","GND")},

    # REXT resistor
    {"id":"R1","ref":"R1","slug":"RESISTOR","value":"2k 1%","x":18.5,"y":20.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDD33_HDMI","REXT")},

    # DDC pull-ups
    {"id":"R2","ref":"R2","slug":"RESISTOR","value":"1k","x":31.5,"y":18.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VCC5V","DDC_SCL")},
    {"id":"R3","ref":"R3","slug":"RESISTOR","value":"4.7k","x":31.5,"y":19.5,"rotation":0,"layer":"F",
     "pads":passive_pads("VCC5V","DDC_SDA")},

    # I2C pull-ups
    {"id":"R4","ref":"R4","slug":"RESISTOR","value":"4.7k","x":31.5,"y":29.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDIO18","I2C_SCL")},
    {"id":"R5","ref":"R5","slug":"RESISTOR","value":"4.7k","x":31.5,"y":30.0,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDIO18","I2C_SDA")},

    # INT pull-up
    {"id":"R6","ref":"R6","slug":"RESISTOR","value":"10k","x":31.5,"y":31.5,"rotation":0,"layer":"F",
     "pads":passive_pads("VDDIO18","INT")},
]

# ── Nets ──────────────────────────────────────────────────────────────────────
# Collect net -> [component.pad] references
net_pads = {}
def add_net(net, ref, pad_num):
    net_pads.setdefault(net, []).append(f"{ref}.{pad_num}")

for comp in components:
    for pad in comp["pads"]:
        if pad["net"] and pad["net"] not in ("NC", ""):
            add_net(pad["net"], comp["ref"], pad["number"])

nets = [{"name": net, "pads": pads} for net, pads in sorted(net_pads.items())]

# ── Traces ────────────────────────────────────────────────────────────────────
# Absolute position of an IC pad
def ic_pad_abs(pad_num):
    p = pad_by_number[pad_num]
    return round(IC_X + p["x"], 4), round(IC_Y + p["y"], 4)

# Component pad absolute position
def comp_pad_abs(comp_id, pad_num):
    comp = next(c for c in components if c["id"] == comp_id)
    pad = next(p for p in comp["pads"] if p["number"] == pad_num)
    return round(comp["x"] + pad["x"], 4), round(comp["y"] + pad["y"], 4)

def trace(net, sx, sy, ex, ey, width, layer="F.Cu"):
    return {"net":net,
            "start":{"x":round(sx,4),"y":round(sy,4)},
            "end":{"x":round(ex,4),"y":round(ey,4)},
            "width":width,"layer":layer}

def l_trace(net, sx, sy, ex, ey, width, layer="F.Cu"):
    """L-shaped trace: horizontal then vertical"""
    segs = []
    if abs(sx - ex) > 0.001:
        segs.append(trace(net, sx, sy, ex, sy, width, layer))
    if abs(sy - ey) > 0.001:
        segs.append(trace(net, ex, sy, ex, ey, width, layer))
    return segs

traces = []

PW = 0.3  # power trace width
GND_VIA = (25.0, 33.0)  # GND via position

# ── VDD33_HDMI: B1 pad -> C1 pad1 (horizontal to x=19.0, then vertical to y=22) ──
# B1 footprint: x=-2.925, y=-2.275  → abs (22.075, 22.725)
# C1 pad1: abs (18.5-0.5=18.0, 22.0)
bx, by = ic_pad_abs("B1")
cx1, cy1 = comp_pad_abs("C1", "1")
traces += l_trace("VDD33_HDMI", bx, by, cx1, cy1, PW)

# G1 VDD33_HDMI: x=-2.925, y=0.975 → abs (22.075, 25.975)
# Connect G1 to C2 pad1 (18.0, 23.0)
gx, gy = ic_pad_abs("G1")
cx2, cy2 = comp_pad_abs("C2", "1")
traces += l_trace("VDD33_HDMI", gx, gy, cx2, cy2, PW)

# ── VDD11_HDMI: B2 -> C3, G2 -> C4 ──
# B2: x=-2.275, y=-2.275 → abs (22.725, 22.725)
bx2, by2 = ic_pad_abs("B2")
cx3, cy3 = comp_pad_abs("C3", "1")
traces += l_trace("VDD11_HDMI", bx2, by2, cx3, cy3, PW)

# G2: x=-2.275, y=0.975 → abs (22.725, 25.975)
gx2, gy2 = ic_pad_abs("G2")
cx4, cy4 = comp_pad_abs("C4", "1")
traces += l_trace("VDD11_HDMI", gx2, gy2, cx4, cy4, PW)

# ── VDDC11: C10 pad -> C5, K6 -> C6 ──
# C10 (footprint pad): x=2.925, y=-1.625 → abs (27.925, 23.375)
cx10, cy10 = ic_pad_abs("C10")
cx5, cy5 = comp_pad_abs("C5", "1")
traces += l_trace("VDDC11", cx10, cy10, cx5, cy5, PW)

# K6: x=0.325, y=2.925 → abs (25.325, 27.925)
kx6, ky6 = ic_pad_abs("K6")
cx6, cy6 = comp_pad_abs("C6", "1")
traces += l_trace("VDDC11", kx6, ky6, cx6, cy6, PW)

# ── VDDIO33: H2 -> C7 ──
# H2: x=-2.275, y=1.625 → abs (22.725, 26.625)
hx2, hy2 = ic_pad_abs("H2")
cx7, cy7 = comp_pad_abs("C7", "1")
traces += l_trace("VDDIO33", hx2, hy2, cx7, cy7, PW)

# ── VDDIO18: J7 -> C8, J7 -> C9 ──
# J7: x=0.975, y=2.275 → abs (25.975, 27.275)
jx7, jy7 = ic_pad_abs("J7")
cx8, cy8 = comp_pad_abs("C8", "1")
cx9, cy9 = comp_pad_abs("C9", "1")
traces += l_trace("VDDIO18", jx7, jy7, cx8, cy8, PW)
traces += l_trace("VDDIO18", jx7, jy7, cx9, cy9, PW)

# ── VDD12_MIPI0: J10 -> C10 cap ──
# J10: x=2.925, y=2.275 → abs (27.925, 27.275)
jx10, jy10 = ic_pad_abs("J10")
cxc10, cyc10 = comp_pad_abs("C10", "1")
traces += l_trace("VDD12_MIPI0", jx10, jy10, cxc10, cyc10, PW)

# ── VDD12_MIPI1: B10 -> C11 ──
# B10: x=2.925, y=-2.275 → abs (27.925, 22.725)
bx10, by10 = ic_pad_abs("B10")
cxc11, cyc11 = comp_pad_abs("C11", "1")
traces += l_trace("VDD12_MIPI1", bx10, by10, cxc11, cyc11, PW)

# ── GND traces: all cap pad2 -> GND via at (25, 33) ──
gnd_caps = ["C1","C2","C3","C4","C5","C6","C7","C8","C9","C10","C11"]
for cap_id in gnd_caps:
    px, py = comp_pad_abs(cap_id, "2")
    traces += l_trace("GND", px, py, GND_VIA[0], GND_VIA[1], PW)

# ── REXT: A1 pad -> R1 pad2 ──
# A1: x=-2.925, y=-2.925 → abs (22.075, 22.075)
ax1, ay1 = ic_pad_abs("A1")
rx1p2, ry1p2 = comp_pad_abs("R1", "2")
traces += l_trace("REXT", ax1, ay1, rx1p2, ry1p2, 0.2)

# ── VDD33_HDMI: R1 pad1 -> C1 pad1 (shared VDD33_HDMI rail) ──
rx1p1, ry1p1 = comp_pad_abs("R1", "1")
traces += l_trace("VDD33_HDMI", rx1p1, ry1p1, cx1, cy1, PW)

# ── DDC: IC A3(DDC_SCL)->R2 pad2, B3(DDC_SDA)->R3 pad2 ──
# A3: x=-1.625, y=-2.925 → abs (23.375, 22.075)
a3x, a3y = ic_pad_abs("A3")
r2p2x, r2p2y = comp_pad_abs("R2", "2")
traces += l_trace("DDC_SCL", a3x, a3y, r2p2x, r2p2y, 0.2)

# B3: x=-1.625, y=-2.275 → abs (23.375, 22.725)
b3x, b3y = ic_pad_abs("B3")
r3p2x, r3p2y = comp_pad_abs("R3", "2")
traces += l_trace("DDC_SDA", b3x, b3y, r3p2x, r3p2y, 0.2)

# ── I2C: IC K4(I2C_SCL)->R4 pad2, K3(I2C_SDA)->R5 pad2 ──
# K4: x=-0.975, y=2.925 → abs (24.025, 27.925)
k4x, k4y = ic_pad_abs("K4")
r4p2x, r4p2y = comp_pad_abs("R4", "2")
traces += l_trace("I2C_SCL", k4x, k4y, r4p2x, r4p2y, 0.2)

# K3: x=-1.625, y=2.925 → abs (23.375, 27.925)
k3x, k3y = ic_pad_abs("K3")
r5p2x, r5p2y = comp_pad_abs("R5", "2")
traces += l_trace("I2C_SDA", k3x, k3y, r5p2x, r5p2y, 0.2)

# ── INT: IC J3(INT)->R6 pad2 ──
# J3: x=-1.625, y=2.275 → abs (23.375, 27.275)
j3x, j3y = ic_pad_abs("J3")
r6p2x, r6p2y = comp_pad_abs("R6", "2")
traces += l_trace("INT", j3x, j3y, r6p2x, r6p2y, 0.2)

# ── Vias ─────────────────────────────────────────────────────────────────────
vias = [
    {"x": GND_VIA[0], "y": GND_VIA[1], "drill": 0.3, "size": 0.6,
     "net": "GND", "layers": ["F.Cu", "B.Cu"]}
]

# ── Assemble layout ───────────────────────────────────────────────────────────
layout = {
    "board": {"width": 50.0, "height": 50.0},
    "components": components,
    "nets": nets,
    "traces": traces,
    "vias": vias
}

print(f"Components: {len(components)}")
print(f"Nets: {len(nets)}")
print(f"Traces: {len(traces)}")
print(f"Vias: {len(vias)}")
print(f"U1 pads: {len(u1_pads)}")

# ── PUT layout_example ────────────────────────────────────────────────────────
body = json.dumps(layout).encode("utf-8")
req = urllib.request.Request(
    "http://localhost:8000/api/library/TC358870XBG/layout_example",
    data=body,
    method="PUT",
    headers={"Content-Type": "application/json"}
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"\nPUT layout_example → {resp.status} {resp.reason}")
        resp_body = resp.read().decode("utf-8")
        print("Response:", resp_body[:500])
except urllib.error.HTTPError as e:
    print(f"\nPUT layout_example → HTTP {e.code} {e.reason}")
    print("Error body:", e.read().decode("utf-8")[:1000])
except Exception as e:
    print(f"\nPUT layout_example failed: {e}")

# ── Mark gen-ticket #25 as done ───────────────────────────────────────────────
ticket_body = json.dumps({"status": "done"}).encode("utf-8")
req2 = urllib.request.Request(
    "http://localhost:8000/api/gen-tickets/25",
    data=ticket_body,
    method="PUT",
    headers={"Content-Type": "application/json"}
)
try:
    with urllib.request.urlopen(req2, timeout=30) as resp:
        print(f"\nPUT gen-ticket #25 → {resp.status} {resp.reason}")
        resp_body = resp.read().decode("utf-8")
        print("Response:", resp_body[:500])
except urllib.error.HTTPError as e:
    print(f"\nPUT gen-ticket #25 → HTTP {e.code} {e.reason}")
    print("Error body:", e.read().decode("utf-8")[:500])
except Exception as e:
    print(f"\nPUT gen-ticket #25 failed: {e}")

print("\nDone.")
