# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (session claude/b20-ledger-projection-tip-fix) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | see PR description / branch `claude/b20-ledger-projection-tip-fix` |
| Related issue or gap ID | ACTION_ITEMS.md B20 |

## 1. Issue / gap identified

`backend/utils/ledger_projection.py::_decompose`'s default fare branch reads
`rides.driver_earnings` / `rides.tax_amount` at projection time without
checking `rides.payment_status`. Migration 287's 30-minute grace window
covers the normal "header written before `update_ride` lands" gap, but not a
ride whose post-charge DB update failed outright and was never recovered
within that window (the path that returns 503 and pages Sentry). In that
stuck case the projection reads pre-tip `driver_earnings`/`tax_amount` and
misbooks the tip into `platform_revenue` instead of `driver_payable`.

## 2. Root cause

`services/payment_service.py::_finalize_card_settlement`'s legacy two-write
settlement path writes the `financial_events` header
(`record_payment_event`, source=`process_payment`) **before** the
subsequent `update_ride` call that applies the tip delta to
`driver_earnings`/`tax_amount` and flips `payment_status` to `'paid'`. If
that second write raises, the function logs "Charge {} succeeded but ride {}
DB update failed — ride stuck in 'processing'" and returns HTTP 503 — the
ride is left at whatever `payment_status` it had before (typically
`'processing'`), while the financial_events header (with the post-tip
charged `delta_cents`) already exists and is now eligible for the migration
287 work queue once it's 30 minutes old. `_decompose` had no way to tell
"this ride's fare-derived columns are trustworthy" from "this ride's
fare-derived columns are still pre-tip" other than blind elapsed time, so it
always decomposed from whatever `driver_earnings`/`tax_amount` happened to
be on the row at projection time.

## 3. Fix / remediation

`_decompose`'s default (fare/tip) branch now calls a new
`_fare_ready_to_decompose(ride, event)` gate before reading
`driver_earnings`/`tax_amount`:

- `ride.payment_status == 'paid'` → proceed exactly as before (this is the
  overwhelming common case and is unchanged).
- Not yet `'paid'` and the event is younger than a new bounded fallback
  (`_SETTLEMENT_FALLBACK_SECONDS = 6h`) → **skip this tick** (return `[],
  False, "awaiting_payment_settlement"`) rather than book anything. The RPC
  hands the same leg-less header back on the next tick (oldest-first), so
  this is a retry, not a drop.
- Not yet `'paid'` and older than the 6h fallback → fall back to the
  existing **degraded** contract (whole amount to `platform_revenue`,
  loudly flagged via `ALERT_LEGS_DEGRADED`), exactly like every other
  can't-decompose reason already in this function. This bounds the
  worst case to "correct total, wrong split, loudly flagged" — never an
  indefinitely-skipped row and never a silently wrong split.

This check is **scoped to the fare/tip (`process_payment`) branch only**.
`source == "cancellation_fee"` and `source == "scheduled_cancel_notice_fee"`
both `return` earlier in the function, unconditionally, before this gate is
ever reached — so cancellation-fee and notice-fee events keep projecting
exactly as before regardless of the pointed-at ride's `payment_status`
(which is legitimately `cancelled`, never `paid`, for those events). Two
regression tests (`test_cancellation_fee_unaffected_by_stuck_ride_payment_status`,
`test_notice_fee_unaffected_by_stuck_ride_payment_status`) pin this by
passing a `payment_status='processing'` ride into those branches directly.

`_RIDE_COLUMNS` gained `payment_status` so the batched ride fetch actually
returns the field the new gate reads.

### Why 6h, not a blanket `payment_status = 'paid'` filter in migration 287

Filtering the work-queue RPC itself on `payment_status = 'paid'` was
explicitly rejected by the ACTION_ITEMS write-up: it would permanently
exclude cancellation-fee/notice-fee events (whose ride is `cancelled`, not
`paid`) from ever projecting. The fix had to be source-aware, in Python,
inside `_decompose` — not a schema/RPC change. No migration was touched.

