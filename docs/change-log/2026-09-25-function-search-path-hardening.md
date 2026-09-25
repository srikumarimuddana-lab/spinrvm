# Change Impact & Risk Log: pin search_path on 16 public functions (LIVE-002)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (agent), branch `claude/fix-db-function-search-path` |
| Surface(s) | backend (Postgres functions only) |
| Domain (Sentry tag) | dispatch, payments, safety |
| PR / commit link | not opened yet (local branch) |
| Related issue or gap ID | `docs/audit/clean-sheet/10-live-checks.md` §3: LIVE-002 (done here), LIVE-003 (**deliberately not done**, see §3a) |

## 1. Issue / gap identified

The Supabase security linter (`function_search_path_mutable`, lint 0011) reports 16 `public` functions with a role-mutable `search_path`. These include four PostGIS dispatch-area functions, two money RPCs, and the append-only guards on `audit_logs`, `financial_events` and `disputes`.

## 2. Root cause

These functions were created without a `SET search_path` clause. They come from the baseline schema and early migrations: 51, 52, 55, 56, 57/434, 58/289/295, 75, 77/80, 94, 262, 286, 435, 456 and 05/27/206. Each unqualified name in their bodies therefore resolves against whatever `search_path` the caller has. Today that is the database default, `"$user", public, extensions, topology, tiger` (read from `pg_db_role_setting`, setrole 0). So the bodies work, but only by accident of caller configuration, and objects in a caller-controlled schema or in `pg_temp` could shadow them.

## 3. Fix / remediation

Migration `backend/migrations/474_function_search_path_hardening.sql` issues one `ALTER FUNCTION public.<fn>(<identity types>) SET search_path = ...` per function. It changes no function body, signature, grant, owner or SECURITY mode. A pre-check raises if any signature is missing (drift). A post-condition raises if any target lacks a pinned path, or if a PostGIS caller's path lacks `extensions`.

Chosen paths, read from prod `pg_proc.prosrc` and `pg_extension`/`pg_namespace` on 2026-09-25. PostGIS 3.3.7 is installed in schema **`extensions`**:

| Function | Identity signature | Extension / object references found in body | Chosen `search_path` |
|---|---|---|---|
| `match_and_claim_driver` | `(text, double precision, double precision, double precision, double precision)` | `geography` type (DECLARE + casts), `ST_SetSRID`, `ST_MakePoint`, `ST_DWithin`, `ST_Distance`; `drivers` rowtype/table | `public, extensions, pg_temp` |
| `find_nearby_drivers` | `(double precision, double precision, double precision)` | `ST_Y`, `ST_X`, `geometry`/`geography` casts, `ST_Distance`, `ST_SetSRID`, `ST_MakePoint`, `ST_DWithin`, geography `<->` operator; `drivers` | `public, extensions, pg_temp` |
| `get_service_area_for_point` | `(double precision, double precision)` | `ST_Intersects`, `ST_SetSRID`, `ST_MakePoint`, `geography` cast; `service_areas` | `public, extensions, pg_temp` |
| `update_driver_location` | `(text, double precision, double precision)` | `ST_SetSRID`, `ST_MakePoint`, `geography` cast; `drivers` | `public, extensions, pg_temp` |
| `fare_split_pay_share` | `(uuid, uuid, numeric)` | none; `wallets`, `fare_split_participants` (public) | `public, pg_temp` |
| `increment_promo_uses` | `(uuid, integer)` | none; `promotions` (public) | `public, pg_temp` |
| `_audit_logs_immutable` | `()` | none (RAISE only) | `public, pg_temp` |
| `audit_logs_block_delete` | `()` | none (`current_setting`, RAISE) | `public, pg_temp` |
| `audit_logs_block_update` | `()` | none (RAISE only) | `public, pg_temp` |
| `_financial_events_immutable` | `()` | none (`current_setting`, RAISE) | `public, pg_temp` |
| `_financial_event_entries_immutable` | `()` | none (RAISE only) | `public, pg_temp` |
| `_subscription_payments_immutable` | `()` | none (RAISE only) | `public, pg_temp` |
| `block_mutation_on_immutable_table` | `()` | none (RAISE, `TG_*`) | `public, pg_temp` |
| `disputes_block_delete` | `()` | none (RAISE only) | `public, pg_temp` |
| `safety_incidents_set_updated_at` | `()` | none (`NOW()`) | `public, pg_temp` |
| `update_updated_at_column` | `()` | none (`now()`) | `public, pg_temp` |

