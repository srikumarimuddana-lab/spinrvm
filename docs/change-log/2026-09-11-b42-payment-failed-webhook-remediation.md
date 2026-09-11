# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (claude-sonnet-5), session `session_016N2vRqybAY6LqEr8Yg7RUB` |
| Surface(s) | backend (webhooks, payments), production data |
| Domain (Sentry tag) | payments |
| PR / commit link | (see PR description this file is attached to) |
| Related issue or gap ID | ACTION_ITEMS.md B42 |

## 1. Issue / gap identified

B42 (filed 2026-09-07) described a single ~36-minute window during PR #5048's
live incident where `payment_failed` Stripe webhook events were allegedly
dropped. That framing was itself stale: this session's live investigation
found the real scope is much larger and still active — **53 of the 55
`payment_intent.payment_failed` events this production project has ever
received (2026-07-15 through 2026-09-11, i.e. through today) were
permanently stuck unprocessed**, not just a 36-minute slice.

## 2. Root cause

`routes/webhooks.py`'s `payment_intent.payment_failed` handler writes to
`rides` with:
```python
{"$set": {"payment_status": "failed", "payment_intent_id": payment_intent_id,
          "payment_failure_reason": failure_message}}
```
`rides.payment_failure_reason` **was never created by any migration** —
confirmed by grep (the column name appears exactly once in the entire
codebase, at this write site) and by a live `information_schema.columns`
query against production. Every real invocation of this write therefore hit
a Postgres "column does not exist" error. Unlike the CAS *read* a few lines
above (already wrapped in try/except + `unclaim_stripe_event`, added by the
N1 director review that predates this bug), this *write* had no such
guard — the exception propagated out of the handler as a 500, and because
`claim_stripe_event` had already inserted the row, Stripe's automatic retry
was deduped as a no-op "duplicate" forever. `processed_at` stayed NULL
permanently, with no error visible anywhere a human would see it (the crash
happens on every retry too, but retries return 200 "duplicate" — no alert
fires).

Cross-checked against `stripe_events.payload`: 49 of the 53 stuck events
carry the exact Stripe error text ("This account is not eligible for the
requested card features...") that PR #5250 (merged earlier the same day,
independently) fixed at the *charge-creation* layer
(`utils/stripe_charge.py`'s `authorize_ride`). That is a related but
distinct bug in a different code path — PR #5250 stops Stripe from
rejecting the pre-auth *create* call outright; this bug is why the
resulting `payment_intent.payment_failed` **webhook**, once Stripe sends
it, was never recorded. Fixing one does not fix the other; both needed
fixing.

## 3. Fix / remediation

**Code (this PR):**
1. Migration 414 adds the missing `rides.payment_failure_reason TEXT`
   column (nullable, no default, no backfill in the migration itself).
2. `routes/webhooks.py`'s CAS write is now wrapped in the same
   try/except + `unclaim_stripe_event` + 503 pattern the read already
   uses, so any *future* write failure (this bug recurring in a different
   form, a transient DB blip, anything) degrades to "Stripe retries"
   instead of "silently lost forever." This is the actual structural fix —
   migration 414 fixes today's specific instance, the try/except fixes the
   *class* of failure.
3. New regression test `TestCasWriteFailureIsNotSilentlyDropped` in the
   existing `test_webhook_payment_failed_guard.py` (already the home for
   the sibling read-failure guard), proving the write failure unclaims and
   503s exactly like the read failure does.

**Production data remediation (already applied, ahead of this PR — see §9):**
Read-only triage first, classified all 53 stuck events by their linked
ride's *current* state:
- **44 rides**: still `payment_status='pending'`, `payment_intent_id IS
  NULL` — i.e. nothing had ever written the intended failure record. All
  44 already have `status='cancelled'` (the booking flow's own separate
  cancel-on-hold-failure logic worked correctly; only the payment-status
  *annotation* was missing). Updated these 44 to `payment_status='failed'`,
  `payment_intent_id=<the failed PI>`, `payment_failure_reason=<Stripe's
  message>` — using the *exact same CAS predicate* the live code uses
  (`WHERE payment_status='pending' AND payment_intent_id IS NULL`), so a
  ride that had changed state between triage and remediation would simply
  not match and be skipped, not overwritten.
- **5 rides**: already `payment_status='paid'`/`status='completed'` via a
  *later, different* PaymentIntent — the rider retried and succeeded. Left
  untouched, matching exactly what the live code's own "already settled,
  ignore stale failure" branch would do if these were replayed today. This
  is the same race N1's CAS logic exists to prevent (recorded as a real,
  historical failure mode in `test_webhook_payment_failed_guard.py`'s
  module docstring) — remediation respects it, not works around it.
