# Change Impact & Risk Log — AI tool-result PII scrub gap

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code session (see PR for attribution) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | (this commit) |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` ranked blocker #5/#6; `ACTION_ITEMS.md` A40 |

## 1. Issue / gap identified

Two related PIPEDA gaps on the AI assistant surface:

1. `_driver_public` (`backend/ai/tools_rides.py`) built a driver's full legal name
   (`first_name + last_name`) into every `get_active_ride`/`get_ride_details` tool result. A rider
   asking "who's my driver" sent that full name to the third-party LLM provider (Anthropic/OpenAI/
   Gemini) as part of the tool-result context, and the same unredacted value was returned verbatim to
   any `/mcp` client calling the same tools.
2. More generally: no AI tool RESULT, from any tool, was ever run through PII scrubbing anywhere in
   the codebase — only the user's own chat message and the model's final reply text were scrubbed
   (`orchestrator.py`). The driver-name leak was the first live instance of this gap, not the only
   possible one; any future tool surfacing free text (an admin-entered rejection reason, a wallet
   transaction description, a support-ticket note) would inherit the same exposure silently.

## 2. Root cause

- **Driver name**: no data-minimization was applied at the tool-handler level before the value
  entered the model context. This codebase already has an established convention for exactly this
  situation — `utils/pii.py::first_name_only` — used elsewhere (the rider's display name on the
  driver-facing WS/push path in `routes/rides/matching.py`) but never applied here.
- **Structural gap**: `execute_tool`/`_cap_result` in `backend/ai/tools.py` is the single choke point
  both the in-process chat loop (`orchestrator.py`) and `/mcp` (`mcp_server.py`) funnel every tool
  call through, but neither of those two entry points, nor `_cap_result` itself, ever called
  `scrub_pii` (or anything like it) on the result before returning it.

## 3. Fix / remediation

**Part 1 — data minimization at the source.** `_driver_public` now sets `info["name"] =
first_name_only(user, fallback="Driver")` instead of concatenating first and last name. This is the
correct fix for the specific leak: a plain name is not regex-detectable, so no scrub — however
thorough — could have caught "Nighil Kumar" as PII. Consistent with `ai/pii.py`'s own documented
mitigation strategy ("Names cannot be scrubbed reliably with regex; mitigate via data-minimization").

**Part 2 — structural scrub at the choke point.** Added `scrub_pii_deep(value, depth=0)` to
`backend/ai/pii.py`: a recursive, value-pattern-only PII scrub over nested dicts/lists/tuples (bounded
depth, never raises). Wired into `_cap_result` in `tools.py` so it runs on the **whole** result
(including `_client_action`, which `/mcp`'s `_call_tool` serializes verbatim with no further
processing) before anything else happens to it. This covers every current and future tool's result
for the regex-detectable categories `scrub_pii` already handles: phone, email, GPS coordinates,
Canadian postal codes, payment card numbers, grouped SINs.

`scrub_pii_deep` is deliberately **value-only**, not key-name-based like
`utils/sentry_scrub.py::_scrub_deep`. That function's `KEY_ALLOWLIST` treats a bare `"name"` key as a
benign stack-frame symbol (correct for Sentry breadcrumbs — a function's local variable named `name`
is not a person). Reusing that same allowlist here would have silently let the driver's name back in
under its own `"name"` key. Since key-based redaction can't reliably distinguish "symbol name" from
"person's name" across these two very different contexts, `scrub_pii_deep` sticks to value-pattern
matching only and leaves the name problem to Part 1's data-minimization fix, exactly as `ai/pii.py`'s
own module docstring already prescribes.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend/ai), isolated to the tool-result pipeline.**

- **`_cap_result` callers**: exactly one — `_execute_tool_inner`'s final line
  (`return _cap_result(result), True`), reached by every tool call from both `execute_tool()`
  (orchestrator's chat loop) and `/mcp`'s `_call_tool`. No other caller exists; grepped to confirm.
- **`_driver_public` callers**: exactly two, both in `tools_rides.py` (`get_active_ride`,
  `get_ride_details`) — both already covered by `test_ai_tools_rides.py`, all passing after the fix.
- **Does NOT touch**: the user-message/final-reply-text scrub path in `orchestrator.py` (`scrub_pii`/
  `filter_tool_leakage` calls there are unchanged) — this fix is additive to that, not a replacement.
- **Does NOT touch**: any tool HANDLER's business logic — only the post-handler capping/scrub step in
  `_cap_result`, which every handler already passes through unconditionally.
- **False-positive risk**: `scrub_pii_deep` reuses `scrub_pii`'s existing, already-accepted false-
  positive profile (the NANP-aware phone check, IIN-prefix-gated card check, etc., documented in
  `ai/pii.py`'s own module comments) — this fix does not introduce new false-positive risk beyond what
  the app already accepts for every user-facing chat message today. A tool result containing a real
  phone/email/coordinate/card/SIN-shaped value the model was relying on programmatically would now see
  a redaction token instead — checked: no downstream code reads a tool result after `_cap_result`
  returns it (it is the final transformation before the value leaves the pipeline as model-context or
  MCP-response content), so this is a content-fidelity trade-off for the model's response text only,
  not a functional-correctness risk.
- **Consistent with existing intent**: `tools_rides.py`'s own module docstring already states "Precise
  GPS coordinates are deliberately excluded from results" — a tool that leaked raw coordinates via some
  other field would now be caught by this scrub too, reinforcing rather than conflicting with that
  stated design.

## 5. User-experience effect

**Rider/driver-facing (indirect).** A rider asking the AI assistant "who's my driver" now sees the
driver's first name only (e.g. "Alex") instead of the full legal name (e.g. "Alex Kumar") in the
assistant's reply — this matches how driver names are already shown elsewhere in this codebase (the
same `first_name_only` convention on the WS/push path) and is a **privacy improvement**, not a
regression: the rider still gets a usable identifier to recognize their driver, just not the driver's
full legal name. No other AI-assistant-facing copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/pii.py` | Added `scrub_pii_deep(value, depth)` — recursive value-only PII scrub over nested structures | Structural fix: the single reusable scrub for any tool result |
| `backend/ai/tools.py` | `_cap_result` now runs `scrub_pii_deep` on the whole result (before the `_client_action` pop) before anything else | Wire the scrub into the single choke point both `execute_tool()` and `/mcp` funnel through |
| `backend/ai/tools_rides.py` | `_driver_public` now uses `first_name_only` instead of concatenating first+last name | Fix the actual leak at its source (data minimization — a name isn't regex-detectable) |
| `backend/tests/test_ai_pii.py` | Added `TestScrubPiiDeep` (6 tests) and `TestCapResultScrubsToolResults` (3 tests) | Regression coverage for the new scrub function and its wiring |
| `backend/tests/test_ai_tools_rides.py` | Updated `test_active_ride_with_driver`'s assertion from `"Alex K"` to `"Alex"` | The old assertion encoded the pre-fix (full-name) behavior |
| `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` | Ranked blocker #5/#6 and their baseline-reconciliation/NEW-findings rows marked FIXED with evidence | Keep the audit's own ledger accurate |
| `ACTION_ITEMS.md` | A40 annotated with the fix | Same ledger-accuracy requirement |

## 7. Before / after

```python
# Before — backend/ai/tools_rides.py, _driver_public:
    info["name"] = (
        f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver"
    ) or "Driver"
```

```python
# After
    info["name"] = first_name_only(user, fallback="Driver")
```

```python
# Before — backend/ai/tools.py, _cap_result:
def _cap_result(result: Dict[str, Any]) -> Dict[str, Any]:
    client_action = result.pop("_client_action", None) if isinstance(result, dict) else None
    serialized = json.dumps(result, default=str)
    ...
```

```python
# After
def _cap_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(result, dict):
        result = scrub_pii_deep(result)
    client_action = result.pop("_client_action", None) if isinstance(result, dict) else None
    serialized = json.dumps(result, default=str)
    ...
```

## 8. Rollback plan

**Code revert is sufficient.** This adds a new scrub transformation to the tool-result pipeline — no
schema change, no data written or mutated, no migration, no persisted-value change (the scrub happens
per-call, on the response path, not on anything stored). Reverting the commit restores the previous
(unscrubbed) behavior immediately on next deploy. No feature flag was used: this closes a PIPEDA
compliance gap on an already-live surface, and every other PII-handling call site in this codebase
(`scrub_pii` on the user message, `filter_tool_leakage` on the reply text) is likewise unflagged.

## 9. Verification performed

- [x] Automated tests added and run: 6 new tests in `TestScrubPiiDeep`, 3 new tests in
  `TestCapResultScrubsToolResults` (`backend/tests/test_ai_pii.py`); updated 1 existing assertion in
  `test_ai_tools_rides.py` that encoded the pre-fix behavior.
- [x] Full AI/MCP test sweep: `pytest tests/ -k "ai_ or mcp"` — 500 passed, 2 skipped, 0 failed.
- [x] Full backend suite run: `pytest backend/tests` (entire suite) — 12,155 passed, 8 skipped, 1
  xfailed, 0 failed.
- [ ] Manual repro steps followed in staging — **not performed**; no staging environment or live LLM
  provider access in this session.
- [x] Blast-radius grep performed: every caller of `_cap_result` (one) and `_driver_public` (two, both
  in the same file), confirmed via full-suite pass that nothing else assumed the old full-name shape.
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA "full names — use user_id" rule (the
  fix); "Names cannot be scrubbed reliably with regex" (informed the design — data-minimization at the
  source, not scrub-and-hope); AI guardrail reuse-don't-duplicate convention (reused `first_name_only`
  rather than inventing a second name-minimization rule).
