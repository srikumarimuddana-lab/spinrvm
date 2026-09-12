# Change Impact & Risk Log — locked-screen background GPS collapse

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers (capture path feeding rides + safety/insurance-period evidence) |
| Related issue or gap ID | Live-testing report 2026-09-12; rides `SPR-VWSR6C`, `SPR-4UG6AC` |

## 1. Issue / gap identified

Two live test rides in Regina on 2026-09-12 recorded almost no GPS. The admin
route view showed **23% GPS observed / 77% inferred** for the iOS ride and
**0% observed / 100% inferred** for the Android (Android Auto) ride, so billed
distance was reconstructed from the planned route rather than measured, and the
Period 2/3 insurance rows have no location trace behind them.

## 2. Root cause

Every value this module persists is read back from the **headless background
task**, which by design runs while the screen is locked. All of those writes used
`expo-secure-store`'s default accessibility, `WHEN_UNLOCKED`
(`kSecAttrAccessibleWhenUnlocked`) — documented in the package's own types as
`@default SecureStore.WHEN_UNLOCKED`.

On iOS that makes the item **unreadable the moment the device locks**. The ~60 s
self-heal inside the background task then read `TRIP_ACTIVE_KEY`, got nothing,
and resolved it as a plain boolean:

```ts
tripActive = (await SecureStore.getItemAsync(TRIP_ACTIVE_KEY)) === 'true';  // → false
await _applyTaskOptions(tripActive ? TRIP_CADENCE : IDLE_CADENCE);          // → IDLE
```

So the repair path *caused* the outage it exists to prevent: it re-pinned a live
ride to `IDLE_CADENCE` once a minute for the whole trip.

Measured confirmation from `driver_location_history`, split by `source`:

| Ride | background points | interval | cadence in force |
|---|---|---|---|
| SPR-YNYA93 (2026-09-12 01:31, unlocked) | 203 over 982 s | 1 per 4.8 s | TRIP (4 s) ✓ |
| SPR-EG7X86 (2026-09-12 01:52, unlocked) | 137 over 841 s | 1 per 6.1 s | TRIP ✓ |
| SPR-VWSR6C (18:11, **screen locked**) | 18 over 967 s | **1 per 53.7 s** | IDLE ✗ |
| SPR-4UG6AC (19:14, **Android Auto**) | 0 | — | dead ✗ |

`ride_location_gap_events` agrees: last night every gap is `resolved`; on
`SPR-4UG6AC` both gaps are `unresolved_at_completion`, the first beginning at
`19:14:31.804178` — the exact microsecond of `ride_started_at`.

No capture-path code changed in between (last such commit `6ba7f0fcd`, 19:07 MDT;
the good rides ran at 19:31 and 19:52 MDT). This was a latent defect exposed by
locking the screen, not a regression from a recent commit.

## 3. Fix / remediation

1. Store every background-read key with
   `keychainAccessible: AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`, so the headless task
   can still read them while locked. `THIS_DEVICE_ONLY` additionally keeps these
   credentials out of a restore onto another device.
2. Make the self-heal's trip check **tri-state** (`true` / `false` / unknown).
   Unknown now re-asserts the cadence already in force; it can never downgrade a
   live ride to idle.
3. Stop `updateBackgroundLocationCadence` swallowing a failed apply: the
   Android-backgrounded foreground-service rejection now parks a foreground
   replay and propagates, and the call site logs instead of `.catch(() => {})`.

## 4. Risk & impact on existing functionality

**Blast radius: driver-app only, single module plus one call site.** Consumers of
the changed functions were enumerated: `updateBackgroundLocationCadence` has one
caller (`hooks/useDriverDashboard.ts`), `reassertDispatchTask(Unlocked)` is called
from the background task self-heal and `lib/androidAuto/carLocationTask.ts`.

- `shared/store/authStore.ts` is **not** touched, so rider-app is unaffected.
- iOS applies keychain accessibility **at write time**, so values written by an
  older build keep `WHEN_UNLOCKED` until rewritten. `TRIP_ACTIVE_KEY` is rewritten
  on every trip-phase transition, so it self-corrects on the first ride after the
  update; no migration needed.
- `AFTER_FIRST_UNLOCK` is a deliberate, standard relaxation for background tasks:
  the item is still encrypted at rest and unreadable until the device has been
  unlocked once since boot. It is a weaker guarantee than `WHEN_UNLOCKED` and is
  recorded here as an accepted trade-off, made because the alternative is losing
  the regulatory GPS trace.
- Behaviour with the screen unlocked is unchanged.

## 5. User-experience effect

