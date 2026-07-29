# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude (ACTION_ITEMS.md A1b, Track 1 item 2 — safety/insurance coverage push) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md A1b |

## 1. Issue / gap identified

`backend/routes/admin/safety.py` (the admin safety-incident triage queue) was at 91% coverage. Untested: the explicit `status` filter override and the `severity`/`role`/`category`/`ride_id` list filters (only the default open+in_progress scope and `search` had coverage), the ride-snapshot lookup's own exception path (only the reporter lookup's identical-shaped exception path was covered), and the `assigned_to_admin_id`/`resolution_notes` update branches.

## 2. Root cause

Not applicable — new test coverage for existing, already-shipped behavior.

## 3. Fix / remediation

Added 6 new tests to `backend/tests/test_admin_safety_incidents.py`:
- `test_list_filters_by_severity_role_category_ride_id` — all 5 optional list filters (including an explicit `status` override) actually reach the DB filter dict.
- `test_detail_survives_ride_snapshot_lookup_failure` — a broken `rides` lookup doesn't 500 the detail view (mirrors the existing reporter-lookup-failure test, but for the ride table's independent try/except).
- `test_update_sets_assigned_to_admin_id` / `test_update_clears_assigned_to_admin_id_with_empty_string` — both the set and the explicit-clear-via-empty-string paths.
- `test_update_sets_resolution_notes` — the resolution-notes update branch on its own (previously only exercised as a side effect of the audit-log-redaction test, never asserted directly).

## 4. Risk & impact on existing functionality

- **What else reads/writes `safety_incidents`?** Grepped: `routes/safety.py` (report submission — separately covered in another A1b subtask), `routes/rides/safety.py` (SOS trigger + auto-escalation — also separately covered), and this admin queue. No other consumer.
- **Could this regress a working flow?** No — test-only, zero production code touched.
- **Blast radius:** isolated — one test file, no application code modified.

## 5. User-experience effect

None — backend test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_safety_incidents.py` | 6 new tests | Close list-filter, ride-snapshot-exception, and update-branch gaps |

## 7. Before / after

Not applicable — purely additive test file, no existing test or production code changed.

## 8. Rollback plan

`git-revert-safe` — test-only, no data or schema dependency.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_admin_safety_incidents.py` — 20 passed, 0 failed (was 14 before this change).
- [x] Coverage measured directly: `routes/admin/safety.py` 91% → **99%** (remaining 1 line is the dual-import `except ImportError` fallback, structurally untestable — same convention noted elsewhere in this codebase).
- [x] `ruff check` — clean.
- [x] Blast-radius grep performed.

## 10. What was NOT verified

- Not run against a real Supabase DB — mocked per this file's existing convention.

## 11. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed (§4).
- [x] No silent behavior change — test-only, zero production code touched.
