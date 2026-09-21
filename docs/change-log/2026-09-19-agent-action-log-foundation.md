# Change Impact & Risk Log — Agent Action Log foundation (Phase 1)

**Date:** 2026-09-19
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** backend (new table + admin route)
**Domain:** admin / observability (non-money, non-ride-state)
**Related:** `docs/audit/2026-09-19-security-automation-roadmap.md` Phase 1

## Issue/gap identified
Spinr has no audit trail of what an AI agent (Claude Code sessions, review
subagents, CI-run scans) actually did against the repo/infra — distinct from
git history, which shows the end state but not the task, outcome, or
escalations along the way.

## Root cause
Never built. `ai_tool_audit` (migration 217) covers the rider/driver-facing
AI assistant's runtime tool calls; nothing covers engineering-automation
actions.

## Fix/remediation
- New append-only table `agent_action_log` (migration 429), RLS enabled,
  admin-role read-only SELECT policy, no UPDATE/DELETE policies (append-only
  by omission, matching the `ai_tool_audit` precedent).
- New `backend/utils/agent_action_logger.py::log_agent_action()` — write
  helper, never raises (mirrors `ai_tool_audit`'s "audit failure must never
  break the calling task" precedent).
- New read-only admin endpoint `GET /api/admin/agent-actions` in
  `backend/routes/admin/maintenance.py`, gated by the existing `require_module("audit")`
  dependency (same gate as `/audit-logs`).

## Risk & impact on existing functionality
- **Blast radius: isolated.** New table, no existing table/column altered.
  `agent_action_logger.py` is a new module with zero callers yet (no
  behavior change to any runtime path until something starts calling
  `log_agent_action()`).
- `backend/routes/admin/maintenance.py` — only additive: one new route
  function inserted between two existing ones; no existing route's behavior,
  filters, or gating changed. Confirmed via full file test run (25/25 pass).
- Other consumers of `db_supabase.get_rows`/`insert_one` generic helpers:
  unaffected — no change to `repositories/_base.py`.
- `spinr-migration-reviewer` verdict: SAFE TO APPLY. `spinr-admin-rbac-reviewer`
  verdict: SAFE TO MERGE (confirmed `"audit"` module is reachable through a
  real grant path in `staff.py`'s `AVAILABLE_MODULES`/`ROLE_PRESETS`, not a
  super-admin-only leak, and not a boundary-by-omission).

## User experience effect
None. No rider/driver/corporate-admin-facing change. Internal-admin-facing:
a new, currently-empty read-only list endpoint; no existing admin screen
changed.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/migrations/429_agent_action_log.sql` | New append-only table + indexes + RLS | Store agent action records |
| `backend/utils/agent_action_logger.py` | New file: `log_agent_action()` helper | Write path for the above |
| `backend/tests/test_agent_action_logger.py` | New file: 5 unit tests | Coverage for the helper |
| `backend/routes/admin/maintenance.py` | Added `GET /agent-actions` route | Read path for admins |
| `backend/tests/test_admin_maintenance_coverage.py` | Added `TestAgentActionLog` (2 tests) | Coverage for the new route |
| `docs/audit/2026-09-19-security-automation-roadmap.md` | New planning doc | Full 5-phase program roadmap |

## Before/after snippet
Before: no `agent_action_log` table or route existed.
After (new route, additive):
```python
@router.get("/agent-actions")
async def get_agent_action_log(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    agent_name: Optional[str] = Query(None),
    ...
    _admin: dict = Depends(require_module("audit")),
):
    ...
    return await db_supabase.get_rows("agent_action_log", filters, order="created_at", desc=True, limit=limit, offset=offset)
```

## Rollback plan
No live data depends on this yet (table has zero writers wired in as of this
commit). Rollback is a plain revert: `DROP TABLE IF EXISTS agent_action_log;`
(documented in the migration's own header comment) plus reverting the two
modified files. No feature flag needed — nothing reads or writes this table
in a hot path today.

## Verification performed
- `pytest backend/tests/test_agent_action_logger.py -v` — 5/5 pass.
- `pytest backend/tests/test_admin_maintenance_coverage.py -q` — 25/25 pass
  (full file, confirms no regression to existing `/audit-logs` tests).
- `spinr-migration-reviewer` agent run against the migration: SAFE TO APPLY,
  no blockers.
- `spinr-admin-rbac-reviewer` agent run against the new route: SAFE TO
  MERGE, no blockers/warnings.
- No `npm run build` applicable — backend-only change, no admin-dashboard
  frontend touched.

## What was NOT verified
- Migration was **not** applied against a real Supabase instance in this
  session (no DB credentials in this environment) — reviewed statically only.
  Apply via `python -m backend.scripts.run_migrations` in an environment with
  `DATABASE_URL` set, per root CLAUDE.md.
- No caller of `log_agent_action()` exists yet — the write path is unit-
  tested with a mocked `insert_one`, not exercised end-to-end against a real
  row. End-to-end verification happens naturally once Phase 2 (automated
  report generation) becomes the first real caller.
- RLS policy (`auth.jwt() ->> 'role' = 'admin'`) is dormant-but-correct per
  the root CLAUDE.md RLS caveat — not exercised against a real Supabase-
  issued admin session, since none exist in this app's actual auth model.
