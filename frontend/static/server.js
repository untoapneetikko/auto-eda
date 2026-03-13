import express from 'express';
import multer from 'multer';
import cors from 'cors';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = 3030;

app.use(cors());
app.use(express.json());
// No-cache for HTML so browser always gets the latest version
app.use((req, res, next) => {
  if (req.path === '/' || req.path.endsWith('.html')) {
    res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');
    res.setHeader('Pragma', 'no-cache');
  }
  next();
});
app.use(express.static(__dirname));

// Ensure folders exist
const DATASHEETS_DIR = path.join(__dirname, 'library');
const INBOX_DIR = path.join(__dirname, 'library', '_inbox');
[DATASHEETS_DIR, INBOX_DIR].forEach(d => fs.mkdirSync(d, { recursive: true }));

// Multer — save uploaded PDFs to inbox
const upload = multer({
  dest: INBOX_DIR,
  fileFilter: (req, file, cb) => cb(null, file.mimetype === 'application/pdf')
});

// ── Upload PDF ──────────────────────────────────────────────────────────────
// Saves PDF + extracts text, marks as "pending parse" in index.json
app.post('/api/upload', upload.single('pdf'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No PDF uploaded' });

  // Dynamic import pdf-parse (CommonJS module)
  const { default: pdfParse } = await import('pdf-parse/lib/pdf-parse.js');

  const originalName = req.file.originalname.replace(/[^a-zA-Z0-9._-]/g, '_');
  const tempPath = req.file.path;

  let rawText = '';
  let confidence = 'HIGH';
  let extractionNote = '';
  let pageCount = 0;

  try {
    const buffer = fs.readFileSync(tempPath);
    const data = await pdfParse(buffer);
    rawText = data.text;
    pageCount = data.numpages;

    const charCount = rawText.replace(/\s/g, '').length;
    const hasPinTable = /pin\s*\d|vcc|gnd|vss|vdd/i.test(rawText);
    const garbageRatio = (rawText.match(/[^\x00-\x7F]/g) || []).length / Math.max(charCount, 1);

    if (charCount < 500) {
      confidence = 'LOW';
      extractionNote = 'Very little text extracted — PDF may be a scanned image.';
    } else if (!hasPinTable) {
      confidence = 'MEDIUM';
      extractionNote = 'No pin table pattern detected in extracted text.';
    } else if (garbageRatio > 0.1) {
      confidence = 'MEDIUM';
      extractionNote = 'High proportion of non-ASCII characters — OCR quality may be poor.';
    }

    if (rawText.length > 60000) {
      rawText = rawText.substring(0, 60000) + `\n\n[TRUNCATED — full doc is ${pageCount} pages]`;
    }

  } catch (err) {
    confidence = 'FAILED';
    extractionNote = 'PDF text extraction failed: ' + err.message;
  }

  // Create a slug from filename
  const slug = originalName.replace('.pdf', '').replace(/[^a-zA-Z0-9-_]/g, '-').toUpperCase();
  const partDir = path.join(DATASHEETS_DIR, slug);
  fs.mkdirSync(partDir, { recursive: true });

  // Save files
  fs.copyFileSync(tempPath, path.join(partDir, 'original.pdf'));
  fs.unlinkSync(tempPath);
  fs.writeFileSync(path.join(partDir, 'raw_text.txt'), rawText, 'utf8');

  // Save a pending profile (Claude Code will fill this in)
  const pendingProfile = {
    part_number: slug,
    status: 'pending_parse',
    confidence,
    extraction_note: extractionNote || null,
    filename: originalName,
    page_count: pageCount,
    raw_text_length: rawText.replace(/\s/g, '').length,
    uploaded_at: new Date().toISOString(),
    parsed_at: null,
    human_corrections: []
  };
  fs.writeFileSync(path.join(partDir, 'profile.json'), JSON.stringify(pendingProfile, null, 2));

  updateIndex();

  res.json({ slug, confidence, extractionNote, charCount: rawText.replace(/\s/g, '').length });
});

// ── Get library index ───────────────────────────────────────────────────────
app.get('/api/library', (req, res) => {
  res.json(readIndex());
});

// ── Create new component manually (no PDF) ──────────────────────────────────
app.post('/api/library/new', (req, res) => {
  const { part_number, description, symbol_type, pins } = req.body;
  if (!part_number) return res.status(400).json({ error: 'part_number required' });
  const slug = part_number.replace(/[^a-zA-Z0-9_\-]/g, '_').toUpperCase();
  const dir = path.join(DATASHEETS_DIR, slug);
  if (fs.existsSync(dir)) return res.status(409).json({ error: 'Component already exists', slug });
  fs.mkdirSync(dir, { recursive: true });
  const profile = {
    part_number,
    description: description || '',
    manufacturer: '',
    package_types: [],
    supply_voltage_range: '',
    absolute_max: {},
    pins: pins || [],
    required_passives: [],
    application_circuits: [],
    common_mistakes: [],
    notes: '',
    symbol_type: symbol_type || 'ic',
    status: 'parsed',
    confidence: 'HIGH',
    extraction_note: null,
    filename: 'manual',
    uploaded_at: null,
    parsed_at: new Date().toISOString(),
    builtin: false,
    human_corrections: []
  };
  fs.writeFileSync(path.join(dir, 'profile.json'), JSON.stringify(profile, null, 2));
  res.json({ ok: true, slug });
});

// ── Get single profile ──────────────────────────────────────────────────────
app.get('/api/library/:slug', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  // Always inject slug so the frontend can use p.slug reliably
  if (!profile.slug) profile.slug = req.params.slug;
  res.json(profile);
});

// ── Get raw text (for Claude Code to read) ─────────────────────────────────
app.get('/api/library/:slug/raw', (req, res) => {
  const textPath = path.join(DATASHEETS_DIR, req.params.slug, 'raw_text.txt');
  if (!fs.existsSync(textPath)) return res.status(404).json({ error: 'Not found' });
  res.type('text/plain').send(fs.readFileSync(textPath, 'utf8'));
});

