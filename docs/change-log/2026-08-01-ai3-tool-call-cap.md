# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/ai3-tool-call-cap` |
| Related issue or gap ID | ACTION_ITEMS.md → AI3 ("AI assistant / MCP guardrail backlog") |

## 1. Issue / gap identified

`backend/ai/orchestrator.py`'s tool loop executed every tool call the model
requested in a single turn concurrently via `asyncio.gather`, with no upper
bound. A single adversarial or hallucinating LLM turn could request an
unbounded number of simultaneous tool calls (up to `ai_max_tool_iterations`,
default 6, turns), several of which (`find_place`, `get_fare_quote`,
`propose_ride_booking` in `tools_booking.py`) call the paid Google Maps API.

## 2. Root cause

`orchestrator.py`'s tool-execution step built the `asyncio.gather(...)` call
directly from `turn_end.tool_calls`, the raw list the model/provider adapter
returned, with no size check before fan-out. Nothing in the loop, the tool
registry (`tools.py`), or the provider adapters imposed a ceiling on how many
tool calls one model turn could request — only a per-tool wall-clock timeout
(`TOOL_TIMEOUT_SECONDS` / `ToolSpec.timeout_seconds`) existed, which bounds
one call's duration, not how many run concurrently.

## 3. Fix / remediation

Added `MAX_TOOL_CALLS_PER_ITERATION = 5` to `orchestrator.py`. Each
iteration now executes only the first 5 requested tool calls via
`asyncio.gather`; any calls beyond the cap are **not silently dropped** —
each gets a synthetic `{"error": "tool call budget exceeded — ..."}` result
with `ok=False`, appended to the conversation exactly like a real failed
tool call, so the model sees the refusal in its next prompt and the turn can
still reach a final answer instead of hanging or crashing. The event is
logged via `logger.warning` (tool names + user_id + conversation_id only —
no arguments/results, per the PIPEDA logging rules) and counted via a new
`spinr_ai_tool_calls_capped_total` metric. `"tool"` start/end SSE frames are
still emitted for every requested call (including excess ones, with
`ok: false`) so the client's tool-activity UI doesn't silently miss a
request either.

5 was chosen matching the item's own suggestion and validated against real
tool inventories: a full booking turn (`find_place` pickup + `find_place`
dropoff + `get_fare_quote` + `propose_ride_booking`) tops out around 4 calls
per turn; the largest single-audience tool surface (rider: booking 5 +
rides 4 + account 6 + support 3 = 18 tools total) is never called in full in
one turn. 5 leaves headroom for the real flow without raising the ceiling on
paid-API fan-out.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, isolated to `backend/ai/orchestrator.py`'s
  tool-execution step inside `run_chat_turn()`.** Grepped every caller of
  `run_chat_turn` and every consumer of the tool-execution path:
  - `backend/routes/ai.py` (`/ai/chat`, the rider/driver chat SSE endpoint)
    — calls `run_chat_turn`; inherits the cap. This is the primary intended
    beneficiary (real-money-adjacent, rider-facing).
  - `backend/routes/admin/ai_console.py` (super-admin AI console, audited,
    impersonates a rider/driver for testing) — also calls `run_chat_turn`
    and goes through the same loop, so it gets the same cap. No separate
    handling needed; console testing that requests ≤5 tools per turn (the
    overwhelming common case) is unaffected.
  - `backend/ai/mcp_server.py` (`/mcp` read-only tool surface) — calls
    `execute_tool` **directly**, one call per MCP client request, never
    through `orchestrator.py`'s `asyncio.gather` loop. **Not affected** by
    this change; it already has its own separate guardrail (a per-user daily
    cap on `/mcp` calls, see `_over_mcp_daily_cap`) that predates this fix.
  - `backend/ai/tools.py` (`execute_tool`, `TOOL_REGISTRY`) — unchanged. The
    per-tool timeout, ownership checks, and audit logging inside
    `execute_tool` all run exactly the same for the calls that do execute;
    this fix only changes which calls reach `execute_tool` in a given
    iteration, not what happens once they do.
