# Change Impact & Risk Log — Android ride offers on the alarm volume (flagged)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | ride-offer sound follow-up to #5761 / #5774 |
| Surface(s) | backend / driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/inspiring-ritchie-9ginf3` |
| Related issue or gap ID | Drivers miss minimised/locked offers when the phone is on silent or vibrate |

## 1. Issue / gap identified

A minimised, locked or killed driver app rings ride offers on `ride-offers-v3`, which plays on the notification volume. Silent and vibrate ringer modes mute that stream, and many drivers keep notification volume low, so the offer is quiet or silent. #5761 tried the ring volume (`ride-offers-v4`, `USAGE_NOTIFICATION_RINGTONE`); on device the minimised offer stopped showing at all, and #5774 moved back to v3.

## 2. Root cause

Notifee's `createChannel` always builds the channel's `AudioAttributes` with `USAGE_NOTIFICATION` and cannot change it, and a channel's sound is immutable once created. The only loud stream that silent/vibrate mode does not mute is the alarm stream, which needs a natively created channel with `USAGE_ALARM`. (The ring stream that v4 used is muted by silent mode too, so v4 would not have met the "loud on silent" goal even if it had shown.)

## 3. Fix / remediation

- Migration 466 adds `settings.ride_offer_alarm_channel_enabled` (default **false**), writable through `PUT /api/admin/settings`.
- Every `new_ride_assignment` payload carries `ring_mode`: `"alarm"` when the flag is exactly true, else `"notification"` (`utils/ride_offer_ring.py`). Stamped in auto-dispatch (`matching.py`, shared by the WS message and the data-only FCM push), admin direct-assign, and `POST /notifications/debug-ride-offer`. The debug endpoint also takes an explicit `ring_mode` so one device can be tested without flipping the flag.
- The driver-app config plugin creates `ride-offers-alarm-v1` natively in `Application.onCreate` (`USAGE_ALARM`, HIGH importance, `ride_offer` sound, same vibration/lights/lockscreen settings as v3). It still deletes v4.
- `notifeeService` posts a loud offer on `ride-offers-alarm-v1` only when the last `ring_mode` seen is `"alarm"` **and** `notifee.getChannel` finds the channel unblocked. Missing, blocked or a lookup error falls back to `ride-offers-v3`. In-app (silent) and muted posts keep the silent channel. The last `ring_mode` is remembered because the reclaim re-post (app backgrounded mid-offer) is built from app state that does not carry it.

Alternative considered: a native foreground service playing the tone with `MediaPlayer` on the alarm stream for the offer window. More control (exact duration, stops on accept), but a new native service, foreground-service type declarations and Play policy review. The channel approach reuses the existing plugin, the Notifee card, and `loopSound` (`FLAG_INSISTENT`, which already rings until the offer's `timeoutAfter`), so it is the smaller change to try first.

## 4. Risk & impact on existing functionality

- Blast radius: cross-surface (backend payload + driver-app Android notification). iOS ignores `ring_mode`.
- Flag off (default): payloads gain `ring_mode: "notification"`; the app never looks up the alarm channel and posts exactly as today. Old app builds ignore the unknown field.
- Flag on:
  - A build with the native channel rings minimised offers on the alarm volume.
  - A build without it logs a warning and uses v3, the same as today.
  - **Main risk:** Android could treat an alarm-usage notification the way it treated the v4 ringtone channel and not show it. Unverified. The flag stays off until the device test below passes.
- Every install of the new binary gets a second "Ride Offers (loud)" channel under Settings → Notifications, even with the flag off. Unused until the flag is on.
- Readers of the same payloads:
  - the driver-app WS handler (`useDriverDashboard.ts`);
  - the headless FCM handler (`backgroundMessaging.ts`);
  - `pendingRideOffer.ts`, which ignores unknown fields;
  - `push_retry.py`, which re-sends the stored data dict including `ring_mode`.
  - `_FCM_EXCLUDE` / minimal-payload mode do not drop it.
- No ride-state, money, insurance-period or dispatch-matching change. `ring_mode` does not affect who gets offered.

## 5. User-experience effect

- Drivers (Android, new binary, flag on): a minimised/locked/killed-app offer rings at alarm volume, through silent/vibrate and DND's default "alarms allowed", looping until the offer expires.
- Drivers with the flag off, iOS drivers, and anyone on an older build: no change.
- Visible on the next offer after the flag flips (settings cache ≤60 s). No copy change on the offer itself.
- New channel name visible in Android settings: "Ride Offers (loud)".

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/466_ride_offer_alarm_channel_flag.sql` | New default-false column | Flag without redeploy |
| `backend/routes/admin/settings.py` | `ride_offer_alarm_channel_enabled` on `SettingsUpdateRequest` | Settable via admin API |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Column added to snapshot | Drift guard |
| `backend/utils/ride_offer_ring.py` | `ride_offer_ring_mode(settings)` | One definition of the rule |
| `backend/routes/rides/matching.py` | `ring_mode` on `dispatch_payload` | WS + FCM offers |
| `backend/routes/admin/rides.py` | `ring_mode` on admin direct-assign offer | Same behaviour as dispatch |
| `backend/routes/notifications.py` | `ring_mode` on debug offer + optional override | Device test before the flag |
| `backend/tests/test_ride_offer_ring.py`, `test_p3_push_notifications.py` | Helper and debug-endpoint tests | Regression |
| `driver-app/plugins/withRideOfferRingChannel.js` | Kotlin also creates `ride-offers-alarm-v1` (USAGE_ALARM) | Notifee cannot set audio usage |
| `driver-app/app.config.ts` | Comment only | Plugin now creates a channel |
| `driver-app/services/notifeeService.ts` | Alarm channel selection with v3 fallback | Loud minimised offer |
| `driver-app/services/backgroundMessaging.ts`, `hooks/useDriverDashboard.ts` | Pass `ring_mode` through | Headless + WS/reclaim paths |
| `driver-app/__tests__/…` | Plugin, channel-selection, mapper tests | Regression |

