# Change Impact & Risk Log — C20 mobile lint debt, rider-app, round 4 (tier 4, `react-hooks/exhaustive-deps`)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | `claude/c20-lint-tier4-rider-app` |
| Related issue or gap ID | ACTION_ITEMS.md C20 "Mobile lint debt under the SDK 57 ruleset" (round 4) |

This is round 4 of C20, closing `react-hooks/exhaustive-deps` for rider-app
— the category every prior round explicitly deferred and flagged as the
highest-risk of all C20 categories ("saved for last, requiring the smallest
batches and most scrutiny"). driver-app was not touched (a sibling
session/agent owns that surface in parallel, in a separate worktree).

## 1. Issue / gap identified

73 `react-hooks/exhaustive-deps` findings across 27 files in rider-app,
confirmed via a fresh `npx eslint . --format json` run against the actual
starting commit (matched the task brief's stated 73 exactly — no drift to
reconcile). This rule flags `useEffect`/`useCallback`/`useMemo` hooks that
reference a component-scope value not listed in their dependency array.

## 2. Root cause

Two different underlying situations, conflated by the same lint rule (this
is *why* the category is risky, not just a description of the fix):

1. Genuine stale-closure bugs — an effect/callback should react to a value
   changing but doesn't, silently using outdated data (e.g. a "back button
   cancel" dialog showing a stale fee, an Android notification showing a
   stale vehicle plate).
