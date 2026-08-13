# Change Impact & Risk Log — C20 mobile lint debt, rider-app, round 3 (tier 3)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | `claude/c20-lint-tier3-rider-app` |
| Related issue or gap ID | ACTION_ITEMS.md C20 "Mobile lint debt under the SDK 57 ruleset" (round 3) |

This is round 3 of C20, targeting the two rider-app categories round 2
(PR #3838, `docs/change-log/2026-08-12-c20-lint-tier2-rider-app.md`)
explicitly deferred: `no-restricted-syntax` (14 findings — the project's own
raw-`error.message`-surfacing rule) and `react-hooks/set-state-in-effect` (40
findings). driver-app was not touched (a sibling session/agent owns that
surface in parallel, in a separate worktree).

## 1. Issue / gap identified

Fresh `npx eslint . --format json` at the start of this session (not trusted
from ACTION_ITEMS.md, per this project's own standing instruction) confirmed:
`no-restricted-syntax` 14, `react-hooks/set-state-in-effect` 40 — matching
the task brief exactly, so no drift to reconcile this round.

## 2. Root cause

- **`no-restricted-syntax`**: `rider-app/eslint.config.js` bans
  `MemberExpression[object.name=/^(e|err|error)$/][property.name='message']`
  in `app/**` and `store/**` — i.e. reading `.message` off a variable
  literally named `e`/`err`/`error`. Of the 14 matches, only 8 were actually
  raw backend-error text reaching a user-visible surface (`showToast`); 6
  were false-positive-for-UX-purposes matches on the same AST shape used in
  a `console.warn(...)` logging call or a `.message.includes(...)`
  control-flow check — the rule can't distinguish those from a real
  user-facing case.
- **`react-hooks/set-state-in-effect`**: `eslint-config-expo`'s SDK 57 bump
  pulled in `eslint-plugin-react-hooks` v7's React Compiler rule set, which
  flags any `setState` call reachable synchronously from an effect body
  (including through a same-file function it can statically trace into),
  regardless of whether that effect's own dependency array would actually
  let the write retrigger it. All 40 were traced individually against their
  effect's dep array; none of the 40 sets a piece of state that is itself a
  dependency of the same effect, so none can cascade into a render loop —
  except one (`work-profile.tsx`) where the *concern* isn't a loop but a
  duplicate-fetch race between two effects that both run on mount.

## 3. Fix / remediation

**`no-restricted-syntax` (14→0), two commits:**
1. 6 non-UI findings (4 `console.warn` logging calls, 2 control-flow-only
   `.message.includes()` checks) — not routed through `getApiErrorMessage`
   at all, per this round's task instructions not to force-fit a UX-helper
   fix onto a non-UX site. Logging calls: pass the whole error object
   instead of `.message` (matches the rule's own guidance text and an
   already-compliant call site in the same file). Control-flow checks:
   narrow `eslint-disable-next-line` with a comment explaining the actual
   user-visible text a few lines below already uses `getApiErrorMessage`.
2. 8 genuine raw-error-to-UI findings — replaced with
   `getApiErrorMessage(err, fallback)`. See section 5 for the exact
   before/after user-visible text per site.

**`react-hooks/set-state-in-effect` (40→1), ten commits (≤3 files each):**
Every finding read in full context, tracing the effect's dependency array
against the state it sets. 39 confirmed benign (a narrow
`eslint-disable-next-line` with a per-site justification comment — not a
behavior change); 1 left unfixed with a `TODO(C20)` comment (not
suppressed — still shows up in `yarn lint`) because it's a real, if minor,
suspected bug rather than a lint false-positive: see section 4.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to rider-app screens/components, one file at a
  time. No shared hook/component was newly introduced this round (unlike
  round 2's `useAnimatedValue.ts` extraction). `hooks/useRiderSocket.ts` was
  touched (comment-only, no logic change) — it is shared by 4 consumers
  (`ai-assistant.tsx`, `app/_layout.tsx`, `ride-status.tsx`,
  `store/rideStore.ts`); grepped all 4, none read anything from the
  connect/disconnect effect beyond the hook's existing public return value,
  which is unchanged.
- **No ride-state-machine or money-path changes.** All touched effects are
  rider-side UI/display state: countdown timers, form resets, corporate
  toggle syncing, saved-card selection, route-coordinate display,
  notification-preference toggles. The one dispatch-adjacent touch
  (`ride-status.tsx`'s ~15s offer-acceptance countdown display, and
  `useRiderSocket.ts`'s live-ride WebSocket lifecycle) got extra scrutiny
  precisely because of that adjacency — reviewed and confirmed neither
  reads/writes ride status, calls a ride-mutating endpoint, or has any path
  back into its own effect's dependencies. Confirmed no interaction with
  any of the 16 backend background loops (this is a pure frontend PR).
- **Found, not fixed — `work-profile.tsx:76-85`**: a second `useEffect`
  (triggered on `activeCompanyId`) duplicates the mount effect's own
  `loadAll()` call — both fetch `fetchBalance()` and the same
  `/rider/work-profile/:id/rides` endpoint. On first mount, when
  `activeCompanyId` is already set, both effects fire concurrently and
  race on which response's `setRides`/`setRidesLoading` call lands last.
  This is a **pre-existing** pattern (not introduced by this PR), not a
  render loop, and not confirmed as user-visible (the two responses should
  usually agree, since they hit the identical endpoint with the identical
  params) — but it's redundant network traffic and a plausible source of a
  rare flicker/stale-overwrite if the two requests resolve out of order.
  Left unfixed: removing either path is a real behavior decision this
  session isn't positioned to make blind (which effect was the intended
  "load once" vs. "reload on switch" split, if any). Flagged with a
  `TODO(C20)` comment at the site and in ACTION_ITEMS.md's C20 section as
  needing a human decision.
- **Interaction with other consumers of touched files**: every file this
  round touched is a leaf screen or component with no other importer,
  except `useRiderSocket.ts` (addressed above) and the `chat-driver.tsx` /
  `manage-cards.tsx` / `verify-email.tsx` error-text sites, which are also
  each a single screen with no other importer. `getApiErrorMessage` itself
  is unchanged — this PR only adds call sites, and grepping the codebase
  confirms it's already used at ~50 other call sites across `app/`,
  `store/`, and `lib/`, all following the same `getApiErrorMessage(err,
  fallback)` signature this round's new call sites also use.

## 5. User-experience effect

**Genuine UX change (raw-error-message routing) — 3 screens, 8 sites:**

| Site | Before (raw text shown) | After (fallback shown when no usable backend detail) |
|---|---|---|
| `chat-driver.tsx` send-message failure | `e.response.data.detail` or raw `e.message` (e.g. "Network Error") | "Could not send message. Check your connection." (unchanged fallback string — behavior is now routed through the shared helper instead of hand-rolled, so it also benefits from `getApiErrorMessage`'s RateLimitError/429 handling that the old code didn't have) |
| `manage-cards.tsx` add-card failure (Stripe `createPaymentMethod` error) | raw Stripe `error.message` (Stripe's own messages are already user-appropriate, e.g. "Your card number is incomplete" — `getApiErrorMessage`'s fallback ladder preserves this exact behavior, see section 7) | "Could not process card. Please try again." (only on a Stripe error with no `.message`, which doesn't happen in practice) |
| `verify-email.tsx` request/confirm failures (4 sites, all in one shared helper) | when no `messageKey` matched: raw `err.message` verbatim, **including** technical strings like "Request failed with status code 500" or "Network Error" if that happened to be the error's message | same non-keyed fallback text as before ("We could not send a verification code. Please try again." / "That code did not work. Please try again."), but now with `getApiErrorMessage`'s noise-filtering applied first — a genuine improvement, not just a lint fix, since the old code could show a raw HTTP-error string to the rider |

This is visible **mid-flow** to a rider who hits an error while chatting
with their driver, adding a payment card, or verifying their email — not
mid-ride in the dispatch/payment sense, but mid-task within these screens.

**No UX change (6 non-UI findings)**: 4 logging-only fixes (console output
only, never rendered) and 2 control-flow-only fixes (routing logic, the
actual displayed text was already unaffected).

**No UX change (set-state-in-effect, 39 of 40)**: every suppressed finding
is a pure lint-suppression with a justification comment; the runtime
behavior — what gets rendered, when — is identical before and after.

**1 unresolved (`work-profile.tsx`)**: no change made; if the duplicate-fetch
race is ever user-visible, it would show as a brief flicker in the
"recent work rides" list on the Work Profile screen on first load with an
active company already set — not touched, not fixed, flagged for follow-up.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/_layout.tsx` | 3× `console.warn(..., e?.message ?? e)` → `console.warn(..., e)` | `no-restricted-syntax`, logging-only |
| `rider-app/app/payment-confirm.tsx` | 1× console.warn fix; 1× narrow suppression on a 409-detection control-flow check; corporate-toggle sync effect suppressed | `no-restricted-syntax` (2) + `set-state-in-effect` (1) |
| `rider-app/app/ride-options.tsx` | 1× console.warn fix (saved cards); 1× console.warn fix (Directions API); 1× narrow suppression on a 409-detection check; 4× `set-state-in-effect` suppressions (fetch-on-route-change, out-of-bounds index reset, corporate-toggle sync, route-coordinates sync) | `no-restricted-syntax` (3) + `set-state-in-effect` (4) |
| `rider-app/app/chat-driver.tsx` | Hand-rolled error-text ladder → `getApiErrorMessage` | `no-restricted-syntax`, real UX fix |
| `rider-app/app/manage-cards.tsx` | Stripe error `.message` → `getApiErrorMessage`; 2× `set-state-in-effect` suppressions (mount fetch, cards-empty reset) | `no-restricted-syntax` (1, UX fix) + `set-state-in-effect` (2) |
| `rider-app/app/verify-email.tsx` | `resolveErrorCopy()` now sources its fallback text from `getApiErrorMessage`; 1× `set-state-in-effect` suppression (one-shot request-code-on-entry) | `no-restricted-syntax` (4, UX fix) + `set-state-in-effect` (1) |
| `rider-app/app/become-driver.tsx`, `emergency-contacts.tsx`, `legal.tsx`, `lost-and-found-chat.tsx`, `loyalty.tsx`, `notifications.tsx`, `otp.tsx`, `promotions.tsx`, `referral.tsx`, `saved-places.tsx`, `scheduled-rides.tsx`, `ride-details.tsx`, `ride-tracking-webview.tsx`, `driver-arriving.tsx`, `ride-completed.tsx`, `ride-in-progress.tsx`, `ride-status.tsx`, `privacy-settings.tsx`, `search-destination.tsx`, `settings.tsx` | Narrow `set-state-in-effect` suppression(s) with justification comment | `set-state-in-effect`, benign (a) |
| `rider-app/app/work-profile.tsx` | Mount-load effect suppressed (a); second effect left **unfixed** with `TODO(C20)` comment | `set-state-in-effect`, 1 (a) + 1 (c) left for review |
| `rider-app/components/BookingProposalCard.tsx`, `CancelReasonSheet.tsx`, `FreeCancelTimer.tsx` | Narrow `set-state-in-effect` suppression with justification comment | `set-state-in-effect`, benign (a) |
| `rider-app/hooks/useRiderSocket.ts` | Narrow `set-state-in-effect` suppression on the connect/disconnect lifecycle effect | `set-state-in-effect`, benign (a), extra scrutiny given 4 consumers |
| `rider-app/__tests__/verifyEmailScreen.test.tsx` | `jest.mock('@shared/api/client', ...)` now spreads `jest.requireActual(...)` so the real `getApiErrorMessage` is available to the mock (matches the existing pattern in `walletStore-error-messages.test.ts`) | Test broke once `verify-email.tsx` started calling the real `getApiErrorMessage`; fixed by exposing it in the mock rather than hand-rolling a fake — every assertion in this file relies on `messageKey`→`tKey` lookups that don't depend on `getApiErrorMessage`'s specific return value, so this doesn't change what the tests verify |
| `ACTION_ITEMS.md` | C20 section: added "Round 3 (rider-app)" bullet | Required by CLAUDE.md |
| `docs/change-log/2026-08-12-c20-lint-tier3-rider-app.md` | New (this file) | Required Change Impact Log |

## 7. Before / after

```tsx
// Before (app/_layout.tsx, ×3) — flagged no-restricted-syntax
useRideStore.getState().fetchRide(dispatchedRideId).catch((e) =>
  console.warn('[Layout] fetchRide on scheduled dispatch failed:', e?.message ?? e),
);
```
```tsx
// After — passes the whole error object, matching the rule's own guidance
// text and an already-compliant console.warn elsewhere in the same file
useRideStore.getState().fetchRide(dispatchedRideId).catch((e) =>
  console.warn('[Layout] fetchRide on scheduled dispatch failed:', e),
);
```

```tsx
// Before (app/chat-driver.tsx) — hand-rolled, no noise filtering
const detail = e?.response?.data?.detail || e?.message || 'Could not send message. Check your connection.';
showToast('Send Failed', detail, 'danger');
```
```tsx
// After
showToast('Send Failed', getApiErrorMessage(e, 'Could not send message. Check your connection.'), 'danger');
```

```tsx
// Before (app/verify-email.tsx) — could show a raw technical string
function resolveErrorCopy(err: unknown, fallback: string): string {
  const e = err as StructuredApiError | null | undefined;
  if (e && typeof e === 'object' && e.messageKey) {
    return tKey(e.messageKey, e.message || fallback);
  }
  if (e && typeof e === 'object' && typeof e.message === 'string' && e.message) {
    return e.message;
  }
  return fallback;
}
```
```tsx
// After — noise-filtered via getApiErrorMessage in both branches
function resolveErrorCopy(err: unknown, fallback: string): string {
  const e = err as StructuredApiError | null | undefined;
  if (e && typeof e === 'object' && e.messageKey) {
    return tKey(e.messageKey, getApiErrorMessage(err, fallback));
  }
  return getApiErrorMessage(err, fallback);
}
```

```tsx
// Before (app/payment-confirm.tsx, app/ride-options.tsx) — flagged, but not user-facing
const is409 = error?.response?.status === 409 || error?.message?.includes('already active');
```
```tsx
// After — narrow suppression, comment documents why
// eslint-disable-next-line no-restricted-syntax
const is409 = error?.response?.status === 409 || error?.message?.includes('already active');
```

```tsx
// Before (~35 sites across 26 files) — flagged react-hooks/set-state-in-effect
useEffect(() => {
  loadData();
}, []);
```
```tsx
// After — narrow suppression, comment documents why it can't loop
useEffect(() => {
  // Mount-only load; deps are empty so the state loadData sets can't
  // retrigger this effect.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  loadData();
}, []);
```

## 8. Rollback plan

`git-revert-safe` — every commit on this branch is pure client-side code
(no migration, no `app_settings` row, no Stripe/webhook/wallet state, no
ride state machine transition). Each of the 12 commits is scoped to a
distinct set of ≤3 files and can be reverted independently without
affecting the others (no cross-commit dependency — later commits never
build on state introduced by earlier ones in this PR).

## 9. Verification performed

- [x] `npx eslint . --format json` run fresh at the start of the session to
  confirm the task's stated baseline before any change: `no-restricted-syntax`
  14, `react-hooks/set-state-in-effect` 40 — matched exactly.
- [x] After all commits: `no-restricted-syntax` 0, `react-hooks/set-state-in-effect`
  1 (the deliberately-unfixed `work-profile.tsx:76-85` finding). No new
  `unused eslint-disable` warnings introduced (checked programmatically
  over the full JSON output).
- [x] `npx tsc --noEmit` — clean before this round's changes (inherited from
  round 2) and clean after every commit and again at the end of the branch.
- [x] Full rider-app `npx jest --ci` — 56/56 suites, 468/468 tests pass at
  the end of the branch. One pre-existing test
  (`__tests__/verifyEmailScreen.test.tsx`) needed its `@shared/api/client`
  jest mock updated to spread `jest.requireActual(...)` (matching the
  pattern already used in `walletStore-error-messages.test.ts`) once
  `verify-email.tsx` started calling the real `getApiErrorMessage` —
  confirmed this doesn't change what any of that file's 10 assertions
  verify (all go through `messageKey`→`tKey` lookups unaffected by
  `getApiErrorMessage`'s specific return value).
- [x] The task brief's referenced pre-existing flake,
  `__tests__/androidAutoDistribution.test.ts` (an `eas.json` `android-auto`
  track mismatch), **does not exist in rider-app** — searched the full
  tree, no match. This suggests that flake is driver-app-specific (or the
  brief carried it over from a shared instruction template); rider-app's
  full jest run is 468/468 green with no failures of any kind, so there is
  nothing to confirm as "still the only failure."
- [x] Blast-radius grep performed for `getApiErrorMessage` (confirmed ~50
  existing call sites, all following the same signature this round's new
  sites also use) and for `useRiderSocket.ts`'s 4 consumers (confirmed none
  reads anything beyond the hook's unchanged public return value).
- [ ] Feature-flagged — not applicable. The 8 error-text changes are
  fallback-string swaps on already-error-path screens (not a new feature
  or flow), and the `set-state-in-effect` suppressions have zero runtime
  behavior change.

## 10. What was NOT verified

- **No real-device/simulator manual test** — same standing gap as rounds 1
  and 2. Not visually confirmed: the 8 error-toast text changes actually
  rendering correctly on a device, the countdown/timer UIs
  (`ride-status.tsx`'s offer countdown, `FreeCancelTimer.tsx`,
  `otp.tsx`/`verify-email.tsx`'s resend countdowns), or the map/route
  redraw behavior in `ride-options.tsx`. All reasoned about via code trace
  + the passing test suite. No visual regression tooling exists in this
  repo for React Native screens (standing gap, tracked in ACTION_ITEMS.md).
- **`npx expo export --platform web` was not re-run this round** — same gap
  round 2 flagged; this round's changes are lower-risk still (mostly
  comment-only suppressions plus string-level error-text swaps), but this
  is real verification depth not exercised, not a deliberate equivalence
  judgment.
- **Not tested against a live backend** — the `getApiErrorMessage` call
  sites were exercised only via the existing mocked-Axios test fixtures
  (and the `verifyEmailScreen.test.tsx` fix above), not against a real
  backend error response shape in staging.
- **`work-profile.tsx`'s duplicate-fetch race was not further
  investigated** beyond confirming it isn't a render loop — did not trace
  whether the two concurrent `/rider/work-profile/:id/rides` requests
  could ever return genuinely different data (e.g. a rider switching
  companies mid-request), which would make the race more than cosmetic.
  Left for the human review this finding is flagged for.
- **The `no-restricted-syntax` fixes were not diffed against Sentry/real
  production error logs** to confirm the 8 fallback strings chosen are
  actually reached rarely (i.e. that most real errors do carry a usable
  backend detail and skip the fallback) — the fallback text choices were
  reasoned from house style (other `getApiErrorMessage` call sites in the
  same files/screens) rather than from production error-frequency data.
