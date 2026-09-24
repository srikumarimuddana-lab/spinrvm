# Change Impact & Risk Log — ride offers ring at call volume when the driver app is minimised

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code (session on `claude/brave-dijkstra-lgcgat`) |
| Surface(s) | driver-app (Android only) |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/brave-dijkstra-lgcgat` |
| Related issue or gap ID | Driver report from live testing: "offer sound is loud with the app open, very low when minimised; vibration fine" |

## 1. Issue / gap identified

On Android, a ride-offer notification that arrives while the driver app is minimised or killed plays its tone very quietly, while the same offer with the app open is loud. Vibration is normal in both cases.

## 2. Root cause

The two app states use two different audio paths:

- **App open:** `hooks/useRideOfferSound.ts` loops `ride_offer.mp3` through expo-audio, which plays on the **media** stream.
- **App minimised or killed:** expo-audio pauses its players on the background transition, and the ring is handed to the OS notification (`useDriverDashboard.ts` AppState handover, `services/backgroundMessaging.ts` for headless FCM). That notification rings through the `ride-offers-v3` channel. `@notifee/react-native` always builds a channel's `AudioAttributes` with `USAGE_NOTIFICATION` and has no option to change it, so the tone follows the phone's **Notifications** volume slider, which on many devices (Samsung especially) sits well below media and ring volume.

Vibration doesn't depend on any volume slider, which is why it was unaffected. The sound file and Notifee's `USAGE_NOTIFICATION` behaviour were reasoned from the code and Notifee's documented API; neither was measured on a device in this session.

## 3. Fix / remediation

- New Expo config plugin `plugins/withRideOfferRingChannel.js` generates a Kotlin object that creates a new Android channel, `ride-offers-v4`, in `MainApplication.onCreate`. It uses the same settings as v3 (HIGH importance, `ride_offer` sound, vibration pattern, lights, bypass-DND, public lockscreen), with one difference: `AudioAttributes.USAGE_NOTIFICATION_RINGTONE`. The tone now plays at the **ring** volume, like an incoming phone call.
- `services/notifeeService.ts`: `ensureNotifeeReady()` looks up `ride-offers-v4`.
  - If it exists (a new binary with the plugin), loud offers post to v4, and v3 is deleted so drivers don't see two "Ride Offers" rows in Settings.
  - If it doesn't exist (an old binary, or the native creation failed), it creates and uses v3 exactly as before.
  - JS **never creates v4**. Android channel sound settings are immutable, so if an OTA JS bundle on an old binary created v4, the quiet notification-stream settings would be locked in permanently.
- Silent/muted paths (`ride-offers-fg-v2`) are unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius: single surface (driver-app Android), one notification.** Grepped the whole repo for `ride-offers-v3` and `NOTIFEE_RIDE_OFFER_CHANNEL_ID`. The only consumers are `services/notifeeService.ts` and its test. The backend (`features.py` `_build_fcm_message`) sends dispatch offers **data-only** with no channel id. Its `android_channel = "ride-offers"` applies only to non-dispatch driver pushes, on the expo-notifications channel created in `app/_layout.tsx`, which this change doesn't touch.
- `displayRideOfferNotification` callers: `hooks/useDriverDashboard.ts` (`_surfaceOfferNotification`, foreground/background handover) and `services/backgroundMessaging.ts` (headless FCM). Both go through `ensureNotifeeReady()` before posting, so both pick up the resolved channel.
- **Headless launch:** `Application.onCreate` runs before the Firebase background handler, so v4 already exists when a killed-app offer arrives.
- **Failure modes:**
  - If the native code throws, or `res/raw/ride_offer` is missing, it logs `Log.e` and skips v4. JS falls back to v3, which gives today's behaviour, never silence.
  - If `getChannel` rejects, the error is logged with `console.error` and v3 is used.
  - If the MainApplication injection anchor is missing, **the build fails** rather than shipping a binary that silently stays quiet.
- **Behavioural difference of `USAGE_NOTIFICATION_RINGTONE`:** the tone follows the ring volume. If the driver puts the phone on vibrate or silent, it doesn't sound, same as today. DND behaviour is still governed by the channel's `bypassDnd` and the driver's DND exceptions, as before.
- **Per-channel customisations reset:** a driver who customised the old "Ride Offers" channel (e.g. picked another sound) loses that on first launch of the new binary, because v3 is deleted and v4 is new.
- No state machine, money, insurance-period or backend changes.

## 5. User-experience effect

- **Drivers on Android:** after installing the new build, offers that arrive while the app is minimised or killed ring at **call (ring) volume** instead of notification volume. For most drivers that's louder.
- **Not visible mid-session** until the driver installs the new native build. OTA updates alone don't change behaviour: without the native channel, JS keeps using v3.
- No copy change. The channel is still named "Ride Offers"; only its Settings description text differs.
- iOS: no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/plugins/withRideOfferRingChannel.js` | New config plugin: writes `RideOfferRingChannel.kt`, injects `RideOfferRingChannel.ensure(this)` after `super.onCreate()` in `MainApplication.kt` | Notifee can't set a channel's audio usage; this has to be created natively before JS runs |
| `driver-app/app.config.ts` | Registered the plugin after `withRideOfferSound` | The Kotlin code looks up the `ride_offer` raw resource that `withRideOfferSound` copies in |
| `driver-app/services/notifeeService.ts` | `ensureNotifeeReady` prefers the native v4 channel, falls back to v3; loud posts use the resolved channel id | Route loud offers to the ring-volume channel without ever letting JS create v4 |
| `driver-app/__tests__/services/notifeeService.test.ts` | `getChannel` mock (default: no v4) + 4 new cases | Cover the v4 path, the lookup-failure fallback, and that silent stays on fg-v2 |

