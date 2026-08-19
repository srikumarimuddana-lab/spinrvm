# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch) |
| Related issue or gap ID | `ACTION_ITEMS.md` A31, "Follow-up (not yet done)" |

## 1. Issue / gap identified

`GET /drivers/balance` (`get_driver_balance`,
`backend/routes/drivers/earnings.py`) computed its `total_rides` response
field from the same `EXCLUDE_LEGACY_RIDES`-filtered `rides` query used for
money totals (`payable_balance`, `total_earnings`, etc.). A driver whose
completed rides in scope are entirely legacy-imported would get
`total_rides: 0` even though they have real ride history.

## 2. Root cause

Identical bug shape to A31's original finding against the sibling endpoint
`GET /drivers/earnings` (fixed 2026-08-13, see
`docs/change-log/2026-08-13-driver-earnings-legacy-activity-stats.md`).
`total_rides` is an activity count, not money — `utils/legacy_rides.py`'s
own docstring says `EXCLUDE_LEGACY_RIDES` "only governs money math" and
imported rides "remain fully visible in ride history." `get_driver_balance`
summed `total_rides = len(rides)` from the legacy-excluded `rides` list
anyway, so the count silently dropped to zero for all-legacy drivers. Left
unfixed on 2026-08-13 (A31's own follow-up note) because the
`total_rides` field in `/balance`'s response had no frontend consumer, so
nothing rendered the wrong number — this fix closes the gap opportunistically/
for consistency, as A31 flagged.

## 3. Fix / remediation

`get_driver_balance` now runs a second, unfiltered "all completed rides"
query (`driver_id` + `status=completed`, no `EXCLUDE_LEGACY_RIDES`) and
sources `total_rides` from it. Every money computation in the function —
`ride_earnings`, `total_tips`, `total_tax`, `total_incentives` (and the
`ride_incentive_claims` lookup keyed off ride IDs), `total_earnings`, and
`payable_balance` — is untouched and continues to read the original,
legacy-excluded `rides` list. This intentionally does **not** match
`get_driver_earnings`'s later A32/A33 change (which fully blended money too
for that endpoint) — A32's own comment in `get_driver_earnings` states
`/balance`'s money math "stays legacy-excluded, still bounds the Stripe
payout Transfer," a deliberate decision this fix does not touch.

