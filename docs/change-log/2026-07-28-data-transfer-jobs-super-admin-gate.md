# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session audit follow-up) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch — see PR touching `backend/routes/admin/data_transfer_jobs.py`) |
| Related issue or gap ID | P0 finding from Data Transfer + Corporate structured SDLC audit, 2026-07-28 |

## 1. Issue / gap identified

`data_transfer_jobs.py`'s three endpoints (`GET /data-transfer/jobs`, `GET /data-transfer/jobs/{id}`, `GET /data-transfer/jobs/{id}/download`) show every admin's export-job history — including which driver/rider entity IDs and PII fields were exported, and via the download endpoint, a live signed URL into the actual export bundle — to any admin. The route-level dependency is `require_module("bulk_operations")`, a per-admin flag broader than `super_admin`. A docstring in the same file claimed "this module is super_admin-gated already" to justify the cross-admin visibility as "oversight, not privilege escalation" — that claim was false.

## 2. Root cause

The module was built (per its own change-log trail) without an independent verification step against the actual RBAC gate it depends on. The author wrote the intended invariant into a comment instead of into an enforced check, and the gap was never caught because no test exercised the "non-super_admin with bulk_operations" case — only super_admin was ever tested.

## 3. Fix / remediation

Added a `_require_super_admin(admin)` guard (matching the existing convention already used in `ai_console.py` and `stripe_import.py`) called at the top of all three handlers in `data_transfer_jobs.py`, so a non-super_admin — even one holding the `bulk_operations` module flag — now gets a 403 with a clear message, before touching the DB or (for the download endpoint) Supabase Storage.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same table?** `data_transfer_export_jobs` is also written by `data_transfer_export.py`'s background export task and read by the retention purge loop (`utils/data_export_purge.py`). Neither is touched by this change — only the three read endpoints in `data_transfer_jobs.py`.
- **Could this regress a working flow?** Yes, deliberately: any admin who has `bulk_operations` but is not `super_admin` and was previously using the Jobs & History tab will now get a 403 on all three actions (list, poll status, re-download). This is treated as a security fix, not a regression, per the user's explicit choice to restrict to super_admin.
- **Blast radius:** isolated to this one file / these three endpoints. No other route imports or calls into `data_transfer_jobs.py`'s handlers.
- **Background loops / money / ride state machine:** none — this module has no interaction with any of the 16 background loops, money paths, or ride states.

## 5. User-experience effect

- **Who sees a difference:** internal admin only (super_admin vs. non-super_admin admins with the `bulk_operations` flag). No rider/driver/corporate-facing effect.
- **Mid-session visible?** Yes — an admin with the Jobs & History tab open who is not super_admin will now see "Failed to load jobs: Data Transfer job history requires super_admin" via the existing toast error-handling path in `JobsTab.tsx` (no frontend code changed; the existing `catch`-and-toast pattern already surfaces backend `detail` messages, so this is not a silent failure).
- **Copy change:** none — this is a new backend-generated error string, not new UI copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_jobs.py` | Added `_require_super_admin()` helper; called it first in `list_data_transfer_jobs`, `get_data_transfer_job`, and `regenerate_job_download_link`; corrected the stale "super_admin-gated already" docstring claim | Close the cross-admin PII-visibility gap; align code with the comment's original (incorrect) claim by making the claim true |
| `backend/tests/test_data_transfer_jobs.py` (new) | 6 tests: 403-denied path for a `bulk_operations`-flagged non-super_admin on all 3 endpoints, 200-allowed path for super_admin on all 3 endpoints; download test also asserts Storage is never called on the 403 path | Every auth/RLS policy needs both allowed and denied path coverage per `CLAUDE.md`'s testing conventions; this route previously had zero tests |

## 7. Before / after

```python
# Before
@router.get("/data-transfer/jobs")
async def list_data_transfer_jobs(
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
):
    """Most recent export batches across all admins (this module is
    super_admin-gated already, so cross-admin visibility here is oversight,
    not a privilege escalation). ..."""
    rows = await db_supabase.get_rows(...)
```

```python
# After
@router.get("/data-transfer/jobs")
async def list_data_transfer_jobs(
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
):
    """Most recent export batches across all admins. Restricted to
    super_admin: this module's router-level gate is the "bulk_operations"
    flag, which is broader than super_admin, and cross-admin export history
    ... shouldn't be visible to every admin who merely has bulk-import
    access. ..."""
    _require_super_admin(admin)
    rows = await db_supabase.get_rows(...)
```

(Same pattern applied to `get_data_transfer_job` and `regenerate_job_download_link`.)

## 8. Rollback plan

`git revert` is sufficient and safe here — this is a pure access-control tightening with no data mutation, no migration, and no in-flight state to reconcile. Reverting restores the previous (broader) access, which is acceptable to do without a data-cleanup step since no data was moved or changed by this fix itself.

## 9. Verification performed

- [x] Automated tests run: unit (`backend/tests/test_data_transfer_jobs.py`, 6/6 passing); `ruff check` clean on both modified/new files.
- [ ] Manual repro steps followed in staging — not performed; this was verified via unit tests against the FastAPI TestClient with dependency overrides, not a real staging admin session.
- [x] Blast-radius grep performed: `grep -rn "data_transfer_jobs" backend/` confirmed only `routes/admin/__init__.py` (router registration) references this module; no other backend code imports its handlers directly.
- [x] Reviewed against relevant CLAUDE.md convention(s): JWT trust model (admin role re-checked server-side, not trusted from claims alone — matches existing pattern), PIPEDA (this fix *reduces* PII exposure, doesn't introduce any).
- [ ] Feature-flagged — not applicable; access-control fixes are not typically flagged (there's no safe "old behavior" to fall back to for a security tightening), consistent with how `stripe_import.py`'s equivalent super_admin gate was shipped without a flag.

## 10. What was NOT verified

- Not tested against a real Supabase/staging environment — only against `unittest.mock`-backed FastAPI TestClient overrides.
- Did not audit which real admin accounts currently hold the `bulk_operations` flag without `super_admin` — i.e., how many people are actually affected by this tightening in the live admin roster. That's an operational question for whoever manages admin role assignments, not something inferable from code.
- Did not check whether any other internal tooling or script calls these three endpoints outside the admin-dashboard UI (e.g., a support runbook curl command) that would also need a role bump.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-cleanup dependency).
- [x] Blast radius is stated, not assumed (isolated to this file; grep-verified).
- [x] No silent behavior change — the UX-effect section above states the visible 403 + toast a non-super_admin will now see.
