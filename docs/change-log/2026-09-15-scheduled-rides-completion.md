# Scheduled rides: admin/app completion and pickup-time safeguards

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | Codex |
| Surfaces | Backend, admin dashboard, driver app, rider app |
| Domains | Dispatch, rides, drivers, payments, corporate, admin |
| Branch | `feat/scheduled-rides-completion` |
| Related PR | #5464 (configuration/schema/scheduler portion already merged by another actor) |

## Issue and root cause

Scheduled rides originally had a hardcoded rider reminder and pickup-time matching, with no accepted-driver reminder or service-area controls. The initial local implementation was lost in a workspace reset before upload. Its backend configuration, migration and scheduler were reconstructed and uploaded; that portion was merged as #5464 while the remainder was still being restored. This follow-up completes the required admin/mobile behavior and timing safeguards. **Keep per-area scheduled settings disabled until this follow-up and staging checks are deployed.**

Early matching requires search timers that last through booked pickup, pickup-based no-show eligibility and cancellation protection. Otherwise a ride offered ten minutes early can time out five minutes before pickup, or an early-arriving driver can start the rider's no-show clock too soon.

## Fix and user experience

Admin → Service Areas → select area → Scheduled Rides exposes the configuration introduced in #5464. Enabled defaults: matching, accepted-driver reminder and rider reminder each ten minutes before pickup. Matching accepts 0–30 whole minutes; reminders accept 1–60. Area configuration is disabled by default and the existing `scheduled_dispatch_enabled` global switch still gates the scheduler.

Driver offers use the normal matching flow. The dedicated reminder only goes to an accepted driver before pickup. A driver accepting after the configured reminder time receives it on the next check before pickup. The loop runs approximately once a minute with jitter; this is not an exact device-delivery guarantee. Matching earlier than the reminder (for example fifteen vs ten minutes) allows time for acceptance. No advance reservation marketplace or supply guarantee is added.

Phone offers, active trips, background/foreground/restored offers and Android Auto display the booked time. Reminder taps open driver home. Early arrival starts waiting at the later of actual arrival and pickup. Early-dispatched rides remain searchable until max(booked pickup, request time) plus five minutes. Cancellation before pickup after early assignment is free; the existing pre-dispatch notice policy remains unchanged. Ride response countdowns agree with these rules. A scheduled timeout atomically claims cancellation before releasing its hold; the canonical helper records release only on confirmed success.

Updated rider apps remove old local alarms on launch/booking; server reminders are authoritative. Failed native cancellations retain their IDs for another attempt. Corrupt stored maps are ignored. Old app versions can still show hardcoded local alarms until updated/opened. This cleanup is global, not gated by the area's early matching setting.

## Risk and blast radius

Cross-surface. Inspected consumers:

- Admin `updateServiceArea`, service-area create/update/list, page state: API validation/storage/RBAC came from #5464; this form submits the complete nested configuration and retains failed edits.
- Matching `_dispatch_retry`, `ride_search_timeout`, and stuck-ride sweeper: scheduled deadlines extend; ordinary ride limits remain. Lost in-process retries still do not resume after backend restart; durable sweeper cancellation remains the backstop.
- `calculate_cancellation_fee`, no-show endpoint and rider `get_ride`: shared financial/time paths. The new protection is scoped to scheduled rides before pickup. No capture/refund is added. Failed hold releases remain eligible for existing reconciliation.
- Driver home consumes `RideOfferPanel`, `ActiveRidePanel`, store and `useDriverDashboard`; foreground/background/WS/REST hydration preserves scheduled metadata. `carCard` feeds Android Auto surface and alerts; root-layout push handlers use the routing helper.
- Rider hook consumers are `ride-options`, `payment-confirm`, `scheduled-rides` cancellation and root-layout FCM handling. Compatibility callback names remain, but no new local timer is scheduled.
- #5464's scheduler and notification changes remain prerequisites: per-assignment reminders, stable inbox IDs, suppression vs transient failure handling, booked-time corporate policy evaluation. This follow-up only reorders scheduler imports and updates its existing scan-count tests.

Early offers occupy drivers before pickup and require enough supply. Config edits affect existing upcoming bookings on the next tick; they do not retract offers or reset assignments. FCM acceptance is not proof of device display. Updated pickup labels and local-alarm removal are visible independently of the runtime dispatch flag.

## Files modified

