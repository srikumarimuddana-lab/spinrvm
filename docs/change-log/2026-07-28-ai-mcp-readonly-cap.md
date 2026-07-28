# Change Impact & Risk Log — /mcp: enforce read-only contract + per-user daily tool cap

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI guardrail hardening, commit 7 of series) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Guardrail audit findings §4: write-capable tool on a documented read-only surface; no rate limit on /mcp |

## 1. Issue / gap identified

1. `escalate_to_support` (write-capable: can open a real Zoho ticket with the chat transcript when `ai_escalation_creates_ticket` is on) defaulted to `mcp_exposed=True`, contradicting `/mcp`'s documented READ-ONLY contract.
2. `/mcp` called `execute_tool` directly with no per-user ceiling — the chat path's daily cap and route rate limit never applied, so an unattended MCP client could make unbounded tool calls (and unbounded tickets via №1).

## 2. Root cause

`ToolSpec.mcp_exposed` defaults to `True` and the support module never overrode it; the MCP mount was built after the orchestrator's cap and never got an equivalent.

## 3. Fix / remediation

- `escalate_to_support` registered with `mcp_exposed=False` (chat behavior unchanged).
- `_call_tool` now checks a per-user daily Redis INCR cap before executing: `ai_mcp_daily_tool_cap` app_setting, falling back to `ai_daily_message_cap` (default 50). Fails open with a loud error log, mirroring the chat-cap policy; `ai_mcp_enabled` remains the hard stop.

## 4. Risk & impact on existing functionality

- Blast radius: `/mcp` surface only. `ai_mcp_enabled` defaults to **False** (kill switch checked per request), so in current production posture this surface is dark — near-zero live risk. Chat-path escalation is untouched (the tool itself is unchanged; only its MCP visibility changed).
- Any external MCP client that relied on `escalate_to_support` would lose it — none are known, and the module contract always said read-only.
- The cap can throttle a legitimate heavy MCP user at 50 calls/day; adjustable via `app_settings` without redeploy (`ai_mcp_daily_tool_cap`).
- New Redis keys `ai:mcp:daily:*` (86400 s TTL) — negligible footprint; falls back to the in-process dict when Redis is unset (cap then per-replica, consistent with existing rate-limit behavior).

## 5. User-experience effect

None for riders/drivers in-app. External MCP clients (feature-flagged off today) see one fewer tool and a 429-style error payload past the daily cap.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/ai/tools_support.py` | `escalate_to_support` → `mcp_exposed=False` | Enforce the read-only contract |
| `backend/ai/mcp_server.py` | `_over_mcp_daily_cap` + cap check in `_call_tool`; docstring | Bound unattended clients |
| `backend/tests/test_ai_mcp.py` | Write-tool exposure test + cap tests (block, per-user isolation, fail-open) | Regression-pin both |

## 7. Before/after

```python
# before (_call_tool)
payload, _ok = await execute_tool(name, arguments or {}, user=user, audience=_audience_for(user))
# after
cap = int(settings.get("ai_mcp_daily_tool_cap") or settings.get("ai_daily_message_cap") or 50)
if await _over_mcp_daily_cap(user["id"], cap):
    payload = {"error": "daily limit reached — try again tomorrow"}
else:
    payload, _ok = await execute_tool(name, arguments or {}, user=user, audience=_audience_for(user))
```

## 8. Rollback plan

Flag-level: `ai_mcp_enabled=false` (already the default) kills the whole surface without redeploy; `ai_mcp_daily_tool_cap` set very high effectively disables the cap. Code-level: `git revert`.

## 9. Verification performed

- `pytest backend/tests/test_ai_mcp.py backend/tests/test_ai_tools_support.py` — 43 passed, 1 skipped (SDK-absent case).

## 10. What was NOT verified

- Not exercised against a real MCP client (SDK-present integration path is covered by existing build tests only).
- Redis-backed cap tested with mocks, not a live Redis.
