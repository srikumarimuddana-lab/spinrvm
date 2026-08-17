# Change Impact & Risk Log — exact-fare hold + pre-capture tip folding

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude Code (session-driven, on `claude/uber-lyft-payment-tips-uyrv75`) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/uber-lyft-payment-tips-uyrv75` (7 commits, `8a93c85`..`834f054` + loop commit) |
| Related issue or gap ID | none filed — originated from a live-testing complaint that a $5 ride showed a $15 pending charge |

## 1. Issue / gap identified

Two problems, one root:

1. **The hold was 3× the fare on small rides.** Booking authorized
   `grand_total + RIDE_AUTH_BUFFER_CAD` (flat **$10**), so a $5 ride put a $15
   pending charge on the rider's bank feed. Riders read that as an overcharge.
2. ~~Tips arriving after settlement were credited but never charged.~~
   **Withdrawn from this change.** `main` had already fixed it — same root
   cause, same day — via `charge_late_tip` and, for wallet/corporate rides,
   `charge_late_wallet_tip` / `charge_late_corporate_tip` with migration 319.
   This branch was cut from a 6-day-stale base and duplicated the work with a
   design that was worse (it charged a personal card even for corporate rides)
   and that contradicted a recorded product decision (main credits the driver
   immediately and absorbs the shortfall; this branch deferred credit by up to
   7 days). The duplicate subsystem was removed rather than merged.

## 2. Root cause

The $10 buffer existed so a post-trip tip could be captured on the *same*
PaymentIntent, saving Stripe's $0.30 fixed fee. Two things made that a bad
trade:

- Migration 248 set `fare_lock_enabled = TRUE`, so settlement keeps the
  booking-time fare. The buffer was therefore ~100% **tip** headroom, not
  fare-variance headroom — it was covering a risk that no longer existed.
- The buffer's *size* never mattered. Combining fare and tip saves exactly one
  fixed fee ($0.30) whether the buffer is $2 or $10. The extra $8 of reserved
  funds bought nothing and cost the rider's trust.

For (2), the gap was simply an unguarded endpoint. `rating.py` had a
payment-status guard; `add_tip` never got one. The rider-app offline replay
queue (`store/rideStore.ts`) made it reachable in ordinary use, since a queued
tip POST can replay long after settlement.

## 3. Fix / remediation

- Hold the **exact fare**. `RIDE_AUTH_BUFFER_CAD` → `0.00`.
- A tip is folded into the existing hold via **incremental authorization**
  (raise the hold, capture once) where the card supports it, and charged
  separately where it does not.
- A tip that arrives **after** capture cannot be folded in at all — Stripe
  cannot increment a captured PaymentIntent. That case is handled by main's
  existing `charge_late_tip` / `charge_late_wallet_tip` /
  `charge_late_corporate_tip`; this branch adds nothing there.
- The **cancellation fee is now a partial capture of the hold** instead of
  cancel-the-hold-then-charge-a-new-PI.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend payments + rider-app).** This is the
money path; it is not isolated.

Consumers checked by grep before changing:

| Thing changed | Other readers/writers found | Assessment |
|---|---|---|
| `RIDE_AUTH_BUFFER_CAD` | `routes/rides/booking.py` (only real consumer); referenced in comments by `utils/orphaned_hold_reconciler.py`, `utils/card_hold_release.py`, `docs/change-log/2026-07-30-*` | Single call site. Reconciler/release read `payment_intent_id`/`auth_status`, not the buffer. |
| `authorize_ride` | `routes/rides/booking.py`, `utils/scheduled_rides.py` (via `_preauthorize_ride_card`) | Both go through the same helper. Scheduled rides authorize at dispatch, unchanged. |
| `ChargeOutcome` | every Stripe helper caller | New field is additive with a default; no positional construction found. |
| `capture_ride` | `services/payment_service.py`, `utils/preauth_capture.py` | Behaviour unchanged; only its docstring's stale "buffer" wording. |
| Hold release on cancel | `routes/rides/cancellation.py` (rider), `routes/drivers/ride_cancel.py` (driver), `routes/rides/matching.py` (auto-cancel) | **Only the rider path changed.** Driver-cancel and no-driver auto-cancel still release in full, which is correct — a rider is never charged for those. |
| Background loops | unchanged | No loop added or removed. |

**Corporate rides (this section was missing and the reviewer was right to
flag it — `payment_service.py` is edited here and `settle_corporate` lives in
it):** the increment path is card-only and never runs for a
`company_allowance` ride, because such a ride has no booking-time card hold to
increment — `settle_corporate` debits the allowance/master wallet instead.
Nothing in this branch changes corporate settlement, allowance caps,
`ride_payment_sources`, corporate policy evaluation, or the billing summary /
PDF statement.

A separate, **pre-existing** issue was found while checking this and is being
filed on its own rather than fixed here: `process_payment` builds
`total_charge = grand_total + tip` and hands the combined figure to
`settle_corporate`, which debits all of it — so a rider's personal gratuity is
billed to their employer, lands in `ride_payment_sources` (the source for the
company's invoice), inflates the `final_fare` corporate policy evaluates, and
can trigger an "allowance used up" notification because of a tip. It behaves
identically on `main`; this branch neither introduces nor fixes it. Fixing it
needs a product decision (is a tip on a corporate ride ever company-billable?)
before code.

Regression risks accepted and mitigated:

- **A zero buffer under-covers settlement if `fare_lock_enabled` is turned
  off.** Mitigated: the buffer is resolved at runtime, and falls back to a
  proportional buffer (25% of fare, floored $2, capped $10) when the lock is
  off. A settings-lookup failure assumes *unlocked* — over-holding is
  recoverable at capture, under-holding fails settlement.
- **`insufficient_funds` no longer retries at a lower amount** when the buffer
  is zero, because there is no lower amount. Booking now declines instead of
  silently retrying an identical authorization.
- **A cancellation fee larger than the hold is capped, not chased.** A $5 fee
  on a $4 fare collects $4. Deliberate: a cancel fee exceeding the ride is not
  defensible, and the shortfall is logged.
- **A failed increment costs one extra Stripe fixed fee**, nothing more: the
  original hold is untouched, so the fare still captures and the tip falls to
  main's existing late-tip path.

## 5. User-experience effect

**Rider (visible, and the point of the change):**
- A $5 ride now shows a **$5** hold, not $15.
- `payment-confirm.tsx` copy changed: it no longer claims "estimated fare +
  $10". It had a hardcoded `totalFare + 10`, which after this change would have
  displayed a number the server does not use.
- Tip presets are now 15/18/20% of fare (whole dollars) instead of flat
  $2/$5/$10 — a $10 preset on a $5 ride was part of the same problem.
- Post-capture tips are unchanged from `main` (see §1.2).

**Driver:** no change. Tip crediting is `main`'s behaviour, untouched here.

**Mid-session impact:** a rider mid-ride at deploy time already has a hold
placed under the old rules; nothing re-reads the buffer mid-ride, so their
settlement is unaffected. Rides booked after deploy get the new hold.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/config.py` | `RIDE_AUTH_BUFFER_CAD` 10.00 → 0.00 | Hold the quoted fare, not more |
| `backend/routes/rides/booking.py` | `_resolve_auth_buffer`; exact-fare hold; retry gated on `buffer > 0`; persist `auth_incrementable` | Zero buffer, safely |
| `backend/utils/stripe_charge.py` | `+increment_authorization`, `+capture_cancellation_fee`, `_reads_incremental_support`; `ChargeOutcome.incremental_authorization_supported` | Fold tips into the hold; take fees from it |
| `backend/services/payment_service.py` | Increment-then-capture before the overflow branch | One fee where the card allows |
| `backend/routes/rides/cancellation.py` | Fee hoisted above hold handling; partial capture | Fee cannot decline |
| `backend/utils/preauth_capture.py` | Window 20 → 5 min; select `auth_incrementable` | Window no longer buys anything |
| `backend/migrations/321_tip_collection.sql` | **new** — `rides.auth_incrementable` only | Persist the per-card increment capability |
| `rider-app/components/tipPresets.ts` | **new** — ladder + selection-reconciliation rules | Testable; see the blocker below |
| `rider-app/__tests__/tipPresets.test.ts` | **new** — 13 tests | There was no tip coverage at all |
| `rider-app/app/payment-confirm.tsx` | Removed hardcoded `+ 10` | Screen was announcing a hold we no longer place |
| `rider-app/app/ride-completed.tsx` | Fare-scaled tip presets | $10 preset on a $5 ride |

