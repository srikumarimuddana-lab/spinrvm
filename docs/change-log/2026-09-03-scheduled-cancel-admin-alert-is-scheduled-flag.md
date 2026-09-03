# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "check the settings page and monitoring for other bugs too" |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Same monitoring audit as the other fixes in this batch |

## 1. Issue / gap identified

The monitoring dashboard's cancellation alert feed is meant to flag *any* cancellation of a scheduled ride for ops follow-up (the rider planned around a specific pickup time and has less slack to re-hail — Finding #12 from the scheduled-rides gap review, already implemented for the driver-cancel path). It only actually worked for driver-initiated cancellations:

- **Rider cancelling an already-dispatched scheduled ride** sent the alert-triggering `ride_cancelled` event to admins, but without the `is_scheduled` field — the alert always read as a generic "Ride cancelled".
- **Rider cancelling a scheduled ride *before* it dispatched** never sent the alert-triggering event at all — only a `ride_status_changed` event, which the monitoring page's alert-feed switch statement doesn't handle for this purpose.
- **Admin cancelling a scheduled ride from the dashboard** had the same missing-field gap as the rider path.

## 2. Root cause

`backend/routes/drivers/ride_cancel.py`'s driver-cancel path computes `_was_scheduled = bool((ride or {}).get("is_scheduled"))` and includes it on its `broadcast_to_admins({"type": "ride_cancelled", ...})` call — the only one of the three cancellation entry points that was ever wired up this way. The rider-cancel path (`routes/rides/cancellation.py`) and the admin-cancel path (`routes/admin/rides.py`) both predate or were never updated for this feature, and the rider path's pre-dispatch branch additionally relies solely on `broadcast_ride_status()` (which sends a *different* event type, `ride_status_changed`, to admins) rather than the explicit `ride_cancelled` event the frontend's alert case actually listens for.

## 3. Fix / remediation

- `routes/rides/cancellation.py`'s `cancel_ride_rider()` (dispatched-ride path): added `"is_scheduled": bool((ride or {}).get("is_scheduled"))` to its existing `broadcast_to_admins({"type": "ride_cancelled", ...})` call.
- Same file's pre-dispatch scheduled-cancel branch: added a new, explicit `broadcast_to_admins({"type": "ride_cancelled", ..., "is_scheduled": True})` call alongside the existing `broadcast_ride_status()` call — `is_scheduled` is unconditionally `True` here since this branch's own DB read filters on `{"is_scheduled": True}`.
- `routes/admin/rides.py`'s `admin_cancel_ride()`: added the same `is_scheduled` field to its `broadcast_to_admins` call.

## 4. Risk & impact on existing functionality

- **Blast radius**: 2 files, 3 call sites, each an additive field (or, for the pre-dispatch branch, one additional best-effort WS broadcast wrapped in its own try/except matching the existing pattern) on an already-existing admin-only WebSocket message. No change to any rider/driver-facing event, HTTP response, or DB write.
- The new pre-dispatch broadcast is wrapped in the same `try/except ... logger.warning` pattern the sibling call sites already use — a failure here cannot undo the cancellation itself (already committed to the DB before this point) or affect the rider/driver's own experience.
- Grepped for other consumers of the `ride_cancelled` WS event type — only the monitoring dashboard's alert feed (`admin-dashboard/src/app/dashboard/monitoring/page.tsx`) reads `is_scheduled` from it; no other frontend code branches on this field.

## 5. User-experience effect

Admin-facing only. An admin watching Live Ride Monitoring now sees "⚠ Scheduled ride cancelled — rider needs follow-up" for every scheduled-ride cancellation regardless of who cancelled it or whether it had dispatched yet, instead of only for driver-initiated cancellations of already-dispatched rides.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/cancellation.py` | Added `is_scheduled` to the dispatched-path admin broadcast; added a new admin broadcast entirely to the pre-dispatch scheduled-cancel branch | Rider-cancelled scheduled rides weren't flagged for admin follow-up (dispatched: missing field; pre-dispatch: missing event entirely) |
| `backend/routes/admin/rides.py` | Added `is_scheduled` to `admin_cancel_ride`'s admin broadcast | Admin-cancelled scheduled rides weren't flagged for follow-up either |

## 7. Before / after

```python
# Before — rider cancel (dispatched path), no is_scheduled
await _deps.manager.broadcast_to_admins(
    {"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"}
)

# After
await _deps.manager.broadcast_to_admins({
    "type": "ride_cancelled",
    "ride_id": ride_id,
    "reason": "rider_cancelled",
    "is_scheduled": bool((ride or {}).get("is_scheduled")),
})
```

```python
# Before — rider cancel, pre-dispatch: only broadcast_ride_status (different
# event type; admin alert feed never fires for this branch at all)
await _deps.manager.broadcast_ride_status(
    ride_id, RideStatus.CANCELLED, rider_id=current_user["id"],
    reason="rider_cancelled", is_scheduled=True,
)
return {"success": True}

# After — also sends the event type the alert feed actually listens for
await _deps.manager.broadcast_ride_status(...)
try:
    await _deps.manager.broadcast_to_admins({
        "type": "ride_cancelled", "ride_id": ride_id,
        "reason": "rider_cancelled", "is_scheduled": True,
    })
except Exception as _exc:
    logger.warning(f"scheduled rider cancel admin broadcast failed: {_exc}")
return {"success": True}
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no schema change. Both changes only add fields/calls to an existing best-effort admin-only WebSocket notification.

## 9. Verification performed

- [x] Traced all three cancellation entry points (driver, rider, admin) and every `broadcast_to_admins({"type": "ride_cancelled", ...})` / `broadcast_ride_status(...)` call site directly, rather than trusting the audit's line citations alone.
- [x] Confirmed `broadcast_ride_status` sends a *different* WS event type (`ride_status_changed`) to admins, and confirmed via direct read of `admin-dashboard/src/app/dashboard/monitoring/page.tsx` that its cancellation alert only fires on the explicit `ride_cancelled` case — this is what surfaced the deeper pre-dispatch gap (missing event entirely, not just a missing field) that the original audit finding didn't fully characterize.
- [x] Confirmed `ride`/`ride.get("is_scheduled")` is in scope and populated (full-row reads, no restricted column list) at every edited call site.
- [x] `python3 -c "import ast; ast.parse(...)"` on both files — syntax valid.
- [x] `ruff check` on both files — clean.
- [x] Ran `test_ride_cancellation_branches.py`, `test_e2e_cancellation.py`, `test_scheduled_cancel_notice_fee.py`, `test_p2_scheduled_rides.py` (62 tests) and the `admin_cancel_ride`-covering tests in `test_ride_accept_flow.py` (2 tests) — all pass.

## What was NOT verified

- **No live WebSocket reproduction** — verified by direct source reading and cross-referencing the frontend's exact event-type switch, not by triggering a real cancellation and watching the admin dashboard live (no live backend/Supabase access from this sandbox).
- **No new regression test added** asserting `is_scheduled` is present on these three broadcasts specifically — the existing cancellation test suites (62 tests, all passing) don't currently assert on WS payload contents for this field; adding that assertion coverage was judged out of scope for this fix.
