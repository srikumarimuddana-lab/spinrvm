"""Web dashboard — configuration, status, reports, and LLM chat."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes — Spinr Ops</title>
<style>
:root {
  --bg: #0f1117; --card: #1a1d27; --border: #2a2d3a;
  --text: #e4e4e7; --muted: #9ca3af; --accent: #6366f1;
  --green: #22c55e; --red: #ef4444; --yellow: #eab308;
  --card-hover: #1f2230;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: var(--bg); color: var(--text);
  padding: 0; min-height: 100vh;
}

/* ── Nav ──────────────────────────────────────── */
nav {
  background: var(--card); border-bottom: 1px solid var(--border);
  padding: 0 24px; display: flex; align-items: center; gap: 24px; height: 56px;
  position: sticky; top: 0; z-index: 10;
}
nav .logo { font-size: 1.2rem; font-weight: 700; color: var(--accent); }
nav .tabs { display: flex; gap: 4px; }
nav .tab {
  padding: 8px 16px; border-radius: 8px; cursor: pointer;
  font-size: 0.9rem; color: var(--muted); transition: all 0.15s;
  border: none; background: none;
}
nav .tab:hover { color: var(--text); background: rgba(255,255,255,0.05); }
nav .tab.active { color: var(--text); background: rgba(99,102,241,0.15); }

/* ── Layout ───────────────────────────────────── */
main { max-width: 1200px; margin: 0 auto; padding: 24px; }
.page { display: none; } .page.active { display: block; }
h2.section { font-size: 1rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px;
}
.card h3 { font-size: 0.95rem; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }

/* ── Status badges ────────────────────────────── */
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot-on { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot-off { background: var(--red); }
.dot-warn { background: var(--yellow); }
.badge {
  display: inline-block; padding: 2px 10px; border-radius: 12px;
  font-size: 0.75rem; font-weight: 600;
}
.badge-on { background: #16331c; color: var(--green); }
.badge-off { background: #331616; color: var(--red); }
.badge-warn { background: #332b16; color: var(--yellow); }

/* ── Detail rows ──────────────────────────────── */
.detail { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.85rem; }
.detail:last-child { border-bottom: none; }
.detail-label { color: var(--muted); }
.detail-value { font-family: monospace; word-break: break-all; max-width: 60%; text-align: right; }

/* ── Buttons ──────────────────────────────────── */
.btn {
  display: inline-block; padding: 8px 16px; border-radius: 8px;
  cursor: pointer; font-size: 0.85rem; border: none; font-weight: 500;
  transition: all 0.15s;
}
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { opacity: 0.9; }
.btn-outline { background: transparent; color: var(--accent); border: 1px solid var(--accent); }
.btn-outline:hover { background: rgba(99,102,241,0.1); }
.btn-sm { padding: 5px 12px; font-size: 0.8rem; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-group { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }

/* ── Output ───────────────────────────────────── */
pre.output {
  background: #12141c; border: 1px solid var(--border); border-radius: 8px;
  padding: 14px; font-size: 0.8rem; overflow: auto; max-height: 400px;
  color: var(--muted); line-height: 1.5;
}

/* ── Chat ─────────────────────────────────────── */
.chat-container { display: flex; flex-direction: column; height: calc(100vh - 160px); }
.chat-messages { flex: 1; overflow-y: auto; padding: 12px 0; display: flex; flex-direction: column; gap: 12px; }
.chat-msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; font-size: 0.9rem; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.chat-msg.user { align-self: flex-end; background: var(--accent); color: white; border-bottom-right-radius: 4px; }
.chat-msg.assistant { align-self: flex-start; background: var(--card); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
.chat-msg.system { align-self: center; color: var(--muted); font-size: 0.8rem; font-style: italic; }
.chat-input-row { display: flex; gap: 8px; padding-top: 12px; border-top: 1px solid var(--border); }
.chat-input {
  flex: 1; padding: 10px 14px; border-radius: 10px; border: 1px solid var(--border);
  background: var(--card); color: var(--text); font-size: 0.9rem; outline: none;
  font-family: inherit;
}
.chat-input:focus { border-color: var(--accent); }

/* ── Copy button ──────────────────────────────── */
.copy-btn { position: absolute; top: 8px; right: 8px; }
.mono-box { position: relative; }
.mono-box code {
  display: block; background: #12141c; border: 1px solid var(--border);
  border-radius: 8px; padding: 12px; font-size: 0.8rem; overflow-x: auto;
  color: var(--muted); word-break: break-all;
}
</style>
</head>
<body>

<nav>
  <span class="logo">Hermes</span>
  <div class="tabs">
    <button class="tab active" data-page="overview">Overview</button>
    <button class="tab" data-page="integrations">Integrations</button>
    <button class="tab" data-page="llm">LLM</button>
    <button class="tab" data-page="reports">Reports</button>
    <button class="tab" data-page="chat">Chat</button>
    <button class="tab" data-page="connect">Connect</button>
  </div>
</nav>

<main>

<!-- ═══ OVERVIEW ═══ -->
<div class="page active" id="page-overview">
  <h2 class="section">System Overview</h2>
  <div class="grid" id="overview-cards">Loading…</div>
</div>

<!-- ═══ INTEGRATIONS ═══ -->
<div class="page" id="page-integrations">
  <h2 class="section">Integrations</h2>
  <div class="grid">
    <div class="card" id="int-spinr">
      <h3><span class="dot dot-off" id="dot-spinr"></span> Spinr Backend</h3>
      <div id="details-spinr">Loading…</div>
      <div class="btn-group">
        <button class="btn btn-outline btn-sm" onclick="testConn('spinr')">Test Connection</button>
      </div>
    </div>
    <div class="card" id="int-telegram">
      <h3><span class="dot dot-off" id="dot-telegram"></span> Telegram</h3>
      <div id="details-telegram">Loading…</div>
      <div class="btn-group">
        <button class="btn btn-outline btn-sm" onclick="testConn('telegram')">Test Connection</button>
      </div>
    </div>
    <div class="card" id="int-zoho">
      <h3><span class="dot dot-off" id="dot-zoho"></span> Zoho (Cliq + Desk)</h3>
      <div id="details-zoho">Loading…</div>
      <div class="btn-group">
        <button class="btn btn-outline btn-sm" onclick="testConn('zoho')">Test Connection</button>
      </div>
    </div>
  </div>
  <div class="card" style="margin-top:8px">
    <h3>Configuration</h3>
    <p style="color:var(--muted);font-size:0.85rem;line-height:1.6">
      Integration secrets are managed via <code>fly secrets set</code> (production) or <code>.env</code> (local dev).<br>
      See <code>hermes/.env.example</code> for all available environment variables.
    </p>
    <div class="mono-box" style="margin-top:12px">
      <code>fly secrets set TELEGRAM_BOT_TOKEN="..." SPINR_API_KEY="..." ZOHO_CLIENT_ID="..."</code>
    </div>
  </div>
</div>

<!-- ═══ LLM ═══ -->
<div class="page" id="page-llm">
  <h2 class="section">LLM Configuration</h2>
  <div class="grid">
    <div class="card">
      <h3><span class="dot dot-off" id="dot-llm"></span> LLM Provider</h3>
      <div id="details-llm">Loading…</div>
      <div class="btn-group">
        <button class="btn btn-outline btn-sm" onclick="testConn('llm')">Test LLM</button>
      </div>
    </div>
    <div class="card">
      <h3>Setup</h3>
      <p style="color:var(--muted);font-size:0.85rem;line-height:1.6;margin-bottom:12px">
        Set <code>LLM_PROVIDER</code> to <code>anthropic</code> or <code>openai</code>, then add the API key:
      </p>
      <div class="mono-box" style="margin-bottom:10px">
        <code># Claude (Anthropic)<br>
fly secrets set LLM_PROVIDER=anthropic ANTHROPIC_API_KEY="sk-ant-..."</code>
      </div>
      <div class="mono-box">
        <code># OpenAI<br>
fly secrets set LLM_PROVIDER=openai OPENAI_API_KEY="sk-..."</code>
      </div>
      <p style="color:var(--muted);font-size:0.85rem;line-height:1.6;margin-top:12px">
        Optional: <code>ANTHROPIC_MODEL</code> (default: claude-sonnet-4-20250514),
        <code>OPENAI_MODEL</code> (default: gpt-4o),
        <code>LLM_MAX_TOKENS</code> (default: 2048)
      </p>
    </div>
  </div>
  <div class="card" style="margin-top:16px">
    <h3>How LLM Integration Works</h3>
    <p style="color:var(--muted);font-size:0.85rem;line-height:1.8">
      When enabled, Hermes uses the LLM as a natural language interface to all its tools.<br>
      <strong>Telegram:</strong> Non-command messages are routed to the LLM, which can call tools to fetch reports, search tickets, etc.<br>
      <strong>Zoho Cliq:</strong> Same — free-text messages go through the LLM.<br>
      <strong>Dashboard Chat:</strong> The Chat tab lets you talk to Hermes directly from this page.<br>
      <strong>Tool-use loop:</strong> The LLM can chain multiple tool calls (up to 5 rounds) to answer complex questions.
    </p>
  </div>
</div>

<!-- ═══ REPORTS ═══ -->
<div class="page" id="page-reports">
  <h2 class="section">Reports</h2>
  <div class="btn-group" style="margin-bottom:16px">
    <button class="btn btn-primary" onclick="fetchReport('health')">Infra Health</button>
    <button class="btn btn-primary" onclick="fetchReport('ops')">Ops KPIs</button>
    <button class="btn btn-primary" onclick="fetchReport('report')">Full Report</button>
    <button class="btn btn-outline" onclick="fetchReport('rides')">Recent Rides</button>
    <button class="btn btn-outline" onclick="fetchReport('drivers?online_only=true')">Online Drivers</button>
  </div>
  <pre class="output" id="report-output">Click a button above to fetch a report.</pre>
</div>

<!-- ═══ CHAT ═══ -->
<div class="page" id="page-chat">
  <div class="chat-container">
    <div class="chat-messages" id="chat-messages">
      <div class="chat-msg system">Ask Hermes anything — it can check infra health, pull KPIs, search tickets, send messages.</div>
    </div>
    <div class="chat-input-row">
      <input class="chat-input" id="chat-input" placeholder="Ask Hermes…" autocomplete="off">
      <button class="btn btn-primary" id="chat-send" onclick="sendChat()">Send</button>
    </div>
  </div>
</div>

<!-- ═══ CONNECT ═══ -->
<div class="page" id="page-connect">
  <h2 class="section">Connect to Hermes</h2>
  <div class="grid">
    <div class="card">
      <h3>Claude Code Desktop (MCP)</h3>
      <p style="color:var(--muted);font-size:0.85rem;line-height:1.6;margin-bottom:12px">
        Add to <code>~/.claude/settings.local.json</code>:
      </p>
      <div class="mono-box">
        <code id="mcp-config-block">Loading…</code>
      </div>
    </div>
    <div class="card">
      <h3>Streamable HTTP (newer MCP)</h3>
      <p style="color:var(--muted);font-size:0.85rem;line-height:1.6;margin-bottom:12px">
        Single-endpoint transport:
      </p>
      <div class="mono-box">
        <code id="mcp-http-block">Loading…</code>
      </div>
    </div>
    <div class="card">
      <h3>Telegram Bot</h3>
      <div id="connect-telegram">Loading…</div>
    </div>
    <div class="card">
      <h3>Zoho Cliq Bot</h3>
      <p style="color:var(--muted);font-size:0.85rem;line-height:1.6">
        Configure your Cliq bot's outgoing webhook URL to:
      </p>
      <div class="mono-box" style="margin-top:8px">
        <code id="cliq-webhook-url">Loading…</code>
      </div>
    </div>
    <div class="card">
      <h3>REST API</h3>
      <p style="color:var(--muted);font-size:0.85rem;line-height:1.6">Direct HTTP access:</p>
      <div style="margin-top:8px;font-size:0.85rem;color:var(--muted);line-height:2">
        <code>GET /spinr/health</code> — Infra health<br>
        <code>GET /spinr/ops</code> — Ops KPIs<br>
        <code>GET /spinr/report</code> — Full report<br>
        <code>GET /spinr/rides</code> — Recent rides<br>
        <code>GET /spinr/drivers</code> — Drivers<br>
        <code>GET /zoho/desk/tickets</code> — Desk tickets<br>
        <code>POST /chat</code> — LLM chat<br>
        <code>GET /health</code> — Service health
      </div>
    </div>
    <div class="card">
      <h3>Available MCP Tools</h3>
      <div style="font-size:0.85rem;color:var(--muted);line-height:2" id="mcp-tools-list">Loading…</div>
    </div>
  </div>
</div>

</main>

<script>
const B = window.location.origin;
let chatHistory = [];

// ── Tab navigation ──────────────────────────────
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('page-' + tab.dataset.page).classList.add('active');
  });
});

// ── Load config status ──────────────────────────
async function loadConfig() {
  try {
    const r = await fetch(B + '/api/config/status');
    const d = await r.json();
    renderOverview(d);
    renderIntegrations(d);
    renderLLM(d);
    renderConnect(d);
  } catch(e) { console.error('Config load failed:', e); }
}

function dot(on) { return on ? 'dot-on' : 'dot-off'; }
function badge(on, label) { return `<span class="badge ${on ? 'badge-on' : 'badge-off'}">${label}</span>`; }
function detailRow(label, value) { return `<div class="detail"><span class="detail-label">${label}</span><span class="detail-value">${value}</span></div>`; }

function renderOverview(d) {
  const ints = d.integrations;
  const cards = [
    { name: 'Spinr Backend', on: ints.spinr.configured, detail: ints.spinr.url },
    { name: 'Telegram', on: ints.telegram.configured, detail: ints.telegram.webhook_url || 'Not set' },
    { name: 'Zoho Cliq', on: ints.zoho_cliq.configured, detail: '' },
    { name: 'Zoho Desk', on: ints.zoho_desk.configured, detail: ints.zoho_desk.api_url },
    { name: 'Zoho OAuth', on: ints.zoho_oauth.configured, detail: '' },
    { name: 'LLM (' + d.llm.provider + ')', on: d.llm.configured, detail: d.llm.model || '' },
  ];
  document.getElementById('overview-cards').innerHTML = cards.map(c => `
    <div class="card">
      <h3><span class="dot ${dot(c.on)}"></span> ${c.name} ${badge(c.on, c.on ? 'Connected' : 'Not configured')}</h3>
      ${c.detail ? `<div class="detail"><span class="detail-label">Endpoint</span><span class="detail-value">${c.detail}</span></div>` : ''}
    </div>
  `).join('');
}

function renderIntegrations(d) {
  const ints = d.integrations;
  document.getElementById('dot-spinr').className = 'dot ' + dot(ints.spinr.configured);
  document.getElementById('details-spinr').innerHTML =
    detailRow('URL', ints.spinr.url) +
    detailRow('API Key', ints.spinr.configured ? 'Set' : 'Not set');

  document.getElementById('dot-telegram').className = 'dot ' + dot(ints.telegram.configured);
  document.getElementById('details-telegram').innerHTML =
    detailRow('Bot Token', ints.telegram.configured ? 'Set' : 'Not set') +
    detailRow('Webhook', ints.telegram.webhook_url || 'N/A') +
    detailRow('Allowed Users', ints.telegram.allowed_users || 'All');

  document.getElementById('dot-zoho').className = 'dot ' + dot(ints.zoho_oauth.configured);
  document.getElementById('details-zoho').innerHTML =
    detailRow('OAuth', ints.zoho_oauth.configured ? 'Configured' : 'Not set') +
    detailRow('Cliq', ints.zoho_cliq.configured ? 'Configured' : 'Not set') +
    detailRow('Desk', ints.zoho_desk.configured ? 'Configured' : 'Not set') +
    detailRow('Desk API', ints.zoho_desk.api_url);
}

function renderLLM(d) {
  document.getElementById('dot-llm').className = 'dot ' + dot(d.llm.configured);
  document.getElementById('details-llm').innerHTML =
    detailRow('Provider', d.llm.provider) +
    detailRow('Model', d.llm.model || 'N/A') +
    detailRow('Status', d.llm.configured ? 'Ready' : 'Not configured');
}

function renderConnect(d) {
  const base = d.mcp.sse_endpoint.replace('/mcp/sse', '') || B;
  document.getElementById('mcp-config-block').innerHTML =
    `{\n  "mcpServers": {\n    "hermes": {\n      "type": "http",\n      "url": "${d.mcp.sse_endpoint}"\n    }\n  }\n}`;
  document.getElementById('mcp-http-block').innerHTML =
    `{\n  "mcpServers": {\n    "hermes": {\n      "type": "http",\n      "url": "${d.mcp.http_endpoint}"\n    }\n  }\n}`;
  document.getElementById('cliq-webhook-url').textContent = base + '/zoho/cliq/webhook';

  if (d.integrations.telegram.configured) {
    document.getElementById('connect-telegram').innerHTML =
      `<p style="color:var(--muted);font-size:0.85rem">Bot is configured. Webhook: <code>${d.integrations.telegram.webhook_url}</code></p>`;
  } else {
    document.getElementById('connect-telegram').innerHTML =
      `<p style="color:var(--muted);font-size:0.85rem">Set <code>TELEGRAM_BOT_TOKEN</code> and <code>HERMES_PUBLIC_URL</code> to enable.</p>`;
  }

  const tools = [
    'get_infra_health', 'get_ops_report', 'get_full_report',
    'get_rides', 'get_drivers', 'search_desk_tickets',
    'get_desk_ticket', 'create_desk_ticket',
    'send_telegram_message', 'send_cliq_message'
  ];
  document.getElementById('mcp-tools-list').innerHTML = tools.map(t =>
    `<code style="display:inline-block;background:#12141c;padding:2px 8px;border-radius:4px;margin:2px">${t}</code>`
  ).join(' ');
}

// ── Connection tests ────────────────────────────
async function testConn(name) {
  const btn = event.target;
  btn.disabled = true; btn.textContent = 'Testing…';
  try {
    const r = await fetch(B + '/api/config/test/' + name, { method: 'POST' });
    const d = await r.json();
    btn.textContent = d.connected ? 'Connected' : 'Failed: ' + (d.error || d.status_code);
    btn.style.borderColor = d.connected ? 'var(--green)' : 'var(--red)';
    btn.style.color = d.connected ? 'var(--green)' : 'var(--red)';
  } catch(e) { btn.textContent = 'Error'; }
  setTimeout(() => { btn.disabled = false; btn.textContent = 'Test Connection'; btn.style.borderColor = ''; btn.style.color = ''; }, 4000);
}

// ── Reports ─────────────────────────────────────
async function fetchReport(path) {
  const out = document.getElementById('report-output');
  out.textContent = 'Loading…';
  try {
    const r = await fetch(B + '/spinr/' + path);
    const d = await r.json();
    out.textContent = JSON.stringify(d, null, 2);
  } catch(e) { out.textContent = 'Error: ' + e.message; }
}

// ── Chat ────────────────────────────────────────
function appendMsg(role, text) {
  const el = document.createElement('div');
  el.className = 'chat-msg ' + role;
  el.textContent = text;
  document.getElementById('chat-messages').appendChild(el);
  el.scrollIntoView({ behavior: 'smooth' });
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  appendMsg('user', msg);
  chatHistory.push({ role: 'user', content: msg });

  const sendBtn = document.getElementById('chat-send');
  sendBtn.disabled = true; sendBtn.textContent = '…';

  try {
    const r = await fetch(B + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, history: chatHistory.slice(-20) }),
    });
    const d = await r.json();
    const reply = d.reply || d.error || 'No response';
    appendMsg('assistant', reply);
    chatHistory.push({ role: 'assistant', content: reply });
  } catch(e) {
    appendMsg('system', 'Error: ' + e.message);
  }
  sendBtn.disabled = false; sendBtn.textContent = 'Send';
}

document.getElementById('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

// ── Init ────────────────────────────────────────
loadConfig();
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
