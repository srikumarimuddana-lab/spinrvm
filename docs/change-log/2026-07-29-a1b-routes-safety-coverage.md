# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude (ACTION_ITEMS.md A1b, Track 1 item 2 — safety/insurance coverage push) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md A1b |

## 1. Issue / gap identified

`backend/routes/safety.py` (`POST /safety/report` — driver/rider safety-incident reporting) was at 80% coverage. Two real branches were untested: WS-18's ride-membership verification for a **driver** caller (only the rider-side branch had coverage), and the `notify_safety_team` best-effort exception path.

## 2. Root cause

Not applicable — new test coverage for existing, already-shipped behavior.

## 3. Fix / remediation

Added a `driver_client` fixture (mirrors the existing `client` fixture but authenticates as a driver — `is_driver: True`) and 3 new tests to `backend/tests/test_p3_addresses_favorites_safety_disputes.py`'s `TestSafety` class:
- `test_submit_report_driver_party_to_ride_gets_verified_ride_id` — a driver reporting on a ride they actually drove gets `ride_id` attached to the incident (the driver-side branch of the WS-18 membership check).
- `test_submit_report_non_party_ride_context_is_dropped` — a caller who isn't actually a party to the referenced ride still gets their report persisted, but `ride_id` is correctly dropped (anti-spoofing behavior).
- `test_submit_report_notify_safety_team_failure_does_not_fail_request` — `notify_safety_team` raising must not turn an already-persisted report into an error response (best-effort, per the route's own docstring).

## 4. Risk & impact on existing functionality

- **What else calls `submit_safety_report` / posts to `/safety/report`?** Grepped: only the rider-app and driver-app safety-report UI call this endpoint; no other backend code path calls the function directly.
- **Could this regress a working flow?** No — test-only, zero production code touched.
- **Blast radius:** isolated — one test file (new fixture + 3 new tests), no application code modified.

## 5. User-experience effect

None — backend test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_p3_addresses_favorites_safety_disputes.py` | New `driver_client` fixture; 3 new tests in `TestSafety` | Close the driver-side WS-18 branch and the `notify_safety_team` exception path |

## 7. Before / after

Not applicable — purely additive test file, no existing test or production code changed.

## 8. Rollback plan

`git-revert-safe` — test-only, no data or schema dependency.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_p3_addresses_favorites_safety_disputes.py` — 29 passed, 0 failed (was 26 before this change).
- [x] Coverage measured directly: `routes/safety.py` 80% → **95%** (remaining 3 lines are the dual-import `except ImportError` fallback — structurally only one branch runs per process, not a real gap, consistent with the convention already documented elsewhere in this codebase).
- [x] `ruff check` — clean.
- [x] Blast-radius grep performed.

## 10. What was NOT verified

- Not run against a real Supabase DB — mocked `db_supabase` per this file's existing convention.

## 11. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed (§4).
- [x] No silent behavior change — test-only, zero production code touched.
