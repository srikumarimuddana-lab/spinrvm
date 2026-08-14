# Change Impact & Risk Log — C20 mobile lint debt, driver-app round 4 (tier 4: exhaustive-deps)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers, dispatch (adjacent — see below) |
| PR / commit link | `claude/c20-lint-tier4-driver-app` |
| Related issue or gap ID | ACTION_ITEMS.md C20 "Mobile lint debt under the SDK 57 ruleset" (round 4, driver-app) |

This is round 4 of C20, scoped to driver-app only, closing the last
deferred category: `react-hooks/exhaustive-deps` — the category flagged
from round 1 onward as the highest-risk one and deliberately saved for
last. Unlike rounds 1-3's more mechanical categories (reordering,
suppressing verified-safe ref idioms, moving `Date.now()` calls),
`exhaustive-deps` can hide two opposite failure modes: a genuine
stale-closure bug on one side, and a genuine "adding the dep re-fires
something the author didn't want" regression on the other. Every one of
the 38 findings got individual review — no bulk/batch pass. rider-app's
own exhaustive-deps round is out of scope here, handled in parallel by a
sibling session in a separate worktree.

## 1. Issue / gap identified

Fresh `npx eslint . --format json` at branch start (not trusting the
task brief's own number until re-verified): 38 `react-hooks/exhaustive-deps`
findings across 20 files in driver-app. Confirmed against the actual
branch-start commit.

## 2. Root cause

Two fundamentally different root causes, matched 1:1 to the categorization
below:

- **(a) Lint-dishonest but behaviorally harmless (21/38)**: the missing
  value was already stable (a Zustand store action/setter, a `useRef`
  object itself, a route param immutable for the screen's lifetime, or
  `router`) but wasn't listed, so the linter couldn't statically prove
  the effect was safe even though it already was.
- **(b) Genuinely unstable value needing a real fix (7/38)**: a plain
  function/object was recreated every render and closed over reactive
  state without being memoized. The most significant instance:
  `useRideOfferSound()` returned a fresh `{ play, stop }` object literal
  every render (even though `play`/`stop` were each already stable) —
  this is a real "unstable object leaking through an otherwise-stable
  hook" bug, not just a lint annoyance, because it silently blocked a
  downstream consumer from safely listing it as a dependency.
- **(c) Deliberately excluded, correctly so — needs documentation not a
  fix (10/38)**: mount-only "don't clobber a user's existing selection"
  guards, and the `useRef(new Animated.Value(x)).current` stable
  animation-driver idiom (already established safe in round 2, but
  discovered this round to interact badly with `exhaustive-deps`
  specifically — see section 3).

## 3. Fix / remediation

**(a) — 21 findings**: added the missing value directly to each
dependency array. Verified stability by reading the value's own
declaration in every case (a Zustand store action is defined once inside
that store's `create()` and never redefined by any subsequent `set()` —
`set()` shallow-merges, so an action absent from a partial-update payload
keeps its original reference forever). Two were pure cleanup rather than
additions: extracting an inline `useDriverStore.getState().error` /
`navigationRef.isReady()` / `driverMe?.is_wav` expression (flagged as
"complex expression, extract to a variable") into a plain `const` before
the effect — same value, same re-run trigger, just satisfies the rule
honestly; and removing a module-level `StyleSheet.create()` constant from
a `useCallback`'s deps per the linter's own "outer scope values aren't
valid dependencies" guidance (`ActivityView.tsx`).

**(b) — 7 findings**: wrapped the unstable function in `useCallback` with
its own correct, minimal deps (usually just the one reactive value the
effect already depended on, so the wrapped function's identity changes on
exactly the same trigger — zero extra re-runs). The one root-cause fix:
`hooks/useRideOfferSound.ts` now returns
`useMemo(() => ({ play, stop }), [play, stop])` instead of a bare object
literal.

**(c) — 10 findings**: left the dependency array as-is and added a narrow
`// eslint-disable-next-line react-hooks/exhaustive-deps` with a one-line
(often longer, when the reasoning was non-obvious) comment stating
specifically why. New discovery this round, not previously documented
anywhere in this codebase: for the `useRef(new Animated.Value(x)).current`
idiom, actually testing "just add the ref-derived value" (in `otp.tsx`'s
`dotAnims`) revealed it introduces a **different** violation —
`react-hooks/refs`'s "Cannot access refs during render" — because a
dependency array is evaluated during render, so listing a `.current`-derived
value there is itself a render-time ref read. Every other instance of the
same idiom in this round's finding set (`goAnim`, `bannerHeight`,
`slideAnim`, `progressAnim`, `fadeAnim`/`slideUpAnim`) was left excluded
citing this same confirmed mechanism rather than individually re-tested.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (driver-app only), 20 files touched across
17 commits, plus 1 shared-within-driver-app hook
(`hooks/useRideOfferSound.ts`) with its blast radius explicitly checked
(see below).** No backend, rider-app, or admin-dashboard file touched.

