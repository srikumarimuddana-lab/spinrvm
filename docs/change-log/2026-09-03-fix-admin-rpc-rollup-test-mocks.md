# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude (fixing GitHub issue #4885) |
| Surface(s) | backend (test suite only — no production code path changed except one, see §3) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `fix-admin-rpc-rollup-test-mocks` |
| Related issue or gap ID | Fixes #4885 |

## 1. Issue / gap identified

`backend-test` on `main` has ~26 failing tests across 9 admin business-logic/
stats test files (`test_admin_business_logic.py`, `test_admin_drivers_coverage.py`,
`test_admin_maintenance_coverage.py`, `test_admin_messaging_coverage.py`,
`test_admin_promo_stats.py`, `test_admin_rides_coverage.py`,
`test_admin_subscriptions_coverage.py`, `test_admin_support_routes.py`,
`test_email_deliverability.py`). Issue #4885 attributed this to PR #4875 —
**that attribution is wrong.**

## 2. Root cause

Across a separate series of PRs, ~14 `routes/admin/*.py` endpoints (subscription
stats, driver earnings/payouts, referral leaderboards/analytics, audit-log top
actors, dispute stats, cloud-message stats, promo stats, payout-period close,
email deliverability) were refactored to call server-side Postgres RPC
"rollup" functions (`admin_subscription_stats_rollup` — migration 385,
`admin_driver_earnings_rollup`/`admin_driver_ride_summary`/
`admin_driver_bonus_summary`/`admin_daily_ride_stats` — migrations 370/388/389/390,
`admin_driver_referral_board`/`admin_referred_user_count` — migrations 386/387,
`admin_audit_actor_stats` — migration 394, `admin_dispute_stats_rollup` —
migration 380, `admin_cloud_message_stats_rollup` — migration 381,
`admin_promo_stats` — migration 393, `admin_payout_period_snapshot` —
migration 391, `admin_email_log_stats` — migration 392) instead of fetching
raw rows via `db_supabase.get_rows(...)` and aggregating in Python. This was
deliberate perf work (the migration files' own comments cite eliminating
5k–20k-row Python-side fetch+loop patterns).

The corresponding tests were never updated: they still mock
`db_supabase.get_rows` with rows shaped for the old Python-aggregation code
path. The RPC call (unmocked) hits the `mock_supabase_client` fixture's
generic default (`res.data = None`), so the endpoint computes 0/empty
results and the tests' assertions on real numbers fail.

One test file (`test_admin_drivers_coverage.py`'s `TestRiderReferralLeaderboard`
and `TestReferralAnalytics` funnel test) had a second, unrelated cause:
PR #4875 ("eliminate N+1 query pattern in 3 admin/background driver-payout
paths") batched a per-referee `count_documents` call into one
`get_rows("rides", ...)` call, and the test fixture's `_rows` side_effect
never added a "rides" branch, so the batched query silently returned `[]`
via the fixture's default `return []`. This one **is** attributable to
PR #4875, but it's a fixture-completeness gap, not the RPC-rollup pattern
the issue's title describes, and it's one of 26, not the whole cluster.

