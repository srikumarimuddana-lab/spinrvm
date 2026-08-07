# Change Impact & Risk Log — Double-entry legs via background projection

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | W2 in the error-free plan; follows migration 287 (`ebec1e9`) |

## 1. Issue / gap identified

Double-entry legs (`financial_event_entries`, migration 286) were written inline in the payment request path. Three problems: settlement latency carries the leg write; a request-path leg failure needs its own error handling per call site; and inline writes can only cover *new* events — the entire pre-existing `financial_events` history would never get legs.

There was also a correctness bug in the inline design: settlement writes the ledger header **before** `update_ride` lands the tip delta on `rides.driver_earnings`, so inline decomposition read a pre-tip ride row and would book the tip into `platform_revenue`.

## 2. Root cause

Inline legs were the minimal first implementation (2026-08-06). The projection was always the intended end state once the substrate existed; the tip-race was discovered during design review of this change.

## 3. Fix / remediation

Leg-writing moves to a background **projection loop** (`utils/ledger_projection.py`, every 15 min): fetch oldest-first headers lacking legs via the migration-287 RPC (which enforces a 30-minute grace window against the tip-race and filters non-decomposable rows), decompose per `metadata.source`, batch-insert via `ledger_service.write_legs` (now public, `check_flag=False` — the flag is checked once per tick).

Decomposition: fares from the ride row; cancellation fees from `metadata.fee_admin/fee_driver` (added in `92771d2`); notice fees all-platform (correct, not degraded); refunds from `metadata.tax_reversed`. **Undecomposable events are booked degraded** — whole amount to `platform_revenue`, escalated with the new non-paging `spinr_alert=ledger_legs_degraded` — rather than skipped, because a skipped row would occupy the head of the oldest-first queue forever and starve all newer events.

The request-path writers (`record_payment_event`, `record_refund_event`) no longer pass `legs=`. **Single-writer invariant: only the projection writes `financial_event_entries`** (pinned by test).

Backfill is free: oldest-first at 200/tick ≈ 19k events/day walks all history once the flag turns on.

Daily reconciliation gains `_check_leg_completeness()`: headers >24 h old still in the work queue while the flag is on → ERROR alert (a dead/wedged projection loop is otherwise invisible — the unbalanced-view check can only see legs that exist).

## 4. Risk & impact on existing functionality

**Blast radius:** `ledger_service` (write path refactor), `payment_service` (two writers stop passing legs), `lifespan.py` (one new loop + watchdog entry), `reconciliation.py` (additive check). Consumers checked:

- **Request path gets strictly faster**: settlement now writes exactly one ledger row, no legs. No response-shape change.
- **Replay safety (loop runs on every replica):** correctness comes from `UNIQUE(event_id, account, side)` + one whole-batch `insert_many` per event — a concurrent duplicate fails whole-statement with 23505, which the writer already treats as success. The Redis lock (`spinr:ledger:projection:lock`, TTL = interval × 1.5) is a throttle only, per the `payment_retry` doctrine. Pinned by a replay-safety test.
- **Flag off (production today): the loop is a no-op** — one settings read per 15 min, zero DB queries. Flag on requires migrations 286 + 287 applied first; if the RPC is absent the loop logs one warning and idles (partial-deploy guard, tested).
- **`record_event`'s `legs` param is kept** (direct callers/tests) — nothing in prod passes it.
- **Watchdog:** loop heartbeats on both the acquired and lock-skipped paths (tested), name registered in `_WATCHDOG_LOOP_NAMES`.
- Reconciliation's Stripe-vs-DB comparison is untouched; both new checks are appended after it and never raise.

## 5. User-experience effect

Nobody — backend-only. Marginal settlement-latency improvement.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/ledger_projection.py` | **New.** Projection loop + `_decompose` + degraded fallback | Derive legs async |
| `backend/services/ledger_service.py` | `_write_legs` → public `write_legs(..., check_flag=True) -> bool`; new `ALERT_LEGS_DEGRADED` tag | Reuse batched write + retry from the loop |
| `backend/services/payment_service.py` | Both writers stop passing `legs=`; docstrings updated | Single-writer invariant + tip-race fix |
| `backend/core/lifespan.py` | `_spawn("ledger_projection (15min)", ...)` + `_WATCHDOG_LOOP_NAMES` entry | Registration |
| `backend/utils/reconciliation.py` | `_check_leg_completeness()` appended to the daily run | Dead-loop detection |
| `backend/tests/test_ledger_projection.py` | **New.** 16 tests: per-source decomposition, degraded paths, flag-off, missing-RPC, per-item isolation, batched ride fetch, single-writer invariant | |
| `backend/tests/test_replay_safety_payment_loops.py` | +2: duplicate-batch-as-written; heartbeat-on-lock-skip | Pin the replay contract |

## 7. Before / after

```python
# Before — legs written inline during settlement (and from a pre-tip ride row)
legs = ledger_service.build_charge_legs(amount_cents, ride.driver_earnings, ride.tax_amount)
await ledger_service.record_event(..., legs=legs)
```

```python
# After — settlement writes the header only; the projection loop derives legs
# ≥30 min later from the settled ride row (tip delta already applied)
await ledger_service.record_event(...)          # request path
stats = await project_pending_legs()            # background, every 15 min
```

## 8. Rollback plan

- `ledger_double_entry_enabled = false` (app_settings, no deploy): loop no-ops within the 60 s settings cache TTL. Headers, reconciliation, and settlement are unaffected either way.
- Full revert: revert this commit; migration 287 can stay (inert function) or be dropped per its header SQL.

## 9. Verification performed

- New: `test_ledger_projection.py` 16 passed; replay-safety additions 12 passed (file total).
- Affected battery (`test_ledger_service`, `test_ledger_projection`, `test_ledger_pii`, `test_refund_ledger`, `test_coverage_rides`, `test_process_payment_card`, `test_settle_card_capture`, `test_reconciliation`, `test_replay_safety_payment_loops`, `test_core_lifespan_coverage`) — **282 passed**.
- `ruff check` + `ruff format` clean on every touched file.
- Grep confirmed no test or prod caller references the removed `_write_legs` name; no lifespan test pins the loop list.
- Full backend suite (~10k tests): **started before the push, still running when the
  branch was pushed** (feature branch, not a merge). Result recorded in a follow-up
  commit — see `docs/change-log/2026-08-07-full-suite-result.md`. Targeted batteries
  for this change were green before commit (listed above).

## 10. What was NOT verified

- The RPC + loop have never run against a real Postgres (mocked `db_supabase.rpc`/`get_rows` only). The 30-minute grace window and `delta_cents <> 0` filter are asserted in SQL review + pglast parse, not runtime.
- Degraded-entry volume on real historical data is unknown — historical cancellation fees (pre-`92771d2`, no fee-split metadata) will all project degraded by design; the count lands in Sentry via `ledger_legs_degraded` when the flag first turns on.
- `_check_leg_completeness` age-parse edge cases exercised in unit tests only.
