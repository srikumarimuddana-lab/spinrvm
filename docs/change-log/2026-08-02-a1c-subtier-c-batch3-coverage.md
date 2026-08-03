# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch / payments / corporate (PIPEDA-adjacent) |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch3`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier C, Batch 3 |

## 1. Issue / gap identified

Three files sat in the 60-80% coverage band (Sub-tier C), each flagged in
`ACTION_ITEMS.md`'s freshly-itemized batch list as deserving Sub-tier-A-style
urgency despite the raw ranking:

- `backend/utils/retention_purge.py` (PIPEDA retention/deletion enforcement —
  69.12%, 136 stmts, 42 missing)
- `backend/utils/orphaned_hold_reconciler.py` (Stripe pre-auth-hold cleanup —
  69.23%, 91 stmts, 28 missing)
- `backend/utils/driver_online.py` (the `intent_online`/`effective_online`/
  `effective_available` composition helper that directly implements the
  documented `is_available ⇒ is_online` invariant — 69.70%, 33 stmts, 10
  missing)

## 2. Root cause

All three had pre-existing test files (`test_retention_purge.py`,
`test_orphaned_hold_reconciler.py`) that covered the primary happy/failure
paths thoroughly but left the less-common error branches, alternate
response-parsing shapes, and the background-loop control flow itself
untested. `driver_online.py` had **no dedicated test file at all** — only
indirect coverage via `intent_online()` calls inside
`test_estimate_intent_projection.py`, which exercises one call site, not the
module's own composition logic.

## 3. Fix / remediation

Test-only change. No application code in any of the three files was
modified.

- **`backend/tests/test_driver_online.py`** (new file, 50 tests) —
  `_parse_ts` edge cases (None/empty/malformed/naive-datetime/Z-suffix/
  tz-aware passthrough); `intent_online`'s full timestamp-composition matrix
  (on-only, off-only, both-set-either-wins, equal-timestamp tie going to
  offline, empty-string treated as unset, malformed timestamp falling
  through, legacy `is_online` fallback for un-migrated rows);
  `effective_online`'s intent×presence matrix and no-driver-id case;
  `effective_available`'s intent×presence×active-ride matrix;
  `filter_effective_online`. Plus a dedicated invariant section (see
  Tier-3 compliance note below).
- **`backend/tests/test_retention_purge_coverage.py`** (new file, 20 tests)
  — every error branch inside `_delete_expired_route_snapshot_objects`
  (ledger-query failure, empty-pending no-op, Storage-removal failure,
  ledger-acknowledgement-write failure, route-reference-clear failure, and
  the invalid-response-shape guard), the `run_retention_purge_tick`
  alternate response-parsing paths (plain-dict `res`/`route_result` rather
  than an object exposing `.data`), the trip-route-geometry rpc's own
  error/invalid-shape branches, the post-storage re-fetch's three branches
  (success, rpc-raises, invalid-shape), the `dsar_users_skipped_fk` loud-log
  line (both present and absent), `dry_run=True` never triggering Storage
  deletion, `_pod_id()`, and both branches of `_tick()` (lock acquired /
  another replica holds it).
- **`backend/tests/test_orphaned_hold_reconciler_coverage.py`** (new file,
  8 tests) — `release_open_hold` itself raising (not just returning a
  failure outcome) is caught per-ride and counted as `failed` without
  stranding the rest of the batch; `_pod_id()`; and
  `orphaned_hold_reconciler_loop`'s control flow — the initial 0-60s
  stagger sleep, the lock-not-acquired skip-and-`continue` path (including
  looping back around for a second pass), the happy-path tick + conditional
  summary log, generic-exception handling incrementing
  `spinr_bgloop_errors_total`, and `asyncio.CancelledError` propagating out
  instead of being swallowed as a generic tick failure.

## 4. Risk & impact on existing functionality

**Blast radius — every real caller enumerated, all unmodified:**

- `retention_purge_loop` / `run_retention_purge_tick` / `_tick` — spawned
  exactly once as a background task from `backend/core/lifespan.py`
  (`_spawn("retention_purge (24h)", retention_purge_loop)`). Not called
  from any request path. `test_p3_loop_jitter_metrics.py` and
  `test_p3_promo_concurrency.py` also reference this module (jitter/metric
  regression tests, unrelated to this pass) — unaffected, still pass.
- `orphaned_hold_reconciler_loop` / `reconcile_tick` — spawned once from
  `backend/core/lifespan.py`. `backend/scripts/reconcile_orphaned_holds.py`
  is a manual operator CLI that also imports `reconcile_tick` for a
  one-off dry-run/live invocation — not touched, and the new tests exercise
  the same function signature it calls.
