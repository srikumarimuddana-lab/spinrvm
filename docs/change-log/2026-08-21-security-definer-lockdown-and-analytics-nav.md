# Change Impact & Risk Log — SECURITY DEFINER lockdown + Analytics nav cleanup

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend (migration), admin-dashboard |
| Domain (Sentry tag) | auth, admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` (follow-up to merged PR #4298) |
| Related issue or gap ID | §11 of `2026-08-20-analytics-function-grants.md` (pre-existing scope, escalated then) |

## 1. Issue / gap identified

**A. Security.** An audit of the live database found **18 `SECURITY DEFINER`
functions in `public` executable by `anon`** — the role Supabase's
publicly-distributed anon key authenticates as, which also holds `USAGE` on
`public`, so PostgREST can route `/rest/v1/rpc/<name>` to them.
`SECURITY DEFINER` runs as the owner and bypasses RLS, and **none** carried an
internal auth guard (no `auth.uid()`, `auth.role()`, or JWT check) — the grant
model was the only control, and it was broken by the no-op
`REVOKE ... FROM anon, authenticated` pattern.

Ten were read-only money/ops aggregates. **Seven mutate state**, including
`wallet_pay_for_ride` (moves money), `corporate_section_spend_add` (arbitrary
spend delta), three promo functions, and `record_insurance_period_transition`
(writes the regulatory audit table).

**B. Navigation.** PR #4298 folded Driver Offers and Demand Forecast into
Analytics tabs but left both in the sidebar, so the same content appeared in
two places. The tabs were also not addressable — `Tabs` used `defaultValue`,
so no tab could be linked or bookmarked.

## 2. Root cause

**A.** Postgres grants `EXECUTE` to `PUBLIC` on `CREATE FUNCTION`. Revoking
from `anon`/`authenticated` removes direct grants those roles never held while
both keep access via `PUBLIC`. The repo contains the correct form
(`FROM PUBLIC, anon, authenticated` + `GRANT ... TO service_role`) in 33
migrations, but 12 copied the weaker one.

**B.** Deliberate at the time — the sidebar entries were kept so bookmarks
didn't break in the same change that moved the content. Leaving them
permanently was the wrong call.

## 3. Fix / remediation

**A.** Migration 353 sweeps every `SECURITY DEFINER` function in `public` that
is `anon`- or `authenticated`-executable, revokes `PUBLIC, anon,
authenticated`, and grants `service_role` **in the same loop iteration**.

A sweep rather than a list of 18 signatures, because the defect is a *copied
pattern* — a fixed list would go stale the next time a migration uses the weak
form. It `RAISE NOTICE`s each function it touches, so the runner's output is an
audit record rather than a silent bulk privilege change, and ends with a
post-condition that raises if anything is still reachable.

**B.** Removed both sidebar entries; made tabs URL-addressable via `?tab=`;
converted `/dashboard/driver-offers` and `/dashboard/forecast` into server-side
`redirect()`s to `?tab=offers` / `?tab=forecast`.

## 4. Risk & impact on existing functionality

**A — the production database was already corrected before this migration was
written.** The grants were fixed directly against the live database on
2026-08-20 after the finding was raised. I verified the current state
read-only: **0** anon-executable `SECURITY DEFINER` functions remain (of 45
total), and **0** lack a `service_role` grant.

**So migration 353 is expected to be a no-op against production.** Its purpose
is reproducibility: `schema_migrations` carries no record of the hotfix, so a
staging refresh, DR restore, or Supabase branch database rebuilt from
migrations would come back vulnerable. This file makes the corrected state part
of the schema's definition.

Blast radius of the migration: privileges only. No table, column, index,
policy, or function *body* is altered; no row is written. Trigger functions are
unaffected in practice — Postgres does not re-check `EXECUTE` when a trigger
fires, so revoking direct-invocation rights does not disable any trigger.

The `GRANT ... TO service_role` inside each iteration is the load-bearing
safety property: a revoke without it would strand the backend, which reaches
these functions only as `service_role`.

**B —** frontend only. The two panel components are unchanged and still
rendered by the Analytics tabs, so no implementation was orphaned. Old links
keep working through the redirects. `useSearchParams()` required a `Suspense`
boundary or the production build fails on the prerender pass; added.

## 5. User-experience effect

**Internal admin only.** Nothing rider-, driver-, or corporate-facing; nothing
visible mid-session to anyone using the apps.

Admins lose two sidebar entries and gain linkable tabs. Anyone with
`/dashboard/driver-offers` or `/dashboard/forecast` bookmarked is redirected to
the matching tab rather than 404ing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/353_revoke_public_execute_on_security_definer_fns.sql` | New. Sweep + post-condition | Codify the hotfix so rebuilt environments aren't vulnerable |
| `backend/tests/test_admin_analytics_coverage.py` | +8 tests | Pin the sweep's shape and safety properties |
| `admin-dashboard/src/components/sidebar.tsx` | Removed 2 entries + 2 orphaned icon imports | Content lives in tabs now |
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | URL tab state, `TAB_IDS` allowlist, `Suspense` wrapper | Make tabs addressable |
| `admin-dashboard/src/app/dashboard/driver-offers/page.tsx` | → server `redirect()` | Preserve bookmarks |
| `admin-dashboard/src/app/dashboard/forecast/page.tsx` | → server `redirect()` | Preserve bookmarks |

