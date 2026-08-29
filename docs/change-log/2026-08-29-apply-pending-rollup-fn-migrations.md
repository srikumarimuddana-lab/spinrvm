# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session on `staging`) |
| Surface(s) | backend (production database) |
| Domain (Sentry tag) | admin, drivers |
| PR / commit link | migration `372` on `staging`; production DB changed directly |
| Related issue or gap ID | `PGRST202` on `GET /api/admin/drivers/stats`, request `cf02ec35` |

## 1. Issue / gap identified

`GET /api/admin/drivers/stats` returned 500 with
`Could not find the function public.admin_driver_earnings_rollup(p_driver_ids)
in the schema cache` (`PGRST202`).

The reported 500 was one symptom of a wider drift: **migrations 363–371 (11
files) were committed but never applied to production.** The newest applied
migration was `359` (2026-08-22).

## 2. Root cause

**No deploy workflow applies migrations.** `deploy-fly.yml` and
`deploy-backend.yml` build and ship the backend; neither runs
`run_migrations.py`. Migrations are applied by hand (production rows show
`applied_by = 'claude-session-apply'` / `postgres`). Code that depends on a new
database object therefore ships and starts calling it before the object exists.

Two migrations in the unapplied batch create functions the deployed code calls
with no fallback:

| Missing object | Migration | Live impact |
|---|---|---|
| `admin_driver_earnings_rollup(text[])` | `370_driver_earnings_rollup_fn` | `GET /admin/drivers/stats` → 500 (the reported error) |
| `route_gap_latest_captures(uuid[])` | `371_route_gap_latest_captures_fn` | route-gap monitor loop failed every 15 s on every replica |

`371`'s own commit message (`258ded5`) recorded that the function "was NOT
created in production" — the code shipped anyway.

## 3. Fix / remediation

Applied the two function migrations to production and recorded them in
`public.schema_migrations` with their real sha256, matching what
`run_migrations.py` would have written. Reloaded the PostgREST schema cache
(`NOTIFY pgrst, 'reload schema'`) — without it the function exists but
PostgREST keeps returning `PGRST202`.

**Then found and closed a privilege gap in both migrations** (new migration
`372`, see §4).

Scope was deliberately limited to these two migrations. The other nine pending
files were **not** applied — see "What was NOT verified".

## 4. Risk & impact on existing functionality

**Security defect found in the migrations being applied.** Both `370` and `371`
end with `REVOKE EXECUTE ... FROM anon, authenticated`, and both state in
their own comments that this stops a leaked anon/authenticated key from reading
the data. It does not. A new function carries a default `EXECUTE` grant to
`PUBLIC`; revoking the two roles by name leaves that grant, and both inherit
through it. Verified on production immediately after applying:

```
admin_driver_earnings_rollup  proacl: =X/postgres | postgres=X/postgres | service_role=X/postgres
                                      ^^^^^^^^^^ PUBLIC
has_function_privilege('anon', ..., 'EXECUTE') = true
```

Both are `SECURITY DEFINER`, so they bypass RLS. As applied, they were callable
as PostgREST RPC endpoints using the anon key that ships inside the mobile
apps, exposing **fleet-wide driver earnings** and **per-ride location timing**
(PIPEDA-relevant). Migration `354` had already established the correct pattern
(`REVOKE ... FROM PUBLIC, anon, authenticated` + `GRANT ... TO service_role`);
`370`/`371` simply did not follow it.

Migration `372` applies `354`'s remedy to both functions. Post-fix ACL is now
byte-identical to `admin_ride_money_rollup`:
`postgres=X/postgres | service_role=X/postgres`, with `anon`/`authenticated`
denied and `service_role` retained.

**Blast radius: additive and narrow.** Both migrations are
`CREATE OR REPLACE FUNCTION` only — no table, column, index, constraint, or row
is created or altered. Nothing else in the schema references either function.
The only readers are the two call sites that were already failing:
`routes/admin/drivers.py:794` and `utils/route_gap_monitor.py:124`. No ride
state, money write, wallet delta, or insurance-period row is touched.

A repo-wide sweep after the lockdown found **zero** `SECURITY DEFINER`
functions in `public` still executable by `anon` or `authenticated`.

## 5. User-experience effect

- **Internal admin:** the Drivers → Stats page stops 500-ing and renders
  earnings again. Previously fully broken for every admin.
- **Nobody else.** The route-gap monitor is an internal safety/telemetry loop
  with no user-facing surface; it resumes detecting mid-trip location outages,
  which it had not been doing since `258ded5` deployed.
- No rider- or driver-visible change. No copy change. Not visible mid-ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/372_lockdown_rollup_fns_public_execute.sql` | New migration: revoke `PUBLIC` execute on both new functions, grant `service_role` | `370`/`371` left the default `PUBLIC` grant in place, making two `SECURITY DEFINER` functions anon-callable |

Production database (no repo diff): `admin_driver_earnings_rollup` and
`route_gap_latest_captures` created; three `schema_migrations` rows inserted
(`370`, `371`, `372`).

`370` and `371` were **not** edited — they are applied and checksummed, and
`run_migrations.py` hard-fails on a checksum change. Migrations are append-only.

## 7. Before / after

```sql
-- Before (in 370 and 371) — PUBLIC keeps EXECUTE, anon inherits it
REVOKE EXECUTE ON FUNCTION public.admin_driver_earnings_rollup(text[])
    FROM anon, authenticated;
