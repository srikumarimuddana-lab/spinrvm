# Change Impact & Risk Log — Code-review fixes: promo legs, backfill alerting, projection cadence

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | Findings 1–3 of the code review of PR #3464 (post-audit pass) |

## 1. Issue / gap identified

Three defects found reviewing this branch's own double-entry work, after the four
auditor passes. All three are **flag-on-only** — none is reachable in production
today, where `ledger_double_entry_enabled` is off.

**(1) Promo-discounted rides project degraded, losing the split and paging per ride.**
`driver_earnings` is derived pre-discount (`total_fare - admin_earnings`,
`fare_service.py:221`) while the rider is charged post-discount
(`grand_total = total_fare + area_fees + tax - discount`, `fare_service.py:388`).
The projection's residual was therefore `area_fees + admin_earnings - discount`,
which goes **negative on any promo larger than the fee floor**. `build_charge_legs`
then refused to build legs at all, and `_decompose` booked the entire charge
degraded to `platform_revenue` — losing `driver_payable` and `tax_payable` and
firing `ledger_legs_degraded` on every such ride. Confirmed by running `_decompose`
directly (fare $20.00 incl. $2.50 booking fee, tax $1.00):

```
PROMO $5  -> degraded=True  reason=amounts_inconsistent
   stripe_receivable debit 1600 / platform_revenue credit 1600     ← driver + tax gone
NO PROMO  -> degraded=False
   stripe_receivable 2100 / driver_payable 1750 / tax_payable 100 / platform_revenue 250
```

Not an edge case: promo codes are a live surface (`routes/promotions.py`) and
`rides.discount_amount` is persisted at booking (`routes/rides/booking.py:1097`).

**(2) The dead-loop alarm cries wolf for the whole backfill.**
`_check_leg_completeness` alerted at ERROR on any projectable header >24 h old with
no legs. That is precisely what the intended backfill looks like: when the flag
first turns on, every historical header is leg-less and older than any age
threshold. The daily reconciliation would have logged
"projection loop dead or failing" every night for the entire drain.

**(3) The 15-minute loop actually ticks every ~30 minutes.**
The Redis throttle lock used `TTL = interval * 1.5` (1350 s) against a sleep of
`interval ± 10%` (810–990 s). The pod that ran the last tick woke to find its **own**
key still alive, failed `SET NX`, skipped, and slept another full interval.

## 2. Root cause

1. `build_charge_legs` was written from the shape of an undiscounted ride, where
   `grand_total ≥ driver_earnings + tax` always holds. `promo_expense` was already
   in the chart of accounts (migration 286) and in `LEDGER_ACCOUNTS` — it was
   defined for exactly this entry and no builder ever emitted it. `discount_amount`
   was in neither `_RIDE_COLUMNS` nor `_charge_event_metadata`, so the projection
   could not see the discount at all. Note the **wallet** settle path's ledger
   metadata does record `discount_amount` + `promo_code` (`payment_service.py:341`)
   — the card path was the asymmetric one.
2. Depth and age were used as a proxy for liveness. They cannot distinguish
   "backfilling" from "dead"; only movement can.
3. Copied verbatim from `payment_retry.py:629-631`, whose own comment states the
   intent and gets the arithmetic backwards: *"TTL is 1.5× interval so a real lock
   expires before the next election."* It does not.

## 3. Fix / remediation

- **`build_charge_legs(total, driver, tax, promo_cents=0)`** — a `DR promo_expense`
  leg for the absorbed discount. Residual becomes `area_fees + admin_earnings`
  regardless of discount size: the platform earns fees **gross** and expenses the
  promo separately, which is the accounting answer and what the account exists for.
  The consistency guard survives — promo shifts the threshold, it does not remove it.
- **`_RIDE_COLUMNS` now selects `discount_amount`**, pinned by a test so a trimmed
  column list fails loudly rather than silently reverting to degraded projection.
- **`_check_leg_completeness` measures progress**: the work queue drains
  oldest-first, so the head event id is carried between daily runs in Redis and the
  alert fires only when it has not moved in 24 h. Nothing can pin the head
  legitimately — an undecomposable event is booked DEGRADED rather than skipped,
  precisely so it leaves the queue.
- **`_LOCK_TTL_SECONDS = interval * 0.85`**, safely under the shortest possible
  sleep, with `_JITTER_FRACTION` extracted so the relationship is expressed in code
  rather than in two unrelated literals.
- **`ACTION_ITEMS.md` B21** — the same TTL idiom is wrong in four other loops
  (`payment_retry`, `driver_claim_reaper`, `offer_expiry_reaper`,
  `orphaned_hold_reconciler`). Filed, not fixed here: each has its own interval and
  multi-replica behaviour, and `payment_retry` is a money path that deserves its own
  change rather than a drive-by.

