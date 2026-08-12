# Change Impact & Risk Log — C20 mobile lint debt, driver-app round 3 (tier 3)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | `claude/c20-lint-tier3-driver-app` |
| Related issue or gap ID | ACTION_ITEMS.md C20 "Mobile lint debt under the SDK 57 ruleset" (round 3, driver-app) |

This is round 3 of C20, scoped to driver-app only, closing the two
categories round 2 explicitly deferred: `react-hooks/set-state-in-effect`
and `no-restricted-syntax`. rider-app is being handled in parallel by a
sibling session in a separate worktree and is out of scope here.

## 1. Issue / gap identified

Fresh `npx eslint . --format json` at the start of this round (not
trusting round 2's own change-log, which had already drifted): 30
`react-hooks/set-state-in-effect` findings and 2 `no-restricted-syntax`
findings in driver-app. (Round 2's log said 27 for the former — drifted
by 3 between rounds; ACTION_ITEMS.md's copy of that number was
additionally stale. 30 was confirmed against the actual branch-start
commit before any work began.)

## 2. Root cause

Two different root causes:

- **`no-restricted-syntax`**: the project's own custom rule bans raw
  `error.message` member access in `app/**` and `store/**`, regardless of
  whether the call site is user-facing or a logger call — the rule is
  syntax-level, not context-aware. Both of this round's 2 findings turned
  out to be `console.warn`/`console.log` calls (never shown to the
  driver), which the rule still flags even though its own message text
  ("For logging, pass the whole error object") describes a different fix
  for that case than the user-facing one.
- **`react-hooks/set-state-in-effect`**: two recurring shapes account for
  all 30 findings — (1) a `fetch*`/`load*` async function called
  synchronously at the top of a mount-only effect, which the compiler
  flags even though the function's own `setState` calls only happen after
  an `await`, never synchronously during the effect's own execution; (2)
  a reset/re-seed of local UI state keyed on a prop/store value changing
  (ride phase, driver profile row, modal visibility), which the compiler
  flags as "setState in effect" regardless of whether the reset could
  ever feed back into its own trigger.

## 3. Fix / remediation

- **`no-restricted-syntax` (2/2)**: both were logger-only, not
  user-facing — fixed by passing the whole caught error object to
  `console.warn`/`console.log` instead of `.message`, per the rule's own
  message and matching existing house style (`app/_layout.tsx` and many
  other files already do this). `getApiErrorMessage` was **not** used at
  either site, since it exists for user-visible text and neither site
  displays anything to the driver — flagging this explicitly since the
  task's own instructions anticipated this exact discrepancy from the
  default assumption.
- **`react-hooks/set-state-in-effect` (30 findings)**: per-finding
  review, not a bulk pass. Categorized every finding into:
  - **(a) benign, safe pattern (29/30)**: narrow
    `eslint-disable-next-line react-hooks/set-state-in-effect` with a
    one-line comment stating specifically why the finding is safe (stable
    `useCallback` dep, one-way sync that never reads back, mount-only
    with empty deps, or — for the single most sensitive finding — a
    traced ref-guard chain proving the effect physically cannot re-fire).
  - **(b) refactorable, zero behavior change (1/30)**: `CarMarker.tsx`'s
    `imageFailed` reset rewritten to React's documented "adjust state
    during render" pattern instead of a suppression.
  - **(c) suspicious / needs a human decision (0/30)**: none found in
    this batch. Every dependency array was checked for a path where the
    setState call could feed back into its own trigger; none did.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the 24 files touched, single-surface
(driver-app only).** No shared component/hook/utility outside these files
was modified in a way that changes its exported behavior — every
`set-state-in-effect` fix is either a narrow inline suppression (zero
code-behavior change) or, in the one refactor case, a same-semantics
rewrite confirmed isolated to that one component (grepped `CarMarker.tsx`
for every reader of `imageFailed`/`imageUri` — both component-local, no
other file reads them).

Per-finding verification performed (not "checked, looks fine"):

- **Fetch-on-mount suppressions**: confirmed each flagged function's
  `setState` calls all occur after an `await` inside the function body,
  never synchronously in the effect itself, and that the effect's dep
  array is empty (or, for `emergency-contacts.tsx` / `lost-and-found-chat.tsx`,
  that the referenced function is `useCallback([])` with a genuinely
  stable identity — read each declaration to confirm the empty dep array
  directly, not assumed).
- **Reset/sync suppressions**: for every one, traced whether the
  setState call(s) could ever appear in, or derive from, the effect's own
  dependency array. None do — `driver/(tabs)/index.tsx`'s heatmap/
  countdown/route resets key off `rideState`/`_hasRidePolyline` and never
  write either; `payout.tsx`'s GST sync and `settings.tsx`'s two toggle
  syncs are one-way (local edit state is never fed back into the
  `driverMe`/`prefsResponse` query objects they're seeded from);
  `ActiveRidePanel.tsx`'s two resets are local DISPLAY-only accumulators
  (`liveDistanceKm` is a client-side odometer shown to the driver, not
  the fare-settlement distance, which is computed server-side and
  untouched by this panel).
- **`useDriverDashboard.ts` — the most scrutinized finding**: the
  one-time `isOnline` profile-hydration effect. Traced the full guard
  chain: `onlineHydratedRef.current = true` executes on the same pass,
  before the `setIsOnline` call, so every subsequent run of the effect
  short-circuits at the top-of-body ref check — it cannot re-fire even
  though `isOnline` is itself listed in the dep array (it's there so a
  toggle-vs-hydration race is correctly detected, not because the effect
  is meant to re-run on every `isOnline` change). Confirmed the call only
  ever sets the driver-toggled `isOnline` flag, never the system-computed
  `is_available` field — the CLAUDE.md invariant `is_available ⇒
  is_online` is not touched by this change; `is_available` remains
  entirely backend-computed and this hook never writes it.
- **Dispatch/payments-risk-posture files got dedicated, isolated
  commits**: `driver/(tabs)/index.tsx` (main dashboard — ride-offer
  countdown, heatmap, route display), `driver/payout.tsx` (money — GST/
  bank/Stripe/T4A screen), `components/dashboard/ActiveRidePanel.tsx`
  (active-ride bottom sheet), `hooks/useDriverDashboard.ts` (WebSocket
  connection + online-flag hydration). None of the 11 findings across
  these 4 files touch ride-state-machine transitions
  (`_require_ride_in_state` equivalents live server-side and are
  untouched), dispatch-offer accept/decline logic, or any wallet/fare
  write path — every one is local UI-display state or a one-way sync
  from already-fetched server data.
- Not touching backend ride state machine, dispatch matching, payment
  settlement, auth, corporate billing, or safety code paths — this PR is
  100% driver-app client code (screens/components/hooks), no backend
  files touched.

## 5. User-experience effect

**No behavior change from the 29 category-(a) suppressions or the 1
category-(b) refactor** — every set-state-in-effect fix is a lint
annotation or a same-semantics rewrite with identical runtime output. The
CarMarker.tsx refactor is arguably a very minor improvement (removes a
one-render flash of the fallback marker image after `imageUri` changes,
in rare cases where the previous `imageUri`'s load had failed) but
introduces no new visible states.

**The `no-restricted-syntax` fix is a real (if small) UX-adjacent
change** — but only to what appears in developer-facing device logs, not
to anything a driver sees:
- Before: `console.warn('[DriverProfile] company-info fetch failed:', e?.message ?? e)` /
  `console.log('[ProfileSetup] /auth/me refetch failed:', err?.message || err)`
- After: the same two log lines, now passing the whole caught error
  object instead of extracting `.message` first.
- Neither site renders anything to the driver — no Alert, Toast, or
  inline error text reads from either catch block — so there is **no
  driver-visible UX change** from this fix, and nothing to review against
  the customer-centric tone standard.

Nobody (rider / driver / corporate admin / internal admin) sees a
different screen, copy string, or timing as a result of this PR.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/profile.tsx` | `console.warn` now passes the whole error object instead of `e?.message ?? e` | `no-restricted-syntax` |
| `driver-app/app/profile-setup.tsx` | `console.log` now passes the whole error object instead of `err?.message \|\| err` | `no-restricted-syntax` |
| `driver-app/app/become-driver.tsx` | 2 narrow `eslint-disable-next-line` suppressions | `set-state-in-effect` |
| `driver-app/app/documents.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/addresses.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/components/CarMarker.tsx` | `imageFailed` reset rewritten from `useEffect` to render-time adjustment | `set-state-in-effect` |
| `driver-app/app/driver/destination-mode.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/emergency-contacts.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/faq.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/referral.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/tax-documents.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/subscription.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/legal.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/lost-and-found-chat.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/ride-detail.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/vehicle-info.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/index.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/otp.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/components/CancelReasonSheet.tsx` | 1 narrow suppression | `set-state-in-effect` |
| `driver-app/app/driver/settings.tsx` | 2 narrow suppressions | `set-state-in-effect` |
| `driver-app/app/driver/(tabs)/index.tsx` | 3 narrow suppressions (heatmap, countdown, route/ETA reset) | `set-state-in-effect` |
| `driver-app/app/driver/payout.tsx` | 2 narrow suppressions (loadData, GST sync) | `set-state-in-effect` |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | 2 narrow suppressions (live-distance reset, wait-timer reset) | `set-state-in-effect` |
| `driver-app/hooks/useDriverDashboard.ts` | 3 narrow suppressions (refreshLocation, connection-state sync, isOnline hydration) | `set-state-in-effect` |
| `ACTION_ITEMS.md` | Round 3 driver-app summary added to C20 | Documentation |

## 7. Before / after

**`no-restricted-syntax` (logger-only fix)** — `app/driver/(tabs)/profile.tsx`:

```tsx
# Before
.catch((e) => console.warn('[DriverProfile] company-info fetch failed:', e?.message ?? e));
```

```tsx
# After
.catch((e) => console.warn('[DriverProfile] company-info fetch failed:', e));
```

**`set-state-in-effect` (narrow suppression)** — `app/driver/faq.tsx`:

```tsx
# Before
useEffect(() => {
    fetchFaqs();
}, []);
```

```tsx
# After
useEffect(() => {
    // Mount-only fetch; fetchFaqs sets state after its own await, not
    // synchronously at the top of the effect. Empty deps, runs once.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchFaqs();
}, []);
```

**`set-state-in-effect` (category-(b) refactor)** — `components/CarMarker.tsx`:

```tsx
# Before
const [imageFailed, setImageFailed] = useState(false);
useEffect(() => setImageFailed(false), [imageUri]);
```

```tsx
# After
const [imageFailed, setImageFailed] = useState(false);
const [prevImageUri, setPrevImageUri] = useState(imageUri);
if (imageUri !== prevImageUri) {
    setPrevImageUri(imageUri);
    setImageFailed(false);
}
```

## 8. Rollback plan

`git revert` is sufficient for every commit in this PR — pure
client-side lint-suppression comments plus one same-semantics local-state
refactor and two logger-argument changes. No server-side state, no
migration, no feature flag, no live data touched. Each of the 12 commits
in this PR (one logical change per commit, per CLAUDE.md's batch-size
rule) is independently revertable; reverting any subset restores that
subset's original code exactly.

## 9. Verification performed

- [x] Automated tests run: `yarn tsc --noEmit` clean after every commit
  (0 errors); full `yarn jest` at the end — 378/379 passing, the 1
  failure (`__tests__/androidAutoDistribution.test.ts`) is the
  pre-existing, documented, unrelated flake (`eas.json`'s
  `android-auto.android.track` reads `"Auto"` vs the test's expected
  `"internal"`) — confirmed via `git diff --stat origin/main --
  __tests__/androidAutoDistribution.test.ts eas.json` returning empty,
  i.e. zero changes to either file in this branch. Targeted re-runs after
  touching the flagged risk-posture files:
  `__tests__/components/ActiveRidePanel.test.tsx` (11/11),
  `__tests__/components/RideOfferPanel.test.tsx` (17/17 — file itself
  untouched, run as an adjacent dispatch-offer regression check per the
  task's instruction), `hooks/__tests__/useDriverDashboard.chat.test.ts`
  (8/8).
- [x] `npx eslint . --format json` run fresh at the start (30
  `set-state-in-effect` / 2 `no-restricted-syntax`, confirmed against the
  actual branch-start commit, not the stale ACTION_ITEMS.md figures) and
  end (0 / 0) of this round. Full before/after error count: 40 → 8
  (exactly the 32 fixed findings; all remaining 8 errors are pre-existing
  `react-hooks/exhaustive-deps` / `react-hooks/refs` findings, both
  explicitly out of scope this round; warning count unchanged at 49,
  confirming no new findings were introduced anywhere in the sweep).
- [x] Blast-radius grep performed for the one shared-utility touch
  (`getApiErrorMessage`) — confirmed it was **not** used at either
  `no-restricted-syntax` site (both are logger-only) and did not need a
  new call pattern; existing call sites across `driver-app/store/`,
  `driver-app/app/`, and `driver-app/hooks/useDriverDashboard.ts` were
  read first to confirm the `getApiErrorMessage(err, fallback)` signature
  and import path (`@shared/api/client`) before concluding neither site
  needed it.
- [x] Reviewed against CLAUDE.md's dispatch/payments risk posture:
  `driver/(tabs)/index.tsx`, `driver/payout.tsx`,
  `ActiveRidePanel.tsx`, and `useDriverDashboard.ts` each got a dedicated
  commit with the specific behavioral trace documented in section 4 above
  and in the commit message, not a generic "looks fine."
- [ ] Feature-flagged — not applicable; none of these changes are
  user-visible or behavior-changing enough to warrant a flag (lint
  suppressions, one local-state refactor, two logger-argument swaps).

## What was NOT verified

- **No manual device/simulator test.** This environment has no device/
  simulator to exercise the actual driver-app UI (ride-offer countdown,
  active-ride bottom sheet drag/reset, car marker image fallback, GST/
  payout form, WebSocket reconnect behavior when toggling online/offline)
  — reasoned about via code read (every suppressed setState's dependency
  chain traced) and the existing unit/component test suite passing, not
  visually confirmed on a device. Same standing gap noted in rounds 1 and
  2's change-logs and ACTION_ITEMS.md — this repo has no visual
  regression tooling for React Native screens.
- **No production build (`expo export`) run this round.** Round 1's
  change-log already ran and confirmed one for driver-app on a closely
  preceding commit; this round's changes are strictly narrower in kind
  (suppressions, one local refactor, two logger-argument swaps) than
  round 1's broader mechanical sweep, and `tsc --noEmit` + the full jest
  suite were judged sufficient verification for changes of this shape.
  Flagging explicitly rather than implying it was covered.
- **`react-hooks/exhaustive-deps` and the 2 pre-existing `react-hooks/refs`
  findings (the deliberately-deferred write/read pair in
  `driver/(tabs)/index.tsx`) were not touched or re-investigated** — both
  remain explicitly out of scope for this round, same as rounds 1 and 2.
- **The `isOnline` hydration effect's guard chain was traced by reading
  the code, not by writing a new regression test that would fail if the
  guard were ever removed.** The existing test suite (including
  `useDriverDashboard.chat.test.ts`) does not specifically assert
  "hydration runs exactly once" as a behavior; this PR added no new test
  for that invariant. Flagging as a gap for a future round rather than
  claiming coverage that doesn't exist.
- **Not tested against a live backend or real ride/driver data** — the
  existing test suites' mocked fixtures were used as-is; this PR adds no
  new automated tests (a pure lint/behavior-preserving cleanup pass,
  matching prior rounds' same "not applicable" call).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` per
  commit)
- [x] Blast radius is stated, not assumed — every suppression's
  dependency-array feedback path was traced individually (section 4), not
  a blanket "reviewed, safe"
- [x] No silent behavior change to an already-shipped flow — the "User
  Experience Effect" section states no driver-visible change and
  distinguishes it from the small logger-only change, and zero findings
  were left in category (c) that would otherwise need explicit flagging
  as unresolved risk
