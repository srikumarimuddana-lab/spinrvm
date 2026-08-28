# Change Impact & Risk Log — P1 admin/driver query optimization

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (session-driven, per @srikumarimuddana) |
| Surface(s) | backend, admin-dashboard (one TypeScript type field removed) |
| Domain (Sentry tag) | `admin`, `drivers`, `rides` |
| PR / commit link | branch `claude/p1-admin-query-optimization` |
| Related issue or gap ID | `docs/audit/2026-08-26-db-query-optimization-recommendations.md` P1 items #9–#14 (PR #4579) |
| Corrections to that doc | §4.1's "same query run twice" claim about `earnings.py:57`/`:91`, and recommendation #13's site list — both corrected in this branch |

## 1. Issue / gap identified

The 2026-08-26 DB audit found the admin read path and the authenticated request path
both doing far more Supabase work than the response needs: unprojected `select *`
batch fetches that ship base64 `profile_image` blobs nobody reads, two genuinely
unbounded full-table scans on the live monitoring endpoints, `?limit=` values the
client can set arbitrarily high, a 50,000-row ride fetch summed in Python to fill one
stat card, and ~70 endpoints re-issuing an uncached `SELECT * FROM drivers WHERE
user_id = ?` for a row the auth dependency had already fetched and discarded on the
same request.

