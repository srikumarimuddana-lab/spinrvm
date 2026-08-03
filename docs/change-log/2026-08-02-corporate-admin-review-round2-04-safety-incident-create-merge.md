# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | safety, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "safety incidents can't be created or merged from the admin side" |

## 1. Issue / gap identified

`routes/admin/safety.py` only had list/get/PATCH-status — an admin had no
way to (a) manually log an incident that came in by phone or in person
(never went through the app's own SOS flow), or (b) link two duplicate
reports of the same event (e.g. both rider and driver SOS the same ride)
together.

## 2. Root cause

The admin triage queue was built to review and update incidents the app
itself created (`routes/safety.py::submit_safety_report`,
`utils/safety_checkin_loop.py`'s auto-escalations) — an admin-initiated
create path, and any notion of "these two rows are the same event," were
simply never built.

## 3. Fix / remediation

- New migration `279_safety_incidents_merge.sql` — additive, nullable
  `merged_into_incident_id` column + partial index. The table's own CHECK
  constraint already had a `'duplicate'` status value waiting for this.
- New `POST /admin/safety/incidents` — the admin-initiated twin of
  `routes/safety.py::submit_safety_report`, same insert shape, RLS
  already anticipated this (94_safety_incidents.sql: "admins escalate via
  the backend API, not by writing directly to the table" — this endpoint
  is that API, using the same service-role backend write every other
  endpoint already uses).
- New `POST /admin/safety/incidents/{id}/merge` — sets `status='duplicate'`
  and `merged_into_incident_id`, **never deletes** (this table is an
  append-only regulated audit record under the SK Transportation Act,
  per its own table comment: "do not purge"). Rejects self-merge (422),
  404s on a missing source or target, and flattens merge chains to the
  root canonical ID so "find every duplicate of X" stays a single-hop
  query instead of needing chain-walking.
- Admin-dashboard: a "Log Incident" dialog (header button) and a
  "Duplicate of (merge)" control in the existing triage drawer, both
  calling the new endpoints.

## 4. Risk & impact on existing functionality

- **Blast radius: two new endpoints, one new column, one page's UI.**
  `admin_list_safety_incidents`, `admin_get_safety_incident`, and
  `admin_update_safety_incident` are byte-for-byte unchanged.
- Grepped every consumer of `safety_incidents`: `routes/safety.py`
  (rider/driver submission — unaffected, different insert path),
  `utils/safety_checkin_loop.py` (auto-escalation — unaffected), the
  admin list/detail/update endpoints (unaffected, `select("*")`-style
  reads simply gain a new nullable field), and the admin-dashboard
  `safety/page.tsx` (the only UI consumer, updated in this same commit).
- RLS: no policy change needed — `INSERT` was already service-role-only
  (the backend, which every write in this codebase goes through), and the
  merge endpoint only calls `update_one`, covered by the existing "Admin
  update safety_incidents" policy semantics at the ORM layer (the backend
  runs as service role regardless, same as every other write path).
- Never deletes a row, matching the table's own regulated-record
  constraint — a merge is fully reversible by clearing
  `merged_into_incident_id` and restoring `status`, unlike a hard delete
  would be.

## 5. User-experience effect

**Internal admin-facing only** (requires the `support` module grant, same
gate as the rest of the safety queue). Two new, additive controls on an
existing page — no change to any existing triage workflow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/279_safety_incidents_merge.sql` (new) | Additive `merged_into_incident_id` column + partial index | Give a merge somewhere to record its link |
| `backend/routes/admin/safety.py` | New `POST /incidents` (create) and `POST /incidents/{id}/merge` endpoints | Close both named gaps |
| `admin-dashboard/src/lib/api/safety-disputes.ts` | New `createSafetyIncident`/`mergeSafetyIncident` client functions; `merged_into_incident_id` added to the `SafetyIncident` type | Call the new endpoints |
| `admin-dashboard/src/lib/api.ts` | Re-export the two new functions | Match existing export pattern |
| `admin-dashboard/src/app/dashboard/safety/page.tsx` | New "Log Incident" dialog; new "Duplicate of (merge)" control in the triage drawer | Surface both new endpoints |
| `backend/tests/test_admin_safety_incidents.py` | 12 new tests: create (happy path, optional fields, validation, DB failure, audit-failure-doesn't-block) and merge (happy path, self-merge rejection, source/target 404s, chain-flattening, DB failure) | Lock in both new endpoints' behavior |

## 7. Before / after

```python
# Before — routes/admin/safety.py had list/get/PATCH only

# After
@router.post("/incidents")
async def admin_create_safety_incident(body: SafetyIncidentCreate, admin=Depends(get_admin_user)):
    ...  # same insert shape as routes/safety.py::submit_safety_report

@router.post("/incidents/{incident_id}/merge")
async def admin_merge_safety_incident(incident_id: str, body: SafetyIncidentMerge, admin=Depends(get_admin_user)):
    ...  # status='duplicate' + merged_into_incident_id, never deletes
```

## 8. Rollback plan

`git revert` the code changes. For the migration:
`DROP INDEX IF EXISTS idx_safety_incidents_merged_into; ALTER TABLE
safety_incidents DROP COLUMN IF EXISTS merged_into_incident_id;` (also in
the migration's own top comment). No data migration in either direction.

## 9. Verification performed

- [x] 12 new backend tests covering both endpoints' happy paths,
      validation, 404s, DB-failure→503 mapping, audit-log-failure
      isolation, self-merge rejection, and merge-chain flattening to the
      root canonical ID.
- [x] `python3 -c "import ast; ast.parse(...)"` on both touched Python
      files — clean.
- [x] Bracket-balance check on the touched `.tsx` files (no TS/JS
      toolchain run, per this round's instruction) — balanced.
- [x] Blast-radius grep performed (see §4): every reader/writer of
      `safety_incidents` and every consumer of the touched frontend files.
- [x] Confirmed via the table's own RLS policy comments
      (94_safety_incidents.sql) that admin-initiated creates were already
      anticipated to go through the backend API, not a new RLS grant.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert` + documented down-SQL
- [x] Blast radius is stated, not assumed — every table consumer grepped
- [x] No silent behavior change to a working flow — existing list/get/
      update endpoints and their tests are untouched; merge never deletes,
      preserving the table's regulated-record append-only guarantee

## What was NOT verified

Did not run `pytest`, `eslint`, `tsc --noEmit`, or a production build —
per this round's explicit instruction, deferred to a single pass at the
end. Did not run against a live Postgres instance. Did not manually
click through the new dialog/merge control in a browser — reasoned
through the existing drawer's established state/handler pattern
(mirroring `handleSave`) rather than screenshotted; no visual-regression
tooling exists in this repo for this surface (a standing, previously-
flagged gap). Did not add frontend component tests for the new dialog or
merge control — the backend behavior they call is fully covered; the UI
itself follows the same established component patterns already exercised
by the page's existing (untested-at-component-level) detail drawer.
