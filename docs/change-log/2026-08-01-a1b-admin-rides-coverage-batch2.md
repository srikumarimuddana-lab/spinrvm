# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides, admin |
| PR / commit link | see PR description |
| Related issue or gap ID | ACTION_ITEMS.md A1b Track 1, item 4 (`backend/routes/admin/`) — `rides.py` gap deferred from PR #2937 |

## 1. Issue / gap identified

`backend/routes/admin/rides.py` (1190 statements) was the largest remaining coverage
gap in the admin-routes coverage initiative. PR #2937 took it from ~34% → 42% by
prioritizing ride-mutation/money-adjacent endpoints (cancel, complete, create,
send-payable-invoice, payout retry/bulk-retry/close-period). Incidental coverage from
elsewhere in the suite later pushed it to 52.35% (full-suite measurement). The
deliberately-deferred remainder was the read/list/export/analytics endpoints.

## 2. Root cause

Not a bug — this is a pure test-coverage gap. The prior PR explicitly scoped mutation
endpoints first (higher consequence) and left read-only endpoints (ride details,
location trail, live ride, invoice, receipt, heatmap, earnings, exports, payouts
overview, admin stats, fare estimate, promo preview, places proxy) with only smoke
coverage or none.

## 3. Fix / remediation

Extended `backend/tests/test_admin_rides_coverage.py` (test-only, no application code
changed) with 24 new test functions covering:
- `GET /rides/{id}/location-trail`, `/live` (404 + happy path)
- `GET /rides/{id}/invoice` (404, non-locked, fare-locked-with-snapshot branches)
- `POST /rides/{id}/send-receipt` (404, no-email 422, provider-failure 502, happy
  path with an override email)
- `GET /rides/heatmap-data` (corporate filter + point aggregation)
- `GET /earnings`, `/earnings/rides`, `/earnings/overview` (happy path + invalid
  period 422)
- `GET /export/rides`, `/export/drivers` (happy path, audit-log-write assertion)
- `GET /payouts/overview` (empty-shell happy path, service-area-with-no-drivers
  early-return branch)
- `GET /stats` (admin dashboard aggregate happy path)
- `GET /rides/fare-estimate`, `POST /promo/preview` (happy path)
- `GET /places/autocomplete`, `/places/details` (503 not-configured guard)

Single-file measured coverage on `routes/admin/rides.py` went from 44% (with only
PR #2937's 57 tests) to **70%** with this batch's 24 additional tests (81 tests
total in the file). See "What was NOT verified" for why this differs slightly from
a full-suite number.

## 4. Risk & impact on existing functionality

Test-only change — no production code in `backend/routes/admin/rides.py` or
elsewhere was modified. Blast radius: isolated to
`backend/tests/test_admin_rides_coverage.py`. No other test file imports from it.
Nothing here writes to a shared fixture, table, or background loop; all DB/Stripe/
email calls are mocked via `AsyncMock`/`patch`, matching the established pattern
from PR #2937 and `test_admin_business_logic.py`.

## 5. User experience effect

None. No application code changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_rides_coverage.py` | Added 24 test functions to `TestAdminRidesReadEndpointsSmoke` | Close coverage gap on read/list/export/analytics admin ride endpoints |
| `docs/change-log/2026-08-01-a1b-admin-rides-coverage-batch2.md` | New Change Impact Log (this file) | Required by CLAUDE.md for any commit touching a live-tested surface's test coverage on rides |
| `ACTION_ITEMS.md` | Updated A1b Track 1 item 4 bullet for `rides.py` | Reflect updated coverage % and note remaining gap |

## 7. Before / after

Not applicable — purely additive test code, no existing test or application behavior
changed.

## 8. Rollback plan

`git revert` is sufficient and safe here: this commit adds tests only, touches no
live data, no ride state, no Stripe charges, no wallet deltas. Reverting removes the
new tests and change-log entry with no other side effects.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_rides_coverage.py -q --no-cov` → 81 passed.
- [x] Automated tests run: full `pytest backend/tests/ -q --no-cov` (whole suite) — see PR description for the exact pass/fail count from this run.
- [x] Coverage measured: `pytest --cov=routes.admin.rides --cov-report=term-missing tests/test_admin_rides_coverage.py` → **70%** (single test-file scope).
- [ ] Manual repro steps in staging — not applicable, test-only change.
- [x] Blast-radius grep performed: confirmed `test_admin_rides_coverage.py` is not imported by any other test module (`grep -rn "test_admin_rides_coverage" backend/tests/`).
- [x] Reviewed against relevant CLAUDE.md conventions: ride-state-machine transitions were NOT touched by this batch (all new tests target read-only endpoints); Decimal-only money assertions preserved from the existing style (`Decimal(str(...))` comparisons, never float equality on money fields).
- [x] Feature-flagged: not applicable (test-only).

## What was NOT verified

- The 70% figure is measured running only `tests/test_admin_rides_coverage.py` in
  isolation with `--cov=routes.admin.rides`. The task's baseline (52.35%) was
  measured via a **full-suite** `pytest --cov=routes.admin` run, where other test
  files exercise `routes/admin/rides.py` incidentally (e.g. through shared
  dependency-override fixtures or admin-dashboard integration tests). The full-suite
  percentage after this change was not independently re-measured with `--cov`
  end-to-end in this session because of a coverage-instrumentation-triggered
  `pydantic`/`pyiceberg` `KeyError: 'pydantic.root_model'` import failure specific to
  this sandbox's installed package versions (reproduces only when `coverage.py`
  tracing is active during `supabase`'s `storage3` → `pyiceberg` import chain — a
  plain, non-coverage `import backend.server` succeeds). A workaround
  (pre-importing `pydantic.root_model` before `backend.server` in `conftest.py`) was
  used locally to obtain the single-file 70% number but was **not** committed, since
  it is an environment-specific workaround out of this task's stated scope
  (test-only additions to `test_admin_rides_coverage.py`). The true full-suite
  percentage for `routes/admin/rides.py` after this change is very likely ≥ 70%
  (single-file coverage is normally a lower bound vs. full-suite, since the full
  suite adds incidental coverage from other files) but this was not independently
  confirmed with `--cov` in this session — only the single-file `--cov` run and a
  full-suite `--no-cov` pass/fail run were performed.
- No new bug was found or fixed in the newly-tested endpoints. The previously
  flagged `admin_get_payout_stats` route-shadowing bug (from a prior PR) remains
  un-fixed, per this initiative's test-only scope; it is not re-litigated here.
- No visual/UI regression tooling exists for the admin dashboard consumers of these
  endpoints (heatmap, earnings, payouts overview) — this batch verifies backend
  response shape only, not the admin-dashboard frontend rendering of these payloads.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer risk)
- [x] Blast radius is stated: isolated to one test file
- [x] No silent behavior change — purely additive tests, no app code touched
