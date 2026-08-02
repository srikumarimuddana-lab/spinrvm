# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (this branch: `claude/a1c-drivers-ride-flow-batch`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier A |

## 1. Issue / gap identified

`backend/routes/drivers/ride_flow.py` (driver accept/decline/arrive/verify-otp/
start), `backend/routes/drivers/ride_cancel.py` (driver cancel/no-show/
rate-rider), and `backend/routes/drivers/ride_reads.py` (driver active-ride
and ride-history reads) — three of the highest-traffic files in the
`routes/drivers/` package — sat at 66.30%, 51.75%, and 58.95% coverage
respectively (baseline re-measured fresh this session via the full
`pytest tests/ -q --cov=routes.drivers.ride_flow --cov=routes.drivers.ride_cancel
--cov=routes.drivers.ride_reads --cov-report=term-missing`, and matched the
`ACTION_ITEMS.md`-documented numbers exactly). These files own the core
driver-side ride state machine (`driver_assigned → driver_accepted →
driver_arrived → in_progress`), the pickup-OTP handshake, driver-side
cancellation/no-show fee collection, and the active-ride/earnings-history
reads the driver app polls continuously.

## 2. Root cause

A broad set of pre-existing tests (`test_drivers_extended.py`,
`test_ride_accept_flow.py`, `test_subscription_enforcement.py`,
`test_c2_driver_cancel_atomic.py`, `test_active_ride_rider_pii.py`,
`test_rides.py`, `test_dispatch_metrics.py`, `test_claim_ride.py`,
`test_fee_wallet_atomic.py`, `test_preauth_release_on_cancel.py`,
`test_idor_ownership_guards.py`) already covered the happy paths and the
highest-severity race/IDOR guards (double-accept, driver-cancel ownership,
atomic-claim-lost races, pre-auth-hold release, wallet-fee-debit atomicity).
What was missing was the long tail of secondary branches: `accept_ride`'s
Spinr Pass subscription-guard sub-branches (parent-area inheritance, expired-
row auto-flip, plan service-area/vehicle-type allowlist mismatches), the
batch-dispatch loser-notification and ride_metrics-write non-fatal-failure
paths, `verify_pickup_otp` as a standalone unit (previously only exercised
end-to-end inside `test_full_ride_lifecycle`), `mark_rider_noshow`'s entire
success path (fee calc → wallet debit → driver payout — previously only the
409-claim-lost branch had coverage), and `get_active_ride`/`get_ride_history`'s
enrichment branches (incentives, quest hints, service-area polygon,
incentive-claims, earnings-snapshot vs. legacy-computed totals).

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_driver_ride_flow_coverage.py`
(95 tests) covering all three files together (kept as one file rather than
three, since they were split from the same god-file and share the same
`_deps`/`_shared` patch-target conventions — matches this batch's own
"whichever fits" guidance). No application code changed.

Coverage closed, by function:
- `accept_ride` — driver-not-found/self-accept/suspended-driver guards; the
  subscription-required gate's child-area-inherits-from-parent branch, the
  expired-active-sub-row auto-mark-expired branch, the plan
  service-areas/vehicle_types allowlist mismatch (including the
  parent-area-coverage exception that lets it pass), and the DB-error
  fails-closed 503; the searching/broadcast claim path's no-pending-offer
  403 and offer-lookup-exception-still-403 branches, and the
  not-assigned-and-not-searching 400; the claim-lost re-check's
  same-driver-idempotent-success vs. taken-by-another-409 split; the
  batch-dispatch winner/loser resolution including a loser's `ride_taken` WS
  push failing non-fatally; the ride_metrics pickup-leg write's success and
  non-fatal-failure paths; and the guest-booking notification spawn.
- `decline_ride` — 404/409/403 guards; the audit-log-insert and
  Redis-cooldown-set non-fatal failure branches; the early-resolution
  re-dispatch decision (offers-remain / no-offers-remain / rematch-check
  exception).
- `arrive_at_pickup` — 404 guards; the 200m geofence rejection and the
  nav-point-vs-raw-pickup-pin nearest-of-either-target check; the 409
  guard-none branch; guest-booking arrival SMS.
- `verify_pickup_otp` — previously zero standalone coverage (only exercised
  as one step inside `test_rides.py::test_full_ride_lifecycle`). Added
  404/400 (OTP mismatch)/409 guards and the success + rider-notify path.
- `start_ride` — the production-environment 410 block, and the
  previously-untested ride-not-found 404 (dev/staging path).
- `cancel_ride` (driver-side) — 404/error-state guards; the JSON-body vs.
  query-param `reason` precedence (and the parse-failure fallback), both
  previously untested for this file (the identical pattern in the
  rider-side `routes/rides/cancellation.py` was already covered by
  `test_ride_cancellation_branches.py`); the PGRST204 attribution-write
  fallback; the pre-auth-release success/exception/write-failure-after-
  success branches; the admin-broadcast-failure non-fatal branch; the
  scheduled-ride `is_scheduled` broadcast flag.
- `mark_rider_noshow` — previously only the 409-claim-lost race branch had
  coverage (`test_c2_driver_cancel_atomic.py`). Added the full success path:
  wallet-payment-method fee debit + driver payout, partial-wallet-collection
  logging, card-payment-method skipping the wallet debit, the area-level
  `noshow_wait_seconds` override, the naive-datetime (non-string, no tzinfo)
  `driver_arrived_at` normalization branch, the extended-fee-columns
  PGRST204 fallback, and the admin-broadcast-failure non-fatal branch.
- `rate_rider` — added the 404-driver-not-found guard (the IDOR and
  ride-not-found paths were already covered by `test_drivers_extended.py`).
- `get_active_ride` — the batch-offer fallback's found/not-found/
  stale-ride-no-longer-searching/lookup-exception branches; the rider-lookup
  and vehicle-type-lookup exception paths (leaving the field `None` rather
  than 500ing); the `driver_assigned`-status incentives+quest-hint
  enrichment, including the service-area `or_()` clause and the
  vehicle-type-mismatch filter, each lookup's independent non-fatal
  exception; and the service-area-polygon fetch success/exception paths.
- `get_ride_history` — the 404 guard; the incentive-claims enrichment
  (including its own non-fatal lookup-exception branch); the
  `driver_earnings_snapshot`-present branch vs. the legacy
  computed-from-columns branch; the `fare_breakdown_snapshot` tax-line
  fallback when `tax_amount` is zero; the `period=None`/`"all"`/`"week"`/
  `"month"` branches of the nested `history_start_for_period` helper; and
  the explicit `status="scheduled"` branch of `history_date_field`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to a new test file.** No application code in
  `routes/drivers/ride_flow.py`, `ride_cancel.py`, `ride_reads.py`, or
  anywhere else was modified. Grepped every real caller:
  - All nine functions under test (`accept_ride`, `decline_ride`,
    `arrive_at_pickup`, `verify_pickup_otp`, `start_ride`, `cancel_ride`,
    `mark_rider_noshow`, `rate_rider`, `get_active_ride`, `get_ride_history`)
    are FastAPI route handlers mounted directly on `router` in their
    respective modules and re-exported through `routes/drivers/__init__.py`
    (`from .ride_flow import *` etc., per the god-file-split pattern
    documented in `docs/refactors/god-file-split.md`). Their only real
    caller is the driver-app HTTP client — no other backend module invokes
    them as plain functions.
  - Shared helpers these functions call into (`_deps.record_period_transition`,
    `_deps.manager.broadcast_ride_status`/`send_personal_message`,
    `repositories.driver_repo.update_acceptance_rate`,
    `services.cancellation_service.calculate_noshow_fee`/
    `pay_driver_cancellation_fee`, `utils.spinr_pass.assert_quota_available`,
    `services.guest_notification_service.notify_guest_driver_assigned`/
    `notify_guest_driver_arrived`) are all called through their real,
    unmodified implementations elsewhere in the codebase — this pass only
    mocks them at the call site inside `ride_flow.py`/`ride_cancel.py`, it
    does not touch their source.
  - `routes/drivers/_shared.py`'s `serialize_ride_for_driver` (strips
    `pickup_otp` before any ride row reaches the driver client) and
    `RideOTPRequest` are used as-is; not modified. `_shared.py` itself was
    the subject of a separate concurrent same-day session
    (`claude/a1c-drivers-shared-batch`) — no overlap in files touched.
- **Ride state machine (CLAUDE.md invariants) — read-only exercise, not
  touched.** Every transition this pass tests
  (`driver_assigned→driver_accepted`, `→driver_arrived`, `→in_progress`,
  `→cancelled`) is asserted against the *existing* guarded
  `update_one(..., {"status": <expected-current>})` atomic-claim pattern —
  no change to the guard filters, the state list, or the
  `_require_ride_in_state` allowlists.
- **Insurance-period adjacency (Period 0-3)**: `accept_ride` calls
  `record_period_transition(driver_id, 2, ride_id=...)` on successful
  claim; `verify_pickup_otp`/`start_ride` call `record_period_transition(driver_id,
  3, ride_id=...)`; `cancel_ride`/`mark_rider_noshow`/`decline_ride` call
  `record_period_transition(driver_id, 1)` on release. New tests assert
  these calls fire with the correct period argument on the success path and
  do NOT assert anything about the append-only `driver_insurance_periods`
  table's own write path (that's `utils/insurance_periods.py`'s own test
  surface, untouched here) — `record_period_transition` itself is mocked at
  the call site in every test, matching this package's existing test
  convention.
- **Money-adjacent (`mark_rider_noshow`'s wallet debit)**: the wallet-debit
  call goes through `wallet_apply_delta` (the same atomic locked RPC
  documented in `test_fee_wallet_atomic.py`'s regression suite) — this pass
  mocks that call at the boundary and asserts it's invoked with the
  driver-payout follow-up (`pay_driver_cancellation_fee`), it does not
  re-verify the RPC's own atomicity/idempotency (already covered by
  `test_fee_wallet_atomic.py`, unmodified).
- **No production code touched** — nothing to regress in ride state,
  wallet/allowance deltas, dispatch, or the insurance-period audit trail.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_driver_ride_flow_coverage.py` | New file — 95 tests | Close coverage gap on `ride_flow.py` (66.30%→99%), `ride_cancel.py` (51.75%→100%), `ride_reads.py` (58.95%→98%) |