## 7. Before / after

```python
# Before — booking.py: flat buffer, $5 ride holds $15
buffer = _round(_d(_deps._settings.RIDE_AUTH_BUFFER_CAD))   # 10.00
hold_amount = _round(_d(grand_total) + buffer)
```

```python
# After — fare-lock aware; normally zero, so hold == quoted fare
buffer = await _resolve_auth_buffer(_round(_d(grand_total)))  # 0.00 when locked
hold_amount = _round(_d(grand_total) + buffer)
```

```python
# Before — cancellation.py: release the hold, then charge a NEW PI (can decline)
_released = await _deps.cancel_authorization(ride_id=..., payment_intent_id=_booking_pi)
...
outcome = await _deps.charge_ancillary_fee(amount=total_cancel_fee, ...)
```

```python
# After — take the fee from the hold; funds are already reserved so it cannot
# be declined for insufficient funds. Remainder auto-released by Stripe.
_fee_outcome = await _deps.capture_cancellation_fee(
    ride_id=ride_id, payment_intent_id=_booking_pi,
    fee=total_cancel_fee, authorized_amount=_held_amount,
)
```

```tsx
// Before — ride-completed.tsx: selection is a raw number, never reconciled
const [selectedTip, setSelectedTip] = useState<number | null>(null);
const tipOptions = useMemo(() => /* fare-derived */, [currentRide]);
// ...
{tipOptions.map((amt) => (
  <TouchableOpacity style={[..., selectedTip === amt && styles.tipBtnActive]} />
))}
// handleSubmit charges `selectedTip` even when no chip matches it.
```

