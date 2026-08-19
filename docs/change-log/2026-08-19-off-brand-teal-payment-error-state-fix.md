# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | rider-app / driver-app |
| Domain (Sentry tag) | rides (map/dispatch-adjacent UI), payments (payment-confirm) |
| PR / commit link | local worktree commits (not pushed) — see commit SHAs in session report |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` ranked blocker #23 (baseline #17, finding N7) |

## 1. Issue / gap identified

Two unrelated design/UX gaps bundled under one audit blocker:

1. An off-brand ad hoc teal color (`rgba(0,212,170,…)`, plus a separate
   Tailwind-emerald hex palette on the driver subscription screen) was used
   in 5 call sites across 4 files instead of Spinr's real brand success
   token. Spinr is not a teal/green brand
   (`.claude/context/brand-spinr.md`).
2. `rider-app/app/payment-confirm.tsx`'s saved-cards fetch failure rendered
   identically to "you genuinely have no cards on file" — a rider whose
   `/payments/cards` call failed saw the same "Tap to add a card" prompt as
   a rider who truly has none, with no way to know a retry might work.

## 2. Root cause

1. The teal (`rgba(0,212,170,…)`) was hand-picked ad hoc for the driver-app
   service-area-polygon "no surge" tint and copy-pasted into two rider-app
   map screens that render the same polygon (`driver-arriving.tsx`,
   `ride-options.tsx`) and into a countdown-circle background on the same
   driver-app screen. Separately, the subscription screen's "free mode"
   celebration card used a hand-picked Tailwind-emerald palette
   (`#ECFDF5`/`#A7F3D0`/`#065F46`/`#047857`/`#D1FAE5`) instead of the
   theme's `success`/`successBg` tokens — this predates
   `CustomAlert.tsx`'s color tokenization fix (the top color-consistency
   blocker from the May review), which established the
   `colors.success`/`colors.successBg` convention but wasn't retrofitted
   here.
2. `payment-confirm.tsx`'s `loadSavedCards()` catch block only
   `console.warn`'d and left `savedCards` as `[]`; the render logic branches
   solely on `savedCards.length === 0`, so a genuine empty state and a fetch
   failure were the same code path with no distinguishing state variable.

## 3. Fix / remediation

1. Replaced every instance of the ad hoc teal/emerald literals with
   `colors.success` / `colors.successBg` from `useTheme()` — reusing the
   exact tokenization convention already established elsewhere in the
   codebase (e.g. `DriverIdlePanel.tsx`'s `statusPillOnline`:
   `backgroundColor: colors.successBg`, `borderColor: colors.success`, and
   `TripCompletedPanel.tsx`'s `${colors.success}CC` alpha-suffix pattern for
   semi-transparent map/gradient use). No new color was invented.
2. Added a dedicated `cardsLoadError` boolean state to `payment-confirm.tsx`,
   set `true` in the fetch's `.catch()` and cleared on success. The render
   now branches three ways: `cardsLoadError` → distinct error row
   ("Couldn't load your cards" / "Tap to retry", danger-colored, retries via
   the same `loadSavedCards` handler) is checked first; only when there is
   no error does the pre-existing `savedCards.length === 0` empty-state row
   render. The two states are now mutually exclusive by construction
   (`!cardsLoadError && savedCards.length === 0`). Copy/structure follows
   the existing cloud-offline/"Try Again" retry convention already used on
   `rider-app/app/referral.tsx`.

## 4. Risk & impact on existing functionality