While fixing `test_admin_support_routes.py`'s dispute-stats tests, a **real,
separate app bug** was found (not a stale-test issue): `admin_get_dispute_stats`
(`routes/admin/support.py`) computes `total_refunded` as
`float(Decimal(str(row.get("total_refunded") or 0)))` with no rounding at
all — the RPC refactor silently dropped the `ROUND_HALF_UP` quantize the
Python-loop version had (per the pinned regression test's own N15 comment).
Fixed in the same PR (§3) since it is a one-line, low-risk fix directly in
the code this PR is already reviewing test coverage for.

## 3. Fix / remediation

- Updated the mocks in all 26 failing tests to patch `db_supabase.rpc` (or
  `<module>.db_supabase.rpc`, matching each file's existing patch-target
  convention) with a return value shaped per the real RPC's migration SQL
  (`jsonb_build_object` keys), instead of `db_supabase.get_rows`. Assertions
  and expected numeric values were **not** weakened — the same expected
  outputs (`19.99`, `50.0`, `10.5`, etc.) are now driven by an RPC mock
  instead of a `get_rows` mock.
- `test_admin_maintenance_coverage.py::TestAuditLogTopActors::
  test_respects_limit_and_flags_row_cap` and `test_days_window_passed_to_query`
  had assertions based on a **stale assumption**: the old Python path
  capped its `get_rows` fetch at 5000 audit_logs rows and set
  `rows_scanned_capped` when it hit that ceiling; the RPC aggregates
  server-side with no such fetch cap, so `routes/admin/maintenance.py` now
  hardcodes `rows_scanned_capped: False` unconditionally. The first test's
  assertion (`rows_scanned_capped is True`) no longer matches any reachable
  behavior and was corrected to `False`, with an explicit comment stating
  why. The second test asserted on `get_rows("audit_logs", ...)` call args
  that no longer exist (the call moved into SQL) — rewritten to assert on
  the RPC call args (`p_since`, `p_limit`) instead.
- Two tests (`test_admin_drivers_coverage.py`'s rider-referral-leaderboard
  and referral-analytics-funnel tests) needed a `"rides"` branch added to
  their `get_rows` fixture (PR #4875's batching change, not an RPC issue —
  see §2).
- One genuine app-code fix: `routes/admin/support.py`'s
  `admin_get_dispute_stats` now quantizes `total_refunded` to 2dp with
  `ROUND_HALF_UP` (restoring the money-rounding convention this codebase
  requires everywhere else — see CLAUDE.md "Money arithmetic").
- Also fixed 3 tests that were **passing for the wrong reason** (asserting
  a zero/empty result that an unmocked, defaulting RPC also happens to
  produce) for consistency with the rest of the cluster and to give them
  real coverage: `test_admin_subscriptions_coverage.py::TestSubscriptionStats::
  test_service_area_filter_excludes_non_matching_driver`,
  `test_admin_messaging_coverage.py::test_get_cloud_message_stats_no_recipients_zero_rate`,
  `test_admin_support_routes.py::test_dispute_stats_table_missing`
  (this last one kept its `get_rows`→exception-fallback shape but was
  re-pointed at `db_supabase.rpc` since that's the call the `try/except`
  in the route actually wraps now).

## 4. Risk & impact on existing functionality

- **Test-only change, isolated, for 25 of 26 tests.** No production code
  path is touched by those; the only risk is a test asserting something
  that is no longer true of the real endpoint (guarded against by reading
  each RPC's migration SQL for its actual output shape before writing the
  mock — not guessed).
- **One production code change** (`routes/admin/support.py`,
  `admin_get_dispute_stats`): adds a `.quantize(Decimal("0.01"),
  rounding=ROUND_HALF_UP)` to the `total_refunded` computation. Blast
  radius: this function is called only by `GET /api/admin/disputes/stats`
  (grepped — no other caller in `routes/` or `services/`). It changes the
  admin disputes-stats card's refund total from an unrounded float
  (e.g. `10.125`) to a correctly rounded one (`10.13`) whenever a dispute's
  `refund_amount` lands on a sub-cent boundary — a strictly more correct
  display value, and additive in the sense that it only affects a display
  figure on an internal admin dashboard card, not anything written back to
  the database or any money movement.
- Nothing in `backend/core/lifespan.py`'s background loops, the ride state
  machine, or `corporate_wallet_apply_delta`/wallet deltas is touched.

## 5. User-experience effect

- Internal-admin-facing only. The `admin_get_dispute_stats` rounding fix is
  visible on the admin dashboard's Disputes stats card (`total_refunded`
  now always shows exactly 2 decimal places rounded half-up, matching every
  other money figure in the admin dashboard) — not visible to riders,
  drivers, or corporate admins, and not a mid-session change (it's a
  page-load stat, not a live-updating one).
