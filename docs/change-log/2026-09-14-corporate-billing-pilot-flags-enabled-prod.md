# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (spinr-competitive-positioning-15juag session), at explicit user request |
| Surface(s) | backend (production database only — no application code changed) |
| Domain (Sentry tag) | corporate |
| PR / commit link | This doc only; the code that these flags gate was already merged via #5340 |
| Related issue or gap ID | ACTION_ITEMS.md G7 (corporate B2B GTM); follow-up to the 2026-09-13 pilot-flag PR |

## 1. Issue / gap identified

PR #5340 (merged 2026-09-14) added a two-gate design for corporate subscription billing — a
global `corporate_subscription_billing_enabled` setting and a per-company
`subscription_billing_pilot_enabled` column — both defaulting to `false`. The user asked to
name Spinr's own internal corporate account as the pilot and flip both flags. No staging
environment exists (`docs/runbooks/staging-environment.md` is scaffolding only; confirmed live
via the Supabase account — only one project, `spinrmobileapp`, exists and it is production), so
this could only be done in production. The user was told this explicitly and confirmed
"flip both flags in production now" before anything was changed.

## 2. Root cause

N/A — this is not a bug fix. It's the deliberate, requested activation of a feature-flagged
capability that shipped dark by design.

## 3. Fix / remediation

Three things were done, all against the production Supabase project:

1. **Applied migration 419**, which had been merged to `main` in #5340 but never actually run
   against production (merging code does not auto-apply schema migrations in this repo — that
   is a separate deploy step, and no deploy/migration run happened between merge and this
   session). Applied via the Supabase MCP `apply_migration` tool, then a matching row was
   inserted into `public.schema_migrations` (filename + SHA-256 checksum, computed the same way
   `backend/scripts/run_migrations.py` computes it) so the app's own migration runner will
   correctly show it as applied and never attempt to reapply it.
2. **Set `settings.corporate_subscription_billing_enabled = true`** (the global gate; singleton
   row, `id = 'app_settings'`).
3. **Set `corporate_accounts.subscription_billing_pilot_enabled = true`** for the existing
   Spinr corporate account (looked up by name in `corporate_accounts`; found already in
   production, `status = active`, created 2026-07-07 — not newly created by this change).

Two `audit_logs` rows were written by hand (actions `corporate_subscription_billing_enabled_set`
and `corporate_subscription_pilot_toggled`) to preserve an audit trail, since this went through
direct SQL rather than the real admin-dashboard endpoints (`PUT /api/admin/settings` and
`POST /admin/corporate-accounts/{id}/subscription-pilot`) — there was no admin browser session
available to this remote session to drive those endpoints instead. `actor_id` was left `NULL`
rather than fabricating a real admin's identity; `details` states plainly that this was a
direct-DB action taken by Claude Code at explicit user request, not a real admin action through
the UI.

**No application code changed in this entry** — this is a data/config change only.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, single-company.** The global flag is now on, but the
  per-company gate (added specifically to prevent this) means `assign_subscription` still
  refuses every corporate account except Spinr's own. Verified no other `corporate_accounts`
  row has `subscription_billing_pilot_enabled = true`.
