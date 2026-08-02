# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | dispatch, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #12 |

## 1. Issue / gap identified

Once dispatched, a scheduled ride is indistinguishable from an on-demand
one in the admin monitoring feed — including when its driver cancels
post-acceptance. **Research correction from the original gap review**: I
had assumed a cancelled scheduled ride re-enters matching through a generic
re-offer path. It doesn't — driver-cancel-after-accept is unconditionally
terminal for every ride type (`backend/routes/drivers/ride_cancel.py`, no
call to `match_driver_to_ride` anywhere in that file's call chain); the
rider must rebook manually. So the actual, narrower gap is: ops has no way
to tell a cancelled *scheduled* ride apart from a routine cancelled
*on-demand* ride, even though the former's rider has less slack to simply
re-hail (they specifically planned around this pickup time).

## 2. Root cause

`build_monitoring_ride()` (the single source of truth for the admin
dashboard's `MonitoringRide` shape) never included `is_scheduled`, and
neither did the two WS broadcasts fired on driver cancel
(`ride_status_changed`, `ride_cancelled`).

## 3. Fix / remediation

- `backend/routes/admin/monitoring.py::build_monitoring_ride()`: added
  `is_scheduled: bool(ride.get("is_scheduled"))` to the payload — flows
  automatically to the live-map snapshot fetcher, the `ride_requested`
  broadcast, and the scheduled dispatcher's own broadcast, since all three
  already build through this one function.
- `backend/routes/drivers/ride_cancel.py::cancel_ride()`: both admin-facing
  broadcasts on driver cancel now carry `is_scheduled` (re-read from the
  ride row fetched right after the cancel write, so it reflects the actual
  DB state).
- `admin-dashboard/src/app/dashboard/monitoring/types.ts`: added
  `is_scheduled: boolean` to `MonitoringRide`, and `is_scheduled?: boolean`
  to the `ride_status_changed`/`ride_cancelled` WS event variants.
- `admin-dashboard/.../monitoring/page.tsx`: the `ride_cancelled` alert-feed
  message reads `"⚠ Scheduled ride cancelled — rider needs follow-up"`
  instead of the generic `"Ride cancelled"` when `is_scheduled` is true —
  kept deliberately simple (a distinct message string in the existing flat
  alert feed) rather than building new badge/severity UI components, which
  the monitoring page doesn't have a pattern for today.

**Explicitly out of scope**: an auto-requeue/re-match mechanism for a
scheduled ride whose driver cancels post-accept. That's a real, bigger
feature (re-introducing dispatch for an already-cancelled ride touches the
core cancellation contract shared by every ride type) and deserves its own
design pass, not a bundled add-on to a visibility fix.

## 4. Risk & impact on existing functionality

- **Blast radius**: `build_monitoring_ride()` is used by the live-map
  snapshot fetcher, `ride_requested` (booking.py), and the scheduled
  dispatcher's own admin broadcast (scheduled_rides.py) — all three now
  emit one additional boolean field. Grepped the admin-dashboard for every
  consumer of `MonitoringRide`/`MonitoringWsEvent`: only
  `monitoring/page.tsx` and the type file itself; no other component
  destructures these types positionally (all field access is by name), so
  adding a field is additive and cannot break existing consumers.
- `ride_cancel.py`'s two broadcasts: grepped for other readers of
  `ride_status_changed`/`ride_cancelled` payloads on the rider/driver apps —
  both already read only `status`/`ride_id`/`reason` from these events and
  ignore unknown extra keys (no strict schema validation on receipt), so
  adding `is_scheduled` cannot break the rider or driver app.
- No interaction with money, the ride state machine, or corporate billing —
  this is presentation-layer visibility only.

## 5. User-experience effect

**Internal admin only.** Ops sees a distinct alert-feed message for a
cancelled scheduled ride instead of the generic one. No rider/driver-facing
change at all — riders and drivers were never shown this distinction and
still aren't.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/monitoring.py` | `build_monitoring_ride()` now includes `is_scheduled` | Single source of truth for the dashboard ride shape |
| `backend/routes/drivers/ride_cancel.py` | Both admin broadcasts on driver-cancel carry `is_scheduled` | Surface the distinction at the moment it matters most |
| `admin-dashboard/src/app/dashboard/monitoring/types.ts` | `MonitoringRide.is_scheduled`; `is_scheduled?` on two WS event variants | Type-level contract matching the new wire payload |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | Distinct alert message for a scheduled ride's cancellation | Make it visible in the one UI surface that exists today (flat alert feed) |
| `backend/tests/test_admin_monitoring_coverage.py` | `is_scheduled` assertions added to both existing `build_monitoring_ride` tests | Pin the new field, including the falsy-coercion case |
| `backend/tests/test_c2_driver_cancel_atomic.py` | Two new tests: `is_scheduled=True` for a scheduled ride's cancel broadcasts, `is_scheduled=False` (not omitted) for an on-demand one | Pin both broadcast payloads |

## 7. Before / after

```python
# Before (build_monitoring_ride)
"is_corporate": bool(ride.get("corporate_account_id")),
}
```

```python
# After
"is_corporate": bool(ride.get("corporate_account_id")),
"is_scheduled": bool(ride.get("is_scheduled")),
}
```

```python
# Before (ride_cancel.py)
await _deps.manager.broadcast_ride_status(ride_id, RideStatus.CANCELLED, rider_id=..., reason="driver_cancelled")
await _deps.manager.broadcast_to_admins({"type": "ride_cancelled", "ride_id": ride_id, "reason": "driver_cancelled"})
```

```python
# After
_was_scheduled = bool((ride or {}).get("is_scheduled"))
await _deps.manager.broadcast_ride_status(..., is_scheduled=_was_scheduled)
await _deps.manager.broadcast_to_admins({..., "is_scheduled": _was_scheduled})
```

## 8. Rollback plan

Plain code change across backend + frontend, no migration, no data written.
`git revert` fully restores prior behavior on both surfaces.

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_admin_monitoring_coverage.py` +
      `backend/tests/test_c2_driver_cancel_atomic.py`, 26 passed (22 prior +
      4 new) via the session's venv.
- [x] `ruff check` on all touched backend files — clean.
- [x] `npx tsc --noEmit` on the full admin-dashboard — 27 pre-existing
      errors, none in `src/app/dashboard/monitoring/` or related to this
      change (confirmed by grep before and after; all 27 are in unrelated
      test files: `driver-statements-panel.test.tsx`,
      `companyApi.test.ts`, `route-segments.test.ts`).
- [ ] Manual repro in staging / real browser check — not performed, no
      staging or dev-server access from this session. This is a UI-adjacent
      change (a text string), so per CLAUDE.md's UI-verification guidance:
      type-checked but not visually confirmed in a running app.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's "no silent behavior change" convention —
      the auto-requeue idea from the original gap review was deliberately
      NOT built here; scope was narrowed to visibility only, and that
      narrowing is stated explicitly rather than silently substituted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — additive field on an existing
      payload/type, no consumer breaks
- [x] No silent behavior change — admin-only, and explicitly scoped down
      from "re-offer" (not built) to "visibility" (built), stated plainly
      rather than letting the smaller delivery pass as the bigger ask

## What was NOT verified

Not visually confirmed in a running admin dashboard (no dev server access
this session) — only type-checked. The alert-feed message change is a
one-line string; low risk, but "looks right in code" was not confirmed
against "renders correctly in the browser." No auto-requeue/re-match
feature was built — flagged above as explicitly out of scope, not
silently dropped.