Per-category risk:

- **(a) 21 findings**: zero behavioral risk. Every addition is a stable
  reference (verified, not assumed) being listed in a dependency array
  that already fires on its real triggers — Object.is comparison against
  an unchanging reference is always false-negative-free, so these
  dependency arrays behave identically before and after.
- **(b) 7 findings**: each wrapped function's *only* reactive input was
  already an existing dependency of the effect that calls it (e.g.
  `ride-detail.tsx`'s `loadRide` only reads `id`, already the effect's
  dep) — so wrapping in `useCallback([thatSameInput])` makes the function
  stable on exactly the trigger the effect already re-runs on, adding it
  changes nothing about firing frequency. Blast-radius grep performed for
  each: `become-driver.tsx`'s `fetchVehicleTypes`/`saveDraft` — used only
  within that one component; `vehicle-info.tsx`'s `fetchVehicleTypes` —
  also used as a retry-button `onPress` (direct call, unaffected by
  memoization); `ride-detail.tsx`'s `loadRide` — also passed to the
  shared `useCompletedRouteRefresh` hook, which was read and confirmed to
  already tolerate either a stable or per-render-fresh callback via its
  own internal ref (`refreshRef.current = refresh` every render); `profile.tsx`'s
  `refetchDriverMe` (TanStack Query's own memoized `refetch`, not
  authored by this PR) — only consumer is the `useFocusEffect` fixed
  here; `destination-mode.tsx`'s `fetchDestination` — used only in the
  one effect. The `useRideOfferSound.ts` fix's blast radius: grepped
  every file for `useRideOfferSound` — only `hooks/useDriverDashboard.ts`
  calls it (`services/notifeeService.ts` only *mentions* it in a
  comment). Single consumer, isolated.
- **(c) 10 findings**: zero behavioral risk by construction — the
  suppressed dependency array is byte-for-byte the array that already
  existed before this round; only a comment was added. The 5
  money-adjacent (`payout.tsx`'s `loadData` + its 4 Promise.all'd
  functions) were traced individually to confirm each closes over zero
  reactive component state before leaving as a suppression instead of a
  `useCallback` cascade — a useCallback cascade across 5 interdependent
  functions on a money screen was considered and rejected as unnecessary
  risk for a dependency set that never actually varies.

**Dispatch/insurance-period/earnings-adjacent files, per CLAUDE.md's risk
posture** — each got a dedicated, isolated commit with the specific trace
in its own commit message:

- `app/driver/(tabs)/index.tsx` (main dashboard): 3 findings, all (a) —
  `mapRef` (stable `useRef`), and an `error`/`clearError` cleanup that
  reads `error` from the store's existing full-object subscription
  instead of an inline `useDriverStore.getState().error` call. No ride-
  state-machine, dispatch-offer, or fare code touched.