### Why 6h specifically (not 24h, not indefinite)

- `utils/payment_retry.py::retry_failed_payments` only waits 30 minutes
  before touching a `'processing'` ride, and for exactly this
  "Stripe-already-succeeded" case fixes `payment_status` on that very tick —
  so the legitimate recovery this gate is waiting on normally lands within
  ~30-60 minutes. 6h is a wide multiple of that, not a tight timeout.
- `utils/reconciliation.py::_check_leg_completeness` pages on-call when the
  projection work-queue **head** has not advanced in 24h (a stuck,
  un-projected event pins the head). Keeping the fallback well under 24h
  means a genuinely stuck row degrades and clears the head with several
  hours of margin, instead of racing that separate alarm.
- A ride `payment_retry` could not fix at all (MAX_RETRIES=3 exhausted, or
  the stale-invoice-sentinel path) already pages an admin well inside 6h, so
  nothing here waits past the point a human has already been notified.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `backend/utils/ledger_projection.py`.** Grepped
every caller/reader of `_decompose`, `project_pending_legs`,
`ledger_projection_loop`, and `_RIDE_COLUMNS` across `backend/`:

- `_decompose` — called only from `project_pending_legs` in the same file.
  No other module imports or calls it.
- `project_pending_legs` — called only from `ledger_projection_loop` in the
  same file (plus directly by this module's own tests).
- `ledger_projection_loop` — spawned once, by `backend/core/lifespan.py`
  (`_spawn("ledger_projection (15min)", ledger_projection_loop)`), and
  referenced by the lifespan watchdog's `_WATCHDOG_LOOP_NAMES` list (name
  string only, not the function) plus `utils/reconciliation.py`'s
  queue-head check (also name/RPC only, no import of this module). Neither
  reader executes `_decompose` or reads `_RIDE_COLUMNS`.
- `_RIDE_COLUMNS` — used only inside this file's own `get_rows(...,
  columns=_RIDE_COLUMNS)` call. Adding `payment_status` to the select list
  cannot affect any other query.
- `financial_events`, `financial_event_entries`, and `rides` are all
  **read-only** from this module's perspective — the projection never
  writes to `rides` or `financial_events`, only to `financial_event_entries`
  via `ledger_service.write_legs`, which is unchanged.

**Interaction found and accounted for:** `utils/reconciliation.py`'s daily
`_check_leg_completeness` alerts if the work-queue head hasn't advanced in
24h. Before this fix, every leg-less header (including a stuck one) was
decomposed and written on its very first eligible tick, so the head always
advanced immediately. After this fix, a genuinely stuck fare event can now
occupy the head for up to `_SETTLEMENT_FALLBACK_SECONDS` (6h) while being
skipped-and-retried. This is *slower* than before but still comfortably
inside reconciliation's 24h alarm window (6h vs 24h, ~4x margin), so it
should never trip that alarm on its own — and if a ride is stuck long
enough to matter, `_finalize_card_settlement`'s own `ledger_service.escalate`
/ payment_retry's admin-exhausted alert will already have paged on-call for
the underlying stuck-ride incident well before 6h elapses. Flagging this
interaction explicitly rather than asserting "no impact" — it is a real,
bounded behavior change to queue-head advancement timing under one specific
failure mode, not a regression to the happy path (which is untouched: a
normal `payment_status='paid'` ride decomposes exactly as it did before this
change, on the very same tick).

**No other regression risk found:** `rides.driver_earnings` and
`financial_events.delta_cents` (the fields feeding T4A/driver statements and
the tax ledger) are never written by this module and are unaffected by this
change, consistent with B20's original severity assessment. The change is
purely about which numbers the *internal double-entry projection overlay*
reads and when — it cannot change what a rider is charged or what a driver
is paid.

## 5. User-experience effect

None. This is an internal accounting/reporting overlay
(`financial_event_entries`) with no rider, driver, corporate-admin, or
internal-admin-facing surface. No API response, notification, or UI screen
reads this table today (grepped: only `utils/reconciliation.py`'s balance
check and this module itself read it). Not visible mid-session to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/ledger_projection.py` | Added `_fare_ready_to_decompose()` gate + `_SETTLEMENT_FALLBACK_SECONDS` constant; default fare branch of `_decompose` now calls the gate before reading `driver_earnings`/`tax_amount`; `_RIDE_COLUMNS` gained `payment_status` | Source-aware fix for B20 — stop misbooking a stuck-unpaid ride's tip into `platform_revenue` |
| `backend/tests/test_ledger_projection.py` | Added `payment_status` (default `'paid'`) to the `_ride()` fixture; added 6 new tests covering the stuck/within-grace/timed-out/resolved cases and the cancellation-fee/notice-fee non-regression cases; added a `project_pending_legs`-level end-to-end test | Regression coverage for the new gate, per CLAUDE.md testing conventions |
| `docs/change-log/2026-08-10-b20-ledger-projection-tip-fix.md` | New file (this log) | Mandatory Change Impact & Risk Log for a live-tested payments-surface change |
| `ACTION_ITEMS.md` | B20 entry flipped `[ ]` → `[x]`, CLOSED status summary added | Close out the tracked backlog item |

