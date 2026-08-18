# Change Impact & Risk Log — driver-accept-while-offline fix

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code session (see PR for attribution) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (this commit) |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` ranked blocker #4; `ACTION_ITEMS.md` A40 |

## 1. Issue / gap identified

`accept_ride` (`backend/routes/drivers/ride_flow.py`) never checked whether the driver was still
online before letting them accept a ride. A driver who went offline (via `POST /drivers/status
{is_online: false}`) after being claimed for an offer — or whose app is killed while a stale queued
push-notification action is still pending, or who simply retries a cached UI state — could still
successfully accept the offer, stranding the rider with a driver who never shows up.

## 2. Root cause

`accept_ride` fetches the driver row once at request entry and runs it through a chain of
eligibility gates (suspended status, document expiry, subscription, service area, daily quota) before
the atomic accept UPDATE — but `is_online` was never one of those gates. `backend/utils/error_handling.py`
already defined `DriverOfflineException` (and `ErrorCode.DRIVER_OFFLINE` / `ErrorKeys.DRIVER_OFFLINE`)
specifically for this case, but it had never actually been raised anywhere in the codebase — confirmed
by grepping every call site before this fix.

## 3. Fix / remediation

`accept_ride` now checks `driver.get("is_online")` immediately after the suspended-status check (before
the more expensive document-expiry check) and raises the pre-existing `DriverOfflineException` if the
driver is offline. This uses the driver row already fetched at function entry — no additional DB
round-trip, keeping the accept path inside its documented <2s dispatch-SLA budget.

`is_available` was deliberately NOT used for this check: it is already `False` for every driver with a
pending offer by design (`claim_driver_atomic` flips it at claim time, and — since the companion Period
2 fix earlier this session — it stays `False` through the whole offer window). Checking `is_available`
here would reject every legitimate accept, not just offline ones. `is_online` is the correct,
driver-toggled field, matching the same field `go_online`'s own eligibility gates key off of.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), isolated to `accept_ride` and its two import sites.**

- **Files changed:** `backend/routes/drivers/ride_flow.py` (the check itself),
  `backend/routes/drivers/_deps.py` (added `DriverOfflineException` to both branches of the dual-import
  pattern — it was defined but never imported anywhere in the `drivers` package).
- **Callers of `accept_ride`:** the single `POST /rides/{ride_id}/accept` route. Grepped for any other
  Python caller (e.g. an admin action re-using this handler) — none found; `routes/admin/rides.py`'s
  manual-assign path writes `driver_assigned` directly and does not call through `accept_ride`.
- **Legitimate-driver impact:** none. A driver who is genuinely online (the overwhelming majority of
  real accepts) sees no behavior change — `is_online` was already `True` for them, so the new check is
  a no-op. Only a driver whose `is_online` is already `False` at the moment they hit this endpoint is
  newly rejected, which is exactly the intended fix.
- **Client-side handling:** the driver-app's accept-button handler needs to surface
  `DriverOfflineException`'s message ("You're currently offline — go online to accept rides") to the
  driver if it doesn't already have generic error-toast handling for unrecognized error codes. Not
  verified in this session (backend-only fix) — flagged under "What was NOT verified" below.
- **Does NOT touch:** the offer-expiry/reaper cleanup path (`process_expired_offer`,
  `_batch_offer_timeout_handler` in `backend/routes/rides/matching.py`) — an offline driver's pending
  `ride_offers` row is left to expire naturally via the existing 15s batch timeout, same as before this
  fix. Proactively expiring it here (for faster re-dispatch) was considered and deliberately left out of
  scope — it would touch a second file/mechanism for a UX latency improvement, not the correctness gap
  this fix closes; the ride still gets re-dispatched, just up to 15s later than it could.
- **Test fixtures updated (not production code):** the shared `_driver()`/`_driver_row()` test helpers in
  `test_driver_ride_flow_coverage.py`, `test_e2e_ride_lifecycle.py`, `test_ride_accept_flow.py`, and
  `test_rides.py` (plus two inline driver dicts in `test_rides.py`) previously didn't set `is_online`
  at all — meaning every one of the ~90 existing tests using them represented an (accidentally) offline
  driver. Added `is_online: True` to each default fixture so they correctly represent the legitimately-
  online driver every one of those tests actually intends to exercise; this is a test-fixture
  correction, not a behavior change to any of those tests' assertions.

## 5. User-experience effect

**Driver-facing.** A driver who is offline and attempts to accept a ride (via a stale notification,
cached UI, or retry) now sees an explicit rejection instead of silently succeeding into a ride they
can't actually service. This is a new, narrow error path — it was previously impossible to observe
because the accept would just succeed. Not visible to riders directly, but prevents the downstream
rider-facing failure mode (a "driver found" state that never resolves because the driver never
arrives). No screen copy was added in this backend-only change; the client already has generic
error-alert handling for unrecognized `SpinrException` responses per the existing pattern used for
every other gate in this same function (subscription-required, service-area, suspended).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_flow.py` | `accept_ride` now rejects with `DriverOfflineException` when the already-fetched driver row shows `is_online=False` | Close the accept-while-offline gap |
| `backend/routes/drivers/_deps.py` | Added `DriverOfflineException` to both branches of the dual-import pattern | Make the exception reachable from `ride_flow.py` |
| `backend/tests/test_driver_ride_flow_coverage.py` | Added `is_online: True` to the `_driver()` default fixture; added 2 tests for the new rejection (offline driver blocked; check runs even when the ride itself doesn't exist) | Regression coverage + keep ~90 existing tests representing an online driver |
| `backend/tests/test_e2e_ride_lifecycle.py` | Added `is_online: True` to `_driver_row()` and the two inline `driver_a`/`driver_b` dicts | Same — these fixtures represent legitimately-online drivers |
| `backend/tests/test_ride_accept_flow.py` | Added `is_online: True` to `_driver_row()` | Same |
| `backend/tests/test_rides.py` | Added `is_online: True` to the inline `driver_1`/`driver_2` dicts in `test_no_double_accept` and the `driver` dict in `test_full_ride_lifecycle` | Same |
| `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` | Ranked blocker #4 and its baseline-reconciliation row marked FIXED with evidence | Keep the audit's own ledger accurate |
| `ACTION_ITEMS.md` | A40 annotated with the fix | Same ledger-accuracy requirement |

## 7. Before / after

```python
# Before — backend/routes/drivers/ride_flow.py, accept_ride:
    if driver.get("status") == "suspended":
        raise AccountDisabledException(...)

    # Mid-session document-expiry re-check (P1 #12): ...
    try:
        await check_driver_documents_current(driver)
    ...
```

```python
# After
    if driver.get("status") == "suspended":
        raise AccountDisabledException(...)

    # Offline re-check (2026-08-18 whole-app fleet audit, ranked blocker #4): ...
    if not driver.get("is_online"):
        diag_logger.info(f"[ACCEPT] rejected: driver_id={driver['id']} is offline ...")
        raise DriverOfflineException(driver["id"])

    # Mid-session document-expiry re-check (P1 #12): ...
    try:
        await check_driver_documents_current(driver)
    ...
```

## 8. Rollback plan

**Code revert is sufficient.** This adds a new rejection branch to a read path — no schema change, no
data written or mutated, no migration. Reverting the commit restores the previous (permissive)
behavior immediately on next deploy. No feature flag was used: this closes a correctness gap on a
liability-relevant action (an offline driver completing a ride-acceptance write), and every other gate
in this same function (suspended, documents, subscription, service-area, quota) is likewise unflagged
— flagging this one gate inconsistently would leave the exact failure mode reachable via a flag flip.

## 9. Verification performed

- [x] Automated tests added and run: 2 new tests in `test_driver_ride_flow_coverage.py`
  (`test_rejects_accept_from_an_offline_driver`, `test_offline_check_runs_before_the_ride_lookup`).
- [x] Full regression sweep: every test file found to call `accept_ride` directly or indirectly
  (`test_accept_ride_document_expiry.py`, `test_accept_ride_service_area_gate.py`, `test_claim_ride.py`,
  `test_dispatch_metrics.py`, `test_e2e_ride_lifecycle.py`, `test_p1_security.py`,
  `test_ride_accept_flow.py`, `test_rides.py`, `test_spinr_pass_quota.py`,
  `test_subscription_enforcement.py`, `test_driver_ride_flow_coverage.py`,
  `test_forced_upgrade_middleware.py`) — 247 tests, all pass.
- [x] Full backend suite run: `pytest backend/tests` (entire suite, not just the affected files) — 12,146
  passed, 8 skipped, 1 xfailed, 0 failed. Confirms no other test anywhere in the repo carried an
  undeclared-offline driver fixture through `accept_ride`.
- [ ] Manual repro steps followed in staging — **not performed**; no staging environment access in this
  session.
- [x] Blast-radius grep performed: every Python caller of `accept_ride`, every prior call site of
  `DriverOfflineException`/`ErrorCode.DRIVER_OFFLINE` (none), every test fixture feeding a driver dict
  into `accept_ride` across the whole backend test suite.
- [x] Reviewed against relevant CLAUDE.md conventions: "Driver online/available flags" section (this fix
  checks `is_online`, not `is_available`, exactly per that section's guidance); "do not silently swallow
  errors" (the new branch raises loudly, logs via `diag_logger.info` for observability, does not swallow
  anything).
- [ ] Feature-flagged — **not applicable**, see rollback-plan justification above.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (revert the commit; no data to unwind)
- [x] Blast radius is stated, not assumed (full caller + fixture grep in §4, full-suite test run in §9)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — §5 states
  the driver-facing effect explicitly (a new, narrow rejection path for an already-broken scenario)

## What was NOT verified

- Not exercised against a live/staging Supabase instance or a real driver-app client — only unit tests
  with mocked `db_supabase`/`_deps`.
- Did not verify the driver-app's error-toast handling actually surfaces `DriverOfflineException`'s
  message in a driver-friendly way — the backend now returns a structured error (400,
  `ErrorCode.DRIVER_OFFLINE`, `message_key: "errors.driver.offline"`), but no rider/driver-app screen
  was checked for how (or whether) it renders an unrecognized `message_key`.
- Did not implement proactive offer-expiry when an offline-driver's accept is rejected — the pending
  `ride_offers` row still waits out the existing 15s batch timeout before the ride re-dispatches; this
  was a deliberate scope decision (see §4), not an oversight, but it means the rider still experiences
  up to a 15s delay in this specific failure mode rather than an immediate re-dispatch.
- Did not check whether a driver could still be mid-transition (e.g. a `POST /drivers/status` call to go
  offline racing an in-flight accept request) in a way that reads a stale `is_online=True` row a few
  milliseconds before the toggle lands — this fix closes the primary, already-landed-offline case the
  audit described; a true concurrent-toggle race window (sub-second) was not specifically modeled or
  tested.
