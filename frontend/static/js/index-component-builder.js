// ── Component Builder (quick manual component creator) ──────────────────────
let _cbPins = [];
const CB_TYPES = ['input','output','power','gnd','passive','bidirectional'];

function openComponentBuilder() {
  _cbPins = [
    { number: 1, name: 'VCC', type: 'power', description: 'Power supply' },
    { number: 2, name: 'GND', type: 'gnd', description: 'Ground' },
    { number: 3, name: 'IN', type: 'input', description: 'Input' },
    { number: 4, name: 'OUT', type: 'output', description: 'Output' }
  ];
  const modal = document.getElementById('comp-builder-modal');
  if (!modal) return;
  document.getElementById('cb-part-number').value = '';
  document.getElementById('cb-description').value = '';
  document.getElementById('cb-symbol-type').value = 'ic';
  renderCBPins();
  renderCBPreview();
  modal.style.display = 'flex';
}

function closeComponentBuilder() {
  const modal = document.getElementById('comp-builder-modal');
  if (modal) modal.style.display = 'none';
}

function renderCBPins() {
  const tbody = document.getElementById('cb-pin-tbody');
  tbody.innerHTML = _cbPins.map((pin, i) => `
    <tr>
      <td><input type="number" value="${pin.number??''}" oninput="cbUpdatePin(${i},'number',this.value)"
        style="width:38px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;"></td>
      <td><input type="text" value="${(pin.name||'').replace(/"/g,'&quot;')}" oninput="cbUpdatePin(${i},'name',this.value)"
        style="width:72px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;font-family:monospace;"></td>
      <td><select onchange="cbUpdatePin(${i},'type',this.value);renderCBPreview()"
        style="background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;">
        ${CB_TYPES.map(t => `<option value="${t}"${pin.type===t?' selected':''}>${t}</option>`).join('')}
      </select></td>
      <td><input type="text" value="${(pin.description||'').replace(/"/g,'&quot;')}" oninput="cbUpdatePin(${i},'description',this.value)"
        style="width:160px;background:var(--bg);border:1px solid var(--border);border-radius:3px;color:var(--text);padding:2px 4px;font-size:11px;"></td>
      <td><button onclick="cbRemovePin(${i})"
        style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);border-radius:3px;color:#ef4444;padding:2px 7px;cursor:pointer;font-size:11px;">✕</button></td>
    </tr>`).join('');
}

function cbUpdatePin(i, field, val) {
  if (!_cbPins[i]) return;
  _cbPins[i][field] = field === 'number' ? (val === '' ? null : parseInt(val)) : val;
}

function cbRemovePin(i) {
  _cbPins.splice(i, 1);
  renderCBPins();
  renderCBPreview();
}

function cbAddPin() {
  const maxN = _cbPins.reduce((m, p) => Math.max(m, p.number || 0), 0);
  _cbPins.push({ number: maxN + 1, name: 'P' + (maxN + 1), type: 'input', description: '' });
  renderCBPins();
  renderCBPreview();
}

function renderCBPreview() {
  const svgEl = document.getElementById('cb-preview-canvas');
  if (!svgEl) return;
  const symType = 'ic';
  const partNum = document.getElementById('cb-part-number')?.value || 'NEW';
  const fakeProfile = { part_number: partNum, pins: _cbPins };
  const fakeSlug = '__cb_preview__';
  profileCache[fakeSlug] = fakeProfile;
  const fakeComp = { symType, slug: fakeSlug, designator: partNum, value: '', rotation: 0 };
  const lay = editor._icLayout(fakeSlug);
  const lw = lay.BOX_W + 2 * lay.PIN_STUB + 40;
  const lh = lay.BOX_H + 30;
  const W = 240, H = 360;
  const s = Math.min(W / lw, H / lh, 1.2) * 0.85;
  let h = `<defs><pattern id="cb-gp" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="0" cy="0" r="0.7" fill="#1e2030"/></pattern></defs>`;
  h += `<rect width="100%" height="100%" fill="#0a0c12"/>`;
  h += `<rect width="100%" height="100%" fill="url(#cb-gp)"/>`;
  h += `<g transform="translate(${W/2},${H/2}) scale(${s})">`;
  h += editor._symH(fakeComp, false, false);
  for (const p of lay.ports) h += `<circle cx="${p.dx}" cy="${p.dy}" r="3" fill="rgba(96,165,250,0.5)"/>`;
  h += `</g>`;
  svgEl.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svgEl.innerHTML = h;
}