- No ride state, wallet/wallet-delta, or Stripe code path is touched.
- Real booking flows (≤5 tool calls/turn, per the tool-inventory check
  above) see **zero behavior change** — same call count, same results, same
  frames, same messages appended to the conversation. Verified by a
  dedicated regression test (`test_at_cap_turn_is_completely_unaffected`).
- A turn that legitimately needs >5 tool calls in one iteration (not
  observed in any current prompt/tool design) would now get partial
  execution + synthetic errors for the rest, rather than full execution.
  This is the intended tradeoff (bounded paid-API fan-out over unbounded
  tool breadth in a single turn); the model can still request the remaining
  tools in a subsequent iteration within the same turn (loop runs up to
  `ai_max_tool_iterations`, default 6).

## 5. User-experience effect

- Rider/driver-facing (`/ai/chat`) and internal-admin-facing (AI console):
  no visible change in the common case (≤5 tool calls per turn, which is
  every currently-designed flow). In the rare case a turn is capped, the
  rider/driver/admin sees the assistant's normal conversational response
  (the model gets the budget-exceeded error as a tool result and continues
  the turn, e.g. asking a follow-up or apologizing) rather than a hang,
  timeout, or crash — this is a **reliability improvement**, not a
  regression, for that edge case.
- Not visible mid-session in the sense of interrupting an in-flight ride,
  payment, or dispatch flow — this only affects the AI chat assistant's own
  tool-calling loop.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/orchestrator.py` | Added `MAX_TOOL_CALLS_PER_ITERATION = 5` constant; split `tool_calls` into `executable_calls` (gathered/executed) and `excess_calls` (synthetic error result, no execution) before building `results`; added a `logger.warning` + `spinr_ai_tool_calls_capped_total` metric increment when the cap is hit | AI3 — bound per-iteration paid-API tool fan-out without silently dropping excess calls |
| `backend/tests/test_ai_orchestrator.py` | Added `TestToolCallCap` with two tests: over-budget (7 calls → 5 executed, 2 synthetic errors, log emitted, turn still completes) and at-cap (exactly 5 calls → fully unaffected, no synthetic errors, no log) | Regression coverage for both the new cap and the "no change for the common case" guarantee |
| `ACTION_ITEMS.md` | Marked AI3 `[x]` with a one-line pointer to this log | Close out the backlog item per its own tracked location |

## 7. Before / after

```python
# Before
messages.append({"role": "assistant", "content": "".join(turn_text), "tool_calls": tool_calls})
for tc in tool_calls:
    yield "tool", {"name": tc.name, "status": "start"}
results = await asyncio.gather(
    *(execute_tool(tc.name, tc.arguments, user=tool_user, audience=audience) for tc in tool_calls)
)
for tc, (result, ok) in zip(tool_calls, results, strict=True):
    ...
```

```python
# After
messages.append({"role": "assistant", "content": "".join(turn_text), "tool_calls": tool_calls})
for tc in tool_calls:
    yield "tool", {"name": tc.name, "status": "start"}

executable_calls = tool_calls[:MAX_TOOL_CALLS_PER_ITERATION]
excess_calls = tool_calls[MAX_TOOL_CALLS_PER_ITERATION:]
if excess_calls:
    logger.warning(
        "ai tool call budget exceeded: %d requested, %d executed, %d rejected",
        len(tool_calls), len(executable_calls), len(excess_calls),
        extra={"user_id": user.get("id"), "conversation_id": conversation["id"],
               "requested_tools": [tc.name for tc in tool_calls]},
    )
    _metric_inc("spinr_ai_tool_calls_capped_total", by=len(excess_calls))

executed_results = await asyncio.gather(
    *(execute_tool(tc.name, tc.arguments, user=tool_user, audience=audience) for tc in executable_calls)
)

