# Change Impact & Risk Log — AI per-tool timeout override for Maps fan-out tools

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI location-selection loop fix, commit 2 of series) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Rider report: selecting a suggested address "threw an error" at the end of the clarification loop |

## 1. Issue / gap identified

AI booking tool calls die with "the lookup took too long — try again" on slow-but-successful quotes. The global tool timeout is 5 s, but `get_fare_quote`'s worst case is ~11–12 s of legitimate work.

## 2. Root cause

`TOOL_TIMEOUT_SECONDS = 5.0` (`backend/ai/tools.py`) is applied uniformly. `get_fare_quote` runs pickup reconciliation and dropoff pair-verification concurrently (each can chain two sequential 4 s geocode HTTP calls), then the estimate engine waits up to 2 s for the road route, plus promo/DB reads. `find_place`'s street-address branch chains two 4 s geocodes plus area resolution. The registry had no per-tool override mechanism.

## 3. Fix / remediation

`ToolSpec` gains an additive `timeout_seconds: Optional[float] = None` field; `execute_tool` uses it when set, else the global default. `find_place`, `get_fare_quote`, and `propose_ride_booking` set `timeout_seconds=15.0`. All other tools keep the 5 s default.

## 4. Risk & impact on existing functionality

- Blast radius: additive dataclass field with a default — every other `ToolSpec` constructor (tools_rides, tools_account, tools_support, tools_driver, test helpers) is unaffected. `execute_tool` callers are the chat orchestrator and the `/mcp` mount; the three overridden tools are all `mcp_exposed=False`, so `/mcp` latency is unchanged.
- Worst-case chat turn latency rises: the orchestrator allows up to 6 tool iterations, so a pathological turn could hold the SSE stream ~6×15 s. Mitigations: the SSE route emits keepalive pings so the stream doesn't drop; real handlers bound their own HTTP calls at 4 s each, so 15 s is a ceiling for the worst chain, not a typical duration.
- The ownership-verifier wait deliberately stays on the 5 s global (single DB read).

## 5. User-experience effect

Rider-facing, positive only: quotes that previously errored at exactly 5 s now complete (slower, but successfully). No UI change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/ai/tools.py` | `ToolSpec.timeout_seconds` field; `execute_tool` honors it | Minimal per-tool override mechanism |
| `backend/ai/tools_booking.py` | `timeout_seconds=15.0` on the three Maps fan-out tools | Fit their real worst case |
| `backend/tests/test_ai_tools_core.py` | Override-shrinks, override-extends, and registry-carries-override tests | Regression-pin the mechanism and the three overrides |

## 7. Before/after

```python
# before
result = await asyncio.wait_for(spec.handler(handler_user, **call_args), timeout=TOOL_TIMEOUT_SECONDS)
# after
handler_timeout = spec.timeout_seconds if spec.timeout_seconds is not None else TOOL_TIMEOUT_SECONDS
result = await asyncio.wait_for(spec.handler(handler_user, **call_args), timeout=handler_timeout)
```

## 8. Rollback plan

`git revert` and redeploy — stateless, server-internal; no data or client coupling. (Not an `app_settings` flag: this is a reliability bound, not user-visible UX.)

## 9. Verification performed

- `pytest backend/tests/test_ai_tools_core.py` — 21 passed (including the existing monkeypatched global-timeout test, unchanged).
- No production build applies (backend-only).

## 10. What was NOT verified

- Real-world worst-case latency against live Google Maps — the 11–12 s estimate is derived from the code's own HTTP timeouts, not measured in production.
- Not tested against live Supabase; unit suites use mocks.