async function saveNewComponent() {
  const partNum = document.getElementById('cb-part-number').value.trim();
  if (!partNum) { alert('Enter a part number first.'); return; }
  const symType = document.getElementById('cb-symbol-type').value;
  const desc = document.getElementById('cb-description').value.trim();
  const pins = _cbPins.map(p => ({ ...p }));
  const res = await fetch('/api/library/new', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ part_number: partNum, description: desc, symbol_type: symType, pins })
  });
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 409) { alert('Component "' + partNum + '" already exists.'); return; }
    alert('Error: ' + (data.error || 'Unknown')); return;
  }
  closeComponentBuilder();
  await loadLibrary();
  const slug = data.slug;
  if (slug) {
    profileCache[slug] = { part_number: partNum, description: desc, symbol_type: symType, pins };
    editor.startPlace(slug, symType);
  }
}

async function saveCorrection() {
  const original = document.getElementById('corr-original').value.trim();
  const fix = document.getElementById('corr-fix').value.trim();
  const reason = document.getElementById('corr-reason').value.trim();
  if (!original || !fix) { alert('Fill in both fields.'); return; }
  await fetch(`/api/library/${selectedSlug}/correction`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ original, correction: fix, reason: reason || null })
  });
  await loadProfile(selectedSlug);
}

let _deletePartSlug = null;
let _deletePartName = null;

function deletePart() {
  if (!selectedSlug) return;
  _deletePartSlug = selectedSlug;
  const prof = Object.values(library).find(p => p.slug === selectedSlug);
  _deletePartName = prof?.part_number || selectedSlug;
  document.getElementById('delete-part-name-display').textContent = _deletePartName;
  document.getElementById('delete-part-confirm-input').value = '';
  const btn = document.getElementById('delete-part-confirm-btn');
  btn.disabled = true; btn.style.opacity = '0.45'; btn.style.cursor = 'not-allowed';
  document.getElementById('delete-part-modal').style.display = 'flex';
  setTimeout(() => document.getElementById('delete-part-confirm-input').focus(), 50);
}

function closeDeletePartModal() {
  document.getElementById('delete-part-modal').style.display = 'none';
  _deletePartSlug = null; _deletePartName = null;
}

function onDeletePartInput(val) {
  const match = val.trim() === _deletePartName;
  const btn = document.getElementById('delete-part-confirm-btn');
  btn.disabled = !match;
  btn.style.opacity = match ? '1' : '0.45';
  btn.style.cursor = match ? 'pointer' : 'not-allowed';
}

async function doDeletePart() {
  if (!_deletePartSlug) return;
  const input = document.getElementById('delete-part-confirm-input');
  if (input.value.trim() !== _deletePartName) return;
  await fetch(`/api/library/${_deletePartSlug}`, { method: 'DELETE' });
  closeDeletePartModal();
  selectedSlug = null;
  await loadLibrary();
  document.getElementById('view-library').innerHTML = `
    <div class="empty-state">
      <div class="big-icon">🔌</div>
      <div style="font-size:16px;font-weight:700;color:var(--text-dim);">No component selected</div>
    </div>`;
  showView('library');
}

function exportProfile() {
  const profile = Object.values(library).find(p => p.slug === selectedSlug);
  // fetch full profile for export
  fetch(`/api/library/${selectedSlug}`).then(r => r.json()).then(p => {
    const blob = new Blob([JSON.stringify(p, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${selectedSlug}_profile.json`;
    a.click();
  });
}

async function copyText(el) {
  await navigator.clipboard.writeText(el.textContent.trim());
  const orig = el.style.borderColor;
  el.style.borderColor = 'var(--green)';
  setTimeout(() => el.style.borderColor = orig, 800);
}

