# Change Impact & Risk Log — minimised ride offers were not shown

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | continuation after the ring-volume channel build |
| Surface(s) | driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `fix/minimised-ride-offer-notification` |
| Related issue or gap ID | Live test after the ride-offers-v4 build: offer with sound while the app is open, no notification while minimised |

## 1. Issue / gap identified

After the build that added `ride-offers-v4`, a driver with the app open receives the ride offer and hears the in-app tone. The same offer with the app minimised produces no notification. Opening the app afterward shows nothing, because the offer window (about 15 seconds) has already closed. A new request made while the app stays open works again.

## 2. Root cause

Android dispatch pushes are data-only. The app must draw the notification itself. The new build posted that notification on `ride-offers-v4`, created with `AudioAttributes.USAGE_NOTIFICATION_RINGTONE`. Android treats that usage as a phone ringtone and does not show a shade or heads-up card unless the app is a dialer or holds full-screen intent, which Spinr does not. The in-app MP3 still plays while the app is in the foreground, so that path looked healthy. The code also deleted `ride-offers-v3` once v4 existed, so there was no visible channel left to post on.

## 3. Fix / remediation

Stop posting on v4. Delete v4 when it is already on the device, both from JS on the next launch and from `Application.onCreate` in the next native build. Recreate and post on `ride-offers-v3`, which shows the heads-up. The tone follows the phone's notification volume again, the same as before the v4 experiment.

## 4. Risk & impact on existing functionality

- Blast radius: driver-app Android ride-offer notifications only. Grep of `ride-offers-v4` is `notifeeService.ts`, `withRideOfferRingChannel.js`, `app.config.ts`, and this service's Jest file. The backend still sends a data-only Android push. iOS still uses the APNs alert and is unchanged.
- Foreground offers keep the in-app MP3 and the silent in-app channel (`ride-offers-fg-v2`).
- A driver who already installed the v4 build gets v3 back the next time `ensureNotifeeReady` runs. Until this JS is on the device, minimised offers stay invisible.
- Deleting v4 drops any per-channel setting the driver changed on that channel. v3 is a new channel if the previous build deleted it, so a custom sound picked on v3 before that deletion is not restored.
- No ride-state, money, or insurance-period change.

## 5. User-experience effect

- Android drivers, after this JS is in the build or an OTA: a minimised or killed app shows the ride-offer notification again, with sound at notification volume and vibration. It will not be as loud as a phone call.
- Visible the next time an offer arrives after the update. An offer that already expired is not replayed.
- No copy change.
- iOS: no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/services/notifeeService.ts` | Always post loud offers on `ride-offers-v3` and delete `ride-offers-v4` | v4 hid the minimised notification |
| `driver-app/plugins/withRideOfferRingChannel.js` | Native `onCreate` deletes v4 instead of creating it | The next binary must not recreate the hidden channel |
| `driver-app/app.config.ts` | Comment only | The plugin no longer creates a ring-volume channel |
| `driver-app/__tests__/services/notifeeService.test.ts` | Assert v3 is used and v4 is deleted | Lock the regression |

## 7. Before / after

```ts
// Before — loud/minimised offers posted on the ring-volume channel, and v3 was deleted
if (ringChannel) {
  rideOfferChannelId = 'ride-offers-v4';
  await notifee.deleteChannel('ride-offers-v3');
}
```

```ts
// After — v4 is removed; the visible v3 channel is recreated and used
await notifee.deleteChannel('ride-offers-v4');
rideOfferChannelId = 'ride-offers-v3';
await notifee.createChannel({ id: 'ride-offers-v3', /* HIGH, ride_offer sound */ });
```

## 8. Rollback plan

This is JS plus a native plugin. Revert the commit and ship that JS (OTA is enough to start posting on v4 again only if the native channel still exists). There is no feature flag. A git revert does not restore an offer the driver already missed. Prefer leaving v3 in place: the failure mode of this fix is a quieter notification, which is the previous live behavior, not a missing one.

## 9. Verification performed

- Jest `driver-app/__tests__/services/notifeeService.test.ts`: 30 passed.
- Not run on a device. rider-app and driver-app have no visual regression tooling. The previous v4 change-log already recorded that the ring-volume channel had no device test; this report is that missing test.
- Not verified: iOS, a killed-app FCM delivery on hardware, or notification volume vs ring volume on a Samsung.

## 10. Sign-off

- Rollback is a revert of this JS; do not recreate v4.
- Blast radius is the Android ride-offer notification channel only.
- Minimised offers become visible again and quieter than a phone ring. That is the behavior drivers had before v4.
