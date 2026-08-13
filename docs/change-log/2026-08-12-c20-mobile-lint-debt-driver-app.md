# Change Impact & Risk Log — C20 mobile lint debt, driver-app

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | `claude/c20-mobile-lint-driver-app` |
| Related issue or gap ID | ACTION_ITEMS.md C20 "Mobile lint debt under the SDK 57 ruleset" |

This log covers only the one behavior-adjacent fix in this branch:
`components/dashboard/ActiveRidePanel.tsx`'s two `react-hooks/refs`
"Cannot update ref during render" findings. Everything else in the branch
(unused vars/imports, import ordering, entity-escaping, `no-undef` config
fix, `import/no-named-as-default`, guarded-require documentation) is pure
mechanical/style cleanup with zero behavior change — those are described in
the PR description instead, per CLAUDE.md's guidance that pure-additive/
style diffs don't need this template.

## 1. Issue / gap identified

Two lines in the driver's active-ride bottom-sheet panel wrote directly to
a ref during the component's render body (`insetsBottomRef.current =
insets.bottom;` and `snapToRef.current = snapTo;`), which the new
`react-hooks/refs` ESLint rule (part of the SDK 57 `eslint-config-expo`
bump) flags as unsafe: if React starts rendering this component but the
render is discarded before committing (e.g. under Suspense/concurrent
interruption), the ref has already been mutated even though that render's
output never shipped.

## 2. Root cause

Both are instances of the common "always-latest-value ref" idiom — syncing
a ref to the newest value/closure on every render so an event handler or
effect registered once (not re-created every render) can read a fresh
value without needing it in a dependency array. Writing directly in the
render body is a widely-used but technically-unsafe form of this idiom;
the React-recommended safe form defers the write to a `useEffect`.

## 3. Fix / remediation

Wrapped both assignments in a `useEffect`:
- `insetsBottomRef.current = insets.bottom` → `useEffect(() => {
  insetsBottomRef.current = insets.bottom; }, [insets.bottom]);`
- `snapToRef.current = snapTo` → `useEffect(() => { snapToRef.current =
  snapTo; });` (no dependency array, matching the original's "every
  render" semantics)

## 4. Risk & impact on existing functionality

- Grepped every read site of both refs in the file: `insetsBottomRef.current`
  is read only inside `collapsedOffsetRef`'s closure, itself only invoked
  from the `PanResponder` gesture handlers below (`onPanResponderMove`,
  `onPanResponderRelease`) — touch-event-driven, never synchronous during
  render. `snapToRef.current` is read only inside those same
  `PanResponder` handlers and one existing `useEffect` (`[rideState]`).
- No other file imports or reads these two refs — both are local to
  `ActiveRidePanel.tsx`.
- Blast radius: isolated to this one component's internal drag-gesture
  state; no other component, store, or backend call is touched.
- Not touching ride state machine, money, or a background loop.

## 5. User-experience effect

None expected. The draggable bottom sheet (drag-to-collapse/expand,
tap-to-snap) should behave identically — the only change is *when* the ref
value updates (after commit via `useEffect`, instead of during the render
pass), and every consumer of both refs is asynchronous (touch gesture
callback or an effect), so no reader could ever have observed the
pre-commit value anyway. No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | Moved 2 ref writes from render body into `useEffect` | Fixes `react-hooks/refs` "Cannot update ref during render"; guards against the ref-corruption-on-discarded-render bug class the rule targets |

## 7. Before / after

```tsx
# Before
const insetsBottomRef = useRef(insets.bottom);
insetsBottomRef.current = insets.bottom;
...
const snapToRef = useRef(snapTo);
snapToRef.current = snapTo;
```

```tsx
# After
const insetsBottomRef = useRef(insets.bottom);
useEffect(() => {
  insetsBottomRef.current = insets.bottom;
}, [insets.bottom]);
...
const snapToRef = useRef(snapTo);
useEffect(() => {
  snapToRef.current = snapTo;
});
```

## 8. Rollback plan

`git revert` is sufficient — this is a pure client-side render-timing fix
with no server-side state, no migration, no feature flag. No live data is
touched. Reverting restores the direct render-body writes exactly as they
were.

## 9. Verification performed

- [x] Automated tests run: `tsc --noEmit` (clean), `__tests__/components/ActiveRidePanel.test.tsx` (11/11 passing), full driver-app `jest` suite (378/379 passing — the 1 failure, `__tests__/androidAutoDistribution.test.ts`, is a pre-existing `eas.json` config/test mismatch unrelated to this branch, confirmed present on `main` and untouched by this diff)
- [x] `npx expo export --platform web` production build run for real (not just dev server) — succeeded
- [ ] Manual repro steps followed in staging — **not performed**; this environment has no device/simulator to exercise the actual drag gesture on the panel
- [x] Blast-radius grep performed: every read site of both refs confirmed async (gesture callback or effect), confirmed no other file references either ref
- [x] Reviewed against `react-hooks/refs` rule intent (concurrent-render safety)
- [ ] Feature-flagged — not applicable; this is a pure internal render-timing fix with no user-visible behavior change to flag

## What was NOT verified

- No real-device/simulator manual test of the drag-to-collapse gesture — reasoned about via code (read-site audit) and the existing snapshot/unit test suite passing, not visually confirmed on a device. This repo has no visual regression tooling for React Native screens (standing gap, see ACTION_ITEMS.md), so a timing-only change like this was reasoned about, not screenshotted.
- Not tested against a live backend or real ride data — `ActiveRidePanel.test.tsx`'s existing mocked fixtures were used as-is.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated to this one component, verified by grep)
- [x] No silent behavior change to an already-shipped flow — User-experience effect section states "none expected" with the reasoning, not silence
