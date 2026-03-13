let _compEditTarget = null;
let _compEditEditor = null;

function openCompEditModal(comp, editorRef) {
  _compEditTarget = comp;
  _compEditEditor = editorRef;
  const isIC = comp.symType === 'ic' || !SYMDEFS[comp.symType];
  document.getElementById('comp-edit-title').textContent = `Edit ${isIC ? 'IC' : 'Component'}: ${comp.designator || comp.slug}`;
  document.getElementById('comp-edit-designator').value = comp.designator || '';
  document.getElementById('comp-edit-value').value = comp.value || '';
  document.getElementById('comp-edit-partnumber').value = comp.partNumber || '';
  const modal = document.getElementById('comp-edit-modal');
  modal.style.display = 'flex';
  setTimeout(() => document.getElementById('comp-edit-value').focus(), 50);
}

function closeCompEditModal() {
  document.getElementById('comp-edit-modal').style.display = 'none';
  _compEditTarget = null;
  _compEditEditor = null;
}

function saveCompEdit() {
  if (!_compEditTarget || !_compEditEditor) return;
  _compEditEditor._saveHist();
  _compEditTarget.designator = document.getElementById('comp-edit-designator').value.trim() || _compEditTarget.designator;
  _compEditTarget.value = document.getElementById('comp-edit-value').value.trim();
  _compEditTarget.partNumber = document.getElementById('comp-edit-partnumber').value.trim();
  _compEditEditor.dirty = true;
  _compEditEditor._render();
  closeCompEditModal();
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.getElementById('comp-edit-modal')?.style.display === 'flex') {
    saveCompEdit();
  }
  if (e.key === 'Enter' && document.getElementById('label-edit-modal')?.style.display === 'flex') {
    saveLabelEdit();
  }
  if (e.key === 'Escape' && document.getElementById('pa-modal')?.classList.contains('open')) {
    paChatClose();
  }
});

let _labelEditTarget = null;
let _labelEditEditor = null;

function openLabelEditModal(lbl, editorRef) {
  _labelEditTarget = lbl;
  _labelEditEditor = editorRef;
  document.getElementById('label-edit-name').value = lbl.name || '';
  const modal = document.getElementById('label-edit-modal');
  modal.style.display = 'flex';
  setTimeout(() => { const i = document.getElementById('label-edit-name'); i.focus(); i.select(); }, 50);
}

function closeLabelEditModal() {
  document.getElementById('label-edit-modal').style.display = 'none';
  _labelEditTarget = null;
  _labelEditEditor = null;
}

function saveLabelEdit() {
  if (!_labelEditTarget || !_labelEditEditor) return;
  const name = document.getElementById('label-edit-name').value.trim();
  if (!name) return;
  _labelEditEditor._saveHist();
  _labelEditTarget.name = name;
  _labelEditEditor.dirty = true;
  _labelEditEditor._render();
  closeLabelEditModal();
}

let _deleteProjectId = null;
let _deleteProjectName = null;

async function openDeleteProjectModal() {
  const id = openTabs.find(t => t.tabId === activeTabId)?.projectId;
  const name = editor.project?.name || '';
  if (!id) { alert('Save the project first before deleting it.'); return; }
  _deleteProjectId = id;
  _deleteProjectName = name;
  document.getElementById('delete-project-name-display').textContent = '"' + name + '"';
  const input = document.getElementById('delete-project-confirm-input');
  input.value = '';
  onDeleteConfirmInput('');

  // Fetch linked PCB boards and show them in the warning section
  const pcbSection = document.getElementById('delete-project-pcb-section');
  const pcbList = document.getElementById('delete-project-pcb-list');
  pcbSection.style.display = 'none';
  pcbList.innerHTML = '';
  try {
    const boards = await fetch(`/api/pcb-boards?projectId=${encodeURIComponent(id)}`).then(r => r.json());
    if (boards && boards.length > 0) {
      pcbList.innerHTML = boards.map(b =>
        `<div style="display:flex;align-items:center;gap:7px;font-size:11px;">
          <span style="color:#f87171;font-size:13px;">🔲</span>
          <span style="font-family:monospace;color:var(--text);">${esc(b.title || b.id)}</span>
          <span style="color:var(--text-muted);font-size:10px;">${b.component_count||0} comp${b.component_count!==1?'s':''}</span>
        </div>`
      ).join('');
      pcbSection.style.display = 'block';
    }
  } catch(_) {}

  document.getElementById('delete-project-modal').style.display = 'flex';
  setTimeout(() => input.focus(), 60);
}

function closeDeleteProjectModal() {
  document.getElementById('delete-project-modal').style.display = 'none';
  _deleteProjectId = null;
  _deleteProjectName = null;
}

function onDeleteConfirmInput(val) {
  const btn = document.getElementById('delete-project-confirm-btn');
  const match = _deleteProjectName && val.trim() === _deleteProjectName.trim();
  btn.disabled = !match;
  btn.style.opacity = match ? '1' : '0.45';
  btn.style.cursor = match ? 'pointer' : 'not-allowed';
}