- **Blast radius for the color change**: isolated to visual styling only —
  no logic, layout, or component structure changed at any site.
  - `driver-app/app/driver/(tabs)/index.tsx`: service-area boundary polygon
    default fill/stroke (below the first surge tier) and the ride-offer
    countdown-circle background. Grepped the whole repo for the literal
    `rgba(0,212,170` triplet and the subscription screen's specific
    emerald hex values — no other consumer of either literal remains
    outside the sites fixed here.
  - `driver-app/app/driver/subscription.tsx`: only the named
    `freeCard`/`freeEmoji`/`freeTitle`/`freeMessage`/`freeBadge`/`freeBadgeText`
    style block (lines ~590-602 in the audit's citation). Not shared by any
    other screen — `createStyles()` is local to this file.
  - `rider-app/app/driver-arriving.tsx`, `rider-app/app/ride-options.tsx`:
    only the `serviceAreaPolygons.map(...)` `<Polygon>` stroke/fill props —
    same literal, same semantic ("calm/no-surge" boundary tint), no other
    prop touched.
  - **Explicitly out of scope, left untouched**: a separate, much more
    widespread Tailwind-emerald palette (same hex family) is used
    elsewhere in the app for unrelated badges/cards — `driver-app/app/documents.tsx`,
    a different badge later in the same `(tabs)/index.tsx` file
    (`tripInfoBadge`, line ~1266), `rider-app/app/ride-options.tsx` promo
    pills, `rider-app/app/ride-details.tsx` status badges,
    `rider-app/app/settings.tsx`, `rider-app/app/saved-places.tsx`,
    `rider-app/app/promotions.tsx`, `rider-app/components/FreeCancelTimer.tsx`,
    and `admin-dashboard/src/app/track/[rideId]/page.tsx`. These were not
    named in the audit finding and are a separate, larger design-consistency
    question — fixing them was explicitly out of scope for this task
    ("scoped color-correction fix") and risks a much larger diff across
    unrelated screens. Flagging as a candidate follow-up, not doing it here.
  - `colors.success`/`colors.successBg` are defined for both light and dark
    palettes in `shared/theme/index.ts` and are already read via
    `useTheme()` in all 4 edited files (pre-existing import, no new
    dependency added) — no risk of an undefined-token crash.
- **Blast radius for the payment-confirm change**: isolated to the Payment
  Method section of one screen. Grepped `rider-app` for other importers of
  `payment-confirm.tsx` — it is a route file (`expo-router`), not an
  importable component, so no other screen embeds this logic.
  `loadSavedCards` is only called from this file's `useFocusEffect` and the
  new retry button's `onPress` — no other caller. `savedCards` state is
  local to this component; no store/global state touched. The rider's
  ability to still pick Wallet/corporate payment sources while cards fail
  to load is unchanged.

## 5. User-experience effect

- **Rider-facing**: the map service-area polygon shown to a rider (on
  `driver-arriving` and `ride-options`) tints green instead of teal when no
  surge is active — a subtle color change only, same shape/behavior.
  Riders whose card list fails to load now see a distinct red "Couldn't
  load your cards — Tap to retry" row instead of being silently steered to
  "add a new card" as if they had none. This is a visible mid-session
  change for anyone hitting the failure path (network blip, backend 5xx
  from `/payments/cards`) — but it improves clarity rather than
  introducing new behavior; the rider previously saw a functioning
  "Add a credit card" action that would have looked correct but masked a
  real fetch failure.
- **Driver-facing**: the service-area polygon on the driver's live map tints
  green instead of teal (same "no surge" semantic); the ride-offer countdown
  circle's subtle background tint changes from teal to a green tied to
  `colors.primary`'s border context; the subscription screen's "You're on
  Free Mode" celebration card now uses the brand green instead of an
  unrelated Tailwind-emerald palette — visually very close (both are
  green), not a jarring change.
- Not gated behind a feature flag: this is a pure, non-functional visual
  correction (color literal → theme token) plus an additive error-state
  branch that only activates on an existing failure path — no previously
  working flow is altered, no new required user action, no copy affecting
  a financial decision.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Service-area polygon default fill/stroke and countdown-circle background changed from `rgba(0,212,170,…)` literals to `${colors.success}…` alpha-suffixed tokens | Off-brand teal → brand success token (audit #23 site #1) |
| `driver-app/app/driver/subscription.tsx` | `freeCard`/`freeEmoji`/`freeTitle`/`freeMessage`/`freeBadge`/`freeBadgeText` style block changed from ad hoc emerald hex to `colors.success`/`colors.successBg` | Off-brand teal/emerald → brand success token (audit #23 / N7, 3rd instance) |
| `rider-app/app/driver-arriving.tsx` | Service-area polygon stroke/fill changed from `rgba(0,212,170,…)` to `${colors.success}…` | Same off-brand teal literal reused here — found via grep, not in the audit's named list |
| `rider-app/app/ride-options.tsx` | Service-area polygon stroke/fill changed from `rgba(0,212,170,…)` to `${colors.success}…` | Same off-brand teal literal reused here — found via grep, not in the audit's named list |
| `rider-app/app/payment-confirm.tsx` | Added `cardsLoadError` state; catch sets it, success clears it; new distinct error row rendered before the empty-state row, gated so the two never render together | Payment fetch-failure indistinguishable from empty state (audit #23) |
| `driver-app/__tests__/off-brand-teal-color-fix.test.tsx` | New source-based contract test | Pins the color-token fix, scoped to only the sites touched |
| `rider-app/__tests__/off-brand-teal-color-fix.test.tsx` | New source-based contract test | Pins the color-token fix on the two rider-app map screens |
| `rider-app/__tests__/payment-confirm-error-state.test.tsx` | New source-based contract test | Pins the error-state vs. empty-state distinction |

## 7. Before / after

**Color (driver-app `(tabs)/index.tsx`, service-area polygon default tint):**

```tsx
// Before
let surgeFill = 'rgba(0,212,170,0.07)';
let surgeStroke = 'rgba(0,212,170,0.65)';

// After
let surgeFill = `${colors.success}12`;
let surgeStroke = `${colors.success}A6`;
```

**Color (driver-app `subscription.tsx`, free-mode card):**

```tsx
// Before
freeCard: {
  backgroundColor: '#ECFDF5', ..., borderColor: '#A7F3D0',
},
freeTitle: { ..., color: '#065F46', ... },

// After
freeCard: {
  backgroundColor: colors.successBg, ..., borderColor: `${colors.success}55`,
},
freeTitle: { ..., color: colors.success, ... },
```

**Payment-confirm error state:**

```tsx
// Before
const loadSavedCards = useCallback(() => {
  api.get('/payments/cards').then((res) => {
    setSavedCards(cards);
    setSelectedCardId((prev) => selectDefaultCardId(prev, cards));
  }).catch((e) => {
    console.warn('[PaymentConfirm] Failed to load saved cards:', e);
  });
}, []);
// ...
{savedCards.length === 0 && <AddCardRow />}

// After
const [cardsLoadError, setCardsLoadError] = useState(false);
const loadSavedCards = useCallback(() => {
  api.get('/payments/cards').then((res) => {
    setSavedCards(cards);
    setSelectedCardId((prev) => selectDefaultCardId(prev, cards));
    setCardsLoadError(false);
  }).catch((e) => {
    console.warn('[PaymentConfirm] Failed to load saved cards:', e);
    setCardsLoadError(true);
  });
}, []);
// ...
{cardsLoadError && <RetryRow onPress={loadSavedCards} />}
{!cardsLoadError && savedCards.length === 0 && <AddCardRow />}
```

## 8. Rollback plan

Pure frontend, additive/color-only change with no server, migration, or
feature-flag surface:
- `git revert` the commit(s) on this branch is a complete, safe rollback —
  nothing here touches live data, Stripe charges, wallet deltas, or ride
  state. A revert only reverts local UI code; no second-order cleanup is
  needed.
- No `app_settings` flag was added or needed — this is not the kind of
  user-visible-and-non-trivial change the flag-rollout rule targets (a
  color swap and a strictly-additive error branch on an existing failure
  path, not a new validation rule or new notification copy).

## 9. Verification performed

- [x] Automated tests run — new source-based contract tests (Jest,
      `npx jest` in both `rider-app/` and `driver-app/` after `yarn install`
      in each — full dependency install was required, no `node_modules`
      existed in this worktree beforehand):
      - `rider-app`: `__tests__/payment-confirm-error-state.test.tsx` (5
        tests) and `__tests__/off-brand-teal-color-fix.test.tsx` (2 tests)
        — all 7 pass.
      - `driver-app`: `__tests__/off-brand-teal-color-fix.test.tsx` (2
        tests) — both pass.
      - Full `rider-app` suite: 536/537 passing (1 pre-existing failure,
        `__tests__/verifyEmailScreen.test.tsx`, a 5000ms timeout unrelated
        to any file touched here — confirmed via `git status --short`
        that this test's source file was not modified).
      - Full `driver-app` suite: 556/557 passing (1 pre-existing failure,
        `__tests__/components/ActivityView.test.tsx`, same category of
        pre-existing timeout flake, also unrelated to files touched here).
- [x] `npx tsc --noEmit -p .` run in both `rider-app/` and `driver-app/` —
      zero errors reported against any of the 5 modified `.tsx` files (this
      is **not** a full production build; see "What was NOT verified"
      below).
- [x] Blast-radius grep performed — see section 4 above; confirmed via
      `grep -rn` across `driver-app`, `rider-app`, `admin-dashboard`,
      `shared` for the literal `rgba(0,212,170` and the specific emerald
      hex values, scoped test assertions to only the sites actually fixed
      so the tests can't silently pass by weakening the check.
- [x] Reviewed against `.claude/context/brand-spinr.md` — confirmed
      `colors.success`/`colors.successBg` are the correct, only defined
      brand tokens for this semantic, and that the fix reuses
      `CustomAlert.tsx`'s already-established tokenization convention
      rather than inventing a new one.
- [ ] Manual repro / staging check — not performed (no staging environment
      available in this session).
- Feature-flagged: N/A — see rollback plan section for why this is not the
  class of change the flagging rule targets.

## What was NOT verified

- **No real production build was run** (`expo export` / EAS build
  equivalent) for either `rider-app` or `driver-app` — only `tsc --noEmit`
  and Jest. Per this repo's own convention, a passing `tsc --noEmit` alone
  is not equivalent to a production build; this is a stated gap.
- **No visual-regression tooling exists in this repo** (standing gap, not
  new to this change) — the color swaps were verified by reading the
  resulting token values against `shared/theme/index.ts`'s light/dark
  palettes, not by rendering and screenshotting the map polygons, countdown
  circle, or free-mode card in either theme. The chosen alpha suffixes
  (`12`, `A6`, `0F`, `55`) were computed arithmetically to approximate the
  original opacities (0.07→`12`≈0.07, 0.65→`A6`≈0.65, 0.06→`0F`≈0.06,
  border tint→`55`≈0.33) but were not visually confirmed on-device or in a
  simulator.
- **Dark mode was not manually inspected** — `colors.success`/`successBg`
  resolve to different (still-green, not teal) values in
  `darkColors` (`#30D158`/`#0B3D2E`) vs. `lightColors`
  (`#34C759`/`#ECFDF5`); this was reasoned about from the token
  definitions, not rendered.
- **No real device/simulator run** of either app — changes were verified
  via source, type-check, and Jest only, consistent with this repo's
  existing test convention for these large screen files (see e.g.
  `rider-app/__tests__/ride-options-payment-sheet.test.tsx`, which uses
  the same source-based contract-test pattern rather than full RTL
  rendering for this class of large, heavily-dependency-coupled screen).
- **The payment-confirm retry button's accessibility/VoiceOver behavior**
  was reasoned about (matches the existing `referral.tsx` pattern's
  `accessibilityRole`/`accessibilityLabel` usage) but not tested with a
  real screen reader.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no live-data
      side effects)
- [x] Blast radius is stated, not assumed (section 4, with grep evidence)
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in (section 5) — the payment-confirm change is
      additive on an existing failure path; the color changes are visual
      only