```tsx
// After — derived during render, and the placeholder ladder is not tappable
const tipOptions = useMemo(() => computeTipOptions(fare), [fare]);
const tipReady = isTipLadderReady(fare);
const effectiveTip = reconcileSelectedTip(selectedTip, tipOptions);
// ...
{tipOptions.map((amt) => (
  <TouchableOpacity disabled={!tipReady}
    style={[..., effectiveTip === amt && styles.tipBtnActive]} />
))}
// handleSubmit charges `effectiveTip` — it cannot diverge from the highlight.
```

## 8. Rollback plan

Ordered least- to most-invasive, none requiring a redeploy except the last:

1. **Hold size** — set `RIDE_AUTH_BUFFER_CAD=10.00` in the backend environment.
   Restores the old hold immediately; the code path is unchanged.
2. **Increment path** — self-disabling. If `auth_incrementable` is false
   everywhere (e.g. set the column default to `false` and clear it), every ride
   takes the two-charge fallback, which is the pre-existing overflow path.
3. **Migration** — rollback SQL is in the header of
   `backend/migrations/321_tip_collection.sql`. Dropping `auth_incrementable`
   is safe at any time: its absence reads as "not incrementable", which is the
   pre-existing behaviour.

**`git revert` is not sufficient** for anything already applied to live data:
captured cancellation fees and incremented authorizations are real Stripe
movements. Reverting the code does not reverse them; they need refunds.

## 8b. Blockers found in Stripe review, after the rebase

A senior-Stripe-perspective review of the rescoped branch found three blockers,
all confirmed against the vendored `stripe` 15.1.0 schema in `backend/venv`, and
all invisible to the test suite because the mocks stopped short of the real
Stripe param/response shapes.

1. **`request_incremental_authorization_support` is not a valid top-level
   `PaymentIntent.create` param.** It is `card_present`-only (Terminal). The
   card-not-present equivalent is
   `payment_method_options.card.request_incremental_authorization`, a
   `Literal["if_available","never"]` — different nesting, different spelling,
   different type. Sending the wrong one risks a 400 on **every card booking
   authorization**, not just tipped rides, since `authorize_ride` runs before
   dispatch on every card ride. Fixed.
2. **The capability read-back checked a field that never exists on a CNP
   charge.** `payment_method_details.card_present.incremental_authorization_supported`
   is a Terminal boolean; the CNP charge carries
   `payment_method_details.card.incremental_authorization.status`. The old read
   returned `None` unconditionally, so `auth_incrementable` would have been
   `False` forever and the whole one-fee design silently inert — failing open
   into the fallback, so nothing would ever have surfaced it. Fixed.