async function doDeleteProject() {
  const input = document.getElementById('delete-project-confirm-input');
  if (!_deleteProjectId || !_deleteProjectName) return;
  if (input.value.trim() !== _deleteProjectName.trim()) return;
  const id = _deleteProjectId;
  closeDeleteProjectModal();
  await fetch(`/api/projects/${id}`, { method: 'DELETE' });
  const tab = openTabs.find(t => t.projectId === id);
  if (tab) closeTab(tab.tabId);
  await loadProjects();
}

// ── AI Project Builder ────────────────────────────────────────────────────────
let _buildGenTicketId = null;
let _buildProjectId = null;
let _buildPollTimer = null;

async function buildLoad() {
  // Populate library chips
  try {
    const lib = await fetch('/api/library').then(r => r.json());
    const grid = document.getElementById('build-lib-grid');
    if (!grid) return;
    const builtins = lib.filter(c => c.builtin);
    const user = lib.filter(c => !c.builtin);
    let html = '';
    builtins.forEach(c => {
      html += `<div class="build-lib-chip"><div class="build-lib-dot" style="background:#4b5563"></div><span>${esc(c.part_number || c.slug)}</span></div>`;
    });
    user.forEach(c => {
      html += `<div class="build-lib-chip user"><div class="build-lib-dot" style="background:#a78bfa"></div><span>${esc(c.part_number || c.slug)}</span></div>`;
    });
    grid.innerHTML = html || '<div style="color:var(--text-muted);font-size:12px;">No components in library yet.</div>';
  } catch(e) { /* ignore */ }
}

function buildUseExample(el) {
  const ta = document.getElementById('build-prompt');
  if (ta) ta.value = el.textContent.trim();
  ta?.focus();
}

async function buildSubmit() {
  const prompt = (document.getElementById('build-prompt')?.value || '').trim();
  if (!prompt) { alert('Please describe the circuit you want to build.'); return; }

  const btn = document.getElementById('build-submit-btn');
  btn.disabled = true;

  buildSetStatus('⏳', 'Queuing your build request…', 10, false, false);
  document.getElementById('build-status').classList.add('visible');

  try {
    const res = await fetch('/api/build-project', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt })
    }).then(r => r.json());

    if (!res.genTicketId) throw new Error(res.error || 'Failed to queue build');
    _buildGenTicketId = res.genTicketId;
    _buildProjectId = null;
    buildSetStatus('🤖', 'Agent is designing your circuit… This usually takes 1–2 minutes.', 30, false, false);
    buildStartPoll();
  } catch(e) {
    buildSetStatus('❌', `Error: ${e.message}`, 0, false, false);
    btn.disabled = false;
  }
}

function buildStartPoll() {
  if (_buildPollTimer) clearInterval(_buildPollTimer);
  _buildPollTimer = setInterval(buildPoll, 4000);
}

async function buildPoll() {
  if (!_buildGenTicketId) return;
  try {
    const data = await fetch('/api/gen-tickets').then(r => r.json());
    const ticket = (data.tickets || []).find(t => t.id === _buildGenTicketId);
    if (!ticket) return;
    if (ticket.status === 'done') {
      clearInterval(_buildPollTimer);
      _buildPollTimer = null;
      // Extract project ID from observations: "PROJECT_ID:xxx"
      const match = (ticket.observations || '').match(/PROJECT_ID:(\S+)/);
      if (match) {
        _buildProjectId = match[1];
        buildSetStatus('✅', 'Your project is ready!', 100, true, true);
      } else {
        buildSetStatus('⚠️', 'Build completed but no project was returned. The AI may not have found suitable components.', 100, false, true);
      }
    } else if (ticket.status === 'cancelled') {
      clearInterval(_buildPollTimer);
      _buildPollTimer = null;
      buildSetStatus('❌', `Build failed: ${ticket.observations || 'Unknown error'}`, 0, false, true);
    } else if (ticket.status === 'inprogress') {
      buildSetStatus('🤖', `Agent is working… ${ticket.observations ? '— ' + ticket.observations : ''}`, 60, false, false);
    }
  } catch(e) { /* network glitch, keep polling */ }
}

function buildSetStatus(icon, msg, pct, showOpen, showReset) {
  const el = {
    icon: document.getElementById('build-status-icon'),
    msg: document.getElementById('build-status-msg'),
    fill: document.getElementById('build-status-fill'),
    openBtn: document.getElementById('build-result-btn'),
    resetBtn: document.getElementById('build-reset-btn'),
  };
  if (el.icon) el.icon.textContent = icon;
  if (el.msg) el.msg.textContent = msg;
  if (el.fill) el.fill.style.width = pct + '%';
  if (el.openBtn) el.openBtn.style.display = showOpen ? 'block' : 'none';
  if (el.resetBtn) el.resetBtn.style.display = showReset ? 'block' : 'none';
}

async function buildOpenProject() {
  if (!_buildProjectId) return;
  await openProject(_buildProjectId);
}

function buildReset() {
  if (_buildPollTimer) { clearInterval(_buildPollTimer); _buildPollTimer = null; }
  _buildGenTicketId = null;
  _buildProjectId = null;
  document.getElementById('build-status')?.classList.remove('visible');
  document.getElementById('build-submit-btn').disabled = false;
}
</script>
