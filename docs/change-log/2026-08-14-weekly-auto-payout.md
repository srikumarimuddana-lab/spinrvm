# Change Impact & Risk Log — Weekly auto-payouts replace driver-initiated cashout

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | srikumarimuddana-lab (via Claude Code) |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | payments |
| PR / commit link | #3925 (`claude/stripe-payouts-schedule-f4v9o8`) |
| Related issue or gap ID | — (product decision, this session) |

## 1. Issue / gap identified

Driver payouts were driver-initiated ("Cash out" in the driver app); Spinr had no control over payout timing, no platform-side ledger of payout runs, and no way for ops to disable fee-bearing instant payouts in a specific market.

## 2. Root cause

The original payout design exposed `POST /drivers/payouts` for ad-hoc manual cashouts. There is no platform-scheduled transfer job, and Stripe's own payout schedule does not apply because Spinr initiates `stripe.Transfer`s from the platform account — so payout timing was entirely in the driver's hands.

## 3. Fix / remediation

Spinr now runs a weekly auto-payout batch every Sunday (UTC): every driver with a Stripe Connect account and `payable_balance >= $10` receives a `stripe.Transfer`; sub-$10 balances carry forward to the next week. Standard manual cashout is removed (`POST /drivers/payouts` returns 410; UI removed). Instant payouts (1.5% fee) remain, now gated by a per-service-area kill switch (`service_areas.instant_payout_enabled`, admin-toggleable). A new `auto_payout_batches` table records each weekly run. An `app_settings.auto_payout_enabled=false` ops flag disables the batch without redeploy.

## 4. Risk & impact on existing functionality

- `payouts` table gains `payout_type='auto'` rows (status `reserved` → `completed`/`failed`). Consumers grepped: `routes/drivers/earnings.py` balance (deducts them — correct, they are real money-out), driver payout-history endpoint (lists them; `bank_name` shows "Auto Payout"), driver statements/T4A (`utils/driver_statement.py` sums payouts by driver — auto rows behave like manual ones), admin payout views, `stripe_payout_sync` (writes `stripe_sync` type only — disjoint).
- `POST /drivers/payouts` now 410s — **old driver-app builds still show the Request button and get an error toast** until a new Expo EAS build ships and is adopted. Visible mid-session regression window for drivers on old builds.
- Background loops go 18 → 19 (`auto_payout`, hourly wake, Sunday-gated, watchdog-registered). Runs on every replica; guarded by Redis leader lock + `week_key` unique index + per-driver deterministic payout IDs + Stripe idempotency keys.
- Instant payout requests gain one extra `service_areas` read (kill-switch check).
- `service_areas` gains `instant_payout_enabled` (DEFAULT TRUE) — additive, no behavior change until toggled.
- The payable-balance formula is **duplicated** in `utils/auto_payout.py` (`_compute_payable_balance`) — any future change to `routes/drivers/earnings.py`'s composition must be mirrored there or the batch pays a different amount than the app displays. Known drift risk, under review by the PR reviewer fleet.
- Blast radius: cross-surface (backend + driver-app), payments domain.

## 5. User-experience effect

- **Drivers**: manual "Cash out" removed; payout screen shows an "Every Sunday" schedule card and auto-payout explainer; next-payout label changed Friday → Sunday. On old builds, tapping the removed flow's button errors (410 with an explanatory message) until the new build ships. Instant payout remains, and in areas where ops disable it the endpoint returns 403 with a clear message.
- **Riders / corporate admins**: no change.
- **Internal admins**: can toggle `instant_payout_enabled` per service area via the existing service-area update endpoint.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/314_auto_payout_and_instant_kill_switch.sql` | New `auto_payout_batches` table; `instant_payout_enabled` column on `service_areas` | Batch ledger + per-area kill switch |
| `backend/utils/auto_payout.py` | New weekly auto-payout service + hourly Sunday loop + ops flag | Platform-controlled payouts |
| `backend/core/lifespan.py` | Spawn `auto_payout` loop; watchdog entry | Run on schedule, replay-safe |
| `backend/routes/drivers/payouts.py` | `POST /payouts` → 410 (legacy handler preserved off-route); `_require_instant_payout_enabled` gate | Remove manual cashout; kill switch |
| `backend/routes/admin/service_areas.py` | `instant_payout_enabled` in update model + payload | Ops toggle |
| `backend/routes/drivers/earnings.py` | `instant_payout_available` in `GET /drivers/balance` | Client can hide instant option |
| `driver-app/app/driver/payout.tsx` | Removed cashout UI; Sunday schedule; new copy | Match new model |
| `driver-app/store/driverStore.ts` | Removed `requestPayout` action | Dead code (endpoint 410s) |
| `backend/tests/test_auto_payout.py` | 15 tests | Coverage |

## 7. Before / after

```
# Before — driver-initiated
Driver taps "Request" → POST /drivers/payouts {amount}
  → payout row (reserved) → stripe.Transfer immediately → completed