3. **`cancellation.py` wrote `auth_status="released"` whenever a live hold
   existed**, regardless of which branch ran. That claimed no money was taken
   when the fee had actually been captured, and — worse — marked a
   deliberately-left-open hold as released, hiding it from
   `orphaned_hold_reconciler` and `card_hold_release`, which both select on
   `OPEN_AUTH_STATES`. The rider's funds would sit until Stripe's ~7-day expiry
   with no sweeper able to find them. Now writes `captured` / `released` /
   leaves it open, matching what actually happened.

Also closed: a successful increment followed by a failed capture abandoned the
(now larger, fare+tip) hold once `payment_intent_id` was repointed at the fresh
charge. The hold is now released best-effort before the fallback.

New tests reach the boundaries the old mocks did not:
`test_authorize_incremental_shape.py` (9) pins the create-param and read-back
shapes including the Terminal-vs-CNP confusion, and
`test_cancel_fee_from_hold.py` now asserts `auth_status` in all three branches.

## 9. Verification performed

- [x] **Automated tests** — full backend suite, after rebasing onto current
      `origin/main`. New: `test_cancel_fee_from_hold.py` (7),
      `rider-app/__tests__/tipPresets.test.ts` (13). Updated:
      `test_ride_preauth_booking.py`, `test_settle_card_capture.py`,
      `test_preauth_capture.py`.
- [x] **Real production build** of rider-app — `npm run build:web`, exit 0.
      Also `tsc --noEmit`, clean. (CLAUDE.md requires the real build; the
      typecheck alone would not have counted.)
- [x] **Blast-radius grep** — searched for consumers of
      `RIDE_AUTH_BUFFER_CAD`, `authorize_ride`, `capture_ride`,
      `cancel_authorization`, `ChargeOutcome`, `charge_ancillary_fee`,
      `tip_amount`, `driver_earnings`, and every `_spawn(` loop registration.
      Results in §4.
- [x] **Conventions reviewed** — Decimal-only money (pre-commit money check
      passed on every commit), Stripe idempotency keys namespaced per
      operation, RLS on the new table, replay-safety contract for the new loop,
      `logger.error` (not warning) on payment-path failures.
- [ ] **Manual staging repro** — NOT done; see below.
- [ ] **Feature flag** — NOT added; see below.

## 10. What was NOT verified

State this plainly rather than letting the checklist imply full coverage.

- **No Stripe test-mode run.** Everything Stripe-facing is verified against
  mocks only. `increment_authorization` and `capture_cancellation_fee` have
  **never executed against real Stripe**. Before enabling on live traffic, book
  a test-mode ride and confirm: a $5 ride holds $5; cancelling past the
  threshold captures the fee and releases the remainder; a tip increments the
  hold.
- **Canadian eligibility for incremental authorization is unconfirmed.** Stripe
  documents Visa/Mastercard support for all card types and user categories
  globally, and Visa's rules name MCC 4121 (taxicabs/limousines) for card-absent
  incremental authorizations — but `docs.stripe.com` was unreachable from the
  build environment, so the per-brand availability table was not read directly.
  Stripe's docs say the authoritative answer is the Dashboard ("find your user
  category"). **If it turns out unavailable, nothing breaks** — every tip takes
  the two-charge fallback at a cost of $0.30 each.
- **Not feature-flagged, and it does not need to be.** The hold size is
  env-tunable (`RIDE_AUTH_BUFFER_CAD`) and the increment path self-disables
  when `auth_incrementable` is false, so both halves have a runtime off switch
  without a deploy.
- **The branch was rebased late.** It was cut from a 6-day-stale base and only
  discovered during review that `main` had already shipped the late-tip fix.
  The duplicate subsystem was removed, but the interaction between this
  branch's pre-capture increment and main's post-capture `charge_late_tip` has
  been verified only by the test suite, not against real Stripe.
- **No visual-regression tooling exists for rider-app**, so the copy and
  tip-preset changes were reasoned about and type/build-checked, **not
  screenshotted**. Standing repo gap, not specific to this change.
- **The no-show fee path (`routes/drivers/ride_cancel.py`) is wallet-only** — a
  card-paying rider is never charged a no-show fee today. That is a
  **pre-existing** gap, unrelated to this change and deliberately not fixed
  here, but it is the same shape as the cancellation fee and could take the
  same partial-capture treatment.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