- **4 events** (all from 2026-08-16, the earliest and only batch with
  plain "Your card was declined." rather than the account-ineligibility
  message): no matching `rides` row exists at all. No ride to update;
  flagged below as a residual open question, not silently dropped.

All 53 `stripe_events` rows are now `processed_at = now()`.

## 4. Risk & impact on existing functionality

**Blast radius — code:** `_is_incremental_auth_ineligible`/`authorize_ride`
(PR #5250, unrelated file) not touched. This PR's code changes are confined
to one `try/except` addition around one existing write, in the one handler
branch (`payment_intent.payment_failed`) that reaches it — grepped for
every other caller of this branch and every other write to
`rides.payment_status`/`payment_intent_id`: none share this code path.
`db_supabase.update_one` itself is unchanged (generic CRUD helper, used
throughout the codebase) — only this one call site's exception handling
changed.

**Blast radius — the new column:** purely additive, nullable, no default,
no index. Nothing else in the codebase reads `payment_failure_reason` yet
(grepped) — it is currently write-only, informational for future
support/admin tooling. Zero risk to any existing query or read path.

**Blast radius — the data remediation:** confined to `rides.payment_status`
/`payment_intent_id`/`payment_failure_reason` and `stripe_events.processed_at`
on exactly the 44+53 rows identified above, each individually verified
against the live production state immediately before writing (§3). No
wallet, `financial_events`, driver-payout, or Stripe-side data was touched —
this remediation only corrects a *labeling* gap on rides that were already
correctly cancelled; no money moved as a result of this fix in either
direction. Push notifications that should have fired at original-failure
time (`send_push_notification` to rider + driver) were **not** backfilled —
sending a "payment failed" push for an event from weeks ago would be
confusing/wrong at this point; this is a data-accuracy fix, not a
notification replay.

**Ride state machine / insurance periods:** not touched — `rides.status`
was already `cancelled` on all 44 remediated rows before this fix; only
`payment_status`/`payment_intent_id`/`payment_failure_reason` changed.

## 5. User-experience effect

None, retroactively — none of the 44 riders/drivers are notified now (see
§4). Going forward: a support agent or future admin-dashboard view looking
at any of these 44 rides will now see an accurate `payment_status='failed'`
with a real failure reason, instead of a ride that looks like it was
cancelled with an unexplained `pending` payment forever. Not mid-session
visible to anyone — all 44 rides are historical and already cancelled.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/414_rides_payment_failure_reason.sql` | New — adds `rides.payment_failure_reason TEXT` | Missing column B42 uncovered |
| `backend/routes/webhooks.py` | Wrapped the CAS write in try/except + unclaim + 503, mirroring the existing read guard | Prevent this class of silent permanent loss recurring |
| `backend/tests/test_webhook_payment_failed_guard.py` | Added `TestCasWriteFailureIsNotSilentlyDropped` | Regression coverage for the write-failure path |
| `ACTION_ITEMS.md` | B42 closed with full findings | Tracking |
| Production `rides` (44 rows), `stripe_events` (53 rows) | Data remediation, applied directly via Supabase MCP ahead of this PR — see §3, §9 | Correct the stuck records this bug caused |

## 7. Before / after

```python
# Before
else:
    updated = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "payment_status": _observed_status, "payment_intent_id": _observed_pi},
        {"$set": {"payment_status": "failed", "payment_intent_id": payment_intent_id,
                  "payment_failure_reason": failure_message}},
    )
    if updated is None:
        ...
