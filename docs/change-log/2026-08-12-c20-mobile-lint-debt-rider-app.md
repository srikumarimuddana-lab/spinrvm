# Change Impact & Risk Log — C20 mobile lint debt, rider-app

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | `claude/c20-mobile-lint-rider-app` |
| Related issue or gap ID | ACTION_ITEMS.md C20 "Mobile lint debt under the SDK 57 ruleset" |

This log covers the behavior-adjacent fixes in this branch:
`app/_layout.tsx` and `app/ride-completed.tsx`'s `react-hooks/refs`
write-during-render fixes, and the dead-code removal in
`app/ride-in-progress.tsx`. Everything else in the branch (unused vars/
imports, import ordering, entity-escaping, `no-undef` config fix,
`import/no-named-as-default`, guarded-require documentation) is pure
mechanical/style cleanup with zero behavior change — described in the PR
description instead, per CLAUDE.md's guidance that pure-additive/style
diffs don't need this template.

## 1. Issue / gap identified

1. Two lines wrote directly to a ref during a component's render body
   (`app/_layout.tsx`'s `pathnameRef.current = pathname` and
   `app/ride-completed.tsx`'s `handleSubmitRef.current = handleSubmit`),
   which the new `react-hooks/refs` ESLint rule (SDK 57's
   `eslint-config-expo` bump) flags as unsafe under concurrent rendering.
2. `app/ride-in-progress.tsx` had a ~20-line dead `handleSafety` function
   (defined, never called) found while removing an unused `useRef` import
   from the same file.

## 2. Root cause

1. Both refs use the common "always-latest-value ref" idiom — syncing a
   ref to the newest value/closure on every render so a listener/callback
   registered once can read a fresh value without needing it in a
   dependency array. Writing directly in the render body is the unsafe
   form of this idiom; a `useEffect` is the React-recommended safe form.
2. `handleSafety` was a leftover local implementation of the SOS
   confirm-then-call-911 flow, superseded by the shared `<SOSButton>`
   component (rendered twice on this screen) without the old function
   being deleted.

## 3. Fix / remediation

1. Wrapped both ref writes in a `useEffect` — `pathnameRef` with a
   `[pathname]` dependency (re-syncs on route change), `handleSubmitRef`
   with no dependency array (matches its original "every render" write).
2. Deleted the dead `handleSafety` function and its now-unused `Linking`
   import (its only consumer).

## 4. Risk & impact on existing functionality

- **`pathnameRef`** (`app/_layout.tsx`): grepped every read site — used
  only by the app-resume `AppState` listener and a cold-start timeout,
  both async/event-driven, never read synchronously during the same
  render. No other file reads this ref (it's module-local to `_layout.tsx`
  via closure).
- **`handleSubmitRef`** (`app/ride-completed.tsx`): read only inside the
  Stripe PaymentSheet `confirmHandler`, itself only invoked from a real
  user tap — async, never during the same render pass. No other file
  reads this ref.
- **`handleSafety` removal**: confirmed via grep that the function was
  never called, never passed as a prop, never referenced anywhere else in
  the file. The live SOS flow (`<SOSButton rideId={...}
  onTrigger={triggerEmergency} .../>`, rendered twice — once in the bottom
  sheet, once as a floating always-visible button) calls `triggerEmergency`
  directly and does not depend on the removed function in any way.
- Blast radius: isolated to these three files' internal render/ref timing
  and one dead-code deletion; no store, backend call, or shared component
  is touched.
- Not touching ride state machine, money, or a background loop. The
  removed function *was* SOS-adjacent code, which is why it got the same
  scrutiny as a safety-surface change even though it turned out to be
  fully dead.

## 5. User-experience effect

None expected for the ref-timing fixes — every reader of both refs is
async, so no reader could ever have observed the pre-commit value. For the
`handleSafety` removal: **no user-visible change**, since the function was
never reachable from any UI element; the actual SOS button behavior
(bottom-sheet + floating variants, both wired to `<SOSButton>`) is
unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/_layout.tsx` | Moved `pathnameRef` write into a `useEffect([pathname])` | Fixes `react-hooks/refs` write-during-render |
| `rider-app/app/ride-completed.tsx` | Moved `handleSubmitRef` write into a dependency-less `useEffect` | Fixes `react-hooks/refs` write-during-render |
| `rider-app/app/ride-in-progress.tsx` | Removed dead `handleSafety` function and its `Linking` import | `no-unused-vars`; verified dead, not wired to any UI |

## 7. Before / after

```tsx
# Before (_layout.tsx)
const pathnameRef = useRef(pathname);
pathnameRef.current = pathname;
```

```tsx
# After (_layout.tsx)
const pathnameRef = useRef(pathname);
useEffect(() => {
  pathnameRef.current = pathname;
}, [pathname]);
```

```tsx
# Before (ride-completed.tsx)
const handleSubmit = async (overrideCardId?: string) => { ... };
handleSubmitRef.current = handleSubmit;
```

```tsx
# After (ride-completed.tsx)
const handleSubmit = async (overrideCardId?: string) => { ... };
useEffect(() => {
  handleSubmitRef.current = handleSubmit;
});
```

## 8. Rollback plan

`git revert` is sufficient for all three changes — pure client-side
render-timing/dead-code fixes, no server-side state, no migration, no
feature flag, no live data touched.

## 9. Verification performed

- [x] Automated tests run: `tsc --noEmit` (clean), full rider-app `jest` suite (56 suites / 468 tests, all passing — one suite flaked once under concurrent-with-`expo export` resource contention and passed cleanly on an isolated rerun), `__tests__/ride-completed-route.test.tsx` (4/4) specifically for the `ride-completed.tsx` change
- [x] `npx expo export --platform web` production build run for real (not just dev server) — succeeded
- [ ] Manual repro steps followed in staging — **not performed**; this environment has no device/simulator to exercise the actual app-resume navigation, PaymentSheet confirm, or SOS button taps
- [x] Blast-radius grep performed: every read site of both refs confirmed async; every reference to `handleSafety` confirmed to be only its own definition
- [x] Reviewed against `react-hooks/refs` rule intent (concurrent-render safety)
- [ ] Feature-flagged — not applicable; pure internal timing fix + dead-code removal, nothing user-visible to flag

## What was NOT verified

- No real-device/simulator manual test of app-resume navigation, the ride-completed PaymentSheet flow, or the SOS button — reasoned about via code (read-site audit, grep) and the existing test suite passing, not visually confirmed. No visual regression tooling exists in this repo for React Native screens (standing gap, see ACTION_ITEMS.md).
- Not tested against a live backend, real Stripe PaymentSheet, or a live ride — existing mocked test fixtures were used as-is.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grep-verified for all three changes)
- [x] No silent behavior change to an already-shipped flow — User-experience effect section states "none expected"/"no user-visible change" with the reasoning, not silence
