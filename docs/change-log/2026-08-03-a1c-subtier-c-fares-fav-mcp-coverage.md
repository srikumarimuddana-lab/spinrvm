# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides (fares), rides (favorites), ai |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-fares-fav-mcp`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier C |

## 1. Issue / gap identified

Three files in the A1c Sub-tier C (60-80% coverage) band, from the original
2026-08-02 scoping scan, remained genuinely open as of the start of this
session (re-verified against `origin/main`'s `ACTION_ITEMS.md` and open PRs
before starting — neither had been picked up by any of today's many
concurrent A1c sessions):

- `backend/routes/fares.py` (136 stmts) — fare-estimate + vehicle-types
  endpoints, the `/fares` Redis-cached estimate lookup, and
  `build_fares_for_area`'s vehicle-pricing/fare_configs precedence logic.
  Documented baseline 72.79%; a fresh `--cov=` run at session start measured
  **65%** (47 missing) with only `tests/test_fares.py`'s targeted regression
  tests in scope — the discrepancy is explained by other test files (e.g.
  `test_ride_estimate_branches.py`, `test_create_ride_post_insert_branches.py`)
  incidentally exercising the HTTP endpoint without "fares" in their
  filename, which a `-k fares` filter misses; the full-suite baseline below
  is the number that matters.
- `backend/routes/favorites.py` (67 stmts) — rider saved-route CRUD +
  "save from completed ride." Documented baseline 73.13%; fresh run: 73%.
- `backend/ai/mcp_server.py` (124 stmts) — the optional `/mcp`
  streamable-HTTP mount exposing a read-only tool subset to external AI
  agent clients. Documented baseline 73.39%; fresh run: 73%.

## 2. Root cause

For `fares.py`: `tests/test_fares.py` covered the surge-cap regression and
the vehicle-pricing-vs-fare_configs precedence directly, but had zero
coverage of the money-string helpers' exception branches, the fare-cache
key/invalidate helpers, `resolve_service_area_for_point`,
`resolve_area_scope`'s empty-input guard, `build_fares_for_area`'s two
early-return guards, the legacy `fare_configs` fallback path inside
`build_fares_for_area`, the full `_fares_for_location_impl` orchestration
function, and the `/fares` endpoint's Redis cache hit/miss/read-error/
write-error branches.

For `favorites.py`: `tests/test_p3_addresses_favorites_safety_disputes.py`
covered list/create/duplicate/address-mismatch/delete-not-found, but never
exercised `POST /favorites/{id}/use`, the delete *success* path, or
`POST /favorites/from-ride/{ride_id}` (all three branches: not found, not
authorized, success-delegates-to-save).

For `mcp_server.py`: `tests/test_ai_mcp.py` thoroughly covered
`MCPAuthMiddleware` (kill switch, bearer auth, admin rejection, context
scoping) and the SDK-exposure rules, but the actual `_list_tools`/
`_call_tool` closures registered inside `build_mcp_asgi_app()` were never
driven end-to-end (only reachable through the `mcp` SDK's `Server` request-
handler dict, since both `@server.list_tools()` and `@server.call_tool()`
decorators return the *original* undecorated function to the caller, not
the registered handler — a genuinely awkward surface to test), and
`build_mcp_asgi_app()`'s top-level exception-swallow branch, `_audience_for`,
two `MCPAuthMiddleware.__call__` branches (non-HTTP ASGI scope, and an
auth failure that raises something other than `HTTPException`), and
`stop_mcp()`'s shutdown-exception swallow were all untested.

## 3. Fix / remediation

Test-only. Added three new test files, no application code changed:

- `backend/tests/test_fares_coverage.py` (29 tests)
- `backend/tests/test_favorites_coverage.py` (10 tests)
- `backend/tests/test_ai_mcp_coverage.py` (12 tests)

51 new tests total (kept as separate files alongside the existing
`test_fares.py`/`test_ai_mcp.py`, matching the established pattern e.g.
`test_payment_retry_coverage.py` next to `test_payment_retry.py`).

