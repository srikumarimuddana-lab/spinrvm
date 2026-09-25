# Change Impact & Risk Log — stuck `reserved` payouts surfaced by the daily Stripe reconcile

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01PPGd1wK6WRzbtxcGj3qNkX) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/fix-stuck-reserved-payouts`, commit `88973ca` (local only, not pushed) |
| Related issue or gap ID | Finding: reserve-then-transfer rows in `routes/drivers/payouts.py` can strand in `reserved` |

## 1. Issue / gap identified

A `payouts` row left in `status='reserved'` by a process death during a payout request stays `reserved` indefinitely, silently reducing the driver's payable balance, and no job or alert surfaces it.

## 2. Root cause

`routes/drivers/payouts.py` uses reserve-then-transfer (WS-7 finding 4). Both `_request_payout_legacy` (~L945) and `request_instant_payout` (~L1176) `INSERT` the row as `reserved` **before** `stripe.Transfer.create`, then write a terminal/next status (`completed`/`pending`/`failed`/`transfer_completed`) afterwards. A crash, deploy, or worker timeout between those two writes leaves no code path that ever revisits the row:

- `get_driver_balance` (and `routes/admin/drivers.py`'s owed/paid math) counts every status except `reversed`/`failed` as money-out, so the row keeps deducting.
- Migration 250's partial unique index `idx_payouts_one_inflight_per_driver` then blocks **every new payout for that driver** (409 "A payout is already in progress"), so the driver cannot cash out at all.
- `utils/auto_payout.sweep_stale_reserved` sweeps only `payout_type='auto'` rows. `stripe_reconcile._reconcile_payouts` covers only `requires_manual_review=true` and `transfer_completed`. Neither looks at a `standard`/`instant` row in `reserved`.

## 3. Fix / remediation

A new **read-only** check, `_reconcile_stuck_reserved_payouts()`, runs as step 3i of the daily `stripe_reconcile` tick (02:00 UTC, one replica under the existing Redis leader lock):

- Query: `payouts` with `status='reserved'` and `created_at < now-1h`. The filter runs server-side. It selects only `id,driver_id,payout_type,status,created_at`, orders oldest first, and is limited to 500 rows. It makes one query and no per-row lookups, so there is no N+1.
- Rows are checked a second time in Python (status must still be `reserved`, and the row must be older than 1h) instead of trusting the filter. This matches the sibling checks.
- For each stuck row the check does three things. It writes a `logger.error` line containing only the payout id, driver id, payout type, `created_at` and the Stripe idempotency key, with `extra={"domain": "payments", "driver_id": ...}`. It increments `spinr_payment_stuck_reserved_payouts_total{payout_type}`. It adds a `PAYOUT_STUCK_RESERVED` discrepancy to the `audit_logs` summary, which also gets a new `payouts_stuck_reserved` count.
- If the query fails, the check logs at error level with `exc_info`, increments `spinr_payment_reconcile_check_failed_total{check="stuck_reserved_payout"}`, and returns `None`. The summary then records `None` (check failed) rather than 0. The rest of the tick continues. This is the same contract as `_reconcile_orphan_holds`.
- If the 500-row limit is reached, the check logs at error level and increments the check-failed metric, so a truncated scan never looks like a complete one.
- The check never transitions a row. Only Stripe can say whether the Transfer happened. Re-opening the row risks paying the driver twice, and marking it `failed` risks the driver losing money. A human confirms the outcome.

**Stripe lookup key (spec item 3).** The Stripe transfer id is **not** stored on a `reserved` row. `stripe_transfer_id`/`stripe_payout_id` are `NULL` at reserve time, and the update that fills them is the same update that moves the row out of `reserved`. The idempotency key is **not stored either**, but it is deterministic in `payouts.py`, so the check derives it and logs it:
- `payout_type='instant'`: `instant-payout-transfer-{payout_id}`
- `payout_type='standard'` or NULL: `payout-transfer-{payout_id}`
- `payout_type='auto'`: no single key, because `auto_payout.py` keys are attempt-scoped (`...-r{n}`). The log line tells the operator to search Transfers by `metadata.payout_id`, which the auto path sets. Standard and instant Transfers carry **no** metadata, so for those rows the idempotency key (in Stripe's request logs) or destination account + amount + time are the only ways to find the Transfer.

**Scope choice, stated as an assumption.** The check covers **all** `payout_type`s, including `auto`, and labels the metric by type. An `auto` row still `reserved` at 02:00 UTC is either retrying a `balance_insufficient`/rate-limit failure (a platform-level problem) or escalated to `needs_manual_reconcile`. After escalation, the auto sweep only re-reports that row at info level. Both cases are real money a human should see. If the `auto` rows turn out to be noise, an alert rule can filter on `payout_type!="auto"` without a code change.

**Alternative rejected: auto-transition stale rows.** One option was to check Stripe (list Transfers for the destination, match on amount and time) and move each row to `completed` or `failed` automatically. It was rejected for three reasons. Standard and instant Transfers carry no `payout_id` metadata, so any match is a heuristic. A wrong match either pays the driver twice or strands their money. And the spec requires a human to confirm. Detection-only is also the existing pattern in this module (3c, 3d, 3f, 3g, 3h).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to backend `utils/stripe_reconcile.py`, and additive.** The check makes no writes to `payouts` or any other table, apart from the extra fields in the existing single `audit_logs` summary insert.
- The `stripe_reconcile` module is imported by `core/lifespan.py`, which spawns `stripe_reconcile_loop`, and by `utils/payment_retry.py`, which imports `_maybe_heal_stuck_processing` and `_truthy`. Neither of those symbols changed. The other hits of a repo-wide `grep -rln stripe_reconcile backend` are comments only: `services/payment_service.py`, `repositories/wallet_repo.py`, `routes/webhooks.py` and `routes/admin/settings.py`.
- Other readers and writers of `payouts.status='reserved'`: `routes/drivers/payouts.py` (writer, not edited, because PR #5791 is modifying it), `utils/auto_payout.py` (writer and sweep), `routes/drivers/earnings.get_driver_balance` and `routes/admin/drivers.py` (readers). None of them is affected by a read. PR #5791's diff was checked read-only via `git fetch origin pull/5791/head`: it adds an instant-payout velocity cap and does not change the reserve INSERT or the idempotency-key formats that this check derives. A stuck `reserved` row will also count against #5791's cap, which is one more reason to surface it.
- Nothing parses the `audit_logs` `details` programmatically. A grep for `stripe_reconciliation` and `discrepancy_detail` found no hits outside the module and its tests. Adding the `payouts_stuck_reserved` key is additive.
- Metrics: the existing `_CHECK_NAMES` gains `stuck_reserved_payout`, which pre-registers one more zero series. One new counter family is added, pre-registered at 0 for `standard`, `instant` and `auto`, following the module's convention so that `increase()` sees the first real hit. The name uses the singular `spinr_payment_` prefix, like its sibling metrics and CLAUDE.md's `spinr_payment_settlement_total`.
- **Replay safety:** the loop runs on every replica but only the holder of the Redis lock ticks, and the check only reads. If the lock fails open, the worst case is a duplicate error log line and a duplicate counter increment. It can never cause a duplicate write.
- There is no interaction with the ride state machine, insurance periods or wallet deltas.

## 5. User-experience effect

- Riders, drivers and corporate admins see no change. The effect is backend-only and nothing is visible mid-session.
- Internal ops and on-call staff get a new `PAYOUT_STUCK_RESERVED` error line in logs and Sentry, tagged `domain=payments` through the existing `extra` → Sentry-tag promotion in `utils/sentry_scrub.tags_from_log_extra`. They also get a new counter they can alert on, and the new entries in the daily `audit_logs` reconcile summary.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/stripe_reconcile.py` | Added `_reconcile_stuck_reserved_payouts()`, `_payout_transfer_idempotency_key()`, constants, pre-registered metric series, step 3i in the tick, and the `payouts_stuck_reserved` summary key | Surfaces stranded `reserved` rows read-only |
| `backend/tests/test_stripe_reconcile_stuck_reserved_payouts.py` | New: 12 tests, 7 functions (1 parametrized over 5 statuses) | Regression coverage per spec item 6 |
| `docs/change-log/2026-09-25-stuck-reserved-payouts.md` | This file | CLAUDE.md Change Impact Log gate |

