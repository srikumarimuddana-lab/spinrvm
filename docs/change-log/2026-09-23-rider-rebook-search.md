# Change Impact & Risk Log

## PR review follow-up — Expo Router compatibility

- Issue/root cause: the new `useIsFocused` import used external React Navigation,
  which Expo Router on SDK 57 rejects. PR native export and web E2E export both failed.
- Change: repoint the hook and its screen-test mock to `expo-router/react-navigation`.
  The focus check and customer-visible behaviour are unchanged.
- Alternative considered: disabling the bundler compatibility check. Rejected because
  the supported import preserves the guard and the correct navigation context.
- Blast radius: `confirm-pickup.tsx` is the only application caller of `useIsFocused`;
  its focused/unfocused tests remain the relevant behaviour checks. No sibling driver
  hook needs changing, and no database, payment, or native dependency changes are made.
- Before: import from `@react-navigation/native`. After: import from
  `expo-router/react-navigation` in the screen and its mock.
- Verification: failing native/web exports observed on PR head `2015b3f79`.
  Follow-up verification results are recorded below before push.
- Rollback: revert this follow-up commit and redeploy the preceding bundle; this
  reintroduces the known export failure. No live data repair is needed.
- Not verified: device behaviour; a successful JS export is not a signed native
  build or Android/iOS device test.

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Cursor agent |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | `8bd06e5ff`, `6af3d11f6`, `da9d8051f` |
| Related issue or gap ID | Rider booking funnel: stale Where to?, blank confirm-pickup, second search leaves the searching screen |

## 1. Issue / gap identified

After a rider cancels a driver search and taps Where to? again, the previous pickup and destination are still filled in. Tapping the pickup X blanks the screen or jumps forward. Booking again, for Economy or XL, leaves Looking for a driver and returns home or crashes.

## 2. Root cause

`clearRide()` keeps the trip draft on purpose, because wiping it there used to bounce the vehicle screen home. The destination screen copied that draft into the inputs, and the GPS bind only ran when `userLocation` changed. The pickup X set pickup to null. `confirm-pickup` called `router.back()` during render and returned nothing, including when a copy of that screen was still mounted underneath. `createRide` cleared `_clearedRideId`, so a late cancel or an empty `/rides/active` read for the previous ride cleared the new ride and navigated home. Economy and XL share that path.

## 3. Fix / remediation

A new Where to? from home, with no live ride, calls `resetBookingDraft()`. The destination screen rebinds Current Location on focus when the draft pickup is empty. The pickup X restores Current Location when GPS is known, and otherwise clears the field without navigating. Confirm pickup goes back only while that screen is focused, and shows a loader instead of an empty view. `createRide` leaves the previous cancel latch in place. `fetchActiveRide` does not `clearRide()` a local ride whose id differs from that latch. A `ride_cancelled` socket event or push leaves the screen only when it is for the ride currently on screen.

`clearRide()` itself is unchanged.

## 4. Risk & impact on existing functionality

- Blast radius: rider-app booking funnel only. No backend, migration, wallet, Stripe, or insurance-period write.
- `clearRide` callers (cancel, receipt, socket dismiss of the current ride) still keep pickup and dropoff. The draft is dropped only from home's Where to? and quick actions, and only when `currentRide` is null.
- `fetchActiveRide` still adopts a different active ride when nothing is stored locally, and still clears a local ride when the latch is empty or matches that ride. The new skip is only "local ride id differs from a set latch."
- `shouldLeaveScreenForRideCancelled` is used by `useRiderSocket` and `_layout`'s notification router. A driver cancel of the ride on screen still toasts, clears, and goes home. A cancel with no ride id still does that when a ride is current. A cancel for any other id, or when no ride is current, does not.
- `ride-completed` still reads `_clearedRideId` with the same meaning.
- Chosen over wiping the draft inside `clearRide`: that wipe is the change that previously stuck ride-options on a loading state and bounced home.

## 5. User-experience effect

- Riders. Drivers and admins see no change.
- Visible mid-session: after cancel search, the next Where to? is a new trip (current location, empty destination, saved places and recents as rows). The pickup X stays on the destination screen. Looking for a driver stays up until that ride's status changes.
- No notification copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/store/rideStore.ts` | `resetBookingDraft`; `createRide` keeps the latch; `fetchActiveRide` skips clearing a newer local ride | Fresh search without reopening the old bounce, and a second booking survives a stale cancel |
| `rider-app/app/(tabs)/index.tsx` | `openNewSearch` resets the draft when no ride is live | Where to? and shortcuts start a new trip |
| `rider-app/app/search-destination.tsx` | Focus rebind; pickup X restores Current Location | The screen can stay mounted, and GPS does not re-fire on its own |
| `rider-app/app/confirm-pickup.tsx` | Focused-only back, loader instead of `null` | Stops the white screen and a background pop |
| `rider-app/hooks/useRiderSocket.ts` | `ride_cancelled` uses the shared guard | A late cancel for the previous ride does not go home |
| `rider-app/app/_layout.tsx` | Same guard on the cancel push | The push path had no ride-id check |
| `rider-app/utils/rideCancelSignal.ts` | Shared decision for both cancel paths | One rule for the socket and the push |
| Rider Jest files listed in section 9 | Cases for the draft, the X, confirm-pickup focus, and the latch | The old tests mocked the store and stayed green while the bugs were live |

## 7. Before / after

```tsx
// Before — confirm-pickup, during render
if (!pickup) {
  router.back();
  return null;
}
```

```tsx
// After — back only while this screen is focused; loader until then
useEffect(() => {
  if (isFocused && !pickup) goBack();
}, [isFocused, pickup, goBack]);
```

```ts
// Before — createRide success
set({ currentRide: ride, /* ... */, _clearedRideId: null });
```

```ts
// After — previous ride's latch stays until fetchActiveRide adopts the new ride
set({ currentRide: ride, /* ... */ });
```

## 8. Rollback plan

This diff does not write Stripe, wallets, or ride rows. Rollback is a rider-app redeploy of the previous JS bundle (OTA or store build) from reverting `8bd06e5ff`, `6af3d11f6`, and `da9d8051f`. No feature flag: the three bugs are the shipped path, and leaving them on behind a flag keeps the blank screen and the home bounce. No migration.

## 9. Verification performed

- [x] Automated tests, `--runInBand`: `rideStore.test.ts`, `rideStore.cancel-flicker.test.ts`, `rideStore.clearedRide-refetch.test.ts`, `rideCancelSignal.test.ts`, `searchDestinationScreen.test.tsx`, `searchDestinationPinIntegrity.test.tsx`, `confirmPickupScreen.test.tsx`, `homeScreen.test.tsx`, `useRiderSocket.chat.test.ts`, `useRiderSocket.reconnect.test.ts`
- [ ] Manual repro on a device or simulator
- [x] Blast radius: `clearRide` contract test left unchanged; `resetBookingDraft` is only called from home; cancel navigation goes through `shouldLeaveScreenForRideCancelled`
- [x] Ride-state client guards only. No money math, RLS, or PIPEDA field added
- [ ] Feature flag — not used; see rollback

## 10. Sign-off

- [x] Rollback is a client bundle revert. No live money or ride-row repair
- [x] Blast radius is the rider booking funnel
- [x] UX change is the three fixes above

## 11. What was NOT verified

Not run against a device, Expo Go, or a production EAS build. Rider-app has no visual-regression suite, so the blank-screen fix is the confirm-pickup render test, not a screenshot. Not tested against live Supabase. A parallel Jest run (without `--runInBand`) hit per-test timeouts while several files compiled at once; the same files passed in one process.
