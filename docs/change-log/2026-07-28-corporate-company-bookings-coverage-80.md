# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-company-bookings-coverage-r2` (see PR link in description) |
| Related issue or gap ID | ACTION_ITEMS.md A1b, track 1, item 3 (`routes/corporate_company_bookings.py`) |

## 1. Issue / gap identified

`backend/routes/corporate_company_bookings.py` (guest ride booking, listing,
cancellation, fare estimate, and company-section CRUD for the corporate
portal) measured at 38% test coverage, below the ≥80% target CLAUDE.md sets
for `routes/corporate_*.py` (money-moving, same tier as rides/dispatch).

## 2. Root cause

The booking-creation, listing/tenancy-filter, and cancellation paths never
had a dedicated test file. Only the sections CRUD sub-section (added in an
earlier PR) had coverage via `test_corporate_sections.py`.

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_corporate_company_bookings_coverage.py`
(31 tests) covering:
- `_require_company_active` (active / pending_verification / missing-company gates)
- `_booking_row` projection (confirms `pickup_otp` and guest `last_name` never leak to the booker)
- `create_booking` (inactive-company 403, success path incl. tracking URL / no-OTP-in-response, scheduled vs. immediate metric tag, E.164 phone normalization)
- `list_bookings` (member-scoped vs. owner/admin-sees-all, `member_id`/`status`/date-range filters, batch member+guest joins with no N+1, `section_id` post-filter, empty-rides short-circuit)
- `cancel_booking` (not-found, cross-company 404, non-guest-booking 404, member-can't-cancel-others 403, admin-can-cancel-any, missing-guest-user 404)
- `booking_fare_estimate` (surge pinned to `Decimal("1")`)

No application code was modified.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Only a new test file was added; `backend/routes/corporate_company_bookings.py` and every other file are unchanged.
- Grepped for other consumers of `corporate_company_bookings.py`'s public router: it is mounted once in `backend/server.py` as `corporate_company_bookings_router`, alongside `corporate_company_router` (`routes/corporate_company.py`). No other module imports functions from this file directly except the test file itself and `test_corporate_sections.py` (pre-existing, untouched).
- No background loop, migration, or shared table schema touched.
- Since this is test-only, there is no runtime behavior change and nothing to regress in a live session.

## 5. User-experience effect

None. No application code changed — riders, corporate-portal bookers, and admins see no behavior difference.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_company_bookings_coverage.py` | New file, 31 tests | Close the coverage gap on `routes/corporate_company_bookings.py` per ACTION_ITEMS.md A1b |
| `ACTION_ITEMS.md` | Updated A1b's corporate-billing coverage list with the measured 87% result and pointer to this change-log | Keep the backlog entry accurate |
| `docs/change-log/2026-07-28-corporate-company-bookings-coverage-80.md` | New file (this document) | Required Change Impact Log for a corporate-surface change per CLAUDE.md |

## 7. Before / after

Not applicable — additive test file only, no behavior-changing diff to any shipped code path.

## 8. Rollback plan

Delete `backend/tests/test_corporate_company_bookings_coverage.py` (or `git revert` the commit) and revert the `ACTION_ITEMS.md` edit. No live data, feature flag, or migration is involved — a plain code revert is sufficient and safe here because nothing runtime-visible changed.

## 9. Verification performed

- [x] Automated tests run: `cd backend && python -m pytest tests/test_corporate_company_bookings_coverage.py tests/test_corporate_sections.py --cov=routes.corporate_company_bookings --cov-report=term-missing -q` — **31 passed**, coverage on `routes/corporate_company_bookings.py`: **87%** (181 statements, 24 missed: lines 26, 34-38, 235-236, 249, 373, 389-401, 414 — dual `except ImportError` fallback branches and section duplicate/404 paths already covered by `test_corporate_sections.py`).
- [ ] Manual repro steps followed in staging — N/A, test-only change, no staging deploy needed.
- [x] Blast-radius grep performed: searched for other importers of `corporate_company_bookings` router/functions — only `backend/server.py` (router mount) and the two test files above.
- [x] Reviewed against relevant CLAUDE.md convention: testing conventions (`mock_supabase_client`-style patching at `backend.routes.corporate_company_bookings.db_supabase.*`, `@pytest.mark.anyio`, handlers called directly with an explicit `ctx` matching `test_corporate_sections.py`'s established pattern).
- [ ] Feature-flagged — N/A, test-only change is not user-visible.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (delete/revert the test file; no data-level remediation needed)
- [x] Blast radius is stated: isolated, test-only, single file plus two doc updates
- [x] No silent behavior change — nothing in `backend/routes/corporate_company_bookings.py` or any other application file was modified

## What was NOT verified

- Not tested against a real Supabase instance — all DB calls are mocked via `unittest.mock.patch` on `db_supabase.*`, matching this module's existing test style; no integration-tier test against a throwaway schema was added.
- `create_company_guest_booking` (the service function `create_booking` delegates to) was mocked at the boundary rather than exercised end-to-end — its own internals (fare calc, guest user creation, policy evaluation, ride insert/dispatch) are covered separately by `services/company_booking_service.py`'s own test surface, not by this file.
- `cancel_ride_rider` (the rides-module function `cancel_booking` delegates to) was likewise mocked at the boundary; its internal state-machine correctness is covered by `test_ride_state_machine.py`, not here.
- No production build was run — this is a backend-only, test-only change; `npm run build` is not applicable (no admin-dashboard/rider-app/driver-app code touched).
- Discovered while writing these tests (not fixed, per constraints): calling `list_bookings` directly (bypassing FastAPI's request pipeline) leaves `Query(...)`-wrapped default parameters unresolved — every optional query param had to be passed explicitly as a keyword to get real `None`/default values instead of a live `Query` object. This is a pre-existing property of calling FastAPI handlers directly in tests (also true of every other handler with `Query(...)` defaults in this codebase), not a bug in `corporate_company_bookings.py` itself — flagging in case a future contributor hits the same surprise writing more direct-call tests against `Query`-parameterized handlers elsewhere.
