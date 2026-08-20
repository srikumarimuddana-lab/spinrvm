# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (promo redemption is a rider-facing eligibility/discount path) |
| PR / commit link | (worktree branch `worktree-agent-a8a7c251547b593c0`, see commit SHA below) |
| Related issue or gap ID | ACTION_ITEMS.md B31 |

## 1. Issue / gap identified

`backend/routes/promotions.py` counts a rider's completed rides to gate `first_ride_only`,
`inactive_days`, `min_total_rides`, and `max_total_rides` promo eligibility, but the count
included legacy-imported (pre-Spinr, old-app) completed rides — so a rider whose only
"completed rides" are old-app history could be wrongly denied a first-time-rider promo, or
wrongly appear to meet a min-rides tier they never actually reached on Spinr.

A prior investigation confirmed this against production data: **186 legacy-completed rides,
79 distinct affected riders**. **Zero live impact today** — no currently-active promo uses
`first_ride_only`/`min_total_rides`/`max_total_rides` — but it is a live-request-path gap
that should be closed correctly now that it's understood, before any promo campaign turns
these gates on.

## 2. Root cause

`services/booking_import_service.py` imported the previous app's completed bookings into the
`rides` table (real rows, real `status='completed'`, real `rider_id`) so riders/drivers keep
their trip history. Every one of promotions.py's ride-count queries filtered only on
`{"rider_id": ..., "status": "completed"}` — with no predicate distinguishing a legacy-imported
row (`legacy_import_metadata` non-empty) from a real Spinr ride. The repo already has an
established fix for exactly this shape of bug — `backend/utils/legacy_rides.py`'s
`EXCLUDE_LEGACY_RIDES` filter, used by `routes/admin/drivers.py`, `routes/drivers/earnings.py`,
`utils/driver_statement.py`, `utils/t4a_annual_job.py`, `utils/auto_payout.py`,
`routes/admin/analytics.py` for the earnings/payout side — but `promotions.py` had never
adopted it for the eligibility-counting side.

## 3. Fix / remediation

**Option B (product decision, confirmed by the user)**: a rider's first *real Spinr* ride is
the semantic that should govern eligibility, not their old-app history. Merged the existing
`EXCLUDE_LEGACY_RIDES` filter (`{"legacy_import_metadata": {"$eq": {}}}`) into every
`count_documents("rides", ...)` filter dict in `promotions.py` that feeds an eligibility rule:

- Rule 6 (`first_ride_only`) — `_validate_promo_for_user`
- Rule 8 (`inactive_days`, "no rides in X days") — `_validate_promo_for_user`
- Rule 9 (`min_total_rides` / `max_total_rides`) — `_validate_promo_for_user`
- The `total_rides` pre-fetch that feeds rules 6 and 9 in `list_available_promos`
  (GET `/promo/available`'s eligibility engine)
- The inactive-days twin inside `list_available_promos`'s per-promo loop

No new filter shape was invented — this reuses the constant exactly as
`routes/admin/drivers.py` (line ~2716) and `routes/drivers/earnings.py` already do, merged
into each existing filter dict with `**EXCLUDE_LEGACY_RIDES`.

## 4. Risk & impact on existing functionality

- **Blast radius: single-file, isolated.** `count_documents("rides", ...)` in this file is
  only ever called from these 5 sites — grepped the whole backend for other readers of
  the same result. No other code path recomputes or caches these counts.
- **Callers of the 3 shared functions this file exports are all pass-through, not
  duplicated logic** — grepped for every caller:
  - `backend/routes/rides/booking.py` (`POST` ride creation's promo validation) calls
    `_validate_promo_for_user` — inherits the fix automatically, no separate change needed.
  - `backend/routes/admin/rides.py` (admin "apply promo on behalf of rider" in Create Ride)
    calls both `_validate_promo_for_user` and `apply_promo_for_admin` — same shared function,
    same fix, so the admin-apply path and the rider self-serve path can never disagree about
    eligibility.
  - `backend/ai/tools_booking.py` (AI assistant's fare-quote tool) calls
    `list_available_promos` and `compute_promo_discount` — the former inherits the fix; the
    latter (`compute_promo_discount`) is pure discount-amount math with no ride-counting, out
    of scope.
  - `backend/ai/tools_account.py` only picks display fields off a promo row (`_PROMO_FIELDS`)
    — no ride-counting logic, unaffected.
  - `backend/routes/admin/promotions.py` is the admin CRUD schema/endpoints for promo *rows*
    (create/update `first_ride_only`/`min_total_rides` as stored settings) — it does not
    itself count rides, unaffected.
- **Money/state impact**: none directly — this changes an eligibility gate, not a fare,
  wallet, or ride-state write. No Stripe charge, wallet delta, or ride-state transition is
  touched.
- **Regression risk**: the change is strictly *narrowing* an existing count (fewer rides now
  qualify as "prior rides"), which can only make `first_ride_only`/`min_total_rides` MORE
  permissive and `max_total_rides`/`inactive_days` LESS permissive for a rider with legacy
  history. For the 79 affected riders this is the intended correction, not a regression. For
  the remaining rider base (no legacy rides), `legacy_import_metadata` is always `{}` by
  construction (migration 268 default), so `EXCLUDE_LEGACY_RIDES` matches every one of their
  real rides — the filter is a no-op for non-imported riders and every existing test/behavior
  for that (overwhelming) majority is unchanged.
- **No currently-active promo is gated by these 3 rules today** (confirmed by the prior
  investigation), so there is no live promo campaign whose numbers shift the moment this
  merges — the correction is inert until such a campaign is created, which is exactly the
  "close it correctly now, before it matters" framing of this ticket.

## 5. User-experience effect

- **Rider-facing eligibility change**, currently inert (no live promo uses these gates).
  Once a `first_ride_only`/`min_total_rides`/`max_total_rides`/`inactive_days` promo is
  created, the 79 riders with only-legacy ride history will see corrected eligibility
  (e.g. a `first_ride_only` promo becomes available to them where it previously was not).
- **Not visible mid-session** — a rider's eligibility for a promo is only read at
  `/promo/validate`, `/promo/apply`, and `/promo/available` request time; there is no
  cached/streamed value that would change under a rider already looking at a promo screen
  mid-session from this deploy alone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/promotions.py` | Imported `EXCLUDE_LEGACY_RIDES` (dual-import pattern, both branches); merged it into the filter dict of all 5 `count_documents("rides", ...)` calls (rules 6, 8, 9 in `_validate_promo_for_user`; the `total_rides` prefetch and the inactive-days twin in `list_available_promos`) | Exclude legacy-imported rides from promo eligibility counts (ACTION_ITEMS.md B31, Option B) |
| `backend/tests/test_routes_promotions_coverage.py` | Added `TestValidatePromoForUserExcludesLegacyRides` and `TestListAvailablePromosExcludesLegacyRides`, plus a filter-aware fake `count_documents` (`_fake_rides_count_documents`) that actually inspects the `EXCLUDE_LEGACY_RIDES` predicate instead of a hardcoded return value | Regression coverage for the fix — proves the filter itself, not just a mocked count, drives the eligibility outcome |
| `docs/change-log/2026-08-20-b31-promo-legacy-ride-exclusion.md` | New Change Impact Log entry | Required by CLAUDE.md for any commit touching a live-tested surface (rides/payments) |

## 7. Before / after

```python
# Before (rule 6, first_ride_only — routes/promotions.py, _validate_promo_for_user)
if promo.get("first_ride_only"):
    ride_count = await db_supabase.count_documents(
        "rides", {"rider_id": user_id, "status": RideStatus.COMPLETED}
    )
    if ride_count > 0:
        raise HTTPException(status_code=400, detail="This promo is for first-time riders only")
```

```python
# After
if promo.get("first_ride_only"):
    ride_count = await db_supabase.count_documents(
        "rides", {"rider_id": user_id, "status": RideStatus.COMPLETED, **EXCLUDE_LEGACY_RIDES}
    )
    if ride_count > 0:
        raise HTTPException(status_code=400, detail="This promo is for first-time riders only")
```

The same `**EXCLUDE_LEGACY_RIDES` merge was applied to the other 4 `count_documents("rides", ...)`
filter dicts (rules 8 and 9 in `_validate_promo_for_user`; the `total_rides` prefetch and the
inactive-days check in `list_available_promos`) — same shape, same reasoning.

**Concrete before/after scenario (dry run)**: rider X has 2 legacy-imported completed rides
and 0 real Spinr rides.

- **Before this fix**: `first_ride_only` promo → `count_documents(...)` returns 2 (legacy rides
  counted) → `ride_count > 0` → `400 "This promo is for first-time riders only"` — rider X is
  incorrectly denied a first-ride promo despite never having taken a real Spinr ride.
- **After this fix**: `count_documents(..., **EXCLUDE_LEGACY_RIDES)` returns 0 (legacy rides
  excluded) → rider X correctly qualifies as a first-time Spinr rider.

## 8. Rollback plan

Pure filter addition, no schema/migration/data change, no feature flag needed. To roll back:
revert the single commit (`git revert <sha>`) — this restores the previous (unfiltered) filter
dicts exactly as they compiled before. Since no money, wallet, or ride-state was touched and
no live promo exercises these rules today, a plain code revert is a complete and sufficient
rollback with no data-level remediation required.

## 9. Verification performed

- [x] Automated tests run — unit only (mocked Supabase via patched `db_supabase.count_documents`
      / `get_rows`, per repo convention; no real DB). See exact commands and results below.
- [ ] Manual repro steps followed in staging — not performed; no staging environment access in
      this session.
- [x] Blast-radius grep performed — grepped the whole `backend/` tree for
      `count_documents("rides"` and for every caller of `_validate_promo_for_user`,
      `list_available_promos`, `apply_promo_for_admin`, `first_ride_only`, `min_total_rides`,
      `max_total_rides` (see Section 4 for the full list of files found and why each is
      unaffected or automatically inherits the fix).
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — reused the established
      `EXCLUDE_LEGACY_RIDES` query-filter pattern verbatim (per "Query filters — the layer owns
      escaping, callers pass raw input" and the dual-import convention); did not invent a new
      filter shape.
- [x] Feature-flagged if user-visible and non-trivial, or justified why not — not flagged: this
      is a pure eligibility-count correction with zero live impact today (no active promo uses
      these 3 rules), so there is no user-visible behavior to stage/canary yet; the "flag it"
      gate applies to changes with live-observable effect, which this does not have until a
      future promo campaign turns these rules on.

Commands run (all from `backend/`):
```
ruff check routes/promotions.py tests/test_routes_promotions_coverage.py
# -> All checks passed!

python -m pytest tests/test_routes_promotions_coverage.py -q
# -> 70 passed, 1 warning in 50.46s
#    (the run's overall "FAIL Required test coverage of 60%" line is the
#    repo-wide --cov-fail-under gate evaluated against this single file in
#    isolation — expected and unrelated to this change; see pytest.ini)

python -m pytest tests/test_routes_promotions_coverage.py -q --no-cov -k "LegacyRides" -v
# -> 8 passed, 62 deselected  (the new regression tests, isolated)

python -m pytest tests/test_promotions_coverage.py tests/test_admin_promotions_crud.py \
  tests/test_promo_discount_parity.py tests/test_promo_rate_limit.py \
  tests/test_p2_promo_wallet_loyalty.py tests/test_p3_promo_concurrency.py \
  tests/test_promo_per_user_race.py --no-cov -q
# -> 121 passed  (every other existing promo test file, unaffected)

python -m pytest tests/test_admin_rides_read_endpoints_coverage.py \
  tests/test_admin_rides_coverage.py tests/test_create_ride_post_insert_branches.py --no-cov -q
# -> 141 passed  (other callers of _validate_promo_for_user / apply_promo_for_admin
#    found via grep — admin Create Ride promo preview/apply, rider booking flow)
```

This is a Python-only backend change — no `admin-dashboard`/`rider-app`/`driver-app` build applies.

## 10. What was NOT verified

- Not tested against a real Supabase instance or PostgREST — only against the repo's
  `mock`/hand-rolled fake `count_documents` fixtures in the test file, which simulate the
  `EXCLUDE_LEGACY_RIDES` `{"$eq": {}}` semantics in Python rather than exercising the real
  PostgREST compilation (`repositories/_base.py::_apply_filters`). The PostgREST compilation
  path itself is already covered by the 6+ other production call sites of `EXCLUDE_LEGACY_RIDES`
  (`routes/admin/drivers.py`, `routes/drivers/earnings.py`, etc.), so this fix only needed to
  prove it *uses* the same constant correctly, not re-verify PostgREST's own `is.eq` compilation.
- Not checked against a live/staging promo campaign with `first_ride_only`/`min_total_rides`
  actually enabled, since none exists in production today — there is nothing live to
  regression-test against.
- No visual/UI change (backend-only), so no visual regression tooling gap applies here.
- Coverage-percentage impact on `routes/promotions.py` was not measured with `--cov` in this
  session (no working `pytest-cov` invocation run); only pass/fail of the new and existing
  tests was confirmed.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`, no data-level remediation
      needed (see Section 8).
- [x] Blast radius is stated, not assumed — see Section 4, including the specific files
      grepped and why each is or isn't affected.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in —
      Section 5 states the change is user-visible-in-principle but currently inert (no live
      promo uses these gates).
