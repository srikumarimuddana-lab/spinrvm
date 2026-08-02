# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Corporate #2 |

## 1. Issue / gap identified

`routes/rides/booking.py`'s two booking-time corporate policy checks
(the `company_allowance` payment-method path and the `work_profile`
path) evaluated a company's `max_fare_per_ride` cap against `total_fare`
— the fare-service subtotal *before* area fees and tax are added. The
amount actually charged to the company (and, once the pre-auth/allowance
debit happens, the amount that leaves the wallet) is `grand_total =
total_fare + area_fees_total + tax_amount` — so a ride whose base fare
was under the cap but whose area fees/tax pushed the real charge over it
would incorrectly clear the policy check at booking time.

## 2. Root cause

`services/company_booking_service.py` (guest bookings) and
`utils/scheduled_rides.py` (the dispatch-time policy re-check added
earlier in this same review, Track B #3) both already pass `grand_total`
to `evaluate_policy_for_ride`'s `estimated_fare` parameter — confirmed by
grepping every caller of that function. `routes/rides/booking.py` was
the one remaining caller still passing the pre-fees `total_fare`,
apparently written before `grand_total` existed at that point in the
function or just not updated when the other two call sites were fixed
to use it.

## 3. Fix / remediation

Changed both `evaluate_policy_for_ride(..., estimated_fare=total_fare,
...)` call sites in `routes/rides/booking.py` to
`estimated_fare=grand_total` — `grand_total` is already computed earlier
in the same function (before either policy check runs), so this is a
pure substitution with no new computation. Left the pre-auth allowance
buffer check (a different rule, ~15 lines below, checking remaining
allowance headroom rather than the policy cap) untouched — out of scope
for this specific finding, which named the `max_fare_per_ride` policy
check.

Known, documented limitation carried forward unchanged: `grand_total`
still excludes tip, since the tip amount isn't known until after the
trip completes — no booking-time check can account for it. This matches
the already-correct behavior in the other two call sites.

## 4. Risk & impact on existing functionality

- **Blast radius: 2 lines in 1 file** (`estimated_fare=total_fare` →
  `estimated_fare=grand_total`, in the `company_allowance` and
  `work_profile` branches of `create_ride`). No change to
  `evaluate_policy_for_ride` itself, to how `grand_total` is computed, or
  to any other policy rule (`time_window`, `allowed_payment_source`).
- Grepped every caller of `evaluate_policy_for_ride` across the backend
  — `company_booking_service.py` and `scheduled_rides.py` were already
  correct; `routes/rides/booking.py` was the sole outlier, now aligned
  with the other two.
- This makes the policy check **stricter**, not looser — some
  corporate-paid bookings that previously cleared `max_fare_per_ride`
  (because area fees/tax pushed the real charge over the cap while
  `total_fare` alone stayed under it) will now correctly be rejected at
  booking time with a `policy_violation`/`Fare exceeds company limit per
  ride` response, matching what the company admin configured the cap to
  mean. A ride that was already being correctly evaluated (no
  area fees/tax, or comfortably under the cap either way) sees no change.
- Ran the full corporate-ride and policy-adjacent test suite (8 files,
  145 tests) — all passing, including a new dedicated regression test
  that fails against the pre-fix code (asserted by construction: it sets
  `fees_total` far above any plausible `total_fare` for the fixture route
  and a cap between the two, so the old `total_fare`-only comparison
  would have incorrectly passed it).

## 5. User-experience effect

**Rider/corporate-member-facing, visible only at booking time.** A rider
booking a work ride whose area fees/tax push the total over their
company's per-ride cap will now see the booking rejected
(`Fare exceeds company limit per ride`) instead of the ride going through
and the company being charged more than its configured cap allowed. This
is a stricter enforcement of an already-configured admin setting, not a
new restriction the company didn't already ask for — the cap's *stated
meaning* (a limit on what the company pays per ride) is now what's
actually enforced. No change to personal (non-corporate) bookings, which
never go through this policy check at all.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/booking.py` | Both `evaluate_policy_for_ride` calls: `estimated_fare=total_fare` → `estimated_fare=grand_total` | The cap must be checked against what's actually charged, not the pre-fees/tax subtotal |
| `backend/tests/test_corporate_ride_payment.py` | New test `test_work_profile_policy_check_uses_grand_total_not_bare_fare` | Lock in the fix with a case that would pass under the old (buggy) comparison and fail under the new one |

## 7. Before / after

```python
# Before — company_allowance path
_policy_result = await _deps.evaluate_policy_for_ride(
    corporate_account_id=body.corporate_account_id,
    rider_id=current_user["id"],
    estimated_fare=total_fare,  # excludes area fees + tax
    ride_type="standard",
    pickup_time=_policy_pickup_time,
)
```

```python
# After
_policy_result = await _deps.evaluate_policy_for_ride(
    corporate_account_id=body.corporate_account_id,
    rider_id=current_user["id"],
    estimated_fare=grand_total,  # total_fare + area_fees_total + tax_amount
    ride_type="standard",
    pickup_time=_policy_pickup_time,
)
```

(Identical substitution applied to the `work_profile` branch's call a
few dozen lines later.)

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores the prior (under-enforcing) behavior. No feature flag — this
closes a policy-enforcement gap using data (`grand_total`) that was
already computed and already used correctly by the other two callers of
the same function; there's no meaningful dark-ship version of "enforce
the cap against what's actually charged."

## 9. Verification performed

- [x] Automated tests: `test_corporate_ride_payment.py` (18, incl. 1
      new), `test_create_ride_remaining_branches.py` (17),
      `test_company_guest_booking.py` (9), `test_scheduled_dispatch_cr.py`
      (32), `test_corporate_policy_service.py` (37),
      `test_corporate_surge_bypass.py` (16), `test_p2_corporate_decimal.py`
      (10), `test_corporate_settle_suspended_audit_flag.py` (6) — 145
      passed, run via the session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on `booking.py` and the test file — clean.
- [x] Blast-radius grep performed (see §4): every caller of
      `evaluate_policy_for_ride`.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: a company sets `max_fare_per_ride=$80`. A rider
      books a work ride with `total_fare=$79` but an airport pickup fee +
      tax bringing `grand_total` to $95. Before this fix: booking
      succeeds (policy check only saw $79 < $80); the company is charged
      $95, over its configured cap. After this fix: booking is rejected
      at request time with `policy_violation` /
      `Fare exceeds company limit per ride`, matching the cap's intent.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — every caller of the affected
      function grepped and confirmed
- [x] User-experience effect stated: this is a behavior change (some
      previously-accepted corporate bookings will now be rejected) —
      documented above as making an already-configured admin setting
      actually mean what it says, not a new restriction

## What was NOT verified

Not tested against a live/staging Supabase, real Google Maps distance/
duration calculation, or a real Stripe charge — only mocked fare/fee
inputs in the unit/integration test suite. Did not extend `grand_total`
to include an estimated tip — tip amount is genuinely unknowable at
booking time (this matches the pre-existing, correct behavior of the
other two `evaluate_policy_for_ride` callers, not a new gap introduced
here). Did not audit or change the separate pre-auth allowance-buffer
check a few lines below (`_remaining < _round(_d(str(_f(total_fare))) *
_d("1.5"))`), which still uses `total_fare` rather than `grand_total` —
that's a different rule (allowance headroom, not the `max_fare_per_ride`
policy cap) and was outside the scope of this specific finding; whether
it has the same class of bug is a reasonable follow-up question, not
answered here.