## 7. Before / after

The code is additive: the new step runs inside the existing current-state block of `_run_reconciliation_tick`.

```python
# Before — nothing reads payouts.status='reserved' outside auto_payout's auto-only sweep
        cc_counts, cc_flagged = await _detect_cancelled_captured()
        discrepancies.extend(cc_flagged)
```

```python
# After
        cc_counts, cc_flagged = await _detect_cancelled_captured()
        discrepancies.extend(cc_flagged)

        stuck_reserved = await _reconcile_stuck_reserved_payouts()   # read-only
        if stuck_reserved is not None:
            discrepancies.extend(stuck_reserved)
```

Concrete scenario:
- **Before.** A driver taps Instant Payout for $80.00. The reserve INSERT commits, and the Fly machine is replaced by a deploy before `stripe.Transfer.create` returns. The row stays `reserved` and the driver's payable balance drops by $80. Every later cash-out attempt returns 409, and no log line, metric or audit row ever mentions it.
- **After.** At the next 02:00 UTC tick, the reconcile logs `PAYOUT_STUCK_RESERVED payout=<id> driver=<id> payout_type=instant ... stripe_idempotency_key=instant-payout-transfer-<id>`, increments the counter, and records the row in `audit_logs`. The row itself is unchanged. An operator searches Stripe for that key or for Transfers to the driver's connected account, then resolves the row by hand: `completed` with the transfer id if the money moved, `failed` if it did not.