// ── Save correction ─────────────────────────────────────────────────────────
app.post('/api/library/:slug/correction', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  profile.human_corrections = profile.human_corrections || [];
  profile.human_corrections.push({
    date: new Date().toISOString().split('T')[0],
    ...req.body
  });
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

// ── Save designator prefix ──────────────────────────────────────────────────
app.put('/api/library/:slug/designator', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  if (!req.body.designator) return res.status(400).json({ error: 'designator required' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  profile.designator = req.body.designator.toUpperCase();
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

// ── Save symbol type ────────────────────────────────────────────────────────
app.put('/api/library/:slug/symbol_type', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  if (!req.body.symbol_type) return res.status(400).json({ error: 'symbol_type required' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  profile.symbol_type = req.body.symbol_type;
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

// ── Save modified pins (pin editor) ─────────────────────────────────────────
app.put('/api/library/:slug/pins', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  if (!Array.isArray(req.body.pins)) return res.status(400).json({ error: 'pins array required' });
  profile.pins = req.body.pins;
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

// ── Library version history helpers ─────────────────────────────────────────
function historyDir(slug) {
  return path.join(DATASHEETS_DIR, slug, 'history');
}
function activeVersionPath(slug) {
  return path.join(DATASHEETS_DIR, slug, 'active_version.json');
}
function clearActiveVersion(slug) {
  try { fs.unlinkSync(activeVersionPath(slug)); } catch(_) {}
}
// Stable JSON stringify (sorted keys) for reliable content comparison
function stableStr(obj) {
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return '[' + obj.map(stableStr).join(',') + ']';
  return '{' + Object.keys(obj).sort().map(k => JSON.stringify(k) + ':' + stableStr(obj[k])).join(',') + '}';
}
function profileAlreadySaved(slug, currentProfile) {
  const hDir = historyDir(slug);
  if (!fs.existsSync(hDir)) return false;
  const needle = stableStr(currentProfile);
  return fs.readdirSync(hDir).filter(f => f.endsWith('.json')).some(f => {
    try { return stableStr(JSON.parse(fs.readFileSync(path.join(hDir, f), 'utf8')).profile) === needle; }
    catch(_) { return false; }
  });
}

function snapshotProfile(slug, label) {
  const profilePath = path.join(DATASHEETS_DIR, slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return null;
  const hDir = historyDir(slug);
  fs.mkdirSync(hDir, { recursive: true });
  const now = new Date();
  const ts = now.toISOString().replace(/[:.]/g, '-');
  const snap = { saved_at: now.toISOString(), label: label || '', profile: JSON.parse(fs.readFileSync(profilePath, 'utf8')) };
  const snapPath = path.join(hDir, ts + '.json');
  fs.writeFileSync(snapPath, JSON.stringify(snap, null, 2));
  // Keep only last 20 snapshots
  const files = fs.readdirSync(hDir).filter(f => f.endsWith('.json')).sort();
  if (files.length > 20) {
    files.slice(0, files.length - 20).forEach(f => { try { fs.unlinkSync(path.join(hDir, f)); } catch(_) {} });
  }
  return ts;
}

// GET /api/library/:slug/history
app.get('/api/library/:slug/history', (req, res) => {
  const hDir = historyDir(req.params.slug);
  if (!fs.existsSync(hDir)) return res.json([]);
  const files = fs.readdirSync(hDir).filter(f => f.endsWith('.json')).sort().reverse();
  const list = files.map(f => {
    try {
      const snap = JSON.parse(fs.readFileSync(path.join(hDir, f), 'utf8'));
      return { id: f.replace('.json',''), saved_at: snap.saved_at, label: snap.label || '' };
    } catch(_) { return null; }
  }).filter(Boolean);
  res.json(list);
});

// POST /api/library/:slug/history/:id/activate — restore a snapshot as current
app.post('/api/library/:slug/history/:id/activate', (req, res) => {
  const slug = req.params.slug;
  const snapPath = path.join(historyDir(slug), req.params.id + '.json');
  if (!fs.existsSync(snapPath)) return res.status(404).json({ error: 'Not found' });
  const snap = JSON.parse(fs.readFileSync(snapPath, 'utf8'));
  if (!snap.profile) return res.status(400).json({ error: 'Invalid snapshot' });
  // Compute vNum: position in sorted history list (oldest = v1)
  const hDir = historyDir(slug);
  const files = fs.readdirSync(hDir).filter(f => f.endsWith('.json')).sort();
  const vNum = files.indexOf(req.params.id + '.json') + 1;
  const profilePath = path.join(DATASHEETS_DIR, slug, 'profile.json');
  fs.writeFileSync(profilePath, JSON.stringify(snap.profile, null, 2));
  fs.writeFileSync(activeVersionPath(slug), JSON.stringify({ id: req.params.id, label: snap.label || '', vNum }));
  res.json({ ok: true, label: snap.label || '', vNum });
});

// GET /api/library/:slug/active_version
app.get('/api/library/:slug/active_version', (req, res) => {
  const avp = activeVersionPath(req.params.slug);
  if (!fs.existsSync(avp)) return res.json(null);
  try { res.json(JSON.parse(fs.readFileSync(avp, 'utf8'))); } catch(_) { res.json(null); }
});

// PUT /api/library/:slug/history/:id — update snapshot content in-place (save to active version)
app.put('/api/library/:slug/history/:id', (req, res) => {
  const snapPath = path.join(historyDir(req.params.slug), req.params.id + '.json');
  if (!fs.existsSync(snapPath)) return res.status(404).json({ error: 'Not found' });
  const snap = JSON.parse(fs.readFileSync(snapPath, 'utf8'));
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  snap.profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  snap.saved_at = new Date().toISOString();
  fs.writeFileSync(snapPath, JSON.stringify(snap, null, 2));
  res.json({ ok: true });
});

// DELETE /api/library/:slug/history/:id
app.delete('/api/library/:slug/history/:id', (req, res) => {
  const snapPath = path.join(historyDir(req.params.slug), req.params.id + '.json');
  if (!fs.existsSync(snapPath)) return res.status(404).json({ error: 'Not found' });
  fs.unlinkSync(snapPath);
  res.json({ ok: true });
});

// POST /api/library/:slug/history/save — manual component snapshot
app.post('/api/library/:slug/history/save', (req, res) => {
  const ts = snapshotProfile(req.params.slug, req.body.label || '');
  if (!ts) return res.status(404).json({ error: 'Component not found' });
  res.json({ ok: true, id: ts });
});

// PUT /api/library/:slug — full profile write (used by agents for datasheet/symbol rebuild)
app.put('/api/library/:slug', (req, res) => {
  const slug = req.params.slug;
  const profilePath = path.join(DATASHEETS_DIR, slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  snapshotProfile(slug, req.body._label || 'profile-rebuild');
  clearActiveVersion(slug);
  const current = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  const newProfile = { ...req.body, human_corrections: current.human_corrections || [] };
  delete newProfile._label;
  fs.writeFileSync(profilePath, JSON.stringify(newProfile, null, 2));
  res.json({ ok: true });
});

app.put('/api/library/:slug/example_circuit', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  profile.example_circuit = req.body;
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

app.put('/api/library/:slug/layout_example', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  profile.layout_example = req.body;
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

app.delete('/api/library/:slug/layout_example', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  delete profile.layout_example;
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

// ── Footprint API ─────────────────────────────────────────────────────────────
const FOOTPRINTS_DIR = path.join(__dirname, 'pcb', 'footprints');

app.get('/api/footprints', (req, res) => {
  const files = fs.readdirSync(FOOTPRINTS_DIR).filter(f => f.endsWith('.json'));
  const list = files.map(f => {
    try { const d = JSON.parse(fs.readFileSync(path.join(FOOTPRINTS_DIR, f), 'utf8')); return { name: d.name, description: d.description, pin_count: d.pin_count, file: f }; }
    catch { return null; }
  }).filter(Boolean);
  res.json(list);
});

app.get('/api/footprints/:name', (req, res) => {
  const file = path.join(FOOTPRINTS_DIR, req.params.name.endsWith('.json') ? req.params.name : req.params.name + '.json');
  if (!fs.existsSync(file)) return res.status(404).json({ error: 'Not found' });
  res.json(JSON.parse(fs.readFileSync(file, 'utf8')));
});

app.put('/api/footprints/:name', (req, res) => {
  const name = req.params.name.replace(/[^a-zA-Z0-9_\-\.]/g, '');
  const file = path.join(FOOTPRINTS_DIR, name.endsWith('.json') ? name : name + '.json');
  fs.writeFileSync(file, JSON.stringify(req.body, null, 2));
  res.json({ ok: true });
});

// Save/update the footprint assigned to a component profile
app.put('/api/library/:slug/footprint', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  profile.footprint = req.body.footprint || null;
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ ok: true });
});

// ── Rule-based footprint generator ───────────────────────────────────────────
app.post('/api/library/:slug/generate-footprint', (req, res) => {
  const profilePath = path.join(DATASHEETS_DIR, req.params.slug, 'profile.json');
  if (!fs.existsSync(profilePath)) return res.status(404).json({ error: 'Profile not found' });
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  const pkgStr = (req.body && req.body.package) || (profile.package_types || [])[0] || '';
  const pins = profile.pins || [];
  const result = generateFootprintRules(pkgStr, pins, profile.part_number || req.params.slug);
  if (!result) return res.status(422).json({ error: 'No rule matches this package', package: pkgStr });
  // Optionally save it
  const name = result.name.replace(/[^a-zA-Z0-9_\-]/g, '_');
  const file = path.join(FOOTPRINTS_DIR, name + '.json');
  fs.writeFileSync(file, JSON.stringify(result, null, 2));
  // Also assign to profile
  profile.footprint = result.name;
  fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
  res.json({ footprint: result, method: 'rule-based', confidence: 'HIGH' });
});

function generateFootprintRules(pkg, pins, partName) {
  const p = (pkg || '').toUpperCase().trim();
  const n = pins.length || 2;

  // Helper: pin name from profile pins array
  const pname = i => (pins[i] && (pins[i].name || String(pins[i].number))) || String(i+1);
  const pnum  = i => (pins[i] && String(pins[i].number)) || String(i+1);

  // SOIC-N / SOP-N / SO-N
  const soicM = p.match(/SOIC-?(\d+)|SOP-?(\d+)|^SO-?(\d+)/);
  if (soicM) {
    const cnt = parseInt(soicM[1]||soicM[2]||soicM[3]) || n;
    return makeDualRowSMD(cnt, pins, 1.27, 5.08, 0.65, 1.6, partName);
  }
  // TSSOP-N / SSOP-N
  const tsM = p.match(/TSSOP-?(\d+)|SSOP-?(\d+)/);
  if (tsM) {
    const cnt = parseInt(tsM[1]||tsM[2]) || n;
    return makeDualRowSMD(cnt, pins, 0.65, 4.4, 0.35, 1.5, partName);
  }
  // DIP-N
  const dipM = p.match(/DIP-?(\d+)/);
  if (dipM) {
    const cnt = parseInt(dipM[1]) || n;
    const row = cnt > 16 ? 15.24 : 7.62;
    return makeDualRowTHT(cnt, pins, 2.54, row, partName);
  }
  // PDIP same as DIP
  const pdipM = p.match(/PDIP-?(\d+)/);
  if (pdipM) {
    const cnt = parseInt(pdipM[1]) || n;
    return makeDualRowTHT(cnt, pins, 2.54, 7.62, partName);
  }
  // TQFP-N / LQFP-N / QFP-N
  const tqfpM = p.match(/(?:T|L)?QFP-?(\d+)/);
  if (tqfpM) {
    const cnt = parseInt(tqfpM[1]) || n;
    const pitch = p.includes('0.4') ? 0.4 : p.includes('0.5') ? 0.5 : 0.8;
    return makeQuadFlatSMD(cnt, pins, pitch, false, partName);
  }
  // QFN-N / DFN-N / WSON-N / LFCSP-N
  const qfnM = p.match(/(?:QFN|DFN|WSON|LFCSP)-?(\d+)/);
  if (qfnM) {
    const cnt = parseInt(qfnM[1]) || n;
    const bodyM = p.match(/(\d+(?:\.\d+)?)X\d/);
    const body = bodyM ? parseFloat(bodyM[1]) : Math.max(3, Math.ceil(cnt/4)*0.65 + 1.5);
    return makeQuadFlatSMD(cnt, pins, null, body, partName);
  }
  // SOT-23 / SOT-23-3
  if (p.match(/SOT-?23-?3?$/) || (p.match(/SOT-?23/) && n <= 3)) return makeSOT23(pins, partName);
  // SOT-23-5
  if (p.match(/SOT-?23-?5/)) return makeSOT235(pins, partName);
  // SOT-223
  if (p.match(/SOT-?223/)) return makeSOT223(pins, partName);
  // SOT-89
  if (p.match(/SOT-?89/)) return makeSOT89(pins, partName);
  // TO-92
  if (p.match(/TO-?92/)) return makeTO92(pins, partName);
  // TO-220
  if (p.match(/TO-?220/)) return makeTO220(pins, partName);
  // SOD-123
  if (p.match(/SOD-?123/)) return makeSOD123(pins, partName);
  // SMA / DO-214AC
  if (p.match(/SMA|DO-?214AC/)) return makeSMA(pins, partName);
  // 0402
  if (p.match(/0402/)) return makePassive('0402', pins, 1.0, 0.6, 0.6, partName);
  // 0603
  if (p.match(/0603/)) return makePassive('0603', pins, 1.6, 1.0, 0.8, partName);
  // 0805
  if (p.match(/0805/)) return makePassive('0805', pins, 2.0, 1.25, 1.0, partName);
  // 1206
  if (p.match(/1206/)) return makePassive('1206', pins, 3.2, 1.75, 1.2, partName);
  // LED 5mm THT
  if (p.match(/LED.*5MM|5MM.*LED|T-?1[\s\-]?(?:3\/4)?/)) return makeLED5mm(pins, partName);
  // CP / Electrolytic 5mm THT
  if (p.match(/CP.*5MM|5MM.*CAP|RADIAL|ELEC/)) return makeCapPol5mm(pins, partName);

  return null;
}

// ── Footprint shape generators ────────────────────────────────────────────────

function makeDualRowSMD(cnt, pins, pitch, rowSpacing, padW, padH, partName) {
  const half = Math.floor(cnt / 2);
  const totalH = (half - 1) * pitch;
  const pads = [];
  for (let i = 0; i < half; i++) {
    const y = -totalH/2 + i * pitch;
    pads.push({ number: String(i+1), x: -rowSpacing/2, y, type:'smd', shape:'rect', size_x: padH, size_y: padW });
  }
  for (let i = 0; i < cnt - half; i++) {
    const y = totalH/2 - i * pitch;
    pads.push({ number: String(half + i + 1), x: rowSpacing/2, y, type:'smd', shape:'rect', size_x: padH, size_y: padW });
  }
  const cy_w = rowSpacing + padH + 0.5, cy_h = totalH + padW + 1.0;
  return {
    name: partName + '_' + (cnt <= 8 ? 'SOIC-' : 'SOIC-') + cnt,
    description: `${cnt}-pin dual-row SMD (pitch ${pitch}mm, row ${rowSpacing}mm)`,
    pin_count: cnt, pitch, row_spacing: rowSpacing,
    pads,
    courtyard: { x: -cy_w/2, y: -cy_h/2, w: cy_w, h: cy_h }
  };
}

function makeDualRowTHT(cnt, pins, pitch, rowSpacing, partName) {
  const half = Math.floor(cnt / 2);
  const totalH = (half - 1) * pitch;
  const pads = [];
  for (let i = 0; i < half; i++) {
    pads.push({ number: String(i+1), x: -rowSpacing/2, y: -totalH/2 + i*pitch, type:'thru_hole', shape: i===0?'rect':'circle', size_x:1.6, size_y:1.6, drill:0.8 });
  }
  for (let i = 0; i < cnt - half; i++) {
    pads.push({ number: String(half+i+1), x: rowSpacing/2, y: totalH/2 - i*pitch, type:'thru_hole', shape:'circle', size_x:1.6, size_y:1.6, drill:0.8 });
  }
  const cy_w = rowSpacing + 2.5, cy_h = totalH + 3.0;
  return {
    name: partName + '_DIP-' + cnt,
    description: `DIP-${cnt} through-hole (${pitch}mm pitch, ${rowSpacing}mm row spacing)`,
    pin_count: cnt, pitch, row_spacing: rowSpacing,
    pads,
    courtyard: { x: -cy_w/2, y: -cy_h/2, w: cy_w, h: cy_h }
  };
}

function makeQuadFlatSMD(cnt, pins, pitch, bodyMm, partName) {
  // Distribute pads equally on 4 sides
  const perSide = Math.floor(cnt / 4);
  const rem = cnt % 4;
  const sides = [perSide + (rem>0?1:0), perSide + (rem>1?1:0), perSide + (rem>2?1:0), perSide];
  const isQFN = !!bodyMm;
  const p = pitch || 0.5;

  let body, padW, padH, padOffset;
  if (isQFN) {
    body = bodyMm;
    padW = 0.3; padH = 1.0;
    padOffset = body/2 + padH/2 - 0.1;
  } else {
    body = sides[0] * p + 1.0;
    padW = p * 0.55; padH = 1.5;
    padOffset = body/2 + padH/2 + 0.3;
  }

  const pads = [];
  let padNum = 1;
  // Side 0: left, pins go bottom to top
  for (let i = 0; i < sides[0]; i++) {
    const y = ((sides[0]-1)/2 - i) * p;
    pads.push({ number: String(padNum++), x: -padOffset, y, type:'smd', shape:'rect', size_x: padH, size_y: padW });
  }
  // Side 1: top, pins go left to right
  for (let i = 0; i < sides[1]; i++) {
    const x = -((sides[1]-1)/2) * p + i * p;
    pads.push({ number: String(padNum++), x, y: -padOffset, type:'smd', shape:'rect', size_x: padW, size_y: padH });
  }
  // Side 2: right, pins go top to bottom
  for (let i = 0; i < sides[2]; i++) {
    const y = -((sides[2]-1)/2) * p + i * p;
    pads.push({ number: String(padNum++), x: padOffset, y, type:'smd', shape:'rect', size_x: padH, size_y: padW });
  }
  // Side 3: bottom, pins go right to left
  for (let i = 0; i < sides[3]; i++) {
    const x = ((sides[3]-1)/2) * p - i * p;
    pads.push({ number: String(padNum++), x, y: padOffset, type:'smd', shape:'rect', size_x: padW, size_y: padH });
  }
  // Exposed pad for QFN
  if (isQFN) {
    const epSize = body * 0.65;
    pads.push({ number: String(padNum), x:0, y:0, type:'smd', shape:'rect', size_x: epSize, size_y: epSize });
  }

  const totalSize = padOffset * 2 + padH + 0.5;
  return {
    name: partName + '_' + (isQFN ? 'QFN-' : 'TQFP-') + cnt,
    description: `${cnt}-pin ${isQFN?'QFN':'TQFP'} (pitch ${p}mm${isQFN?', body '+bodyMm+'mm':''}${isQFN?' + EPAD':''})`,
    pin_count: cnt + (isQFN?1:0), pitch: p,
    pads,
    courtyard: { x: -totalSize/2, y: -totalSize/2, w: totalSize, h: totalSize }
  };
}

function makeSOT23(pins, partName) {
  return {
    name: partName + '_SOT-23', description: 'SOT-23 3-pin SMD', pin_count: 3,
    pads: [
      { number:'1', x:-0.95, y: 1.0, type:'smd', shape:'rect', size_x:0.6, size_y:0.9 },
      { number:'2', x: 0.95, y: 1.0, type:'smd', shape:'rect', size_x:0.6, size_y:0.9 },
      { number:'3', x: 0.0,  y:-1.0, type:'smd', shape:'rect', size_x:0.6, size_y:0.9 }
    ],
    courtyard: { x:-1.6, y:-1.7, w:3.2, h:3.4 }
  };
}

function makeSOT235(pins, partName) {
  return {
    name: partName + '_SOT-23-5', description: 'SOT-23-5 5-pin SMD', pin_count: 5,
    pads: [
      { number:'1', x:-1.5, y: 1.3, type:'smd', shape:'rect', size_x:0.6, size_y:0.7 },
      { number:'2', x:-0.5, y: 1.3, type:'smd', shape:'rect', size_x:0.6, size_y:0.7 },  // Actually corrected for SOT-23-5 standard layout
      { number:'3', x: 0.5, y: 1.3, type:'smd', shape:'rect', size_x:0.6, size_y:0.7 },
      { number:'4', x: 0.95, y:-1.3, type:'smd', shape:'rect', size_x:0.6, size_y:0.9 },
      { number:'5', x:-0.95, y:-1.3, type:'smd', shape:'rect', size_x:0.6, size_y:0.9 }
    ],
    courtyard: { x:-1.8, y:-2.0, w:3.6, h:4.0 }
  };
}

function makeSOT223(pins, partName) {
  return {
    name: partName + '_SOT-223', description: 'SOT-223 4-pin SMD (3 signal + tab)', pin_count: 4,
    pads: [
      { number:'1', x:-2.3, y: 1.6, type:'smd', shape:'rect', size_x:0.7, size_y:1.5 },
      { number:'2', x: 0.0, y: 1.6, type:'smd', shape:'rect', size_x:0.7, size_y:1.5 },
      { number:'3', x: 2.3, y: 1.6, type:'smd', shape:'rect', size_x:0.7, size_y:1.5 },
      { number:'4', x: 0.0, y:-1.6, type:'smd', shape:'rect', size_x:3.5, size_y:2.2 }
    ],
    courtyard: { x:-3.5, y:-2.9, w:7.0, h:5.8 }
  };
}

function makeSOT89(pins, partName) {
  return {
    name: partName + '_SOT-89', description: 'SOT-89 3-pin SMD', pin_count: 3,
    pads: [
      { number:'1', x:-1.5, y: 1.5, type:'smd', shape:'rect', size_x:0.7, size_y:1.3 },
      { number:'2', x: 0.0, y:-1.0, type:'smd', shape:'rect', size_x:1.5, size_y:2.5 },
      { number:'3', x: 1.5, y: 1.5, type:'smd', shape:'rect', size_x:0.7, size_y:1.3 }
    ],
    courtyard: { x:-2.5, y:-2.5, w:5.0, h:5.0 }
  };
}

function makeTO92(pins, partName) {
  return {
    name: partName + '_TO-92', description: 'TO-92 3-pin through-hole', pin_count: 3,
    pads: [
      { number:'1', x:-1.27, y:0, type:'thru_hole', shape:'rect',   size_x:1.6, size_y:1.6, drill:0.8 },
      { number:'2', x: 0.0,  y:0, type:'thru_hole', shape:'circle', size_x:1.6, size_y:1.6, drill:0.8 },
      { number:'3', x: 1.27, y:0, type:'thru_hole', shape:'circle', size_x:1.6, size_y:1.6, drill:0.8 }
    ],
    courtyard: { x:-2.5, y:-2.5, w:5.0, h:5.0 }
  };
}

function makeTO220(pins, partName) {
  return {
    name: partName + '_TO-220', description: 'TO-220 3-pin through-hole (2.54mm pitch)', pin_count: 3,
    pads: [
      { number:'1', x:-2.54, y:0, type:'thru_hole', shape:'rect',   size_x:1.8, size_y:1.8, drill:1.0 },
      { number:'2', x: 0.0,  y:0, type:'thru_hole', shape:'circle', size_x:1.8, size_y:1.8, drill:1.0 },
      { number:'3', x: 2.54, y:0, type:'thru_hole', shape:'circle', size_x:1.8, size_y:1.8, drill:1.0 }
    ],
    courtyard: { x:-4.0, y:-6.5, w:8.0, h:10.0 }
  };
}

function makeSOD123(pins, partName) {
  return {
    name: partName + '_SOD-123', description: 'SOD-123 2-pin SMD diode', pin_count: 2,
    pads: [
      { number:'1', x:-1.6, y:0, type:'smd', shape:'rect', size_x:1.1, size_y:1.1 },
      { number:'2', x: 1.6, y:0, type:'smd', shape:'rect', size_x:1.1, size_y:1.1 }
    ],
    courtyard: { x:-2.5, y:-1.0, w:5.0, h:2.0 }
  };
}

function makeSMA(pins, partName) {
  return {
    name: partName + '_SMA', description: 'SMA / DO-214AC 2-pin SMD diode', pin_count: 2,
    pads: [
      { number:'1', x:-2.0, y:0, type:'smd', shape:'rect', size_x:1.5, size_y:2.3 },
      { number:'2', x: 2.0, y:0, type:'smd', shape:'rect', size_x:1.5, size_y:2.3 }
    ],
    courtyard: { x:-3.2, y:-1.5, w:6.4, h:3.0 }
  };
}

function makePassive(pkg, pins, padSpacing, padW, padH, partName) {
  return {
    name: partName + '_' + pkg, description: `${pkg} 2-pad SMD passive`, pin_count: 2,
    pads: [
      { number:'1', x:-padSpacing/2, y:0, type:'smd', shape:'rect', size_x:padW, size_y:padH },
      { number:'2', x: padSpacing/2, y:0, type:'smd', shape:'rect', size_x:padW, size_y:padH }
    ],
    courtyard: { x:-(padSpacing/2+padW/2+0.2), y:-(padH/2+0.2), w:padSpacing+padW+0.4, h:padH+0.4 }
  };
}

function makeLED5mm(pins, partName) {
  return {
    name: partName + '_LED-5mm', description: 'LED 5mm through-hole (2.54mm pitch)', pin_count: 2,
    pads: [
      { number:'1', x:-1.27, y:0, type:'thru_hole', shape:'rect',   size_x:1.8, size_y:1.8, drill:0.8 },
      { number:'2', x: 1.27, y:0, type:'thru_hole', shape:'circle', size_x:1.8, size_y:1.8, drill:0.8 }
    ],
    courtyard: { x:-3.5, y:-3.5, w:7.0, h:7.0 }
  };
}

function makeCapPol5mm(pins, partName) {
  return {
    name: partName + '_CAP-POL-5mm', description: 'Polarized capacitor radial 5mm pitch', pin_count: 2,
    pads: [
      { number:'1', x:-2.5, y:0, type:'thru_hole', shape:'rect',   size_x:1.8, size_y:1.8, drill:0.8 },
      { number:'2', x: 2.5, y:0, type:'thru_hole', shape:'circle', size_x:1.8, size_y:1.8, drill:0.8 }
    ],
    courtyard: { x:-4.5, y:-4.5, w:9.0, h:9.0 }
  };
}

// ── Serve original PDF ───────────────────────────────────────────────────────
app.get('/api/library/:slug/pdf', (req, res) => {
  const pdfPath = path.join(DATASHEETS_DIR, req.params.slug, 'original.pdf');
  if (!fs.existsSync(pdfPath)) return res.status(404).json({ error: 'No PDF' });
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('Content-Disposition', 'inline');
  fs.createReadStream(pdfPath).pipe(res);
});

// ── Projects API ─────────────────────────────────────────────────────────────
const PROJECTS_DIR = path.join(__dirname, 'projects');
fs.mkdirSync(PROJECTS_DIR, { recursive: true });

app.get('/api/projects', (req, res) => {
  const projects = [];
  for (const file of fs.readdirSync(PROJECTS_DIR)) {
    if (!file.endsWith('.json')) continue;
    try {
      const d = JSON.parse(fs.readFileSync(path.join(PROJECTS_DIR, file), 'utf8'));
      projects.push({ id: d.id, name: d.name, updated_at: d.updated_at,
        component_count: (d.components || []).length });
    } catch {}
  }
  projects.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  res.json(projects);
});

app.post('/api/projects', (req, res) => {
  const p = req.body;
  if (!p.id) p.id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  p.created_at = p.created_at || new Date().toISOString();
  p.updated_at = new Date().toISOString();
  fs.writeFileSync(path.join(PROJECTS_DIR, p.id + '.json'), JSON.stringify(p, null, 2));
  res.json({ id: p.id });
});

app.get('/api/projects/:id', (req, res) => {
  const file = path.join(PROJECTS_DIR, req.params.id + '.json');
  if (!fs.existsSync(file)) return res.status(404).json({ error: 'Not found' });
  res.json(JSON.parse(fs.readFileSync(file, 'utf8')));
});

app.delete('/api/projects/:id', (req, res) => {
  const file = path.join(PROJECTS_DIR, req.params.id + '.json');
  if (fs.existsSync(file)) fs.unlinkSync(file);
  // Cascade: delete all PCB boards linked to this project
  let deletedBoards = 0;
  try {
    for (const f of fs.readdirSync(PCB_BOARDS_DIR)) {
      if (!f.endsWith('.json')) continue;
      const boardPath = path.join(PCB_BOARDS_DIR, f);
      try {
        const d = JSON.parse(fs.readFileSync(boardPath, 'utf8'));
        if (d.projectId === req.params.id) { fs.unlinkSync(boardPath); deletedBoards++; }
      } catch {}
    }
  } catch {}
  res.json({ ok: true, deletedBoards });
});

// ── Export design bundle ─────────────────────────────────────────────────────
// GET /api/export-design/:id — returns a self-contained bundle with project + all
// referenced component profiles (no raw_text — just what's needed to render)
app.get('/api/export-design/:id', (req, res) => {
  const file = path.join(PROJECTS_DIR, req.params.id + '.json');
  if (!fs.existsSync(file)) return res.status(404).json({ error: 'Not found' });
  const project = JSON.parse(fs.readFileSync(file, 'utf8'));

  // Collect unique slugs referenced in the schematic
  const BUILTIN_SLUGS = new Set([
    'RESISTOR','CAPACITOR','CAPACITOR_POL','INDUCTOR','VCC','GND',
    'DIODE','LED','LM7805','AMS1117-3.3','NE555','2N2222','BC547',
    'IRF540N','LM358','L298N','DRV8833','ATMEGA328P','ESP32_WROOM_32'
  ]);
  const slugs = [...new Set((project.components || []).map(c => c.slug).filter(Boolean))];
  const library = {};
  for (const slug of slugs) {
    if (BUILTIN_SLUGS.has(slug)) continue; // recipient already has these
    const profilePath = path.join(DATASHEETS_DIR, slug, 'profile.json');
    if (!fs.existsSync(profilePath)) continue;
    const p = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
    // Strip raw_text (can be 60 kB+) — not needed for rendering
    delete p.raw_text;
    library[slug] = p;
  }

  const bundle = {
    format: 'schematic-designer-v1',
    exported_at: new Date().toISOString(),
    project,
    library
  };

  const filename = (project.name || 'design').replace(/[^a-zA-Z0-9_-]/g, '_') + '.schematic';
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
  res.json(bundle);
});

// ── Import design bundle ─────────────────────────────────────────────────────
// POST /api/import-design — accepts a bundle, installs missing profiles, saves project
app.post('/api/import-design', (req, res) => {
  const bundle = req.body;
  if (bundle.format !== 'schematic-designer-v1') {
    return res.status(400).json({ error: 'Unrecognised bundle format' });
  }
  const installed = [], skipped = [];

  // Install component profiles that don't exist locally
  for (const [slug, profile] of Object.entries(bundle.library || {})) {
    const dir = path.join(DATASHEETS_DIR, slug);
    const profilePath = path.join(dir, 'profile.json');
    if (fs.existsSync(profilePath)) { skipped.push(slug); continue; }
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
    installed.push(slug);
  }
  if (installed.length) updateIndex();

  // Save project (new ID to avoid collision)
  const p = bundle.project;
  p.id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  p.imported_at = new Date().toISOString();
  p.updated_at = new Date().toISOString();
  fs.writeFileSync(path.join(PROJECTS_DIR, p.id + '.json'), JSON.stringify(p, null, 2));

  res.json({ ok: true, project_id: p.id, installed, skipped });
});

// ── PCB Boards API ────────────────────────────────────────────────────────────
const PCB_BOARDS_DIR = path.join(__dirname, 'pcb-boards');
fs.mkdirSync(PCB_BOARDS_DIR, { recursive: true });

app.get('/api/pcb-boards', (req, res) => {
  const filterProjectId = req.query.projectId || null;
  const boards = [];
  for (const file of fs.readdirSync(PCB_BOARDS_DIR)) {
    if (!file.endsWith('.json')) continue;
    try {
      const d = JSON.parse(fs.readFileSync(path.join(PCB_BOARDS_DIR, file), 'utf8'));
      const pid = d.projectId || null;
      if (filterProjectId && pid !== filterProjectId) continue;
      boards.push({ id: d.id, title: d.title, updated_at: d.updated_at,
        projectId: pid,
        component_count: (d.components || []).length });
    } catch {}
  }
  boards.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
  res.json(boards);
});

app.post('/api/pcb-boards', (req, res) => {
  const b = req.body;
  if (!b.id) b.id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  b.updated_at = new Date().toISOString();
  fs.writeFileSync(path.join(PCB_BOARDS_DIR, b.id + '.json'), JSON.stringify(b, null, 2));
  res.json({ id: b.id });
});

app.get('/api/pcb-boards/:id', (req, res) => {
  const file = path.join(PCB_BOARDS_DIR, req.params.id + '.json');
  if (!fs.existsSync(file)) return res.status(404).json({ error: 'Not found' });
  res.json(JSON.parse(fs.readFileSync(file, 'utf8')));
});

app.delete('/api/pcb-boards/:id', (req, res) => {
  const file = path.join(PCB_BOARDS_DIR, req.params.id + '.json');
  if (fs.existsSync(file)) fs.unlinkSync(file);
  res.json({ ok: true });
});

// ── Gen Tickets API ──────────────────────────────────────────────────────────
const GEN_TICKETS_FILE = path.join(__dirname, 'gen_tickets.json');

function readGenTickets() {
  if (!fs.existsSync(GEN_TICKETS_FILE)) {
    const init = { nextId: 1, tickets: [] };
    fs.writeFileSync(GEN_TICKETS_FILE, JSON.stringify(init, null, 2));
    return init;
  }
  return JSON.parse(fs.readFileSync(GEN_TICKETS_FILE, 'utf8'));
}

function saveGenTickets(data) {
  fs.writeFileSync(GEN_TICKETS_FILE, JSON.stringify(data, null, 2));
}

app.get('/api/gen-tickets', (req, res) => {
  res.json(readGenTickets());
});

app.post('/api/gen-tickets', (req, res) => {
  const data = readGenTickets();
  const ticket = {
    id: data.nextId++,
    type: req.body.type || 'footprint',
    slug: req.body.slug || '',
    title: req.body.title || 'Untitled',
    prompt: req.body.prompt || '',
    status: req.body.status || 'pending',
    created_at: new Date().toISOString()
  };
  data.tickets.push(ticket);
  saveGenTickets(data);
  res.json(ticket);
});

app.put('/api/gen-tickets/:id', (req, res) => {
  const data = readGenTickets();
  const idx = data.tickets.findIndex(t => t.id === parseInt(req.params.id));
  if (idx === -1) return res.status(404).json({ error: 'Not found' });
  data.tickets[idx] = { ...data.tickets[idx], ...req.body, id: data.tickets[idx].id };
  saveGenTickets(data);
  res.json(data.tickets[idx]);
});

app.delete('/api/gen-tickets/:id', (req, res) => {
  const data = readGenTickets();
  data.tickets = data.tickets.filter(t => t.id !== parseInt(req.params.id));
  saveGenTickets(data);
  res.json({ ok: true });
});

// POST /api/gen-tickets/:id/retract — restore the pre-ticket snapshot for this ticket's slug
app.post('/api/gen-tickets/:id/retract', (req, res) => {
  const data = readGenTickets();
  const ticket = data.tickets.find(t => t.id === parseInt(req.params.id));
  if (!ticket) return res.status(404).json({ error: 'Ticket not found' });
  if (!ticket.slug) return res.status(400).json({ error: 'Ticket has no slug' });
  const hDir = historyDir(ticket.slug);
  if (!fs.existsSync(hDir)) return res.status(400).json({ error: 'No version history for this component. Changes may have been made directly to the file.' });
  const createdMs = new Date(ticket.created_at).getTime();
  // Find the earliest snapshot taken at or after ticket created_at (= state just before agent's first write)
  const files = fs.readdirSync(hDir).filter(f => f.endsWith('.json')).sort();
  let snapFile = null;
  for (const f of files) {
    try {
      const snap = JSON.parse(fs.readFileSync(path.join(hDir, f), 'utf8'));
      if (new Date(snap.saved_at).getTime() >= createdMs) { snapFile = f; break; }
    } catch(_) {}
  }
  if (!snapFile) return res.status(400).json({ error: 'No snapshot found for this ticket window. The component was not modified via the API after the ticket was created.' });
  const snap = JSON.parse(fs.readFileSync(path.join(hDir, snapFile), 'utf8'));
  // Snapshot current state before retract so it's recoverable
  snapshotProfile(ticket.slug, `auto — before retract of GT-${ticket.id}`);
  fs.writeFileSync(path.join(DATASHEETS_DIR, ticket.slug, 'profile.json'), JSON.stringify(snap.profile, null, 2));
  // Mark ticket retracted
  const idx = data.tickets.findIndex(t => t.id === parseInt(req.params.id));
  data.tickets[idx].status = 'retracted';
  data.tickets[idx].retracted_at = new Date().toISOString();
  saveGenTickets(data);
  res.json({ ok: true, restored_from: snap.saved_at });
});

// ── Netlist-to-schematic auto-builder ────────────────────────────────────────
//
// POST /api/build-from-netlist
// Body: { name, components:[{id,slug,value?,designator?}], nets:[{name,pins:["compId.pinRef",...]}] }
// Response: { id, project }
//
// Pin reference formats accepted:
//   "u1.IN"   — IC pin by name
//   "u1.1"    — IC pin by number (1-based)
//   "c1.P1"   — passive port key
//   "c1.1"    — passive port by number (1→P1, 2→P2)
//   "vcc1.VCC", "gnd1.GND" — power symbol ports

const NL_SYMDEFS = {
  resistor:     {ports:{P1:[-30,0],  P2:[30,0]}},
  capacitor:    {ports:{P1:[0,-20],  P2:[0,20]}},
  capacitor_pol:{ports:{'+':[0,-20], '-':[0,20]}},
  inductor:     {ports:{P1:[-40,0],  P2:[40,0]}},
  vcc:          {ports:{VCC:[0,20]}},
  gnd:          {ports:{GND:[0,-20]}},
  diode:        {ports:{A:[-30,0],   K:[30,0]}},
  led:          {ports:{A:[-30,0],   K:[30,0]}},
  npn:          {ports:{B:[-30,0],   C:[20,-25], E:[20,25]}},
  pnp:          {ports:{B:[-30,0],   C:[20,25],  E:[20,-25]}},
  nmos:         {ports:{G:[-30,0],   D:[20,-25], S:[20,25]}},
  pmos:         {ports:{G:[-30,0],   D:[20,25],  S:[20,-25]}},
  amplifier:    {ports:{IN:[-50,0],  OUT:[50,0], GND:[0,40]}},
  opamp:        {ports:{'+'  :[-50,-20], '-':[-50,20], OUT:[50,0]}},
};
const NL_BUILTIN_SYMTYPE = {
  RESISTOR:'resistor', CAPACITOR:'capacitor', CAPACITOR_POL:'capacitor_pol',
  INDUCTOR:'inductor', VCC:'vcc', GND:'gnd', DIODE:'diode', LED:'led',
  '2N2222':'npn', BC547:'npn', IRF540N:'nmos',
  LM7805:'ic', 'AMS1117-3.3':'ic', NE555:'ic', LM358:'ic',
  L298N:'ic', DRV8833:'ic', ATMEGA328P:'ic', ESP32_WROOM_32:'ic',
};

function nlSymType(slug, profile) {
  return NL_BUILTIN_SYMTYPE[slug] || profile?.symbol_type || 'ic';
}
function nlSnap(v) { return Math.round(v / 20) * 20; }
function nlRot(dx, dy, r) {
  if (r === 1) return [dy, -dx];
  if (r === 2) return [-dx, -dy];
  if (r === 3) return [-dy, dx];
  return [dx, dy];
}

// Compute all port world-positions for a placed component
function nlPorts(comp, profile) {
  const { x, y, symType, rotation = 0 } = comp;
  if (symType === 'ic' || symType === 'amplifier') {
    const pins = profile?.pins || [];
    if (!pins.length) return {};
    const half = Math.ceil(pins.length / 2);
    const BOX_W = 120, ROW_H = 20;
    const out = {};
    pins.forEach((pin, i) => {
      const [dx, dy] = i < half
        ? [-(BOX_W / 2 + 40), -(half - 1) * ROW_H / 2 + i * ROW_H]
        : [+(BOX_W / 2 + 40), +(half - 1) * ROW_H / 2 - (i - half) * ROW_H];
      const [rx, ry] = nlRot(dx, dy, rotation);
      out[`p${i}`] = { x: x + rx, y: y + ry, pinNumber: String(pin.number), pinName: pin.name };
    });
    return out;
  }
  const sdef = NL_SYMDEFS[symType];
  if (!sdef) return {};
  const out = {};
  for (const [k, [dx, dy]] of Object.entries(sdef.ports)) {
    const [rx, ry] = nlRot(dx, dy, rotation);
    out[k] = { x: x + rx, y: y + ry };
  }
  return out;
}

// Resolve "compId.pinRef" → {compId, portKey, x, y}
function nlResolvePin(pinRef, compMap, portCache) {
  const dot = pinRef.indexOf('.');
  if (dot < 0) return null;
  const cid = pinRef.slice(0, dot);
  const pkey = pinRef.slice(dot + 1);
  if (!compMap[cid] || !portCache[cid]) return null;
  const ports = portCache[cid];
  // Exact port key match (P1, P2, VCC, GND, A, K, B, C, E, G, D, S, IN, OUT, +, -)
  if (ports[pkey]) return { compId: cid, portKey: pkey, ...ports[pkey] };
  // p{N} index match
  if (/^p\d+$/.test(pkey) && ports[pkey]) return { compId: cid, portKey: pkey, ...ports[pkey] };
  // Match by pin number (for ICs)
  for (const [pk, pv] of Object.entries(ports)) {
    if (pv.pinNumber !== undefined && pv.pinNumber === pkey)
      return { compId: cid, portKey: pk, ...pv };
  }
  // Match by pin name (for ICs, case-insensitive)
  for (const [pk, pv] of Object.entries(ports)) {
    if (pv.pinName && pv.pinName.toLowerCase() === pkey.toLowerCase())
      return { compId: cid, portKey: pk, ...pv };
  }
  // Numeric shorthand: "1"→P1, "2"→P2
  const numMap = { '1': 'P1', '2': 'P2', '3': 'P3' };
  if (numMap[pkey] && ports[numMap[pkey]]) return { compId: cid, portKey: numMap[pkey], ...ports[numMap[pkey]] };
  return null;
}

// Auto-place: assign {x,y,rotation} to every component
function nlAutoPlace(components, nets, profiles) {
  const placed = {};
  const isIC  = c => ['ic','amplifier'].includes(c.symType);
  const isPas = c => ['resistor','capacitor','capacitor_pol','inductor','diode','led'].includes(c.symType);
  const isTr  = c => ['npn','pnp','nmos','pmos'].includes(c.symType);

  const ics  = components.filter(isIC);
  const pas  = components.filter(isPas);
  const trs  = components.filter(isTr);
  const vccs = components.filter(c => c.symType === 'vcc');
  const gnds = components.filter(c => c.symType === 'gnd');

  // Reverse map: compId → nets[]
  const compNets = {};
  nets.forEach(net => net.pins.forEach(p => {
    const cid = p.split('.')[0];
    (compNets[cid] || (compNets[cid] = [])).push(net);
  }));

  // ICs: horizontal row at y=400
  ics.forEach((ic, i) => { placed[ic.id] = { x: nlSnap(500 + i * 340), y: 400, rotation: 0 }; });
  trs.forEach((tr, i) => { placed[tr.id] = { x: nlSnap(500 + ics.length * 340 + i * 200), y: 400, rotation: 0 }; });

  // Passives: place next to IC pins they connect to
  pas.forEach(p => {
    let bestX = null, bestY = 400;
    (compNets[p.id] || []).forEach(net => {
      net.pins.forEach(pinRef => {
        const cid = pinRef.split('.')[0];
        if (placed[cid] && (ics.find(ic => ic.id === cid) || trs.find(t => t.id === cid))) {
          const nm = net.name.toLowerCase();
          const isIn  = nm.includes('in') || nm === 'vin' || nm === 'vcc' || nm === 'vdd';
          const isOut = nm.includes('out') || nm === 'vout';
          if (bestX === null) bestX = placed[cid].x + (isOut ? 200 : -200);
        }
      });
    });
    if (bestX === null && ics.length) bestX = placed[ics[0].id].x - 200;
    if (bestX === null) bestX = 300;
    placed[p.id] = { x: nlSnap(bestX), y: bestY, rotation: 1 };
  });

  // VCC/GND: temporary positions — refined later after port positions are computed
  vccs.forEach((v, i) => { placed[v.id] = { x: nlSnap(300 + i * 140), y: 200, rotation: 0 }; });
  gnds.forEach((g, i) => { placed[g.id] = { x: nlSnap(300 + i * 140), y: 560, rotation: 0 }; });

  return placed;
}

// L-route between two points
function nlLRoute(a, b) {
  if (a.x === b.x || a.y === b.y) return [{ x: a.x, y: a.y }, { x: b.x, y: b.y }];
  return [{ x: a.x, y: a.y }, { x: b.x, y: a.y }, { x: b.x, y: b.y }];
}

// Route wires for a net — MST with L-routes
function nlRouteNet(portPositions) {
  if (portPositions.length < 2) return [];
  const wires = [];
  const connected = [portPositions[0]];
  const remaining = portPositions.slice(1);
  while (remaining.length) {
    let bestD = Infinity, bestC = null, bestRi = -1;
    remaining.forEach((r, ri) => connected.forEach(c => {
      const d = Math.abs(r.x - c.x) + Math.abs(r.y - c.y);
      if (d < bestD) { bestD = d; bestC = c; bestRi = ri; }
    }));
    if (bestRi < 0) break;
    const target = remaining.splice(bestRi, 1)[0];
    wires.push({ points: nlLRoute(bestC, target) });
    connected.push(target);
  }
  return wires;
}

app.post('/api/build-from-netlist', async (req, res) => {
  const { components, nets, name = 'Schematic' } = req.body;
  if (!Array.isArray(components) || !components.length)
    return res.status(400).json({ error: 'components array required' });
  if (!Array.isArray(nets) || !nets.length)
    return res.status(400).json({ error: 'nets array required' });

  // Load profiles
  const profiles = {};
  for (const comp of components) {
    try {
      const pf = path.join(DATASHEETS_DIR, comp.slug, 'profile.json');
      if (fs.existsSync(pf)) profiles[comp.slug] = JSON.parse(fs.readFileSync(pf, 'utf8'));
    } catch (_) {}
  }

  // Assign symTypes and copy into working array
  const comps = components.map(c => ({
    ...c,
    symType:    nlSymType(c.slug, profiles[c.slug]),
    designator: c.designator || c.id.toUpperCase(),
    value:      c.value || c.slug,
    rotation:   c.rotation || 0,
  }));

  // Auto-place
  const positions = nlAutoPlace(comps, nets, profiles);
  comps.forEach(c => Object.assign(c, positions[c.id] || { x: 400, y: 400, rotation: 0 }));

  // Compute port positions first pass
  const portCache = {};
  comps.forEach(c => { portCache[c.id] = nlPorts(c, profiles[c.slug]); });
  const compMap = Object.fromEntries(comps.map(c => [c.id, c]));

  // Refine VCC/GND positions: snap them directly above/below their connected pin
  comps.forEach(c => {
    if (c.symType !== 'vcc' && c.symType !== 'gnd') return;
    const isVcc = c.symType === 'vcc';
    const myNets = nets.filter(n => n.pins.some(p => p.split('.')[0] === c.id));
    let refX = c.x, refY = c.y, found = false;
    for (const net of myNets) {
      for (const pinRef of net.pins) {
        if (found) break;
        const cid = pinRef.split('.')[0];
        if (cid === c.id) continue;
        const resolved = nlResolvePin(pinRef, compMap, portCache);
        if (resolved) {
          refX = resolved.x;
          refY = isVcc ? resolved.y - 80 : resolved.y + 80;
          found = true;
        }
      }
      if (found) break;
    }
    c.x = nlSnap(refX);
    c.y = nlSnap(isVcc ? refY - 20 : refY + 20);
    portCache[c.id] = nlPorts(c, profiles[c.slug]);
  });

  // Route wires for each net
  const allWires = [];
  let wIdx = 0;
  nets.forEach(net => {
    const pts = net.pins
      .map(pinRef => nlResolvePin(pinRef, compMap, portCache))
      .filter(Boolean)
      .map(r => ({ x: r.x, y: r.y }));
    nlRouteNet(pts).forEach(w => allWires.push({ id: `w${++wIdx}`, points: w.points }));
  });

  // Assemble and save project
  const project = {
    name,
    components: comps.map(({ symType, ...rest }) => ({ ...rest, symType })),
    wires: allWires,
    labels: [],
  };
  project.id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  project.created_at = new Date().toISOString();
  project.updated_at = new Date().toISOString();
  fs.writeFileSync(path.join(PROJECTS_DIR, project.id + '.json'), JSON.stringify(project, null, 2));

  res.json({ id: project.id, project });
});

// ── AI Project Builder ────────────────────────────────────────────────────────
// ── AI Build auto-runner ──────────────────────────────────────────────────────
const _activeBuilds = new Set(); // prevent duplicate spawns per genTicketId

function spawnBuildAgent(promptText, genTicketId) {
  if (_activeBuilds.has(genTicketId)) return;
  _activeBuilds.add(genTicketId);

  const claudeBin = path.join(
    process.env.USERPROFILE || process.env.HOME || '',
    '.local', 'bin', 'claude'
  );

  const childEnv = { ...process.env };
  delete childEnv.CLAUDECODE; // allow nested claude session

  const child = spawn(claudeBin, ['--print', '--dangerously-skip-permissions'], {
    cwd: __dirname,
    env: childEnv,
    stdio: ['pipe', 'pipe', 'pipe'],
    windowsHide: true,
  });

  child.stdin.write(promptText);
  child.stdin.end();

  console.log(`[build] Spawned agent for gen ticket ${genTicketId} (pid ${child.pid})`);

  // Mark gen ticket inprogress
  try {
    const data = readGenTickets();
    const t = data.tickets.find(t => t.id === genTicketId);
    if (t) { t.status = 'inprogress'; t.observations = 'Agent spawned, working…'; saveGenTickets(data); }
  } catch (_) {}

  let stderr = '';
  child.stderr?.on('data', d => { stderr += d.toString(); stderr = stderr.slice(-600); });

  child.on('close', code => {
    _activeBuilds.delete(genTicketId);
    console.log(`[build] Agent for gen ticket ${genTicketId} exited with code ${code}`);
    // If the agent didn't mark the ticket done itself, mark it failed
    try {
      const data = readGenTickets();
      const t = data.tickets.find(t => t.id === genTicketId);
      if (t && t.status !== 'done') {
        t.status = 'cancelled';
        t.observations = code === 0
          ? 'Agent completed but did not produce a project.'
          : `Agent exited with code ${code}. ${stderr.slice(-300)}`;
        saveGenTickets(data);
      }
    } catch (_) {}
  });

  child.on('error', err => {
    _activeBuilds.delete(genTicketId);
    console.error(`[build] Failed to spawn agent for gen ticket ${genTicketId}:`, err.message);
    try {
      const data = readGenTickets();
      const t = data.tickets.find(t => t.id === genTicketId);
      if (t && t.status !== 'done') {
        t.status = 'cancelled';
        t.observations = `Failed to spawn agent: ${err.message}`;
        saveGenTickets(data);
      }
    } catch (_) {}
  });
}

// POST /api/build-project  { prompt, userNotes? }
// Creates a gen ticket for Claude Code to build a schematic project.
app.post('/api/build-project', (req, res) => {
  const prompt = (req.body.prompt || '').trim();
  if (!prompt) return res.status(400).json({ error: 'prompt required' });

  // Read library index for context
  let libSummary = '';
  try {
    const idx = JSON.parse(fs.readFileSync(path.join(DATASHEETS_DIR, 'index.json'), 'utf8'));
    const userParts = Object.values(idx).filter(p => !p.builtin && p.status !== 'pending_parse');
    const builtins = ['LM7805','AMS1117-3.3','NE555','LM358','L298N','DRV8833','ATMEGA328P',
                      'ESP32_WROOM_32','RESISTOR','CAPACITOR','INDUCTOR','DIODE','LED',
                      '2N2222','BC547','IRF540N','VCC','GND'];
    libSummary = 'BUILTIN: ' + builtins.join(', ');
    if (userParts.length) {
      libSummary += '\nUSER LIBRARY: ' + userParts.map(p =>
        `${p.slug} (${p.part_number||p.slug} — ${(p.description||'').slice(0,60)})`
      ).join('; ');
    }
  } catch (_) { libSummary = 'Could not read library index.'; }

  // Create gen ticket with full instructions
  const genData = readGenTickets();
  const genTicket = {
    id: genData.nextId++,
    type: 'build-project',
    slug: '',
    title: `Build: ${prompt.slice(0,60)}`,
    prompt: '',
    status: 'pending',
    observations: '',
    created_at: new Date().toISOString(),
  };
  const genPrompt = `AI PROJECT BUILDER REQUEST
Gen Ticket ID: ${genTicket.id}

USER PROMPT: ${prompt}

AVAILABLE COMPONENTS:
${libSummary}

INSTRUCTIONS:
Read PROJECT_BUILDER_GUIDE.md — it has the full workflow and pin reference syntax.

YOUR TASKS:
1. Register as an agent (id: "build-${genTicket.id}-xxxx")
2. Choose components and design the net connections
3. POST to http://localhost:3030/api/build-from-netlist (server auto-places + routes — do NOT compute coordinates yourself)
4. PUT http://localhost:3030/api/gen-tickets/${genTicket.id} with { "observations": "PROJECT_ID:<the_id>", "status": "done" }
5. Unregister agent

The netlist format is simple — just list components and which pins connect to which nets.
See PROJECT_BUILDER_GUIDE.md Step 4 for a worked example.
Be practical — a 5-component circuit that works beats a 15-component one that doesn't.`;

  genTicket.prompt = genPrompt;
  genData.tickets.push(genTicket);
  saveGenTickets(genData);

  res.json({ genTicketId: genTicket.id });

  // Spawn the agent automatically — fire and forget
  spawnBuildAgent(genPrompt, genTicket.id);
});

// ── Export full library as a single JSON bundle ──────────────────────────────
// GET /api/export-library — all profile.json files packed into one object
app.get('/api/export-library', (req, res) => {
  const bundle = {};
  if (fs.existsSync(DATASHEETS_DIR)) {
    for (const slug of fs.readdirSync(DATASHEETS_DIR)) {
      if (slug.startsWith('_')) continue;
      const profilePath = path.join(DATASHEETS_DIR, slug, 'profile.json');
      if (!fs.existsSync(profilePath)) continue;
      try {
        const p = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
        delete p.raw_text; // strip bloat — not needed for rendering
        bundle[slug] = p;
      } catch {}
    }
  }
  // Include gen tickets scoped to the exported slugs
  const slugSet = new Set(Object.keys(bundle));
  const genData = readGenTickets();
  const genTickets = genData.tickets.filter(t => slugSet.has(t.slug));
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Content-Disposition', 'attachment; filename="library.json"');
  res.json({ format: 'schematic-library-v1', exported_at: new Date().toISOString(), components: bundle, gen_tickets: genTickets });
});

// ── Import library bundle ────────────────────────────────────────────────────
// POST /api/import-library — installs missing profiles; existing ones are NOT overwritten
app.post('/api/import-library', (req, res) => {
  const body = req.body;
  if (body.format !== 'schematic-library-v1') {
    return res.status(400).json({ error: 'Unrecognised format — expected schematic-library-v1' });
  }
  const components = body.components || {};
  const installed = [], skipped = [];
  for (const [slug, profile] of Object.entries(components)) {
    const dir = path.join(DATASHEETS_DIR, slug);
    const profilePath = path.join(dir, 'profile.json');
    if (fs.existsSync(profilePath)) { skipped.push(slug); continue; }
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(profilePath, JSON.stringify(profile, null, 2));
    installed.push(slug);
  }
  if (installed.length) updateIndex();
  // Import gen tickets — skip any whose (type+slug+title) already exists to avoid duplicates
  let ticketsImported = 0;
  if (Array.isArray(body.gen_tickets) && body.gen_tickets.length) {
    const genData = readGenTickets();
    const existing = new Set(genData.tickets.map(t => `${t.type}|${t.slug}|${t.title}`));
    for (const t of body.gen_tickets) {
      const key = `${t.type}|${t.slug}|${t.title}`;
      if (existing.has(key)) continue;
      const ticket = { ...t, id: genData.nextId++ };
      genData.tickets.push(ticket);
      existing.add(key);
      ticketsImported++;
    }
    if (ticketsImported) saveGenTickets(genData);
  }
  res.json({ ok: true, installed, skipped, ticketsImported });
});

// ── Delete component ────────────────────────────────────────────────────────
app.delete('/api/library/:slug', (req, res) => {
  const partDir = path.join(DATASHEETS_DIR, req.params.slug);
  if (fs.existsSync(partDir)) fs.rmSync(partDir, { recursive: true });
  updateIndex();
  res.json({ ok: true });
});

// ── SSE for live updates ────────────────────────────────────────────────────
const sseClients = new Set();
app.get('/api/events', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  sseClients.add(res);
  req.on('close', () => sseClients.delete(res));
});

function broadcast(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  sseClients.forEach(c => c.write(msg));
}

// Watch library folder for changes (Claude Code writes profile.json)
import chokidar from 'chokidar';
chokidar.watch(path.join(DATASHEETS_DIR, '**/profile.json'), { ignoreInitial: false })
  .on('add', () => updateIndex())
  .on('change', (filePath) => {
    const slug = path.basename(path.dirname(filePath));
    updateIndex();
    broadcast('profile_updated', { slug });
  });

// ── Helpers ─────────────────────────────────────────────────────────────────
function readIndex() {
  const indexPath = path.join(DATASHEETS_DIR, 'index.json');
  if (!fs.existsSync(indexPath)) return {};
  return JSON.parse(fs.readFileSync(indexPath, 'utf8'));
}

function updateIndex() {
  const index = {};
  if (!fs.existsSync(DATASHEETS_DIR)) return;
  for (const slug of fs.readdirSync(DATASHEETS_DIR)) {
    if (slug.startsWith('_')) continue;
    const profilePath = path.join(DATASHEETS_DIR, slug, 'profile.json');
    if (!fs.existsSync(profilePath)) continue;
    const p = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
    index[slug] = {
      slug,
      part_number: p.part_number || slug,
      description: p.description || '',
      confidence: p.confidence || 'HIGH',
      status: p.status || 'parsed',
      parsed_at: p.parsed_at,
      uploaded_at: p.uploaded_at || null,
      pin_count: (p.pins || []).length,
      builtin: p.builtin || false
    };
  }
  fs.writeFileSync(path.join(DATASHEETS_DIR, 'index.json'), JSON.stringify(index, null, 2));
  broadcast('library_updated', { count: Object.keys(index).length });
}

// ── Agent Registry ────────────────────────────────────────────────────────────
// In-memory store. Agents register on start, ping every ~20s, unregister on done.
// Entries older than STALE_MS with no ping are auto-marked stale.
const STALE_MS = 90_000;  // 90 seconds without a ping = stale
const agentRegistry = new Map(); // id → agent record

function agentRecord(id) {
  return agentRegistry.get(id) || null;
}

function pruneStale() {
  const now = Date.now();
  for (const [id, agent] of agentRegistry) {
    if (agent.status === 'working' && now - new Date(agent.last_ping).getTime() > STALE_MS) {
      agent.status = 'stale';
    }
  }
}

// GET /api/agents — list all agents (prunes stale first)
app.get('/api/agents', (req, res) => {
  pruneStale();
  res.json(Array.from(agentRegistry.values()).sort((a, b) => new Date(b.started_at) - new Date(a.started_at)));
});

// POST /api/agents/register — agent calls this on startup
// Body: { id, name, task, ticket_id?, worktree?, model? }
app.post('/api/agents/register', (req, res) => {
  const { id, name, task, ticket_id, worktree, model } = req.body;
  if (!id || !name) return res.status(400).json({ error: 'id and name required' });
  const now = new Date().toISOString();
  const agent = {
    id,
    name: name || 'Claude Code',
    task: task || '',
    ticket_id: ticket_id || null,
    worktree: worktree || null,
    model: model || null,
    status: 'working',
    started_at: now,
    last_ping: now,
    observations: '',
    steps: []
  };
  agentRegistry.set(id, agent);
  broadcast('agent_registered', { id, name, task });
  res.json({ ok: true, agent });
});

// POST /api/agents/:id/ping — heartbeat, update observations
// Body: { observations?, step?, status? }
app.post('/api/agents/:id/ping', (req, res) => {
  const agent = agentRecord(req.params.id);
  if (!agent) return res.status(404).json({ error: 'Agent not registered' });
  agent.last_ping = new Date().toISOString();
  if (req.body.observations !== undefined) agent.observations = req.body.observations;
  if (req.body.step) agent.steps.push({ t: agent.last_ping, msg: req.body.step });
  if (req.body.status) agent.status = req.body.status;
  broadcast('agent_ping', { id: req.params.id, status: agent.status, observations: agent.observations });
  res.json({ ok: true });
});

// DELETE /api/agents/:id — agent unregisters when done/failed
// Body: { status?, observations? }
app.delete('/api/agents/:id', (req, res) => {
  const agent = agentRecord(req.params.id);
  if (!agent) return res.status(404).json({ ok: true }); // idempotent
  agent.status = req.body?.status || 'done';
  if (req.body?.observations) agent.observations = req.body.observations;
  agent.last_ping = new Date().toISOString();
  broadcast('agent_done', { id: req.params.id, status: agent.status });
  // Keep in registry for 10 minutes so the UI can show completed agents
  setTimeout(() => agentRegistry.delete(req.params.id), 600_000);
  res.json({ ok: true });
});

app.listen(PORT, () => {
  updateIndex();
  console.log(`\nSchematic Designer running at http://localhost:${PORT}`);
  console.log(`Datasheets folder: ${DATASHEETS_DIR}\n`);
});