- No rider/driver/corporate-app change of any kind.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_business_logic.py` | 2 tests: mock `db_supabase.rpc` for `admin_subscription_stats_rollup` | RPC-shape mismatch |
| `backend/tests/test_admin_drivers_coverage.py` | 10 tests: mock `db_supabase.rpc` for `admin_driver_earnings_rollup`/`admin_daily_ride_stats`/`admin_driver_ride_summary`/`admin_driver_bonus_summary`/`admin_driver_referral_board`/`admin_referred_user_count`; 2 tests: add missing `"rides"` fixture branch | RPC-shape mismatch (8 tests) + PR #4875 fixture gap (2 tests) |
| `backend/tests/test_admin_maintenance_coverage.py` | 5 tests: mock `db_supabase.rpc` for `admin_audit_actor_stats`; corrected 2 stale assertions | RPC-shape mismatch + stale `rows_scanned_capped`/`get_rows` assumptions |
| `backend/tests/test_admin_messaging_coverage.py` | 2 tests: mock `db_supabase.rpc` for `admin_cloud_message_stats_rollup` | RPC-shape mismatch (1 failing + 1 accidentally-passing) |
| `backend/tests/test_admin_promo_stats.py` | 2 tests: mock `db_supabase.rpc` for `admin_promo_stats` | RPC-shape mismatch |
| `backend/tests/test_admin_rides_coverage.py` | 1 test: mock `db_supabase.rpc` for `admin_payout_period_snapshot` | RPC-shape mismatch |
| `backend/tests/test_admin_subscriptions_coverage.py` | 3 tests: mock `db_supabase.rpc` for `admin_subscription_stats_rollup` | RPC-shape mismatch (2 failing + 1 accidentally-passing) |
| `backend/tests/test_admin_support_routes.py` | 3 tests: mock `db_supabase.rpc` for `admin_dispute_stats_rollup` | RPC-shape mismatch (2 failing + 1 accidentally-passing) |
| `backend/tests/test_email_deliverability.py` | 1 test: mock `db_supabase.rpc` for `admin_email_log_stats`, narrowed `get_rows` mock to the two remaining recent-row queries | RPC-shape mismatch |
| `backend/routes/admin/support.py` | `admin_get_dispute_stats` now quantizes `total_refunded` to `ROUND_HALF_UP` 2dp | Real app bug found while fixing the test: RPC refactor silently dropped the money-rounding the pinned N15 regression test requires |

## 7. Before / after

```python
# Before (routes/admin/support.py, admin_get_dispute_stats)
"total_refunded": float(Decimal(str(row.get("total_refunded") or 0))),
```

```python
# After
"total_refunded": float(
    Decimal(str(row.get("total_refunded") or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
),
```

Test-mock shape change (representative example, `test_admin_support_routes.py`):

```python
# Before — mocks the pre-refactor Python-aggregation code path, no longer read
monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(return_value=rows))

# After — mocks the actual admin_dispute_stats_rollup RPC call
rollup = {"open": 1, "under_review": 0, "resolved": 1, "rejected": 0, "total_refunded": "10.50"}
monkeypatch.setattr(m.db_supabase, "rpc", AsyncMock(return_value=rollup))
```

## 8. Rollback plan

`git revert` is sufficient and complete — this touches only test files (a
pure `git revert` restores the prior, currently-broken test state) plus one
additive quantize in `admin_get_dispute_stats` that reads no new data and
writes nothing; reverting it simply restores the unrounded (but not
incorrect-direction, just imprecise) float. No migration, no feature flag,
no data written that needs remediation.

## 9. Verification performed

- [x] `pytest tests/test_admin_business_logic.py tests/test_admin_drivers_coverage.py tests/test_admin_maintenance_coverage.py tests/test_admin_messaging_coverage.py tests/test_admin_promo_stats.py tests/test_admin_rides_coverage.py tests/test_admin_subscriptions_coverage.py tests/test_admin_support_routes.py tests/test_email_deliverability.py --no-cov -q` → 475 passed, 0 failed (was 449 passed / 26 failed)
- [x] Full suite `pytest -m "not slow" --no-cov -q` run before and after (see PR body for exact counts)
- [x] Each RPC mock's shape was checked against the real RPC's migration SQL (`backend/migrations/*_fn.sql`) `jsonb_build_object` keys, not guessed
- [x] Blast-radius grep performed for the one production-code change: `grep -rn "admin_get_dispute_stats"` in `backend/` — only the route decorator registers it, no other Python caller
- [ ] Not run against a live Supabase / staging environment — this is a mocked-unit-test-only fix plus one Decimal-rounding change verified by the existing regression test's Decimal math, not a staging click-through
- [ ] No `npm run build` applicable — backend-only change, no frontend surface touched

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (test files: isolated by construction; `support.py`: single call site, grepped)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the one visible, admin-only, page-load-only change)
