# Change Impact & Risk Log — ride_location_gap_events constraint fix + Stripe stuck-events admin tooling

> Backfilled 2026-08-31 as part of a gate-compliance sweep
> (docs/audit/2026-08-27-cicd-gates-guardrails-audit.md's follow-through) —
> PR #4624 merged 2026-08-27 with no Change Impact Log at all (only a
> one-line commit-message description). This entry reconstructs it from
> the actual merged diff and fresh blast-radius checks.
>
> **Process note, not silently absorbed into the entry below**: this PR's
> title and one-line description name only the constraint fix
> (`ride_location_gap_events_status_valid`), but the actual diff (985
> additions / 21 deletions / 10 files) also ships an entirely separate
> feature — a Stripe stuck-webhook-events admin viewer/replay tool
> (`backend/routes/admin/stripe_events.py`, the admin-dashboard
> `stripe-events` page, a dashboard widget, a sidebar entry) plus a real
> behavior change to the core Stripe webhook dispatch path
> (`backend/routes/webhooks.py`). That's two logically unrelated changes
> in one PR — exactly what CLAUDE.md's "Scope contract" checkbox exists to
> catch, and there was no Change Impact Log to catch it. Documented as one
> entry below (matching this repo's existing one-file-per-PR convention),
> with both halves called out explicitly rather than merged into a single
> vague description.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 (PR merged) / entry backfilled 2026-08-31 |
| Author | Claude Code (original PR); backfilled by Claude Code |
| Surface(s) | backend, admin-dashboard, migrations |
| Domain (Sentry tag) | safety (gap events), payments (Stripe tooling) |
| PR / commit link | #4624 (merge commit `378efa9`) |
| Related issue or gap ID | none filed — found live in production (23514 constraint violation) |

## Part A — ride_location_gap_events constraint fix

### 1. Issue / gap identified

`route_gap_monitor.py` writes `status = 'unresolved_at_completion'` to
`ride_location_gap_events` when a ride completes while a location gap is
still open. The table's CHECK constraint only allowed `'open'` and
`'resolved'` — every such write raised a real production error (Postgres
23514, check constraint violation).

### 2. Root cause

The constraint was never updated when `route_gap_monitor.py`'s
completion-handling logic was written to use this third status value —
schema and application code drifted apart at authoring time, not from a
later change to either side.

### 3. Fix / remediation

`backend/migrations/370_add_unresolved_at_completion_status_to_gap_events.sql`:
drop the constraint, re-add it with the third value included as `NOT
VALID`, then `VALIDATE CONSTRAINT` in the same migration — the standard
low-lock-time pattern for widening a CHECK constraint (the `NOT VALID` add
is a fast metadata-only change; `VALIDATE` takes a `SHARE UPDATE
EXCLUSIVE` lock that permits concurrent reads/writes while it scans
existing rows).

### 4. Risk & impact on existing functionality

- **Isolated** to `ride_location_gap_events` — grepped for every writer of
  this table (`route_gap_monitor.py`'s `_open_gap_event`,
  `_resolve_open_gap_event`, and this PR's own completion-handling path)
  and every reader (the admin dashboard has no UI for this table as of
  this PR). No other table or code path references this constraint.
- Widening a CHECK constraint (adding an allowed value) cannot invalidate
  any existing row — every row that satisfied the 2-value constraint still
  satisfies the 3-value one. Zero data-migration risk.
- Directly enables `route_gap_monitor.py`'s already-shipped completion
  logic to actually persist instead of raising on every ride that
  completes with an open gap — this migration was a hard dependency for
  code that had already merged and was failing without it, the same
  pattern as the `admin_driver_earnings_rollup` / `route_gap_latest_captures`
  functions this session separately found stuck behind an unapplied
  migration backlog (see `docs/audit/2026-08-27-cicd-gates-guardrails-audit.md`
  follow-through).

### 5. User-experience effect

None directly visible — this is an internal safety/observability data
path (active-trip GPS gap tracking), not rider/driver-facing. Indirect
effect: before this fix, every ride that completed with an unresolved
location gap left that gap row silently un-recorded as
`unresolved_at_completion` (the write errored and was presumably logged,
not silently dropped, per the loop's own `except Exception:
logger.error(...)` wrapper) — meaning safety-team visibility into
"which completed rides had an unresolved GPS gap" was incomplete until
this migration applied.

## Part B — Stripe stuck-webhook-events admin viewer + replay tool

### 1. Issue / gap identified

Stripe webhook events that fail to process (e.g. a transient DB error) had
no operational visibility beyond tailing logs or waiting for the daily
`_reconcile_stuck_stripe_events` sweep (`ACTION_ITEMS.md` C10). No way for
an operator to inspect or manually replay a specific stuck event from the
admin panel.

### 2. Root cause

Not a bug fix — this is a new capability, not a gap being closed in
existing behavior.

### 3. Fix / remediation

New `backend/routes/admin/stripe_events.py`: list/inspect/replay/dismiss
endpoints for events where `processed_at IS NULL`, gated on
`require_super_admin` (same posture as the repo's other Stripe operational
admin routers). New admin-dashboard page + dashboard widget (badge shown
only when `stuckCount > 0`) + sidebar entry.

**This required a real behavior change to `backend/routes/webhooks.py`**,
not just additive admin tooling:
- The core dispatch logic was extracted from `stripe_webhook()` into a new
  `_dispatch_stripe_event()` function, callable both by the real webhook
  handler and by the new admin replay endpoint — so replay exercises the
  identical code path a live Stripe delivery would, not a reimplementation.
- Two failure paths (`payment_intent.succeeded` and
  `payment_intent.payment_failed`, when the ride-status `update_ride()`
  call itself returns `None`) now call `unclaim_stripe_event(event_id)`
  before raising the 500. Previously these paths left the event
  claimed-but-unprocessed forever, so Stripe's own retry delivery would be
  deduped by `claim_stripe_event` and silently dropped — permanently
  losing the retry's usefulness for exactly the class of transient error
  (DB blip, ride briefly not visible) retries exist to paper over. Now the
  retry can genuinely re-process.

### 4. Risk & impact on existing functionality

- **Cross-cutting within the payments domain** — this touches the actual
  Stripe webhook dispatch path, not just new admin surface area. Traced
  both new `unclaim_stripe_event()` call sites directly against this
  PR's own safety contract (`wallet_repo.py`'s docstring: "Only call this
  when NO side effects have been performed for the event"):
  - `payment_intent.succeeded` path: the unclaim fires only when
    `db_supabase.update_ride(ride_id, _paid_fields)` itself returns `None`
    (0 rows updated / ride not found) — meaning the payment-status write,
    tip credit, and the downstream `record_payment_event` ledger call (which
    only runs `if updated is not None`) never executed. **Confirmed safe**:
    no side effect had happened yet at the point of unclaim.
  - `payment_intent.payment_failed` path: same shape — the unclaim only
    fires when the `update_ride()` call recording the failure itself
    returns `None`. **Confirmed safe** for the same reason.
  - Not independently re-verified: the `corporate_topup` and
    `wallet_topup` branches of `payment_intent.succeeded` (`apply_topup`,
    `wallet_apply_credit`) are unchanged by this PR and were not re-audited
    here — they don't call `unclaim_stripe_event` at all, so they're out
    of this PR's behavior-change scope.
  - `wallet_repo.py`'s existing duplicate-claim handler (the
    `_PG_UNIQUE_VIOLATION` branch) was also touched: the `processed_at`
    lookup used to check "is this a stuck duplicate" is now wrapped in its
    own `try/except`, so a failure *checking* whether a duplicate is stuck
    no longer crashes the whole claim call — it falls back to "deduplicating
    anyway" (returns `False`, same as the already-processed case). This is
    a strictly more defensive change (a check that used to be able to raise
    now degrades gracefully) but is itself a silent-fallback pattern worth
    naming: a failure to determine whether an event is *actually* stuck now
    logs a warning and proceeds as if it *isn't* stuck, rather than
    escalating. Given the surrounding code already fires a `CRITICAL` log
    for the genuinely-stuck case when the check succeeds, this narrows
    (does not eliminate) the alerting surface for a specific double-failure
    (duplicate delivery *and* the stuck-check itself failing) — flagged
    here, not treated as a blocker, since it fails toward the existing
    daily reconciliation sweep rather than toward data loss.
- New admin routes are correctly gated (`require_super_admin`, matching
  every other Stripe-operational admin router in this file).
- The replay endpoint has its own idempotency guard against
  double-processing: 409 if `processed_at` is already set, and 409 if the
  unclaim/re-claim race is lost to a concurrent delivery — grepped and
  confirmed present in `replay_event()`.

### 5. User-experience effect

- **Internal admin only** (super-admin gated). No rider/driver/corporate
  visibility.
- Indirect, real effect on payment processing: the two ride-update-failure
  paths above now recover via Stripe's natural retry instead of requiring
  a manual replay for every occurrence — a behavior improvement, not a
  regression, but a genuine change to when/how a failed webhook delivery
  gets a second attempt.

## 6. Files modified (both parts)

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/370_add_unresolved_at_completion_status_to_gap_events.sql` | Widen CHECK constraint to allow `'unresolved_at_completion'` | Part A |
| `backend/routes/admin/stripe_events.py` | New file — list/detail/replay/dismiss stuck-event admin endpoints | Part B |
| `backend/routes/admin/__init__.py` | Mount the new router, `require_super_admin` | Part B |
| `backend/routes/webhooks.py` | Extract `_dispatch_stripe_event()`; add `unclaim_stripe_event()` on 2 ride-update-failure paths | Part B |
| `backend/repositories/wallet_repo.py` | Wrap the stuck-duplicate `processed_at` check in try/except | Part B |
| `backend/tests/test_wallet_repo.py` | Test updates for the above | Part B |
| `admin-dashboard/src/lib/api/stripe-events.ts` | New file — API client types/calls for the admin page | Part B |
| `admin-dashboard/src/app/dashboard/stripe-events/page.tsx` | New file — the admin page itself | Part B |
| `admin-dashboard/src/app/dashboard/page.tsx` | Stuck-event-count widget on the main dashboard | Part B |
| `admin-dashboard/src/components/sidebar.tsx` | Sidebar nav entry | Part B |

## 7. Before / after (Part B — the money-relevant change)

```python
# Before — event stays claimed forever on a ride-update failure
updated = await db_supabase.update_ride(ride_id, _paid_fields)
if updated is None:
    logger.error(...)
    raise HTTPException(status_code=500, detail="Ride update failed — Stripe will retry")
    # ^ comment was misleading: claim_stripe_event dedupes the retry, so it
    #   would NOT actually be reprocessed despite the message
```

```python
# After — release the claim so Stripe's retry genuinely reprocesses
updated = await db_supabase.update_ride(ride_id, _paid_fields)
if updated is None:
    logger.error(...)
    await unclaim_stripe_event(event_id)
    raise HTTPException(status_code=500, detail="Ride update failed — Stripe will retry")
```

## 8. Rollback plan

- **Part A (migration 370)**: reversible on paper — `ALTER TABLE
  ride_location_gap_events DROP CONSTRAINT
  ride_location_gap_events_status_valid; ALTER TABLE ... ADD CONSTRAINT
  ... CHECK (status IN ('open','resolved'));` — but only safe to run
  *before* any row has actually been written with
  `status='unresolved_at_completion'`; after that, reverting the
  constraint would itself raise a violation on existing data. Not stated
  in the original migration's own header (a real gap this backfill is
  surfacing, not fixing).
- **Part B**: `git-revert-safe` for the admin tooling (new files,
  additive). The `webhooks.py` dispatch-path change is also revert-safe
  in isolation — reverting restores the pre-PR "stays claimed on
  ride-update failure" behavior, which is a functional regression
  (stuck events accumulate faster) but not a data-corruption risk.

## 9. Verification performed (backfilled, not the original PR's own claim)

- [ ] Automated tests run — `backend/tests/test_wallet_repo.py` was
      updated by the original PR (visible in the diff), but no test was
      added for the new `unclaim_stripe_event` call sites in
      `webhooks.py` itself, nor for the new admin replay endpoint's
      idempotency guards.
- [ ] Manual repro steps followed in staging — no staging environment
      existed at merge time (`ACTION_ITEMS.md` E1).
- [x] Blast-radius grep performed (backfilled 2026-08-31): every writer/
      reader of `ride_location_gap_events`; every caller of
      `unclaim_stripe_event` (both sites traced against its own safety
      docstring, both confirmed safe); every consumer of the new admin
      route/types.
- [x] Reviewed against `CLAUDE.md` Stripe-idempotency convention —
      confirmed `claim_stripe_event`/`unclaim_stripe_event` semantics are
      respected at both new call sites.
- [ ] Feature-flagged: not applicable — internal admin tooling, and the
      webhook dispatch-path change has no user-visible surface to flag.

## 10. Sign-off

- [x] Rollback plan is concrete for Part B; Part A's rollback caveat
      (constraint revert unsafe once real rows exist) is now stated
      explicitly rather than assumed
- [x] Blast radius is stated, not assumed
- [x] The two-unrelated-changes-in-one-PR issue is named explicitly
      rather than smoothed over

**What was NOT verified**: the `corporate_topup`/`wallet_topup` branches
of `payment_intent.succeeded` were not re-audited (unchanged by this PR).
Whether a *second* failure mode exists in the wallet_repo.py duplicate-
check try/except (the narrowed-alerting concern in Section 4) was flagged
but not resolved — would benefit from a `spinr-money-auditor` pass if this
is considered worth closing rather than accepting as-is.
