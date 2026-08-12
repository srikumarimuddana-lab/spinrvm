# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, dispatch |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #01 |

## 1. Issue / gap identified

A rider can cancel a pre-dispatch scheduled ride free of charge at any
point — even seconds before the scheduled pickup, as long as they beat the
dispatcher's next ~60s tick. No minimum-notice fee exists for this path
(unlike a dispatched ride, which already has a driver-assigned cancellation
fee).

## 2. Root cause

`cancel_scheduled_ride()`'s pre-dispatch branch was written correctly for
what it *does* handle ("no driver, offer, or hold to unwind pre-dispatch")
but never had a fee concept of its own — the existing
`calculate_cancellation_fee`/`pay_driver_cancellation_fee` machinery is
specifically about compensating a *driver* who was already engaged, which
doesn't apply here (no driver exists pre-dispatch).

## 3. Fix / remediation

- New `calculate_scheduled_cancel_notice_fee(ride, settings)` in
  `backend/services/cancellation_service.py` — rider-only, no driver
  payout branch (there's no driver to pay). Free unless: the flag is on,
  the ride isn't corporate-paid, `scheduled_time` parses, the pickup
  hasn't already passed, and the cancellation happens at or inside the
  notice window (default 60 min) — in which case it returns the
  configured flat fee (default $3.00).
- New `_charge_scheduled_cancel_notice_fee(ride, rider_id)` in
  `backend/routes/rides/cancellation.py` — mirrors `cancel_ride_rider`'s
  existing card/wallet charging pattern (`charge_ancillary_fee` /
  `wallet_apply_delta`), reusing the same Stripe idempotency-key shape
  (namespaced by `fee_type="scheduled_cancel_notice_fee"`, distinct from
  the dispatched-ride fee's `"cancellation_fee"`) and the same
  `financial_events` receipt pattern. Called *after* the cancellation
  claim already succeeded, wrapped in its own try/except so a fee failure
  can never undo an already-persisted cancellation.
- **Corporate-paid rides are excluded** (return 0), mirroring the existing,
  explicit exclusion in `calculate_cancellation_fee`'s card branch — that
  fee belongs on the corporate wallet ledger, not wired up in either path.
- **Flag-gated, defaulted OFF**: `scheduled_ride_notice_window_fee_enabled = False`,
  plus `scheduled_ride_notice_window_minutes = 60` and
  `scheduled_ride_notice_window_fee_amount = Decimal("3.00")`, both
  admin-configurable via `app_settings` (mirroring
  `cancellation_fee_admin`/`_driver`) rather than hardcoded — a genuine
  pricing decision, ships dark pending explicit approval.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the pre-dispatch branch of
  `cancel_scheduled_ride()`.** The dispatched-ride path (delegates to
  `cancel_ride_rider`) and its existing fee logic are completely untouched
  — a separate function, separate fee-type string, separate settings keys.
  Grepped for other callers of the new function — none; single call site.
- With the flag at its default (`False`), `calculate_scheduled_cancel_notice_fee`
  returns 0 on its first line and the charging function returns immediately
  — verified this doesn't change existing test behavior: the full
  `test_ride_cancellation_branches.py` + `test_p2_scheduled_rides.py` +
  `test_scheduled_dispatch_cr.py` suites pass unmodified except for the
  new tests added.
- **Money-safety review**: `Decimal` used throughout (`_d()`/`_round()`
  helpers, matching the existing `cancellation_service.py` convention);
  the wallet branch uses the same atomic, floor-clamped
  `wallet_apply_delta` RPC as the existing fee (idempotent via
  `reference_id=ride_id`); the card branch reuses `charge_ancillary_fee`'s
  own idempotency-key construction, just with a distinct `fee_type` so it
  can never collide with the dispatched-ride fee's key for the same ride.
- Dry-run scenario (per CLAUDE.md's money-change gate): rider books a
  scheduled ride for 2:00 PM by card, flag is on with default settings.
  Rider cancels at 1:15 PM (45 min out, inside the 60-min window) →
  `calculate_scheduled_cancel_notice_fee` returns `Decimal("3.00")` →
  `charge_ancillary_fee` attempts a $3.00 off-session Stripe charge on the
  rider's saved card → on success, a `financial_events` row is written
  (`event_type="stripe_charge"`, `metadata.source="scheduled_cancel_notice_fee"`).
  If the rider had instead cancelled at 12:30 PM (90 min out), the fee
  function returns 0 and no charge is attempted at all.

## 5. User-experience effect

**Rider-facing, and currently invisible** — ships dark. Once an admin
enables the flag: a rider cancelling a scheduled ride inside the notice
window sees a $3.00 (default, configurable) charge instead of a free
cancellation. **This needs a corresponding rider-app UI change before
enabling** (disclosing the fee/window at booking time and at
cancel-confirmation time) — not built in this pass, flagged explicitly as a
prerequisite rather than silently assumed to already exist.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/cancellation_service.py` | New `calculate_scheduled_cancel_notice_fee()` | Rider-only fee calculation, separate from the driver-payout-aware existing function |
| `backend/schemas.py` | Three new `AppSettings` fields (enabled flag, window minutes, fee amount) | Admin-configurable, flag-gated pricing |
| `backend/routes/rides/_deps.py` | Re-export the new calculation function | Match the existing `_deps` re-export pattern for `calculate_cancellation_fee` |
| `backend/routes/rides/cancellation.py` | New `_charge_scheduled_cancel_notice_fee()`; wired into `cancel_scheduled_ride()`'s successful-claim branch | Implement the charge, safely after the cancel already succeeded |
| `backend/tests/test_scheduled_cancel_notice_fee.py` | New file: 10 pure-function tests covering flag-off, window boundaries, corporate exclusion, missing/unparseable/past timestamps, custom settings | Isolated, fast coverage of the calculation logic |
| `backend/tests/test_ride_cancellation_branches.py` | 3 new integration tests: fee charged when enabled, no-op when disabled, charge failure doesn't block the cancel | Cover the wiring, not just the calculation |

## 7. Before / after

```python
# Before (cancel_scheduled_ride, successful-claim branch)
if claimed is not None:
    # Pre-dispatch there is no driver, offer, or card hold to unwind;
    # notify the rider's own devices and any watching admin console.
    await _deps.manager.send_personal_message(...)
```

```python
# After
if claimed is not None:
    # Pre-dispatch there is no driver, offer, or card hold to unwind;
    # notify the rider's own devices and any watching admin console.
    # Notice-window fee (Finding #01): flag-gated, defaulted off; a
    # failure here must never undo the cancellation above.
    await _charge_scheduled_cancel_notice_fee(ride, current_user["id"])
    await _deps.manager.send_personal_message(...)
```

## 8. Rollback plan

- **Without a deploy**: flip `scheduled_ride_notice_window_fee_enabled`
  back to `false` — effective within the 60s settings-cache TTL. No data
  needs unwinding for future cancellations; this is the primary rollback
  path.
- **A charge already collected while the flag was on is NOT undone by
  flipping the flag** — that's live money already moved (a Stripe charge
  or a wallet debit). Per CLAUDE.md's rule that a `git revert` is not a
  rollback plan for anything already applied to live data: reversing an
  individual bad charge requires a manual Stripe refund (or a compensating
  `wallet_apply_delta` credit with `reference_id` referencing the original
  debit) — the same remediation path the existing dispatched-ride
  cancellation fee already relies on, since this reuses its exact charging
  primitives.
- Code-level rollback: plain `git revert`, no migration involved (the new
  `AppSettings` fields have Pydantic defaults, no schema migration
  required for a flat-JSON settings row).

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_cancel_notice_fee.py`
      (10 passed) + `backend/tests/test_ride_cancellation_branches.py`
      (16 passed, 13 prior + 3 new) via the session's venv. Also re-ran
      `test_p2_scheduled_rides.py` and `test_scheduled_dispatch_cr.py`
      earlier in this session's work — unaffected by this change (verified
      no shared code paths beyond the already-tested `cancel_scheduled_ride`
      claim logic, which is untouched by this diff).
- [x] `ruff check` on all six touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access. **This
      is a real Stripe-charging code path**; before enabling the flag in
      production, a staging run against Stripe test-mode cards (success,
      decline, no-payment-method) is strongly recommended, not just unit
      tests against mocked `charge_ancillary_fee`.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's money-arithmetic convention (Decimal
      throughout, `_d()`/`_round()` helpers) and the mandatory
      state-machine/money dry-run requirement (scenario given in §4).
- [x] Feature-flagged — yes, defaulted off, per CLAUDE.md's pricing-decision
      gate.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, and explicitly distinguishes
      "stop future charges" (flag flip, instant) from "undo a charge
      already made" (manual Stripe refund / compensating wallet credit —
      not automated by this change, same as the existing fee mechanism)
- [x] Blast radius is stated, not assumed — isolated to one new function
      pair with its own fee-type string and settings keys
- [x] No silent behavior change — off by default; the rider-app disclosure
      gap needed before enabling is named explicitly in §5, not implied to
      already exist

## What was NOT verified

Not tested against real Stripe (test-mode or otherwise) — only against a
mocked `charge_ancillary_fee`. No rider-app UI work was done to disclose
this fee/window at booking or cancel time — flagged as a hard prerequisite
before flipping the flag on, not a nice-to-have. No corporate-wallet
charging path was built for corporate-paid scheduled rides — they remain
fee-free under this feature, matching (not fixing) the existing gap in the
dispatched-ride fee path.