## 4. Risk & impact on existing functionality

**Blast radius: one leg builder, one decomposition branch, one alerting function,
one loop constant. No schema, no migration, no request path.**

- **`build_charge_legs` has two callers**, both in `_decompose`
  (`ledger_projection.py`) — the fare branch (now passes `promo_cents`) and the
  cancellation-fee branch (unchanged, 3-arg). Grepped: no other production caller.
  `promo_cents=0` reproduces the previous output byte-for-byte, pinned by a test.
- **No migration needed.** `promo_expense` is already in migration 286's `account`
  CHECK constraint and in `LEDGER_ACCOUNTS`; this is the first code path to emit it.
  The user applied 286–291 to a real Postgres on 2026-08-08, so the constraint that
  has to accept the new value is already verified present.
- **`_check_leg_completeness` never raises** and is appended after the
  Stripe-vs-ledger comparison, which is untouched. It now writes one Redis key
  (`spinr:ledger:projection:queue_head`, 8-day TTL). Redis is optional in this
  codebase; with the in-process fallback the marker is lost on restart, costing one
  run's blind spot — stated in the docstring.
- **Shorter lock TTL** leaves a brief window each cycle with no holder, so two
  replicas can occasionally run the same projection tick. Harmless by construction:
  correctness comes from `UNIQUE(event_id, account, side)` plus the whole-batch
  insert (a concurrent duplicate fails whole-statement with 23505, which the writer
  treats as written), and this lock has only ever been a throttle. Pinned by the
  existing replay-safety test.
- **Everything here is behind `ledger_double_entry_enabled`, which is off in
  production.** The flag-off path executes none of this code.
- Reconciliation's Stripe-vs-DB comparison, the `financial_events` header write,
  settlement, receipts, and the atomic-settle RPC are all untouched.

## 5. User-experience effect

Nobody. No rider, driver, corporate-admin or internal-admin surface changes. The
only observable difference is to on-call: fewer false ERROR alerts during backfill,
no `ledger_legs_degraded` page per promo ride, and a dead-loop alert that means
something when it fires.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/ledger_service.py` | `build_charge_legs` gains `promo_cents`; emits `DR promo_expense` | Promo rides were degrading |
| `backend/utils/ledger_projection.py` | `_RIDE_COLUMNS` + `discount_amount`; `_decompose` passes `promo_cents`; `_LOCK_TTL_SECONDS` / `_JITTER_FRACTION` | Fix 1 + fix 3 |
| `backend/utils/reconciliation.py` | `_check_leg_completeness` rewritten to a progress check; queue-head marker constants; function-body dual imports | Fix 2 |
| `backend/tests/test_ledger_service.py` | +6: promo balance, gross residual, default-zero equivalence, negative promo, guard survival | |
| `backend/tests/test_ledger_projection.py` | +3: promo ride not degraded, `_RIDE_COLUMNS` pin, lock-TTL invariant | |
| `backend/tests/test_replay_safety_payment_loops.py` | +1: virtual-clock two-wake re-acquisition | |
| `backend/tests/test_reconciliation.py` | +7: first tests for `_check_leg_completeness` | Function had zero coverage |
| `ACTION_ITEMS.md` | B21 | Same TTL bug in four other loops |

## 7. Before / after

```python
# Before — residual = total - driver - tax, negative on any real promo
legs = build_charge_legs(total_cents=1600, driver_cents=1750, tax_cents=100)
# -> []  ->  _decompose books DEGRADED: DR receivable 1600 / CR platform_revenue 1600
```

```python
# After — the discount is a debit, so the entry balances and keeps its split
legs = build_charge_legs(total_cents=1600, driver_cents=1750, tax_cents=100, promo_cents=500)
# -> DR stripe_receivable 1600, DR promo_expense 500
#    CR driver_payable 1750, CR tax_payable 100, CR platform_revenue 250   (2100 = 2100)
```

```python
# Before — alert on absolute age; every historical row qualifies during backfill
if created_dt < now - 24h: stale.append(r)
if stale: logger.error("... projection loop dead or failing")
```

```python
# After — alert only when the oldest-first queue head stops moving
previous = await redis_get(_QUEUE_HEAD_KEY)
await redis_set(_QUEUE_HEAD_KEY, head_id, _QUEUE_HEAD_TTL_SECONDS)
if previous is not None and previous == head_id:
    logger.error("... made NO progress in 24h — head still {head_id}")