- `app/driver/payout.tsx` (money): 2 findings — 1 (c) `loadData` (traced
  all 5 Promise.all'd functions for reactive closures — none), 1 (a)
  `clearError`. No wallet/fare write path touched.
- `app/index.tsx` + `app/profile-setup.tsx` (auth routing): 5 findings —
  the most-scrutinized of the (c) suppressions is `profile-setup.tsx`'s
  profile-already-complete-check effect, which calls
  `useAuthStore.setState({ user: fresh })` inside its own body. Adding
  `user`/`token` as deps (as the linter suggests) would make that
  `setState` re-trigger the very effect that called it — traced this
  explicitly and judged the re-run "very likely harmless" (same branch
  fires again, calls `router.replace()` a second time, returns) but
  could not verify `router.replace()`'s idempotency for a duplicate call
  with full certainty in this environment (no device/simulator), so
  excluded deliberately rather than guessed into the deps array.
- `components/panels/RideOfferPanel.tsx` (ride-offer countdown): 2
  findings, both (c) — the stable-ref animation idiom. No change to the
  `[incomingRide]` / `[countdownSeconds, maxCountdown]` triggers that
  actually drive the offer slide-in/progress-bar animation.
- `components/dashboard/ActiveRidePanel.tsx` (active-ride bottom sheet):
  1 finding, (a) — `loadNavApp` (stable navStore action).
- `hooks/useDriverDashboard.ts` (WS connection, dispatch-offer FCM/WS
  ingestion, online-flag hydration): 6 findings, split across 2 commits
  so the one real behavioral fix is independently revertable from the 5
  mechanical additions. None touched a ride-state-machine transition, a
  dispatch-offer accept/decline call, an insurance-period write, or a
  wallet/fare write path directly.

**No interaction with the 18 backend background loops, the ride state
machine, `driver_insurance_periods` writes, or any wallet/fare-settlement
code path** — this PR is 100% driver-app client code (screens/
components/hooks), no backend files touched.

**Standing discrepancy flagged, not silently absorbed**: `hooks/useDriverDashboard.ts`
still carries 6 pre-existing lint errors (1 `react-hooks/purity`, 4
`react-hooks/refs`, 1 `react-hooks/immutability`), and
`app/driver/(tabs)/index.tsx` carries the 2 already-documented
`react-hooks/refs` findings from round 2 — all 8 confirmed present in the
branch-start commit (diffed against `origin/main`, unrelated to this
round's changes; line numbers shift slightly across this PR's commits
because comments were added around them, but the underlying findings are
identical). ACTION_ITEMS.md's round-2/3 bullets describe
`purity`/`immutability`/read-during-render `refs` as closed to
(near-)zero for driver-app overall; this file's residual 6 is a
discrepancy worth a human look, out of scope for this round's
exhaustive-deps-only task.

## 5. User-experience effect

**No driver-visible change from the (a) or (c) fixes** — every one is
either a dependency-array annotation with a verified-stable reference, or
a documented suppression of an already-existing dependency array (byte-
identical behavior).

**The (b) fixes are also behavior-preserving, with one exception that is
an improvement, not a regression**: `hooks/useRideOfferSound.ts`'s object
memoization has a positive side effect — two *other*, pre-existing
`offerSound` dependency-array listings in `useDriverDashboard.ts` (the
`incomingRide`-cleared tone-stop effect, and the WS message handler) were
already "correctly" listing `offerSound` per lint rules, but because the
object was unstable, those effects/callbacks were silently
re-running/recreating on every render already — harmless in practice
(`.stop()`/`.play()` are cheap and idempotent) but real unnecessary
churn. This fix incidentally reduces that churn to only-when-actually-needed,
with zero change to the actual play/stop/interval/audio-mode logic
itself (verified: `playOnce`/`play`/`stop`'s own bodies and `useCallback`
deps are completely untouched).

Nobody (rider / corporate admin / internal admin) sees any difference —
this is a driver-app-only PR. A driver would not notice any of these
changes: the ride-offer tone still plays/loops/stops on the same
triggers, the offer countdown/slide-in animation is unchanged, the
dashboard map re-center behavior is unchanged, and no screen's copy,
timing, or navigation flow changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/_layout.tsx` | Added `initializeAuth`/`initializeLocation` to cold-start effect deps | (a) |
| `driver-app/app/become-driver.tsx` | `serviceAreaId` suppression (c); `fetchVehicleTypes`/`saveDraft` wrapped in `useCallback` (b) | mixed |
| `driver-app/app/driver/(tabs)/index.tsx` | Added `mapRef`; refactored `error`/`clearError` to use existing store subscription instead of inline `getState()` call | (a) |
| `driver-app/app/driver/(tabs)/profile.tsx` | Added `refetchDriverMe` to `useFocusEffect`'s inner `useCallback` | (b) |
| `driver-app/app/driver/chat.tsx` | Added `CHAT_STORAGE_KEY`/`setChatMessages` | (a) |
| `driver-app/app/driver/destination-mode.tsx` | `fetchDestination` wrapped in `useCallback` | (b) |
| `driver-app/app/driver/payout-history.tsx` | Added `fetchPayoutHistory` | (a) |
| `driver-app/app/driver/payout.tsx` | `loadData` suppression (c); added `clearError` (a) | mixed |
| `driver-app/app/driver/ride-detail.tsx` | `loadRide` wrapped in `useCallback` | (b) |
| `driver-app/app/driver/settings.tsx` | Added `loadAlertPrefs`/`loadLanguage`/`loadNavApp`; extracted `driverMeIsWav` variable | (a) |
| `driver-app/app/index.tsx` | Extracted `navReady` variable; added `logout`/`router` | (a) |
| `driver-app/app/otp.tsx` | Added `phoneNumber`/`router` (×2); `dotAnims` suppression | mixed |
| `driver-app/app/profile-setup.tsx` | Added `router` (×2); `token`/`user`/`serviceAreaId` suppressions | mixed |
| `driver-app/app/vehicle-info.tsx` | `fetchVehicleTypes` wrapped in `useCallback` | (b) |
| `driver-app/components/activity/ActivityView.tsx` | Removed unnecessary `styles` dep | (a) |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | Added `loadNavApp` | (a) |
| `driver-app/components/dashboard/DriverIdlePanel.tsx` | `goAnim` suppression | (c) |
| `driver-app/components/dashboard/DriverTopBar.tsx` | `bannerHeight` suppression | (c) |
| `driver-app/components/panels/RideOfferPanel.tsx` | `slideAnim`/`progressAnim` suppressions | (c) |
| `driver-app/hooks/useDriverDashboard.ts` | Added `fetchEarnings`/`foregroundLocationTransport`/`fetchActiveRide`/`hydrateDriverRideState`/`offerSound` across 5 effects; `fadeAnim`/`slideUpAnim` suppression | mixed |
| `driver-app/hooks/useRideOfferSound.ts` | Memoized returned `{ play, stop }` object | (b), root-cause fix |
| `ACTION_ITEMS.md` | Round 4 driver-app summary added to C20 | Documentation |

## 7. Before / after

**(b) root-cause fix** — `hooks/useRideOfferSound.ts`:

```ts
// Before
return { play, stop };
```

```ts
// After
return useMemo(() => ({ play, stop }), [play, stop]);
```

**(a) cleanup, not addition** — `app/driver/(tabs)/index.tsx`:

```tsx
// Before
useEffect(() => {
  const { error } = useDriverStore.getState();
  if (error) {
    showToast('error', 'Something Went Wrong', error || 'Please try again.');
    clearError();
  }
}, [useDriverStore.getState().error]);
```

```tsx
// After
// error is destructured from the top-level useDriverStore() call above
useEffect(() => {
  if (error) {
    showToast('error', 'Something Went Wrong', error || 'Please try again.');
    clearError();
  }
}, [error, clearError]);
```

**(c) suppression with a real discovered reason** — `app/otp.tsx`:

```tsx
// Before
useEffect(() => {
  dotAnims.forEach((anim, i) => {
    Animated.spring(anim, { toValue: i < code.length ? 1 : 0, ... }).start();
  });
}, [code]);
```

```tsx
// After
useEffect(() => {
  dotAnims.forEach((anim, i) => {
    Animated.spring(anim, { toValue: i < code.length ? 1 : 0, ... }).start();
  });
  // dotAnims intentionally excluded: adding it was TESTED and traded one
  // violation (exhaustive-deps) for another (react-hooks/refs — a
  // dependency array is evaluated during render, so listing a
  // ref-derived value there is itself a render-time ref read).
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [code]);
```

## 8. Rollback plan

`git revert` is sufficient for every commit in this PR — pure dependency-
array edits, lint-suppression comments, `useCallback` wrapping of
already-local functions, and one `useMemo` wrap in a single-consumer
hook. No server-side state, no migration, no feature flag, no live data
touched. Each of the 17 commits is independently revertable; the
`useDriverDashboard.ts` work was deliberately split into 2 commits (5
mechanical dependency additions, then the `offerSound`/`useRideOfferSound.ts`
root-cause fix) specifically so the one real behavioral change in this
PR can be reverted in isolation without touching the other 5 findings in
that file.

## 9. Verification performed

- [x] Automated tests run: `yarn tsc --noEmit` clean before and after
  every commit (0 errors both times). Full `yarn jest` at the end:
  **379/379 passing, 53/53 suites** — the previously-known
  `androidAutoDistribution.test.ts` flake is confirmed fixed at its root
  (per PR #3842, referenced in this round's task brief) and did not
  reappear; no failures of any kind. Targeted re-runs after touching
  risk-posture files: `__tests__/components/ActiveRidePanel.test.tsx`
  (11/11), `__tests__/components/RideOfferPanel.test.tsx` (17/17, run
  twice — once after the ref-suppression commit, once after the
  `offerSound` root-cause fix, both green),
  `hooks/__tests__/useDriverDashboard.chat.test.ts` (8/8, also run
  twice, same reason).
- [x] `npx eslint . --format json` run fresh at the start (38
  `exhaustive-deps` findings, confirmed against the actual branch-start
  commit) and end (0) of this round. Total driver-app lint problems: 57
  (8 errors, 49 warnings) → 19 (8 errors, 11 warnings) — the unchanged 8
  errors are pre-existing `purity`/`refs`/`immutability` findings (see
  section 4's discrepancy note); the remaining 11 warnings are
  pre-existing `@typescript-eslint/no-require-imports` in `__tests__/`
  files, both untouched by this round and confirmed via direct
  inspection of the after-run's message list.
- [x] Blast-radius grep performed for every function wrapped in
  `useCallback`/`useMemo` (see section 4) and for `useRideOfferSound`'s
  only consumer.
- [x] Reviewed against CLAUDE.md's dispatch/insurance-period/earnings
  risk posture: `driver/(tabs)/index.tsx`, `driver/payout.tsx`,
  `app/index.tsx` + `profile-setup.tsx`, `RideOfferPanel.tsx`,
  `ActiveRidePanel.tsx`, and `hooks/useDriverDashboard.ts` each got a
  dedicated commit with the specific behavioral trace documented in
  section 4 above and in that commit's own message — not a generic
  "looks fine."
- [ ] Feature-flagged — not applicable; none of these changes are
  user-visible or behavior-changing enough to warrant a flag (dependency-
  array edits, lint suppressions, `useCallback`/`useMemo` wrapping with
  verified-identical trigger semantics).

## What was NOT verified

- **No manual device/simulator test.** This environment has no device/
  simulator to exercise the actual driver-app UI — in particular, no
  audible confirmation that the ride-offer tone still plays/loops/stops
  correctly after the `useRideOfferSound.ts` change, no visual
  confirmation of the offer-countdown slide-in/progress-bar animation, no
  confirmation of the active-ride bottom sheet's phase-driven
  show/hide animation, and no live-device test of the WebSocket
  reconnect path (`connectWebSocket`'s `fetchActiveRide` addition) or the
  FCM foreground-message path (`offerSound` addition). Reasoned about via
  code trace (every wrapped function's own deps and every store action's
  `create()`-time stability verified by reading the source) and the
  passing unit/component test suite, not visually/audibly confirmed on a
  device. Same standing gap noted in rounds 1-3's change-logs — this
  repo has no visual or audio regression tooling for React Native
  screens.
- **`router.replace()`'s idempotency for a duplicate call was not
  verified** in the one case where it mattered
  (`profile-setup.tsx`'s profile-already-complete-check effect) — this
  is exactly why `token`/`user` were left excluded rather than added;
  the uncertainty was resolved by choosing the more conservative option,
  not by testing the actual behavior.
- **No production build (`expo export`) run this round.** Round 1's
  change-log already ran and confirmed one for driver-app on a closely
  preceding commit; this round's changes are narrower in kind
  (dependency-array edits, suppressions, `useCallback`/`useMemo`
  wrapping) than round 1's broader mechanical sweep, and `tsc --noEmit` +
  the full jest suite were judged sufficient verification for changes of
  this shape. Flagging explicitly rather than implying it was covered.
- **The 8 pre-existing `purity`/`refs`/`immutability` errors flagged in
  section 4 were not investigated or fixed** — confirmed present before
  this round's changes (diffed against `origin/main`) and explicitly out
  of scope for an exhaustive-deps-only round; flagged as a discrepancy
  against ACTION_ITEMS.md's prior "closed to near-zero" claim rather than
  silently left unmentioned.
- **Not tested against a live backend or real ride/driver data** — the
  existing test suites' mocked fixtures were used as-is; this PR adds no
  new automated tests (a lint/behavior-preserving cleanup pass, plus one
  genuine root-cause hook fix that is itself behavior-preserving from the
  caller's perspective — same "not applicable" call as prior rounds).
- **No new regression test was added for the `offerSound` object-identity
  fix or for `profile-setup.tsx`'s excluded `user`/`token` feedback-loop
  risk** — both were verified by code trace and the existing test suite
  passing, not by a new test that would fail if either were reverted.
  Flagging as a gap for a future round rather than claiming coverage
  that doesn't exist.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` per
  commit; the one real behavioral fix is isolated to its own commit)
- [x] Blast radius is stated, not assumed — every `useCallback`/`useMemo`
  wrap's consumers were grepped and listed (section 4), not a blanket
  "reviewed, safe"
- [x] No silent behavior change to an already-shipped flow — the "User
  Experience Effect" section states no driver-visible change and
  explicitly calls out the one case (`offerSound` churn reduction) that
  is a positive side effect rather than neutral; zero findings were left
  in category (d) that would otherwise need explicit flagging as
  unresolved risk, and the one genuinely uncertain case
  (`profile-setup.tsx`'s `user`/`token`) was resolved conservatively
  (excluded) rather than guessed