No body references pgcrypto, uuid-ossp, http, pgsodium, vault, topology or tiger. `pg_catalog` is always searched implicitly first, which covers `now()`, `current_setting()` and the other built-ins. `pg_temp` is listed last explicitly so it can never shadow anything.

**Alternative considered:** schema-qualify every reference inside each body (`extensions.ST_MakePoint(...)`, `public.drivers`, and so on) with `CREATE OR REPLACE`. **Rejected:** that re-forks 16 bodies, including four PostGIS functions, and risks transcription drift from the prod source. It trips migration-check CHECK G (CREATE OR REPLACE conflict). It would also not satisfy lint 0011 anyway, because the lint checks `proconfig`, not the body. `ALTER FUNCTION ... SET` is metadata-only and cannot change behavior while names still resolve to the same objects.

### 3a. LIVE-003 not done, needs a decision

The audit asks to `REVOKE EXECUTE ON FUNCTION public.is_party_to_lost_and_found_case(text) FROM authenticated`. The blast-radius check found that this would break a standing, test-pinned decision:

- **Grep result:** no application code calls the function (backend, rider-app, driver-app, admin-dashboard, shared), which agrees with migration 450's header. **But** prod `pg_policies` shows two RLS policies on `public.lost_and_found_messages` that call it: `lfm_select` (USING) and `lfm_insert` (WITH CHECK), both with roles `{public}`, created by migration 412.
- Postgres checks EXECUTE on functions in a policy expression **as the invoking role**. Revoking from `authenticated` makes every `authenticated` read or insert on `lost_and_found_messages` fail with 42501 instead of evaluating the ownership check.
- Migration 450 (applied 2026-09-23) revoked `PUBLIC`/`anon` and **explicitly kept `authenticated`** for this reason. `backend/tests/test_admin_secdef_fn_revokes.py::test_450_keeps_authenticated_on_rls_helper_but_drops_anon` pins it, and `docs/audit/2026-09-23-pr5725-live-preflight.md` says "Preserve the authenticated ownership helper". The audit line saying it "breaks the lockdown pattern of migrations 354 and 450" is inaccurate about 450.
- Live ACL today: `{postgres=X, authenticated=X, service_role=X}`. `anon`/`PUBLIC` already have no EXECUTE, and the function already has a pinned `search_path = public, pg_catalog`.
- The practical exposure is small. The function returns only whether **the caller's own** `auth.uid()` is a party to a given case UUID, and no end user holds a Supabase JWT today (C108).
- Options for whoever decides: (a) accept it as a documented linter exception; (b) move the helper into a schema PostgREST does not expose (for example `private`) while keeping `authenticated` EXECUTE (policies reference it by OID, so they should follow `ALTER FUNCTION ... SET SCHEMA`, but that needs its own review and an RLS-tier test); (c) revoke and accept that the lfm policies fail closed with errors.

## 4. Risk & impact on existing functionality

**Blast radius: cross-domain (dispatch, payments, audit/safety triggers), backend-only, metadata-only.**

