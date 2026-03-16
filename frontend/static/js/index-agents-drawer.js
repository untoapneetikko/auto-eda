
// ── Agents View ──────────────────────────────────────────────────────────────

let _agentsTimer = null;
let _agentsLastLoad = 0;
// Unread completions: incremented when an agent finishes while the drawer is closed.
// Persisted in localStorage so a page refresh doesn't lose the indicator.
let _agentsUnread = parseInt(localStorage.getItem('agents_unread') || '0', 10);

function _agentsMarkUnread(count) {
  _agentsUnread = count;
  localStorage.setItem('agents_unread', count);
  _agentsRefreshBadge();
}

function _agentsRefreshBadge() {
  const badge = document.getElementById('agents-toggle-badge');
  if (!badge) return;
  const running = Object.values(_paState).filter(a => a.status === 'running').length;
  if (running > 0) {
    badge.textContent = running;
    badge.classList.add('visible');
    badge.classList.remove('done');
  } else if (_agentsUnread > 0) {
    badge.textContent = _agentsUnread;
    badge.classList.add('visible', 'done');
  } else {
    badge.classList.remove('visible', 'done');
  }
}

const CAT_ICON = { library:'📚', schematic:'📐', layout:'🔲', other:'⚙' };
const PRI_STYLE = { urgent:'background:rgba(239,68,68,0.2);color:#ef4444', high:'background:rgba(245,158,11,0.2);color:#f59e0b', medium:'background:rgba(108,99,255,0.2);color:#818cf8', low:'background:rgba(100,116,139,0.2);color:#64748b' };

function agentsTimeAgo(iso) {
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}

// How stale is the heartbeat? Returns { label, color }
function agentsPingAge(iso) {
  const secs = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (secs < 30) return { label: secs + 's ago', color: '#22c55e' };
  if (secs < 90) return { label: secs + 's ago', color: '#f59e0b' };
  return { label: Math.floor(secs/60) + 'm ago (stale)', color: '#ef4444' };
}

// Card for a LIVE registered agent (from /api/agents)
function agentsLiveCard(agent) {
  const ping = agentsPingAge(agent.last_ping);
  const dotCls = agent.status === 'working' ? 'working' : agent.status === 'done' ? 'done-dot' : 'waiting';
  const cardCls = agent.status === 'working' ? 'active' : agent.status === 'stale' ? 'locked-other' : '';
  const obs = agent.observations ? agent.observations.slice(0, 200) + (agent.observations.length > 200 ? '…' : '') : '';
  const ticketBadge = agent.ticket_id ? `<span class="agent-card-id">#${agent.ticket_id}</span>` : '';
  const wtBadge = agent.worktree ? `<span class="agent-card-cat" title="${esc(agent.worktree)}">⑂ branch</span>` : '';
  const recentSteps = (agent.steps || []).slice(-3);
  return `<div class="agent-card ${cardCls}">
    <div class="agent-card-top">
      <span class="agent-card-status ${dotCls}"></span>
      <span style="font-size:12px;font-weight:700;color:var(--text);">${esc(agent.name)}</span>
      ${ticketBadge}${wtBadge}
      <span style="margin-left:auto;font-size:10px;color:${ping.color};font-weight:700;" title="Last heartbeat">⬤ ${ping.label}</span>
    </div>
    <div class="agent-card-title">${esc(agent.task)}</div>
    ${obs ? `<div class="agent-card-obs">${esc(obs)}</div>` : ''}
    ${recentSteps.length ? `<div style="margin-top:3px;">${recentSteps.map(s=>`<div style="font-size:10px;color:var(--text-muted);line-height:1.6;">→ ${esc(s.msg)}</div>`).join('')}</div>` : ''}
    <div class="agent-card-time">Started ${agentsTimeAgo(agent.started_at)}${agent.model ? ' · ' + esc(agent.model) : ''}</div>
  </div>`;
}

