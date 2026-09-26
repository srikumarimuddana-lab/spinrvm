# Change Impact & Risk Log — iOS minimised ride-offer alert (15 s) + app-open handover, Android offer-tone tail

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (claude-sonnet-5) with the driver-app owner |
| Surface(s) | driver-app (iOS + one Android-only change) |
| Domain (Sentry tag) | dispatch / drivers |
| PR / commit link | not committed — working tree only (user handles all commits) |
| Related issue or gap ID | driver reports 2026-09-25: iPhone minimised offer sound "2–3 s, once, quiet"; Android brief tone after an expired offer |

Hard rule from the owner: **Android works today; iOS changes must not alter Android.** Section 4 states, per change, exactly what Android does.

## 1. Issue / gap identified

Three separate driver-reported symptoms:
1. **iOS, app minimised:** the offer sound plays ~2–3 s, once, and quietly.
2. **iOS, opening the app:** the push alert card is left in Notification Center; nothing about the push is handed over to the in-app tone.
3. **Android, opening the app after an offer expired:** the offer tone is heard for ~1–2 s.

## 2. Root cause

1. **Confirmed by code.** With the app minimised the only sound on iOS is the OS playing the APNs `sound: "ride_offer.caf"` (`backgroundMessaging.ts:313` deliberately skips the Notifee card on iOS; `reelect`'s `'os'` branch is a no-op on iOS). iOS plays a push sound once and never loops it. The file was 1.81 s, peak 0.81, RMS 0.107. Not caused by the 2026-09-25 Android Auto / alarm-channel work — that code is Android-only (`carConnected` false on iOS; `ride-offer-tone` module `platforms: ["android"]`; `loudChannelId()` returns early on iOS).
2. **Confirmed by code.** The APNs alert is a different notification id from Notifee's `ride-offer-current`, so `cancelNotification` never reached it.
3. **Confirmed in library source, NOT reproduced on a device.** expo-audio Android (`AudioModule.kt` `OnActivityEntersBackground`/`Foreground`) marks any playing player `isPaused = true` on background and replays every `isPaused` player on return. The JS `pause()` in `useRideOfferSound.stop()` never clears that flag, so a tone caught mid-clip resumes on return even if its offer expired meanwhile (`ride_offer.mp3` ≈ 1.9 s ≈ the reported 1–2 s). A second, independent cause was confirmed in review (Codex, #5878): an offer that expires while backgrounded is still in the store on return (its expiry event went to a closed socket, and `driverStore.declineRide` awaits the POST before clearing `incomingRide`, `driverStore.ts:698-715`), so the AppState re-election rings for it even with the native resume fixed. Both causes are fixed. An earlier version of this log said the guard had been dropped; that was wrong — it was restored.

## 3. Fix / remediation

1. `ride_offer.caf` regenerated: exactly 15.00 s (the offer window; the file is the only way to "repeat" on iOS), peak 0.98, RMS 0.206 (≈ +6 dB average, ≈ +2 dB peak), with a 50 ms fade-out so the mid-note cut ends at exactly 0 without a click.
2. New iOS-only `dismissDeliveredOfferAlerts(rideId?)` (`notifeeService.ts`) removes the delivered APNs offer alert(s) via `getDisplayedNotifications` → `cancelDisplayedNotifications`. Called from `useDriverDashboard` only at the two foreground-handover sites (`consumePendingOffer`, `reelect`'s `'app'` branch incl. its already-owned early return), only on iOS, only when `AppState` is `active`/`inactive`, and only for the ride being handed over.
3. **Stale-offer guard.** `isOfferStillLive` (`utils/offerLiveness.ts`) is checked once the `'app'` handover settles: ring only if the same ride is still `ride_offered` and `offer_expires_at` has not passed. No/unparseable deadline counts as live (legacy offers are not silenced). Applies to both platforms' `'app'` election.
4. `useRideOfferSound.stop()` on **Android, when the app is not in the foreground**, now releases the player (`pause()` + `remove()`), removing it from expo-audio's native resume list. `_getOrCreatePlayer()` rebuilds it on the next offer — from the admin-uploaded URL when one is set, not the bundled tone.

## 4. Risk & impact on existing functionality

**Blast radius: driver-app only. No backend, DB, migration, Stripe, or ride-state change.**

- **Android, change 1 (.caf):** none. `withRideOfferSound.js:27-62` copies only `ride_offer.mp3` to `res/raw`; the `.caf` goes only to the Xcode project; no JS `require()`s it.
- **Android, change 2 (iOS helper):** none. `dismissDeliveredOfferAlerts` returns immediately off iOS; call sites are `Platform.OS === 'ios'`-guarded. The Android arm of the `reelect` ternary is the original call with the same arguments, no extra await/microtask (independent review confirmed).
- **Android/iOS, stale-offer guard:** shared code in the `'app'` re-election. It only *withholds* a tone: a live offer (same ride, `ride_offered`, deadline not passed or absent) rings exactly as before. Residual risk is device-clock skew vs the server's `offer_expires_at` silencing the last seconds of an offer on the `'app'` election; the resume path already auto-declines on the same comparison (`index.tsx:671-688`), so this adds no new exposure beyond the Android Auto hand-back with the app in front.
- **Android, change 3 (release):** **this is a deliberate Android behaviour change**, scoped to `stop()` while `AppState !== 'active'`. Foreground `stop()` (in-app accept/decline) is unchanged — warm player retained. Consumers of `useRideOfferSound`/`stop()`: `useDriverDashboard` (all `offerSound.stop()` sites: WS offer cancel/expire/taken paths, the `incomingRide → null` effect, the `'os'`/`'car'` re-election) and `subscribeCarRingOwner` (stops when the car takes the ring, only released if backgrounded). Regression risk: after a background release, the next in-app tone rebuilds the player lazily — the same path the first offer of every session already takes — so the first tone after a background→foreground return with a live offer starts through a cold player. Not measured on a device.
- **iOS:** the removal must never delete the only card a killed-app driver has. iOS wakes a killed app in the background for an offer push (`content_available`) and the dashboard mounts with `rideState 'idle'`; the generic dismiss effect runs then. For that reason the removal is **not** in `dismissRideOfferNotification` and is foreground-gated; unit tests assert the generic dismiss and a silent post never remove delivered alerts.
- **Known limitation (inherent to iOS):** a push sound that has started **cannot be stopped** by the app; removing the card clears the card only. With a 15 s file, on a foreground handover the in-app tone can overlap the remaining APNs sound for up to ~15 s (was ~2–3 s), and the sound outlasts the offer if `ride_offer_timeout_seconds` (admin-configurable, `matching.py:431`) is below 15 or the push arrives late. Whether removing the card cuts the sound on-device is **unverified**.
- iOS `foregroundPresentationOptions.sound = !muted` and the local loud iOS post (`notifeeService.ts` ios `sound: 'ride_offer.caf'`) now also use the 15 s file — same file, longer.

## 5. User-experience effect

- **iOS driver, app minimised:** a longer (15 s), louder repeating chime instead of a ~2 s blip. Volume remains bounded by the phone's ringer volume; a much louder alert needs Apple's Critical Alerts entitlement (`IOS_CRITICAL_ALERTS_ENABLED`, off, entitlement not held).
- **iOS driver, opens the app mid-offer:** the push card is cleared; the sound may keep playing to its end.
- **Android driver:** the 1–2 s tone after reopening the app on an expired offer should no longer play. No other visible change. Visible mid-session: yes for any driver already online once the new build/OTA lands.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/scripts/generate_ride_offer_caf.py` | 15 s tiled, peak-normalised, slower decay | iOS never loops a push sound |
| `driver-app/assets/sounds/ride_offer.caf` | regenerated (160 KB → 1.32 MB) | same |
| `driver-app/services/notifeeService.ts` | new exported `dismissDeliveredOfferAlerts(rideId?)` | remove delivered APNs alert on handover |
| `driver-app/hooks/useDriverDashboard.ts` | lazy binding, foreground-gated helper, 3 call sites; stale-offer guard on the `'app'` handover | iOS handover; expired-offer tone |
| `driver-app/utils/offerLiveness.ts` | new pure `isOfferStillLive` | stale-offer guard |
| `driver-app/__tests__/utils/offerLiveness.test.ts` | new, 8 tests | guard coverage |
| `driver-app/hooks/useRideOfferSound.ts` | `_releasePlayer`, `_createRemotePlayer`, URL-aware `_getOrCreatePlayer`, Android background release in `stop()` | native resume-on-foreground bug |
| `driver-app/__tests__/services/notifeeService.test.ts` | +7 tests (iOS filter/no-op Android/mount-trap negatives/rideId filter) | coverage |
| `driver-app/hooks/__tests__/useRideOfferSound.release.test.ts` | new, 6 tests | Android release scoping, URL rebuild, iOS/foreground unchanged |

## 7. Before / after

```ts
// Before — stop(): JS pause only; expo-audio's native isPaused flag survives
try { _player?.pause(); } catch {}
// After — Android + app not in front: also release
try { _player?.pause(); } catch {}
if (Platform.OS === 'android' && AppState.currentState !== 'active') _releasePlayer();
```

```ts
// Before — 'app' handover (Android arm is byte-for-byte this)
void _surfaceOfferNotification(offer, true).finally(() => offerSound.play());
// After
const handover = Platform.OS === 'ios'
  ? _removeDeliveredOfferAlertsIfForeground(offer.ride_id).then(() => _surfaceOfferNotification(offer, true))
  : _surfaceOfferNotification(offer, true);
void handover.finally(() => offerSound.play());
```

## 8. Rollback plan

No feature flag exists for driver-app audio/notification behaviour (not `app_settings`-backed) — none added, to keep the change minimal; noted as a gap.
- **JS changes (2, 3):** revert and ship as an OTA update (no native change) — drivers pick it up on next launch.
- **`.caf` (1):** a bundled resource; reverting needs a new iOS build (OTA cannot ship it). Fallback if a device test shows the 15 s tail is unacceptable: regenerate with a shorter `RING_SECONDS` in the generator and rebuild.
- No data-layer state is touched, so no data remediation is needed.

## 9. Verification performed

- [x] Automated: 11 suites / 149 tests pass (`offerLiveness`, `notifeeService`, `useRideOfferSound.release`, `useRideOfferSound.carOwner`, `useDriverDashboard.socketLifecycle`/`.chat`/`.alwaysLocationGate`/`.wsSenders`, `backgroundMessaging` iOS+Android, `pendingRideOffer`).
- [x] `.caf` parsed: 15.00 s, 44.1 kHz mono PCM, peak 0.98, RMS 0.206 (old: 1.81 s, 0.81, 0.107).
- [x] Library behaviour read from source, not assumed: Notifee iOS lists delivered remote alerts and removes by identifier (`NotifeeCore.m`); expo-audio Android background pause/resume + `remove()` deregistering (`AudioModule.kt`).
- [x] Blast-radius grep: consumers of `useRideOfferSound`, `dismissRideOfferNotification`, `_surfaceOfferNotification`, `.caf` references (plugin, backend `features.py`/`push_retry.py`, `notifeeService`).
- [x] Independent reviewer (`spinr-realtime-reliability-reviewer`, Opus) over the diff. Its findings were applied: dropped the unproven expiry guard and fixed the real Android cause; added the iOS already-owned-path removal; added per-ride filtering.
- [x] `eslint`: 0 errors in changed files apart from 6 pre-existing React-compiler errors in `useDriverDashboard.ts` on untouched code; `tsc --noEmit`: no errors in changed files.
- [ ] **Production build (`eas build`/`expo export`) was NOT run.**

## 10. What was NOT verified

- **No device run of any of this.** Not confirmed on an iPhone that the 15 s file plays fully, is audibly louder, or that removing the card stops or does not stop the sound; not confirmed on Android that the tail is gone.
- The Android root cause (native resume) is established from library source and matches the symptom, but the bug was not reproduced before/after.
- **Hook-level behaviour is untested:** `reelect`/`consumePendingOffer` iOS call sites (including the already-owned early-return removal) have no unit test; only the helper and its gating rules do. Suggested minimal test (extend `useDriverDashboard.socketLifecycle.test.ts`): iOS background-mount handover then `'active'` → removal called; Android → never called.
- How iOS mixes two overlapping notification sounds, and how Notifee/Firebase foreground handlers chain on iOS, were not checked.
- No visual/snapshot tooling exists for driver-app; nothing visual changed.
- `ride_offer_timeout_seconds` < 15 and late-push cases (sound outliving the offer) are reasoned about, not tested.