Separately, and unrelated to the audit: `backend/migrations/369_drop_duplicate_indexes.sql`
(merged to `main` in PR #4593) shipped as an unfilled template — lines 48/57/66 are live
SQL reading `DROP INDEX IF EXISTS <DUPLICATE_INDEX_NAME_1>;`. `<` is not a valid
identifier, so the ordered migration runner would have hit a syntax error and halted the
entire pending queue on its first apply.

## 2. Root cause

- **Projection / payload**: `_batch_fetch_drivers_and_users` was written as a generic
  helper and never given a column list, so every one of its 11 callers pays for every
  column including `drivers.profile_image` (base64 data URIs). `/admin/drivers/stats`
  additionally returned a top-level `drivers` array of up to 5000 full rows that the
  dashboard never reads — the table it renders comes from a separate paginated
  `getDrivers()` call.
- **Unbounded scans**: `admin/monitoring.py`'s two fetchers had neither a filter nor a
  `.limit()`. They were written when PostgREST's implicit `db-max-rows` = 1000 masked
  the problem; P0 (PR #4593) raised the client-side default, which removed the mask.
- **50k ride scan**: the per-driver earnings total was computed with a Python
  `defaultdict` sum because no aggregate RPC existed for it. The `limit=50000` was also
  a silent correctness ceiling — past it the card under-reports with no signal.
- **Duplicate driver read**: `get_current_user` calls `get_driver_by_user_id_cached`
  on every authenticated request purely to set `user["is_driver"]`, then throws the row
  away. Downstream handlers needing the same row had no way to reach it, so each
  re-queried `drivers` uncached.
- **Migration 369**: the file was authored as a template with placeholders intended to
  be filled from a duplicate-index sweep, and merged with them still in place.

## 3. Fix / remediation

Seven scoped changes, one per commit:

- **A1** — explicit column projection on `_batch_fetch_drivers_and_users`
  (`drivers`: `id,user_id,name,lat,lng,service_area_id`; `users`:
  `id,first_name,last_name,email,phone`), verified closed against all 11 callers;
  `columns="id"` on the driver photo-existence lookup; deleted a dead
  `_batch_fetch_drivers_and_users([], [])` call in `admin/safety.py` whose result was
  overwritten two lines later.
- **A2** — removed the unread `drivers` key from `GET /admin/drivers/stats` and its
  `admin-dashboard` TypeScript type field.
- **A3** — `_MONITORING_ROW_CAP = 2000` on both monitoring fetchers, with a
  `logger.warning` on truncation. A **cap, not pagination**: the WS `drivers_snapshot`
  consumer does full-replace reconciliation (deletes markers absent from the payload),
  so a paginated page would silently erase the rest of the fleet from the map.
- **A4** — `le=200` bounds on 5 non-admin and 3 admin list endpoints, plus
  `Field(le=200)` on `DriverSearchRequest.limit` / `UserSearchRequest.limit` (the POST
  search paths call the GET handler in Python and so bypass its `Query` bounds).
- **A5** — `count_documents` instead of fetching rows for `total_rides` in
  `GET /drivers/balance`.
- **A6** — new `admin_driver_earnings_rollup` RPC (migration 370) replaces the
  50,000-row fetch with a server-side `GROUP BY`.
- **A7** — `get_current_user` now stashes the driver row it already fetched on
  `current_user["_driver"]`; new `driver_row_for()` helper returns it. Converted the 8
  zero-risk participant-check sites that read only the immutable `driver["id"]`.
- **369 repair** — filled the three placeholders with the advisor-confirmed duplicate
  pairs (`idx_surge_pricing_area`, `idx_dlh_driver`, `idx_dlh_ride`), each of which keeps
  a proven-identical twin.

## 4. Risk & impact on existing functionality

**Blast radius: backend-wide on the auth path (A7), single-surface elsewhere.**

| Change | What else touches it | Regression risk |
|---|---|---|
| A1 projection | All 11 callers of `_batch_fetch_drivers_and_users` were read; the union of fields any of them consumes is exactly the projected set. `profile_image` is read by **zero** callers. | A future caller adding a field read without extending the projection gets `None`, not an exception. Mitigated by naming the column sets inline at the definition. |
| A2 response shape | Grepped `admin-dashboard/` for `data.drivers` / `.drivers` off the stats response: **no readers**. No test asserted it. | If an unknown external consumer existed, it would see the key vanish. Judged acceptable — this is an admin-only, JWT-gated endpoint with one known client. |
| A3 monitoring cap | `admin-dashboard/src/app/dashboard/monitoring/page.tsx:218-231` full-replace reconciliation. Cap is 2000 against a current fleet of **212 drivers** — ~9× headroom. | Above 2000 drivers the map would show a truncated fleet. That is why the truncation `logger.warning` exists: it fires before the ceiling is reached in practice. |
| A4 limit bounds | Any client passing `?limit=` above 200 now gets a 422 instead of a large page. Checked the three apps: none sends a literal limit above 200. | A previously-succeeding oversized request now fails loudly rather than degrading the DB. Intentional. |
| A5 `count_documents` | `GET /drivers/balance` only. Deliberately kept **legacy-inclusive** (no `EXCLUDE_LEGACY_RIDES`) to preserve fix A31 — the money query at `earnings.py:57` excludes legacy, this activity counter must not, or a driver with only legacy rides shows `total_rides = 0`. | None; the count is over the identical filter the fetch used. |
| A6 RPC | Reads `rides`; relies on `idx_rides_completed_driver` (migration 159), which `admin_ride_money_rollup` (302) and `admin_payouts_overview_aggregates` (303) already use. Same legacy-exclusion semantics as both. | **Deploy ordering**: migration 370 must be applied before or with the code deploy, or `/admin/drivers/stats` 500s. Same requirement as its 159/302/303 precedents. |
| A7 `_driver` reuse | The dependency is on **every** authenticated request. The 8 converted sites read only `driver["id"]`, which is immutable. | The row can be up to **30 s stale** (the `get_driver_by_user_id_cached` TTL). The ~26 sites needing a fresh row — all of `drivers/payouts.py` (Stripe read-modify-write, GST/SIN gates), `drivers/location.py` (a stale `is_online` yields a 409 the client treats as terminal, silently dropping Period-1 insurance breadcrumbs), `drivers/ride_flow.py` (suspension gate on accept), `ride_complete.py`, `ride_cancel.py`, `websocket.py`, `users.py`, `profile.py`, `subscriptions.py` — were **deliberately not converted**, and the helper's docstring states that contract. |
| 369 repair | The whole pending migration queue (363–370) sits behind it. | Verified against production `schema_migrations`: the highest applied migration is **362**, so 369 has never run and nothing has drifted — editing it in place does not violate the append-only rule and creates no checksum drift. |

**No interaction with**: the ride state machine (no transition added or changed), money
or wallet deltas (A6 changes where a sum is computed, not what it computes — see §7),
dispatch, or any of the background loops.

## 5. User-experience effect

- **Rider / driver**: no visible change. A7 alters only where a participant check gets
  its driver id, not the check's outcome. A5 changes an internal query shape, not the
  number it returns.
- **Internal admin**: `/admin/drivers/stats` no longer carries the unread `drivers`
  array — the dashboard renders identically because it never read it. Above 2000
  drivers the live monitoring map would show a bounded subset (not reachable at the
  current 212-driver fleet).
- **Mid-session visibility**: none. No change is observable to a rider mid-ride or a
  driver online.
- **Copy / notifications**: unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | Column projection on the batch fetch; `columns="id"` on the photo lookup; dropped the `drivers` response key; `le=200` bounds; 50k fetch → `admin_driver_earnings_rollup` RPC | A1, A2, A4, A6 |
| `backend/routes/admin/safety.py` | Deleted the no-op `_batch_fetch_drivers_and_users([], [])` and its now-orphaned import | A1 |
| `backend/routes/admin/monitoring.py` | `_MONITORING_ROW_CAP = 2000` + truncation warning on both fetchers | A3 |
| `backend/routes/admin/users.py`, `corporate_company.py`, `drivers/payouts.py`, `drivers/referrals.py`, `drivers/ride_reads.py`, `notifications.py` | `le=200` on client-controlled `limit` | A4 |
| `backend/routes/drivers/earnings.py` | `count_documents` for `total_rides` (legacy-inclusive, per A31) | A5 |
| `backend/migrations/370_driver_earnings_rollup_fn.sql` | New `admin_driver_earnings_rollup(text[]) RETURNS jsonb` | A6 |
| `backend/migrations/369_drop_duplicate_indexes.sql` | Filled three `<DUPLICATE_INDEX_NAME_N>` placeholders; documented the real production state | Unblocks the pending migration queue |
| `backend/dependencies/__init__.py` | Stash the already-fetched driver row on `_driver`; new `driver_row_for()` with an explicit staleness contract | A7 |
| `backend/routes/rides/{_deps,chat,queries,safety,sharing,tracking,lifecycle}.py`, `backend/routes/safety.py` | 8 participant checks now reuse the stashed row | A7 |
| `admin-dashboard/src/lib/api/drivers.ts` | Removed the `drivers` field from the stats response type | A2 |
| `backend/tests/*` (11 files) | Updated fixtures for the new dependency contract and the RPC; equality assertions preserved | — |
| `docs/audit/2026-08-26-db-query-optimization-recommendations.md` | Corrected two claims the implementation disproved (see below) | Accuracy of the doc this branch implements |

## 7. Before / after

**A6 — the earnings sum (the money-relevant diff):**

```python
# Before — every completed non-legacy ride for every driver in scope, summed in Python
earnings_rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": {"$in": list(driver_ids_set)}, "status": "completed", **EXCLUDE_LEGACY_RIDES},
    limit=50000,
)
for r in earnings_rides:
    did = r.get("driver_id")
    if did:
        earnings_by_driver[did] += Decimal(str(r.get("driver_earnings") or 0))
```

```python
# After — one RPC round-trip, N driver rows instead of N-thousand ride rows
_rollup_rows = await db_supabase.rpc(
    "admin_driver_earnings_rollup",
    {"p_driver_ids": list(driver_ids_set)},
)
_rollup = _rollup_rows[0] if isinstance(_rollup_rows, list) and _rollup_rows else _rollup_rows
if not isinstance(_rollup, dict):
    _rollup = {}
for did, amount in (_rollup.get("by_driver") or {}).items():
    earnings_by_driver[did] = Decimal(str(amount or 0))
```

The SQL sums `COALESCE(driver_earnings, 0)::text::numeric`. The **double cast is
load-bearing, not stylistic**: `driver_earnings` is a `FLOAT` column, and `float8::text`
uses the shortest round-trip representation, so `::text::numeric` reproduces Python's
`Decimal(str(float))` exactly. A bare `::numeric` would carry the binary rounding error
and drift the card from every other earnings surface. Legacy exclusion is
`legacy_import_metadata = '{}'::jsonb` (the column is `NOT NULL DEFAULT '{}'`, so
`IS NULL` would match nothing).

**A7 — the participant check:**

```python
# Before — an uncached SELECT * for a row this request already fetched
driver = (lambda _r: _r[0] if _r else None)(
    await _deps.db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
)
```

```python
# After
driver = await _deps.driver_row_for(current_user)
```

**A2 — response shape:**

```python
# Before
return {..., "drivers": enriched_drivers, "stats": stats, "area_stats": area_stats}
# After
return {..., "stats": stats, "area_stats": area_stats}
```

## 8. Rollback plan

- **A1–A5, A7 (code-only, no data written)**: `git revert` is a complete rollback. None
  of these writes to any table, mutates ride state, moves money, or creates an
  insurance-period row — there is no live data to remediate. A redeploy is the only
  path; that is acceptable here precisely because the changes are read-path only.
- **A6**: revert the `admin/drivers.py` hunk to the in-Python sum — it works whether or
  not the function exists. Dropping the function is optional and separate:
  `DROP FUNCTION IF EXISTS public.admin_driver_earnings_rollup(text[]);` (stated in the
  migration header). **Deploy ordering matters**: apply 370 before or with the code, or
  the endpoint 500s until it is applied.
- **369**: the three statements are `DROP INDEX IF EXISTS` against indexes already
  absent from production, so applying it is a no-op there. Rollback for an environment
  that does carry them is the `CREATE INDEX` in the file's own header comment.
- **No feature flag.** These are read-path optimizations with no user-visible behavior
  to dark-launch; the one response-shape change (A2) removes a key with zero readers.
  Flagging would add a branch to maintain for a change nobody can observe.

## 9. Verification performed

- [x] **Automated tests** — full `pytest -m "not slow"` run to completion, split into 10
      chunks (a single process is killed by this container's memory ceiling; that is an
      environment limit, not a test failure). Post-merge result: **12,762 passed,
      6 skipped, 1 xfailed, 0 failed** — all ten chunks exit 0.

      Two failures seen on the branch's pre-merge base were **pre-existing and already
      fixed on `origin/main`** by `71698ad83` (`ACTION_ITEMS.md` C45); merging main in
      resolved both, and each was verified to reproduce on the *unmodified* tree first:
      - `test_payments_coverage_gap_closure.py::test_confirm_payment_real_ride_ownership_mismatch`
        — an unawaitable `MagicMock` on `update_ride` turned the expected 403 into a 500.
      - `test_compliance_reports.py::test_truncation_flag_set_at_row_limit` — an
        **infinite loop**, not a slow test: `_get_all_rows_paginated` only stops when a
        page comes back shorter than `page_size`, and the test's mock returned the full
        `_ROW_LIMIT`-row list for every offset, so `all_rows` grew without bound until
        the process was killed.
      Targeted suites re-run green after the fixture updates: `test_coverage_rides.py`
      (177), `test_e2e_sos_flow.py` (7), `test_ride_messages.py` + `test_p2_sos.py` (45),
      `test_admin_drivers_coverage.py` (138), `test_admin_safety_incidents.py`,
      `test_admin_driver_search.py`, `test_admin_users_search.py`,
      `test_earnings_coverage.py`, `test_drivers_extended.py`, `test_p2_chat.py`,
      `test_rides_extended.py`, `test_p3_addresses_favorites_safety_disputes.py`.
- [x] **Money equality proof** — the A6 test asserts the identical `stats.total_earnings`
      and `area_stats[*].total_earnings` values (`50.0`) the pre-RPC code produced.
      Additionally, the RPC's `GROUP BY` result was checked **read-only** against
      production and matched a direct control sum (grand total `5.98` both ways).
- [x] **Blast-radius greps performed** — all 11 callers of
      `_batch_fetch_drivers_and_users`; `admin-dashboard/` for readers of the stats
      `drivers` key; the monitoring WS snapshot consumer; every
      `get_driver_by_user_id_cached` call site (~70) classified into safe-to-reuse
      vs. must-stay-fresh; `pg_indexes` + all of `backend/migrations/` +
      `supabase_schema.sql` for the three 369 index names and their twins.
- [x] **Reviewed against `CLAUDE.md` conventions** — Decimal-only money math (A6),
      dual-import pattern preserved (`_deps` re-export, `routes/safety.py`), migration
      conventions (`SECURITY DEFINER`, pinned `search_path`, `p_`-prefixed arg,
      `REVOKE EXECUTE FROM anon, authenticated`, rollback in header), PIPEDA (the A1
      projection **narrows** what PII leaves the DB — it drops `profile_image` and does
      not add any field).
- [x] **Reviewer agents** (automated PR review is dark per `ACTION_ITEMS.md` C9, so these
      were run manually): `spinr-migration-reviewer` → 370 SAFE TO APPLY, numbering free,
      conventions match 159/302/303, no `migration-override-ok` needed, rollback complete;
      `spinr-money-auditor` → SAFE TO MERGE, no blockers, no float introduced, totals
      compute identically, legacy semantics unchanged.
- [x] **Lint** — `ruff check` / `ruff format --check` over all 32 Python files this
      branch touches, compared against `origin/main`'s content for the same files:
      **6 errors both before and after**, and format diffs go **6 → 5** (the repo's
      formatter hook cleaned `routes/admin/safety.py` while this branch was editing it).
      This branch introduces no new lint or format finding.
- [ ] **Manual repro in staging** — not performed; see §"What was NOT verified".
- [x] **Feature flag** — deliberately none; justified in §8.

## What was NOT verified

- **No staging run.** Everything here was verified against `mock_supabase_client`
  fixtures and, for the A6 SQL only, read-only queries against production. The
  `/admin/drivers/stats` endpoint was **not** exercised end-to-end against a real
  Supabase instance.
- **Migration 370 is not applied anywhere.** Its function body *was* compiled in
  production under a throwaway name (`_tmp_validate_370`) and dropped in the same
  statement, purely to prove the SQL parses and the types resolve — **that was a write,
  not a read.** Zero `_tmp_validate*` functions remain. `admin_driver_earnings_rollup`
  itself has not been created in production.
- **Migration 369's `DROP INDEX` statements have not been executed.** They were verified
  to be no-ops against production (`pg_indexes` shows all three targets already absent,
  only their twins remain and are actively scanned) but not run.
- **`admin-dashboard` production build not run for A2.** The change removes one optional
  field from a TypeScript response type with no readers; a `tsc` error would require a
  reader to exist. This was reasoned about, not built. **There is no active
  visual-regression coverage on any surface** — `admin-dashboard`'s Playwright job
  (`e2e/visual-regression.spec.ts`) has zero committed baselines and skips itself on
  every run (`ACTION_ITEMS.md` B38); rider-app and driver-app have none at all.
- **The A7 staleness contract is enforced by documentation and review, not by a type or
  a lint rule.** A future caller can read a mutable field off `_driver` and get a value
  up to 30 s old with nothing failing. The ~26 must-stay-fresh sites are enumerated in
  the branch plan and in the helper's docstring, but that is a convention, not a guard.
- **Load/latency improvement is estimated, not measured.** No before/after profiling was
  run against production traffic; the claims are round-trip and row-count arithmetic
  from the audit.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