async function agentsLoad() {
  clearTimeout(_agentsTimer);
  const now = Date.now();
  if (now - _agentsLastLoad < 2500) { _agentsTimer = setTimeout(agentsLoad, 2500); return; }
  _agentsLastLoad = now;

  await pipelineAgentsLoad();

  _agentsRefreshBadge();

  const running = Object.values(_paState).filter(a => a.status === 'running').length;
  const liveEl = document.getElementById('agents-live-indicator');
  if (liveEl) liveEl.style.display = running ? 'flex' : 'none';
  const acEl = document.getElementById('agents-active-count');
  if (acEl) acEl.textContent = running + ' running';

  const tsEl = document.getElementById('agents-last-refresh');
  if (tsEl) tsEl.textContent = new Date().toLocaleTimeString();

  // Auto-refresh every 3s while drawer is open; every 8s when agents are running (even with drawer closed)
  const drawer = document.getElementById('agents-drawer');
  const anyRunning = Object.values(_paState).some(a => a.status === 'running');
  if (drawer && drawer.classList.contains('open')) {
    _agentsTimer = setTimeout(agentsLoad, 3000);
  } else if (anyRunning) {
    _agentsTimer = setTimeout(agentsLoad, 8000);
  }
}

// ── Agents Drawer ─────────────────────────────────────────────────────────────
let _drawerOpen = false;

function toggleAgentsDrawer(forceOpen) {
  const drawer = document.getElementById('agents-drawer');
  _drawerOpen = forceOpen !== undefined ? forceOpen : !_drawerOpen;
  drawer.classList.toggle('open', _drawerOpen);
  // Slide the toggle handle with the drawer
  const offset = _drawerOpen ? drawer.offsetWidth : 0;
  document.documentElement.style.setProperty('--drawer-toggle-right', offset + 'px');
  const navBtn = document.getElementById('nav-agents');
  if (navBtn) navBtn.classList.toggle('active', _drawerOpen);
  if (_drawerOpen) {
    _agentsMarkUnread(0); // clear unread when drawer opens
    agentsLoad();
    ahLoad();
  } else {
    clearTimeout(_agentsTimer);
  }
}

function toggleAdrSection(key) {
  const body = document.getElementById('adr-' + key + '-body');
  const hdr  = document.getElementById('adr-' + key + '-hdr');
  if (!body) return;
  const hidden = body.style.display === 'none';
  body.style.display = hidden ? '' : 'none';
  if (hdr) hdr.classList.toggle('collapsed', !hidden);
}

function _initDrawerResize() {
  const handle = document.getElementById('agents-drawer-resize');
  const drawer = document.getElementById('agents-drawer');
  if (!handle || !drawer) return;
  let startX, startW;
  handle.addEventListener('mousedown', e => {
    startX = e.clientX; startW = drawer.offsetWidth;
    handle.classList.add('dragging');
    document.addEventListener('mousemove', _onDrawerMove);
    document.addEventListener('mouseup', _onDrawerUp);
    e.preventDefault();
  });
}
function _onDrawerMove(e) {
  const drawer = document.getElementById('agents-drawer');
  const startX = _onDrawerMove._sx || 0, startW = _onDrawerMove._sw || drawer.offsetWidth;
  if (!_onDrawerMove._init) return;
  const dx = _onDrawerMove._sx - e.clientX;
  const newW = Math.max(300, Math.min(window.innerWidth * 0.8, _onDrawerMove._sw + dx));
  drawer.style.width = newW + 'px';
  document.documentElement.style.setProperty('--drawer-toggle-right', newW + 'px');
}
function _onDrawerUp() {
  const handle = document.getElementById('agents-drawer-resize');
  if (handle) handle.classList.remove('dragging');
  document.removeEventListener('mousemove', _onDrawerMove);
  document.removeEventListener('mouseup', _onDrawerUp);
  _onDrawerMove._init = false;
}

