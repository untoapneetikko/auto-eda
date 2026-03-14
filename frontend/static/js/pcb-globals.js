'use strict';
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
// ═══════════════════════════════════════════════════════════════
// SYMDEFS — matches index.html exactly (for schematic import)
// ═══════════════════════════════════════════════════════════════
const SYMDEFS = {
  resistor:    {w:60, h:20,ports:[{dx:-30,dy:0,name:'P1'},{dx:30,dy:0,name:'P2'}]},
  capacitor:   {w:40, h:40,ports:[{dx:0,dy:-20,name:'P1'},{dx:0,dy:20,name:'P2'}]},
  capacitor_pol:{w:40,h:40,ports:[{dx:0,dy:-20,name:'+'},{dx:0,dy:20,name:'-'}]},
  inductor:    {w:80, h:20,ports:[{dx:-40,dy:0,name:'P1'},{dx:40,dy:0,name:'P2'}]},
  vcc:         {w:30, h:40,ports:[{dx:0,dy:15,name:'VCC'}]},
  gnd:         {w:30, h:30,ports:[{dx:0,dy:-15,name:'GND'}]},
  amplifier:   {w:100,h:80,ports:[{dx:-50,dy:0,name:'IN'},{dx:50,dy:0,name:'OUT'},{dx:0,dy:40,name:'GND'}]},
  opamp:       {w:100,h:80,ports:[{dx:-50,dy:-20,name:'+'},{dx:-50,dy:20,name:'-'},{dx:50,dy:0,name:'OUT'}]},
  diode:       {w:60, h:20,ports:[{dx:-30,dy:0,name:'A'},{dx:30,dy:0,name:'K'}]},
  led:         {w:60, h:20,ports:[{dx:-30,dy:0,name:'A'},{dx:30,dy:0,name:'K'}]},
  npn:         {w:60, h:70,ports:[{dx:-30,dy:0,name:'B'},{dx:20,dy:-25,name:'C'},{dx:20,dy:25,name:'E'}]},
  pnp:         {w:60, h:70,ports:[{dx:-30,dy:0,name:'B'},{dx:20,dy:-25,name:'E'},{dx:20,dy:25,name:'C'}]},
  nmos:        {w:60, h:70,ports:[{dx:-30,dy:0,name:'G'},{dx:20,dy:-25,name:'D'},{dx:20,dy:25,name:'S'}]},
  pmos:        {w:60, h:70,ports:[{dx:-30,dy:0,name:'G'},{dx:20,dy:-25,name:'D'},{dx:20,dy:25,name:'S'}]},
};

