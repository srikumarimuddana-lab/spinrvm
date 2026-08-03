# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no pricing/fee mechanism exists for the corporate product" (business decision: flat SaaS subscription, full Stripe automation) — webhook sync slice |

## 1. Issue / gap identified

Third slice of the corporate subscription-billing build: `assign_subscription`
(round2-13) creates a real Stripe Subscription, but nothing keeps our copy
of its state (`corporate_subscriptions.status`/`current_period_end`) in
sync with what actually happens in Stripe afterward — a renewal, a failed
charge, or a cancellation would leave our row silently stale.

## 2. Root cause

Never built — the webhook route (`routes/webhooks.py`) already handles
`customer.subscription.deleted/updated`, `invoice.paid`,
`invoice.payment_failed` for `driver_subscriptions` (Spinr Pass) but has
no equivalent for `corporate_subscriptions`.

## 3. Fix / remediation

Added a self-contained early-exit dispatch in `stripe_webhook()`, inserted
right after the idempotency (`claim_stripe_event`) gate and before the
existing driver-specific `if/elif` chain: for the four relevant event
types, look up the incoming Stripe subscription id against
`corporate_subscriptions` (cheap indexed lookup via
`get_corporate_subscription_by_stripe_id`, added in round2-12). If it
matches a corporate row, a new private helper
`_sync_corporate_subscription_event` handles the full sync and the
function returns immediately — **the existing driver dispatch below is
never reached for a matched corporate event, and is not modified in any
way for an unmatched one** (a driver subscription id never matches the
corporate lookup, so it falls through exactly as before).

`_sync_corporate_subscription_event` mirrors Stripe state into the row:

- `customer.subscription.deleted` → `status='cancelled'`, guarded so a
  late/duplicate delete never re-processes an already-cancelled row.
- `customer.subscription.updated` → maps Stripe's `status` field
  (active/trialing → `active`, past_due → `past_due`,
  canceled/unpaid/incomplete_expired → `cancelled`), syncs
  `current_period_end` and `cancel_at_period_end`.
- `invoice.paid` → renewal confirmed, sets `status='active'` (clearing a
  prior `past_due`) and advances `current_period_end` (reusing the
  existing `_invoice_period_end_iso` helper already in this file for the
  driver path — same Stripe Invoice shape, no new parsing logic).
- `invoice.payment_failed` → `status='past_due'`; Stripe's own dunning
  retries the charge, matching the existing driver-side comment on the
  same event type.
- A guard at the top ignores any of the three non-delete events for a row
  already `status='cancelled'` — the same "never resurrect a terminal row
  on a late/duplicate event" rule the existing driver `invoice.paid`
  handler already applies, reused for the same reason here.

8 new tests in `test_webhooks_corporate_subscription.py`, including one
that explicitly proves a driver Spinr Pass subscription id (which never
matches the corporate lookup) still falls through to the untouched
existing driver dispatch.

## 4. Risk & impact on existing functionality

- **Blast radius, driver Spinr Pass path: zero lines of the existing
  `if/elif` chain were modified.** The new code is a pure insertion
  before it; a driver subscription id never has a
  `corporate_subscriptions` row, so `corp_sub` is always `None` for those
  events and control falls through exactly as it did before this commit.
- The new DB lookup (`get_corporate_subscription_by_stripe_id`) runs on
  every `customer.subscription.deleted/updated`, `invoice.paid`,
  `invoice.payment_failed` event — including every existing driver
  Spinr Pass event — adding one extra indexed query per event. Confirmed
  via the repo's autouse `patch_external_dependencies` fixture
  (`backend/tests/conftest.py`) that `corporate_repo.py`'s `supabase`
  binding is already globally mocked in every test, so this new call is
  safe by default (`mock_supabase_client`'s default empty response) even
  in pre-existing driver-subscription webhook tests that don't
  explicitly mock it — verified by reasoning through the fixture, not
  merely assumed.
- Grepped for other callers of the four touched event types: only this
  route handles Stripe webhooks in this codebase; no other consumer.
- Idempotency is preserved: the early-exit path still calls
  `mark_stripe_event_processed(event_id)` before returning, exactly like
  every other handled branch, so Stripe's replay/retry semantics are
  unaffected.

## 5. User-experience effect

None directly user-facing yet — no admin route or UI surfaces this state
in this commit (follow-up commits). Once wired up, a company's admin-
visible subscription status will track Stripe's real billing state
instead of only reflecting what was true at assignment time.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | New `_sync_corporate_subscription_event` helper + early-exit dispatch guard for 4 event types | Keep `corporate_subscriptions` state in sync with real Stripe events |
| `backend/tests/test_webhooks_corporate_subscription.py` | New file: 8 tests | Cover every corporate sync branch + confirm the driver path is untouched |

## 7. Rollback plan

`git revert` the commit. The early-exit guard and its helper are the only
changes; reverting restores the exact prior dispatch chain byte-for-byte.
No data migration involved — `corporate_subscriptions` rows simply stop
being updated by webhooks again (they'd only ever be set by
`assign_subscription`/`cancel_subscription` from round2-13, which are
also not yet wired to any route).

## 8. Verification performed

- [x] `ast.parse` syntax check on both modified/new files — clean.
- [x] Traced the early-exit guard's placement relative to the existing
      idempotency gate and driver dispatch chain by reading the
      surrounding ~150 lines before inserting, rather than guessing the
      right insertion point.
- [x] Confirmed `_invoice_period_end_iso` (reused, not duplicated) has
      the exact signature and defensive-parsing behavior needed for the
      corporate `invoice.paid` case — same Stripe Invoice `lines[0].period`
      shape.
- [x] Reasoned through `patch_external_dependencies` (autouse fixture) to
      confirm the new unmocked-by-default DB call is safe in every
      existing driver-subscription webhook test, rather than assuming it.
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      deferred to the single end-of-round pass, where the existing
      driver Spinr Pass webhook test suite (`test_spinr_pass_subscription.py`,
      `test_webhooks_main.py`) will be watched closely for any regression
      from this insertion.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — the driver dispatch chain is
      provably unmodified (pure insertion before it), confirmed by diff
      inspection line-by-line
- [x] No silent behavior change to a working flow — the driver Spinr Pass
      path executes identically to before for every event that isn't a
      corporate subscription match

## What was NOT verified

Did not run `pytest`, and did not exercise this against a real Stripe
webhook delivery (sandbox or otherwise) — no live Stripe calls are
possible in this session. The single highest-risk assumption in this
commit — that adding one extra DB lookup per driver Spinr Pass webhook
event doesn't regress any of the ~15+ existing tests covering that path —
is reasoned from the autouse mock-Supabase fixture, not confirmed by
running those tests; this will be the first thing checked in the
end-of-round full-suite pass.
