# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch: `claude/a1c-subtier-c-batch-1`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c, Sub-tier C (`routes/promotions.py`) |

## 1. Issue / gap identified

`backend/routes/promotions.py` (promo-code validation, discount calculation,
and application recording — feeds directly into fare discounting) was at
65.85% coverage per the Track 2 full-repo scoping snapshot.

## 2. Root cause

Existing test files (`test_p2_promo_wallet_loyalty.py`,
`test_promo_discount_parity.py`, `test_promo_per_user_race.py`,
`test_promo_rate_limit.py`, plus several ride/AI-tool test files that mock
this module's functions wholesale) covered rules 1-4 of
`_validate_promo_for_user`'s 10-rule engine (expiry, total-usage limit,
per-user limit, minimum fare) and the flat/percentage discount math, but had
no coverage of: rules 5-10 (private-coupon targeting, first-ride-only,
new-user-only, inactive-user targeting, min/max total-ride count, budget
cap), the `free_ride` discount branch, the `ride_id`-driven server-side fare
re-fetch branch (including the ride-not-found 404), the malformed-expiry
`except` swallow branch, `compute_promo_discount`'s edge cases (zero
`ride_portion` on both percentage and flat types), and most of
`list_available_promos` (the `/promo/available` filter/ranking engine) —
service-area resolution success/exception, the ineligible-but-shown min-fare
branch, per-promo processing-exception isolation, and eligible-first/
discount-desc sorting.

Separately, this file also defines a second `admin_router` (`admin_get_promo_codes`
/ `admin_create_promo_code` / `admin_update_promo_code` / `admin_delete_promo_code`)
that is **never mounted in `backend/server.py`** — confirmed via
`grep -n "promotions" backend/server.py`, which shows only `promotions_router`
(this module's `api_router`, the user-facing `/promo/*` endpoints) included;
the live admin promo-code CRUD surface is `routes/admin/promotions.py`
(mounted separately, already covered by `test_admin_promotions_crud.py`).
This module's `admin_router` is dead/unreachable code, which itself explains
part of the original coverage gap.

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_promotions_coverage.py` (41
tests) covering all of the gaps above. The four dead admin CRUD functions
are exercised directly as plain async function calls (not through an HTTP
client, since no route reaches them) purely to close coverage on otherwise
untested code — flagged here as a finding, not fixed, per task scope
("test-only, no application code changes; note dead/unreachable code found,
don't remove it without confirming with the user first").

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New test file only; zero application code
  touched.
- **Money-adjacent**: promo discounts subtract from what a rider is charged
  and (via `corporate` account-paid rides, though promos are consumer-only
  per the fare service) never apply to corporate rides. Every new test
  mocks `db_supabase` at the same seam existing promo tests use
  (`backend.routes.promotions.db_supabase.*`), so no test performs a real
  DB write. Decimal-only math is preserved — no float introduced in any new
  test's discount assertions.
- **Other consumers of the functions under test**, per
  `grep -rn "routes.promotions\." backend --include=*.py | grep -v tests/`:
  `routes/admin/rides.py` calls `apply_promo_for_admin` and
  `_validate_promo_for_user` (admin "Create Ride on behalf of rider" flow);
  `routes/ai_tools.py` / the AI booking assistant calls
  `list_available_promos` and `compute_promo_discount` for quoting parity.
  None of these call sites are modified; the new tests pin the exact
  behavior these callers already depend on (e.g. `apply_promo_for_admin`'s
  return shape) rather than changing it.
- **Dead-code finding (not fixed)**: this module's `admin_router` (4
  functions, ~90 lines) is unreachable via HTTP. Not removed in this PR —
  test-only scope; flagging for a separate cleanup decision since deleting
  it is a behavior-non-change but still needs its own review (confirm
  nothing dynamically re-mounts it, e.g. via a plugin/extension loader,
  before removal).

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_promotions_coverage.py` | New file — 41 tests | Close coverage gap on `routes/promotions.py` (65.85% → 93%) |
| `docs/change-log/2026-08-02-a1c-promotions-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (payments-adjacent: promo discounts) |
| `ACTION_ITEMS.md` | Sub-tier C section | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing diff.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_promotions_coverage.py -q --no-cov` — 41 passed.
- [x] Coverage measured: `pytest tests/test_promotions_coverage.py tests/test_p2_promo_wallet_loyalty.py tests/test_promo_discount_parity.py tests/test_promo_per_user_race.py tests/test_promo_rate_limit.py tests/test_ai_tools_booking.py tests/test_create_ride_post_insert_branches.py tests/test_admin_rides_coverage.py tests/test_admin_rides_read_endpoints_coverage.py tests/test_p3_promo_concurrency.py --cov=routes.promotions --cov-report=term-missing` — **routes/promotions.py: 93%** (328 stmts, 24 missing — the dual-import fallback block at the top of the file and a handful of defensive branches in `list_available_promos`'s per-promo exception path/logging lines). 321 passed, 0 failed, 0 collisions with the pre-existing promo test files run alongside it.
- [x] Full backend suite run: `pytest tests/ -q --no-cov` — `8456 passed, 8 skipped, 1 xfailed, 0 failed` (was 8415 per the prior `payment_retry.py` checkpoint entry — this run adds the 41 new tests). No regressions; the one pre-existing documented flaky test (`test_two_drivers_accepting_same_ride_one_wins`) was not hit as a failure this run.
- [ ] Manual repro / staging check — not applicable, test-only change with no deployable behavior difference.
- [x] Blast-radius grep performed: `grep -rn "routes.promotions\." backend --include=*.py | grep -v tests/` — callers are `routes/admin/rides.py` and `routes/ai_tools.py`; both listed above, neither modified.
- [x] Reviewed against CLAUDE.md conventions: confirmed money arithmetic in every new discount assertion uses `Decimal`, never float; confirmed the "never silently swallow" convention isn't violated by the existing `except Exception` in `list_available_promos`'s per-promo loop (it `logger.error`s and skips just that one promo, not the whole request — pinned by `test_error_processing_single_promo_is_skipped_not_fatal`).

## 10. What was NOT verified

- Not run against a real Supabase — every DB call is mocked, matching repo
  convention for this test tier.
- The dead `admin_router`'s functions were verified to work correctly as
  plain Python functions, but their behavior *as HTTP endpoints* (auth
  dependency wiring, request/response serialization) was never verifiable
  in the first place since the router isn't mounted — this is inherent to
  the finding, not a gap in this PR's testing.
- No visual/UI verification — this is a backend-only route module with no
  frontend surface in this diff.