## 7. Before / after

```ts
// Before — loud offers always on the Notifee-created channel (USAGE_NOTIFICATION)
channelId: muted ? RIDE_OFFER_SILENT_CHANNEL_ID : RIDE_OFFER_CHANNEL_ID, // 'ride-offers-v3'
```

```ts
// After — v4 (natively created, USAGE_NOTIFICATION_RINGTONE) when present, else v3
const ringChannel = await notifee.getChannel('ride-offers-v4').catch(/* log */ () => null);
if (ringChannel) { rideOfferChannelId = 'ride-offers-v4'; await notifee.deleteChannel('ride-offers-v3'); }
else { rideOfferChannelId = 'ride-offers-v3'; await notifee.createChannel({ id: 'ride-offers-v3', ... }); }
// ...
channelId: muted ? RIDE_OFFER_SILENT_CHANNEL_ID : rideOfferChannelId,
```

Scenario: a driver is online on the new build with the app minimised, Notifications volume at 20% and Ring volume at 80%. Before, the offer tone played at 20%. After, it plays at 80%.

## 8. Rollback plan

- **No app_settings flag.** The channel decision has to be made on-device before any network call (including the killed-app headless launch), so a server flag can't gate it reliably.
- **Rollback without a native rebuild:** publish an OTA update (expo-updates is configured, Android runtime `2.8.0`) where `ensureNotifeeReady` always takes the v3 branch. It recreates v3 (Android restores a deleted channel with the same id) and posts there. The native v4 channel stays harmlessly unused.
- The native side has no data effects. Nothing is written to the backend, ride state, or money paths.
- No `runtimeVersion` bump is needed. Old binaries running the new JS find no v4 and keep using v3. New binaries running old JS post to v3 (today's behaviour).

## 9. Verification performed

- [x] Plugin logic: a Node harness with a stubbed `@expo/config-plugins` ran both mods against a MainApplication.kt shaped like the Expo SDK 57 template. The call lands right after `super.onCreate()`, injection is idempotent, it throws on a missing anchor or a Java MainApplication, and `RideOfferRingChannel.kt` is written under `com/spinr/driver/` with `USAGE_NOTIFICATION_RINGTONE`.
- [x] Syntax check of `notifeeService.ts` and its test via Node's TypeScript type-stripping parser. This is a parse check only, **not** `tsc`.
- [x] Blast-radius grep: `ride-offers-v3`, `NOTIFEE_RIDE_OFFER_CHANNEL_ID`, `createChannel` mocks across `__tests__`, `__mocks__`, `jest.setup.js`, and backend `channel_id`.
- [ ] `spinr-notification-ux-reviewer` agent review of the diff — in progress at commit time; findings addressed in follow-up commits.
- [ ] Jest **not run**: driver-app dependencies couldn't be installed in this session (npm registry returned 403).
- [ ] Not feature-flagged; see section 8 for why and for the OTA kill switch.

## 10. What was NOT verified

- **The Kotlin was not compiled** (no `kotlinc` or Android SDK in this session). The first EAS Android build is the compile check.
- **No device test.** Loudness at ring volume, FLAG_INSISTENT looping on the native channel, and OEM behaviour (Samsung/Xiaomi volume and DND handling) were reasoned about, not observed. Verify on a real device: install the build, minimise the app, set Notifications volume low and Ring volume high, trigger an offer, and confirm it's loud. Also check Settings → Apps → Spinr Driver → Notifications shows a single "Ride Offers" channel.
- The Expo SDK 57 `MainApplication.kt` shape was taken from the template, not from a real `expo prebuild` in this session.
- driver-app has no visual or snapshot regression tooling. The notification card itself is unchanged; only the channel and its audio routing changed.
- `npm run build` / EAS build not run.