```

```sql
-- After (migration 372, matching migration 354's pattern)
REVOKE EXECUTE ON FUNCTION public.admin_driver_earnings_rollup(text[])
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_driver_earnings_rollup(text[])
    TO service_role;
```

## 8. Rollback plan

Both functions are additive and independently droppable, per each migration's
own documented rollback:

```sql
DROP FUNCTION IF EXISTS public.admin_driver_earnings_rollup(text[]);
DROP FUNCTION IF EXISTS public.route_gap_latest_captures(uuid[]);
DELETE FROM public.schema_migrations
 WHERE filename IN ('370_driver_earnings_rollup_fn.sql',
                    '371_route_gap_latest_captures_fn.sql',
                    '372_lockdown_rollup_fns_public_execute.sql');
NOTIFY pgrst, 'reload schema';
```

No deploy is needed to roll back — this is a database-only change. Dropping
returns the endpoints to their current broken state, so rollback is only
sensible if a function is found to be wrong, not as a fix. **Do not roll back
`372` alone** — that re-opens anon execute on both functions.

No data was written or migrated, so there is no data-level remediation to plan:
both functions are `STABLE` and read-only.

## 9. Verification performed

- [x] **`admin_driver_earnings_rollup` validated against a manual SQL sum** over
      the same predicate (completed, non-legacy, by driver): function returned
      `6.35`, manual sum `6.35`, driver counts equal, `totals_match = true`.
      Empty-array input returns `{"by_driver":{},"total":0}` rather than erroring —
      the caller guards the empty case, but it is safe either way.
- [x] **`route_gap_latest_captures` diffed against a SQL reproduction of the
      per-ride logic it replaced** (newest non-NULL `captured_at`, else newest
      non-NULL `timestamp`) over 20 rides: 11 rides with a time in both, 11
      returned by the function, **0 mismatches**. Rides with neither are absent
      from the result, which the caller reads as `None` — same as the old
      no-rows path.
- [x] Signatures match the call sites exactly: `p_driver_ids text[]` for
      `rpc("admin_driver_earnings_rollup", {"p_driver_ids": [...]})`, and
      `p_ride_ids uuid[]` for `rpc("route_gap_latest_captures", {"p_ride_ids": [...]})`.
- [x] `service_role` retains EXECUTE on both (the backend's identity);
      `anon`/`authenticated` denied. ACLs match `admin_ride_money_rollup`.
- [x] Repo-wide sweep: no `SECURITY DEFINER` function in `public` is
      anon/authenticated-executable.
- [x] PostgREST schema cache reloaded after both the create and the grant change.
- [x] `schema_migrations` rows written with the real file sha256, so a future
      `run_migrations.py` run treats all three as applied and does not re-run.
- [x] Blast-radius grep: every reference to both function names across
      `backend/`, `admin-dashboard/`; every pending migration checked for code
      that already depends on it.

### What was NOT verified

- **The failing endpoint was not re-called.** This container has no
  `SUPABASE_SERVICE_ROLE_KEY` (no `backend/.env`) and cannot reach the
  deployed API, so `GET /api/admin/drivers/stats` was verified at the database
  layer — function exists, correct signature, correct grants, correct results,
  schema cache reloaded — **not** by observing a 200 from the live endpoint.
  Worth one manual load of the admin Drivers → Stats page to confirm.
- **The remaining 9 pending migrations were deliberately not applied**, per an
  explicit scoping decision. Still pending: `363`, `364`, `365`, `366`, `367`,
  `368`, `369`, `370_add_unresolved_at_completion_status_to_gap_events`,
  `370_location_marker_write_gate_flag`. Consequences that remain live:
  - `settings.ai_public_chat_enabled` missing → public AI chat reads as
    disabled (`.get()` default, no crash).
  - `settings.legacy_ride_badge_enabled` missing → legacy ride badge never renders.
  - `settings.location_marker_write_gate_enabled` missing → gate reads as off;
    an admin toggling it would error on write.
  - `368` (indexes) / `369` (drop duplicate indexes) → query performance only.
  - `365`–`367` → FAQ content not seeded/merged.
  - `370_add_unresolved_at_completion_status_to_gap_events` is a **no-op in
    practice**: production's `ride_location_gap_events_status_valid` CHECK
    already allows `unresolved_at_completion`, so the write at
    `route_gap_monitor.py:277` was never failing. It is unapplied on paper only.
- **No pytest run.** PyPI returns 403 under this environment's network policy,
  so backend deps cannot be installed here (same limitation as the previous
  fix in this session). No Python code changed in this commit, so nothing new
  needs a unit test, but the existing suite was not exercised.
- The root cause — no migration step in the deploy pipeline — is **reported,
  not fixed**. The drift will recur on the next migration-bearing deploy.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