Driver-facing but invisible in the UI: no screen, copy, or interaction changes.
The observable effect is that a trip driven with the phone locked keeps recording
a real route, so rider/driver receipts and the admin route view show measured
rather than inferred distance. Nothing changes mid-session for a driver already
online; the new cadence applies from the next trip-phase transition.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/backgroundLocation.ts` | `KEYCHAIN_BACKGROUND_READABLE` applied to all 4 background-read writes; tri-state trip resolution + `_lastAppliedCadence`; cadence-apply failure no longer swallowed | the locked-screen root cause and its two safety nets |
| `driver-app/hooks/useDriverDashboard.ts` | `.catch(() => {})` → logged handler | a failed tighten was invisible |
| `driver-app/jest.setup.js` | added keychain constants to the global SecureStore mock | without them a `keychainAccessible` assertion compares `undefined` to `undefined` |
| `driver-app/utils/__tests__/backgroundLocation.test.ts` | local mock constants; updated + 1 new accessibility test | pin the fix |
| `driver-app/__tests__/utils/backgroundLocation.reassert.test.ts` | 5 new cadence-selection tests | pin "never downgrade a live trip" |

## 7. Before / after

```ts
// Before — any unreadable flag means "no trip", so the heal applies IDLE.
let tripActive = false;
try {
  tripActive = (await SecureStore.getItemAsync(TRIP_ACTIVE_KEY)) === 'true';
} catch { /* Unreadable flag → idle cadence */ }
await _applyTaskOptions(tripActive ? TRIP_CADENCE : IDLE_CADENCE);
```

```ts
// After — unknown re-asserts what is already in force; only a definitive
// "no trip" drops to idle.
let tripActive: boolean | null = null;
try {
  tripActive = (await SecureStore.getItemAsync(TRIP_ACTIVE_KEY)) === 'true';
} catch { /* still unknown */ }
const cadence =
  tripActive === null ? (_lastAppliedCadence ?? IDLE_CADENCE)
  : tripActive ? TRIP_CADENCE : IDLE_CADENCE;
await _applyTaskOptions(cadence);
```

## 8. Rollback plan

`git revert` of this commit, **plus a new build** — this is native app code, so
there is no config/flag that disables it without shipping a binary. Stated plainly
because it means rollback is not same-day. No data migration is involved and no
already-written row is altered, so a revert is otherwise clean: the keychain
attribute simply reverts to the default on the next write.

## 9. Verification performed

- [x] `npx tsc --noEmit` on driver-app — clean.
- [x] Targeted suites: `backgroundLocation.test.ts` + `backgroundLocation.reassert.test.ts` — **68/68 pass**, including 6 new tests written for this fix.
- [x] Full driver-app suite run **before and after**, to separate pre-existing failures from new ones: baseline (changes stashed) **15 failed suites / 16 failed tests**; with the fix **12 / 13**. No new failure introduced; `hooks/__tests__/onlineResync.test.ts` and the `__tests__/app/*Screen` timeouts fail on baseline too.
- [x] Blast-radius grep: every caller of the changed functions enumerated (above); confirmed `shared/store/authStore.ts` untouched so rider-app is out of scope.
- [x] Root cause confirmed against production data (`driver_location_history` grouped by `source`, `ride_location_gap_events`) rather than inferred from code alone.

## 10. What was NOT verified

- **Not verified on a real device.** The decisive test — a locked-screen trip
  producing ~4 s-spaced background fixes instead of ~54 s — requires a build on a
  physical phone. driver-app has no visual or device regression tooling at all, so
  this is reasoned about and unit-tested, not observed.
- **The Android Auto 0% case is NOT fixed by this change.** That ride also hit an
  ANR (`"Spinr Driver isn't responding"` on the head unit) plus the documented
  two-registered-tasks native dedup. Tracked separately.
- **`shared/store/authStore.ts` still writes `fg_access_token` / `token_expires_at`
  with the default `WHEN_UNLOCKED`.** `getBackgroundAuthToken()` falls back to
  those when `bg_access_token` is absent, so uploads can still defer while locked
  until the background copy exists. Deliberately excluded here because that file is
  shared with rider-app and changing auth-token storage for both apps warrants its
  own review. This is the most likely cause of the Sentry issue
  `CRIMSON-SMOKE-7445-TJ` ("Points fall outside completed ride retention window").
- No staging run; there is no staging environment for the mobile apps.

## 11. Sign-off

- [x] Rollback plan is concrete, and its "needs a new build" limitation is stated rather than implied
- [x] Blast radius stated and enumerated, not assumed
- [x] No silent behaviour change to a shipped flow — UX field completed; behaviour with the screen unlocked is unchanged
