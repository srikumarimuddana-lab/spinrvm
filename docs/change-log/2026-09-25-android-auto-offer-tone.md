# Change Impact & Risk Log: ride-offer tone on Android Auto

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (Developer role), for mkkreddy52@gmail.com |
| Surface(s) | backend, driver-app (Android only; iOS is a no-op) |
| Domain (Sentry tag) | drivers, dispatch |
| PR / commit link | branch `claude/cool-knuth-8ixvlb` (commits `534603d`..HEAD) |
| Related issue or gap ID | "No offer sound on Android Auto" plus the car-only stuck-offer bug found in the same investigation |

## 1. Issue / gap identified

With the phone connected to Android Auto, a ride offer shows on the car screen but makes no sound through the car. On a car-only launch (phone UI never opened) an unanswered offer also never expires, so the driver cannot receive another offer until they open the phone app.

## 2. Root cause

- The car offer is `template.showAlert` (`lib/androidAuto/register.ts`), which is visual only.
- The in-app tone (`hooks/useRideOfferSound.ts`) is started only by `hooks/useDriverDashboard.ts`, which is not mounted on a car-only launch. The car receives offers through `carSession.onBackgroundDispatch`, and that path only calls `setIncomingRide`.
- The headless FCM path (`services/backgroundMessaging.ts`) posts a Notifee card on `ride-offers-v3` with `loopSound`. We believe Android Auto does not play that card through the car, because it has no `CarAppExtender`. This was inferred and has not been measured on a head unit.
- Phone open, then car connected and phone locked: the AppState ownership effect in `useDriverDashboard` hands the ring to that same OS card.
- Nothing dismissed the Notifee card when an offer ended on a car-only launch. The only dismiss is the dashboard's `rideState` effect.
- The only offer countdown is the phone screen's (`app/driver/(tabs)/index.tsx`, `setCountdown(0)` → `declineRide(id, 'offer_expired')`). On a car-only launch nothing calls it. `setIncomingRide` refuses unless the state is `idle`, so every later offer was dropped.

## 3. Fix / remediation

- **One ring owner.** `lib/androidAuto/carOfferRing.ts` introduces `isCarRingOwner()`. It is true only when a car is connected, the new `settings.android_auto_offer_tone_enabled` flag is on (migration 482, default **off**), and the new native module works on this build.
- **When the car owns the ring:**
  - A new local Expo module (`modules/ride-offer-tone`, Android) plays the bundled `res/raw/ride_offer` tone as `USAGE_ASSISTANCE_NAVIGATION_GUIDANCE` with `AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK`. The tone reaches the car speakers and ducks music.
  - It loops with a 2.5 s gap until the offer deadline (at most 60 s).
  - It never rings during a call. It waits up to 3 s for a nav prompt to finish, and it pauses whenever a nav prompt takes focus.
  - The phone's in-app loop and the Notifee card stay silent. The card is still posted, but muted.
- **Hand back.** If the flag goes off, the car disconnects, or the car tone fails, the ring goes back to the phone. With the phone UI mounted, the dashboard re-runs its audio election. With no phone UI, the card is re-posted with `reclaim`.
- **Offer end (fixes the car-only card leak).** When the offer is no longer live, the car tone stops and the Notifee card is dismissed.
- **Car-only expiry (not flag-gated, restores parity with the phone).** While a car is connected and no phone UI is mounted, the store's `setCountdown(0)` is armed at the offer deadline + 1.5 s. It re-checks that the same offer is still live and waits out an accept in flight.
- **Flag delivery.** The flag reaches the app on `/drivers/config`. The car session re-reads the config every 5th 60 s tick, so the kill switch applies within about 5 minutes.

## 4. Risk & impact on existing functionality

**Blast radius: single surface (driver-app, Android), plus an additive backend field.**

