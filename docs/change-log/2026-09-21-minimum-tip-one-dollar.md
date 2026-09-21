# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | agent |
| Surface(s) | backend, rider-app, admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | (uncommitted at write time) |
| Related issue or gap ID | Sub-$0.50 tips silently dropped (ride 0c24901f…, 2026-09-21) |

## 1. Issue / gap identified

A rider could enter any custom tip above $0. On ride `0c24901f…` (2026-09-21) the rider tipped **$0.05**. The tip exceeded the card hold, so the backend tried a separate Stripe charge, which Stripe rejected ("Amount must be at least $0.50 CAD"). The tip was **silently dropped**: the rider wasn't charged, the driver wasn't paid, and the only trace was a Sentry error.

## 2. Root cause

Nothing enforced a minimum tip. The rider app's "Other" box accepts any positive amount, and every server tip entry point accepted `> 0` / `>= 0`. The preset buttons already floor at $1.

## 3. Fix / remediation

The owner decided:
- **Riders who don't tip are unaffected.** A rider may tip **$0** or **at least the minimum**.
- The check only applies when a rider types a custom amount **between $0.01 and the minimum**. The rider then stays on the tip screen with **"Minimum tip is $1.00"** until they change or clear it; the tip is never skipped silently.
- It **ships OFF**, and is turned on after the updated rider app is live (see §8).

**Setting:** `settings.min_tip_amount` (migration 438)
- `numeric(6,2)`, **default 0.00 (off)**. CHECK: `0` **or** `0.50–50`. Below $0.50 would recreate Stripe's minimum-charge failure.
- Read through `get_app_settings` (60 s cache). Exposed to the apps via `GET /api/v1/settings`, formatted like `"0.00"`.
- Editable on admin **Settings → Operations → Tips**. The model validates 0 or ≥ 0.50, 2 decimal places, maximum 50.

**Server** (`utils/tip_policy.enforce_min_tip`):
- Rounds the tip to cents, then rejects 0 < tip < minimum with 400 `"Minimum tip is $X.XX"`.
- $0 returns immediately, without reading the settings.
- Called at every server entry point **before any charge or write**:
  - `POST /rides/{id}/tip`: before the ride is read.
  - `POST /rides/{id}/rate`: before the rating is written.
  - `POST /rides/{id}/process-payment`: in the existing pre-claim tip validation, after the already-paid short-circuits, so replays still answer `already_paid`.
  - `routes/payments._authoritative_ride_charge`, the **Google Pay / PaymentSheet** and create-intent path: before the ride is read or any Stripe object is created.

**Rider app:**
- `ride-completed.tsx`: the message under the tip row; **Pay & Done** and **Google Pay** disabled; guards in `handleSubmit`/`handleGooglePay`.
- The rating is marked sent only when `/rate` succeeds or answers 409 "already rated", so a rejected rating is re-sent, not lost.
- Presets never fall below the minimum.
- `attemptRidePayment` shows a server minimum-tip 400 as its own alert ("Tip too small", with **Edit tip** + **Contact Support**), not the generic Change Card alert.
- The minimum comes from `MinTipAmountContext` (`utils/minTipContext.ts`), loaded from `/settings` in `_layout.tsx`, with a $1 fallback if the settings request fails.

Alternatives considered:
- Round sub-minimum tips up to $1. Rejected by the owner.
- Skip the tip and still charge the fare. Rejected by the owner: "don't move from the screen".
- Hard-code $1. Rejected: CLAUDE.md gate 3 requires a new rule that rejects previously valid input to be switchable.
- Turn it on at deploy. Rejected by the owner, because of the old-app experience below.

## 4. Risk & impact on existing functionality

- **While off (default):** behaviour is identical to today at every entry point. Tested: "a 0 minimum switches the rule off".
- **Once on, previously valid input is rejected:** custom tips of $0.01 up to the minimum.
  - On card rides above the hold, those were already failing at Stripe.
  - Within the hold, or on wallet/corporate rides, they used to be collected; now they're blocked. That's intended.
- **Old rider-app builds once it's on:** a sub-minimum custom tip gets a 400. They show a generic payment error instead of the message, and the old app marks the rating as done even though `/rate` rejected it, so the rating is lost. This is why it ships off; turn it on only after the updated app is broadly adopted.
- **Blast radius:**
  - The check is only called at the 4 entry points above.
  - `GET /settings` gains an additive field; admin `SettingsUpdateRequest` gains an optional field.
  - Rider-app changes are limited to the tip screen, the payment-attempt helper, two tip helpers, and one context.
  - Preset buttons are unchanged when the minimum is 0 or 1.
  - The admin field is on the **Operations** tab; the `dashboard-settings` visual baseline screenshots the default **Integrations** tab, so it's unaffected.
- **Deploy order: apply migration 438 BEFORE the backend.**
  - Without the column, the admin GET still returns `min_tip_amount` (from the schema default), the dashboard sends the whole settings object back, and every admin Settings save fails with PGRST204.
  - The server-side check itself is fine before the migration: its default is off.
  - A new app on an old backend falls back to $1 client-side while the server accepts anything. Harmless.
- **Not fixed here (follow-up):** a $1+ tip can still become a separate charge under $0.50. That happens when the booking hold (an estimate) exceeds the final fare and the card can't be incremented: the extra charge is `total − authorized`, not the tip (`services/payment_service.py` ~2120). Suggested follow-up: when the leftover is under $0.50, release the hold and charge the full total fresh.

## 5. User-experience effect