// fixed resize init — attach once
(function() {
  document.addEventListener('DOMContentLoaded', () => {
    const handle = document.getElementById('agents-drawer-resize');
    const drawer = document.getElementById('agents-drawer');
    if (!handle || !drawer) return;
    handle.addEventListener('mousedown', e => {
      const sx = e.clientX, sw = drawer.offsetWidth;
      handle.classList.add('dragging');
      function move(ev) {
        const dx = sx - ev.clientX;
        const nw = Math.max(300, Math.min(window.innerWidth * 0.82, sw + dx));
        drawer.style.width = nw + 'px';
      }
      function up() {
        handle.classList.remove('dragging');
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
      e.preventDefault();
    });
  });
})();

// ── Pipeline Agents ──────────────────────────────────────────────────────────
let _paState = {};        // name → agent summary object
let _paPollTimer = null;  // fallback poll when SSE events are missed

// Agent activity tracking for card feedback
const _paRunStart   = {};  // name → timestamp when agent started
const _paLastChunk  = {};  // name → timestamp of last received chunk
const _paActText    = {};  // name → current activity label text
let   _paTickTimer  = null;

function _paStartTick() {
  if (_paTickTimer) return;
  _paTickTimer = setInterval(() => {
    const now = Date.now();
    Object.keys(_paRunStart).forEach(name => {
      const elapsed = Math.round((now - _paRunStart[name]) / 1000);
      const sinceChunk = now - (_paLastChunk[name] || _paRunStart[name]);
      const stale = sinceChunk > 30000;
      const elEl = document.getElementById('pa-act-elapsed-' + name);
      const lblEl = document.getElementById('pa-act-lbl-' + name);
      if (elEl) {
        const mins = Math.floor(elapsed / 60);
        elEl.textContent = mins > 0 ? `${mins}m ${elapsed % 60}s` : `${elapsed}s`;
      }
      if (lblEl) {
        if (stale) {
          lblEl.textContent = _paActText[name] || 'Working…';
          lblEl.classList.remove('stale');
        } else {
          lblEl.textContent = _paActText[name] || 'Working…';
          lblEl.classList.remove('stale');
        }
      }
    });
  }, 1000);
}

function _paStopTickFor(name) {
  delete _paRunStart[name];
  delete _paLastChunk[name];
  delete _paActText[name];
  if (!Object.keys(_paRunStart).length && _paTickTimer) {
    clearInterval(_paTickTimer);
    _paTickTimer = null;
  }
}

function _paStartPoll(name) {
  _paStopPoll();
  _paPollTimer = setInterval(async () => {
    try {
      const hist = await fetch(`/api/pipeline/agents/${name}/history`).then(r => r.json());
      if (hist && hist.status !== 'running') {
        _paStopPoll();
        if (_activePaAgent === name) {
          _paRenderHistory(hist);
          _paHideThinking();
          _paUpdateModalStatus(hist.status === 'done' ? 'done' : hist.status === 'error' ? 'error' : 'idle');
          const btn = document.getElementById('pa-send-btn');
          if (btn) btn.disabled = false;
        }
      }
    } catch(e) {}
  }, 3000);
}
function _paStopPoll() {
  if (_paPollTimer) { clearInterval(_paPollTimer); _paPollTimer = null; }
}
let _activePaAgent = null; // currently open agent name
let _paTempMsgEl = null;  // optimistically-shown user msg element, cleared once SSE confirms it
const _paModelPref = {}; // agent name → model string
const _paThinkPref = {}; // agent name → bool
const _PA_DEFAULT_MODEL = 'claude-sonnet-4-6';

function _paSetModel(model) {
  if (_activePaAgent) _paModelPref[_activePaAgent] = model;
}

function _paGetModel() {
  return (_activePaAgent && _paModelPref[_activePaAgent]) || _PA_DEFAULT_MODEL;
}

function _paToggleThink() {
  if (!_activePaAgent) return;
  _paThinkPref[_activePaAgent] = !_paThinkPref[_activePaAgent];
  const btn = document.getElementById('pa-think-toggle');
  if (btn) btn.classList.toggle('on', !!_paThinkPref[_activePaAgent]);
}

function _paGetThink() {
  return !!(_activePaAgent && _paThinkPref[_activePaAgent]);
}

// ── Design Assistant ──────────────────────────────────────────────────────────
async function daSend() {
  const prompt = document.getElementById('da-prompt')?.value.trim();
  const agentName = document.getElementById('da-agent-select')?.value || 'orchestrator';
  if (!prompt) return;

  // Clear input
  const ta = document.getElementById('da-prompt');
  ta.value = '';
  ta.style.height = 'auto';

  // Open the chat modal for that agent pre-loaded, then send
  await pipelineAgentsLoad(); // make sure state is current
  if (!_paState[agentName]) {
    // Fallback: agents haven't loaded yet
    await new Promise(r => setTimeout(r, 600));
  }
  await paOpenChat(agentName);

  // Pre-fill and send the prompt
  const inp = document.getElementById('pa-chat-input');
  if (inp) inp.value = prompt;
  await paChatSend();
}

function paTimeAgo(iso) {
  if (!iso) return 'Never run';
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}

function _paCardCls(agent) {
  const st = agent.status;
  if (st === 'running') return 'pa-running';
  if (st === 'error') return 'pa-error';
  if (st === 'done' && agent.last_run) {
    const lastOpened = parseInt(localStorage.getItem('pa-opened-' + agent.name) || '0');
    if (new Date(agent.last_run).getTime() > lastOpened) return 'pa-unread';
  }
  return '';
}

const _PA_GROUPS = [
  { key: 'orchestrator', label: 'Orchestrator', icon: '🎯', names: ['orchestrator'] },
  { key: 'pipeline',     label: 'Pipeline',     icon: '⚡', names: ['datasheet-parser','component','footprint','example-schematic','schematic','connectivity','autoplace','autoroute','layout','layout-example'] },
  { key: 'gui',          label: 'GUI',          icon: '🖥️', names: ['library','schematic-gui','layout-gui','import-export'] },
];
const _paGroupCollapsed = {};

function paCard(agent) {
  const st = agent.status;
  const dotCls = st === 'running' ? 'running' : st === 'done' ? 'done' : st === 'error' ? 'error' : 'idle';
  const cardCls = _paCardCls(agent);
  const statusTitle = st === 'running' ? 'Running' : st === 'done' ? 'Done' : st === 'error' ? 'Error' : 'Idle';
  const msgBadge = agent.msg_count ? `<span class="pa-badge" style="font-size:9px;">${agent.msg_count}</span>` : '';
  const cloneBadge = agent.is_clone ? `<span class="pa-clone-badge">↳</span>` : '';
  const lastMsg = agent.last_msg ? `<span class="pa-msg-preview" id="pa-preview-${agent.name}">${esc(agent.last_msg)}</span>` : `<span class="pa-msg-preview" id="pa-preview-${agent.name}"></span>`;
  return `<div class="pa-card ${cardCls}" id="pa-card-${agent.name}" onclick="paOpenChat('${agent.name}')" title="${esc(agent.desc)}">
    <div class="pa-card-main">
      <span class="pa-icon">${agent.icon}</span>
      <span class="pa-name">${esc(agent.label)}</span>
      ${cloneBadge}${msgBadge}
      ${lastMsg}
      <span class="pa-status-dot ${dotCls}" title="${statusTitle}"></span>
    </div>
    <div class="pa-card-act" id="pa-act-${agent.name}">
      <span class="pa-act-label" id="pa-act-lbl-${agent.name}">Starting…</span>
      <span class="pa-act-elapsed" id="pa-act-elapsed-${agent.name}">0s</span>
    </div>
  </div>`;
}

function _paRenderGroup(group, agents) {
  const members = group.names.flatMap(n => agents.filter(a => a.name === n || (a.is_clone && a.base === n)));
  if (!members.length) return '';
  const collapsed = _paGroupCollapsed[group.key] ? 'collapsed' : '';
  const running = members.filter(a => a.status === 'running').length;
  const unread  = members.filter(a => _paCardCls(a) === 'pa-unread').length;
  const badge = running ? `<span class="pa-group-count" style="color:#f59e0b;">⚡${running}</span>`
              : unread  ? `<span class="pa-group-count" style="color:#4ade80;">●${unread}</span>`
              : `<span class="pa-group-count">${members.length}</span>`;
  return `<div class="pa-group ${collapsed}" id="pa-group-${group.key}">
    <div class="pa-group-hdr" onclick="_paToggleGroup('${group.key}')">
      <span class="pa-group-chevron">▾</span>
      <span style="font-size:12px;">${group.icon}</span>
      <span class="pa-group-label">${group.label}</span>
      ${badge}
    </div>
    <div class="pa-group-body">${members.map(paCard).join('')}</div>
  </div>`;
}

function _paToggleGroup(key) {
  _paGroupCollapsed[key] = !_paGroupCollapsed[key];
  const el = document.getElementById('pa-group-' + key);
  if (el) el.classList.toggle('collapsed', !!_paGroupCollapsed[key]);
}

async function pipelineAgentsLoad() {
  const agents = await fetch('/api/pipeline/agents').then(r=>r.json()).catch(()=>[]);
  const prevState = {..._paState};
  _paState = {};
  agents.forEach(a => {
    _paState[a.name] = a;
    const prev = prevState[a.name];
    // If agent is running and we don't have a run-start time, seed it so the tick works
    if (a.status === 'running' && !_paRunStart[a.name]) {
      _paRunStart[a.name] = Date.now();
      _paLastChunk[a.name] = Date.now();
      _paActText[a.name] = 'Working…';
      _paStartTick();
    } else if (a.status !== 'running' && _paRunStart[a.name]) {
      _paStopTickFor(a.name);
    }
    // Poll detected agent finished (SSE was missed) — reload chat pane with full history
    if (prev?.status === 'running' && a.status !== 'running') {
      if (!_drawerOpen) _agentsMarkUnread(_agentsUnread + 1);
      _paStopPoll();
      if (_activePaAgent === a.name) {
        fetch(`/api/pipeline/agents/${a.name}/history`)
          .then(r => r.json())
          .then(hist => {
            if (_activePaAgent !== a.name) return;
            _paRenderHistory(hist);
            _paHideThinking();
            _paUpdateModalStatus(a.status === 'done' ? 'done' : a.status === 'error' ? 'error' : 'idle');
            const btn = document.getElementById('pa-send-btn');
            if (btn) btn.disabled = false;
          }).catch(() => {});
      }
    }
  });
  const grid = document.getElementById('pipeline-agents-grid');
  if (!grid) return;
  if (!agents.length) { grid.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px 6px;">No agents found.</div>'; return; }
  // Agents not in any group (clones, unknown) fall into pipeline
  const known = new Set(_PA_GROUPS.flatMap(g => g.names));
  const extras = agents.filter(a => !known.has(a.base || a.name) && !a.is_clone);
  const extendedGroups = _PA_GROUPS.map(g =>
    g.key === 'pipeline' ? {...g, names: [...g.names, ...extras.map(a=>a.name)]} : g
  );
  grid.innerHTML = extendedGroups.map(g => _paRenderGroup(g, agents)).join('');
}

// ── Chat Modal ────────────────────────────────────────────────────────────────

async function paOpenChat(name) {
  _activePaAgent = name;
  localStorage.setItem('pa-opened-' + name, Date.now()); // mark as read
  const agent = _paState[name];
  if (!agent) return;

  // Ensure drawer is open
  if (!_drawerOpen) toggleAgentsDrawer(true);

  // Update pane header
  document.getElementById('pa-modal-icon').textContent = agent.icon;
  document.getElementById('pa-modal-title').textContent = agent.label;
  document.getElementById('pa-modal-agent-name').textContent = `agents/${name}`;
  const sel = document.getElementById('pa-model-select');
  if (sel) sel.value = _paModelPref[name] || _PA_DEFAULT_MODEL;
  const thinkBtn = document.getElementById('pa-think-toggle');
  if (thinkBtn) thinkBtn.classList.toggle('on', !!_paThinkPref[name]);

  // Show inline pane
  document.getElementById('pa-chat-pane').classList.add('open');

  // Update status badge
  _paUpdateModalStatus(agent.status);

  // Clear immediately so previous agent's messages don't flash
  const area = document.getElementById('pa-chat-area');
  if (area) area.innerHTML = '<div class="pa-system-line">Loading…</div>';

  // Load history — guard against race: user may switch agents before fetch resolves
  const hist = await fetch(`/api/pipeline/agents/${name}/history`).then(r=>r.json()).catch(()=>({messages:[],status:'idle'}));
  if (_activePaAgent !== name) return; // switched away while fetching
  _paRenderHistory(hist);

  // Focus input
  setTimeout(() => document.getElementById('pa-chat-input')?.focus(), 100);
}

function paChatClose() {
  document.getElementById('pa-chat-pane').classList.remove('open');
  _activePaAgent = null;
  _paStopPoll();
}

function _paUpdateModalStatus(status) {
  const el = document.getElementById('pa-modal-status');
  if (!el) return;
  el.className = `pa-modal-status ${status}`;
  el.textContent = status;
  const dot = document.querySelector('.pa-modal-dot');
  if (dot) {
    dot.style.background = status==='running'?'#6c63ff':status==='done'?'#16a34a':status==='error'?'#ef4444':'#374151';
    dot.style.animation = status==='running'?'agentPulse2 2s infinite':'none';
  }
}

function _paRenderHistory(hist) {
  const area = document.getElementById('pa-chat-area');
  if (!area) return;
  area.innerHTML = '';

  const session = hist.has_session ? `<div class="pa-system-line">— Session resumed —</div>` : `<div class="pa-system-line">— New session —</div>`;
  area.insertAdjacentHTML('beforeend', session);

  (hist.messages || []).forEach(msg => _paAppendMsg(msg, false));

  if (hist.status === 'running') {
    _paShowThinking();
  }

  // Update session id
  const sessEl = document.getElementById('pa-modal-session');
  if (sessEl) sessEl.textContent = hist.has_session ? '⬤ session active' : '';

  _paUpdateModalStatus(hist.status);
  _paScrollBottom();
}

function _paAppendMsg(msg, scroll=true) {
  const area = document.getElementById('pa-chat-area');
  if (!area) return;
  const isUser = msg.role === 'user';
  const tsStr = msg.ts ? new Date(msg.ts).toLocaleTimeString() : '';
  const html = `<div class="pa-msg pa-msg-${isUser?'user':'assistant'}" id="pa-msg-${msg.id}">
    <div class="pa-msg-bubble">${isUser ? esc(msg.content) : _paFormatAssistant(msg.content)}</div>
    <div class="pa-msg-ts">${tsStr}${msg.streaming?' · typing…':''}</div>
  </div>`;
  area.insertAdjacentHTML('beforeend', html);
  if (scroll) _paScrollBottom();
}

function _paFormatAssistant(text) {
  // Render ## Plan / ## Progress blocks as visual checklists
  const planBlockRe = /^##\s+(Plan|Progress)\s*\n((?:[ \t]*[-*]\s+[\[→].*\n?)*)/gm;
  let out = text.replace(planBlockRe, (_, title, body) => {
    const steps = body.split('\n').filter(l => l.trim());
    const rows = steps.map(line => {
      const m = line.match(/^\s*[-*]\s+(\[[ x→]\]|→)\s+(.*)/i);
      if (!m) return '';
      const marker = m[1].toLowerCase();
      const label = m[2];
      if (marker === '[x]') {
        return `\x01<span class="pa-plan-step done"><span class="pa-plan-check">✓</span><span>${esc(label)}</span></span>\x01`;
      } else if (marker === '→' || marker === '[→]') {
        return `\x01<span class="pa-plan-step active"><span class="pa-plan-check">→</span><span>${esc(label)}</span></span>\x01`;
      } else {
        return `\x01<span class="pa-plan-step pending"><span class="pa-plan-check">○</span><span>${esc(label)}</span></span>\x01`;
      }
    }).filter(Boolean).join('');
    return `\x01<span class="pa-plan-block"><span class="pa-plan-title">${esc(title)}</span>${rows}</span>\x01`;
  });

  // Escape remaining text (non-plan parts) and apply other formatters
  // Split on \x01 markers to avoid double-escaping plan HTML
  return out.split('\x01').map((chunk, i) => {
    if (i % 2 === 1) return chunk; // already HTML (plan block)
    return esc(chunk)
      .replace(/▶ ([^\n]+)/g, '<span class="pa-tool-line">▶ $1</span>')
      .replace(/◀ ((?:[^\n]|\n(?![▶◀💭]))+)/g, '<span class="pa-result-line">◀ $1</span>')
      .replace(/💭 ((?:[^\n]|\n(?![▶◀]))+)/g, '<span class="pa-thinking-line">💭 $1</span>');
  }).join('');
}

function _paShowThinking(msg) {
  const area = document.getElementById('pa-chat-area');
  if (!area) return;
  let el = document.getElementById('pa-thinking-indicator');
  if (!el) {
    area.insertAdjacentHTML('beforeend', `<div class="pa-thinking" id="pa-thinking-indicator">
      <div class="pa-thinking-dots"><span></span><span></span><span></span></div>
      <span id="pa-thinking-label">${esc(msg || 'Thinking…')}</span>
    </div>`);
  } else {
    const lbl = el.querySelector('#pa-thinking-label');
    if (lbl) lbl.textContent = msg || 'Thinking…';
  }
  _paScrollBottom();
}

function _paHideThinking() {
  document.getElementById('pa-thinking-indicator')?.remove();
}

function _paScrollBottom() {
  const area = document.getElementById('pa-chat-area');
  if (area) area.scrollTop = area.scrollHeight;
}

async function paChatSend() {
  if (!_activePaAgent) return;
  const agentName = _activePaAgent; // capture before any await
  const inp = document.getElementById('pa-chat-input');
  const msg = inp?.value.trim();
  if (!msg) return;
  inp.value = '';
  inp.style.height = 'auto';

  const btn = document.getElementById('pa-send-btn');
  if (btn) btn.disabled = true;

  // Show user message immediately — don't wait for SSE
  const now = new Date().toISOString();
  const tempId = 'tmp-' + Date.now();
  _paAppendMsg({ id: tempId, role: 'user', content: msg, ts: now });
  _paTempMsgEl = document.getElementById('pa-msg-' + tempId); // hold ref for SSE dedup
  _paShowThinking('Sending…');

  const res = await fetch(`/api/pipeline/agents/${agentName}/chat`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({message: msg, model: _paGetModel(), think: _paGetThink()}),
  }).then(r=>r.json()).catch(e=>({error: String(e)}));

  if (res.error) {
    _paHideThinking();
    const area = document.getElementById('pa-chat-area');
    if (area) area.insertAdjacentHTML('beforeend', `<div class="pa-system-line" style="color:#ef4444;">Error: ${esc(res.error)}</div>`);
    if (btn) btn.disabled = false;
  } else {
    _paShowThinking('Waiting for agent…');

    // If the backend routed to a different (cloned) instance, switch the chat pane to it
    const actualName = res.actual_name || agentName;
    if (actualName !== agentName) {
      // Show notification banner
      const area = document.getElementById('pa-chat-area');
      if (area) area.insertAdjacentHTML('beforeend',
        `<div class="pa-system-line" style="color:#f59e0b;">↳ Routed to <b>${esc(actualName)}</b> (parallel instance)</div>`);
      // Move temp message to the cloned agent's chat pane
      _paStopPoll();
      await paOpenChat(actualName);
    }
    _paStartPoll(actualName);
  }
}

function paChatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    paChatSend();
  }
}