| Touched | Other readers/writers (grepped) | Risk |
|---|---|---|
| `settings.android_auto_offer_tone_enabled` (new column) | `SettingsUpdateRequest`, `get_driver_config` only | Additive, default false. |
| `/drivers/config` response | `shared/hooks/queries/driverQueries.ts` (`useDriverConfig`), `carSession.loadDriverConfig`, `e2e/fixtures.ts` | Additive key. Older apps ignore it. |
| `useRideOfferSound` | only `useDriverDashboard` | `playOnce` returns early while the car owns the ring. With the flag off, `isCarRingOwner()` is always false, so there is no change. |
| `useDriverDashboard._surfaceOfferNotification` | WS handler, FCM foreground handler, `consumePendingOffer`, the AppState election | `muted` also becomes true while the car owns the ring. The field mapping moved to `services/rideOfferDisplayData.ts` with identical fields. |
| AppState audio election (`audioOwnerRef`) | `consumePendingOffer` | New owner `'car'`. The election also re-runs on car-ownership changes and on hand back. With the flag off, `'car'` is unreachable. |
| `backgroundMessaging` headless card | the Notifee card only | Muted while the car owns the ring. A failed check falls back to ringing as before. |
| `notifeeService.displayRideOfferNotification` / `dismissRideOfferNotification` | `useDriverDashboard`, `backgroundMessaging`, `app/_layout.tsx` (dismiss) | Called, not changed. The extra dismiss is idempotent. |
| `driverStore.setCountdown(0)` | `app/driver/(tabs)/index.tsx` (the phone countdown) | Called by the car only when no phone UI is registered, so it never races the phone countdown. The store logic is unchanged. |
| `register.ts` `apply()` | runs on every store change while connected | One contained call at the end. A throw is logged and cannot cost the chrome. |
| `carSession` refresh interval | existing 60 s tick | Adds one config GET every 5 minutes, behind the same App Check / token guard. |
| `isCarSessionActive()` | `lib/navigation/launchNavigation.ts` | Untouched. |
| `driver-app/.gitignore` | none | Adds a negation so `modules/*/android/` is committed. Without it the module's native sources were silently ignored. |

- **Ride state machine:** no new transition. The car-only expiry calls the existing `setCountdown(0)` → `declineRide(id, 'offer_expired')`, the same call the phone countdown makes.
- **Money / wallet / insurance periods:** not touched.
- **Background loops:** none added on the backend.

## 5. User-experience effect

- **Driver on Android Auto, flag on:**
  - The offer tone plays through the car speakers and music ducks. It repeats every ~2.5 s until they act or the offer expires.
  - The phone stays silent, and Accept/Decline stays on the car screen.
  - With Settings → Sound Effects off, the offer is visual only in the car too.
  - During a call nothing rings, neither car nor phone.
- **Driver on Android Auto, flag off (default):** the tone is unchanged (the phone rings as before). Behaviour does change in two places, both bug fixes:
  - An unanswered offer on a car-only launch now expires after the deadline + 1.5 s (a toast is set in the store; nobody sees it car-only). Before, the offer stayed stuck.
  - The Notifee card is dismissed when the offer ends.
- **Mid-session:**
  - Flipping the flag on reaches a phone-open session on the next `/drivers/config` read, and a car-only session within about 5 minutes. An offer that is live at that moment moves its ring to the car.
  - Flipping the flag off moves a live offer's ring back to the phone.
- **Copy:** no new text. The expiry toast string is the store's existing one.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/482_android_auto_offer_tone_flag.sql` | New BOOLEAN column, default false | Kill switch / canary flag |
| `backend/routes/admin/settings.py` | `android_auto_offer_tone_enabled: Optional[bool]` | Admin save can write it |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Column added to snapshot | Drift guard |
| `backend/routes/drivers/profile.py` | Field on `/drivers/config` (`is True`) | Deliver the flag to the app |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | Parametrized flag test + failure default | Coverage |
| `driver-app/.gitignore` | `!modules/*/android/` | Commit the module's native sources |
| `driver-app/modules/ride-offer-tone/{expo-module.config.json,android/build.gradle,android/src/.../RideOfferToneModule.kt,index.ts}` | New local Expo module + JS wrapper | Tone that routes to the car and ducks |
| `driver-app/lib/androidAuto/carOfferRing.ts` | New ring owner + car-only expiry | Single-owner rule, hand back, card dismiss, expiry |
| `driver-app/services/rideOfferDisplayData.ts` | Extracted Notifee field mapping | Shared by dashboard + hand back |
| `driver-app/lib/androidAuto/register.ts` | `setCarConnected`, `syncCarOfferRing` in `apply()` | Drive the owner from the car session |
| `driver-app/lib/androidAuto/carSession.ts` | Apply flag from config; config re-read every 5th tick | Flag + kill switch reach |
| `driver-app/hooks/useRideOfferSound.ts` | Skip while car owns; pause on takeover | Phone silent while car rings |
| `driver-app/hooks/useDriverDashboard.ts` | `muted` includes car owner; `'car'` owner in the election; phone ring handler; flag from config | Phone side of the rule |
| `driver-app/services/backgroundMessaging.ts` | Headless card muted while car owns | Phone side of the rule |
| `driver-app/lib/androidAuto/__tests__/{carOfferRing,register,carSession}.test.ts`, `driver-app/hooks/__tests__/useRideOfferSound.carOwner.test.ts`, `driver-app/__tests__/services/backgroundMessaging.android.test.ts` | Tests | See §9 |

## 7. Before / after

```ts
// Before: hooks/useRideOfferSound.ts playOnce
if (!useAlertPrefsStore.getState().soundEffects) return;
const player = _getOrCreatePlayer();
```