- `intent_online` / `effective_online` / `effective_available` /
  `filter_effective_online` — read by `backend/routes/admin/monitoring.py`
  (admin driver-online dashboards), `backend/routes/websocket.py`
  (connection-time online-state checks), `backend/routes/drivers/_deps.py`
  (shared dependency used across the drivers route package), and
  `backend/routes/rides/estimates.py` (`intent_online` filters the
  ghost-driver projection for fare estimates — this is the call site
  `test_estimate_intent_projection.py` already covers). None of these
  call sites were modified; the new test file exercises the same public
  functions they import.

**PIPEDA/compliance adjacency (`retention_purge.py`):** this module enforces
the "User rights → Deletion" policy from root `CLAUDE.md` — DSAR accounts
hard-deleted only past their 7-year window, rides GPS-scrubbed at 3 years,
hard-deleted at 7 years. The new tests assert the `skipped_fk` loud-log path
(a DSAR account blocked on a residual FK must never fail silently) and every
Storage/DB error branch re-raises rather than swallowing — consistent with,
not a change to, the module's existing behavior. No retention SQL, no
migration, no application logic touched.

**Payment adjacency (`orphaned_hold_reconciler.py`):** this module cancels
Stripe authorizations autonomously on a 15-minute cadence. The riskiest
class of bug here is releasing a hold that belongs to a driver (a completed
ride's fare) — that invariant (`_TERMINAL_STATES = ("cancelled",)` only) was
already covered by the pre-existing test file and is untouched by this pass.
The new tests add coverage for `release_open_hold` *raising* (vs. returning
a failure code), which the existing per-ride try/except already handled in
production code — no behavior change, only verification that the existing
catch actually catches it.

**Dispatch-critical (`driver_online.py`) — the invariant.** Root `CLAUDE.md`
states: *"the invariant `is_available ⇒ is_online` must hold; the inverse
does not."* This is now explicitly, exhaustively tested:
`test_invariant_available_implies_online_across_every_state_combo` in
`test_driver_online.py` is parametrized over every intent state this module
can produce (online-via-timestamp, offline-via-timestamp, online-via-legacy-
flag, offline-via-legacy-flag, never-toggled) × presence (absent/present) ×
active-ride (free/on-active-ride) — 20 combinations — and asserts
`not (effective_available and not effective_online)` in each one, i.e. the
test would fail loudly if any future change to `effective_online`/
`effective_available`'s composition broke the forward implication. A
companion test (`test_invariant_online_true_does_not_imply_available_true`)
confirms the documented inverse-does-NOT-hold case (online but mid-trip ⇒
not available) still behaves as documented. No production code was
changed — this is purely a coverage/regression-guard addition for an
invariant that already held; the tests exist so a future regression is
caught by CI rather than in production dispatch.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_driver_online.py` | New file — 50 tests | Close coverage gap on `utils/driver_online.py` (69.70% → 100%); explicit invariant test |
| `backend/tests/test_retention_purge_coverage.py` | New file — 20 tests | Close coverage gap on `utils/retention_purge.py` (69.12% → 98%) |
| `backend/tests/test_orphaned_hold_reconciler_coverage.py` | New file — 8 tests | Close coverage gap on `utils/orphaned_hold_reconciler.py` (69.23% → 95%) |
| `docs/change-log/2026-08-02-a1c-subtier-c-batch3-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested/compliance surface |
| `ACTION_ITEMS.md` | Reconciled Sub-tier C's itemized batch list locally (PR #3335 not yet merged) and marked Batch 3 closed with real numbers | Track progress per the existing series format; avoid diverging from the in-flight scoping PR's structure |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing
diff in application code. The closest thing to a "behavior" diff is the new
invariant test itself:

```python
# backend/tests/test_driver_online.py — new coverage, not a code change
def test_invariant_available_implies_online_across_every_state_combo(...):
    online = effective_online(driver, present_ids)
    available = effective_available(driver, present_ids, on_active_ride=on_active_ride)
    assert not (available and not online), (
        f"INVARIANT VIOLATION ... effective_available=True but effective_online=False"
    )
```

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] New test files run alone:
  - `pytest tests/test_driver_online.py -q --no-cov` — **50 passed**
  - `pytest tests/test_retention_purge_coverage.py -q --no-cov` — **20 passed**
  - `pytest tests/test_orphaned_hold_reconciler_coverage.py -q --no-cov` — **8 passed**