```

```python
# After
else:
    try:
        updated = await db_supabase.update_one(
            "rides",
            {"id": ride_id, "payment_status": _observed_status, "payment_intent_id": _observed_pi},
            {"$set": {"payment_status": "failed", "payment_intent_id": payment_intent_id,
                      "payment_failure_reason": failure_message}},
        )
    except Exception as _write_err:
        logger.error(f"... unclaiming {event_id} so Stripe can retry: {_write_err}", exc_info=True, ...)
        await unclaim_stripe_event(event_id)
        raise HTTPException(status_code=503, detail="Ride payment-status update failed — Stripe will retry") from _write_err
    if updated is None:
        ...
```

## 8. Rollback plan

**Code**: `git revert` — the try/except addition is a pure safety net; reverting it returns to the (buggy but now schema-correct, since the migration stays) prior behavior. Migration 414 rollback: `ALTER TABLE rides DROP COLUMN IF EXISTS payment_failure_reason;` (safe at any time, nothing else reads it).

**Data remediation**: **not revertible via `git revert`** — already applied directly to production, per this repo's own convention that a code revert is not a rollback plan for data already written. If any of the 44 remediated rows turns out to need correction, the original pre-remediation values were `payment_status='pending'`, `payment_intent_id=NULL`, `payment_failure_reason=NULL` for all 44 (verified identical before writing — see §3) — a corrective script could restore that specific triple per-row if ever needed, referencing the event_id → ride_id mapping in this log's §3 methodology. No such need is anticipated; this was a pure gap-fill, not a value change to a previously-correct field.

## 9. Verification performed

- [x] `npx pytest tests/test_webhook_payment_failed_guard.py` (backend): **16/16 passed**, including the new write-failure test.
- [x] `ruff check` + `ruff format --check` on both changed Python files: clean.
- [x] Migration 414 applied directly to production (`soavhtdhefowwvforzwb`) via Supabase MCP `apply_migration`, confirmed via a follow-up `information_schema.columns` read that the column now exists. Tracked in `schema_migrations` (filename/checksum/applied_at/applied_by) using the same sha256-of-file-bytes checksum `backend/scripts/run_migrations.py` itself computes, so the official runner recognizes this as already-applied and will not attempt to reapply it.
- [x] Data remediation: read-only triage query run first (classified all 53 by ride's current state), the 44-row UPDATE and the 53-row `stripe_events` UPDATE both used `RETURNING` and the actual returned row counts/ids were inspected and matched expectations exactly (44 and 53) before proceeding.
- [x] Post-remediation verification: `select count(*) from stripe_events where event_type='payment_intent.payment_failed' and processed_at is null` → **0**.
- [x] Reviewed against CLAUDE.md conventions: Stripe idempotency (unclaim-before-retry pattern preserved, matches the existing read guard exactly), "do not silently swallow errors" (the new except re-raises as a 503, doesn't swallow), money-path Change Impact Log (this file), migration conventions (additive, numbered 414 — confirmed 413 was the prior highest on `main` before writing).

## 10. What was NOT verified

- **The 4 orphaned events** (2026-08-16, `ride_exists: null`, plain "Your card was declined." rather than the account-ineligibility message) were marked `processed_at` but their missing-ride cause was not separately root-caused — could be a since-deleted test ride, a ride ID that was never valid, or something else. Flagging as a residual open question rather than assuming either explanation; not re-opening B42 for it since it's a distinct, much smaller anomaly (4 events, oldest in the dataset, different failure message) from the 49-event pattern this fix addresses.
- **Whether any of the 44 remediated riders should be retroactively notified** (a "your ride was cancelled — here's why" follow-up) is a product/support decision, not made here — this fix corrects the data record only.
- **Stripe's own dashboard/webhook delivery log** was not cross-checked (no Stripe MCP access this session — unauthenticated, per this session's connector state) — the remediation is based entirely on `stripe_events`' own stored payloads, which B42's original text already flagged as the best-available source absent Stripe API access.
- Not tested against a real Stripe webhook delivery end-to-end (mocked `update_one`/`get_ride` in the regression test, per this codebase's established webhook-test convention) — no real Stripe test-mode webhook was fired.