async function paChatStop() {
  if (!_activePaAgent) return;
  await fetch(`/api/pipeline/agents/${_activePaAgent}/stop`, {method:'POST'}).catch(()=>{});
}

async function paChatClear() {
  if (!_activePaAgent) return;
  if (!confirm('Clear all chat history for this agent?')) return;
  await fetch(`/api/pipeline/agents/${_activePaAgent}/history`, {method:'DELETE'}).catch(()=>{});
  const area = document.getElementById('pa-chat-area');
  if (area) area.innerHTML = '<div class="pa-system-line">— History cleared. New session will start. —</div>';
  _paUpdateModalStatus('idle');
  pipelineAgentsLoad();
}

// ── SSE handler ───────────────────────────────────────────────────────────────
function _handlePipelineSSE(evt, data) {
  const name = data.name;

  // Update card in grid
  if (_paState[name]) {
    if (evt === 'pipeline_agent_started') {
      _paState[name].status = 'running';
      _paRunStart[name] = Date.now();
      _paLastChunk[name] = Date.now();
      _paActText[name] = 'Starting…';
      _paStartTick();
    } else if (evt === 'pipeline_agent_chunk') {
      _paLastChunk[name] = Date.now();
      // Derive activity label from chunk type
      if (data.is_tool) {
        _paActText[name] = (data.chunk || '').replace(/^\n[▶◀] /, '').split('\n')[0].trim() || 'Running tool…';
      } else if (data.is_thinking) {
        _paActText[name] = 'Reasoning…';
      } else if (data.is_result) {
        _paActText[name] = 'Reading result…';
      } else {
        _paActText[name] = 'Writing response…';
      }
      // Update preview element live
      const previewEl = document.getElementById('pa-preview-' + name);
      if (previewEl && !data.is_tool && !data.is_thinking && !data.is_result) {
        previewEl.textContent = (data.chunk || '').slice(-80).replace(/\n/g, ' ').trim();
      }
    } else if (evt === 'pipeline_agent_done') {
      _paState[name].status = data.status === 'done' ? 'done' : data.status === 'error' ? 'error' : 'idle';
      _paStopTickFor(name);
      if (!_drawerOpen) _agentsMarkUnread(_agentsUnread + 1);
      _agentsRefreshBadge();
    }
    const card = document.getElementById('pa-card-' + name);
    if (card) {
      const cls = _paCardCls(_paState[name]);
      card.className = `pa-card${cls ? ' ' + cls : ''}`;
    }
  }

  // On done: reload history into server cache regardless of pane state, re-render if open
  if (evt === 'pipeline_agent_done') {
    setTimeout(agentsLoad, 400);
    fetch(`/api/pipeline/agents/${name}/history`)
      .then(r => r.json())
      .then(hist => { if (name === _activePaAgent) _paRenderHistory(hist); })
      .catch(() => {});
  }

  // Auto-open drawer and switch to the running agent so user can see it working
  if (evt === 'pipeline_agent_started') {
    toggleAgentsDrawer(true);
    if (name !== _activePaAgent) paOpenChat(name);
  }

  // Route live updates to pane only if this agent is open
  if (name !== _activePaAgent) return;

  if (evt === 'pipeline_agent_started') {
    _paUpdateModalStatus('running');
    if (data.msg) {
      if (_paTempMsgEl) {
        // Optimistic message already shown — just stamp it with the real id
        _paTempMsgEl.id = 'pa-msg-' + data.msg.id;
        _paTempMsgEl = null;
      } else if (!document.getElementById('pa-msg-' + data.msg.id)) {
        // Another tab / SSE reconnect: show it fresh
        _paAppendMsg(data.msg);
      }
    }
    _paShowThinking('Agent starting…');
    document.getElementById('pa-send-btn') && (document.getElementById('pa-send-btn').disabled = true);
  }
  else if (evt === 'pipeline_agent_msg_start') {
    _paHideThinking();
    _paAppendMsg(data.msg);
    _paShowThinking();
  }
  else if (evt === 'pipeline_agent_chunk') {
    let msgEl = document.getElementById('pa-msg-' + data.msg_id);
    // If msg_start was missed (e.g. SSE reconnect), create the bubble now
    if (!msgEl) {
      const area = document.getElementById('pa-chat-area');
      if (area) {
        area.insertAdjacentHTML('beforeend',
          `<div class="pa-msg pa-msg-assistant" id="pa-msg-${data.msg_id}"><div class="pa-msg-bubble"></div><div class="pa-msg-ts">typing…</div></div>`);
        msgEl = document.getElementById('pa-msg-' + data.msg_id);
      }
    }
    if (msgEl) {
      const bubble = msgEl.querySelector('.pa-msg-bubble');
      if (bubble) {
        const raw = bubble.dataset.raw || '';
        const newRaw = raw + (data.chunk || '');
        bubble.dataset.raw = newRaw;
        bubble.innerHTML = _paFormatAssistant(newRaw);
      }
      const ts = msgEl.querySelector('.pa-msg-ts');
      if (ts) ts.textContent = 'typing…';
    }
    _paScrollBottom();
    // Update live status indicator
    if (data.is_tool) {
      const toolLabel = (data.chunk || '').replace(/^\n▶ /, '').split('\n')[0].trim();
      _paShowThinking(toolLabel || `Running ${data.tool_name || 'tool'}…`);
    } else if (data.is_thinking) {
      _paShowThinking('Reasoning…');
    } else if (data.is_result) {
      _paShowThinking('Reading result…');
    } else {
      _paShowThinking('Responding…');
    }
  }
  else if (evt === 'pipeline_agent_done') {
    _paStopPoll();
    _paHideThinking();
    _paUpdateModalStatus(data.status === 'done' ? 'done' : data.status === 'error' ? 'error' : 'idle');
    const sessEl = document.getElementById('pa-modal-session');
    if (sessEl && data.session_id) sessEl.textContent = '⬤ session active';
    const btn = document.getElementById('pa-send-btn');
    if (btn) btn.disabled = false;
    document.getElementById('pa-chat-input')?.focus();
    setTimeout(agentsLoad, 400);
  }
}


