# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety (insurance-period classification) |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | Item #1 of the stage 7/8 (insurance-period / fare-billing) GPS-to-billing audit, 2026-09-02 |

## 1. Issue / gap identified

Two places that compute "when does this ride's Period-2 window start" checked `driver_accepted_at` before `assigned_at`, instead of the other way around. Per CLAUDE.md, Period 2 (TNC primary commercial coverage) starts at `driver_assigned` — the instant a driver is matched/offered the ride — not at `driver_accepted`, when they tap Accept. Checking `driver_accepted_at` first means any point captured between assignment and acceptance is misclassified as being outside the Period-2 window.

## 2. Root cause

`persist_idle_location_batch` (`backend/utils/breadcrumbs.py`) and `_route_window_points` (`backend/utils/route_finalizer.py`) were each written with the fallback chain `driver_accepted_at or assigned_at`, the reverse of the precedence already established (and correctly used) in three other call sites that write the same `driver_insurance_periods`-adjacent data: `ride_settlement.py:228`, `routes/drivers/ride_complete.py:709` (whose own comment explicitly documents this exact ordering fix from an earlier bug), and `scripts/backfill_period_distances.py:110`. These two files were evidently written independently of that established pattern and never brought in line with it.

## 3. Fix / remediation

Swapped the fallback order in both functions to `assigned_at or driver_accepted_at or <original last fallback>`, matching the reference implementations. No other logic changed.

- `breadcrumbs.py`'s `persist_idle_location_batch`: `ride_window_start` (used to reject idle-batch points that fall inside an active ride's window, so they aren't double-counted as Period-1 deadhead) now keys off `assigned_at` first.
- `route_finalizer.py`'s `_route_window_points`: the widened P2 finalization window (`include_pickup_leg=True`, gated by `settings.p2_route_geometry_enabled`) now starts at `assigned_at` first, so pickup-leg GPS points captured between assignment and acceptance are correctly included as Period-2 route evidence instead of being dropped.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped both functions for every caller: `persist_idle_location_batch` has exactly one call site (`routes/drivers/location.py:193`); `_route_window_points` has exactly two call sites, both internal to `route_finalizer.py` itself (the default P3-only path at line 123, and the P2-widened path at line 910). No other module reads these functions or duplicates their fallback logic.
- **Widens, never narrows,** the set of points treated as "inside the ride window" — `assigned_at` is always the same time or earlier than `driver_accepted_at`, so the window only gets earlier/wider, not later/narrower. For `persist_idle_location_batch` this means a few more idle-batch points (captured in the assignment→acceptance gap) will now correctly reject as `ride_active` instead of being persisted as Period-1 idle rows. For `_route_window_points` it means a few more early pickup-leg GPS points are now included as Period-2 route evidence instead of silently dropped.
- No schema change, no new column, no write to `driver_insurance_periods` itself — this only fixes which time window two read-side helpers use when deciding what a point belongs to. The actual period-transition audit rows (written elsewhere, already using the correct precedence) are unaffected.
- Does not touch fare/billing calculation, Decimal money paths, or any Stripe flow.

## 5. User-experience effect

- **Backend-only.** No rider- or driver-facing UI/copy change. The only observable effect is slightly more accurate Period-2 route-geometry data on ride detail views that render the pickup leg (when `p2_route_geometry_enabled` is on) and slightly stricter idle-batch classification for the same few-second assignment→acceptance window. Not visible mid-session as any behavior change a driver or rider would notice.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/breadcrumbs.py` | `persist_idle_location_batch`'s `ride_window_start` fallback reordered to check `assigned_at` before `driver_accepted_at` | Period 2 starts at assignment, not acceptance (CLAUDE.md) |
| `backend/utils/route_finalizer.py` | `_route_window_points`'s widened-window `window_start` fallback reordered the same way | Same precedence bug, same fix |
| `backend/tests/test_idle_location_batch.py` | New regression test: a point between `assigned_at` and `driver_accepted_at` now rejects as `ride_active` | Prevents this precedence bug from regressing |
| `backend/tests/test_route_finalizer.py` | New direct unit test for `_route_window_points` covering the same between-assigned-and-accepted case | Same, for the finalizer's window function |

## 7. Before / after

```python
# Before (breadcrumbs.py)
ride_window_start = (
    parse_iso_utc(active_ride.get("driver_accepted_at"))
    or parse_iso_utc(active_ride.get("assigned_at"))
    or parse_iso_utc(active_ride.get("created_at"))
)
```
```python
# After
ride_window_start = (
    parse_iso_utc(active_ride.get("assigned_at"))
    or parse_iso_utc(active_ride.get("driver_accepted_at"))
    or parse_iso_utc(active_ride.get("created_at"))
)
```

```python
# Before (route_finalizer.py)
window_start = (
    parse_iso_utc(ride.get("driver_accepted_at")) or parse_iso_utc(ride.get("assigned_at")) or window_start
)
```
```python
# After
window_start = (
    parse_iso_utc(ride.get("assigned_at")) or parse_iso_utc(ride.get("driver_accepted_at")) or window_start
)
```

## 8. Rollback plan

`git revert` is sufficient here — this is a pure read-side window-computation fix with no data written under the old (wrong) precedence that needs remediation. No feature flag exists or is needed: the fix only changes which of two already-populated ride timestamp columns is consulted first, both of which always existed on the row.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_idle_location_batch.py backend/tests/test_route_finalizer.py -q --no-cov` — **26 passed** (24 pre-existing + 2 new regression tests), including the new assigned-at-precedence cases.
- [x] `ruff check` on all 4 changed files — clean, 0 errors.
- [x] Blast-radius grep performed — confirmed 1 caller of `persist_idle_location_batch`, 2 (internal) callers of `_route_window_points`; both listed above.
- [x] Reviewed against CLAUDE.md's insurance-period convention (Period 2 starts at `driver_assigned`, not `driver_accepted`) — this fix directly restores that documented precedence, matching the three other call sites that already implement it correctly.
- [ ] Not feature-flagged — not applicable; this is a bugfix restoring already-documented, already-elsewhere-correct behavior, not new user-visible functionality. No `app_settings` flag exists for "which ride timestamp to prefer."

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius is stated, not assumed (2 call sites total, both grepped and confirmed).
- [x] No silent behavior change to an already-shipped rider/driver-facing flow — this is backend-only and corrects a regulatory-audit-trail/route-geometry classification bug, not a UX change.