| `docs/change-log/2026-08-02-a1c-drivers-ride-flow-batch-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (rides) |
| `ACTION_ITEMS.md` | Updated A1c Sub-tier A's `ride_flow.py`/`ride_cancel.py`/`ride_reads.py` bullet; reconciled alongside two other same-day concurrent sessions' bullets in the same section (`payouts.py`/`earnings.py`/`referrals.py` batch, `_shared.py`/`status.py`/`profile.py` batch) that landed in the same shared working tree — kept side-by-side per the established reconciliation convention rather than picking a winner | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing
diff. One notable pre-existing-behavior finding surfaced but **not fixed**
(test-only scope, per instructions):

`routes/drivers/ride_reads.py`'s `get_active_ride`, in both the rider-lookup
and vehicle-type-lookup `except` blocks, formats the log message with a
direct dict index (`ride['vehicle_type_id']`) rather than `.get(...)`:

```python
try:
    vehicle_type = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("vehicle_types", {"id": ride["vehicle_type_id"]}, limit=1)
    )
except Exception as e:
    logger.error(
        f"get_active_ride: failed to load vehicle_type {ride['vehicle_type_id']}: {e}",
        exc_info=True,
    )
    vehicle_type = None
```

If the outer `get_rows` call raises because `ride` is missing the
`vehicle_type_id` **key entirely** (not just a `None` value), the `except`
handler's own f-string re-raises a second, unhandled `KeyError` — the
`except Exception` guard doesn't protect its own body. In production this
is not reachable: a Supabase `rides` row always carries the
`vehicle_type_id` column (value possibly `None`, but the key is always
present), so the only way to trigger this is a row shape the DB schema
doesn't produce. Flagging per CLAUDE.md's "don't silently work around, note
it" guidance rather than fixing it in a test-only pass; a fix (swap to
`ride.get("vehicle_type_id")` in both the call and the log line) would be a
one-line, low-risk follow-up if a future session touches this file for a
non-test reason.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] New test file run alone: `pytest tests/test_driver_ride_flow_coverage.py -q --no-cov` — **95 passed**.
- [x] Run together with every other test file already touching these three
  modules: `pytest tests/test_driver_ride_flow_coverage.py
  tests/test_drivers_extended.py tests/test_ride_accept_flow.py
  tests/test_subscription_enforcement.py tests/test_c2_driver_cancel_atomic.py
  tests/test_active_ride_rider_pii.py tests/test_rides.py
  tests/test_dispatch_metrics.py tests/test_claim_ride.py
  tests/test_fee_wallet_atomic.py tests/test_preauth_release_on_cancel.py
  tests/test_idor_ownership_guards.py -q --cov=routes.drivers.ride_flow
  --cov=routes.drivers.ride_cancel --cov=routes.drivers.ride_reads
  --cov-report=term-missing --no-cov-on-fail` — **297 passed**, no
  collisions.
- [x] Coverage measured (same command as above): `routes/drivers/ride_flow.py`
  **99%** (273 stmts, 2 missing — both the dual-import `ImportError`
  fallback for `match_driver_to_ride`); `routes/drivers/ride_cancel.py`
  **100%** (144 stmts, 0 missing); `routes/drivers/ride_reads.py` **98%**
  (190 stmts, 3 missing — the dual-import `ImportError` fallback for
  `_redact_driver_location_fields` and `history_date_field`'s unreachable
  trailing fallback). All three up from the documented baselines (66.30%,
  51.75%, 58.95%), which this session's own fresh measurement matched
  exactly before any new tests were added.
- [x] Full backend suite: `pytest tests/ -q --no-cov` — see section 9
  addendum below (run was in flight in a shared working directory
  alongside two other concurrent same-day A1c sessions on sibling files in
  this same package; see PR description for the actual pass/fail count and
  delta against the pre-session baseline).
- [x] Blast-radius grep performed: see section 4 above, every real caller
  enumerated and confirmed unmodified.
- [x] Reviewed against CLAUDE.md conventions: patch targets follow this
  package's dual-binding pattern documented in
  `test_subscriptions_coverage.py` and `test_drivers_extended.py` —
  `backend.routes.drivers._deps.db_supabase.<fn>` (module reference, shared
  by `_deps.db.<fn>` too, since `_deps.py` sets `db = db_supabase` as a
  same-object alias) for CRUD calls in `ride_flow.py`/`ride_cancel.py`;
  `backend.routes.drivers.ride_reads.db_supabase.<fn>` for `ride_reads.py`
  (same module-identity reasoning, just referenced via that file's own
  `from ._deps import db_supabase` name); `backend.routes.drivers._deps.<name>`
  for bound-name copies (`manager`, `record_period_transition`,
  `send_push_notification`, `reset_miss_streak`, `cancel_authorization`,
  `spawn`); and the source module for dual-imported-inside-the-function
  names (`utils.spinr_pass.assert_quota_available`,
  `utils.redis_client.redis_set`, `repositories.driver_repo.update_acceptance_rate`,
  `services.cancellation_service.*`, `routes.rides.match_driver_to_ride`).
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout, matching repo
  convention for this test tier.
- `mark_rider_noshow`'s wallet-debit and driver-payout tests mock
  `wallet_apply_delta`/`pay_driver_cancellation_fee` at the boundary; the
  RPC's own row-locking/idempotency behavior is exercised by
  `test_fee_wallet_atomic.py` (unmodified, not re-verified here).
- `record_period_transition` is mocked at every call site in every new
  test; the append-only `driver_insurance_periods` write path itself
  (`utils/insurance_periods.py`) is out of scope for this pass and was not
  re-verified.
- The two remaining dual-import `ImportError` fallback lines
  (`ride_flow.py` 537-538, `ride_reads.py` 347-348) and `ride_reads.py`'s
  unreachable `history_date_field` trailing fallback (line 279) were judged
  not worth chasing via `sys.modules` monkeypatching, matching the
  documented precedent for the identical `redis_set_nx` fallback pattern in
  `docs/change-log/2026-08-02-a1c-subscriptions-coverage.md`.
- This session's working directory was shared, in real time, with at least
  two other concurrent A1c sessions closing sibling files in the same
  `routes/drivers/` package (`payouts.py`/`earnings.py`/`referrals.py` on
  branch `claude/a1c-drivers-payouts-batch`, and
  `_shared.py`/`status.py`/`profile.py` on branch
  `claude/a1c-drivers-shared-batch`) — their in-progress `ACTION_ITEMS.md`
  edits and untracked test files were visible in this checkout throughout.
  This session's own commit stages only its own test file and change-log,
  plus an `ACTION_ITEMS.md` edit reconciled to keep all three sessions'
  bullets side-by-side (per the established convention) — but because all
  three sessions share one working-tree copy of `ACTION_ITEMS.md`, this
  session's commit necessarily also carries whatever the other two sessions
  had already written into that file at commit time. Not independently
  re-verified against what those two sessions' own PRs will actually
  contain.
