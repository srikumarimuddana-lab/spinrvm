# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, rides |
| PR / commit link | (this branch — `claude/saskatoon-city-monthly-report-7xwtco`, restarted from `main` after PR #4908 merged) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-09-03-saskatoon-city-trip-log-report.md` (PR #4908, merged) |

## 1. Issue / gap identified

PR #4908 (merged 2026-09-03) added a `Driver_License_Number` column to the Saskatoon City trip log at the requester's explicit ask. On reviewing the merged report, the requester reconsidered: a driver's license number is a government ID that doesn't belong in a *municipal* per-trip log — it's reserved elsewhere in this codebase for the SGI/insurance-side reports and the internal Driver Roster. Asked to remove it.

## 2. Root cause

Not a bug — reversing a shipped, explicitly-requested feature after reconsideration. Same-session round-trip: added in PR #4908, removed here.

## 3. Fix / remediation

Removed `Driver_License_Number` end-to-end, restoring `_saskatoon_city_trip_log_rows` and its endpoint to the original 6-column shape (`Request_Timestamp` / `Accept_Timestamp` / `Begin_Timestamp` / `End_Timestamp` / `Passenger_Wait_Time (Mins)` / `Trip_Status`):
- `backend/routes/admin/compliance.py`: dropped the batched `drivers` lookup + `_decrypt_driver_pii` call, the `driver_id` column fetch (no longer needed once the lookup is gone), the `Driver_License_Number` dict key, its `fieldnames` entry, and the extra `pdf_col_widths` entry. Docstrings updated to state the omission is deliberate (not an oversight) and to point at both change-log entries for context.
- `backend/tests/test_compliance_reports.py`: reverted the 3 tests to their pre-follow-up shape and deleted the 3 license-specific tests (lookup/decrypt wiring, batching-scoped-to-kept-rows).
- `admin-dashboard/src/app/dashboard/compliance/page.tsx`: reverted the Saskatoon City tab's description text to drop the license-number mention. The page-level Service Area filter's hint text (which now correctly says both T4A and Saskatoon City bypass it) was untouched — that fix is still correct independent of the license-number question.

Branch was restarted from `main` before this change (`git merge --ff-only origin/main`) per this repo's convention for follow-up work after a PR has already merged, rather than stacking new commits on merged history. That fast-forward also picked up an unrelated fix already on `main` (commit `5a791b9`, PR #4922): a different session found and fixed a real bug in the original PR #4908 — `parse_iso_utc` was imported in `compliance.py`'s package-relative (`try:`) import branch but not its top-level (`except ImportError:`) fallback, violating CLAUDE.md's dual-import convention and raising `NameError` at every call site when the module loaded via the fallback path. Confirms the honest gap flagged in the original change-log entry (§9: "pytest was NOT run" in this sandboxed session) was a real, not just theoretical, risk.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated — same read-only endpoint, strictly fewer columns and one less table read (`drivers`).** No new code paths introduced; this is a subtraction.
- No schema change, no data ever written or deleted (the column only ever existed in a generated report response, never persisted).
- Removes the PIPEDA consideration the license-number addition introduced — a driver government ID no longer leaves the system via this report at all, so there's nothing further to reason about on that front for this endpoint.

## 5. User-experience effect

- **Internal (super_admin) only**, same as before. The Saskatoon City tab's generated report now has 6 columns instead of 7; no other UI change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/compliance.py` | Removed the driver-license batching/decrypt block, `driver_id` column fetch, `Driver_License_Number` field, and its `fieldnames`/`pdf_col_widths` entries | Revert per requester decision |
| `backend/tests/test_compliance_reports.py` | Reverted 3 tests, deleted 3 license-specific tests | Match reverted behavior |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | Reverted the Saskatoon City tab's description text | Match reverted behavior |

## 7. Before / after

```python
# Before (PR #4908 follow-up):
rows.append({..., "Trip_Status": trip_status, "Driver_License_Number": license_by_driver.get(r.get("driver_id"), "")})

# After (this change):
rows.append({..., "Trip_Status": trip_status})
```

## 8. Rollback plan

Plain `git revert` — pure subtraction, no data written or migrated. Re-adding the column, if ever wanted again, is the diff this change removes (visible in PR #4908's history).

## 9. Verification performed

- [x] `ruff check` and `ruff format --check` clean on both changed Python files.
- [x] `python3 -c "import ast; ast.parse(...)"` syntax-checked both changed Python files.
- [x] Manual trace confirming the reverted `_saskatoon_city_trip_log_rows` matches the original (pre-follow-up) 6-column behavior exactly, and that `_decrypt_driver_pii`'s import stays (still used by `_driver_roster_rows` and the insurance-billing report, unrelated to this change).
- [ ] **`pytest` still NOT run** — same sandboxed-environment network restriction as the original entry (`pypi.org`/`files.pythonhosted.org` blocked). Run `pytest backend/tests/test_compliance_reports.py -k Saskatoon` before merging — this matters more than usual here, given a different session already had to fix one real bug in this report that this same gap let through.
- [ ] No production build of `admin-dashboard` run, same reason.
- [ ] Not visually verified in a browser.

## 10. What was NOT verified / deferred

- Same residual items as the original entry: the report's column set and date-field framing are still not confirmed against an actual City of Saskatoon published spec (only against the requester's own stated intent), and the second City-of-Saskatoon report mentioned by the requester is still unscoped.
- **`pytest` has not been run against this change or the original PR #4908 change in any session available here** — only inferred from a separate session's fix commit that the suite runs and catches real bugs in this file. Treat this report as needing a real test run before its next merge, not just a lint pass.
