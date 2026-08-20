# Change Impact & Risk Log — SECURITY: analytics function EXECUTE grants

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend (database) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | Found while executing migrations 350–352 against a real Postgres |

## 1. Issue / gap identified

Migrations 350, 351 and 352 ended each function with:

```sql
REVOKE EXECUTE ON FUNCTION public.<fn>(...) FROM anon, authenticated;
```

**That statement is a no-op.** Postgres grants `EXECUTE` to `PUBLIC` by
default on `CREATE FUNCTION`. Revoking from `anon` and `authenticated`
removes direct grants those roles never had; both keep `EXECUTE` through
`PUBLIC`.

These are `SECURITY DEFINER` functions — they run as the owner and bypass
RLS — and they return aggregate business data: gross bookings, ride counts,
driver utilization, cancellation breakdowns. In a Supabase project, `anon` is
the role the publicly-distributed anon key authenticates as, and PostgREST
exposes `public` functions at `/rest/v1/rpc/<name>`.

Found only by applying the migrations to a real PostgreSQL 16 instance and
querying `has_function_privilege`. **Every static check had passed** — the
text contained `REVOKE EXECUTE`, `SECURITY DEFINER`, and a pinned
`search_path`, so reading the SQL could not reveal it.

## 2. Root cause

Migrations 350–352 copied the revoke line from migrations 165/166, which use
the same ineffective form. The repo already contains the correct pattern in
33 other migrations — e.g. `purge_pii_retention` (50/296) and
`encrypt/decrypt_driver_pii` (216):

```sql
REVOKE EXECUTE ON FUNCTION <fn> FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION <fn> TO service_role;
```

The weaker form was propagated rather than the correct one.

## 3. Fix / remediation

All six functions declared by migrations 350–352 now use the repo's correct
pattern. `service_role` must be granted back explicitly: it does **not**
inherit `EXECUTE` any other way, and the backend calls these functions
through it — revoking `PUBLIC` without that `GRANT` would have broken every
analytics endpoint in production.

Each migration carries a comment explaining why `PUBLIC` is named, so the
next author does not drop it back to the weaker form. The three
migration-assertion test classes now require `FROM PUBLIC, anon,
authenticated` and a matching `GRANT  EXECUTE`, so this specific regression
cannot pass CI again.

Migrations 350–352 were **unmerged and unapplied anywhere** when this was
found, so they were corrected in place rather than amended by a follow-up
migration. The append-only rule protects migrations that have already been
applied; these had not been.

## 4. Risk & impact on existing functionality

**Blast radius: the six functions declared in 350–352.** No other function's
grants are touched by this change.

The `GRANT ... TO service_role` is the load-bearing half. Verified on the
local instance: after the fix, `anon`=false, `authenticated`=false,
`service_role`=true on all six, and every function still returns identical
values to before the grant change (revenue 115.00, matched 4, utilization
40%, deadhead 20%, bookings 115.00).

**If `service_role` does not exist in the target database, these migrations
now fail at the `GRANT`.** That is the intended behaviour — failing loudly
beats silently leaving the functions world-executable — and it matches the
33 existing migrations that already `GRANT ... TO service_role` unguarded, so
any database where those applied has the role.

## 5. User-experience effect

**None.** No admin, rider, driver, or corporate surface changes. This is a
database privilege correction on functions that are called only by the
backend's service role.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/350_analytics_regina_buckets_and_area_scope.sql` | Revoke names `PUBLIC`; grant to `service_role`; explanatory comment | The revoke was a no-op |
| `backend/migrations/351_marketplace_funnel_and_supply_fns.sql` | Same | Same |
| `backend/migrations/352_efficiency_and_financial_fns.sql` | Same | Same |
| `backend/tests/test_admin_analytics_coverage.py` | Grant assertions in all three migration test classes | Stop the weaker form passing CI |

## 7. Before / after

```sql
-- Before — no-op: anon and authenticated keep EXECUTE via PUBLIC.
-- proacl showed {=X/postgres,postgres=X/postgres}; the `=X` IS the PUBLIC grant.
REVOKE EXECUTE ON FUNCTION public.admin_financial_metrics(timestamptz, timestamptz, text)
    FROM anon, authenticated;
```

```sql
-- After — PUBLIC named, service_role granted back explicitly.
REVOKE EXECUTE ON FUNCTION public.admin_financial_metrics(timestamptz, timestamptz, text)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_financial_metrics(timestamptz, timestamptz, text)
    TO service_role;
```

## 8. Rollback plan

The functions are not yet applied to any environment, so there is nothing to
roll back operationally. If a rollback were needed after deployment:

```sql
GRANT EXECUTE ON FUNCTION public.<fn>(...) TO PUBLIC;
```

That restores the previous (insecure) state and would only be appropriate if
something unexpected turns out to depend on `PUBLIC` execute — in which case
the right fix is to grant that specific role, not `PUBLIC`.

## 9. Verification performed

- [x] **Reproduced the defect on a real database.** PostgreSQL 16.13, minimal schema, migrations applied with `ON_ERROR_STOP=1`. `has_function_privilege('anon', ...)` returned **true** for all six functions; `proacl` showed the `=X/postgres` PUBLIC entry.
- [x] **Verified the fix on the same instance.** After re-applying, `anon`=false, `authenticated`=false, `service_role`=true for all six.
- [x] **Verified the fix changed nothing else** — every function re-executed and returned values identical to the pre-fix run.
- [x] Tests updated and passing — **101 passed**. The three migration test classes now fail if the weaker revoke form reappears.
- [x] Confirmed the chosen pattern matches the repo's established correct form (33 migrations, incl. `purge_pii_retention` and `encrypt/decrypt_driver_pii`).

## 10. What was NOT verified

- **The production exposure was not confirmed against the live Supabase project.** The reasoning — `anon` is the public anon key's role, PostgREST exposes `public` functions over RPC — is standard Supabase behaviour, but whether these specific functions are reachable from the internet today depends on that project's PostgREST config (`db-schemas`, exposed schema settings) and on whether `anon` is actually granted at the schema level. **Someone with production access should confirm before deciding how urgent the pre-existing issue below is.**
- **`service_role`'s existence in the production database was assumed**, on the basis that 33 existing migrations grant to it unguarded. Not directly checked.
- The local schema is a minimal stand-in, not a production dump — column types match the migrations that created them, but row volumes, real indexes, and RLS policies differ.

## 11. PRE-EXISTING ISSUE — NOT FIXED HERE

**12 migrations use the same ineffective revoke form**, and 3 of the 6
functions fixed here were the only overlap. The remaining affected
`SECURITY DEFINER` functions include:

`admin_driver_acceptance_rates`, `admin_driver_offer_stats`,
`admin_driver_offer_trends`, `admin_earnings_daily_series`,
`admin_earnings_overview_agg`, `admin_earnings_refunds`,
`admin_payout_stats`, `admin_payouts_overview_aggregates`,
`admin_ride_daily_counts`, `admin_ride_money_rollup`.

Several of these return money. **This was deliberately left alone**: changing
grants on functions already applied in production is a security change with
real breakage risk (a missing `service_role` grant takes down earnings and
payouts), it is outside the scope of the analytics work that was requested,
and it warrants its own audit and its own migration. Raised with the user
rather than swept in silently.

Recommended follow-up: one migration re-issuing
`REVOKE ... FROM PUBLIC, anon, authenticated` + `GRANT ... TO service_role`
for every `SECURITY DEFINER` function in `public`, verified the same way this
one was.

## 12. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] Defect reproduced and fix verified on a real database, not reasoned about
- [x] Pre-existing scope explicitly excluded and escalated rather than silently expanded