## 8. Rollback plan

The change is detection-only and has **no data to roll back**, because it never writes to `payouts` or any other table except its existing daily summary row.
- If the log volume is noisy: filter or silence `PAYOUT_STUCK_RESERVED` in Sentry, or scope alerts with `payout_type!="auto"` on the metric. No deploy is needed.
- If the check itself misbehaves: there is no `app_settings` flag. It is not user-visible and it only reads, so the remaining path is a `git revert` of `88973ca` and a redeploy. A revert is sufficient here because the change never applied anything to live data.
- Blocking the whole reconcile loop is not a realistic risk. The check's DB call is wrapped, and the loop's own `try/except` already contains any exception from a tick.

## 9. Verification performed

- [x] Automated tests. New file: **12 passed**. These cover: a stale reserved row is flagged, with the query chain asserted as `eq status`, `lt created_at` about 1h ago, the 5-column select and `limit(500)`, and no `update`/`insert`/`upsert`/`delete` on the mocked table; a fresh row is not flagged; `completed`, `failed`, `pending`, `transfer_completed` and `reversed` rows are not flagged; a DB error logs at error level with `exc_info` and returns `None`; the row limit is surfaced; the metric is incremented per `payout_type`; a DB error does not crash the tick, and `audit_logs` is still written with `payouts_stuck_reserved=None`; the tick summary includes the count. The unit tests run the real `repositories._base.get_rows` against the `mock_supabase_client` fixture, so the PostgREST chain itself is exercised.
- [x] Existing tests: `tests/test_stripe_reconcile.py` + `tests/test_stripe_reconcile_orphan_hold_and_cc.py` — **120 passed** (unchanged); `tests/test_loguru_call_conventions.py` — **8 passed**. Combined run: 140 passed.
- [x] Mutation check. Removing the Python `status == 'reserved'` re-check made 5 tests fail. The check was then restored.
- [x] `ruff check` and `ruff format --check` are clean on both .py files. The repo pre-commit hook passed all 11 checks; its only warning is pre-existing and unrelated (`sprint-current.md` cites a missing path).
- [x] Blast-radius greps: `reserved` across `backend/` (excluding tests), `stripe_reconcile` importers, `stripe_reconciliation`/`discrepancy_detail` readers, `payout_type` defaults in migrations 92/138, and a read-only diff of PR #5791.
- [x] CLAUDE.md conventions: the module uses stdlib `logging`, not loguru, so `extra=`/`exc_info=` are correct there, matching the sibling checks. The dual-import block is unchanged. The log carries IDs only, with no PII. There is no money arithmetic.
- [ ] Feature flag: none. The change is not user-visible and is read-only (justified in section 8).

## What was NOT verified

- The check was not run against live Supabase, staging or production. There was no real `payouts` data, and it was not confirmed that any stuck `reserved` rows exist today.
- PostgREST was not run for real, so the `created_at < cutoff` comparison against a `timestamptz` column is untested. The mock accepts any filter chain. The same `$lt`/ISO-string pattern is used elsewhere in the repo.
- Sentry tag promotion for a **stdlib** logger's `extra={"domain": "payments"}` was not re-verified end to end. The check relies on the same path the sibling checks in this file already use.
- No alert rule was added for `spinr_payment_stuck_reserved_payouts_total`, and none is known to exist. The metric is only useful once someone writes that rule.
- Latency is not covered: the check runs once a day at 02:00 UTC, so a stuck row can take up to about 25h to surface.
- The check does not run on a `target_date` backfill, which is the same as 3g/3h. It also does not run on any tick that returns early because the Stripe PaymentIntent list failed or the rides query failed.
- A `reserved` row with a `NULL` `created_at` would be excluded by the server-side `lt` filter. Every known writer sets `created_at`, but this was not checked against live data.
- The `spinr-money-auditor`/`/code-review` pass required by CLAUDE.md gate 10 was **not run**. This session had no Agent tool, so only a self-review plus the mutation check was done. It should be run before merge.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