## 7. Before / after

```sql
-- Before — the pattern in 12 migrations. anon keeps EXECUTE via PUBLIC.
REVOKE EXECUTE ON FUNCTION public.wallet_pay_for_ride(uuid, uuid, numeric, numeric)
    FROM anon, authenticated;
```

```sql
-- After — migration 353, applied to whatever is actually still reachable
EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated', f.sig);
EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role', f.sig);
RAISE NOTICE 'migration 353: locked down %', f.sig;
```

## 8. Rollback plan

The migration changes nothing in production (already corrected), so there is
nothing to roll back there. For an environment where it *did* act, the
rollback SQL is in the migration header — it re-grants `PUBLIC`, restoring the
insecure state, and is only appropriate if something unexpected turns out to
depend on the `PUBLIC` grant. The correct response in that case is granting
that specific role, not `PUBLIC`.

Frontend: `git revert` is complete — no migration, no persisted state.

## 9. Verification performed

- [x] **Reproduced the defect and the fix on a real database.** PostgreSQL 16.13: created a `SECURITY DEFINER` function with only the weak revoke → `anon_exec = true`. Applied migration 353 → `anon_exec = false`, `authenticated = false`, `service_role = true`. The `RAISE NOTICE` named the function it touched.
- [x] **Idempotency confirmed** — re-running emits `no-op — no SECURITY DEFINER function in public was anon/authenticated-executable` and changes nothing. This is the expected production outcome.
- [x] **Verified the migration doesn't disturb the analytics functions** from 350–352 on the same instance: `admin_analytics_overview`, `admin_marketplace_funnel`, `admin_financial_metrics` all return values identical to before.
- [x] **Production state verified read-only** (`pg_proc`/`pg_namespace` only): 0 of 45 `SECURITY DEFINER` functions are anon-executable; 0 lack `service_role`.
- [x] Backend tests — **109 passed** (101 prior + 8 new).
- [x] `ruff check` + `ruff format` clean.
- [x] **Real production build run** — `npm run build`, exit 0. All three routes present (`/dashboard/analytics`, and the two redirect routes). `tsc --noEmit` exit 0.
- [x] `npm run lint` — **0 errors**; warning count dropped 336 → 334 after removing the two imports my own change orphaned.
- [x] Migration numbering — 353 confirmed free; `schema_migrations` confirmed to hold no 35x entry beyond `35_refresh_tokens_revoked_at_backfill.sql`.

## 10. What was NOT verified

- **I did not apply migration 353 to production.** It should be a no-op, but that is an inference from the read-only privilege check, not an observed run. Apply it through `run_migrations.py` so `schema_migrations` records it — otherwise the reproducibility gap this migration exists to close stays open.
- **I never confirmed the exposure was reachable over HTTP.** The database half is proven (grants + schema `USAGE`); whether PostgREST actually routed those RPCs depends on its `db-schemas` config, which isn't readable from SQL. I did not attempt to call any function as `anon` — probing a live money path isn't appropriate unasked. **The severity of what was exposed between deploy and hotfix is therefore still unquantified**, and worth a look at PostgREST logs if anyone wants to rule out actual use.
- **Who applied the production hotfix, and exactly when, is not established** — only that the grants changed between two of my queries roughly 20 minutes apart, and that no migration recorded it.
- **The remaining 12 migrations still contain the weak `REVOKE` text.** Migration 353 corrects the *database*, but re-running any of those older migrations against a fresh environment would re-grant `PUBLIC` before 353 runs and re-fix it. Ordering saves it; the source text is still wrong and should be swept separately.
- **Nothing was rendered.** The redirects, the tab deep-linking, and the sidebar with two fewer entries are covered by typecheck and build only. No repo visual-regression tooling exists for admin-dashboard.
- **The redirects were not exercised** — no request was made to `/dashboard/driver-offers` to confirm it lands on `?tab=offers`.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] Defect and fix both reproduced on a real database, not reasoned about
- [ ] **Open: apply migration 353 via `run_migrations.py`** so the fix is recorded (see §10)
- [ ] **Open: manual render pass** on the redirects and tab deep-links