```ts
// After
if (!useAlertPrefsStore.getState().soundEffects) return;
if (isCarRingOwner()) return; // Android Auto rings this offer through the car
const player = _getOrCreatePlayer();
```

```ts
// Before: services/backgroundMessaging.ts (headless FCM card)
const muted = !useAlertPrefsStore.getState().soundEffects;
```

```ts
// After
let carOwner = false;
try { carOwner = require('../lib/androidAuto/carOfferRing').isCarRingOwner() === true; } catch (e) { console.warn(...); }
const muted = !useAlertPrefsStore.getState().soundEffects || carOwner;
```

```ts
// Before: useDriverDashboard AppState election
const owner: 'app' | 'os' = next === 'active' ? 'app' : 'os';
```

```ts
// After (re-run on AppState change, car-ownership change, and car hand back)
const owner: 'app' | 'os' | 'car' = isCarRingOwner() ? 'car' : next === 'active' ? 'app' : 'os';
if (owner === 'car') { offerSound.stop(); void _surfaceOfferNotification(offer, true); }
```

Scenarios:

| Scenario | Before | After |
|---|---|---|
| Car-only, flag on, offer arrives | Car popup; no sound in the car (OS card likely suppressed by AA) | Car popup + tone through the car speakers, music ducked; card muted |
| Car-only, offer ignored | Offer stuck in `ride_offered`; later offers dropped | Declined `offer_expired` at deadline + 1.5 s → `idle` |
| Phone open, car connected, phone locked, flag on | Ring handed to the OS card (AA-suppressed) | Car tone; phone loop stopped; card silent |
| Flag on, car tone fails (`error`) | n/a | Phone re-rings (loop if foreground, reclaimed card if background) |

## 8. Rollback plan

- **Tone behaviour, no deploy:** `UPDATE public.settings SET android_auto_offer_tone_enabled = false WHERE id = 'app_settings';`. A phone-open session picks it up on its next `/drivers/config` read. A connected car picks it up within about 5 minutes (re-read every 5th tick) or on reconnect. A live offer's ring moves back to the phone at that point.
- **Car-only expiry (subtask 5, not flag-gated):** revert the commit and ship it via OTA/JS update. The native module is not needed for the revert. This is safe for live data: it only calls the existing `setCountdown(0)` → `declineRide(…, 'offer_expired')` path. No money or state-machine data is written that the phone path would not also write.
- **Schema:** `ALTER TABLE public.settings DROP COLUMN android_auto_offer_tone_enabled;`, after the readers are retired. It is also in the migration header.
- **Native module:** shipping a binary without the module (or with an older binary) makes `isRideOfferToneSupported()` false, which gives today's behaviour.

## 9. Verification performed

- **Automated tests:**
  - **pytest was NOT run.** fastapi is not installed and the PyPI registry is blocked in this session. Only `python3 -m py_compile` was run on the changed backend files, and it passed.
  - **jest was NOT run.** `node_modules` is not installed and the npm registry is blocked.
  - As a partial substitute, a minimal jest-compatible shim, written for this session, ran some suites by transpiling with the global `typescript` package. It is **not jest** and is not equivalent to a real run:
    - `lib/androidAuto/__tests__/carOfferRing.test.ts`: 41/41 passed; 45/45 after the review fixes.
    - `lib/androidAuto/__tests__/carSession.test.ts`: 38/38 passed, including the pre-existing cases. `zustand` was stubbed.
    - `__tests__/services/backgroundMessaging.android.test.ts`: the new car-owner cases and the surrounding handler cases passed. Several pre-existing cases could not run because the shim lacks `expect.stringContaining` / `expect.anything`.
    - Mutation checks confirmed the shim catches regressions: making `blocked_call` hand back, removing the phone-handler guard on expiry, and dropping `|| carOwner` each failed the matching test.
  - `register.test.ts` and `useRideOfferSound.carOwner.test.ts` were **not executed** at all. They need the real RN/renderer stack.
- **Type check:** the full project `tsc --noEmit` could not run without `node_modules`. A targeted `tsc` over the changed files (dependencies unresolved, so treated as `any`) reported no errors in the changed files. That check is weak: the store/zustand types resolved to `any`.
- **Native code:**
  - **Kotlin was not compiled** and **no native build** (`expo prebuild` / Gradle / EAS) was run.
  - Android APIs used were checked against their documented signatures and API levels: `AudioFocusRequest.Builder` (26), `activePlaybackConfigurations` / `AudioPlaybackConfiguration.getAudioAttributes().usage` (26), `MediaPlayer.create(Context, int, AudioAttributes, int)` (24), `abandonAudioFocusRequest` (26), `Handler.postAtTime(Runnable, Object, long)`. Every path is gated on `SDK_INT >= 26`; app `minSdk` is 25.
  - The Expo DSL (`Name`, `Function`, `AsyncFunction` with a trailing `Promise`, `OnDestroy`, `appContext.reactContext`) was checked against the current Expo Modules API docs.