```

```
# After — platform-initiated
POST /drivers/payouts → 410 "Manual payouts have been replaced by automatic weekly payouts..."
Sunday (UTC), hourly loop → run_weekly_auto_payout()
  → one batch row per ISO week (unique week_key)
  → per eligible driver (balance >= $10, has stripe_account_id):
      payout row id "auto-{driver_id}-{week_key}" (reserved)
      → stripe.Transfer (idempotency_key "auto-payout-{driver_id}-{week_key}")
      → completed / failed
```

## 8. Rollback plan

- **Weekly batch off, no redeploy**: set `app_settings.auto_payout_enabled = false` (admin dashboard settings). The loop then skips before writing any batch row or transfer.
- **Instant payouts per area, no redeploy**: `service_areas.instant_payout_enabled` toggle.
- **Restore manual cashout**: requires a redeploy (`_STANDARD_CASHOUT_DISABLED = False` in `backend/routes/drivers/payouts.py` — one-line revert; the original handler is preserved). Accepted: the removal is the product decision itself.
- **Schema**: rollback SQL in migration 314's header comment (`DROP TABLE auto_payout_batches; ALTER TABLE service_areas DROP COLUMN instant_payout_enabled`).
- **Money already moved** by a completed batch is NOT undone by any flag — reversal would be per-transfer `stripe.Transfer` reversals, driver by driver; payout rows carry `stripe_transfer_id` for exactly this.

## 9. Verification performed

- [x] Automated tests: 15 unit tests in `backend/tests/test_auto_payout.py`, all passing locally (mocked Supabase + Stripe): balance math incl. exclusions, batch-already-completed skip, $10 threshold, no-Stripe-account skip, reserve-then-transfer ordering, kill-switch 403/allow/no-area paths, 410 endpoint, disabled-via-settings skip.
- [ ] Manual repro in staging — **not done** (see below).
- [x] Blast-radius greps: `requestPayout` (driver-app), `payout_type` consumers (backend), `_WATCHDOG_LOOP_NAMES`, instant-payout gate callers, `instant_payout_enabled` readers.
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money, background-task replay safety, dual-import pattern, app_settings flag pattern, error-surfacing rules.
- [x] Feature-flagged: batch has `auto_payout_enabled` ops flag; instant payout has per-area DB toggle.
- driver-app: `npx tsc --noEmit` clean. **No production build was run** (`npm run build` / EAS export) — stated explicitly per policy; tsc alone is not equivalent.

**What was NOT verified**
- No live/staging Stripe run — the Transfer path is exercised only against mocks; platform-balance sufficiency on a real batch day is untested.
- No test against live Supabase (unique-index and duplicate-key behavior simulated via mocked errors).
- Multi-replica concurrency exercised only via the duplicate-batch-insert skip path, not with real Redis and two processes.
- Old-app-version 410 UX not exercised on a device.
- No visual-regression tooling exists in this repo — driver-app UI changes were reasoned about, not screenshotted (standing gap, see ACTION_ITEMS.md).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (settings flag + area toggle + migration SQL)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — the UX field above covers the removed cashout flow and the old-build error window