// ═══════════════════════════════════════════════════════════════
// PCB EXAMPLE (LM7805 regulator board)
// ═══════════════════════════════════════════════════════════════
const EXAMPLE_PCB = {"version":"1.0","title":"LM7805 5V Regulator","board":{"width":50,"height":35,"units":"mm"},"components":[{"id":"U1","ref":"U1","value":"LM7805","footprint":"TO-220","x":25,"y":17.5,"rotation":0,"layer":"F","pads":[{"number":"1","name":"INPUT","x":-2.54,"y":0,"type":"thru_hole","shape":"rect","size_x":1.8,"size_y":1.8,"drill":1.0,"net":"VIN"},{"number":"2","name":"GND","x":0,"y":0,"type":"thru_hole","shape":"circle","size_x":1.8,"size_y":1.8,"drill":1.0,"net":"GND"},{"number":"3","name":"OUTPUT","x":2.54,"y":0,"type":"thru_hole","shape":"circle","size_x":1.8,"size_y":1.8,"drill":1.0,"net":"VOUT"}]},{"id":"C1","ref":"C1","value":"330nF","footprint":"C_0805","x":15,"y":17.5,"rotation":0,"layer":"F","pads":[{"number":"1","name":"+","x":-1.0,"y":0,"type":"smd","shape":"rect","size_x":1.3,"size_y":1.5,"net":"VIN"},{"number":"2","name":"-","x":1.0,"y":0,"type":"smd","shape":"rect","size_x":1.3,"size_y":1.5,"net":"GND"}]},{"id":"C2","ref":"C2","value":"100nF","footprint":"C_0805","x":35,"y":17.5,"rotation":0,"layer":"F","pads":[{"number":"1","name":"+","x":-1.0,"y":0,"type":"smd","shape":"rect","size_x":1.3,"size_y":1.5,"net":"VOUT"},{"number":"2","name":"-","x":1.0,"y":0,"type":"smd","shape":"rect","size_x":1.3,"size_y":1.5,"net":"GND"}]},{"id":"J1","ref":"J1","value":"PWR_IN","footprint":"PinHeader_2.54","x":7,"y":17.5,"rotation":0,"layer":"F","pads":[{"number":"1","name":"VIN","x":0,"y":-1.27,"type":"thru_hole","shape":"rect","size_x":1.7,"size_y":1.7,"drill":1.0,"net":"VIN"},{"number":"2","name":"GND","x":0,"y":1.27,"type":"thru_hole","shape":"circle","size_x":1.7,"size_y":1.7,"drill":1.0,"net":"GND"}]},{"id":"J2","ref":"J2","value":"5V_OUT","footprint":"PinHeader_2.54","x":43,"y":17.5,"rotation":0,"layer":"F","pads":[{"number":"1","name":"VOUT","x":0,"y":-1.27,"type":"thru_hole","shape":"rect","size_x":1.7,"size_y":1.7,"drill":1.0,"net":"VOUT"},{"number":"2","name":"GND","x":0,"y":1.27,"type":"thru_hole","shape":"circle","size_x":1.7,"size_y":1.7,"drill":1.0,"net":"GND"}]}],"nets":[{"name":"VIN","pads":["U1.1","C1.1","J1.1"]},{"name":"GND","pads":["U1.2","C1.2","C2.2","J1.2","J2.2"]},{"name":"VOUT","pads":["U1.3","C2.1","J2.1"]}],"traces":[],"vias":[]};

// ═══════════════════════════════════════════════════════════════
// DESIGN RULES  (mutable by Layer Manager)
// ═══════════════════════════════════════════════════════════════
let DR = {
  // Copper traces
  minTraceWidth: 0.15,    // Min copper trace width (mm)
  traceWidth: 0.25,       // Default auto-route trace width (mm)
  // Clearances
  clearance: 0.2,         // Min copper-to-copper clearance (mm)
  edgeClearance: 0.5,     // Min clearance from board edge (mm)
  drillClearance: 0.25,   // Min drill-to-drill clearance (mm)
  packageGap: 1.0, // Min silkscreen-to-silkscreen clearance for auto-place (mm)
  // Vias
  viaSize: 1.0,           // Via outer diameter (mm)
  viaDrill: 0.6,          // Via drill diameter (mm)
  minAnnularRing: 0.15,   // Min copper ring width around hole (mm)
  viaClearance: 0.25,     // Min via-to-via pad clearance (mm)
  tentedVias: true,       // Solder mask covers via holes
  viaInPad: false,        // Allow vias inside component pads
  // Board
  boardThickness: 1.6,    // PCB thickness (mm) — used for drill aspect ratio
  copperWeight: 1.0,      // Copper weight oz/ft² — affects current capacity
  // Silkscreen
  silkscreenWidth: 0.12,  // Min silkscreen line width (mm)
  // Routing aesthetics
  cornerAngle: 90,        // Trace corner angle: 90 = sharp, 0 = fully rounded (degrees)
  // Routing constraints
  minTraceAngle: 90,      // Minimum allowed angle between trace segments (degrees); 90 = no acute bends
  // Auto-route
  allowVias: true,        // Allow autorouter to use vias for layer transitions
  powerTraceWidth: 0.4,   // Min trace width for power nets (mm)
};
// Persist DR to localStorage
function _saveDRStorage(){try{localStorage.setItem('pcb_DR',JSON.stringify(DR));}catch(_){}}
// Load DR from localStorage (called once at startup)
function _loadDRStorage(){
  try{
    const s=localStorage.getItem('pcb_DR');
    if(s){const saved=JSON.parse(s);Object.assign(DR,saved);}
  }catch(_){}
}

