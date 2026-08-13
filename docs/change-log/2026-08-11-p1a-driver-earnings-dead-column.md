# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend, admin-dashboard (data only, no frontend file changed) |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | P1-A, `docs/audit/2026-08-11-driver-rider-migration-audit.md`; also Phase 3 cross-surface finding #2 |

## 1. Issue / gap identified

`GET /admin/drivers/stats` (fleet-wide dashboard) reads `drivers.total_earnings` for both the top-level "Total Earnings" stat card and the per-service-area breakdown. That column is never written by any code path in the codebase — `admin_get_driver_live_stats`'s own docstring already documents this ("is never written by any code path"). Net effect: the fleet-wide earnings stat and every per-area earnings figure always read `$0`.

Separately, while fixing this, found `admin_get_driver_live_stats` (the per-driver detail slideout's "Earnings" header) — the one endpoint that *does* compute earnings live rather than reading the dead column — has no legacy-ride exclusion. The audit's Phase 3 cross-surface table (finding #2) already flagged this: the same driver's "Earnings" header and "Payouts" tab (which does exclude legacy rides) can show two different numbers on the same screen.

## 2. Root cause

`drivers.total_earnings` was presumably intended to be a denormalized rollup column, but no write path was ever implemented for it (confirmed by grep across `db_supabase.py`, `repositories/*.py`, and every migration). The per-driver live-stats endpoint was already fixed to compute live instead of relying on it, but that fix's own legacy-ride exclusion was never applied — likely because it predates the P0-B/A26 legacy-exclusion work done earlier this session, or was simply missed when the driver-facing exclusion pattern was rolled out everywhere except this endpoint.

## 3. Fix / remediation

- `admin_get_driver_stats`: replaced `d.get("total_earnings")` reads with a live-computed sum. One batched query (`driver_id IN (...)`, `status='completed'`, `**EXCLUDE_LEGACY_RIDES`) across all drivers in scope, avoiding an N+1 per-driver query pattern — bucketed into a `driver_id → Decimal` map used for both the fleet-wide total and the per-area breakdown. Lifetime-scoped (not date-windowed), matching `total_rides_sum`'s existing lifetime semantics.
- Same endpoint's daily-earnings/daily-rides chart: added `**EXCLUDE_LEGACY_RIDES` to the existing `ride_filters` so a historical bulk import doesn't appear as a spike in "new" fleet activity on whatever day it happened to be imported.
- `admin_get_driver_live_stats`: `total_earnings` now sums only `drop_legacy_rides(completed)` instead of all completed rides. `total_assigned`/`completed_count`/`acceptance_rate`/`cancelled_by_driver` are deliberately left unfiltered — legacy-imported rides are always `status='completed'` (booking_import_service only imports completed bookings), so excluding them from those other metrics would change the acceptance-rate denominator in a way neither the audit nor this fix asked for.
- New tests: `test_admin_drivers_coverage.py` gained a fleet-stats test proving `total_earnings` ignores a deliberately-poisoned dead-column value and computes from `rides` instead (with legacy exclusion asserted on the filter dict), and a live-stats test proving a legacy-imported ride is excluded from the earnings sum but still correctly counted toward `total_assigned`.

## 4. Risk & impact on existing functionality

- Blast radius: `admin_get_driver_stats` (`/admin/drivers/stats`) and `admin_get_driver_live_stats` (`/admin/drivers/{id}/live-stats`) — both admin-only, both already grepped for every other reader of `drivers.total_earnings` (none — the column has no other consumer to break).
- `total_rides`/`total_rides_sum` are untouched (that column *is* correctly maintained, per `repositories/driver_repo.py`'s increment-on-completion path) — only the earnings figures change.
- Both fleet-wide and per-driver earnings numbers will **increase** from their previous `$0`/legacy-inflated states to their correct values — a display correction, not a ledger/balance change; no money moves.
- The new batched query in `admin_get_driver_stats` is bounded (`limit=50000`) and runs once per request, not once per driver — reviewed against CLAUDE.md's N+1 anti-pattern warning explicitly.

## 5. User-experience effect

Internal-admin-facing only. Two dashboard surfaces (fleet stats page, per-driver detail slideout) show corrected (non-zero, and — for the slideout — legacy-excluded) earnings figures. Not mid-session-relevant in the ride/payment sense — these are admin reporting views, not something a rider or driver sees.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | `admin_get_driver_stats`: live-computed, legacy-excluded, batched earnings query replacing the dead-column reads; daily chart gains legacy exclusion. `admin_get_driver_live_stats`: earnings sum now excludes legacy rides via `drop_legacy_rides()`. | Fix the dead-column stat cards (P1-A) and the Earnings-vs-Payouts-tab mismatch (Phase 3 #2) |
| `backend/tests/test_admin_drivers_coverage.py` | 2 new tests: fleet-stats dead-column-ignored + legacy-excluded; live-stats legacy-excluded-from-earnings-only | Regression coverage |

## 7. Before / after

```python
# Before (admin_get_driver_stats)
total_earnings_sum = float(sum(Decimal(str(d.get("total_earnings") or 0)) for d in enriched_drivers))
# drivers.total_earnings is never written anywhere -> always sums to 0
```

```python
# After
earnings_rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": {"$in": list(driver_ids_set)}, "status": "completed", **EXCLUDE_LEGACY_RIDES},
    limit=50000,
)
for r in earnings_rides:
    earnings_by_driver[r["driver_id"]] += Decimal(str(r.get("driver_earnings") or 0))
total_earnings_sum = float(sum(earnings_by_driver.values(), Decimal("0")))
```

## 8. Rollback plan

Pure code change, no migration, no data mutation. `git revert` restores the dead-column read (i.e., the pre-existing `$0` display bug) — no cleanup needed since this only changes what a read-only endpoint returns.

## 9. Verification performed

- [x] Full existing test suites for both endpoints pass unchanged (198 tests across `test_admin_drivers_coverage.py`, `test_admin_extended.py`, `test_admin_security.py`)
- [x] 2 new tests added and passing, proving both the dead-column bypass and the legacy exclusion
- [x] Blast-radius grep: `drivers.total_earnings` has no other reader; `EXCLUDE_LEGACY_RIDES`/`drop_legacy_rides` are the same, now-fixed (A26) helpers already used consistently elsewhere

## What was NOT verified

- Not tested against live production data end-to-end (no HTTP call against a running server) — verified via unit tests against the actual endpoint handlers with mocked `get_rows`.
- No visual regression tooling exists for admin-dashboard; the stat-card values changing from `$0` to a real number has no UI file change to screenshot (the frontend already renders whatever number the API returns).
- Did not investigate why `drivers.total_earnings` was never wired up in the first place, or whether a background rollup job was ever intended — out of scope for this fix.
