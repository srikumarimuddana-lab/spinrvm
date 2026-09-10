"""Web dashboard — desktop browser access to Hermes status and controls."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes — Spinr Ops</title>
<style>
:root {
  --bg: #0f1117;
  --card: #1a1d27;
  --border: #2a2d3a;
  --text: #e4e4e7;
  --muted: #9ca3af;
  --accent: #6366f1;
  --green: #22c55e;
  --red: #ef4444;
  --yellow: #eab308;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}
h1 { font-size: 1.8rem; margin-bottom: 8px; }
.subtitle { color: var(--muted); margin-bottom: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.card h2 { font-size: 1rem; color: var(--muted); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.status-ok { background: var(--green); }
.status-error { background: var(--red); }
.status-unknown { background: var(--yellow); }
.metric { margin-bottom: 8px; }
.metric-label { color: var(--muted); font-size: 0.85rem; }
.metric-value { font-size: 1.1rem; font-weight: 600; }
pre {
  background: #12141c;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  font-size: 0.85rem;
  overflow-x: auto;
  max-height: 300px;
  overflow-y: auto;
  color: var(--muted);
}
.btn {
  display: inline-block;
  padding: 8px 16px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.9rem;
  margin-right: 8px;
  margin-bottom: 8px;
}
.btn:hover { opacity: 0.9; }
.btn:disabled { opacity: 0.5; cursor: wait; }
.integrations { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
.integration-badge {
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 0.8rem;
  font-weight: 500;
}
.integration-on { background: #16331c; color: var(--green); }
.integration-off { background: #331616; color: var(--red); }
#logs { margin-top: 16px; }
</style>
</head>
<body>
<h1>Hermes</h1>
<p class="subtitle">Spinr Ops Agent — ENV</p>

<div class="grid">
  <div class="card">
    <h2>Integrations</h2>
    <div class="integrations" id="integrations">Loading…</div>
  </div>
  <div class="card">
    <h2>Quick Actions</h2>
    <button class="btn" onclick="fetchReport('health')">Infra Health</button>
    <button class="btn" onclick="fetchReport('ops')">Ops KPIs</button>
    <button class="btn" onclick="fetchReport('report')">Full Report</button>
    <button class="btn" onclick="fetchReport('rides')">Rides</button>
    <button class="btn" onclick="fetchReport('drivers?online_only=true')">Online Drivers</button>
  </div>
</div>

<div class="card">
  <h2>Report Output</h2>
  <pre id="output">Click a button above to fetch a report.</pre>
</div>

<div class="card" id="logs" style="margin-top:16px">
  <h2>Connection Info</h2>
  <div class="metric">
    <span class="metric-label">MCP SSE Endpoint</span><br>
    <span class="metric-value" style="font-family:monospace;font-size:0.9rem" id="mcp-url">Loading…</span>
  </div>
  <div class="metric" style="margin-top:8px">
    <span class="metric-label">MCP Streamable HTTP</span><br>
    <span class="metric-value" style="font-family:monospace;font-size:0.9rem" id="mcp-http-url">Loading…</span>
  </div>
  <div class="metric" style="margin-top:8px">
    <span class="metric-label">Telegram Webhook</span><br>
    <span class="metric-value" style="font-family:monospace;font-size:0.9rem" id="tg-url">Loading…</span>
  </div>
</div>

<script>
const BASE = window.location.origin;
document.querySelector('.subtitle').textContent = `Spinr Ops Agent — ${BASE}`;
document.getElementById('mcp-url').textContent = `${BASE}/mcp/sse`;
document.getElementById('mcp-http-url').textContent = `${BASE}/mcp`;
document.getElementById('tg-url').textContent = `${BASE}/telegram/webhook`;

async function loadHealth() {
  try {
    const r = await fetch(`${BASE}/health`);
    const d = await r.json();
    const el = document.getElementById('integrations');
    el.innerHTML = Object.entries(d.integrations || {}).map(([k, v]) =>
      `<span class="integration-badge ${v ? 'integration-on' : 'integration-off'}">${k}: ${v ? 'ON' : 'OFF'}</span>`
    ).join('');
  } catch(e) { document.getElementById('integrations').textContent = 'Error loading'; }
}

async function fetchReport(path) {
  const out = document.getElementById('output');
  out.textContent = 'Loading…';
  try {
    const r = await fetch(`${BASE}/spinr/${path}`);
    const d = await r.json();
    out.textContent = JSON.stringify(d, null, 2);
  } catch(e) { out.textContent = `Error: ${e.message}`; }
}

loadHealth();
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML
