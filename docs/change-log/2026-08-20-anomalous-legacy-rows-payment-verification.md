# 7-anomalous-row disposition — payment verification (follow-up to the cancelled/failed booking import)

## 1. Issue/gap identified

`docs/change-log/2026-08-20-legacy-cancelled-failed-booking-import.md` found 7 legacy `bookings.csv`
rows with `booking_status='failed'` but `start_ride_at` AND `complete_delivery_at` both populated —
structurally indistinguishable from a completed trip. That task explicitly did not resolve the
disposition ("needs a future session with Stripe/live-DB context") and `ACTION_ITEMS.md` A41 tracked
it as blocked on live access this session does not have.

## 2. Root cause / investigation

The blocker as stated was real for *Spinr's* live Stripe/Supabase — this session has neither. But the
**old app's own MongoDB export**, already cached in this session's scratchpad
(`mongo_extract/Mongo/*.csv`) from the original migration audit, includes `payments.csv` (372 rows,
keyed `booking_id` → `bookings._id`, carrying a `status` field) — a collection nobody had cross-
referenced against the 7 anomalous rows yet. That closes the gap without needing anything live:

- **Dataset-wide correlation (all 1,210 bookings, not just the 7):** `payments.csv` presence tracks
  `booking_status` almost perfectly — 257/271 (94.8%) `completed` bookings have a matching payment row
  (`status='completed'`, 100% of matches); **0/225 `failed` bookings have any payment row at all**
  (0.0%); 7/712 `cancelled` bookings have one (a separate, small, pre-existing discrepancy, not
  addressed here — out of scope for this note).
- **The 7 anomalous rows specifically: 0/7 have a `payments.csv` row.** Every one of the 7
  `booking_id`s was checked directly against `payments.csv`'s `booking_id` column — no match, zero
  exceptions.
- **Corroborating signal:** 5 of the 7 anomalous rows share one `customer_id`
  (`69fb4abb173f91297055ab16`) whose `customers.csv` record carries `block_reason: "Card issue."` —
  114 total bookings for this customer, only 4 completed (67 cancelled, 43 failed). The 6th/7th rows'
  customer (`69ebeb9e468f2ceebf146c3e`) has no `block_reason` set but the same skewed pattern (52
  bookings: 37 cancelled, 10 failed, 5 completed) — consistent with a chronically-failing payment
  method even without an explicit block flag.
- **A separate, independent reason not to trust the dollar figures even if payment had settled:** 2 of
  the 7 rows (`CB6694656`, `CB1712640`) have `you_earn` *greater than* `total_amount` — a driver
  earning more than the full amount charged to the rider, which is not possible under this app's fare
  model (driver earnings are computed as total minus commission on every other row checked). Whatever
  produced these two rows' numbers is internally inconsistent, independent of the payment question.
- `payment_status` (a column on `bookings.csv` itself, distinct from the `payments.csv` collection) is
  blank for 270/271 `completed` rows too — it's an unpopulated field across this export generally, not
  a usable signal either way; this note does not rely on it.

**Conclusion:** the trips almost certainly happened (real driver assignment via `declined_bookings.csv`
showing an `accepted` driver for all 7; real GPS/timestamps), but **no payment was ever collected for
any of the 7** — `booking_status='failed'` reads as literally true (payment failed), not a mislabeled
completion. The existing completed-row importer's offsetting-payout mechanism assumes "already settled
in the previous app" (module docstring, `booking_import_service.py`); that assumption does not hold for
these 7 rows, so importing them via that path would create a payout entry asserting the driver was
already paid for a trip that was, per the old app's own payment ledger, never paid for.

Equally, the *existing* cancelled/failed lightweight path is also the wrong fit — these rides reached
and completed `in_progress` in trip-timestamp terms, and CLAUDE.md's ride state machine is explicit:
"Transitions from `in_progress` are `completed` only. Never `cancelled` after trip start." Writing
`status='cancelled'` for a ride with real start/end timestamps would be a state-machine violation the
existing hard guard was specifically written to prevent (see `booking_import_service.py`'s guard
comment).

## 3. Fix/remediation

**None applied — this is an investigation note, not a code change.** No `--apply`/commit exists for
any path touching these 7 rows; nothing was imported by this note.

## 4. Disposition options (for product-owner decision — not decided here)

1. **Import as `status='completed'` with real GPS/timestamps/distance, but $0 fare / no driver
   earnings / no payout.** Matches physical reality (trip happened) and the ride-state-machine
   invariant (no illegal `cancelled`-after-start transition) without asserting any payment claim that
   the data doesn't support. Zero money risk. This is the same "skip payout-offset logic, keep
   GPS+timestamps" principle already applied to the other 937 cancelled/failed rows, just filed under
   `completed` instead of `cancelled` because these 7 structurally are completions.
2. **Import as `completed` with the real `total_amount`/`you_earn` figures AND a genuine (non-
   offsetting) payout crediting the driver now**, on the theory that a ~9-year-old payment failure is
   something Spinr should make right today. Financially consequential (creates a real, withdrawable
   payable balance) and two of the seven rows' own dollar figures are internally inconsistent
   (`you_earn > total_amount`) regardless of the payment question — not something to import blindly
   even if this option is chosen.
3. **Leave permanently excluded** (status quo — no code or data change). Simplest and zero-risk, but
   loses trip-history/GPS-retention value for 7 real historical trips the same PIPEDA/SK
   Transportation Act retention argument used to justify importing the other 937 rows would also apply
   to.

## 5. Risk & impact on existing functionality

Not applicable yet — no code change in this note. Whichever option is chosen, the blast radius is the
same 7 specific `_id`s in the cached `bookings.csv`; nothing else in the 1,210-row export is affected
by this investigation or by resolving it.

## 6. What was NOT verified

- **Not verified**: whether any of the 7 drivers/riders were compensated or refunded through a
  completely separate old-app channel outside this export (e.g. manual support adjustment, a wallet
  credit) — `wallets.csv` and `refrals.csv` exist in the export but were not cross-checked against
  these 7 rows for this note; if option 2 is ever pursued, that cross-check should happen first.
- **Not verified against live Spinr Supabase or Stripe** — not needed for this specific question (the
  old app's own payment ledger answers it), but still true of everything else in this note same as the
  original finding.
