# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (session) |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | claude/b12-corporate-coverage-runbook |
| Related issue or gap ID | ACTION_ITEMS.md B12 |

## 1. Issue / gap identified

`backend/routes/corporate_company_bookings.py` was at 57% test coverage,
below the money-path floor — `list_bookings`, `cancel_booking`, and most of
`create_booking`/`booking_fare_estimate` had no direct test coverage, plus
several `sections` branches (update/archive error and no-op paths).

## 2. Root cause

The file was added incrementally (booking creation got one happy/blocked-path
test; sections got CRUD tests) but list/cancel/fare-estimate and several
section edge cases were never covered as the route grew.

## 3. Fix / remediation

Added `backend/tests/test_corporate_company_bookings_routes.py` covering:
`create_booking` (success + scheduled-metric tag + blocked-company +
`_require_company_active` null-row branch), `list_bookings` (member-scoped
vs admin-all-vs-filtered, status/date filters, member/guest join, section
filter), `cancel_booking` (not-found, wrong-company, non-guest ride,
member-can't-cancel-others, admin-can-cancel-any, customer-missing, and the
success path's delegate-to-`cancel_ride_rider` + guest-notify spawn), and
`booking_fare_estimate` (surge pinned to `Decimal("1")`).

Extended `backend/tests/test_corporate_sections.py` with `update_section`
success/no-op/duplicate-409/non-duplicate-reraise branches and
`archive_section`'s not-found branch.

Coverage for `routes/corporate_company_bookings.py`: 57% → 94% (measured via
`pytest --cov=routes.corporate_company_bookings --cov-report=term-missing`
against just the two files above). Remaining misses are the dual-import
`except ImportError` fallback branches (CLAUDE.md: intentional, not to be
"simplified away") and function-local import lines that mirror them — not
reachable from a single import style in one test run.

## 4. Risk & impact on existing functionality

**Test-only change — zero application code modified.** Blast radius:
isolated, no other callers — no production file in this commit.

## 5. User-experience effect

None. No behavior change; tests only exercise existing code paths.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_company_bookings_routes.py` | New file, 21 tests | Cover create/list/cancel/fare-estimate branches |
| `backend/tests/test_corporate_sections.py` | +6 tests | Cover update_section/archive_section edge cases |

## 7. Before / after

Not applicable — additive tests only, no existing test or application
behavior changed.

## 8. Rollback plan

`git revert` this commit. No data or production code is touched, so a plain
revert is a complete and sufficient rollback.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_corporate_company_bookings_routes.py backend/tests/test_corporate_sections.py -q` — 30 passed, 0 failed
- [x] Coverage measured: `pytest --cov=routes.corporate_company_bookings --cov-report=term-missing` against the two files above → 94%
- [ ] Not applicable: no manual/staging repro (test-only change)
- [x] Reviewed against CLAUDE.md Testing Conventions (patch target style, `@pytest.mark.anyio`, handlers called directly with explicit ctx per existing `test_corporate_sections.py` convention)

## What was NOT verified

- Coverage percentage above was measured running only the new/extended test
  files, not the full `pytest -k corporate` suite — running the full suite
  should not lower coverage (it's additive), but the exact combined number
  was not separately re-measured in this commit.
- No production code path was exercised against a real/staging Supabase
  instance — `mock_supabase_client`/direct `AsyncMock` patches only, per
  existing test conventions in this file's sibling tests.
