# C43 — RLS-Enable Migration Readiness

**Status:** prepared, not applied. Migration
`backend/migrations/378_enable_rls_settings_document_files_driver_imports.sql`
exists and has been verified locally, but has **not** been run against
staging or production, and must not be until the condition below is met.

## What the migration does

Runs exactly 4 statements, no new policies:

```sql
ALTER TABLE public.document_files     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.settings           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.driver_csv_import  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.driver_bank_import ENABLE ROW LEVEL SECURITY;
```

These are the 4 tables `ACTION_ITEMS.md` C43 found with RLS disabled in
production, including `settings` (Stripe/Twilio/Google Maps API keys) and
`document_files` (PIPEDA-sensitive driver documents). Zero policies are
added deliberately — with no policy, RLS defaults to deny-all for the
`anon`/`authenticated` roles, and the backend's service-role connections
bypass RLS entirely regardless of whether it is enabled, so nothing the
backend does changes.

## Why this is currently believed low-risk

This document does not re-derive the risk assessment — see ACTION_ITEMS.md
C43 for the full blast-radius check already performed: a grep across
`rider-app`, `driver-app`, and `admin-dashboard` for any `createClient(...)`
call reading these 4 tables with an anon/publishable key found zero real
matches; the only anon-key `createClient` anywhere in the repo is a dead,
unwired scaffold file. Every real read/write to these 4 tables goes through
the backend's `SUPABASE_SERVICE_ROLE_KEY`, which bypasses RLS. That check
was static (grep only), not verified against staging/canary — C43 already
flags this as a hypothesis to re-check, not a guarantee.

## Required re-confirmation step before anyone runs `--apply`

Code changes between now and whenever this is picked back up, so **re-run
the same grep** across all three frontend surfaces before applying, to
catch any new code added since the original check:

```bash
grep -rn "createClient" rider-app driver-app admin-dashboard \
  --include="*.ts" --include="*.tsx" --include="*.js" | grep -v node_modules
```

Expect the same result as before: no real anon-key usage against
`settings`, `document_files`, `driver_csv_import`, or `driver_bank_import`
(the dead `frontend/config/supabase.ts` scaffold aside). If this turns up
a new, real, shipped anon-key consumer of any of these 4 tables, **stop** —
that consumer needs an explicit RLS policy in a follow-up migration before
this one can be applied without breaking it.

## What would make this unsafe

The whole "low risk" case rests on there being no legitimate anon/
authenticated-role reader of these tables today. It stops being safe the
moment any of the following happens without a matching policy being added
first:
- A rider/driver/admin app surface starts calling Supabase directly with
  the anon or publishable key against any of these 4 tables (the exact
  gap the original blast-radius check found closed today, but which a
  future PR could reopen without anyone connecting it back to this item).
- `frontend/config/supabase.ts`'s scaffold is ever wired into a real build
  instead of staying a dead placeholder file.
- A future feature deliberately wants public/anon read access to part of
  `settings` (e.g. a public feature-flag row) — that needs its own scoped
  policy, not a reason to skip enabling RLS on the rest of the table.

## Migration review

Reviewed against `spinr-migration-reviewer`'s checklist:

```
SPINR MIGRATION REVIEW — backend/migrations/378_enable_rls_settings_document_files_driver_imports.sql
==================================
NUMBERING:     OK (378 is next free after 377; verified via `ls backend/migrations | sort -V | tail`)
APPEND-ONLY:   OK (new file only, no edits to a merged migration)
RLS:           OK, WITH NOTE — this enables RLS on 4 EXISTING tables with
               zero policies (deny-all to anon/authenticated), not the
               "new user-data table ships policies in the same migration"
               case the checklist is normally written for. Deny-all matches
               current real-world behavior per the blast-radius check
               (no legitimate anon/authenticated consumer exists), so this
               is treated as OK rather than MISSING — but it is a
               deliberate zero-policy design, not an oversight, and is
               documented as such in the migration's own header comment.
REVERSIBILITY: OK (rollback = 4 DISABLE ROW LEVEL SECURITY statements,
               documented at the top of the file, verified to work locally)
FORWARD-COMPAT: OK, WITH NOTE — `ENABLE ROW LEVEL SECURITY` takes a brief
               ACCESS EXCLUSIVE lock per table (catalog-only change, not a
               row rewrite/scan, so near-instantaneous), but none of the 4
               target tables are on a hot request path (not `rides`,
               `drivers`, `users`, or `wallet_*`), so this is low concern.
               Whoever applies it should still run the 4 statements
               individually rather than as one blind paste, so a problem
               on one table doesn't obscure the others.
INDEXES:       N/A (no new query pattern)
MONEY SAFETY:  N/A (no money/credit function touched)
RETENTION:     N/A (none of the 4 tables are trip/insurance/safety
               retention-sensitive tables)

BLOCKERS
  (none)

WARNINGS
  - Zero policies means any future legitimate anon/authenticated consumer
    of these tables would be silently denied, not just left insecure —
    a real future use case needs its own scoped policy added in a
    follow-up migration, not just "add a policy later" as an afterthought.
  - This is a security-hardening change on a table holding live third-party
    secrets (`settings`: Stripe/Twilio/Google Maps keys). Low risk today
    per the blast-radius check, but the check is static-grep-only and
    should be re-run (see "Required re-confirmation step" above)
    immediately before applying, not trusted as permanently current.

IMPACT MISMATCHES
  - N/A — no PR opened yet; this migration has not been submitted for
    review via a PR, so there is no PR body to cross-check against.

VERDICT: SAFE TO APPLY — once the deferral condition below is explicitly
lifted by the product owner, and the re-confirmation grep above has been
re-run.
```

## Gating condition — do not skip

This migration requires the deferral recorded in ACTION_ITEMS.md C43 to be
**explicitly lifted by the product owner** before anyone runs
`run_migrations.py --apply` (or otherwise executes these statements against
staging or production). As of 2026-08-31, the deferral is **still in
force** — the legacy-migration/A41-family work it was deferred behind has
not concluded, confirmed again with the product owner this session. This
document is the prepared procedure for whenever that changes, not the
sign-off itself.
