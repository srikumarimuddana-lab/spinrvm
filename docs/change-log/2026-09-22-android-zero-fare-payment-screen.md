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

Five changes. Four in `ride-completed.tsx`, plus the store fix in §11 — that
last one is the root cause of the *persistent* $0.00 screen the reporter
actually hit, and is the reason this entry is not purely a display fix.

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
   ride, so there is nothing to show and nothing to collect.
5. **`rideStore.ts`: `_clearedRideId`'s permanent latch replaced with a
   `_clearEpoch` comparison** — see §11 for the full writeup and blast radius.

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

**Blast radius: one screen, plus one guard inside the rider ride store.** No
API, backend, schema, or shared-component change.
`rider-app/app/ride-completed.tsx` is not in `docs/known-forks.md` (its only
mention there is as a *navigation destination* in the `notifications.tsx` row)
and has no driver-app sibling, so the fork registry's check 11 does not apply.
`rideStore.ts` is rider-app-only — driver-app has its own store.

**The store change is the wider half of this diff; its own blast-radius
analysis, including the one real behaviour change it allows and why that is
safe, is in §11.** Summary of what was checked there: every `fetchRide` call
site, every screen that reacts to a cancelled `currentRide`, both other
`_clearedRideId` consumers, and what `cancel-flicker.test.ts` actually pins.

Greps performed:

- `fetchRide` across `rider-app/` — 10 non-test call sites (`ride-completed`,
  `ride-in-progress`, `driver-arriving`, `driver-arrived`, `ride-status`,
  `ride-details`, `_layout`, `useRiderSocket`, `useRideLocationFallback`). All
  reviewed for the epoch change — see §11.
- `clearRide` across `rider-app/` — 10 non-test call sites. No call reordered or
  removed; `clearRide` now also bumps `_clearEpoch`.