## 7. Before / after

```ts
// Before — loud posts always on the notification-volume channel
channelId: muted ? RIDE_OFFER_SILENT_CHANNEL_ID : rideOfferChannelId, // ride-offers-v3

// After — alarm channel only when the backend says so and the native channel exists
const channelId = muted ? RIDE_OFFER_SILENT_CHANNEL_ID : await loudChannelId();
// loudChannelId(): lastRingMode === 'alarm' && getChannel('ride-offers-alarm-v1') unblocked
//   ? 'ride-offers-alarm-v1' : 'ride-offers-v3'
```

## 8. Rollback plan

- Operational, no release: `UPDATE public.settings SET ride_offer_alarm_channel_enabled = false WHERE id = 'app_settings';` Offers created after the 60 s settings cache carry `ring_mode: "notification"`.
- An app process that already saw an `"alarm"` offer keeps it until it receives a `"notification"` offer: `lastRingMode` is module state, and every offer carries the field, so the next offer corrects it.
- The native channel stays on devices but is unused. No data to clean up. The migration's own comment has the optional column drop.

## 9. Verification performed

- Plugin logic checked with plain Node against a stubbed `@expo/config-plugins`: all assertions in the new plugin test hold.
- Backend helper logic run directly with Python. All touched Python files parse.
- **Not run:** Jest, pytest, `tsc`, ruff, and the Kotlin compile. This session's network policy blocked npm and PyPI, so no dependencies could be installed. CI's `driver-app-test`, `backend-test` and the EAS build are the first real runs.

## 10. What was NOT verified

- **No device test.** Before turning the flag on:
  1. Install an EAS build containing this plugin.
  2. Put the phone on silent.
  3. Minimise or lock the app.
  4. Send `POST /api/notifications/debug-ride-offer` with `{"user_id": "<driver>", "ring_mode": "alarm"}`.
  5. Confirm the card shows in the shade/heads-up and rings at alarm volume until it expires.
  6. Repeat with the app killed, and on a Samsung device.
  7. If the card does not show, capture `adb logcat | grep -i NotificationService`.
- Not verified: DND behaviour on OEM skins, interaction with Android 14 full-screen-intent denial, and whether `CATEGORY_CALL` plus alarm usage changes heads-up behaviour.
- iOS: unchanged. The 1.8 s `ride_offer.caf` still plays once. Not addressed here.
- No visual regression tooling for driver-app; the notification card was not screenshotted.
