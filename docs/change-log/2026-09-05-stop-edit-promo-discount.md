# Change Impact & Risk Log — Stop edits no longer drop the promo discount

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (secondary: rides) |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.10 finding **N3** (Major) |

## 1. Issue / gap identified

`_reestimate_fare_for_stops` computed `grand_total = new_total + fees_total +
tax_amount`, omitting `- discount`. Stop edits are permitted while a ride is
`in_progress`, and settlement charges the stored `grand_total`, so **adding or
removing a stop on a promo ride silently re-added the discount to the rider's
charge**. Meanwhile `_ride_receipt_lines` in the same file still rendered the
`-$5.00` promo line, so the receipt no longer matched the amount charged — a
violation of CLAUDE.md's "every charge on the receipt maps to a disclosed line
item".

Audit's worked example: a $20 fare with a $5 promo books at $15.75; adding a $3
stop charged **$24.15 instead of $19.15**, a $5.00 overcharge.

## 2. Root cause

Two independent recompute paths exist for the same number and only one carries
the discount:

- `services/fare_service.py:468` (settlement) —
  `new_total_fare + area_fees_total + tax_amount - discount` ✅
- `routes/rides/_shared.py:419` (stop edit) —
  `new_total + fees_total + tax_amount` ❌

The stop-edit path was written against the pre-promo fare shape and never
updated when `discount_amount` was introduced. Nothing caught it because the two
functions have no shared helper and no cross-check asserting they agree.

## 3. Fix / remediation

Subtract `_d(ride.get("discount_amount") or 0)`, matching `fare_service.py`.

Additionally **clamped at zero**, which `fare_service.py` does not do.
`fare_service`'s fare only ever grows relative to the booked one, so it cannot
go negative there — but `DELETE /{ride_id}/stops/{index}` *shrinks* the fare, so
a ride whose remaining fare falls below the promo would otherwise store a
negative `grand_total` and settle a negative charge. That case is reachable only
through the path being fixed here, so the clamp is not speculative.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** `_reestimate_fare_for_stops` has exactly two callers,
both in `routes/rides/stops.py` (`POST /{ride_id}/stops` add, `DELETE
/{ride_id}/stops/{index}` remove), plus the `routes/rides/__init__.py`
re-export pair. It is not used by booking, settlement, estimates, or any
background loop.

Interactions considered:

- **Settlement** (`services/fare_service.py`) — untouched. It recomputes at
  completion with its own (already-correct) discount subtraction, so this change
  makes the two paths *agree* rather than introducing a second opinion.
- **Receipt rendering** (`_ride_receipt_lines`, same file) — untouched. It
  already rendered the promo line; the charge now matches it.
- **Promo capping** — deliberately **not** re-implemented here. `routes/rides/booking.py:1338`
  and `routes/promotions.py:305,477` cap `discount_amount` at the ride portion
  when it is stored, and `fare_service.py` subtracts the stored value uncapped.
  This change mirrors `fare_service.py` (subtract the stored value) rather than
  re-deriving a cap, so a `free_ride` promo — which by design covers the full
  grand total and bypasses the cap — keeps working.
- **Ride state machine / insurance periods / dispatch** — not touched.
- **Money rules** — `Decimal` throughout (`_d`, `_round`); no float introduced.

Regression risk: rides **without** a promo are arithmetically unchanged
(`discount = 0`), which is the majority of traffic and is covered by an explicit
test. The behaviour change is confined to promo rides whose stops are edited.

**Known residual (self-review finding).** `grand_total` subtracts the discount
**uncapped** — mirroring `fare_service.py:468` so `free_ride` promos, which
exceed the ride fare by design, keep working — while `_ride_receipt_lines`
renders the promo line **capped at ride fare**. They agree for every normal
ride, because `booking.py:1338` caps `discount_amount` at the ride portion when
it is stored. They diverge only once stop *removal* drags the fare below the
stored promo: a $3 remaining fare with $1 tax and a $5 promo charges $0 but
renders lines summing to $1. Reconciling that means changing the shared
renderer, which several other surfaces read, so it is out of scope here.