- **Production build:** no `npm run build` / EAS build. It is not applicable to this environment.
- **Manual:** no DHU (Desktop Head Unit) and no real-car check.
- **Blast-radius greps:** `ride_offer_alarm_channel_enabled` (the pattern mirrored), `setCountdown(`, `displayRideOfferNotification|dismissRideOfferNotification`, `useRideOfferSound|alertPrefsStore`, `drivers/config`, and `docs/known-forks.md` (no entries for the touched files).
- **Feature flag:** the tone is behind `android_auto_offer_tone_enabled`, default off. The expiry fix is deliberately un-flagged (see §8).
- **Review:** `spinr-dispatch-reviewer`, `spinr-edge-case-reviewer`, and `spinr-migration-reviewer` were run against the diff (migration: SAFE TO APPLY). Two findings were fixed:
  - **Silent offer (edge-case, blocker):** the native hard stop could fire while the module was still waiting out a nav prompt, with less than about 3 s of the offer left. It resolved `'cancelled'` without any JS supersede. The car then kept ownership while nothing played, and the phone loop and card stayed muted. **Fix:** a same-generation `'cancelled'` now takes the error path and hands back to the phone (`af99f77`).
  - **Stuck offer (dispatch, major):** the car-only expiry backstop bailed whenever the phone dashboard was mounted, even when it was backgrounded (the usual Android Auto case: the WS closes after 3 s and the countdown may not tick). **Fix:** it now defers only to a *foreground* phone screen, and re-checks every 2 s instead of giving up (`632c9ba`). A duplicate decline is rejected by the backend's decline guards (403/409, since the ride is no longer this driver's), and `declineRide` resets to idle either way.
  - Both have regression tests. `carOfferRing.test.ts` is 45/45 on the same shim, and a mutation check (reverting the `'cancelled'` fix) failed the new test.

### What was NOT verified

- Whether Android Auto actually routes `USAGE_ASSISTANCE_NAVIGATION_GUIDANCE` from a non-navigation-category car app to the car speakers, and whether it ducks media. This follows Google's navigation-app guidance and has not been observed.
- Whether AA suppresses the Notifee card's sound (the premise of BA-B). This is inferred.
- **Android 15+ background audio-focus denial.** The module plays unfocused and returns `playing_unfocused`. In that case music is not ducked and the tone may be quieter than music. The first result per car session is reported to Crashlytics/Sentry (`reason: car_offer_tone_result`) so real head units answer this.
- **Android 17 background-audio hardening.** A further restriction on background `MediaPlayer` without a foreground service could make the tone fail (`error` → hand back to the phone) or play silently. The silent case would not be detected by JS.
- Expo SDK 57 autolinking of a `modules/` local module without a `package.json`. It follows the documented default `nativeModulesDir: "./modules"` and the local-module template, but it has not been built. The `expo-module-gradle-plugin` build.gradle form was written from the local-module template, not copied from an SDK 57 install.
- **JS timers on a car-only launch.** The car-only expiry timer relies on JS timers running while the app is backgrounded. The existing car session makes the same assumption with its 60 s interval, and this change does not prove it.
- **Reconnect mid-offer on a car-only launch.** On disconnect, the hand back posts a loud reclaim card. If the car reconnects before the offer ends, the car rings again but that card is not re-muted, so both may sound until the offer ends. This is a rare window and was left unhandled.
- **Clock skew (accepted, parity with the phone):** the car deadline uses the server's `offer_expires_at` against the device clock. A forward-skewed clock expires the offer early. The phone screen (`app/driver/(tabs)/index.tsx:668-671`) computes expiry the same way, so this is existing exposure, not new.
- **Headless FCM and car session in the same JS context:** `backgroundMessaging` mutes the card by reading `isCarRingOwner()` from module state that `register.ts` sets. If some OEM ran the FCM handler in a fresh JS context, it would read `false`. The failure mode is a double ring, not silence. Check on a real head unit.
- **Call in progress:** with `blocked_call`, nothing rings on either the car or the phone for that offer; only the visual alert and card show. This is intentional: never ring over a call. It is tracked by the per-session telemetry.
- **No visual regression tooling** exists for driver-app. There are no UI changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag UPDATE; JS revert for the expiry)
- [x] Blast radius is stated, not assumed (§4 table from greps)
- [x] UX field filled for the behaviour changes, including the un-flagged expiry fix