- **Dispatch-area functions (highest risk).** Live callers: `update_driver_location` (`driver_repo.py:123`, called from `routes/drivers/location.py:189` and `routes/websocket.py:193`) and `get_service_area_for_point` (`driver_repo.py:91`, `routes/promotions.py:502`). `match_and_claim_driver` (`driver_repo.py:245`) and `find_nearby_drivers` (`driver_repo.py:111`) have Python wrappers but no production caller: real dispatch uses `dispatch_claim_batch`/`claim_driver_atomic` (migrations 402/403/448), and `routes/rides/matching.py` deliberately avoids `find_nearby_drivers`. Corrected after `spinr-migration-reviewer` review; the original text called `match_and_claim_driver` the live claim RPC. All four depend on unqualified PostGIS names that live in `extensions`. That is why their path includes `extensions`. Leaving it out would make every call fail with `function st_makepoint(...) does not exist`. The migration's post-condition refuses to commit if any of the four lacks `extensions`.
- **Money.** `fare_split_pay_share` (`backend/repositories/wallet_repo.py:297`) and `increment_promo_uses` (`wallet_repo.py:239`) touch only `public` tables, so resolution is unchanged. The `FOR UPDATE` lock and the `uses < max_uses` guard are untouched.
- **Trigger guards.** The 8 append-only guards (on `audit_logs`, `financial_events`, `financial_event_entries`, `subscription_payments`, `disputes`, and tables using `block_mutation_on_immutable_table` from migration 435) and the 2 `updated_at` helpers (`update_updated_at_column` is shared by many tables) use only built-ins. The GUC gates `spinr.audit_logs.allow_delete` / `spinr.financial_events.allow_delete` that `purge_pii_retention()` uses are read through `current_setting` from `pg_catalog`. No change.
- **SQL-function inlining.** A `LANGUAGE sql` function with a SET clause is no longer inlined by the planner. `find_nearby_drivers` and `get_service_area_for_point` (STABLE sql) will run as a function scan. Their internal plans (GiST on `location`/`area`) are unchanged, and PostgREST calls them as `SELECT ... FROM fn(...)` with no outer predicate to push down, so the expected impact is negligible. It was **not measured** against the dispatch SLA (< 2 s) or the fare-estimate SLA.
- **Grants unchanged.** The existing ACL `{=X, anon=X, authenticated=X, service_role=X}` on the 6 non-trigger RPCs is untouched. All 6 are SECURITY INVOKER, and `drivers`, `wallets` and `fare_split_participants` have RLS enabled with no policies, so a non-service caller sees zero rows. Revoking those grants is out of scope.
- **Background loops / state machine.** No loop, state transition, or insurance-period write path changes. The claim RPC's own logic is byte-identical.
- **Future migrations.** A later `CREATE OR REPLACE FUNCTION` of any of these 16 **without** a SET clause silently resets `proconfig` and undoes this fix. Nothing in CI guards against that yet.

## 5. User-experience effect

Nobody sees a difference (backend-only). Riders and drivers mid-session are unaffected if the migration applies cleanly. If it were wrong for a PostGIS function, the effect would be immediate and visible (dispatch and driver-location RPC errors). The post-condition exists to prevent exactly that.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/474_function_search_path_hardening.sql` | New migration: 16 `ALTER FUNCTION ... SET search_path`, drift pre-check, post-condition, rollback comment | LIVE-002 |
| `docs/change-log/2026-09-25-function-search-path-hardening.md` | This log | CLAUDE.md mandatory Change Impact Log |

Test harness lists (`backend/tests/direct_pool/conftest.py`, `backend/tests/rls/conftest.py`) were **not** changed. Both apply a curated subset of migrations, not a complete list, and neither applies the migrations that create these functions (for example, direct_pool deliberately skips 77/80).

## 7. Before / after

```sql
-- Before (prod pg_proc, 2026-09-25)
-- match_and_claim_driver(...)  proconfig = NULL   -- resolves ST_* via the caller's search_path
```

```sql
-- After
ALTER FUNCTION public.match_and_claim_driver(text, double precision, double precision, double precision, double precision)
    SET search_path = public, extensions, pg_temp;