- [ ] Feature-flagged — **not applicable**, see rollback-plan justification above.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (revert the commit; nothing stored to unwind)
- [x] Blast radius is stated, not assumed (full caller grep in §4, full-suite test run in §9)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — §5 states
  the rider-facing effect explicitly (first name only instead of full name in AI replies — a privacy
  improvement, consistent with an existing convention used elsewhere in the app)

## What was NOT verified

- Not exercised against a live third-party LLM provider or a real `/mcp` client — only unit tests with
  direct calls to `_cap_result`/`scrub_pii_deep`/`_driver_public`.
- Did not audit every existing tool handler in `backend/ai/tools_*.py` for other data-minimization
  gaps beyond the driver-name case — `scrub_pii_deep` now provides a structural backstop for the
  regex-detectable categories, but a tool that surfaces some other non-regex-detectable PII (e.g. a
  home address in free text, a government ID number in an unexpected format) would still need its own
  source-level fix, the same way the driver name did. Not exhaustively searched for in this session.
- Did not verify the driver-app/rider-app AI-assistant UI renders the shortened driver name
  acceptably — this is a backend-only response-content change; no client screen was checked.
- Did not measure the performance cost of the added recursive scrub on typical (small) tool-result
  payloads — expected negligible given `TOOL_RESULT_MAX_CHARS = 4000` bounds every result's size
  regardless, but not benchmarked.
