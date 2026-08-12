# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (local commit, not yet pushed) |
| Related issue or gap ID | Tracker task #15 (P2) |

## 1. Issue / gap identified

Two small UI bugs in `driver-app/app/driver/(tabs)/index.tsx`, both around the ride-offer panel:

- **Bug A:** `<RideOfferPanel isLoading={false}>` was hard-coded, so a driver double-tapping Accept (or Decline) could fire two `/accept` (or `/decline`) requests — the panel's own `disabled={isLoading}` guard on both buttons was never actually wired to anything.
- **Bug B:** the offer countdown is a plain `setInterval` reseeded only on `rideState` transitions into `'ride_offered'`. RN suspends JS timers while backgrounded, so after a driver briefly backgrounds the app mid-offer and returns, the displayed number can be stale (too high) for a beat.

## 2. Root cause

- Bug A: the panel-level prop wiring was simply never connected to the store's real `isLoading` field when `RideOfferPanel` was integrated into the dashboard — the component itself already supported it.
- Bug B: the countdown effect's dependency array is `[rideState]` only, so it never re-syncs against wall-clock time; it just keeps decrementing an in-memory counter that doesn't account for time elapsed while the interval was suspended in the background.

## 3. Fix / remediation

- Bug A: destructured `isLoading` from `useDriverStore()` and passed it straight through (`isLoading={isLoading}`) instead of the hard-coded `false`.
- Bug B: added a second `AppState.addEventListener('change', ...)` effect that, on transition to `'active'`, reads live state via `useDriverStore.getState()` and — if an offer is still showing (`rideState === 'ride_offered'`) and the offer carries `offer_expires_at` — recomputes the remaining seconds as a wall-clock diff (`Math.floor((expiresAt - now) / 1000)`, clamped to ≥ 0) and pushes it into the local countdown state. The existing per-second interval is untouched and keeps driving the normal steady-state tick; only the terminal `0` case is also pushed back into the store (mirroring the interval's own existing convention), so the store's own auto-decline-on-expiry side effect (`setCountdown` → `declineRide` when `seconds <= 0`) still fires correctly if the offer was found to already be expired on resume.

Both fixes are purely additive/wiring — no new state, no new endpoint, no schema change.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `driver-app`, single component + single screen.** Grepped for every `RideOfferPanel` consumer (`components/index.ts` barrel export, `app/driver/(tabs)/index.tsx`, and its own test file) — it is imported and rendered in exactly one place in the app (`app/driver/(tabs)/index.tsx`); there is no other screen or panel that renders it. `useDriverStore`'s `isLoading` field is a shared, general-purpose loading flag also set by `arriveAtPickup`, `startRide`, `completeRide`, `cancelRide`, and others — but none of those can run concurrently with the ride-offer panel being visible (the panel only renders while `rideState === 'ride_offered'`, and those other actions only run once the ride has moved past that state), so reading it here does not risk the panel spuriously showing a spinner from an unrelated in-flight call.
- `declineRide` (unlike `acceptRide`) does **not** set `isLoading` around its own request — this was true before this change and is unchanged by it. A double-tap on Decline alone is therefore not newly protected by this fix; it was and remains a lower-severity gap than the reported Accept case (decline is already synchronous-feeling and its failure path is swallowed/idempotent client-side). Not touched here to keep the commit to the one described, wiring-only fix; flagging in case it's worth a separate follow-up.
- Bug B's new effect calls `setCountdown(0)` on the store when it determines the offer is truly expired after a background stretch — this reuses the *exact* code path the interval's own zero-tick already exercises (`setCountdown(0)` → store's existing `seconds <= 0 && rideState === 'ride_offered'` → `declineRide`), so no new decline logic was introduced; the AppState resync can only make that existing decline fire slightly earlier (correctly) than it otherwise would have.
- Server remains authoritative in both cases: acceptance races are already handled idempotently on the backend (`ride_flow.py` accept path, atomic `{'status': 'searching'}`-style claim) and reconciled client-side via the `alreadyTakenStatus` branch in `driverStore.acceptRide` (~lines 592–611); offer expiry is already enforced server-side via the `ride_offer_expired` WS event and the accept-time 409. Neither fix changes any of that — both are purely UI-layer.

## 5. User-experience effect

- **Driver-facing only**, visible mid-session (while an offer is on screen).
- Bug A fix: Accept/Decline buttons now visibly disable (spinner on Accept) while a request is in flight, instead of silently accepting a second tap. This is a strict improvement — previously a double-tap could send two `/accept` calls (harmless server-side today, but wasteful and could show a confusing "already taken by another driver" toast to the *winning* driver on the rare race).
- Bug B fix: after backgrounding and returning during an active offer, the countdown number may visibly jump down to match reality (or the offer may now correctly auto-decline as expired) instead of staying frozen too high for a moment. No new user-facing copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Destructured `isLoading` from `useDriverStore()`; changed `<RideOfferPanel isLoading={false}>` → `isLoading={isLoading}`; added an `AppState`-driven effect that recomputes the offer countdown from `offer_expires_at` on foreground resume | Fix Bug A (double-tap) and Bug B (stale countdown after backgrounding) |
| `driver-app/__tests__/components/RideOfferPanel.test.tsx` | Added `isLoading` disabling-buttons test coverage (loading vs. not-loading, spinner shown) | Regression coverage for Bug A |
| `driver-app/__tests__/screens/driverOfferPanelWiring.test.ts` | New source-contract test file (same convention as `driver-dashboard-route.test.ts` / `appStateFlush.test.ts`) asserting the `isLoading` wiring and the `AppState` resync effect's exact shape | Regression coverage for both bugs at the screen-wiring level, without needing to fully mount the maps-heavy dashboard screen |
| `docs/change-log/2026-08-11-driver-offer-panel-doubletap-countdown.md` | This log | Process requirement |

## 7. Before / after

```tsx
// Before
<RideOfferPanel
  incomingRide={incomingRide as any}
  ...
  isLoading={false}
  onAccept={() => acceptRide(incomingRide.ride_id)}
  onDecline={() => declineRide(incomingRide.ride_id)}
/>
```

```tsx
// After
<RideOfferPanel
  incomingRide={incomingRide as any}
  ...
  isLoading={isLoading}
  onAccept={() => acceptRide(incomingRide.ride_id)}
  onDecline={() => declineRide(incomingRide.ride_id)}
/>
```

```tsx
// Before — countdown only re-seeds on rideState transition, no resync
useEffect(() => {
  if (rideState !== 'ride_offered') return;
  setCountdownState(countdownSeconds);
  const interval = setInterval(() => { /* decrement */ }, 1000);
  return () => clearInterval(interval);
}, [rideState]);
```

```tsx
// After — added alongside the existing interval effect (unchanged)
useEffect(() => {
  const sub = AppState.addEventListener('change', (next) => {
    if (next !== 'active') return;
    const { rideState: curRideState, incomingRide: curIncomingRide } = useDriverStore.getState();
    if (curRideState !== 'ride_offered' || !curIncomingRide?.offer_expires_at) return;
    const remaining = Math.max(
      0,
      Math.floor((new Date(curIncomingRide.offer_expires_at).getTime() - Date.now()) / 1000),
    );
    setCountdownState(remaining);
    if (remaining <= 0) setCountdown(0);
  });
  return () => sub.remove();
}, [setCountdown]);
```

## 8. Rollback plan

No feature flag — this is a same-file, additive UI wiring change with no new endpoint, table, or schema. Rollback is a plain `git revert` of the single commit; there is no live data (no Stripe charge, wallet delta, or ride-state row) written by either fix, so a code-level revert is sufficient and complete. If only one of the two fixes needs to be reverted, both hunks are independently revertible (they touch non-overlapping lines in the same file).

## 9. Verification performed

- [x] Automated tests run: `npx jest __tests__/components/RideOfferPanel.test.tsx __tests__/screens/driverOfferPanelWiring.test.ts __tests__/screens/driver-dashboard-route.test.ts` → 3 suites / 18 tests passed. Also ran the full `driver-app` suite (`npx jest`) → 51/52 suites passed, 369/370 tests passed; the one failure (`ActivityView.test.tsx`, an unrelated 5s-timeout on an earnings-loading-failure test) is pre-existing and untouched by this change — neither `ActivityView` nor `fetchEarnings` appear in either diff.
- [x] `yarn tsc --noEmit` (ran via `npx tsc --noEmit` from `driver-app/`, same effect) → clean, no errors.
- [x] Blast-radius grep performed: `RideOfferPanel` usage (only consumer is `app/driver/(tabs)/index.tsx`), all `isLoading` set-sites in `driverStore.ts` (confirmed no other in-flight action can overlap with the ride-offer-panel render window).
- [ ] Manual repro steps followed in staging — **not performed**, this fix was verified via unit/source-contract tests only, not by driving the built app.
- [x] Reviewed against relevant `CLAUDE.md` conventions — ride state machine (no new transition introduced; `declineRide` on expiry is the pre-existing path), no money/DB/RLS surface touched.
- [ ] Feature-flagged — **not applicable**; per CLAUDE.md gate 3 this is UI polish limited to a single, non-shared component render path (`RideOfferPanel`'s one consumer), not a 3+-page shared surface, so a flag was judged unnecessary. Flagging this call explicitly rather than silently skipping the gate.

## 10a. What was NOT verified

- Not exercised on a real device or simulator — no screenshot/visual check of the countdown UI before/after backgrounding, and no manual double-tap timing test against a real (or even mocked) network round-trip. This repo has no automated visual/snapshot regression tooling for `driver-app` (standing gap, not re-litigated here).
- The `AppState` resync effect's behavior under real OS-level backgrounding timing (RN timer suspension semantics vary somewhat by platform/OS version) was reasoned about from RN's documented behavior, not measured against real device background durations.
- `declineRide`'s own lack of an `isLoading` guard was identified but intentionally left unfixed (see §4) — a double-tap on Decline alone is not newly protected by this change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no live-data dependency)
- [x] Blast radius is stated, not assumed (single component, single consumer, grepped)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