- **Distance to an actual Stripe charge, checked directly against production data**:
  - Spinr's corporate account already has a real Stripe customer id on file (checked directly
    against production, value not repeated here) — pre-existing, not created by this change.
  - **`corporate_subscription_plans` is completely empty (zero rows).** `assign_subscription`
    looks up the plan before doing anything else and raises `plan_not_found_or_inactive` for
    any `plan_id` if none exists — so no call to Stripe is possible right now, from anyone,
    for any company, regardless of these two flags. An admin would first have to create a
    plan (with a real `stripe_price_id`) via the admin dashboard before assignment could even
    reach the "no payment method on file" / Stripe-call stage.
  - Whether a default payment method is attached to that Stripe customer was **not checked** —
    that lookup (`get_default_payment_method`) hits the Stripe API directly, and this session
    has no authorized Stripe MCP access (Stripe connector requires separate OAuth the user
    hasn't completed here).
  - No existing row in `corporate_subscriptions` for Spinr (confirmed empty) — no live
    subscription exists today.
- **No other reader/writer of these two flags exists** besides the code shipped in #5340
  (confirmed by the `spinr-corporate-billing-reviewer` audit on that PR, not re-derived here).
- **No interaction with ride state, dispatch, wallet/allowance deltas, or surge** — unchanged
  from #5340's own analysis; this entry only activates flags that PR's code already gates.

## 5. User-experience effect

- **Internal admin only, and only for one company.** Any admin with the `corporate_accounts`
  module grant can now see and use the subscription-assign UI for Spinr's own account on
  Spinr's own `/dashboard/corporate-accounts/{id}/subscription` page. No rider,
  driver, or other corporate client is affected.
- **Not mid-session visible to anyone** — no active user session touches this surface.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| (production DB) `settings` | `corporate_subscription_billing_enabled`: `false` → `true` | Global gate, per explicit user request |
| (production DB) `corporate_accounts` | Spinr row: `subscription_billing_pilot_enabled`: `false` → `true` | Per-company gate, naming Spinr as pilot |
| (production DB) `schema_migrations` | New row for `419_corporate_subscription_billing_pilot.sql` | Keep the app's migration runner's bookkeeping accurate — the column now really exists |
| (production DB) `audit_logs` | 2 new rows | Preserve an audit trail for a change made outside the normal admin-UI path |
| `docs/change-log/2026-09-14-corporate-billing-pilot-flags-enabled-prod.md` | This file | Required log for a production change on a money-touching surface |

## 7. Before / after

```
-- Before
settings.corporate_subscription_billing_enabled = false
corporate_accounts(name='Spinr').subscription_billing_pilot_enabled = false  -- (column didn't exist until this session applied migration 419)
```

```
-- After
settings.corporate_subscription_billing_enabled = true
corporate_accounts(name='Spinr').subscription_billing_pilot_enabled = true
```

## 8. Rollback plan

**Without a second deploy, no migration needed** — both are plain data toggles:

```sql
UPDATE public.settings SET corporate_subscription_billing_enabled = false WHERE id = 'app_settings';
UPDATE public.corporate_accounts SET subscription_billing_pilot_enabled = false WHERE name = 'Spinr';
```

Equivalently, either flag can be flipped back off from the admin dashboard itself (Settings
page for the global flag; the new pilot toggle on Spinr's own subscription page for the
per-company flag) — no direct DB access required to undo this.

If this is ever reverted past the migration too: `ALTER TABLE public.corporate_accounts DROP
COLUMN IF EXISTS subscription_billing_pilot_enabled;` (from migration 419's own header).

No Stripe object has been created, so there is nothing to unwind on Stripe's side as of this
entry.

## 9. Verification performed

- [x] Confirmed column exists post-migration (`information_schema.columns`) with the expected
  default (`false`, `NOT NULL`).
- [x] Confirmed both flags read back as `true` after the update, via fresh `SELECT`s (not
  inferred from the `UPDATE` statement succeeding).
- [x] Confirmed exactly one row exists in `settings` before running an unconditional `UPDATE`
  against it (to rule out a table-wide unintended update).
- [x] Confirmed the two audit rows were actually written (`SELECT` after `INSERT`, not assumed).
- [x] Checked the real blocking precondition (empty plan catalog) directly against production
  data rather than assuming the flags alone were the only gate.
- [ ] Not verified: whether a Stripe default payment method exists for Spinr's customer — no
  Stripe API access available in this session.
- [ ] Not run through the real admin-dashboard UI or API endpoints — done via direct SQL/MCP
  because no staging exists and no admin browser session was available here.

## What was NOT verified

- Stripe-side payment method status for Spinr's on-file Stripe customer (see above).
- Whether anyone with admin access will create a subscription plan next, which is the actual
  next gate before any real Stripe subscription could be created for Spinr.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (two `UPDATE` statements, or two dashboard toggles)
- [x] Blast radius is stated, not assumed — verified against live production data, not just
  the PR's prior static analysis
- [x] No silent behavior change — this was an explicit, confirmed user request, and this
  document exists specifically so the change isn't silent