## 7. Before / after

```python
# Before (backend/utils/ledger_projection.py::_decompose, default branch)
    # Default: a ride fare settlement (source process_payment, or webhook
    # settles that reuse it). Decompose from the ride row.
    if not ride:
        return _degraded_legs(event_type, amount), True, "ride_missing"
    legs = ledger_service.build_charge_legs(
        total_cents=amount,
        driver_cents=to_cents(ride.get("driver_earnings")),
        tax_cents=to_cents(ride.get("tax_amount")),
        promo_cents=to_cents(ride.get("discount_amount")),
    )
```

```python
# After
    # Default: a ride fare settlement (source process_payment, or webhook
    # settles that reuse it). Decompose from the ride row.
    if not ride:
        return _degraded_legs(event_type, amount), True, "ride_missing"

    # B20: rides.driver_earnings / rides.tax_amount only reflect the tip
    # split once payment_status has actually reached 'paid'. Cancellation-fee
    # and notice-fee events never reach this branch (both return above,
    # unconditionally, regardless of ride.payment_status) — this gate is
    # scoped to fare/tip events only, by construction.
    ready, timed_out = _fare_ready_to_decompose(ride, event)
    if not ready:
        if timed_out:
            return _degraded_legs(event_type, amount), True, "payment_not_settled_timeout"
        return [], False, "awaiting_payment_settlement"

    legs = ledger_service.build_charge_legs(
        total_cents=amount,
        driver_cents=to_cents(ride.get("driver_earnings")),
        tax_cents=to_cents(ride.get("tax_amount")),
        promo_cents=to_cents(ride.get("discount_amount")),
    )
```

## 8. Rollback plan

No migration, no feature flag, no data was written or mutated by this
change — it only changes when/how a not-yet-projected
`financial_event_entries` row gets computed. If this regresses:

- **`git revert`** the commit on this branch (or via a follow-up PR) — this
  is one of the rare cases where a plain code revert genuinely is the
  rollback plan, because the change is read-path-only logic inside a
  15-minute idempotent projection loop with `UNIQUE(event_id, account,
  side)` protecting every write. No Stripe charge, wallet delta, or ride
  state is touched by this file, so there is no live-data cleanup to do on
  top of the code revert.
- If a revert is not immediately available (e.g. mid-incident), the
  projection loop can be paused entirely by flipping the existing
  `ledger_double_entry_enabled` app_settings flag off — `project_pending_legs`
  returns immediately with an all-zero stats dict when
  `ledger_service.double_entry_enabled()` is false, with no redeploy
  required (existing mechanism, not new).

