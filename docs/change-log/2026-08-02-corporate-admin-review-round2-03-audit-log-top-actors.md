# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "who touched the most" finding |

## 1. Issue / gap identified

No "who touched the most" rollup view existed anywhere in the admin
portal — a SOC investigator wanting "which admin made the most changes in
the last week" had to run raw SQL against `audit_logs` directly.

## 2. Root cause

`audit_logs` had a listing/search endpoint (`GET /admin/audit-logs`) but
nothing that aggregated by actor. This is a pure gap, not a regression —
the feature was simply never built.

## 3. Fix / remediation

- New endpoint `GET /admin/audit-logs/top-actors` in
  `routes/admin/maintenance.py`, gated identically to the existing
  `/audit-logs` endpoint (`require_module("audit")`, same router-level
  `require_module("dashboard")`). Bounded `days` window (1–90, default 7)
  and `limit` (1–200, default 20).
- Aggregation follows the exact precedent already in this codebase
  (`routes/admin/monitoring.py::get_email_deliverability`): a single
  `get_rows` fetch capped at 5,000 rows within the window, aggregated in
  Python with `collections.Counter` — not a new Postgres `GROUP BY` RPC,
  since this table doesn't have one and standing one up is out of scope
  for a SOC convenience view. Response explicitly flags
  `rows_scanned_capped` when the 5,000-row cap was hit, so a heavy window
  doesn't silently under-report — the caller can see the count is a floor,
  not a total.
- Admin-dashboard: new "Most active actors" panel on the existing Audit
  Logs page (`audit-logs/page.tsx`), with a 24h/7d/30d/90d window selector,
  showing each actor's total action count and a hover tooltip with their
  top 5 action types.

## 4. Risk & impact on existing functionality

- **Blast radius: one new read-only endpoint, one new UI panel on an
  existing page.** Nothing existing was modified — `get_audit_logs` and
  the rest of the Audit Logs page are untouched.
- Read-only: no writes, no schema change, no migration.
- Grepped every other consumer of `routes/admin/maintenance.py` and of
  the `audit-logs` page — none reference the new endpoint or panel, so
  nothing else could regress from adding them.
- Bounded cost: capped at 5,000 rows per call, same ceiling as the
  existing `email-deliverability` precedent this pattern was copied from,
  which has been running in production without incident.

## 5. User-experience effect

**Internal admin-facing only** (requires the `audit` module grant, same
as the rest of the Audit Logs page). Purely additive — a new panel above
the existing table; no change to any existing admin-facing behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/maintenance.py` | New `GET /audit-logs/top-actors` endpoint | Close the rollup-view gap |
| `admin-dashboard/src/lib/api/staff-subscriptions.ts` | New `getAuditLogTopActors` client function | Call the new endpoint |
| `admin-dashboard/src/lib/api.ts` | Re-export `getAuditLogTopActors` | Match the existing `getAuditLogs` export pattern |
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | New "Most active actors" panel with a window selector | Surface the rollup without raw SQL |
| `backend/tests/test_admin_maintenance_coverage.py` | 5 new tests: aggregation/sort order, per-actor action breakdown, missing-field fallback, limit + row-cap flag, days-window filter | Lock in the new endpoint's behavior |

## 7. Before / after

```python
# Before — no rollup endpoint existed

# After
@router.get("/audit-logs/top-actors")
async def get_audit_log_top_actors(days=7, limit=20, _admin=Depends(require_module("audit"))):
    rows = await db_supabase.get_rows("audit_logs", {"created_at": {"$gte": since}}, limit=5000)
    by_actor = Counter(...)
    ...
    return {"days": days, "rows_scanned": ..., "rows_scanned_capped": ..., "actors": [...]}
```

## 8. Rollback plan

`git revert` the commit. No migration, no data written — purely additive
read endpoint and UI panel.

## 9. Verification performed

- [x] 5 new backend tests covering aggregation/sort order, the top-5
      per-actor action breakdown, missing `actor_id`/`action` fallback to
      `"unknown"`, `limit` truncation + the `rows_scanned_capped` flag at
      the 5,000-row ceiling, and that the `days` window reaches the
      `get_rows` filter as `created_at: {"$gte": ...}`.
- [x] `python3 -c "import ast; ast.parse(...)"` on both touched Python
      files — clean.
- [x] Bracket-balance check on the touched `.tsx` file (no TS/JS toolchain
      run, per this round's "no tests/CI per item" instruction) — balanced.
- [x] Blast-radius grep performed (see §4): no other consumer of either
      touched file.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — new, read-only, additive
      surface confirmed via grep
- [x] No silent behavior change to a working flow — nothing existing was
      modified

## What was NOT verified

Did not run `eslint`/`tsc --noEmit`/`vitest` or a production build for the
admin-dashboard changes, and did not run `pytest` for the backend changes
— per this round's explicit instruction, verification is deferred to a
single pass at the end once every remaining item is implemented. Did not
manually click through the new panel in a browser — reasoned through the
existing page's established patterns (Select/Card/Badge usage already in
the same file) rather than screenshotted; no visual-regression tooling
exists in this repo for this surface (a standing, previously-flagged
gap, not re-discovered here).
