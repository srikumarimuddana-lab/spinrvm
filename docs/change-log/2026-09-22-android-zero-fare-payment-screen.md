# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | mkkreddy52@gmail.com (Claude Code assisted) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/android-zero-payment-bug-7ikwwt` |
| Related issue or gap ID | Live-testing bug report: "after rider payment, rider sees a payment screen with 0.00 — only in Android" |

## 1. Issue / gap identified

After completing payment on the post-trip "You've arrived" screen
(`rider-app/app/ride-completed.tsx`), a rider on Android sees that screen render
`TRIP TOTAL $0.00` with a live, tappable `Pay $0.00 & Done` button — a payment
prompt for an amount that is not their fare, on a ride they have already paid
for. Reported from live app testing; reproduced by reading the render path, not
on a device (see §9).

## 2. Root cause

Two independent defects in the same screen compound into the symptom.

**(a) The screen has no loading state.** The fare is derived once:

```ts
const fare = toNum((currentRide as any)?.grand_total || currentRide?.total_fare);
```

`fare` therefore reads `0` whenever `currentRide` is `null`, and both the
`TRIP TOTAL` figure and the submit button's label rendered it unconditionally.
Nothing on this screen ever read a loading flag — `rideStore`'s `isLoading` is
not consumed here at all. The file already acknowledged the unloaded window for
the *tip ladder* (`tipReady` / `isTipLadderReady`, added so placeholder presets
can't be tapped before the fare loads) but that gate was never extended to the
fare display or the pay button.

**(b) `currentRide` is null while the screen is still mounted and painting.**
Two windows:

1. **Before the first `fetchRide` resolves.** This screen is reachable cold — a
   `ride_completed` push tap routes straight into it
   (`rider-app/app/_layout.tsx:181`), so the first frames render with no ride.
2. **Immediately after payment succeeds.** `handleSubmit`'s success path called
   `clearRide()` (which sets `currentRide: null`) and *then*
   `router.replace('/(tabs)')`. The zustand update repaints the still-mounted
   screen at `$0.00` before the navigation completes. `handleGooglePay` has the
   same `clearRide()` → `router.replace()` ordering.

Window 2 is why the report says "after I do the actual payment". It surfaces on
Android rather than iOS because React Navigation keeps the outgoing screen
mounted and painted through the platform stack transition, which is slower and
less occluded on Android than iOS's — the defect is platform-independent, the
**visibility** of it is not.

Compounding both: `alreadyPaid` was never latched on the success path, so the
repaint rendered the *payable* variant of the button (`Pay $… & Done`), not the
`Rate & Done` variant.

## 3. Fix / remediation

Four changes, all inside `ride-completed.tsx`:

1. A `rideLoaded = !!currentRide` readiness signal. `TRIP TOTAL` renders `—`
   (not `$0.00`) until the ride row loads, and the submit button quotes no
   dollar figure until then.
2. While the ride is unloaded (`awaitingRide`), the primary button becomes a
   **retry** that re-calls `fetchRide(rideId)`. It is *not* disabled, and it
   never charges.
3. `setAlreadyPaid(true)` on both success paths (`handleSubmit` after
   `paymentOk`, `handleGooglePay` after `result.ok`), latched *before* the
   `await onRideRated(...)` / `clearRide()` / `router.replace()` sequence, so
   every repaint during the transition reads as paid.
4. A new effect leaves the screen (`router.replace('/(tabs)')`) when
   `_clearedRideId === rideId` — this client has already finished with the
   ride, and in that state it can *never* load (see §11), so the screen has
   nothing to show and nothing to collect.

**Do not turn that button into a dead end.** The first draft of this fix
(commit `145ee10`) *disabled* it while the ride was unloaded. That is wrong,
and materially worse than the bug it fixes: this screen blocks the Android
hardware back button (`BackHandler.addEventListener('hardwareBackPress', () =>
true)`) and sets `gestureEnabled: false` on the route (`_layout.tsx:1045`), so
there is no other way off it. The reporter confirmed that tapping the `$0.00`
button is *exactly* how they currently get out — it round-trips to
`/rides/{id}/process-payment`, is told `already_paid: true`, treats that as
success, and navigates home. Disabling it would have taken away the only exit
and left riders stranded on a payment screen. `spinr-money-auditor` flagged the
same failure mode independently, via the cold-start path
(`_layout.tsx:181` pushes with a `rideId` only, so a network blip on that first
`fetchRide` leaves `currentRide` null with nothing retrying it —
`rideStore.ts`'s `fetchRide` swallows the error and `useCompletedRouteRefresh`
never polls while `currentRide` is null).

**Deliberately gated on `!!currentRide`, not on `fare > 0`.** A comped or
fully-covered ride has a legitimate `$0.00` `grand_total` —
`_authoritative_ride_charge` in `backend/routes/payments.py` explicitly refuses
to treat `$0` as "missing" and falls back to `total_fare` only when
`grand_total` is `None`. Gating on the amount would have shown that rider a
permanent loading state and blocked their receipt.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to one screen.** No store, API, backend, schema, or
shared-component change. `rider-app/app/ride-completed.tsx` is not in
`docs/known-forks.md` (its only mention there is as a *navigation destination*
in the `notifications.tsx` row) and has no driver-app sibling, so the fork
registry's check 11 does not apply.

Greps performed:

- `fetchRide` across `rider-app/` — 10 non-test call sites (`ride-completed`,
  `ride-in-progress`, `driver-arriving`, `driver-arrived`, `ride-status`,
  `ride-details`, `_layout`, `useRiderSocket`, `useRideLocationFallback`).
  **Untouched** — this change reads `currentRide`, it does not change how the
  store is populated.
- `clearRide` across `rider-app/` — 10 non-test call sites. **Untouched**; the
  fix does not reorder or remove any `clearRide()` call.
- `alreadyPaid` within the screen — 7 readers: the `payWithCard` auto-retry
  effect guard, `handleGooglePay`'s early return, `handleSubmit`'s
  `paymentOk = alreadyPaid` short-circuit, the `PAID` chip, the Google Pay
  button's visibility condition, the button label, and its accessibility label.
  Latching it earlier is correct for all seven: each already means "this ride
  has been charged", which is true at the point the latch now happens.

Could regress:

- **The change-card escape.** `handleSubmit` is deliberately *not* guarded on
  `currentRide` — the `payWithCard` param re-invokes `handleSubmitRef.current()`
  on mount, potentially before `fetchRide` lands, and the charge is settled
  server-side by `rideId` (`attemptRidePayment` never sends an amount). Adding a
  guard there would have broken that retry. Only the *button* is disabled; the
  programmatic retry path is unaffected.
- **Tip correctness.** `tipOptions` / `tipReady` / `reconcileSelectedTip` are
  unchanged and still key on `fare > 0`, which remains right for a ladder (a
  `$0` fare cannot scale one). No tip amount changes.
- **Money arithmetic.** No new arithmetic. `fare` and the button's
  `fare + tip` expression are unchanged; they are now only *conditionally
  rendered*.

No interaction with the ride state machine, wallet deltas, insurance-period
rows, or any `core/lifespan.py` background loop.

## 5. User-experience effect

Rider-facing, and **visible mid-session** — it fires on the post-trip screen of
a rider who has just been charged.

| Situation | Before | After |
|---|---|---|
| Ride still loading (push-tap cold start) | `TRIP TOTAL $0.00`, tappable `Pay $0.00 & Done` | `TRIP TOTAL —`, `Loading your trip… tap to retry` (re-fetches, never charges) |
| Ride failed to load (network blip, no retry existed) | `$0.00` + a charge attempt was the only action | Same retry button — now the recovery, not a charge |
| Re-entered for an already-finished ride | Stuck on `$0.00` with no exit but tapping Pay | Leaves to home automatically |
| Just after a successful charge | `TRIP TOTAL $0.00`, tappable `Pay $0.00 & Done` | `PAID` chip, `Rate & Done` |
| Comped / fully-covered ride ($0 grand_total) | `TRIP TOTAL $0.00`, `Pay $0.00 & Done` | **unchanged** — still `$0.00`, still payable |
| Normal loaded ride | `Pay $17.25 & Done` | **unchanged** |

Copy added: `Loading your trip…` (button) and `—` (total). Both are states, not
new messaging. Screen-reader labels were added alongside so the placeholder is
not announced as a bare dash: the total carries
`accessibilityLabel="Trip total still loading"` and the button's label switches
to `"Loading your trip"` with `accessibilityState.disabled` set.

**One incidental visible change, called out rather than left implied** (gate
#5): the submit button now renders at `opacity: 0.6` whenever it is disabled.
Previously it had no disabled styling at all, so this also applies to the
pre-existing disabled conditions — most visibly **during `isSubmitting`**,
where the button now dims while its "Confirming payment…" spinner runs. This is
deliberate and matches the app's own existing convention for exactly this case
(`payment-confirm.tsx`'s Book button: `[styles.bookButton, (isLoading ||
isBooking) && { opacity: 0.6 }]`). It is also load-bearing for the fix: a
disabled button that looks identical to an enabled one is the same trap as the
`$0.00` label — the rider taps a dead control and learns nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-completed.tsx` | Added `rideLoaded` / `awaitingRide` / `submitDisabled`; gated `TRIP TOTAL` and the submit button's label+icon on them; made the unloaded button a retry; latched `setAlreadyPaid(true)` on both success paths; added the `_clearedRideId === rideId` leave effect | Stop rendering and offering a `$0.00` charge while the ride row is null, without creating a dead end on a back-blocked screen |
| `rider-app/__tests__/rideCompletedScreen.test.tsx` | Added 9 regression tests across 4 describe blocks; gave `mockClearRide` a real implementation in the post-charge tests | Pin the unloaded-render contract, the retry behaviour, the comped-$0 exemption, the PAID latch on both pay paths, the cleared-ride exit, and the change-card-while-unloaded charge |

## 7. Before / after

```tsx
// Before — fare printed and offered unconditionally
<Text style={styles.fareAmount} allowFontScaling={false}>${fare.toFixed(2)}</Text>
...
<TouchableOpacity
  disabled={isSubmitting || sheetLoading || !!tipMinimumError}
>
  {alreadyPaid
    ? 'Rate & Done'
    : `Pay $${(fare + (effectiveTip || getCustomTipAmount(customTip))).toFixed(2)} & Done`}
```

```tsx
// After — both gated on whether the ride row has loaded
const rideLoaded = !!currentRide;
const submitDisabled =
  isSubmitting || sheetLoading || !!tipMinimumError || (!rideLoaded && !alreadyPaid);
...
<Text style={styles.fareAmount} allowFontScaling={false}
      accessibilityLabel={rideLoaded ? undefined : 'Trip total still loading'}>
  {rideLoaded ? `$${fare.toFixed(2)}` : '—'}
</Text>
...
<TouchableOpacity disabled={submitDisabled}>
  {alreadyPaid
    ? 'Rate & Done'
    : !rideLoaded
      ? 'Loading your trip…'
      : `Pay $${(fare + (effectiveTip || getCustomTipAmount(customTip))).toFixed(2)} & Done`}
```

```tsx
// After — handleSubmit success path, added before the awaits that follow
if (!paymentOk) return;
setAlreadyPaid(true);   // ← new: repaints during the transition read as paid
```

## 8. Rollback plan

`git revert` of the single commit is a complete and sufficient rollback here,
and this is one of the cases the template's caveat explicitly permits:

- No migration, no schema change, no `app_settings` key, no backend change.
- **No live data is written by this diff.** It changes render conditions and one
  piece of local component state. It creates no Stripe charge, no wallet delta,
  no ride-state transition, and no `driver_insurance_periods` row — so there is
  nothing applied-to-live-data that a code revert would leave stranded.
- Reverting restores the previous (buggy) render exactly; a rider mid-session
  during the revert sees only the old label on their next render.

Not feature-flagged. Justification: the change removes a wrong, actionable money
figure from a payment surface. Dark-shipping it would mean deliberately leaving
riders looking at `Pay $0.00 & Done` while the flag was off, which is worse than
the (bounded, revertible) risk of shipping it on. The fallback state is strictly
more conservative than the current behaviour — a disabled button cannot charge
anyone.

## 9. Verification performed

- [x] Blast-radius grep performed — `fetchRide`, `clearRide`, `alreadyPaid`
      across `rider-app/`; `docs/known-forks.md` checked for a sibling (none).
- [x] Syntax/parse check — `tsc --noEmit --noResolve --skipLibCheck
      --jsx preserve` on both changed files: **zero `TS1xxx` syntax errors**
      (all remaining diagnostics are `TS2307`/`TS2304`/`TS7006`/`TS2593`, i.e.
      unresolved modules and missing `@types`, which are artifacts of
      `--noResolve` with no `node_modules`).
- [x] Reviewed against `CLAUDE.md` conventions — Decimal/money (no new
      arithmetic introduced), no silent error swallowing, WCAG 2.1 AA labels on
      the new states.
- [x] Reviewer agent run against the actual diff — `spinr-money-auditor`
      (CLAUDE.md pre-merge gate #10). **Verdict: no BLOCKERS.** It confirmed
      the charge amount is 100% server-authoritative on both payment paths
      (`/rides/{id}/process-payment` sends no amount at all;
      `/payments/payment-sheet` treats the client `amount` as advisory), that
      the `alreadyPaid` latch sits strictly downstream of a confirmed
      `paymentOk === true` and so cannot suppress a real retry, that the tip
      survives the change-card remount via the URL param independently of
      `currentRide`, and that server-side idempotency
      (`payment_status` atomic claim) is a second layer under the new client
      latch. Three warnings raised, **all three resolved in this commit**:
      (a) the disabled-button dead end — the button is now a retry;
      (b) no test for `payWithCard` firing while `currentRide` is null —
      added; (c) `mockClearRide` was a bare `jest.fn()`, so the post-charge
      tests never actually nulled `currentRide` and proved less than they
      claimed — it now has a real implementation.
- [ ] **Automated tests NOT run.** 5 regression tests were written but could not
      be executed in this session: `rider-app/node_modules` is absent and
      `registry.npmjs.org` is blocked by this environment's egress policy
      (`npm error code E403`). The agent-proxy README says not to route around a
      policy denial. **These tests must be run before merge.**
- [ ] **No manual/staging repro.** The fix was derived by reading the render
      path; it was not reproduced or confirmed on an Android device or emulator.
- [ ] **No production build run** (`npm run build` / EAS) — same npm blocker.

## 10. What was NOT verified

- **Not run on a device.** The Android-vs-iOS visibility explanation (React
  Navigation keeps the outgoing screen painted through the transition) is
  reasoned from the code and platform behaviour, **not measured**. The $0.00
  render itself is certain from the code; *how long* it is visible on Android is
  not.
- **The exact Android-only trigger is NOT identified.** The reporter confirms
  the screen *persists* and is tappable (they escape by pressing the $0.00
  button), so this is a re-entry / never-left state, not a transition flash.
  What re-enters it, and why that happens on Android and not iOS, was not
  determined from static reading. Android-only code that could plausibly be
  involved, none confirmed: the Notifee ongoing "live ride" notification
  (`services/rideLiveNotification.ts`, gated `Platform.OS === 'android'`,
  `ongoing: true`, `pressAction.launchActivity: 'default'` — its `cancel()`
  is driven by `useRideStatusNotification`'s `isPostedRef`, a per-instance
  ref), the Android-only Google Pay button, and the fact that Stripe's 3DS
  challenge is a separate Activity on Android (so the app crosses
  background→active and fires `_layout.tsx`'s foreground-resume
  `fetchActiveRide`, which iOS's in-app sheet does not). **Settling this needs
  a device log or a screen recording**, not more code reading. The fix in this
  commit is deliberately written to be correct regardless of which trigger it
  is — it removes the wrong figure, keeps an exit, and leaves the screen when
  the ride is unloadable.
- **rider-app has no visual/snapshot regression tooling at all** (CLAUDE.md
  pre-merge gate #6). The layout of the `—` placeholder and the
  `Loading your trip…` button was reasoned about, not screenshotted.
- The 5 new tests are unexecuted (see §9) — treat them as written-but-unproven.

## 11. Open follow-up — GUARDED here, root cause NOT fixed

`_clearedRideId` in `rider-app/store/rideStore.ts` is a **permanent, one-way
latch**, and it turns the same `$0.00` render into an unrecoverable trap if
anything re-enters this screen for a just-cleared ride.

`clearRide()` records the ride id (`rideStore.ts:1097-1115`). Both
`fetchRide` (`:844`) and `fetchActiveRide` (`:493`) then refuse that ride
**forever** — the latch is only reset by `createRide` (`:809`) or by
`fetchActiveRide` succeeding for a *different* ride (`:498`). Its guard comment
scopes the intent to *in-flight* fetches ("If `clearRide()` ran while this fetch
was in-flight"), but the implementation is id-based, so it also discards a
deliberate, *later* re-fetch — e.g. a fresh mount of `/ride-completed` for that
same ride.

Consequence: re-entering `/ride-completed?rideId=X` after `clearRide()` (via a
`ride_completed` push tap — `_layout.tsx:181` — or the in-app notification list
— `notifications.tsx:139`, neither of which consults `payment_status` or
`_clearedRideId`) leaves `currentRide` permanently `null`. The auto-dismiss
effect (`ride-completed.tsx:330`) keys on `currentRide?.payment_status`, which
stays `undefined`, so it never fires; Android's `BackHandler` returns `true` and
`gestureEnabled: false` is set on the route, so there is **no way off the
screen**.

**What this commit does about it:** guards the *symptom* only. The new effect in
`ride-completed.tsx` detects `_clearedRideId === rideId` and leaves for
`/(tabs)` instead of rendering an unloadable receipt. That is safe (nothing is
owed — `clearRide` only runs after the ride was paid, waived, held, or
cancelled), screen-local, and has zero blast radius on the store. It stops the
rider being stranded.

**It does not fix the latch.** `fetchRide` still permanently refuses a cleared
ride for every other caller and every other screen.

**Proposed real fix (NOT applied):** replace the id-only check with a monotonic
clear epoch captured at fetch start, so the guard discards a response only when
a clear happened for that ride *during* the fetch, and lets a later deliberate
re-fetch through.

**Why that is not in this commit:** `fetchRide` has 10 call sites, four of which
(`ride-status.tsx:164`, `driver-arriving.tsx:326`, `driver-arrived.tsx:124`,
`useRiderSocket`'s handlers) poll or fire on a timer and can start a fetch
*after* a cancel's `clearRide()`. Loosening the guard could let one of those
repopulate a cancelled ride and re-open the cancel-during-search toast flicker
that `rideStore.cancel-flicker.test.ts` pins. That needs its own blast-radius
pass and its own Change Impact Log — CLAUDE.md pre-merge gate #9 (escalate,
don't silently ship) rather than widening a payments diff.
