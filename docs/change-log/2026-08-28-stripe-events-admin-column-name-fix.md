# Change Impact & Risk Log — stripe_events admin page: fix received_at column-name mismatch

> Backfilled 2026-08-31 as part of a gate-compliance sweep
> (docs/audit/2026-08-27-cicd-gates-guardrails-audit.md's follow-through) —
> PR #4646 merged 2026-08-28 with the mandatory Change Impact Log template
> left as unfilled placeholder text. This entry reconstructs it from the
> actual merged diff and a fresh blast-radius check, not from the PR
> description (which had none).

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 (PR merged) / entry backfilled 2026-08-31 |
| Author | Claude Code (original PR); backfilled by Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | #4646 (merge commit `0d70fe6`) |
| Related issue or gap ID | none filed — found live in production |

## 1. Issue / gap identified

The admin "Stripe stuck events" page (`/dashboard/stripe-events`) and its
backing endpoints (`backend/routes/admin/stripe_events.py`) queried a
`created_at` column on the `stripe_events` table. That column has never
existed — a real production error (PostgREST 42703, column not found) on
every load of that admin page.

## 2. Root cause

`stripe_events` (migration `22_stripe_events.sql`) has only ever had a
`received_at` timestamp column, never `created_at`. The admin route was
authored against the wrong column name from the start — not schema drift,
not a renamed column, just a naming mistake that shipped and was never
caught because this admin page has no test coverage and no one had opened
it since.

## 3. Fix / remediation

Renamed every `created_at` reference to `received_at` across the full
stack for this one page: the backend route's `select()` projection and
`.order()` clause, the response dict keys, the TypeScript response types,
and the two places the admin UI renders the value. No behavior beyond the
rename — same query shape, same ordering, same age-in-minutes calculation.

## 4. Risk & impact on existing functionality

- **Isolated.** Grepped every other reader of the `stripe_events` table:
  `backend/repositories/wallet_repo.py:378` selects only `processed_at`
  from this table (a different column, unaffected). No other backend
  module queries `stripe_events.created_at`/`received_at`. No other
  frontend file imports `StripeEventDetail`/`StuckStripeEvent` besides the
  two files this PR touched.
- Blast radius: **single-surface** — this one admin page and its two
  backing endpoints (`GET /admin/stripe-events`, `GET
  /admin/stripe-events/{event_id}`) only.
- No interaction with the Stripe webhook processing path itself
  (`claim_stripe_event`, `wallet_repo.py`'s idempotency check) — this page
  is read-only observability tooling, not part of the payment-processing
  path. A broken query here could not have caused a missed or double-
  processed webhook.
- No ride state machine or money-arithmetic interaction.

## 5. User-experience effect

- **Internal admin only** (super-admin gated, `require_super_admin`
  dependency on `get_event_detail`). No rider, driver, or corporate-admin
  visibility.
- Before the fix: the page's stuck-events list and detail view either
  errored outright or silently rendered every row's "Received" timestamp
  as "Unknown" (both code paths guard with `?? "Unknown"` /
  `if received:`), depending on how Supabase's client surfaced the
  PostgREST error to the caller. Either way, staff monitoring for stuck
  Stripe events lost the one signal (age) the page exists to show.
- Not visible mid-session to any rider/driver — admin-only tooling.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/stripe_events.py` | `created_at` → `received_at` in `select_cols`, `.order()`, response dict keys (2 endpoints) | Match the real column name |
| `admin-dashboard/src/lib/api/stripe-events.ts` | `created_at` → `received_at` in `StuckStripeEvent`/`StripeEventDetail` TS interfaces | Match the corrected API response shape |
| `admin-dashboard/src/app/dashboard/stripe-events/page.tsx` | `evt.created_at`/`detail.created_at` → `received_at` in 2 render sites | Match the corrected type |

## 7. Before / after

```python
# Before
select_cols = "event_id,event_type,created_at"
...
.order("created_at", desc=False)
...
"created_at": created,
```

```python
# After
select_cols = "event_id,event_type,received_at"
...
.order("received_at", desc=False)
...
"received_at": received,
```

## 8. Rollback plan

`git-revert-safe` — pure rename across 3 files, no schema change, no data
written or migrated. A revert restores the broken (but harmless — read-only
observability page) prior state instantly. No feature flag: this is a bug
fix to a broken admin-only read path, not a behavior change to dark-launch.

## 9. Verification performed (backfilled, not the original PR's own claim)

- [ ] Automated tests run — **none exist for this route or page**; no
      regression test was added by the original PR to catch a future
      recurrence of this exact class of bug (a column-name typo against a
      real schema).
- [ ] Manual repro steps followed in staging — no staging environment
      existed at merge time (see `ACTION_ITEMS.md` E1).
- [x] Blast-radius grep performed (backfilled 2026-08-31): confirmed via
      `grep -rn "stripe_events"` across `backend/` and
      `grep -rln "StripeEventDetail\|StuckStripeEvent"` across
      `admin-dashboard/src/` — see Section 4.
- [x] Reviewed against relevant `CLAUDE.md` convention — confirmed the
      real column name directly against
      `backend/migrations/22_stripe_events.sql` rather than assuming the
      original PR's diff was correct.
- [x] Feature-flag: not applicable — bug fix to an already-broken
      internal-only page, no existing correct behavior to protect.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — grepped, not guessed
- [x] No silent behavior change to an already-shipped *working* flow — the
      page was broken before this fix; there is nothing this PR could have
      regressed for a working user besides itself

**What was NOT verified, then or now**: whether the page ever actually
returned a 500 to the browser vs. silently rendering "Unknown" for every
row — depends on how the Supabase Python client surfaces a PostgREST
column-not-found error, which was not traced through in either the
original PR or this backfill. Either failure mode is now moot post-fix.
No test was added, so a future re-introduction of this exact bug class
(a route querying a column name that doesn't match the migration) would
not be caught automatically.