def _capped_result() -> Tuple[Dict[str, Any], bool]:
    return ({"error": f"tool call budget exceeded — only {MAX_TOOL_CALLS_PER_ITERATION} tool calls "
                       "are allowed per turn; this call was not executed, try again with fewer calls "
                       "this turn"}, False)

results = list(executed_results) + [_capped_result() for _ in excess_calls]
for tc, (result, ok) in zip(tool_calls, results, strict=True):
    ...
```

## 8. Rollback plan

Pure code change, no migration, no `app_settings` flag, no data mutation —
`git revert` of this commit is a complete and sufficient rollback. No Stripe
charges, wallet deltas, or ride state are touched by this change, so no
data-level remediation is needed on top of the code revert. If a faster
in-place mitigation is ever needed without a redeploy, `MAX_TOOL_CALLS_PER_ITERATION`
could be trivially converted to an `app_settings`-backed value in a
follow-up, but that was judged unnecessary for a safety ceiling that isn't
expected to need runtime tuning (unlike, e.g., surge or fare parameters).

## 9. Verification performed

- [x] Automated tests run (unit): `backend/tests/test_ai_orchestrator.py` —
  21/21 passed (19 pre-existing + 2 new), run via
  `/tmp/ai3-venv/bin/python -m pytest backend/tests/test_ai_orchestrator.py --no-cov -q`
  → `21 passed, 1 warning in 1.06s`.
- [x] Broader AI test suite: `pytest backend/tests/ -k ai --no-cov -q` →
  `1 failed, 1692 passed, 2 skipped, 4520 deselected, 1 xfailed`. The one
  failure (`test_ai_admin_console.py::TestSecurityEvents::test_returns_events_and_summary`)
  was confirmed **pre-existing and unrelated**: (a) it passes in isolation,
  and (b) the identical failure reproduces on a clean `origin/main` checkout
  (via `git stash`) running the exact same `-k ai` command — a test-ordering
  flake in that file, not something this change introduced.
- [x] Blast-radius grep performed: `run_chat_turn` callers
  (`routes/ai.py`, `routes/admin/ai_console.py`) and `execute_tool` callers
  (`orchestrator.py`, `mcp_server.py`) — see section 4.
- [x] Reviewed against relevant `CLAUDE.md` conventions: observability
  (log level, metric naming `spinr_ai_tool_calls_capped_total`), PIPEDA
  (only tool names + user_id + conversation_id logged, never
  arguments/results), "do not silently swallow errors" (synthetic error
  result instead of a dropped call).
- [ ] Feature-flagged: not applicable — this is a defensive safety ceiling
  with no business-tunable value (unlike surge/fare parameters), and it is
  purely additive for the ≤5-calls-per-turn case that covers 100% of
  currently-designed flows; see rollback plan above for how it could be
  flagged later if that assumption changes.

## 10. What was NOT verified

- Not tested against a live Anthropic/OpenAI provider or a live Google Maps
  API call — all tests use the existing `FakeAdapter` scripted-turn harness
  and mock `execute_tool`, matching this test file's established pattern.
  No real LLM was prompted to actually request >5 tools; the test
  constructs that scenario synthetically via a scripted `turn_end.tool_calls`
  list, which is the same technique the pre-existing iteration-cap test
  (`test_iteration_cap_graceful`) already uses for its analogous scenario.
- Not run against a live Supabase instance — `conversations.get_or_create_conversation`,
  `append_message`, and `load_history` are patched per the existing test
  harness (`_patches()`), consistent with every other test in this file.
- No staging/canary verification was performed (no staging environment was
  available in this session) — verification is unit-test-only, following
  the existing coverage pattern in `test_ai_orchestrator.py`. Given the
  fix's isolated blast radius (a single conditional branch inside one
  already-well-tested loop) and that it is purely defensive/additive for
  the common case, this was judged sufficient, but a reviewer with staging
  access may want to exercise a real multi-tool-call turn before merge.
- Did not add an `app_settings`-backed override for the cap value — treated
  as a fixed safety ceiling rather than a runtime-tunable business
  parameter; flagged as a possible follow-up in the rollback plan.