Per CLAUDE.md's surge-pricing conventions, `fares.py`'s new tests
explicitly assert (not just exercise for line coverage):
- `SURGE_CAP = 2.5` is a hard ceiling even when the DB row carries a higher
  value (`test_surge_never_exceeds_2_5x_hard_cap_even_with_higher_db_value`,
  DB value 9.9 → capped at 2.5).
- Surge only applies when both `surge_enabled` (admin master toggle) AND
  `surge_active` are true — a stale `surge_multiplier` left on a row with
  the toggle off must never leak through
  (`test_surge_toggle_off_ignores_stale_multiplier`).
- The `/fares` cache TTL is capped at 60s while surge is active, so a
  stale surge multiplier can't linger in the rider's cached estimate past
  the surge window (`test_cache_miss_with_surge_caps_ttl_at_60s`) — this is
  the mechanism that keeps the "surge visible before booking, never
  retroactive" guarantee intact even with the fare cache in the loop
  (payments.py separately re-validates at settlement, per the module's own
  header comment; not touched by this change).

For `mcp_server.py`, the `_list_tools`/`_call_tool` closures are exercised
by pulling the actual `Server` instance back out of
`_state["manager"].app` after `build_mcp_asgi_app()` and driving its
`request_handlers[ListToolsRequest]` / `request_handlers[CallToolRequest]`
directly with real `mcp.types` request objects — the same dispatch path
the streamable-HTTP transport itself uses, run against the real `mcp` SDK
(present in this environment; the SDK-absent path stays covered by
`test_ai_mcp.py`'s existing `skipif`-gated tests). `execute_tool` itself
is mocked in these tests (its own business logic is already covered by
`ai/tools_*.py` test files) — this file is scoped to `mcp_server.py`'s
routing/daily-cap/audience-scoping layer only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to three new test files.** No application code
  in `routes/fares.py`, `routes/favorites.py`, or `ai/mcp_server.py` (or
  anywhere else) was modified.
- Grepped every other consumer of the functions under test:
  - `routes/fares.py`'s `build_fares_for_area` / `resolve_service_area_for_point`
    / `_fares_for_location_impl` — also called from `routes/rides.py`'s
    `create_ride` flow (fare re-validation at booking time) and
    `services/fare_service.py` imports `DEFAULT_FARE` from the same
    module. Neither call site was modified; the new tests only add
    coverage of `fares.py`'s own functions in isolation with mocked
    `db_supabase`.
  - `routes/favorites.py` has no other backend consumers — it's a
    self-contained CRUD surface for the rider-app only.
  - `ai/mcp_server.py`'s `MCPAuthMiddleware`/`build_mcp_asgi_app` are
    wired into `backend/core/lifespan.py` (`start_mcp()`/`stop_mcp()` at
    app startup/shutdown) and mounted once in `backend/server.py`; the
    `TOOL_REGISTRY` it reads from is shared with the chat-path AI
    orchestrator (`ai/orchestrator.py` or similar) — this pass does not
    modify the registry or any tool handler, only reads `mcp_exposed`/
    `audiences` off existing `ToolSpec` entries (verified via the existing
    `TestExposureRules` tests, unchanged).
- No money, ride-state-machine, or insurance-period code touched.
  `fares.py`'s surge-cap and cache-TTL behavior is asserted, not changed.
- No migration, no RLS change, no Stripe/webhook surface touched.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_fares_coverage.py` | New file — 29 tests | Close coverage gap on `routes/fares.py` |
| `backend/tests/test_favorites_coverage.py` | New file — 10 tests | Close coverage gap on `routes/favorites.py` |
| `backend/tests/test_ai_mcp_coverage.py` | New file — 12 tests | Close coverage gap on `ai/mcp_server.py` |
| `docs/change-log/2026-08-03-a1c-subtier-c-fares-fav-mcp-coverage.md` | New file (this log) | Required per CLAUDE.md for anything landing on a live-tested surface's test suite |
| `ACTION_ITEMS.md` | Added three closed entries under A1c Sub-tier C | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing
diff. `fares.py`/`favorites.py`/`ai/mcp_server.py` themselves are byte-for-
byte unchanged.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] New test files run alone:
  `pytest tests/test_fares_coverage.py tests/test_favorites_coverage.py tests/test_ai_mcp_coverage.py -q --no-cov`
  — **51 passed** (29 fares + 10 favorites + 12 mcp_server).
- [x] Run together with the pre-existing test files covering these three
  modules:
  `pytest tests/test_fares_coverage.py tests/test_favorites_coverage.py tests/test_ai_mcp_coverage.py tests/test_fares.py tests/test_ai_mcp.py tests/test_p3_addresses_favorites_safety_disputes.py --cov=routes.fares --cov=routes.favorites --cov=ai.mcp_server --cov-report=term-missing --no-cov-on-fail`
  — **97 passed, 1 skipped**, no collisions.
- [x] Coverage measured (same command as above):
  - `routes/fares.py`: **136 stmts, 4 missing, 97%** (was 65% on a fresh
    isolated `-k fares` run / 72.79% documented baseline).
  - `routes/favorites.py`: **67 stmts, 2 missing, 97%** (was 73%).
  - `ai/mcp_server.py`: **124 stmts, 7 missing, 94%** (was 73%).
  Remaining gaps in all three are the dual-import `ImportError` fallback
  blocks (structurally unreachable in this harness — same documented
  pattern as prior Sub-tier A/B/C files) plus, for `mcp_server.py`, the
  SDK-absent branch (environment has the SDK installed, so
  `test_ai_mcp.py`'s existing `skipif`-gated test is the only coverage of
  that path) and the single `_asgi`/`manager.handle_request` line, which
  would require driving a full streamable-HTTP/SSE protocol negotiation to
  reach — judged out of scope for a unit-test pass.
- [x] Full backend suite: `pytest tests/ -q --no-cov` —
  **8762 passed**, 8 skipped, 1 xfailed, 0 failed, 412s (baseline
  immediately before this session's changes, measured fresh at session
  start: **8711 passed**, 8 skipped, 1 xfailed, 0 failed, 588s). Delta
  +51 passed, exactly matching the 51 new tests added — 0 regressions.
  Note: `origin/main` is extremely active today (many concurrent A1c
  sessions); this before/after pair was measured back-to-back on the same
  checkout before any `origin/main` merge, so the delta isolates this
  session's contribution cleanly. The suite was re-run once more after
  merging `origin/main` in immediately before pushing (see commit
  history) to catch any conflict-introduced regression.
- [x] Blast-radius grep performed: see section 4 above.
- [x] Reviewed against CLAUDE.md's surge-pricing conventions (section 3
  above spells out the specific assertions made).
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Not run against real Supabase or a real Redis instance — mocked
  throughout, matching repo convention for this test tier.
- `mcp_server.py`'s `_list_tools`/`_call_tool` tests exercise the real
  `mcp` SDK's request-handler dispatch (jsonschema input validation, tool
  cache refresh, `ServerResult` wrapping) but do **not** exercise the
  actual streamable-HTTP transport (`StreamableHTTPSessionManager.handle_request`,
  SSE framing, session-id negotiation) — that would require a live
  ASGI/HTTP round trip and is out of scope for a unit-coverage pass; the
  one remaining uncovered line in `mcp_server.py` (`_asgi`'s
  `manager.handle_request(...)` call) is exactly that boundary.
- No production/behavior bugs found in any of the three files during this
  pass — pure test-coverage exercise. Nothing to flag under "considered
  but not fixed."
- `fares.py`'s Redis cache correctness under real concurrent load (two
  requests racing on the same cache key) is not exercised — only the
  hit/miss/error branches in isolation, matching how the rest of the
  Redis-cache-adjacent test suite in this repo is scoped.