## 9. Verification performed

- [x] Automated tests run — **unit only** (all in-process, `mock_supabase_client`-style
  mocking per `backend/tests/conftest.py` conventions; nothing here talks to
  Supabase, so there is no integration/e2e tier for this change).
  - Ran a **real venv + pytest**, not just `tsc`/type-check equivalent
    reasoning: created `.venv` with `python3.11 -m venv .venv`, installed
    `backend/requirements.txt`, then ran:
    - `pytest backend/tests/test_ledger_projection.py -q --no-cov` →
      **26 passed, 0 failed** (20 pre-existing + 6 new).
    - `pytest backend/tests/test_ledger_projection.py
      backend/tests/test_replay_safety_payment_loops.py
      backend/tests/test_payment_retry.py
      backend/tests/test_payment_retry_coverage.py
      backend/tests/test_atomic_settle.py
      backend/tests/test_coverage_payments.py -q --no-cov` → **148 passed, 0
      failed** (broader payment/loop surface this touches by proximity).
    - `pytest backend/tests/test_reconciliation.py -q --no-cov` → **37
      passed, 0 failed** (the module flagged as having a bounded timing
      interaction in section 4).
  - `ruff check` and `ruff format --check` on both modified `.py` files:
    clean.
- [x] Blast-radius grep performed — see section 4; searched for every
  caller of `_decompose`, `project_pending_legs`, `ledger_projection_loop`,
  and every consumer of `_RIDE_COLUMNS` across `backend/`.
- [x] Reviewed against CLAUDE.md conventions — money arithmetic (no new
  arithmetic added; `to_cents`/`build_charge_legs` unchanged and still the
  only place cents are computed), background-loop replay-safety (no change
  to the lock/throttle contract; the gate only changes what a single
  `_decompose` call returns, which was always allowed to skip/degrade), and
  "do not silently swallow errors" (the new skip/degrade paths are both
  explicit, both counted in `stats`, and the degrade path is still loudly
  escalated exactly like every other degraded reason).
- [ ] Manual repro steps followed in staging — **not done** (see below).
- [ ] Feature-flagged — **not applicable/not done** (see below).

## 10. What was NOT verified

- **Not exercised against a real Supabase instance or staging.** All
  coverage is unit-level with `_event()`/`_ride()` dict fixtures and mocked
  `db_supabase.rpc`/`get_rows`/`ledger_service.write_legs`. The RPC's actual
  30-minute filter behavior (migration 287, already-applied/unchanged) was
  read but not re-verified against a live database in this change.
- **Not feature-flagged.** This is a pure bugfix to an internal, read-only
  (from the rider/driver/admin's perspective) accounting overlay with no
  user-visible surface and no existing flag gating `_decompose`'s branching
  logic — `ledger_double_entry_enabled` gates the whole loop, not this
  specific fix, and flipping it off would also disable the correct
  (non-buggy) 99%+ of decompositions, which is a strictly worse rollback
  than a plain revert. Given CLAUDE.md's guidance to prefer flags for
  "user-visible and non-trivial" changes, and this having no user-visible
  surface at all, a flag was judged unnecessary rather than skipped by
  oversight.
- **The 6-hour fallback window is a judgment call, not something empirically
  tuned against real stuck-ride incident data** (no history of this
  particular failure mode's duration was available to consult). The
  reasoning tying it to `payment_retry`'s ~30-60 minute typical recovery and
  `reconciliation.py`'s 24h alarm is documented inline in
  `_SETTLEMENT_FALLBACK_SECONDS`'s comment for whoever revisits the number
  later.
- **No load/performance testing.** The new gate is an in-memory dict lookup
  plus one `datetime` subtraction per fare event per tick — no new DB call,
  no change to the batch size or query shape — so no measurable P95 impact
  is expected on the (already background, not user-request-path) projection
  loop, but this was reasoned about, not benchmarked.
- **No visual/UI verification** — not applicable; this change has no UI.