```

```python
# Before — TTL 1350s vs sleep 810-990s: the holder cannot re-acquire on its own wake
await redis_set_nx(_LOCK_KEY, _pod_id(), int(LEDGER_PROJECTION_INTERVAL_SECONDS * 1.5))
```

```python
# After — TTL 765s, below the shortest possible sleep
_LOCK_TTL_SECONDS = int(LEDGER_PROJECTION_INTERVAL_SECONDS * (1 - _JITTER_FRACTION - 0.05))
await redis_set_nx(_LOCK_KEY, _pod_id(), _LOCK_TTL_SECONDS)
```

## 8. Rollback plan

`ledger_double_entry_enabled = false` in `app_settings` (DB-backed, no deploy) takes
all three changes out of the executing path within the 60 s settings cache TTL: the
projection loop early-returns, `_check_leg_completeness` returns before touching
Redis, and no legs are built. `financial_events` — the tax record and the input to
the daily Stripe reconciliation — is unaffected either way.

No data to unwind. If legs were already written with a `promo_expense` leg and the
change is reverted, those rows stay valid and balanced; the account is in migration
286's CHECK constraint independently of this commit. The Redis marker key expires on
its own in 8 days.

## 9. Verification performed

- **The exact repro from the review re-run against the fixed code**: the $5-promo
  ride now returns `degraded=False`, five legs, debits 2100 = credits 2100.
- **Both new cadence tests confirmed to FAIL against the old TTL** (temporarily
  restored, then reverted): the virtual-clock two-wake test and the arithmetic
  invariant.
- **Four of the seven new `_check_leg_completeness` tests confirmed to FAIL against
  the previous implementation** (old file restored from `HEAD`, run, restored),
  including the backfill regression case.
- Targeted battery (`test_reconciliation`, `test_ledger_service`,
  `test_ledger_projection`, `test_atomic_settle`, `test_replay_safety_payment_loops`,
  `test_core_lifespan_coverage`) — **115 passed**.
- `ruff check` + `ruff format --check` clean on all touched files.
- Blast-radius grep for `build_charge_legs` callers before writing the fix: two, both
  in `_decompose`.
- **Full backend suite run to completion BEFORE the push** — result in §11.

## 10. What was NOT verified

- **No end-to-end run with `ledger_double_entry_enabled` on.** The promo legs have
  never been written to a real `financial_event_entries` row; the `promo_expense`
  value is verified to be in migration 286's CHECK constraint by reading the applied
  migration, not by inserting a row with it. This is the same standing boundary the
  rest of the branch carries (see `2026-08-08-migration-verification-result.md`).
- **The projection loop has still never run against real data**, so the real-world
  degraded-entry volume after this fix is unknown. The promo class is now expected to
  project cleanly; historical cancellation fees predating the fee-split metadata will
  still degrade by design.
- **The cadence fix is proven against a virtual clock, not a real one.** No timing
  measurement against a real Redis was taken, and the multi-replica behaviour (how
  often two pods overlap in the no-holder window) is reasoned from the TTL/sleep
  arithmetic, not observed.
- **The Redis marker's behaviour under the in-process fallback across a real restart
  was not exercised** — the one-run blind spot is reasoned about and documented, not
  reproduced.
- **Finding 4 from the review is NOT fixed**: `_check_entry_balance` filters the
  unbalanced view on `MIN(created_at)`, an aggregate output, so Postgres cannot push
  the predicate below the `GROUP BY` and the nightly run full-aggregates
  `financial_event_entries`. Deferred by scope, not by judgement — it is a
  performance issue that grows with the table (~20 M rows/year at projected volume).
  Findings 5–7 (non-atomic `extra_ride_fields` follow-up, cross-module `_escalate`,
  unconfigured-Supabase counters) are likewise unaddressed.
- `_check_entry_balance` still has **zero test coverage**; only
  `_check_leg_completeness` gained tests here.

## 11. Full suite result

`pytest backend/tests` run to completion **before** the push (the branch's earlier
lesson: on a branch adding new modules, the suite has to gate the push, not trail
it — see `2026-08-07-full-suite-result.md`).

```
10035 passed, 8 skipped, 1 xfailed, 20 warnings in 527.72s (0:08:47)
```

Exit code 0, zero `FAILED`/`ERROR` lines. Baseline on this branch before these three
fixes was **10,019 passed** — the delta of exactly **+16** is the 16 tests added here
(5 in `test_ledger_service.py`, 3 in `test_ledger_projection.py`, 1 in
`test_replay_safety_payment_loops.py`, 7 in `test_reconciliation.py`), with no
pre-existing test changing state.