`get_driver_balance` doesn't compute `total_distance_km` /
`total_duration_minutes` at all (unlike `get_driver_earnings`), so there is
no equivalent second field to fix — `total_rides` was the only
activity-only stat with this bug in this endpoint.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one endpoint, and effectively invisible.**
  Grepped every consumer of `/drivers/balance`'s response:
  - driver-app: `store/driverStore.ts`'s `DriverBalance` TS interface
    (lines ~264-273) does **not** include a `total_rides` field. The only
    consumer, `app/driver/payout.tsx`, reads `driverBalance.payable_balance`,
    `.total_earnings`, `.previous_app_paid_total`, `.pending_payouts`, and
    `.total_paid_out` — never `.total_rides`. Re-confirmed directly (not
    trusting A31's note blindly) via
    `grep -n "driverBalance\." driver-app/app/driver/payout.tsx` and a full
    `grep -rn "total_rides" driver-app/` sweep — every other `total_rides`
    hit in driver-app is on `earnings`/`earningsByPeriod` state
    (`/drivers/earnings`'s response, a different endpoint, already fixed by
    A31 on 2026-08-13), not `driverBalance`.
  - admin-dashboard: `grep -rn "total_rides" admin-dashboard/` shows many
    hits, but every one traces to other backend endpoints (driver list/
    detail, analytics, heatmap, monitoring panel) — none read
    `/drivers/balance`.
  - **Conclusion: no frontend consumer exists today.** This matches (and
    re-verifies) A31's original claim rather than assuming it.
  - No other backend module imports `get_driver_balance` or reuses its
    query composition.
- One extra `db_supabase.get_rows("rides", ...)` call per `/balance`
  request (same table, same driver, no `EXCLUDE_LEGACY_RIDES` filter,
  `limit=10000` — same shape as the existing calls in this function). Not
  expected to be perf-significant; same pattern already accepted for
  `get_driver_earnings` on 2026-08-13.
- No money computation touched. No ride state, WebSocket path, or Stripe
  Transfer bound (`payable_balance`) is affected in any way — verified by
  keeping the fix scoped to a single new variable feeding only
  `total_rides`.
- No migration, no write path.

## 5. User-experience effect

**None, today.** No frontend surface reads `/drivers/balance`'s
`total_rides` field (see §4), so this change is not visible to any rider,
driver, corporate admin, or internal admin in the current app. The fix is
preventative/for-consistency: it closes the latent-bug gap before any
future frontend surface starts reading this field, so that surface
inherits correct behavior from day one instead of a second all-legacy-driver
bug report.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/earnings.py` | `get_driver_balance`: added a second, unfiltered `all_completed_rides` query; `total_rides` now sources from it instead of the legacy-excluded `rides` list. All money fields unchanged. | Close the A31 follow-up gap: activity counts must not go through the money-only `EXCLUDE_LEGACY_RIDES` filter |
| `backend/tests/test_earnings_coverage.py` | Added `TestGetDriverBalanceLegacyActivityStats` (all-legacy and mixed-legacy-plus-real cases) | Regression coverage for this exact bug, mirroring `TestGetDriverEarningsLegacyActivityStats`'s style |
| `ACTION_ITEMS.md` | A31's "Follow-up" bullet marked `[x]` DONE with fix details | Close out the tracked follow-up |
| `docs/change-log/2026-08-18-a31-driver-balance-legacy-total-rides.md` | This log | Required for any live-tested-surface change per `CLAUDE.md` |

## 7. Before / after

```python
# Before
rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": driver["id"], "status": RideStatus.COMPLETED, **EXCLUDE_LEGACY_RIDES},
    limit=10000,
)
ride_earnings = sum((_ride_income(r) for r in rides), Decimal("0"))
total_tips = sum((_d(r.get("tip_amount") or 0) for r in rides), Decimal("0"))
total_rides = len(rides)
```

```python
# After
rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": driver["id"], "status": RideStatus.COMPLETED, **EXCLUDE_LEGACY_RIDES},
    limit=10000,
)
ride_earnings = sum((_ride_income(r) for r in rides), Decimal("0"))
total_tips = sum((_d(r.get("tip_amount") or 0) for r in rides), Decimal("0"))
# total_rides is activity, not money — separate unfiltered query.
all_completed_rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": driver["id"], "status": RideStatus.COMPLETED},
    limit=10000,
)
total_rides = len(all_completed_rides)
```

`payable_balance`, `total_earnings`, and every other money field's source
expression is byte-for-byte unchanged — only `total_rides`'s source list
moved.

## 8. Rollback plan

Plain `git revert` — no data mutation, no migration, no Stripe/wallet
interaction. `/drivers/balance` is a `GET`-only read path, so a revert
takes effect on the very next request with zero cleanup. No feature flag
needed: the change has zero current user-visible surface (§5), so there is
nothing to "turn off" mid-incident beyond the revert itself.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_earnings_coverage.py backend/tests/test_drivers_extended.py -q --no-cov` — 137/137 pass (2 new)
- [x] `ruff check backend/routes/drivers/earnings.py backend/tests/test_earnings_coverage.py` — clean on the touched lines (one pre-existing, unrelated `F841` at `test_earnings_coverage.py:915`, outside this diff, confirmed via `git diff --stat` showing this change is purely additive)
- [x] `ruff format --check` — both files already formatted
- [x] Blast-radius grep performed (see §4): `driver-app/store/driverStore.ts` `DriverBalance` type, `driver-app/app/driver/payout.tsx` consumer, full `total_rides` sweep across `driver-app/` and `admin-dashboard/` — no consumer of `/drivers/balance`'s `total_rides` found, re-verifying (not assuming) A31's original claim
- [x] Reviewed against relevant CLAUDE.md convention: this is a `GET`-only bugfix restoring the intended contract of `utils/legacy_rides.py` ("only governs money math"), consistent with the A31 fix already accepted for the sibling endpoint
- [ ] Manual repro / staging check — not available in this session; reasoned from the query code and mirrored the already-verified A31 fix pattern instead
- [ ] Feature-flagged — not applicable; no current user-visible surface reads this field (§5), and this is a pure backend bugfix restoring an already-documented contract, not a new feature

## 10. What was NOT verified

- Not exercised against a real/staging Supabase instance — only the
  `mock_supabase_client`-equivalent `AsyncMock(side_effect=...)` pattern
  already used throughout `test_earnings_coverage.py`.
- No visual/browser verification was needed or performed — this is a
  backend-only, `GET`-only change with no frontend file touched (confirmed
  no consumer exists, so no frontend change was made per the task's scope).
- Did not re-audit every other caller of `EXCLUDE_LEGACY_RIDES` across the
  codebase for the same latent pattern beyond the two already known
  (`get_driver_earnings`, fixed 2026-08-13; `get_driver_balance`, fixed
  here) — `utils/driver_statement.py` is referenced in existing comments as
  having its own unfiltered query already and was not re-examined in this
  session.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data touched)
- [x] Blast radius is stated: isolated to `GET /drivers/balance`, zero
      current frontend consumers of the changed field (re-verified, not
      assumed)
- [x] No silent behavior change to an already-shipped flow — nothing
      renders this field today, so there is no shipped UX to change; this
      is documented explicitly in §5 rather than left to silence
