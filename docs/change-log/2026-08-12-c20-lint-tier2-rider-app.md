# Change Impact & Risk Log — C20 mobile lint debt, rider-app, round 2 (tier 2)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | `claude/c20-lint-tier2-rider-app` |
| Related issue or gap ID | ACTION_ITEMS.md C20 "Mobile lint debt under the SDK 57 ruleset" (round 2) |

This is round 2 of C20, targeting four specific ESLint categories left
deliberately deferred by round 1 (PR #3777, `docs/change-log/2026-08-12-c20-mobile-lint-debt-rider-app.md`):
`react-hooks/preserve-manual-memoization`, `react-hooks/purity`,
`react-hooks/immutability`, and `react-hooks/refs` **read**-during-render
findings only (write-during-render was already fixed in round 1; none found
remaining — no discrepancy to flag). `react-hooks/exhaustive-deps`,
`react-hooks/set-state-in-effect`, and the project's own
`no-restricted-syntax` raw-`error.message` rule remain explicitly out of
scope (left for a future round/product decision), same as round 1. Driver-app
and backend were not touched.

## 1. Issue / gap identified

164 real `yarn lint` errors remained in rider-app after round 1, split
across several categories. This round targets four of them (measured fresh
at start, not trusted from ACTION_ITEMS.md's numbers, which round 1 already
found were stale once before): `react-hooks/preserve-manual-memoization`
(2), `react-hooks/purity` (9), `react-hooks/immutability` (11),
`react-hooks/refs` read-during-render (98) — 120 of the 164, leaving 44
(`no-restricted-syntax` 14, `react-hooks/set-state-in-effect` 32,
`import/no-unresolved` 1 minus overlap — see exact math in section 9)
deliberately untouched.

## 2. Root cause

Same root cause as round 1: `eslint-config-expo`'s SDK 57 bump pulled in
`eslint-plugin-react-hooks` v7's React Compiler rule set, which is stricter
than the plain `exhaustive-deps`/`rules-of-hooks` rules the codebase was
originally written against. Per-category root cause of what was actually
found (not always what the rule name suggests):

- **`refs`**: two distinct patterns — (a) one site (`confirm-pickup.tsx`)
  where a plain, never-reassigned value was read via `.current` directly in
  JSX; (b) ~15 sites of the extremely common React Native
  `useRef(new Animated.Value(x)).current` idiom for native-driver
  animations, which the rule flags because it can't distinguish "a ref that
  holds an intentionally-mutable, never-reassigned instance" from "a ref
  being used as a render-time data source."
- **`purity`**: a mix of two real bugs (one `Math.random()` re-randomizing
  every render, one `Date.now()` inside a `useMemo` whose single-evaluation
  isn't actually guaranteed by React) and several false positives where the
  rule's static analysis flags an impure call anywhere lexically inside a
  component function, including inside event-handler closures that never
  run during render.
- **`immutability`**: **not** the `array.push()`/`obj.field = x` mutation
  pattern the rule name and this task's brief both assumed — every finding
  was a "function accessed before declared" pattern (a mount `useEffect`
  referencing a fetch/load function defined later in the same component),
  except one genuine self-reference (`useRiderSocket.ts`'s recursive
  reconnect).
- **`preserve-manual-memoization`**: a `useMemo` reading the same nested
  field twice, once optional-chained and once not, which makes the
  compiler's dependency inference land on the coarser parent object instead
  of the narrow field declared in the dependency array.

## 3. Fix / remediation

See the four commits on this branch, each scoped to one rule category:

1. `react-hooks/refs` (98→0): converted one genuinely-render-needed ref to
   `useState`; extracted a shared `hooks/useAnimatedValue.ts`
   (`useAnimatedValue`/`useAnimatedValues`/`useStableRef`) centralizing the
   Animated.Value/PanResponder pattern's one audited suppression, so every
   call site gets a plain value back instead of scattering ~90 individual
   disables.
2. `react-hooks/purity` (9→0): 2 real fixes (deterministic per-driver
   fallback heading instead of `Math.random()`; `useMemo`→`useState` for a
   one-time `Date.now()` read) + 1 behavior-preserving restructure (moved a
   `Date.now()`-derived JSX prop pair into the button's `onPress` handler,
   which is actually *more* correct than the old every-render value) + 5
   verified-safe narrow suppressions (event-handler-only calls the rule's
   static analysis can't distinguish from render, and two display-only
   FlatList `renderItem` reads with no money/booking logic downstream).
3. `react-hooks/immutability` (11→0): reordered 9 functions above the
   `useEffect` that references them (pure reordering, no logic touched) + 1
   narrow suppression for `useRiderSocket.ts`'s genuine self-referencing
   reconnect callback.
4. `react-hooks/preserve-manual-memoization` (2→0): read a nested field once
   into a local const instead of twice (once optional-chained, once not) so
   the compiler's inferred dependency matches the declared one.

## 4. Risk & impact on existing functionality

- **Blast radius, per fix category:**
  - `hooks/useAnimatedValue.ts` is new and rider-app-local (not in
    `shared/`, so driver-app is unaffected). Grepped for existing
    `useAnimatedValue`/`useStableRef` names before adding — none existed.
    Consumed by 10 files, all updated in the same commit: `Toast.tsx`,
    `SkeletonBox.tsx`, `BrandSplash.tsx`, `SchedulePicker.tsx`,
    `driver-arriving.tsx`, `otp.tsx`, `payment-confirm.tsx`,
    `ride-completed.tsx`, `ride-options.tsx`, `verify-email.tsx`. No other
    file imports from these components' internals (checked: nothing reads
    e.g. `Toast`'s `translateY` from outside the component — it's not
    exported).
  - `confirm-pickup.tsx`'s `originalLat`/`originalLng`: grepped every read
    site in the file (6 total) — all now read the state variable directly
    instead of `.current`; no other file imports anything from this screen.
  - `ride-options.tsx`'s schedule-bounds change: `scheduleBounds` state is
    local to `RideOptionsScreenContent`; only consumed by the
    `<SchedulePicker>` JSX props in the same file. `fallbackHeading` is a
    new file-local (unexported) function — no other consumer possible.
  - The 9 reordered functions (`become-driver.tsx`, `legal.tsx`,
    `manage-cards.tsx`, `pick-on-map.tsx`, `promotions.tsx`,
    `ride-details.tsx`, `ride-tracking-webview.tsx`, `saved-places.tsx`,
    `scheduled-rides.tsx`) are each local to their own screen component;
    none are exported or shared. Reordering a `const fn = () => {}`
    declaration relative to a `useEffect` in the same function body has no
    runtime effect as long as nothing between the old and new position
    depends on execution order — checked for each (no such dependency
    found).
  - `useRiderSocket.ts` is shared by 4 files: `ai-assistant.tsx`,
    `app/_layout.tsx`, `ride-status.tsx`, `store/rideStore.ts`. This is the
    **highest-blast-radius file touched this round** — it drives the live
    WebSocket connection for an active ride (dispatch-adjacent). No logic
    was changed here, only a suppression comment added at the existing
    recursive `connect()` call site; verified the reconnect's existing
    `connectGenRef`/`myGen` generation guard (unchanged) already bounds the
    stale-closure risk the linter flags.
  - `ride-completed.tsx`/`ride-details.tsx`'s memoization fix: both
    `plannedSegments` are purely local `useMemo` values, consumed only
    within their own component's JSX; no other file reads them.
- **Interaction with ride state machine / money / background loops**: none.
  No route/dispatch/payment logic paths were changed — every fix is either
  a pure reordering, a pure ref→state/hook-extraction with identical
  runtime value, or a suppression comment. The `useRiderSocket.ts` touch is
  the only file with any adjacency to a live-ride-critical surface, and it
  is comment-only (no code path changed).
- **Side effect discovered, not introduced**: reordering the 9 immutability
  fixes made 8 previously linter-invisible `react-hooks/set-state-in-effect`
  findings visible in the exact same functions (rider-app-wide count
  32→40) — confirmed via before/after diff of the lint JSON output. These
  are the same pre-existing `setLoading(false)`-inside-a-mount-effect calls
  that already ran identically before this change; the linter simply
  couldn't statically trace into a forward-referenced function declaration
  before the reorder. No new runtime behavior. Left untouched — explicitly
  out of scope this round.

## 5. User-experience effect

- **Rider-facing, one intentional improvement**: driver markers on the
  vehicle-selection map (`ride-options.tsx`) that lack a real `heading`
  value from the backend now show a **stable** fallback rotation (derived
  from the driver's id) instead of a fresh random rotation on every
  re-render. Previously this would visibly jitter/snap on every
  driver-location poll for any driver without heading data — this fix
  removes that jitter. No change for drivers that already report a real
  heading.
- **Rider-facing, one intentional improvement**: the "Pickup time" schedule
  picker's min/max selectable bounds now reflect "now" as of the moment the
  rider taps to open it, rather than a value that recomputed on every
  incidental re-render of the ride-options screen. In practice this is
  equivalent-or-better (was previously "always fresh because always
  recomputed"; is now "fresh as of open time," which is the only moment it
  actually matters for validity).
- **Everything else: no rider/driver/admin-facing behavior change** — pure
  reordering, ref/state-shape changes with identical resolved values, and
  suppression comments. None of this is visible mid-session in a way that
  differs from before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/hooks/useAnimatedValue.ts` | New — `useAnimatedValue`/`useAnimatedValues`/`useStableRef` | Centralize the one audited `react-hooks/refs` suppression for the Animated.Value/PanResponder idiom |
| `rider-app/app/confirm-pickup.tsx` | `originalLat`/`originalLng` `useRef`→`useState` | `refs`: value read in JSX, not a ref-appropriate case |
| `rider-app/app/driver-arriving.tsx`, `otp.tsx`, `payment-confirm.tsx`, `ride-completed.tsx`, `ride-options.tsx`, `verify-email.tsx`, `components/BrandSplash.tsx`, `components/SchedulePicker.tsx`, `components/SkeletonBox.tsx`, `components/Toast.tsx` | `useRef(new Animated.Value(x)).current` → `useAnimatedValue(x)` (or `useAnimatedValues`/`useStableRef` for arrays/PanResponder) | `refs`: same fix via the shared hook |
| `rider-app/app/chat-driver.tsx` | Narrow `react-hooks/purity` suppression | Verified event-handler-only, not render |
| `rider-app/app/ride-options.tsx` | `Math.random()` heading → deterministic hash; `Date.now()` JSX props → state set in `onPress`; 1 narrow purity suppression | `purity`: 2 real fixes + 1 verified-safe suppression |
| `rider-app/components/SchedulePicker.tsx` | `initDate` `useMemo`→`useState` | `purity`: real fix, one-time impure read belongs in `useState`'s lazy initializer |
| `rider-app/app/scheduled-rides.tsx` | `loadRides` reordered above its effect; 2 narrow purity suppressions | `immutability` + `purity` (bundled, same small file) |
| `rider-app/app/become-driver.tsx`, `legal.tsx`, `manage-cards.tsx`, `pick-on-map.tsx`, `promotions.tsx`, `ride-tracking-webview.tsx`, `saved-places.tsx` | Function reordered above its effect | `immutability`: "accessed before declared" |
| `rider-app/app/ride-details.tsx` | `fetchRide` reordered above its effect; `plannedSegments` memo dependency fix | `immutability` + `preserve-manual-memoization` (bundled, same file) |
| `rider-app/hooks/useRiderSocket.ts` | Narrow `react-hooks/immutability` suppression on the recursive `connect()` call | Genuine self-reference, not reorderable; existing generation guard already bounds the risk |
| `rider-app/app/ride-completed.tsx` | `plannedSegments` memo dependency fix | `preserve-manual-memoization` |
| `ACTION_ITEMS.md` | C20 section updated with round-2 results | Required by CLAUDE.md |
| `docs/change-log/2026-08-12-c20-lint-tier2-rider-app.md` | New (this file) | Required Change Impact Log |

## 7. Before / after

```tsx
# Before (ride-options.tsx) — real bug: re-randomizes every render
heading={(driver as any).heading ?? Math.random() * 360}
```
```tsx
# After — stable per-driver, still varied across drivers
heading={(driver as any).heading ?? fallbackHeading(driver.id)}
```

```tsx
# Before (~15 sites) — flagged react-hooks/refs
const translateY = useRef(new Animated.Value(-120)).current;
```
```tsx
# After
const translateY = useAnimatedValue(-120);
```

```tsx
# Before (9 files) — flagged react-hooks/immutability
useEffect(() => { loadRides(); }, []);
const loadRides = async () => { ... };
```
```tsx
# After
const loadRides = async () => { ... };
useEffect(() => { loadRides(); }, []);
```

```tsx
# Before (ride-completed.tsx / ride-details.tsx) — memoization silently not preserved
const plannedSegments = useMemo(
  () => toReactNativeSegments(currentRide?.planned_route_polyline ? [currentRide.planned_route_polyline] : []),
  [currentRide?.planned_route_polyline],
);
```
```tsx
# After
const plannedSegments = useMemo(() => {
  const polyline = currentRide?.planned_route_polyline;
  return toReactNativeSegments(polyline ? [polyline] : []);
}, [currentRide?.planned_route_polyline]);
```

## 8. Rollback plan

`git-revert-safe` — all four commits are pure client-side code (no
migration, no `app_settings` row, no Stripe/webhook/wallet state, no ride
state machine transition). Each of the four commits (refs, purity,
immutability, preserve-manual-memoization) can be reverted independently
without affecting the others, since they touch non-overlapping rule
categories (the two files with bundled fixes — `scheduled-rides.tsx`,
`ride-details.tsx` — would need both their commits reverted together to
fully undo, but reverting either one alone leaves the other's fix intact
and functional).

## 9. Verification performed

- [x] `yarn lint` run fresh at start of session (not trusted from
  ACTION_ITEMS.md) to get real baseline: 232 problems (164 errors, 68
  warnings) via `expo lint`; 167 errors via a broader `npx eslint`
  invocation covering `__tests__`/`e2e`/`scripts` too (expo lint's default
  target set is narrower) — per-rule breakdown taken from the broader run
  since it's a superset.
- [x] Per-rule counts before this round (broad run): `react-hooks/refs` 98,
  `react-hooks/set-state-in-effect` 32, `no-restricted-syntax` 14,
  `react-hooks/immutability` 11, `react-hooks/purity` 9,
  `react-hooks/preserve-manual-memoization` 2, `import/no-unresolved` 1.
- [x] After all four commits: `yarn lint` → 122 problems (53 errors, 69
  warnings); broad `npx eslint` run → 55 errors:
  `react-hooks/set-state-in-effect` 40, `no-restricted-syntax` 14,
  `import/no-unresolved` 1. All four target rules confirmed at 0 via
  `grep` over the raw `yarn lint` output.
- [x] `npx tsc --noEmit` — clean, run after every commit and again at the
  end on the full branch state.
- [x] Full rider-app `npx jest` suite — 56/56 suites, 468/468 tests, run
  after every commit and again at the end (matches the pre-change baseline
  exactly; also matches round 1's documented baseline).
- [x] `npx expo export --platform web` — **not run this round** (round 1
  ran it; this round's changes are narrower in scope — pure hook
  extraction, reordering, and memoization/state fixes with no new native
  module or bundler-relevant surface — but see section 10, this is a real
  gap, not silently assumed equivalent to round 1's coverage).
- [x] Blast-radius grep performed for every shared/multi-consumer touch
  point (`hooks/useAnimatedValue.ts`'s 10 consumers, `useRiderSocket.ts`'s
  4 consumers) — see section 4.
- [ ] Feature-flagged — not applicable; no user-visible flow changed except
  the two documented UX improvements (marker jitter fix, schedule-bounds
  freshness), neither of which is a new feature requiring a flag (both are
  bug fixes to existing, already-shipped behavior).

## 10. What was NOT verified

- **No real-device/simulator manual test** — same standing gap as round 1.
  Not tested: the schedule picker's calendar/time UI, the OTP/verify-email
  shake animation, the payment-confirm/ride-completed fade-in animations,
  the toast swipe-to-dismiss gesture, the ride-options vehicle-card
  scale/image animations, or the driver-marker heading rotation on a real
  map. All reasoned about via code trace + the passing test suite, not
  visually confirmed. No visual regression tooling exists in this repo for
  React Native screens (standing gap, tracked in ACTION_ITEMS.md).
- **`npx expo export --platform web` was not re-run this round** (see
  section 9) — round 1 ran a full production web export as an extra check
  beyond tsc/jest; this round relied on tsc + jest only. The change surface
  this round (hook extraction, function reordering, memoization deps) is
  lower-risk than round 1's write-during-render ref timing fixes, but this
  is still a real gap in verification depth relative to round 1, not a
  deliberate equivalence judgment — flagging it rather than implying full
  parity.
- **Not tested against a live backend or a real active ride** — the
  `useRiderSocket.ts` touch (comment-only, no logic change) was not
  exercised against a live WebSocket connection; existing mocked test
  fixtures were used as-is, same as round 1.
- **`react-hooks/set-state-in-effect`'s new visibility (32→40) was not
  investigated further** — confirmed via lint-JSON diff that all 8 new
  findings sit in functions this round already touched (same setState
  calls, not new ones), but did not individually re-verify each of the 8
  functions' actual runtime timing beyond that diff — a future round
  tackling `set-state-in-effect` should re-measure fresh rather than trust
  this session's diff-based attribution.