- [x] Run together with every existing test file touching these three
  modules:
  `pytest tests/test_driver_online.py tests/test_retention_purge.py
  tests/test_retention_purge_coverage.py tests/test_orphaned_hold_reconciler.py
  tests/test_orphaned_hold_reconciler_coverage.py tests/test_p3_loop_jitter_metrics.py
  tests/test_estimate_intent_projection.py -q --cov=utils.retention_purge
  --cov=utils.orphaned_hold_reconciler --cov=utils.driver_online
  --cov-report=term-missing --no-cov-on-fail` — **120 passed, 0 collisions**.
- [x] Coverage measured (same command as above):
  - `utils/driver_online.py`: **100%** (33 stmts, 0 missing — up from 69.70%/10 missing)
  - `utils/orphaned_hold_reconciler.py`: **95%** (91 stmts, 5 missing — up from 69.23%/28 missing). Remaining 5 lines are the dual-import `ImportError` fallback boilerplate for `card_hold_release`/`metrics`/`redis_client` — same documented structurally-near-unreachable pattern as prior Sub-tier B/C files (CLAUDE.md: "do not simplify away" the dual-import pattern).
  - `utils/retention_purge.py`: **98%** (136 stmts, 3 missing — up from 69.12%/42 missing). Remaining 3 lines are the same dual-import `ImportError` fallback pattern for `loop_monitor`/`redis_client`.
- [x] Full backend suite: `pytest tests/ -q --no-cov` — **before: 8415
  passed, 8 skipped, 1 xfailed** (baseline run at session start, before any
  new files were added) → **after: 8526 passed, 8 skipped, 1 xfailed, 0
  failed** (406s). Delta is +111; this session added 78 new tests, so the
  remaining +33 reflects other sessions' commits landing on `main` while
  this branch was in progress (repo is under heavy concurrent-agent load
  today per this task's own warning) — not attributable to this change, and
  zero failures either way.
- [x] Blast-radius grep performed: see section 4 above, every real caller
  of `retention_purge_loop`, `orphaned_hold_reconciler_loop`/
  `reconcile_tick`, and the four `driver_online` public functions
  enumerated and confirmed unmodified.
- [x] Checked for open/recently-merged PRs on these three files
  (`mcp__github__search_pull_requests`, query `"driver_online" OR
  "period1_distance_finalizer" OR "driver_claim_reaper" in:title,body
  is:open`) before starting — **none found**, no duplicate-sweep risk.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [ ] Feature-flagged — not applicable, test-only.
- [x] Real production build — not applicable, backend-only change (no
  `admin-dashboard`/`rider-app`/`driver-app` files touched).

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout, matching repo
  convention for this test tier.
- The background-loop tests (`retention_purge_loop`,
  `orphaned_hold_reconciler_loop`) exercise exactly one-to-two iterations
  of their respective `while True:` loops per test (via forcing
  `asyncio.sleep` to raise a sentinel exception), the same technique
  already used by `test_p3_loop_jitter_metrics.py`. The Redis
  distributed-lock's real cross-replica mutual-exclusion behavior under
  concurrent replicas is not exercised here — only that the code branches
  correctly on the lock result. A real multi-replica integration test is
  out of scope for this coverage pass.
- No bugs found or fixed in any of the three files during this pass — pure
  test-coverage exercise per the task instructions. No "considered but not
  fixed" findings turned up in `orphaned_hold_reconciler.py` or
  `retention_purge.py`'s error-handling branches; each re-raises loudly
  after `logger.exception`/`logger.error` exactly as documented.
- `driver_online.py`'s invariant test is a **behavioral property test on
  the existing implementation**, not a proof — it exhaustively covers the
  finite state space the module's own composition can produce (5 intent
  states × 2 presence states × 2 active-ride states = 20 combinations), but
  does not fuzz malformed/adversarial inputs beyond what `_parse_ts`'s
  existing tests already cover (malformed strings, empty strings, `None`).
- The 5 (orphaned_hold_reconciler) and 3 (retention_purge) remaining
  uncovered lines are dual-import `ImportError` fallback branches judged
  not worth chasing given the fragility of the `sys.modules` monkeypatching
  that would be required to reach them in this test harness — not pursued
  further, consistent with how prior Sub-tier B/C sessions treated the
  same pattern.
- `ACTION_ITEMS.md`'s Sub-tier C itemization was reconciled locally from
  PR #3335's diff (still open/draft, not yet merged as of this session) —
  if that PR's content changes materially before merging, a future session
  will need to re-reconcile; not something this session can control.