-- proconfig = {"search_path=public, extensions, pg_temp"}
```

Concrete dispatch scenario: a rider requests a ride, and the backend calls `rpc('match_and_claim_driver', {...})` as `service_role`.

- Before: `ST_MakePoint` resolves through the database-default path (`public`, then `extensions`) and the nearest available driver is claimed.
- After: the function's own path is `public, extensions`, so the same `extensions.st_makepoint` is resolved and the same driver is claimed. No row or ordering difference is expected.

## 8. Rollback plan

Metadata-only SQL. It can be run at any time with no redeploy, and it restores the exact prior state (the linter warning comes back):

```sql
ALTER FUNCTION public.match_and_claim_driver(text, double precision, double precision, double precision, double precision) RESET search_path;
ALTER FUNCTION public.find_nearby_drivers(double precision, double precision, double precision) RESET search_path;
ALTER FUNCTION public.get_service_area_for_point(double precision, double precision) RESET search_path;
ALTER FUNCTION public.update_driver_location(text, double precision, double precision) RESET search_path;
ALTER FUNCTION public.fare_split_pay_share(uuid, uuid, numeric) RESET search_path;
ALTER FUNCTION public.increment_promo_uses(uuid, integer) RESET search_path;
ALTER FUNCTION public._audit_logs_immutable() RESET search_path;
ALTER FUNCTION public.audit_logs_block_delete() RESET search_path;
ALTER FUNCTION public.audit_logs_block_update() RESET search_path;
ALTER FUNCTION public._financial_events_immutable() RESET search_path;
ALTER FUNCTION public._financial_event_entries_immutable() RESET search_path;
ALTER FUNCTION public._subscription_payments_immutable() RESET search_path;
ALTER FUNCTION public.block_mutation_on_immutable_table() RESET search_path;
ALTER FUNCTION public.disputes_block_delete() RESET search_path;
ALTER FUNCTION public.safety_incidents_set_updated_at() RESET search_path;
ALTER FUNCTION public.update_updated_at_column() RESET search_path;
```

If dispatch alone misbehaves, reset just the four PostGIS functions. No data is written, so no data remediation is needed. (LIVE-003 was not applied, so there is no GRANT to restore.)

## 9. Verification performed

- [x] Read-only prod catalog queries (SELECT only, project `soavhtdhefowwvforzwb`): `get_advisors(security)` for the exact 16-function list; `pg_proc` for identity args, `prosrc`, `prosecdef`, `proconfig`, `proacl`, and overload count (1 each); `pg_extension` joined to `pg_namespace` (PostGIS in `extensions`); `pg_db_role_setting` for the database-default search_path; `pg_policies` for callers of `is_party_to_lost_and_found_case`.
- [x] `cd backend && python -m pytest -o addopts="" -q -p no:cacheprovider tests/test_migration_ordering.py tests/test_admin_secdef_fn_revokes.py tests/test_audit_migration_drift.py`: all passed (16 + 4).
- [x] `run_migrations._split_sql_statements` on the new file: the `BEGIN ... COMMIT` block folds into one statement, so it applies atomically.
- [x] Blast-radius grep: each function name across `backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/`, `shared/` (callers listed in §4); `is_party_to_lost_and_found_case` across the whole repo.
- [x] Self-review against `.claude/agents/spinr-migration-reviewer.md`. Numbering 474 is free (471 is the highest on main; 472/473 are reserved by other open PRs). Append-only: OK (new file). RLS: N/A. Rollback comment: present. Forward-compat: metadata only, no table lock, `lock_timeout 2s`. Money safety: body unchanged, now path-pinned. Retention: N/A. No CHECK E dangerous patterns.
- [x] Self-review against `.claude/agents/spinr-dispatch-reviewer.md`. Claim atomicity (`FOR UPDATE SKIP LOCKED`, `is_available=false` in the same frame) is unchanged. `is_available ⇒ is_online` is untouched. No state transition or WS event is affected. The only dispatch risk is name resolution, which the `extensions` post-condition guards.
- [ ] Feature flag: N/A. There is no user-visible behavior, and a DB-metadata change cannot be gated by `app_settings`. The rollback SQL is the kill switch.

## What was NOT verified

- **The migration has not been executed against any Postgres.** There is no local or staging Postgres in this environment, and nothing was applied to production. The SQL syntax, the DO-block logic, and the claim that PostGIS calls resolve under the new path are reasoned from the catalog, not run. The first real execution should be a Supabase branch or staging database, followed by a call to each of the 4 PostGIS RPCs.
- The dispatch and fare-estimate latency effect of losing SQL-function inlining was not benchmarked.
- The linter was not re-run after apply (it cannot be until the migration is applied).
- It was not checked whether other environments (staging, DR restore, Supabase branches) have PostGIS in `extensions` too. If one had PostGIS in `public`, the path still includes `public` and works.
- No CI guard yet stops a future `CREATE OR REPLACE` from silently dropping the pinned path.
