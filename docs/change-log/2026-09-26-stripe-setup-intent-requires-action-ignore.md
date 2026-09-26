# Change Impact & Risk Log — `setup_intent.requires_action` missing from Stripe ignore-list

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (daily `/sentry-triage --severity-only` scan) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch, `fix/stripe-setup-intent-requires-action-ignore`) |
| Related issue or gap ID | Sentry CRIMSON-SMOKE-7445-HC; relates to ACTION_ITEMS.md C10 (closed 2026-08-01) |

## 1. Issue / gap identified

`STRIPE_EVENT_STUCK_UNPROCESSED` fired repeatedly (7493 events, climbing since 2026-08-02) from
`utils/stripe_reconcile.py`'s daily stuck-event sweep. The latest sample event type,
`setup_intent.requires_action`, was falling into the "genuinely unhandled" branch of
`routes/webhooks.py`'s dispatch and never got its `processed_at` stamped, so the reconciler kept
re-flagging it as stuck on every run, forever.

## 2. Root cause

`_STRIPE_IGNORED_EVENTS` in `routes/webhooks.py` already lists `payment_intent.requires_action`
as a routine, non-actionable lifecycle echo (a payment needing 3DS/authentication), but its
`setup_intent` sibling — the same lifecycle state for a SetupIntent instead of a PaymentIntent —
was never added. Confirmed via `git log`: no recent commit touches the ignore-list; this is a
standing omission, not a regression.

## 3. Fix / remediation

Added `"setup_intent.requires_action"` to `_STRIPE_IGNORED_EVENTS`, grouped next to the existing
`setup_intent.created`/`setup_intent.succeeded` entries. This routes the event through the
existing ignored-event branch, which already logs at `debug` (not warning/error) and calls
`mark_stripe_event_processed(event_id)` — the same mechanism that closed ACTION_ITEMS.md C10 for
every other now-ignored event type. No new code path was written; this purely adds one string to
an existing, already-tested allowlist.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one frozenset membership check.** No change to
  `_STRIPE_HANDLED_EVENTS`, no change to any actual payment-processing branch, no change to
  `mark_stripe_event_processed`'s implementation or the reconciler itself.
- Grepped both consumers of `_STRIPE_IGNORED_EVENTS` (the dispatch-classification `elif` at
  `routes/webhooks.py`'s bottom, and the label-cardinality guard near the top of
  `stripe_webhook()`) — both treat membership as a plain set-containment check with no
  per-event-type special-casing, so adding an entry cannot change behavior for any other event
  type.
- The event only ever reaches this branch for a real Stripe-originated `setup_intent.requires_action`
  webhook; no rider/driver-initiated code path constructs this event type.

## 5. User-experience effect

None — no rider/driver/admin-facing behavior changes. This event type carries no payment
settlement action (Stripe's own hosted UI drives the SetupIntent's authentication step); Spinr's
backend was never meant to act on it, only to stop mis-classifying it as stuck.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | Added `"setup_intent.requires_action"` to `_STRIPE_IGNORED_EVENTS` | Stop the daily stuck-event reconciler from re-flagging this routine lifecycle echo forever |
| `backend/tests/test_webhooks_coverage_gap.py` | Added `TestSetupIntentRequiresActionIgnored` regression test | Pin the fixed classification; confirmed to fail without the fix via `git stash` |

## 7. Before / after

```py
# Before
"setup_intent.created",
"setup_intent.succeeded",
"invoice.created",

# After
"setup_intent.created",
"setup_intent.succeeded",
"setup_intent.requires_action",
"invoice.created",
```

## 8. Rollback plan

`git revert` is a complete rollback — removing one frozenset entry. No data migration, no
config/flag, no schema change. Reverting returns the event type to the "unhandled" classification
(the prior, already-broken-in-this-narrow-case behavior), not a newly-broken one.

## 9. Verification performed

- [x] Automated tests run — `python -m pytest tests/test_webhooks_coverage_gap.py -q --no-cov`
  (58 passed)
- [x] Regression proof — the new test confirmed to FAIL (hits the "Unhandled Stripe event type"
  warning path, `unhandled: True`) without the fix (`git stash` on `routes/webhooks.py` alone) and
  PASS (`mark_stripe_event_processed` awaited, no `unhandled` flag) with it
- [x] `ruff check routes/webhooks.py tests/test_webhooks_coverage_gap.py` — clean
- [ ] Manual repro against a real Stripe webhook — not performed; no staging/production Stripe
  access in this sandboxed session; verified via the existing mocked webhook-dispatch test
  harness only
- [x] Blast-radius grep performed — confirmed both consumers of `_STRIPE_IGNORED_EVENTS` treat
  membership as a plain, uniform set-containment check (see §4)
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — Stripe idempotency/event-handling
  conventions and the "do not silently swallow errors" rule (this fix doesn't silence a real
  error — it correctly reclassifies a non-actionable lifecycle event that was previously mis-bucketed
  as unhandled); dispatched `spinr-money-auditor` before commit

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — no payment-processing branch changed;
  only a routine lifecycle event's classification (unhandled → ignored) changed, which is the
  point of the fix

## What was NOT verified

- Not tested against a real Stripe webhook delivery — verified via the existing mocked
  `stripe_webhook()` dispatch test harness only.
- The historical backlog of already-mis-flagged rows (event type `setup_intent.requires_action`,
  stuck since 2026-08-02) is not backfilled by this fix — it only stops the classification from
  recurring going forward. A retroactive `processed_at` backfill for historical rows whose
  `event_type` is now ignore-listed remains open backlog (the investigator's report recommends
  this as a separate follow-up, not bundled into this PR per the one-issue-per-PR rule).