| Paths | Change | Purpose |
|---|---|---|
| `backend/routes/rides/matching.py`, `backend/utils/stuck_ride_sweeper.py` | Scheduled deadlines, conditional timeout claim, canonical hold release; offer pickup time | Prevent premature cancellation and unsafe release races |
| `backend/services/cancellation_service.py`, `backend/routes/drivers/ride_cancel.py`, `backend/routes/rides/queries.py` | Booked-pickup fee/no-show/countdown guards | Protect early pickups |
| `backend/routes/drivers/ride_reads.py` | Add scheduled time | Authenticated offer hydration |
| `backend/utils/scheduled_rides.py` | Import ordering | Lint only; runtime change already in #5464 |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx`, `_components/scheduled-rides-tab.tsx` | Area form, validation and save/error feedback | Admin configuration |
| `driver-app/store/driverStore.ts`, `hooks/useDriverDashboard.ts`, `services/backgroundMessaging.ts` | Typed metadata propagation, including degraded fetch | Consistent offers |
| `driver-app/components/panels/RideOfferPanel.tsx`, `components/dashboard/ActiveRidePanel.tsx`, `lib/androidAuto/carCard.ts`, `utils/pushNotificationRouting.ts` | Pickup labels, waiting clock and reminder tap routing | Driver clarity |
| `rider-app/hooks/useScheduledRideReminder.ts`, `app/_layout.tsx`, `app/payment-confirm.tsx` | Legacy timer cleanup | Server-owned configurable timing |
| Backend, admin, driver and rider test files in this diff | Boundary/failure and actual component regression tests | Verify changed behavior |

## Before / after

```python
# Before: every searching ride expires five minutes after dispatch.
# After: scheduled rides search through booked pickup plus grace.
deadline = max(scheduled_time, ride_requested_at) + timedelta(minutes=5)
# Scheduled timeout claims searching -> cancelled before releasing its card hold.
```

```python
# Before: early arrival starts no-show waiting immediately.
wait_start = arrived_at
# After:
wait_start = max(arrived_at, scheduled_time)
# Pickup 14:00, arrival 13:50 -> no no-show waiting before 14:00;
# rider cancellation at 13:55 after early assignment remains free.
```

```text
Before: local T-10 rider alarm competes with the configurable server reminder.
After: updated rider app cancels legacy alarms; server owns reminder timing.
Before: background/restored offers can lose booked pickup time.
After: scheduled metadata survives WS, FCM and REST hydration, including failed minimal-offer refresh.
```

## Rollout / rollback

Migration 426 from #5464 must be applied before the scheduler code runs. It adds the area JSON, driver reminder marker and partial index. Confirm the index is valid; interrupted concurrent builds require dropping the invalid index concurrently and rerunning, as documented in that SQL.

Deploy this backend/admin follow-up and tested mobile versions before enabling any area. Stage a scheduled ride with matching fifteen/reminders ten; check acceptance/reminder/tap, reassignment, early arrival, cancellation and no-driver timeout using Stripe test mode. Verify iOS/Android foreground/background/terminated delivery and Android Auto, then canary one area.

Immediate rollback: disable the area's configuration or set `service_areas.scheduled_ride_config = '{}'`. This restores pickup-time matching for undispatched bookings. `scheduled_dispatch_enabled=false` is the emergency scheduler pause (also pauses due dispatch and rider reminders). Never reset a dispatched/accepted ride or undo a confirmed release; let existing reconciliation handle failed holds. No schema rollback is required to disable the feature. Removed local alarms cannot be restored by a DB switch; reverting that mobile behavior requires an app release. No production migration, configuration activation, deployment or merge was performed by this session.

## Verification

Fresh results for the reconstructed branch will be recorded in the PR description. Prior lost-workspace results are historical and are not claimed as proof for this branch.

Tests cover enabled/disabled area windows, assigned-trip reminders, reassignment, target app, transient failures, inbox identity/suppression, search deadlines and lost/successful timeout claims, early cancellation/no-show, scheduled/unassigned response clocks, actual phone panels, car labels, background hydration, tap routing, legacy alarm cleanup/corrupt storage, and admin saves/validation/failures. Native child components and database calls are mocked; these are not live delivery/payment tests.

Real production build attempts: admin `npm run build -- --webpack`; driver and rider `expo export --platform android --platform ios`. Expo outputs are production JS/Hermes bundles, not signed native/EAS builds. Rider export is blocked by the pre-existing Voltra/Metro integration (`@use-voltra/core/dynamic-live-activity` cannot resolve with this repo's package-exports setting); no dependency/resolver change is included. Local Python tests additionally installed `socksio` for this workspace's proxy, and driver Jest aligned react-test-renderer to React 19.3 without changing repository manifests.

Not verified: live Supabase migration/index/constraints, FCM/APNs device display, deployed admin save, live service-area values, signed native builds, physical devices/head unit or Stripe staging ride. No mobile screenshot regression tooling exists. Admin has active baselines for six other pages, but service areas is not seeded; the new tab is component-tested and built, not screenshot-verified. Keep this PR draft until release blockers and staging gates are cleared.
