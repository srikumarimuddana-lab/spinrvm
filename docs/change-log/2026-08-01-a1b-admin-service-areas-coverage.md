# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude (A1b Track 1 coverage initiative) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR — `claude/admin-service-areas-coverage`) |
| Related issue or gap ID | ACTION_ITEMS.md item 4 (`backend/routes/admin/` coverage floor) |

## 1. Issue / gap identified

`backend/routes/admin/service_areas.py` (service-area config, surge overrides,
area fees, tax config, vehicle pricing) measured 65.80% line coverage
(348 statements, 119 missing), below this repo's 70% admin-routes floor.

## 2. Root cause

The module accumulated write endpoints (create/update/delete service area,
surge pricing, area fees, area tax, vehicle pricing) and validation guards
(airport bbox/subregion checks, surge-multiplier bounds/justification) over
several PRs without matching unit tests — only a handful of narrow
regression tests existed (`test_service_area_create_regulatory.py`,
`test_service_areas_public.py`, etc.), covering create's regulatory fields
and the public read path, but not the update/delete/fees/tax/vehicle-pricing
handlers or most validation branches.

## 3. Fix / remediation

**Test-only.** Added `backend/tests/test_admin_service_areas_coverage.py`
(32 new unit tests) covering:
- `admin_get_service_areas` parent/sub-region nesting
- `admin_create_service_area`: airport-bbox guard, airport-on-top-level guard,
  happy path, `subscription_required` → `spinr_pass_enabled` coercion,
  vehicle-pricing auto-seed from active vehicle types
- `admin_update_service_area`: airport-flip-on-top-level guard, surge-above-cap
  without/with justification, surge-disable-without-justification-required,
  `subscription_required`/`spinr_pass_enabled` coercion (both directions),
  surge-disable clears active+multiplier, `airport_fee=0` clears `is_airport`,
  empty-payload short-circuit
- `admin_delete_service_area` happy path
- `admin_update_surge_pricing`: activate (insert history row) and
  deactivate (update existing history row) branches
- `admin_get_surge_status` happy path + DB-failure 503
- Area-fees CRUD (create/update/partial-update/delete)
- Area-tax get (default + row-present) and update (write + no-op)
- Vehicle-pricing get (rows present + `None`→`[]` defaulting)

No application code in `service_areas.py` was modified.

## 4. Risk & impact on existing functionality

**None — test-only change, zero production code touched.** Blast radius is
the test suite itself, isolated to this one new file plus this log and an
`ACTION_ITEMS.md` bullet.

Blast-radius grep performed for completeness even though no app code
changed: no other module imports from `test_admin_service_areas_coverage.py`
(new file, not imported elsewhere). The handlers under test
(`admin_get_service_areas`, `admin_create_service_area`,
`admin_update_service_area`, `admin_delete_service_area`,
`admin_update_surge_pricing`, `admin_get_surge_status`,
`admin_get_area_fees`/`admin_create_area_fee`/`admin_update_area_fee`/
`admin_delete_area_fee`, `admin_get_area_tax`/`admin_update_area_tax`,
`admin_get_vehicle_pricing`) are called only via the FastAPI router mounted
in `backend/routes/admin/__init__.py`; this PR does not touch that mount or
any caller.

## 5. User-experience effect

None. No admin-facing or rider/driver-facing behavior changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_service_areas_coverage.py` | New file, 32 unit tests | Close coverage gap on `service_areas.py` |
| `docs/change-log/2026-08-01-a1b-admin-service-areas-coverage.md` | New file | Required Change Impact Log for this PR |
| `ACTION_ITEMS.md` | Added bullet under item 4 | Track this module in the coverage series |

## 7. Before / after

N/A — additive test-only change, no behavior-changing diff.

## 8. Rollback plan

Revert the test-file commit (`git revert`) — safe for a test-only change
since no production code path, migration, or live data is touched. No
feature flag or data remediation needed.

## 9. Verification performed

- [x] Automated tests run: `python -m pytest tests/test_admin_service_areas_coverage.py -q --no-cov` → 32 passed
- [x] Automated tests run: full suite `python -m pytest tests/ -q --no-cov` (see PR / final report for pass/fail counts)
- [x] Coverage measured: `python -m pytest tests/ -k "service_area" -q` (project's default `--cov=.` addopts) → `routes/admin/service_areas.py` 91% (up from 65.80% baseline / 59% on the pre-existing `-k service_area` subset)
- [x] Blast-radius grep performed (see §4) — isolated, test-only
- [x] Reviewed against `CLAUDE.md` testing conventions (`mock_supabase_client`-equivalent pattern via `patch("backend.routes.admin.service_areas.db_supabase.<fn>", AsyncMock(...))`, matching this module's existing `test_service_area_create_regulatory.py` style; `@pytest.mark.anyio` used throughout)
- [ ] Manual repro / staging check — not applicable, no runtime behavior changed
- [x] Feature-flagged if user-visible — N/A, not user-visible

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, safe for test-only change)
- [x] Blast radius is stated, not assumed (isolated to new test file)
- [x] No silent behavior change to an already-shipped flow — none made

## Bug found but not fixed (per this series' established pattern, e.g. PR #2948)

`admin_update_service_area`'s manual surge-multiplier range check
(`sm < 1.0 or sm > _SURGE_MAX` at ~line 523-527 of `service_areas.py`) is
**dead code** through the normal request path: `ServiceAreaUpdateRequest
.surge_multiplier` is already a Pydantic `Field(ge=1.0, le=10.0)`, and
`_SURGE_MAX = 10.0` is numerically identical to that upper bound. Pydantic
rejects any out-of-range value with a 422 before the handler body ever runs,
so the handler's own `raise HTTPException(400, ...)` for that condition is
unreachable in practice. Not a live-data risk (Pydantic's 422 is stricter,
not looser, than the dead branch), so not fixed here per this initiative's
test-only scope — flagging for a follow-up cleanup PR to either remove the
redundant handler-level check or intentionally loosen the Pydantic bound if
a wider intake range was actually intended.