- **Rider (updated app, rule on):** typing $0.01–$0.99 in "Other" shows "Minimum tip is $1.00" in red under the tip row, with the pay buttons disabled until it's $0 or $1+. No-tip riders and preset taps see no change. If the minimum was changed after the screen loaded, the server's message appears as a "Tip too small" alert with "Edit tip".
- **Driver:** no visible change; tips that do go through are always collectable.
- **Admin:** a new "Tips → Minimum tip (CAD)" field on Settings → Operations; values must be 0 or $0.50+.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/438_settings_min_tip_amount.sql` | `settings.min_tip_amount` (default 0 = off; CHECK 0 or 0.50–50) | Switchable minimum, ships dark |
| `backend/schemas.py` | `AppSettings.min_tip_amount = 0.00` | Default before the migration / when absent |
| `backend/utils/tip_policy.py` | New `enforce_min_tip` (cent-rounded) | One rule for every entry point |
| `backend/routes/rides/payments.py` | Check in `add_tip`; in `process_payment`'s pre-claim validation | Reject before any charge |
| `backend/routes/rides/rating.py` | Check before the rating write | Reject before any write |
| `backend/routes/payments.py` | Check in `_authoritative_ride_charge` | Google Pay / PaymentSheet / create-intent |
| `backend/routes/settings.py` | Public `min_tip_amount` (2-dp string) | App reads the live minimum |
| `backend/routes/admin/settings.py` | `SettingsUpdateRequest.min_tip_amount` (0 or 0.50–50) | Admin control |
| `backend/tests/test_min_tip_policy.py` | New, 38 tests | Rule, all 4 paths before side effects, settings, admin bounds |
| `rider-app/utils/customTipSchema.ts` | `customTipMinimumError` | Testable message rule |
| `rider-app/utils/minTipContext.ts` | New `MinTipAmountContext` (fallback 1) | Screen reads it without importing `_layout` |
| `rider-app/components/tipPresets.ts` | `computeTipOptions(fare, minTip = 1)` | Presets never below the minimum |
| `rider-app/app/_layout.tsx` | Read `min_tip_amount`, provide the context | Live minimum |
| `rider-app/app/ride-completed.tsx` | Message, disabled buttons, guards; rating re-sent unless it succeeded/409 | Rider stays and fixes it; no lost rating |
| `rider-app/utils/attemptRidePayment.ts` | `MIN_TIP_ALERT` for the server's minimum-tip 400 | Real reason + Edit tip, not Change Card |
| `rider-app/__tests__/customTipMinimum.test.ts`, `tipPresets.test.ts` | New and extended | Message rule, presets, payment alert |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Tips card (Operations tab) | Edit without a release |

## 7. Before / after

```python
# Before: every entry point accepted any positive tip.
# After: enforce_min_tip(tip) before any side effect:
#   0 < round(tip, 2) < settings.min_tip_amount  ->  400 "Minimum tip is $1.00"
#   (settings.min_tip_amount = 0 at deploy -> no change until an admin sets it)
```

```tsx
// Before: "Other" accepted $0.05 and Pay & Done charged it (Stripe then dropped it).
// After (rule on): "Minimum tip is $1.00" under the tip row; Pay & Done / Google Pay disabled.
```

## 8. Rollout & rollback

**Rollout:**
1. Apply migration 438. The rule is **off**.
2. Deploy the backend and admin dashboard.
3. Release the rider app update.
4. Once it's broadly adopted, set **Settings → Operations → Tips → Minimum tip = 1.00**, or run `UPDATE settings SET min_tip_amount = 1.00 WHERE id = 'app_settings';`. It takes effect within ~60 s.

**Rollback:**
- **Preferred, no deploy:** set it back to **0**.
- **Full:** revert the PR first, then optionally `ALTER TABLE settings DROP COLUMN IF EXISTS min_tip_amount;`. Never drop the column while this code is live, or admin saves will fail.

## 9. Verification performed

- **Backend:** `tests/test_min_tip_policy.py`, 38 passed.
  - $0 allowed without a settings read; $0.01–$0.99 → 400 with the message; ≥ minimum allowed.
  - Cent rounding: $0.995 allowed, $0.994 rejected. Configurable minimum; 0 = off.
  - `/tip` rejects before reading the ride.
  - `process-payment` rejects before any claim or charge, and a replay on a paid ride still answers `already_paid`.
  - `/rate` rejects before any write.
  - payment-sheet and create-intent reject before reading the ride or calling Stripe.
  - Public settings formatting; admin bounds (0 or 0.50–50; 0.01/0.25/0.49 rejected).
- **Red checks:** with the check made a no-op, the 4 endpoint tests fail. With it removed from `_authoritative_ride_charge`, the 2 Google Pay tests fail.
- **Migration 438 on a real PostgreSQL 17** (a throwaway cluster):
  - applies cleanly **twice**
  - the existing row gets 0.00
  - the CHECK accepts 0/0.50/1.00/50 and rejects 0.25/50.01
- **Broad backend run** (every test file touching tips, payments or rating, 76 files): 2296 passed. The failures are environment or pre-existing, and also fail without this change:
  - 3 compliance-report tests: `openpyxl`/`docx` not installed locally
  - `test_p0_ship_blockers::test_cancel_publishes_to_rider_channel`: fails the same on a `main`-based tree
- **A test-isolation bug in my own tests, found and fixed:** a doubled patch, undone in the wrong order, leaked a mock `get_ride` into later tests (`test_rides::test_get_ride_by_id`, `test_webhooks_coverage_gap::…raises_500`). Fixed with `ExitStack`; those tests now pass when run after mine.
- **Rider app:**
  - jest: tip helpers, presets and payment alert (23), `rideCompletedScreen` plus 5 neighbouring screen suites — all pass
  - `tsc --noEmit` clean
  - eslint: no new warnings versus `main` (ride-completed 98 = 98, `_layout` 2 = 2)
- **Admin dashboard:**
  - `tsc --noEmit` clean
  - eslint: 0 errors, no new warnings (5 = 5)
  - **`npm run build` (real production build) succeeded**
- **`spinr-money-auditor` review:** FIX FIRST. Its findings are addressed here:
  - the Google Pay path is guarded
  - the rollout ships off
  - the rollback is "set to 0"
  - the rating is no longer lost; the min-tip 400 gets its own alert
  - cent rounding is consistent; the admin minimum is 0 or ≥ $0.50
  - process-payment replay order is preserved

  The hold-remainder case (§4) is a follow-up.

## 10. What was NOT verified

- **Not tried on a device.** rider-app has no visual-regression tooling, so the red message, the disabled buttons and the "Tip too small" alert were reasoned about, not screenshotted.
- **Not run against a real Supabase or Stripe.** Endpoint tests use mocks. Migration 438 was verified on a local PostgreSQL 17, not applied to production.
- **Older installed rider apps' behaviour once the rule is on** was reasoned about from code (generic error, lost rating), not exercised. That is why it ships off.