Note the direction, though: **before this fix the mismatch was the full
discount on every promo stop-edit.** This narrows it to a rare edge case rather
than introducing it.

## 5. User-experience effect

- **Rider (visible, mid-ride):** a rider on a promo ride who edits stops is now
  charged the correct, lower amount. Anyone who did this during live testing was
  overcharged by exactly their promo amount; this stops that.
- **Driver:** no change. `driver_earnings` is computed from `new_total` above
  this line and is not affected — the promo comes out of the platform's side,
  consistent with the 0%-commission model.
- **Internal admin:** rides edited after this ship will show a `grand_total`
  consistent with their receipt lines; historic over-charged rides are not
  retroactively corrected by this change (see §8).
- No copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/_shared.py` | `_reestimate_fare_for_stops` subtracts `discount_amount` and clamps the result at zero | The charge must match the receipt, and stop removal can push the fare below the promo |
| `backend/tests/test_stop_edit_promo_discount.py` | New | Pins the subtraction, the no-promo no-op, the receipt-math equality, and the zero floor |

## 7. Before / after

```python
# Before
grand_total = _round(new_total + fees_total + tax_amount)
```

```python
# After
discount = _d(ride.get("discount_amount") or 0)
grand_total = max(_round(new_total + fees_total + tax_amount - discount), _d(0))
```

Concrete scenario: $20 fare ($3 base + $12 distance + $4 time + $1 booking fee),
$5 promo, $0.75 tax. Booked `grand_total` = $15.75. Rider adds a stop worth
$3.40 of extra distance/time.

| | Before | After |
|---|---|---|
| `new_total` | $23.40 | $23.40 |
| `grand_total` stored | **$24.15** | **$19.15** |
| Receipt promo line rendered | −$5.00 | −$5.00 |
| Charge vs. receipt | mismatched by $5.00 | matches |

## 8. Rollback plan

Single-expression change, no migration, no schema change, no new dependency —
a `git revert` is a complete rollback for future stop edits.

It is **not** a remediation for money already taken: rides settled at the
inflated `grand_total` while the bug was live were genuinely overcharged and
need refunds, not a code revert. The affected set is enumerable —
`rides` where `discount_amount > 0` AND `stops` is non-empty AND
`payment_status = 'paid'` AND the ride was edited after booking — and the
overcharge is exactly `discount_amount` per affected ride. **This backfill is
not performed by this change and is left as a deliberate follow-up for a human
to scope against production data.**

No feature flag: the change is a correction toward the already-correct
settlement path, and a flag that toggles "overcharge by the promo amount" back
on is not a state worth being able to reach.

## 9. Verification performed

- [x] Blast-radius grep performed — `_reestimate_fare_for_stops` (2 callers +
      re-export pair), `discount_amount` (all writers/readers), and the two
      recompute paths cross-read against each other.
- [x] Reviewed against `CLAUDE.md` conventions: Decimal-only money math,
      disclosed-line-item rule, no surge/corporate interaction introduced.
- [x] Before/after money scenario written out above (gate 4).
- [x] `ruff check` and `ruff format --check` clean.
- [ ] **Automated tests NOT run** — see below.

## What was NOT verified

**No tests were executed.** PyPI is blocked by this environment's network policy
(403), so backend dependencies could not be installed and `pytest` could not
run. `backend/tests/test_stop_edit_promo_discount.py` is written but **has never
been run** — in particular its expected values depend on `_reestimate_fare_for_stops`'s
`per_km_effective` / `per_min_effective` back-derivation and on `multi_leg_distance`,
which were read but not executed, so the arithmetic in the fixtures is reasoned
about rather than confirmed. Existing stop-edit tests (if any) were likewise not
re-run. **Needs a real `pytest` run before merge.**

Also not verified: no end-to-end check that the settled charge equals the
receipt total after a stop edit (that spans `stops.py` → `payments.py` →
`_ride_receipt_lines` and needs the integration tier); no check against live
Supabase; and the size of the already-overcharged production set in §8 was not
measured — no production query was run in this session.