2. Deliberate one-time-only / guarded patterns where the "missing"
   dependency was intentionally left out because including it would
   re-trigger the effect in an unwanted way (extra GPS/location-permission
   prompts, a map re-fit on every ride poll instead of only on a real
   position change, overwriting a rider's in-progress text edit).

Treating every finding as (1) — "just add the dep" — would have introduced
real bugs into situation (2). Treating every finding as (2) — "just
suppress it" — would have left situation (1)'s bugs in place. Every one of
the 73 was read in full context (effect/callback body + the referenced
value's own definition, plus surrounding code/comments for author intent)
to tell which situation it was, per CLAUDE.md's explicit warning that this
rule "can hide real bugs."

## 3. Fix / remediation

Per-finding, categorized a/b/c/d (see `ACTION_ITEMS.md`'s C20 round 4
bullet for the full breakdown with file-level detail; summarized here):

- **(a) safe to add directly — 33 of 73**: the missing value was already
  stable (zustand store action/selector, `router` — confirmed a stable
  singleton by reading expo-router's source, a `useRef` value, a
  `useAnimatedValue`-created `Animated.Value`, or a primitive pure
  derivation of an already-tracked dep). No wrapping needed.
- **(b) real stale-closure bug or genuine gap, fixed safely — 37 of 73**:
  wrapped a plain function in `useCallback` with its own correct deps
  before adding it (11 sites); rewrote a whole-object truthy check into a
  narrowed field-level read so effects didn't need to depend on an
  entire ride/driver object that updates on every poll (7 sites, including
  a `buildContent()` signature change in `useRideStatusNotification.ts`);
  added a genuinely-missing reactive value after confirming a guard makes
  it safe (`riderBill`/`cancellationFee`/`fare` into 4 cancel-dialog
  effects, `selectedIndex`/`stops`/`activeCompanyId` into 3 ride-options.tsx/
  payment-confirm.tsx effects, `driverOriginSnapshot` into 1
  driver-arriving.tsx effect); added 2 new **latest-value refs**
  (`pickupValueRef`/`dropoffValueRef` in search-destination.tsx) after
  tracing a real regression risk in that same file's own text-input
  handler; added `userLocation` to a mount effect after confirming (via
  grep) it's never reset to null elsewhere in the codebase.
- **(c) intentional exclusion, narrow suppression — 2 of 73** (+1 hybrid,
  see below): `pick-on-map.tsx` (re-adding would re-prompt for location
  permission on spurious re-renders); `search-destination.tsx`'s
  stops-sync effect (the whole `stops` array reference changes on every
  keystroke-triggered `updateStop()` call, confirmed in the same file);
  `FreeCancelTimer.tsx`'s `secondsLeft` (would tear down/recreate its
  countdown `setInterval` every tick) — this finding also named `onExpire`,
  which WAS added (category b, after stabilizing the one caller that
  passes it), so it's a hybrid b+c resolution for one finding.
- **(d) suspicious / needs a human decision — 1 of 73, left UNFIXED (not
  suppressed)**: `rider-app/app/work-profile.tsx:90` — see section 4.

## 4. Risk & impact on existing functionality

- **Blast radius**: 30 files touched across 17 commits (≤3 files each,
  smaller batches than prior C20 rounds per this round's task
  instruction). All rider-app client-side only — no backend, no
  `shared/`, no migration. Two of the touched files are test files
  (`hooks/__tests__/useRiderSocket.reconnect.test.ts`,
  `.chat.test.ts`) — see the useRiderSocket entry below for why.
- **`hooks/useRiderSocket.ts`** (shared by 4 consumers: `ai-assistant.tsx`,
  `app/_layout.tsx`, `ride-status.tsx`, `store/rideStore.ts`) — grepped all
  4, none reads anything beyond this hook's unchanged public return value
  (`{ connectionState, wsConnected, sendMessage }`). The 3 findings fixed
  here (adding `router`/`setConnectionState`, both stable) do not change
  `connect`/`disconnect`/`handleMessage`'s own stability, so the
  connect/disconnect lifecycle effect (already carrying detailed comments
  from round 3 about needing `connect`/`disconnect` to stay stable) is
  unaffected. **This fix broke a test** (see section 9) — the test's mock
  was the bug (an unrealistic `useRouter()` mock returning a fresh object
  per call), not the production code; fixed the mock to match real
  behavior rather than reverting the production fix.
- **Cancellation-fee dialog fixes** (driver-arrived.tsx, driver-arriving.tsx,
  ride-in-progress.tsx, ride-status.tsx) — each now shows the current
  fee/fare if it updates without a ride-status change also happening in
  the same tick. This is a **display-text freshness fix on a
  cancel-confirmation dialog**, not a change to what fee is actually
  charged (the charge itself is always server-computed at cancel time;
  these dialogs are advisory text only). No ride-state-machine transition,
  dispatch-offer logic, or wallet/Stripe code path was touched.
- **`useRideStatusNotification.ts`** — `buildContent()`'s signature changed
  from `(status, ride, driver, etaSeconds)` to flat scalar params. Grepped
  for other callers: none (single call site, inside this hook's own
  effect). The Android notification this hook posts will now correctly
  update when vehicle color/make/model, license plate, dropoff address, or
  the pickup OTP change without a status/id/driver-name change also
  happening — previously it could show stale text in that narrow case.
- **`search-destination.tsx`** — the highest-risk single file this round.
  Traced `handleTextChange` (the pickup/dropoff/stop text-input handler)
  before touching any of this file's 6 effects, because it nulls out
  `pickup`/`dropoff` in the store the instant typed text diverges from the
  stored address (existing "orphan the pin" behavior, not introduced this
  round). Two effects (GPS-sets-pickup, map-pick-return) would have
  silently overwritten the rider's in-progress typing if `pickup`/`dropoff`
  were added as plain deps — fixed via latest-value refs instead, which
  read the current value without making it a re-trigger. The stops-sync
  effect has the same shape (`updateStop()` also nulls a stop's address on
  divergent typing) — suppressed instead of ref'd, since it doesn't need
  the "read at effect-run time" behavior the other two do (kept the
  existing `stops.length`-only key). Verified against
  `__tests__/searchDestinationPinIntegrity.test.tsx`, which specifically
  exercises the "typing clears the stale pin" scenario — 4/4 passing after
  the change.
- **No interaction with any of the 16 backend background loops** — this is
  a pure frontend PR.
- **No ride-state-machine transitions changed.** Every touched effect is
  rider-side UI/display state, a WebSocket message handler's dependency
  array (not its logic), a map-fit/animation trigger, a data-fetch
  trigger, or a text-input interaction.

## 5. User-experience effect

- **Visible, narrow text-freshness improvements** (not new features): the
  cancellation-fee/fare text on 4 screens' "Keep ride / Cancel" dialogs now
  stays current if the fee updates without a status change; the Android
  live-ride notification (`useRideStatusNotification.ts`) now stays
  current on vehicle/plate/dropoff/OTP changes in the same narrow case;
  `ride-options.tsx`'s available-promo list now recomputes when the rider
  switches vehicle type; the map on `ride-options.tsx` now refits when a
  stop is added/removed and on `driver-arriving.tsx` now refits on device
  rotation.
- **No visible change** to the vast majority of fixes — most are lint
  honesty (adding an already-stable dependency to a dep array changes
  nothing observable) or narrow suppressions (explicitly chosen to
  preserve existing behavior).
- **Nothing visible mid-ride in the dispatch/payment sense** — no fare
  calculation, Stripe flow, or ride-state transition changed. The
  cancel-dialog text refreshes are the closest thing to a mid-session
  visual change, and they only make already-correct-at-render-time text
  refresh more promptly, not change what's charged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/(tabs)/activity.tsx` | Hoisted `ACTIVITY_TTL_MS` to module scope; wrapped `getStatusText`/`getVehicleType`/`handleRidePress` in `useCallback` | exhaustive-deps (a) + (b) |
| `rider-app/app/_layout.tsx` | Added stable zustand actions to mount-init effect deps | exhaustive-deps (a) |
| `rider-app/app/chat-driver.tsx` | Added `CHAT_STORAGE_KEY`/`router`/`setChatMessages` to deps | exhaustive-deps (a) |
| `rider-app/app/confirm-pickup.tsx` | Added `originalLat`/`originalLng` to deps | exhaustive-deps (a) |
| `rider-app/app/driver-arrived.tsx` | `handleCancelPress` → `useCallback`; effect deps rewritten to avoid whole-object `currentRide`/`currentDriver` reads | exhaustive-deps (a) + (b) |
| `rider-app/app/driver-arriving.tsx` | Rewrote 3 whole-object checks to narrow field reads; `handleCancel`/`performCancel` → `useCallback`; added `handleFreeCancelExpire` useCallback for FreeCancelTimer's `onExpire` prop | exhaustive-deps (a) + (b) |
| `rider-app/app/index.tsx` | Added `router` to deps | exhaustive-deps (a) |
| `rider-app/app/legal.tsx` | `fetchLegalText` → `useCallback` | exhaustive-deps (b) |
| `rider-app/app/pick-on-map.tsx` | Narrow suppression on mount effect | exhaustive-deps (c) |
| `rider-app/app/otp.tsx` | Added `phoneNumber`/`router`/`dotAnims` to 3 effects' deps | exhaustive-deps (a) |
| `rider-app/app/payment-confirm.tsx` | Added stable actions to 2 effects; extracted `firstCorporateAccountId` for the corporate-sync effect | exhaustive-deps (a) + (b) |
| `rider-app/app/ride-completed.tsx` | Added stable deps to 3 effects; payWithCard retry effect now calls `handleSubmitRef.current` instead of `handleSubmit` directly | exhaustive-deps (a) + (b) |
| `rider-app/app/ride-details.tsx` | `fetchRide` → `useCallback` | exhaustive-deps (b) |
| `rider-app/app/saved-places.tsx` | `loadData` → `useCallback` | exhaustive-deps (b) |
| `rider-app/app/scheduled-rides.tsx` | `loadRides` → `useCallback` | exhaustive-deps (b) |
| `rider-app/app/ride-in-progress.tsx` | Rewrote map-fit effect to avoid `currentDriver` alias; added stable deps to 4 effects; added `riderBill`/`currentRide?.id` to back-button effect | exhaustive-deps (a) + (b) |
| `rider-app/app/ride-options.tsx` | `handleFetchEstimates` → `useCallback`; added stable deps + `selectedIndex`/`stops`/`mapBottomInset`/`selectedVehicle`/`activeCompanyId`/`corporateAccounts` across 7 effects; added `scaleAnim`/`imageSizeAnim` in the `VehicleCard` subcomponent | exhaustive-deps (a) + (b) |
| `rider-app/app/ride-status.tsx` | Hoisted `offerExpiresAt`/`offerTimeoutSeconds`/`cancellationFee`/`freeCancelSecondsLeft` to component scope; rewrote fetch-poll effect's whole-object check; `handleBackPress`/`performCancel` → `useCallback`; added `pulseAnim`/`dotAnim` | exhaustive-deps (a) + (b) |
| `rider-app/app/ride-tracking-webview.tsx` | `fetchTrackingUrl` → `useCallback` | exhaustive-deps (b) |
| `rider-app/app/wallet.tsx` | Added stable actions to mount effect | exhaustive-deps (a) |
| `rider-app/app/work-allowance-request.tsx` | Added `fetchRequests` to deps | exhaustive-deps (a) |
| `rider-app/app/work-profile.tsx` | Added `fetchBalance` to one effect; added `TODO(C20)` comment (not suppressed) to the other, left unfixed | exhaustive-deps (a) + (d) |
| `rider-app/app/search-destination.tsx` | Rewrote 2 effects' whole-object checks; added `pickupValueRef`/`dropoffValueRef`; added stable deps + `params.mapPickAddress`/`params.mapPickField`/`userLocation`/`locationReady` across effects; narrow suppression on stops-sync effect; removed a now-unused `set-state-in-effect` disable directive | exhaustive-deps (a) + (b) + (c) |
| `rider-app/components/FreeCancelTimer.tsx` | Added `onExpire` (now stable via its one caller); narrow suppression on `secondsLeft` | exhaustive-deps (b) + (c) |
| `rider-app/components/SchedulePicker.tsx` | Added `slideAnim`/`fadeAnim` to deps | exhaustive-deps (a) |
| `rider-app/components/Toast.tsx` | `hide` → `useCallback`; narrowed 2 effects' whole-object `current` checks; added `current?.duration` | exhaustive-deps (a) + (b) |
| `rider-app/hooks/useRideStatusNotification.ts` | `buildContent()` signature changed to flat scalar params; effect deps narrowed to the exact fields read | exhaustive-deps (b) |
| `rider-app/hooks/useRiderSocket.ts` | Added `router`/`setConnectionState` to 3 useCallbacks | exhaustive-deps (a) |
| `rider-app/hooks/__tests__/useRiderSocket.reconnect.test.ts` | Fixed `expo-router` mock to return a stable object (was fresh per call) | Test bug this round's own verification caught — see section 9 |
| `rider-app/hooks/__tests__/useRiderSocket.chat.test.ts` | Same mock fix | Same |
| `ACTION_ITEMS.md` | C20 section: added "Round 4 (rider-app)" bullet | Required by CLAUDE.md |
| `docs/change-log/2026-08-12-c20-lint-tier4-rider-app.md` | New (this file) | Required Change Impact Log |

## 7. Before / after

```tsx
// Before (hooks/useRideStatusNotification.ts) — needs the whole objects
function buildContent(status: string, ride: any, driver: any, etaSeconds: number | null) {
  const vehicle = driver ? [driver.vehicle_color, driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(' ') : '';
  // ...
}
// ...
const { title, body } = buildContent(status, currentRide, currentDriver, driverEtaSeconds);
// ...
}, [currentRide?.status, currentRide?.id, currentDriver?.name, driverEtaSeconds]);
```
```tsx
// After — flat scalar params so the effect's own deps can stay narrow
function buildContent(
  status: string, dropoffAddress: string | null | undefined, pickupOtp: string | null | undefined,
  driverFullName: string | null | undefined, vehicleColor: string | null | undefined,
  vehicleMake: string | null | undefined, vehicleModel: string | null | undefined,
  licensePlate: string | null | undefined, etaSeconds: number | null,
) { /* same switch/return logic, reading the scalar params instead */ }
// ...
const { title, body } = buildContent(
  status, currentRide?.dropoff_address, currentRide?.pickup_otp, currentDriver?.name,
  currentDriver?.vehicle_color, currentDriver?.vehicle_make, currentDriver?.vehicle_model,
  currentDriver?.license_plate, driverEtaSeconds,
);
// ...
}, [currentRide?.status, currentRide?.id, currentRide?.dropoff_address, currentRide?.pickup_otp,
    currentDriver?.name, currentDriver?.vehicle_color, currentDriver?.vehicle_make,
    currentDriver?.vehicle_model, currentDriver?.license_plate, driverEtaSeconds]);
```

```tsx
// Before (app/search-destination.tsx) — GPS-sets-pickup effect
useEffect(() => {
  if (userLocation) {
    if (!pickup || pickup.address === 'Current Location') {
      setPickup({ address: 'Current Location', lat: userLocation.latitude, lng: userLocation.longitude });
      setPickupText('Current Location');
    }
  }
}, [userLocation]);
```
```tsx
// After — reads pickup via a latest-value ref instead of depending on it
// directly, since handleTextChange nulls `pickup` on the first diverging
// keystroke and a plain dep would overwrite that mid-typing.
useEffect(() => {
  if (userLocation) {
    const currentPickup = pickupValueRef.current;
    if (!currentPickup || currentPickup.address === 'Current Location') {
      setPickup({ address: 'Current Location', lat: userLocation.latitude, lng: userLocation.longitude });
      setPickupText('Current Location');
    }
  }
}, [userLocation, setPickup]);
```

```tsx
// Before (app/driver-arrived.tsx) — keyed on the wrong fields
useEffect(() => {
  const sub = BackHandler.addEventListener('hardwareBackPress', () => { handleCancelPress(); return true; });
  return () => sub.remove();
}, [currentRide?.total_fare, (currentRide as any)?.grand_total]);
```
```tsx
// After — handleCancelPress is now a useCallback keyed on cancellationFee
// (what its dialog text actually depends on), and the effect depends on
// that callback instead of the two fields that were never quite right.
}, [handleCancelPress]);
```

## 8. Rollback plan

`git-revert-safe` — all 17 commits are pure client-side code (no
migration, no `app_settings` row, no Stripe/webhook/wallet state, no ride
state machine transition). Each commit is scoped to ≤3 files; later
commits do not depend on state introduced by earlier ones (each file's
findings were resolved independently), so any individual commit can be
reverted without affecting the others. The two test-mock-fix commits
(bundled into the `useRiderSocket.ts` commit) are also independently
revertible — reverting them would just restore the flaky mock, which only
matters if the `useRiderSocket.ts` production fix in the same commit is
also reverted.

## 9. Verification performed

- [x] `npx eslint . --format json` run fresh at the start of the session
  against the actual starting commit (not trusted from the task brief):
  **73** `react-hooks/exhaustive-deps` findings — matched exactly.
- [x] After all 17 commits, fresh `npx eslint . --format json`: **1**
  `react-hooks/exhaustive-deps` finding remains (the deliberately-unfixed
  `work-profile.tsx:90`, expected — not a miss). `73 → 1`.
- [x] `npx tsc --noEmit` — clean before this round's changes and clean
  again after every commit and at the end of the branch.
- [x] Full rider-app `npx jest --ci` — **468/468 tests passing, 56/56
  suites**, matching the task brief's stated baseline exactly. One
  unrelated, untouched, pre-existing test
  (`__tests__/privacySettingsToggles.test.tsx`) prints a benign
  post-teardown `ReferenceError: ... Jest environment has been torn down`
  AFTER its suite already reported passing (a leaked timer/async handle in
  that file) — not a reported failure, not touched by this round, flagged
  because it's unusual output rather than silently ignored. The previously
  tracked `androidAutoDistribution.test.ts` flake did not appear (confirmed
  fixed at its root by PR #3842, per the task brief — matches round 3's
  own note that it's driver-app-specific and doesn't exist in rider-app).
- [x] **A real regression was caught by this verification step, not
  shipped**: the `useRiderSocket.ts` `router` fix broke
  `useRiderSocket.reconnect.test.ts` (18/19 → confirmed 1 failure: 3
  WebSocket instances opened where 1 was expected). Root-caused to an
  unrealistic test mock (fresh `useRouter()` object per call, unlike the
  real singleton), fixed the mock in both of that hook's test files, then
  re-ran: 19/19 passing.
- [x] For every (b)-category `useCallback`/`useMemo` wrap added, grepped
  the file for other call sites of that function to confirm no other
  effect/memo/handler in the same file relied on the previous
  (non-memoized) identity behaving differently.
- [x] Dedicated test files re-run for every touched file that has one:
  `__tests__/ride-completed-route.test.tsx` (4/4), `__tests__/ride-details-route.test.tsx`
  (4/4), `__tests__/ride-options-payment-sheet.test.tsx` (7/7),
  `__tests__/walletStore-error-messages.test.ts` (3/3, store-level — doesn't
  cover `wallet.tsx`'s own effect directly), `__tests__/searchDestinationPinIntegrity.test.tsx`
  (4/4 — specifically covers the pickup/dropoff-orphaning behavior this
  round reasoned about), `hooks/__tests__/useRiderSocket.chat.test.ts`
  (10/10 after mock fix), `hooks/__tests__/useRiderSocket.reconnect.test.ts`
  (9/9 after mock fix), `store/__tests__/rideStore.cancel-flicker.test.ts`
  (a related consumer-side test, unaffected).
- [ ] Feature-flagged — not applicable. Every user-visible change this
  round is a small text-freshness or map-refit-timing improvement on an
  already-shipped screen (not a new feature or flow), matching the posture
  prior C20 rounds took for their own small UX fixes.

## 10. What was NOT verified

- **No real-device/simulator manual test** — same standing gap as rounds
  1-3. Not visually confirmed on-device: the cancellation-fee dialogs
  refreshing correctly on a live fare update, the map re-fit timing
  changes (driver-arriving.tsx rotation, ride-options.tsx stop add/remove),
  or the Android live-ride notification's field-freshness fix (this one
  specifically can't be verified in this environment at all — Notifee is
  Android-only and this session has no device/emulator). All reasoned
  about via code trace + the passing test suite. No visual regression
  tooling exists in this repo for React Native screens (standing gap,
  tracked in `ACTION_ITEMS.md`).
- **`npx expo export --platform web` was not re-run this round** — same
  gap flagged by rounds 2 and 3.
- **Not tested against a live backend or live WebSocket server** — the
  `useRiderSocket.ts` fix and its test-mock correction were exercised only
  via the existing mocked-WebSocket test fixtures, not a real
  `/ws/rider/{userId}` connection.
- **`search-destination.tsx`'s `userLocation`-never-resets-to-null
  assumption was verified by grep, not by reading every possible code path
  that could theoretically clear it in a future change** — if a future
  commit ever does reset `userLocation` to null while this screen is
  mounted, the mount effect (now depending on `userLocation`) would re-run
  `fetchSavedAddresses()`/`loadRecentSearches()` an extra time each such
  reset. Documented as an accepted, understood, low-probability cost, not
  something ruled out for all time.
- **`work-profile.tsx`'s underlying duplicate-fetch design question
  (flagged in round 3, referenced again by this round's deferred finding)
  was not investigated further** — this round only confirmed that guessing
  at a fix for the NEW exhaustive-deps finding on the same code would mean
  guessing at that SAME unresolved intent, and left both un-decided
  findings for a human, rather than resolving one blind and leaving the
  other.
- **The FreeCancelTimer.tsx `onExpire` fix's only real caller
  (driver-arriving.tsx) was not exercised by any automated test** — no
  dedicated test file exists for either component; the fix (wrapping the
  handler in `useCallback`) was reasoned about via code trace (grepped all
  3 `<FreeCancelTimer>` usages, confirmed only one passes `onExpire`) but
  not screenshotted or device-tested.
