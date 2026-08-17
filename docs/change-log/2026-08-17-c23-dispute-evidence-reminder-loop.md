# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | ACTION_ITEMS.md C23, Action item 2 of 5 (the background-loop half) |

## 1. Issue / gap identified

`charge.dispute.created` now records `evidence_due_by` (C23 Action 1), but nothing alerts as the deadline approaches. Miss it and the dispute is lost automatically with no evidence considered — an unrecoverable loss, and today it would only be noticed by someone manually checking the Stripe Dashboard or querying `stripe_disputes` directly.

## 2. Root cause

No mechanism existed to surface an approaching, still-open dispute deadline — the `charge.dispute.created` handler's own `CHARGEBACK:` error log fires once, at creation time, and nothing runs again as the deadline nears.

## 3. Fix / remediation

Scoped to the background-loop half of Action item 2 (the item's own text offers two options — a Sentry rule on the existing log, which is a Sentry-UI config action outside this session's reach, or a new replay-safe loop; built the loop):

- **Migration 327**: additive `evidence_reminder_sent_at timestamptz` claim-flag column on `stripe_disputes`.
- **New `backend/utils/dispute_evidence_reminder.py`**: a 19th/20th background loop (registered in `core/lifespan.py`, after `stripe_reconcile`), ticking every 6 hours (matching `subscription_expiry`'s cadence). Queries open disputes (`status` not in `won`/`lost`/`warning_closed`) with `evidence_due_by` in `(now, now+3d]`, not yet reminded, not yet submitted. For each, atomically claims via `UPDATE ... WHERE evidence_reminder_sent_at IS NULL` (same idempotency shape as `routes/drivers/subscriptions.py`'s `expiry_warned_3d`) and, only on a successful claim, fires a `logger.error` (`CHARGEBACK EVIDENCE DUE SOON: ...`) plus a Sentry-tagged `_escalate()` call (`spinr_alert=dispute_evidence_due_soon`, `domain=payments`, `surface=backend`), mirroring `services/ledger_service.py`'s `escalate()` pattern.
- Re-checks the due-by window at claim time (not just query time) before attempting the claim, defending against a few-seconds-stale candidate list.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated new loop + one new column.** No existing loop, route, or table is modified. The 19 other background loops are untouched.
- **Replay-safety**: no Redis lock — correctness relies entirely on the atomic claim (`update_one` with `evidence_reminder_sent_at: None` in the filter), same as the precedent it mirrors. Every replica queries the same candidates each tick; only the replica whose `UPDATE` actually matches a still-`NULL` row proceeds to alert — every other replica's write matches zero rows and is silently skipped. At most one alert per dispute regardless of replica count.
- **Never crashes**: a failed tick (DB error, etc.) is caught at the loop's top level, logged, and the loop waits for the next 6h tick rather than spinning or dying.
- **No PII in the Sentry escalation context**: `stripe_dispute_id`, `ride_id`, `days_left`, `evidence_due_by`, `amount_cents`, `reason` only — no rider/driver name, phone, or coordinates.
- **No index added** on `evidence_due_by`/`evidence_reminder_sent_at` — chargeback volume is currently ~zero (per C23's own note); the query would be a cheap seq scan on a table with single-digit rows. Deferred alongside migration 326's own same deferral, to be revisited together if real volume ever justifies it.

## 5. User-experience effect

None rider/driver-facing. Internal-admin-facing only indirectly: an on-call engineer subscribed to the `dispute_evidence_due_soon` Sentry tag now gets paged 3 days before a dispute deadline instead of finding out only by manually checking.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/327_stripe_disputes_evidence_reminder_claim_flag.sql` | New — adds `evidence_reminder_sent_at` claim-flag column | Idempotency for the new loop's alert-once-per-dispute guarantee |
| `backend/utils/dispute_evidence_reminder.py` | New — the T-3-days alert loop | C23 Action 2 |
| `backend/core/lifespan.py` | Registers `dispute_evidence_reminder_loop` as a new startup background task | Wires the loop in |
| `backend/tests/test_dispute_evidence_reminder.py` | New — 6 tests: happy-path alert, lost-claim-race skip, stale-window defensive skip, malformed-`due_by` skip, tick-exception swallowed, query-filter-shape regression pin | Regression coverage |
| `docs/change-log/2026-08-17-c23-dispute-evidence-reminder-loop.md` | This file | Mandatory Change Impact Log |

## 7. Before / after

```python
# Before: no code reads evidence_due_by after it's written
```

```python
# After (backend/utils/dispute_evidence_reminder.py, _tick, abbreviated)
candidates = await get_rows("stripe_disputes", {
    "$and": [{"evidence_due_by": {"$gt": now.isoformat()}}, {"evidence_due_by": {"$lte": window_end.isoformat()}}],
    "evidence_reminder_sent_at": None,
    "evidence_submitted_at": None,
    "status": {"$nin": ["won", "lost", "warning_closed"]},
}, limit=200)

for dispute in candidates:
    ...
    claimed = await update_one("stripe_disputes", {"id": dispute["id"], "evidence_reminder_sent_at": None},
                                {"$set": {"evidence_reminder_sent_at": now.isoformat()}})
    if claimed is None:
        continue
    logger.error("CHARGEBACK EVIDENCE DUE SOON: ...")
    _escalate(...)
```

## 8. Rollback plan

`git-revert-safe`. Migration 327's header states: `ALTER TABLE stripe_disputes DROP COLUMN IF EXISTS evidence_reminder_sent_at;` — safe at any point, no other column/function/view depends on it. The loop itself is additive (a new `_spawn()` call); reverting `lifespan.py`'s registration stops the loop from starting on the next deploy, no data cleanup needed either way (the loop only writes a timestamp to its own claim-flag column, never mutates anything else).

## 9. Verification performed

- [x] `pytest backend/tests/test_dispute_evidence_reminder.py backend/tests/test_core_lifespan_coverage.py backend/tests/test_routes_webhooks_coverage.py backend/tests/test_disputes_admin_coverage.py backend/tests/test_routes_disputes_coverage.py -q --no-cov` — 82/82 pass.
- [x] `ruff check` + `ruff format --check` on all touched Python files — clean.
- [x] Confirmed `test_core_lifespan_coverage.py`'s `ENV=test` guard test (asserts zero real `asyncio.create_task` calls for any loop) still passes with the new loop registered — it asserts an empty list, not a fixed count, so adding a loop doesn't break it.
- [x] `spinr-realtime-reliability-reviewer` review requested before PR creation, specifically checking the replay-safety contract against this repo's established loop precedent (`routes/drivers/subscriptions.py`'s `expiry_warned_3d`). Verdict: FIX BLOCKERS — one real finding: the loop was spawned but not watchdog-covered (no `record_heartbeat()` call, absent from `core/lifespan.py`'s `_WATCHDOG_LOOP_NAMES`), so a silently-stalled (not crashed) loop would never be caught by the watchdog. Fixed before merge: added the heartbeat call (recorded even after a caught tick exception, matching `subscription_expiry`'s placement) and the missing watchdog-list entry, plus a new regression test (`test_heartbeat_recorded_even_after_tick_exception`). Everything else — replay-safety, migration correctness, PII handling, query-filter compilation, test coverage — was confirmed sound with no other issues.
- [ ] Not run against a real multi-replica deployment or live Supabase — verified via unit tests with mocked `get_rows`/`update_one` calls exercising the claim-race and stale-window paths.

## What was NOT verified

- The Sentry-rule half of C23 Action item 2 ("a Sentry rule on the existing `CHARGEBACK:` error log for the open event") — that's a Sentry dashboard/UI configuration action, out of reach from this session; only the code-side loop was built.
- Real Sentry delivery of the `dispute_evidence_due_soon`-tagged event — `_escalate()`'s own `except Exception` guard means a Sentry SDK failure is swallowed by design (matches `ledger_service.escalate()`'s established posture), so delivery itself was not observed end-to-end.
- Whether 6-hour tick granularity is the right cadence for a genuinely urgent, unrecoverable-if-missed deadline — chosen to match `subscription_expiry`'s existing cadence rather than a fresh analysis; worth reconsidering if real chargeback volume ever materializes (a 3-day-out warning caught within a 6h window is still a comfortable margin, but a shorter interval costs little at near-zero current volume).
