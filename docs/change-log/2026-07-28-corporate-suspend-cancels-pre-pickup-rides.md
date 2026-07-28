# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session), reviewed with @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-module-review-6eh65j`, commits 4849e89, 606bfea, 9191bf5, 012dc2b |
| Related issue or gap ID | Corporate module review — gap #1 ("mid-ride company deactivation") |

## 1. Issue / gap identified

Suspending or closing a corporate account had **zero effect** on rides already created for that company. A ride still in `searching`/`driver_assigned`/`driver_accepted`/`driver_arrived` when the company was suspended continued to dispatch and complete normally, billed to the now-suspended company, with no record that anything unusual happened.

## 2. Root cause

`routes/corporate_accounts.py::change_company_status` (the `POST /admin/corporate-accounts/{id}/status` handler) only ever updated `corporate_accounts.status` and disabled auto-topup on suspend/close — it never queried `rides` for the company at all. Separately, `services/corporate_policy_service.py::evaluate_policy` never included company status as a rule, so `payment_service.py::settle_corporate`'s completion-time policy check couldn't have caught it either. The gap was a missing integration between the corporate-account lifecycle and the ride lifecycle, not a bug in either one individually.

## 3. Fix / remediation

Two distinct behaviors, split by ride phase (per the ride state machine's "never cancel after trip start" rule):

- **Pre-pickup rides** (`searching`, `driver_assigned`, `driver_accepted`, `driver_arrived`): new `services/corporate_suspension_service.py::cancel_pre_pickup_rides_for_company` auto-cancels each one when the owning company transitions to `suspended`/`closed` — no cancellation fee (system/company-side event, not rider/driver fault), releases any assigned driver back to available + Period 1, notifies rider/driver over WebSocket + push, and SMS-notifies guest bookings. Wired into `change_company_status` right after the status write.
- **In-progress rides**: grandfathered — continue to bill the company normally at settlement (unchanged). `payment_service.py::settle_corporate` now reads the company's current status at completion and, if suspended/closed, appends a `company_inactive_during_ride` flag to the `corporate_policy_evaluations` audit row. This is audit-only: it never blocks the payment or changes the amount charged.
- The whole pre-pickup-cancellation behavior is gated behind a new `app_settings.corporate_suspend_cancels_pre_pickup_rides` flag (default `True`) so it can be switched off from the admin dashboard without a redeploy if it misbehaves in production.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the corporate-account status-transition endpoint and corporate ride settlement.** Grepped for other callers:
  - `corporate_accounts.py::change_company_status` — the only writer of `corporate_accounts.status`; no other route flips this field.
  - `payment_service.py::settle_corporate` — the only corporate settlement path; consumer (non-corporate) rides use `settle_card`/`settle_wallet`, untouched.
  - `corporate_policy_service.evaluate_policy` — also called from booking-time `evaluate_policy_for_ride`, which does **not** pass a `company_status` context key, so the new `company_inactive_during_ride` flag can only ever appear from the completion-time call in `settle_corporate`; booking-time evaluation is unaffected.
- New DB reads: `get_rows("rides", ...)` and `update_one("rides", ...)` inside the new service, and one extra `get_corporate_account_by_id` read in `settle_corporate` per corporate ride completion — small, non-loop, request-scoped reads, no measurable SLA risk.
- Interaction with background loops: none of the 16 startup loops call this code path; `corporate_autotopup_loop`/`corporate_low_balance_loop` are unaffected (they already skip suspended companies via existing wallet-config checks).
- Ride state machine: the cancellation uses the same atomic `$in`-status-guard claim pattern as `cancel_ride_rider`/`ride_search_timeout`, so a ride that raced into `in_progress` between the DB read and the write is correctly left alone (claim returns `None`, ride skipped) — verified by `test_race_ride_left_pre_trip_state_is_skipped`.
- Money impact: pre-pickup cancellation charges no fee to rider or driver (this is a company-caused, not user-caused, cancellation). In-progress rides bill exactly as before — no change to amounts charged.

## 5. User-experience effect

- **Rider**: if mid-search or waiting for a driver when their company account is suspended, their ride is now cancelled automatically with a clear reason ("Your company account was suspended before this ride started.") instead of silently continuing to dispatch against a suspended account. This is a new, user-visible interruption — previously the ride just proceeded as if nothing happened.
- **Driver**: if already assigned/en route to a now-cancelled ride, released back to available immediately and notified, same as any other system cancellation.
- **Corporate admin**: none directly — this happens automatically on the existing suspend/close action already available in the admin dashboard's corporate-accounts page. The admin audit log entry for the status change now includes a `pre_pickup_rides_cancelled` count.
- **Internal admin/finance**: `corporate_policy_evaluations` now surfaces `company_inactive_during_ride` for any in-progress ride that completed after its company was suspended/closed — new visibility, no UI change (existing endpoint/table).
- Not visible mid-session to anyone whose ride is already `in_progress` — that path is intentionally unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/corporate_suspension_service.py` | New file: `cancel_pre_pickup_rides_for_company` + per-ride cancellation helper | Core fix — auto-cancel pre-pickup rides on company suspend/close |
| `backend/routes/corporate_accounts.py` | `change_company_status` calls the new service on suspend/close, gated by the new flag, logs cancelled count in audit entry | Wiring + rollback flag + audit visibility |
| `backend/services/payment_service.py` | `settle_corporate` reads company status at completion and flags `company_inactive_during_ride` in the audit row (non-blocking) | Audit visibility for grandfathered in-progress rides |
| `backend/schemas.py` | New `AppSettings.corporate_suspend_cancels_pre_pickup_rides: bool = True` | No-redeploy rollback switch |
| `backend/tests/test_corporate_suspension_service.py` | New: 5 unit tests for the cancellation service | Regression coverage |
| `backend/tests/test_corporate_status.py` | +3 tests: cancellation triggered on suspend, skipped on reactivate, skipped when flag off | Regression coverage |
| `backend/tests/test_corporate_settle_suspended_audit_flag.py` | New: 3 tests for the completion-time audit flag | Regression coverage |

## 7. Before / after

```python
# Before — routes/corporate_accounts.py::change_company_status
if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
    wallet = await get_corporate_wallet_by_company(normalized_id)
    if wallet and wallet.get("auto_topup_enabled"):
        await update_corporate_wallet_config(wallet_id=wallet["id"], patch={"auto_topup_enabled": False})
# ... nothing else happens to existing rides for this company
```

```python
# After
if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
    wallet = await get_corporate_wallet_by_company(normalized_id)
    if wallet and wallet.get("auto_topup_enabled"):
        await update_corporate_wallet_config(wallet_id=wallet["id"], patch={"auto_topup_enabled": False})

cancelled_rides = 0
if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
    settings = await get_app_settings()
    if settings.get("corporate_suspend_cancels_pre_pickup_rides", True):
        cancelled_rides = await cancel_pre_pickup_rides_for_company(normalized_id)
```

## 8. Rollback plan

- **Immediate, no redeploy**: set `app_settings.corporate_suspend_cancels_pre_pickup_rides = false` via the admin dashboard settings — takes effect within 60 seconds (existing settings cache TTL). This fully disables the new cancellation behavior; the endpoint reverts to its pre-fix behavior (status flip + auto-topup disable only).
- The completion-time `company_inactive_during_ride` audit flag is additive-only (a new value in an existing `failed_rules` array on a new/existing audit row) and has no user-facing or payment-blocking effect, so no rollback lever is needed for it — worst case it's a harmless extra audit entry.
- No data migration involved; no Stripe charges or wallet deltas are altered by this change (fee is explicitly 0 for the new cancellation path), so there is nothing to reverse on already-applied live data.

## 9. Verification performed

- [x] Automated tests run (unit): `test_corporate_suspension_service.py` (5), `test_corporate_status.py` (+3 new, 8 total), `test_corporate_settle_suspended_audit_flag.py` (3) — 16 new/updated tests, all passing.
- [x] Regression check: existing corporate payment/booking suite (`test_allowance_cap_fallback.py`, `test_allowance_rpc_sign_contract.py`, `test_company_guest_booking.py`, `test_corporate_ride_payment.py`, `test_guest_auto_settle.py`) — 36 tests, all passing, no regressions.
- [ ] Manual repro steps followed in staging — **not performed**; no staging environment available in this session.
- [x] Blast-radius grep performed: searched for other callers of `corporate_accounts.status` writes, other invokers of `evaluate_policy`/`evaluate_policy_for_ride`, and other corporate settlement paths (see §4).
- [x] Reviewed against relevant CLAUDE.md conventions: ride state machine (atomic `$in` claim, never cancel after trip start), money arithmetic (Decimal via existing `_d`/`_round` helpers, no float), do-not-silently-swallow-errors (per-ride cancellation failures are logged with `exc_info=True` and don't block the rest of the batch or the status-transition response).
- [x] Feature-flagged (`corporate_suspend_cancels_pre_pickup_rides`, default on) per gate #3.

## What was NOT verified

- Not tested against a live/staging Supabase instance — only against `mock_supabase_client`-style `AsyncMock`/`patch` fixtures in unit tests. No integration-tier test (real DB) was added.
- No end-to-end manual repro (create a real ride, suspend the company via the admin dashboard UI, observe the rider/driver app receive the cancellation) was performed — this session has no running app/staging environment to exercise the rider/driver WebSocket clients against.
- Push notification delivery (`send_push_notification`) is mocked in tests, not verified against real FCM/Expo delivery.
- No visual/UI check of the admin dashboard's corporate-accounts audit log rendering of the new `pre_pickup_rides_cancelled` field — the admin UI wasn't touched, so this assumes the existing generic audit-log JSON viewer renders any new key without changes, which was not screenshotted.
- Guest-booking SMS notification (`notify_guest_cancelled`) on this new path is called but not verified against a real Twilio send.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flip `app_settings` flag, verify via a suspend test that no cancellation occurs)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)
