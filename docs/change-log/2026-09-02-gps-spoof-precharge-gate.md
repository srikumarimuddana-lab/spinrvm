# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | backend, admin-dashboard, rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | Item #4 of the stage 7/8 (insurance-period / fare-billing) GPS-to-billing audit, 2026-09-02 |

## 1. Issue / gap identified

`backend/utils/route_validation.py`'s GPS-spoof detector (road-matches a completed trip's GPS trace against OSRM/Google Roads and flags a trip whose points don't align with real roads) existed, was tested, but was **completely unwired in production** — its only caller, `_validate_ride_route` in `routes/drivers/_shared.py`, was itself dead code, never spawned anywhere. `process_payment` (the endpoint that actually charges the rider's card) had zero spoof gate of any kind, before or after the charge.

## 2. Root cause

The detector and its wrapper were written for an earlier "fire-and-forget `spawn()` at ride completion" architecture that this codebase later moved away from in favor of a durable, replay-safe queue (`route_finalizer.py`'s background loop, `mark_route_pending()` + `finalize_route()`) — the same architecture item #2/#3 of this audit already extended earlier today. The old wiring was removed (a regression test, `test_ride_completion_location.py`, explicitly asserts `ride_complete.py` never contains `spawn(_validate_ride_route(...)`) but the detector itself was never reconnected to the new pipeline, so it simply stopped running.

## 3. Fix / remediation

Four pieces, landed as four commits on this branch:

1. **Detector reconnected** (`utils/route_finalizer.py`): `finalize_route()` now calls `validate_trip_route()` on the same breadcrumbs it already loads, storing the verdict at `ride_routes.route_quality.gps_route_validation`. Flag-gated (`gps_spoof_charge_gate_enabled`, `app_settings`, default OFF) so a dark rollout costs zero extra OSRM/Google calls until enabled.
2. **Pre-charge gate** (`routes/rides/payments.py::process_payment`): when the flag is on, reads that verdict; if `verdict == "likely_spoofed"` and `deviation_pct` exceeds a configurable threshold (`gps_spoof_deviation_hold_threshold_pct`, default 40.0), the ride is held (`payment_status = "held_for_review"`) instead of charged. Below the threshold, flag off, or the verdict hasn't landed yet (route_finalizer.py runs on a ~15s cadence and may not have reached this ride yet) — **fails open**, charging exactly as today. This mirrors how a card network scores every transaction instantly and only holds the ones that trip a risk threshold, rather than holding every transaction or holding none.
3. **Admin surface** (`routes/admin/rides.py`): `GET /rides/held-for-review` lists held rides with their GPS verdict; `POST /rides/{id}/held-for-review/release` clears the verdict (so the rider's next payment attempt charges normally) and reopens `payment_status`; `POST /rides/{id}/held-for-review/waive` comps the ride (`payment_status = "waived_admin"`, the same terminal state `admin_complete_ride` already uses) with no charge at all. Both actions are audit-logged via the existing `log_admin_action`. Already gated by the existing `require_module("rides")` router-level RBAC — no new auth code needed.
4. **Frontend surfaces**: `admin-dashboard`'s ride-detail payment badge gives `held_for_review` its own color instead of falling into the generic amber "pending" bucket; `rider-app`'s `attemptRidePayment.ts` recognizes the new `{ success: false, held_for_review: true }` response shape and returns a dedicated informational alert (no Change Card/Retry buttons — neither can resolve a hold); `ride-completed.tsx` shows that alert then navigates the rider home (staying on the back-button-blocked screen would serve no purpose — nothing the rider does resolves a hold); `ride-details.tsx`'s payment label shows "Under review" instead of a misleading generic "Pending".

## 4. Risk & impact on existing functionality

- **Blast radius, backend:** `finalize_route()` gains one new flag-gated network call — no other behavior in that function changed (verified: the full existing `route_finalizer`/`route_reconstruction` test suite passes unmodified). `process_payment` gains one new flag-gated read + an early-return branch inserted *before* the existing atomic claim — every existing branch (idempotency, invoice guard, wallet re-drive, tip validation) is unchanged and unconditionally reached when the flag is off (verified: the full existing payment-guard/atomic-settle/corporate-payment suite — 71 tests — passes unmodified). `rides.payment_status` is a plain `TEXT` column with no CHECK constraint or enum type (confirmed by migration search) and the only DB trigger on it (`399_transactional_outbox.sql`) fires solely on `= 'paid'` — a new string value is safe there with **no migration needed**.
- **Blast radius, frontends:** grepped every `payment_status` consumer across `admin-dashboard` (4 files) and `rider-app` (5 files) before writing code. All handle an unrecognized value gracefully by construction (raw string print or an inclusive ternary fallback) — none would have broken or shown blank for `held_for_review` even without this change; the frontend edits are explicit-handling improvements (correct copy/color), not bug fixes for a crash.
- **Fare/billing is not at risk from this change specifically.** Nothing is charged differently — a held ride is charged $0 extra and $0 less than before; it's charged *later*, after an admin looks at it, or never (if waived). The distance/fare-calculation pipeline (already hardened in items #1-#3 of this audit) is untouched.
- **New failure mode to be aware of:** a false positive (a legitimate trip whose GPS trace looks spoofed — e.g. a long tunnel, severe multipath in a dense downtown core) now gets **held instead of charged** when the flag is on. This is a deliberate trade — an unreviewed spoofed trip costs the platform a stolen fare; a false-positive hold costs a rider a delayed receipt, recoverable by an admin in one click (`release`). The threshold (40% default) and the flag itself exist specifically so this can be tuned or turned off without a redeploy if false positives prove too frequent in practice — **not yet validated against real traffic, since it ships dark.**

## 5. User-experience effect

- **Rider-facing, only when the flag is enabled and a trip trips the threshold (expected to be rare).** Instead of an immediate charge confirmation, the rider sees "Receipt pending verification — we're verifying your trip before finalizing your receipt. We'll notify you once it's ready," and is returned to the home screen. Their ride-details view for that trip shows "Under review" instead of "Pending". No other rider ever sees any change — the flag defaults off, and even when on, the fast/normal charge path is byte-for-byte unchanged.
- **Admin-facing.** A new `Rides → Held for Review`-shaped queue (via the two new endpoints; no dedicated admin-dashboard page was built for it in this pass — see §10) with two one-click actions.
- **Not visible mid-session** to anyone already using the app — this only affects the payment step at the very end of a completed ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/route_finalizer.py` | `finalize_route()` calls `validate_trip_route()` behind a new flag, stores the verdict on `route_quality.gps_route_validation` | Reconnects the dead detector to the durable pipeline |
| `backend/routes/rides/payments.py` | `process_payment` reads the verdict and holds the charge past a configurable threshold; `held_for_review` short-circuit added alongside the existing `paid` one | The actual pre-charge gate |
| `backend/routes/admin/rides.py` | 3 new endpoints: list, release, waive | Admin visibility/action |
| `admin-dashboard/.../ride-detail-modal.tsx` | Payment badge gives `held_for_review` its own color | Explicit handling, not generic-pending fallthrough |
| `rider-app/utils/attemptRidePayment.ts` | New `held_for_review` response case + `HELD_FOR_REVIEW_ALERT` | Rider-facing informational alert instead of a misleading generic error |
| `rider-app/app/ride-completed.tsx` | Held response now shown + navigates home; initial-load auto-dismiss extended to `held_for_review` | Don't trap the rider on a back-blocked screen with nothing to retry |
| `rider-app/app/ride-details.tsx` | New `paymentStatusLabel()` helper, "Under review" label | Explicit handling, not misleading "Pending" |
| `backend/tests/test_route_finalizer.py` | 2 new tests (flag on/off) | Regression coverage |
| `backend/tests/test_gps_spoof_payment_gate.py` | New file, 7 tests | Regression coverage for the gate itself |
| `backend/tests/test_admin_gps_spoof_holds.py` | New file, 6 tests | Regression coverage for the admin endpoints |
| `rider-app/utils/__tests__/attemptRidePayment.test.ts` | 2 new tests | Regression coverage |
| `rider-app/__tests__/rideCompletedScreen.test.tsx` | 2 new tests + mock update | Regression coverage |
| `rider-app/__tests__/rideDetailsScreen.test.tsx` | 1 new test | Regression coverage |

## 7. Before / after

```python
# Before (payments.py::process_payment) — no spoof gate of any kind existed.
if ride.get("stripe_invoice_id"):
    raise HTTPException(...)
# straight to tip validation + atomic claim + charge
```
```python
# After
if ride.get("stripe_invoice_id"):
    raise HTTPException(...)

if _app_settings.get("gps_spoof_charge_gate_enabled", False):
    ...
    if _gps_validation.get("verdict") == "likely_spoofed" and _deviation_pct > _deviation_threshold:
        await _deps.db_supabase.update_one(
            "rides", {"id": ride_id, "payment_status": _pstatus}, {"payment_status": "held_for_review"}
        )
        return {"success": False, "held_for_review": True, "message": "..."}
# unchanged: tip validation + atomic claim + charge
```

## 8. Rollback plan

Flip `gps_spoof_charge_gate_enabled` off in `app_settings` (no redeploy) — every ride, including any currently `held_for_review`, immediately stops being newly held (existing held rides need an admin `release` or `waive` action, or a one-off `UPDATE rides SET payment_status = 'pending' WHERE payment_status = 'held_for_review'` to bulk-clear if the flag is turned off as an emergency measure). `git revert` is safe for the code itself — no migration, no data mutation the revert would need to undo beyond that flag/status combination.

## 9. Verification performed

- [x] Automated tests: backend — `pytest` across `test_route_finalizer.py`, `test_route_finalizer_loop.py`, `test_route_finalizer_recompute.py`, `test_e2e_route_tail_recovery.py`, `test_gps_spoof_payment_gate.py`, `test_admin_gps_spoof_holds.py`, `test_e2e_payment_guard.py`, `test_atomic_settle.py`, `test_admin_extended.py` — **131 passed**. `ruff check` clean on every changed backend file.
- [x] rider-app: full suite — **1947/1947 passing** (139 suites, includes 5 new tests across 3 files). `npx tsc --noEmit` and `npx eslint` clean on every changed file.
- [x] admin-dashboard: full suite via `npm test` (vitest) — **561/561 passing** (59 files). `npx tsc --noEmit` clean.
- [x] Blast-radius grep performed for every `payment_status` consumer in both frontends before writing any frontend code (documented in §4).
- [x] Reviewed against CLAUDE.md's money-path/release-gate conventions: additive-only column value (no migration), flag-gated dark launch, fail-open on ambiguity, audit-logged admin actions, Decimal arithmetic untouched (no money math in this change at all — it only gates *whether* to call the existing, unmodified charge path).

## 10. What was NOT verified

- **Not tested against real GPS traffic or real OSRM/Google Roads responses** — `validate_trip_route` itself was already tested prior to this change; this change only tests the wiring around it (mocked verdicts in, held/not-held out).
- **No dedicated admin-dashboard UI page for the held-for-review queue was built** in this pass — the two admin actions (release/waive) and the list endpoint exist and are tested at the API level, but an admin currently needs to call them directly (or a future small UI page) rather than clicking through a dedicated dashboard screen. Flagged as a natural follow-up, not silently dropped.
- **The 40% deviation threshold and 15s finalizer cadence were not tuned against production data** — both are configurable via `app_settings` specifically so they can be adjusted once real dark-launch data is available, rather than being treated as final.
- **No load/performance testing** of the added `ride_routes` read inside `process_payment` — it's a single indexed-by-`ride_id` row read, gated entirely behind the (default-off) flag, so it adds zero cost to the current production path, but this wasn't independently benchmarked.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (a single flag flip; no migration to reverse).
- [x] Blast radius is stated, not assumed (every `payment_status` consumer across 3 surfaces was grepped and read before any code was written).
- [x] No silent behavior change to an already-shipped flow — the fast/normal charge path is unconditionally reached and unchanged when the flag is off (verified by the full pre-existing payment test suite passing unmodified); the new behavior is additive and dark by default.