- `_clearedRideId` across `rider-app/` — 2 consumers outside the store
  (`useRiderSocket.ts:147`, and `ride-completed.tsx`'s new leave effect), both
  deliberately unaffected by the epoch.
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
  guard there would have broken that retry, so none was added — only the
  *button's* behaviour changes, and the programmatic path is untouched. Pinned
  by a new test (`change-card escape while the ride is still unloaded`).
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
| `rider-app/store/rideStore.ts` | Added `_clearEpoch`; bumped it at all three ride-retiring sites (`clearRide`, both `cancelRide` set sites); `fetchRide` now snapshots it and discards only responses overtaken by a clear | Stop the permanent latch silencing deliberate re-fetches — the root cause of the *persistent* $0.00 receipt (§11) |
| `rider-app/store/__tests__/rideStore.clearedRide-refetch.test.ts` | New file, 7 tests | Pin both halves of the epoch guard, that the two other `_clearedRideId` consumers keep permanent semantics, and the post-cancel poll-tick case the change allows |

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

```ts
// Before — rideStore.ts fetchRide: a permanent, one-way latch
if (get()._clearedRideId === rideId) { set({ isLoading: false }); return; }
```

```ts
// After — discards only a response overtaken by a clear mid-request
const clearEpochAtStart = get()._clearEpoch;          // before the request
// ...
if (get()._clearedRideId === rideId && get()._clearEpoch !== clearEpochAtStart) {
  set({ isLoading: false });
  return;
}
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

`git revert` of these commits is a complete and sufficient rollback, and this is
one of the cases the template's caveat explicitly permits:

- No migration, no schema change, no `app_settings` key, no backend change.
- **No live data is written by this diff.** It changes render conditions, one
  piece of local component state, and one in-memory guard in a client-side
  zustand store. It creates no Stripe charge, no wallet delta, no ride-state
  transition, and no `driver_insurance_periods` row — so there is nothing
  applied-to-live-data that a code revert would leave stranded.
- `_clearEpoch` is **in-memory only**. It is not persisted to AsyncStorage, not
  sent to the server, and not read by anything outside `rideStore.ts`, so a
  revert cannot leave a stale or unreadable value anywhere — the field simply
  stops existing, and `_clearedRideId` reverts to its previous meaning on the
  next app start.
- Reverting restores the previous (buggy) behaviour exactly; a rider mid-session
  during the revert sees only the old label on their next render.

Not feature-flagged. Justification: the change removes a wrong, actionable money
figure from a payment surface, and frees riders who are currently stuck on it.
Dark-shipping would mean deliberately leaving riders looking at
`Pay $0.00 & Done` while the flag was off, which is worse than the (bounded,
revertible) risk of shipping it on. Every fallback state introduced here is more
conservative than what it replaces — the button never quotes an amount it does
not know, and never charges when it cannot.

## 9. Verification performed

- [x] Blast-radius grep performed — `fetchRide`, `clearRide`, `alreadyPaid`,
      `_clearedRideId` across `rider-app/`; `docs/known-forks.md` checked for a
      sibling (none). For the store change, additionally: every screen's
      handling of a `cancelled` `currentRide` (only `driver-arriving.tsx:347`
      reacts, and without a toast), every `_clearedRideId:` write site in the
      store (3 retire-sites, all now epoch-bumped; 2 reset-sites, correctly
      not), and what `rideStore.cancel-flicker.test.ts` actually covers
      (`cancelRide`/`clearRide`/`fetchActiveRide` — not `fetchRide`). See §11.
- [x] Syntax/parse check — `tsc --noEmit --noResolve --skipLibCheck
      --jsx preserve` on all four changed files: **zero `TS1xxx` syntax
      errors**. All remaining diagnostics are `TS2307`/`TS2304`/`TS7006`/
      `TS2593`/`TS2591` (unresolved modules, missing `@types`) — artifacts of
      `--noResolve` with no `node_modules`. `rideStore.ts`'s two `TS18046`
      (`'error' is of type 'unknown'`) were confirmed **pre-existing** by
      running the same check against `git show HEAD:…` — 2 before, 2 after.
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
- [ ] **Automated tests NOT run.** 16 regression tests were written but could not
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
- The 16 new tests are unexecuted (see §9) — treat them as written-but-unproven.
  This matters more for the store change than the screen change: the store's
  guard is exercised by 8 other call sites this session could not run.

## 11. Root cause behind the *persistent* $0.00 screen — FIXED

The reporter confirmed the screen **persists and is tappable** (they escape by
pressing the $0.00 button), not a transition flash. That points past the render
bug to a second defect in the store, which is fixed here too.

### The defect

`_clearedRideId` in `rider-app/store/rideStore.ts` was a **permanent, one-way
latch**. `clearRide()` records the retired ride id; the latch is only reset by
`createRide` or by `fetchActiveRide` landing a *different* ride. `fetchRide`'s
guard tested that id alone:

```ts
if (get()._clearedRideId === rideId) { set({ isLoading: false }); return; }
```

Its own comment scopes the intent to *in-flight* responses ("If `clearRide()`
ran while this fetch was in-flight"), but an id-only test cannot express "while
in flight". It also discarded every **later, deliberate** re-fetch — including a
fresh mount of `/ride-completed` explicitly asking for that ride.

So re-entering `/ride-completed?rideId=X` after `clearRide()` (a
`ride_completed` push tap, `_layout.tsx:181`; or the in-app notification list,
`notifications.tsx:139` — neither consults `payment_status` or the latch) left
`currentRide` `null` **forever**: the fare read `$0.00`, the screen's
auto-dismiss effect could never fire because it keys on a `payment_status` that
stayed `undefined`, and the hardware back button plus `gestureEnabled: false`
meant no exit. The rider's only escape was pressing the $0.00 button, which
round-trips to the server purely to be told `already_paid` and navigates home
off the back of that.

### The fix

A monotonic `_clearEpoch`, bumped at all three sites that retire a ride
(`clearRide`, and both `cancelRide` set sites including the 409/terminal
branch). `fetchRide` snapshots it before issuing its request and compares after:

```ts
if (get()._clearedRideId === rideId && get()._clearEpoch !== clearEpochAtStart)
```

- clear happened **during** this request → epoch moved → stale, discard
  (the original race, still fixed)
- clear happened **before** it was issued → epoch same → the caller asked for
  this ride knowing it was cleared → honour it

### What was deliberately left alone

Two other consumers read `_clearedRideId` directly and still want the permanent
semantics. Neither is touched, and both are re-asserted by tests:

| Consumer | Why it stays a permanent latch |
|---|---|
| `fetchActiveRide` (`rideStore.ts:493`) | Guards **server read-after-write lag** — `/rides/active` still reporting a just-cancelled ride as active. That is a property of the server's state, not of request ordering; an epoch cannot express it. |
| `useRiderSocket.ts:147` (`ride_cancelled`) | Suppresses the server's **echo** of a cancel the rider already saw. Independent of `fetchRide` entirely. |

### Blast radius of the store change

`fetchRide` has 10 non-test call sites. The change only alters behaviour for a
fetch that **starts after** a clear for that same ride — for every other caller
`_clearedRideId !== rideId`, so the guard was already a no-op.

The one real behaviour change is a **post-cancel poll tick**: `ride-status.tsx`
(3s/15s), `driver-arriving.tsx` (5s), `driver-arrived.tsx` (15s) and
`useRideLocationFallback` each hold an interval that can fire in the narrow
window between `clearRide()` and unmount. Such a tick can now repopulate
`currentRide` with the cancelled ride. Verified safe, by reading every consumer:

- **Only one screen reacts to a cancelled `currentRide`** —
  `driver-arriving.tsx:347`, which calls `clearRide()` and routes home. **It
  shows no toast.** `ride-status.tsx`, `driver-arrived.tsx` and
  `ride-in-progress.tsx` have no cancelled-status watcher at all (their
  `cancelled` greps are all local effect-cleanup flags).
- **The cancel-flicker toast** — the actual bug the latch is remembered for —
  comes from `useRiderSocket`'s `ride_cancelled` handler, guarded by its own
  direct `_clearedRideId` read. Not on this path.
- **`rideStore.cancel-flicker.test.ts`** exercises `cancelRide`, `clearRide` and
  `fetchActiveRide` — **not** `fetchRide`. Nothing it pins is affected.
- A repopulated ride arrives in terminal `cancelled` state and `_clearedRideId`
  stays set, so the WS echo stays suppressed either way.

This case is pinned deliberately by a test rather than left implicit, so a
future reader sees it was considered and not overlooked.

### Screen-level guard kept as well

`ride-completed.tsx` still leaves for `/(tabs)` when `_clearedRideId === rideId`.
With the store fixed this is no longer load-bearing — the ride loads now, and
the auto-dismiss effect would catch the paid case a moment later — but it skips
the round trip and keeps the screen out of a dead end if the fetch fails for an
unrelated reason. Its comment was rewritten to say exactly that, so nobody
mistakes it for the fix.
