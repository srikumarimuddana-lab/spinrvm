# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-26 |
| Author | Claude (audit requested by vikas@ngitservices.com) |
| Surface(s) | backend, admin-dashboard (consumer of the fixed endpoint) |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR opened from branch `claude/admin-portal-heatmaps-audit-gm8fbn`) |
| Related issue or gap ID | User report: "Admin portal heat maps has issues and not working at all" |

## 1. Issue / gap identified

The admin Heat Map page's "Corporate" ride-type filter (`/dashboard/heatmap`,
`GET /api/admin/rides/heatmap-data?filter=corporate`) always returned zero
pickup/dropoff points and `corporate_rides: 0` — even when corporate rides
existed in the selected date range/service area. "All" and "Regular"
returned correct data.

## 2. Root cause

`backend/routes/admin/rides.py`'s `admin_get_heatmap_data` built the corporate
filter as `{"corporate_account_id": {"$ne": None}}`. In the query-filter layer
(`repositories/_base.py`), `$ne` compiles unconditionally to PostgREST's
`neq` operator — there is no special-case for a `None` operand (unlike the
bare `{col: None}` form, which the same layer already translates to `IS
NULL`). SQL `<> NULL` is three-valued-logic `UNKNOWN` for every row, so the
resulting query matched nothing. This is the exact bug class the repo had
already hit and fixed once before in `routes/admin/drivers.py` (see the
`$notnull, NOT {"$ne": None}` comment there); `_base.py` even carries a
`$notnull` operator specifically to do this correctly
(`q.not_.is_(k, "null")`). The heatmap endpoint just never got the same
fix. The unit test covering this endpoint mocked `db_supabase.get_rows`
directly, bypassing filter compilation entirely, so it could not catch the
bug — it only ever verified response *shaping*, not the real Postgres
semantics.

## 3. Fix / remediation

Changed the corporate branch to `{"corporate_account_id": {"$notnull": True}}`,
matching the pattern already used (and documented) in
`routes/admin/drivers.py`. Added a regression assertion to the existing
`test_get_heatmap_data_happy_path` test that inspects the actual filter dict
passed to `db_supabase.get_rows`, so a future regression to `$ne` is caught
even though the DB call itself is mocked.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped the whole backend for other callers of
  this endpoint and other `{"...": {"$ne": None}}` patterns:
  - `backend/routes/admin/rides.py` — only call site of this exact filter;
    fixed.
  - `backend/services/dispatch_service.py:547` (`last_assigned_driver_id`,
    used only by the `round_robin` dispatch strategy's tie-break helper) has
    the *identical* bug pattern (`{"driver_id": {"$ne": None}}`) but is
    **out of scope for this change** — it's dispatch code, not heatmap, and
    fixing it needs its own review/test under the dispatch state-machine
    gates rather than being folded into an admin-reporting fix. Flagging it
    here and recommending a follow-up ticket rather than silently leaving it
    undocumented.
  - No other admin/report endpoint uses this exact filter shape.
- This is a read-only reporting endpoint (`GET`, no writes). Fixing it can
  only ever *increase* the rows returned for `filter=corporate` (from
  "always zero" to "actually matching corporate rides") — there's no way for
  this change to newly exclude data that was previously shown, so it cannot
  regress the "All" or "Regular" tabs, which don't touch this branch.
- No interaction with background loops, the ride state machine, or money/wallet
  deltas — this only reads `rides.corporate_account_id`/lat-lng columns for
  display.

## 5. User-experience effect

- Internal-admin-facing only (Heat Map page, "Corporate" ride-type tab and its
  derived stat card). No rider/driver/corporate-admin visibility.
- Not visible mid-session in the live-ride sense — it's a historical reporting
  view an admin refreshes on demand; the only "mid-session" effect is that an
  admin who has the page open and switches to the Corporate tab now sees data
  where they previously saw an empty map with 0/0% stats.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | `corporate_account_id` filter: `{"$ne": None}` → `{"$notnull": True}` | `$ne` against `None` never matches in the PostgREST/SQL filter layer; `$notnull` is the operator this layer provides for exactly this case |
| `backend/tests/test_admin_rides_coverage.py` | Added assertion on the filter dict passed to `db_supabase.get_rows` for `filter=corporate` | Existing test mocked the DB call and only checked response shape, so it could not have caught this bug; the new assertion pins the actual filter operator |
| `docs/change-log/2026-08-26-admin-heatmap-corporate-filter-fix.md` | This log | Required for a fix touching a live-tested admin surface |

## 7. Before / after

```python
# Before
if filter == "corporate":
    query_filters["corporate_account_id"] = {"$ne": None}
elif filter == "regular":
    query_filters["corporate_account_id"] = None
```

```python
# After
if filter == "corporate":
    query_filters["corporate_account_id"] = {"$notnull": True}
elif filter == "regular":
    query_filters["corporate_account_id"] = None
```

## 8. Rollback plan

Pure code fix, no migration, no feature flag, no data written. Revert the
single commit (`git revert`) to restore the prior (broken) behavior if
needed — safe here specifically *because* nothing downstream of this
GET endpoint persists state; there is no live data to reconcile.

## 9. Verification performed

- [x] Automated tests run: `python3 -m pytest tests/test_admin_rides_coverage.py -k heatmap -q` — 1 passed (extended test, including the new filter-shape assertion).
- [x] `ruff check` and `ruff format --check` on both changed backend files — clean.
- [ ] Manual repro steps followed in staging — not performed (no staging access in this session); reasoned from the query-filter layer's own documented semantics and the pre-existing identical fix in `routes/admin/drivers.py`, not observed against a live Supabase instance.
- [x] Blast-radius grep performed: searched for other `{"$ne": None}`/`{"$ne": null}` patterns across `backend/`; found one unrelated instance in `dispatch_service.py` (documented above, not touched).
- [x] Reviewed against relevant CLAUDE.md convention: "Query filters — the layer owns escaping, callers pass raw input" section, specifically the existing `$notnull` guidance.
- [ ] Not applicable: change is not user-visible/non-trivial in the feature-flag sense (bug fix to an existing, always-on reporting filter, not new behavior).

## What was NOT verified

- Not tested against a live Supabase instance — verified via the mocked unit test and by reading the exact `neq`/`is_`/`not_.is_` compilation in `repositories/_base.py`, not by running a real query against Postgres.
- No production build (`npm run build`) was run — this PR makes no admin-dashboard frontend changes; the frontend already calls the endpoint correctly and needed no changes.
- Did not audit the rest of the Heat Map page beyond this one endpoint for other bugs in this pass (see the accompanying audit notes in chat for a broader list of lower-severity/no-action findings reviewed and ruled out: RBAC gating, settings persistence, forecast chart mapping, demand-poll memoization, etc. — all found already fixed by prior work).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single commit revert; no data-level irreversibility).
- [x] Blast radius is stated, not assumed (isolated to this one filter branch; one related-but-out-of-scope instance named).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in.
