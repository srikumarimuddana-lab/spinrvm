# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app, shared |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`, commits `58fc426`, `108e11d`, `c1967e9`, `0ecce13`) |
| Related issue or gap ID | Round 7 of live-testing feedback on driver-app map/vehicle-tracking (follows merged PRs #4652, #4656, #4694, #4712, #4715, #4718) |

## 1. Issue / gap identified

Four issues reported from on-device live testing:
1. Car marker icon disappears (though the camera still recenters correctly) after going offline then back online and tapping recenter.
2. The recenter button is not responsive — feels laggy/non-instant.
3. The always-expanded online/vehicle "hud" panel eats map screen real estate while online.
4. The driver-app notification bell shows an unread count, but tapping it shows no actual notifications (both in-app list and OS push, per user confirmation).

## 2. Root cause

1. Same failure class as an already-documented, already-fixed bug in the same file (`(tabs)/index.tsx`): Android's Google Maps native layer can leave a marker's underlying native view stale across an interruption. The existing fix (bump `mapKey` to force a clean `MapView` remount) only fired on ride-phase transitions back to `idle`; an offline→online cycle never leaves `idle`, so it never triggered a remount, leaving the marker's native view stale.
2. `MapControls.handleRecenter` awaited a fresh `Location.getCurrentPositionAsync` fetch (up to 15s timeout) before animating the camera at all, even though the app already tracks a live, continuously-updating position via `watchPositionAsync`.
3. The idle hud panel had no collapsed state — it was designed to always show full vehicle+status detail, which was reasonable before but was flagged by live testing as taking too much of the map view once a driver is online and just waiting.
4. `refetchOnWindowFocus: true` was already set as a default TanStack Query option, but is a browser-only concept out of the box — the React Native `focusManager`/`AppState` wiring TanStack Query's own docs describe as required for RN was never added anywhere in this codebase. So a notifications fetch that exhausted its retries (e.g. an App-Check-gated 401 near cold start — a previously-fixed, unrelated issue) never got a foreground-triggered retry, and no per-screen-focus refetch existed either. Confirmed via grep: zero prior matches for `focusManager`/`AppState` wiring in `queryClient.ts`. **OS-level push non-delivery specifically is not conclusively diagnosed by this change** — the FCM token registration flow (`users.fcm_token_driver`, `push_tokens`, `POST /notifications/register-token`) was inspected and looks correctly implemented and correctly auth-gated; true non-delivery diagnosis needs device logs or Firebase console access not available in this session.

## 3. Fix / remediation

1. Extend the existing `mapKey` remount trigger to also fire on `isOnline` false→true transitions (previously only fired on ride-phase→idle transitions).
2. Rewrite `handleRecenter` to animate the camera immediately using the already-live `location` prop; the fresh GPS fetch still runs in the background (fire-and-forget) purely to correct rare stale/cold-start cases.
3. Give the idle hud an auto-collapse: full detail for ~2s after going online, then a spring-animated (`Animated.spring`, matching the existing `ActiveRidePanel` house style) collapse to a compact pill; tap the pill to re-expand (with its own ~2.5s re-collapse timer). Always fully expanded while offline. `MapView.mapPadding` (the same SDK-native mechanism used for panel clearance in rounds 5/6) is extended to account for the new collapsed/expanded height so the map camera center never sits under the panel.
4. Wire TanStack Query's `focusManager` to React Native's `AppState` in `shared/api/queryClient.ts` (the documented RN integration step), and add a `useFocusEffect`-based refetch directly on the driver notifications screen (matching the existing `lost-and-found.tsx` pattern) to cover in-app screen-focus navigation, which is a different signal from app-level foreground/background.

## 4. Risk & impact on existing functionality

- **Marker remount (#1)**: isolated to `driver-app/app/driver/(tabs)/index.tsx`'s existing `mapKey` effect — no other file reads or sets `mapKey`. Same accepted tradeoff as the original fix: a brief camera flash to `initialRegion` on remount, now also on offline→online in addition to ride-end.
- **Recenter (#2)**: isolated to `MapControls.tsx`'s `handleRecenter`; `onRecenter` prop callback and its consumer (`useDriverDashboard.refreshLocation`) are unchanged, still invoked, just no longer blocking the camera animation.
- **Idle hud (#3)**: isolated to `DriverIdlePanel.tsx` + its two exported height constants, consumed only by `(tabs)/index.tsx`'s `mapPadding` calculation. Grepped for other consumers of `DriverIdlePanel` and the old `IDLE_PANEL_HEIGHT_DP` constant — none found outside this file pair. No change to the offline hud rendering (always expanded).
- **Notifications (#4)**: `shared/api/queryClient.ts` is imported by both driver-app and rider-app. The `focusManager`/`AppState` wiring is a global, app-wide behavior change: **every** query using default options (in both apps) now genuinely refetches on app foreground, not just notifications. Grepped both apps' query hooks for anything relying on foreground *not* refetching (e.g. a hook that intentionally disables `refetchOnWindowFocus`) — none found; hooks that need different behavior already set their own `staleTime`/`refetchOnWindowFocus` overrides (e.g. active-ride at `staleTime: 0`, config at 10 min), which are unaffected since per-hook overrides take precedence. Full rider-app (134 suites, 1896 tests, 5 pre-existing failures unrelated to this change — see below) and full driver-app (118 suites, 1332 tests, all passing) suites both run clean against this shared-file change. The `useFocusEffect` addition is scoped to `driver-app/app/driver/notifications.tsx` only.

## 5. User-experience effect

- **Driver-facing**, all four changes. Visible mid-session to a driver already online (hud collapse/expand, marker restoration, recenter responsiveness) — this is by design per the report (live-testing on a real device already online).
- No rider-facing or corporate-admin-facing change in this round.
- Idle hud collapse is a UX change to an already-shipped screen: previously always-expanded, now auto-collapses ~2s after going online. Not feature-flagged — this is a small, additive UI polish change (tap-to-expand always available, no functionality removed, driver's own device/testing context) rather than a structural behavior change to a live-tested flow (ride state, money, dispatch). Consistent with CLAUDE.md gate 3's flagging guidance being for "non-trivial" UX changes on shared components used by 3+ pages; `DriverIdlePanel` is used by exactly one screen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Extended `mapKey` remount effect to also trigger on `isOnline` false→true; updated `mapPadding` idle calculation for new hud collapsed/expanded heights; wired `DriverIdlePanel`'s `onExpandedChange` prop | Fix #1 (marker), support #3 (hud padding) |
| `driver-app/components/dashboard/MapControls.tsx` | `handleRecenter` now animates immediately off the live `location` prop instead of awaiting a fresh GPS fetch | Fix #2 (recenter) |
| `driver-app/components/dashboard/DriverIdlePanel.tsx` | Added auto-collapse (2s after online) / tap-to-re-expand (2.5s re-collapse) hud state with `Animated.spring` crossfade; exported `HUD_EXPANDED_HEIGHT_DP`/`HUD_COLLAPSED_HEIGHT_DP`; added `onExpandedChange` prop | Fix #3 (screen real estate) |
| `driver-app/components/dashboard/index.ts` | Export the two new height constants | Support #3 |
| `driver-app/__tests__/components/DriverIdlePanel.test.tsx` | New test file: mount-expanded, auto-collapse timing, tap-to-re-expand + re-collapse | Test coverage for #3 |
| `shared/api/queryClient.ts` | Wired TanStack Query `focusManager` to RN `AppState` | Fix #4 (root cause: inert `refetchOnWindowFocus`) |
| `driver-app/app/driver/notifications.tsx` | Added `useFocusEffect`-based refetch on screen focus | Fix #4 (in-app navigation-focus refetch) |
| `driver-app/__tests__/screens/notifications.test.tsx`, `driver-app/__tests__/app/driverNotificationsScreen.test.tsx` | Added `expo-router/react-navigation` `useFocusEffect` mock (matching existing `documentsScreen.test.tsx` pattern) | Keep existing suites passing against the new hook usage |

## 7. Before / after

**Recenter (`MapControls.tsx`):**
```tsx
// Before
const handleRecenter = async () => {
  await onRecenter?.();
  if (location && mapRef.current) {
    mapRef.current.animateToRegion({ ... }, 500);
  }
};
```
```tsx
// After
const handleRecenter = () => {
  if (location && mapRef.current) {
    mapRef.current.animateToRegion({ ... }, 400);
  }
  void Promise.resolve(onRecenter?.()).catch(() => {});
};
```

**Marker remount trigger (`(tabs)/index.tsx`):**
```tsx
// Before
if (rideState === 'idle' && prevRideState !== 'idle') {
  setMapKey((k) => k + 1);
}
```
```tsx
// After
if ((rideState === 'idle' && prevRideState !== 'idle') || (isOnline && !prevOnline)) {
  setMapKey((k) => k + 1);
}
```

**`refetchOnWindowFocus` wiring (`queryClient.ts`):**
```ts
// Before — no focusManager/AppState wiring anywhere in the file
export const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: true, ... } } });
```
```ts
// After
function onAppStateChange(status: AppStateStatus) {
  if (Platform.OS !== 'web') focusManager.setFocused(status === 'active');
}
AppState.addEventListener('change', onAppStateChange);
export const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: true, ... } } });
```

## 8. Rollback plan

All four changes are pure client-side code (no migration, no `app_settings` flag, no server state). A `git revert` of the four commits (`58fc426`, `108e11d`, `c1967e9`, `0ecce13`) is a complete rollback — none of these touch Stripe, wallet, or ride-state data, so a code revert is sufficient here (unlike the money/ride-state cases CLAUDE.md calls out where revert alone would not be enough). No feature flag was introduced; a mobile build (`[build]` commit tag) is required for the change to reach devices either way, same as every other round.

## 9. Verification performed

- [x] Automated tests run: full driver-app suite (`npx jest` in `driver-app/`) — 118 suites, 1332 tests, all passing. Full rider-app suite (`npx jest` in `rider-app/`) — 134 suites, 1896 tests, 5 failing in `rideDetailsScreen.test.tsx`; confirmed via `git stash`/re-run that these 5 fail identically on `main` without any of this round's changes — pre-existing and unrelated.
- [x] `npx tsc --noEmit` run clean in both `driver-app/` and `rider-app/` (the latter specifically to catch fallout from the shared `queryClient.ts` change).
- [x] `npx eslint` run on all changed files: 0 errors. 4 pre-existing-pattern warnings (`@typescript-eslint/no-require-imports` on `require()` inside `jest.mock()` factories) matching the established codebase pattern already present in `documentsScreen.test.tsx`.
- [x] Blast-radius grep performed: `DriverIdlePanel`/`IDLE_PANEL_HEIGHT_DP` consumers (only `(tabs)/index.tsx`), `mapKey` consumers (same file only), `focusManager`/`AppState` wiring (confirmed absent before this change, and confirmed `shared/api/queryClient.ts`'s only consumers are driver-app and rider-app — `admin-dashboard` does not import it).
- [ ] Manual repro steps followed in staging — **not performed**; this round has not yet been tested by the user on a real device (that happens after this PR/build ships, per the established rhythm of this session).
- [x] Reviewed against relevant CLAUDE.md conventions: no state-machine, money, RLS, or PIPEDA surface touched by this round; observability (Sentry/metrics) not applicable — no new error paths or background loops added.
- [x] Feature-flagged if user-visible and non-trivial: not flagged — justified above (§5) as small/additive polish on a single-consumer component, consistent with prior rounds' treatment of similar map/UI polish.

## 10. What was NOT verified

- OS-level push notification delivery (item #4, "phone doesn't buzz/banner" half of the user's "Both" answer) was **not fixed or conclusively diagnosed** by this round. The FCM token registration code was inspected and looks correct; the actual non-delivery has not been reproduced or traced (would need device logs / Firebase console access unavailable in this session). Flagging as an open item — see PR description for the follow-up question to the user (whether ride-offer pushes, which share the same token pipeline, currently work).
- No on-device manual verification of any of the four fixes yet — driver-app and rider-app have no automated visual-regression tooling (per CLAUDE.md gate 6), so the hud collapse/expand animation and marker-restoration visual behavior were reasoned about and unit-tested (timer-driven `onExpandedChange` assertions), not screenshotted.
- The "make our own custom marker icon/animation" question raised by the user is a product question, not a bug fix — not implemented in this round; answered directly in the PR/response instead (short answer: yes, we already render our own custom `CarMarker`/glyph rather than a native Google Maps default pin, so a bespoke icon or animation is entirely within our control and not blocked by Google Maps or Waze).
