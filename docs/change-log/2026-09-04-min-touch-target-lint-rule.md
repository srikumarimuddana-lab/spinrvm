# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | rider-app / driver-app |
| Domain (Sentry tag) | n/a (lint-only, dev-tooling change; no runtime code path touched) |
| PR / commit link | See PR description |
| Related issue or gap ID | Follow-up to PR #4951 (`/design Spinr Apps` audit finding), which shipped the SPACING/FONT half and explicitly deferred the MIN_TOUCH (44pt touch-target) half — see its `docs/change-log/2026-09-04-spacing-font-touch-target-lint-rule.md`, "What was NOT verified" |

## 1. Issue / gap identified

PR #4951 added a warn-level ESLint rule enforcing `SPACING`/`FONT` token usage from
`shared/utils/responsive.ts`, but explicitly left out `MIN_TOUCH` (the 44pt Apple HIG
touch-target minimum) — a plain `no-restricted-syntax` selector can't compute an *effective*
touch-target size, which depends on `width`/`height`/`padding` combined with `hitSlop`, not a
single AST literal.

## 2. Root cause

No enforcement mechanism exists for touch-target sizing at all; `MIN_TOUCH = 44` is exported from
`shared/utils/responsive.ts` but nothing checks it against real `TouchableOpacity`/`Pressable`
usage, so undersized tap targets can ship unnoticed.

## 3. Fix / remediation

Added a real custom ESLint rule (`create(context)` with its own resolution logic, not a
`no-restricted-syntax` selector) — `spinr/min-touch-target` — wired into both
`rider-app/eslint.config.js` and `driver-app/eslint.config.js` via a local inline plugin
(`plugins: { spinr: { rules: { 'min-touch-target': ... } } }`), no new npm package needed since
ESLint flat config accepts a plugin object literal directly.

The rule (`<app>/eslint-rules/min-touch-target.js`, duplicated identically in both apps — each
app's ESLint config only resolves local relative requires, and the apps are otherwise
independently versioned, so this avoids adding a third app as a shared dependency for a ~250-line
self-contained file) targets `TouchableOpacity`, `TouchableHighlight`, `TouchableWithoutFeedback`,
and `Pressable` JSX elements (not `Button` — React Native's built-in `Button` is platform-rendered
and has no `style` prop that changes its touch-target size, so there's nothing to check). For each
matching element it:

1. Resolves the `style` prop to literal `width`/`height`/`minWidth`/`minHeight` numbers — handles
   an inline object literal, a `styles.foo` reference into a same-file `StyleSheet.create({...})`
   (collected via a full-file pre-pass, since styles are conventionally declared *below* the
   component that uses them — a single top-down listener would see the JSX before the style
   object exists), or an array of those two shapes (later entries override earlier ones per key,
   matching React Native's own array-style merge order).
2. Resolves the `hitSlop` prop (a bare number, meaning all four sides, or an object with literal
   `top`/`bottom`/`left`/`right`) and adds it to the resolved width/height per axis.
3. Flags the element only if **both** width and height were resolved to literal numbers (i.e. the
   element isn't relying on unknown content/padding-driven sizing) **and** the effective
   (style + hitSlop) size is still under 44pt on either axis.

Everything else — no `style` prop at all, a `style` that's dynamic/conditional/spread, an array
style entry ESLint can't statically read, or a `hitSlop` that isn't a literal number/object — is
silently skipped rather than flagged. This deliberately accepts false negatives (e.g. the common
"no explicit size, just `padding: 8`" pattern is invisible to this rule) in exchange for never
producing a false positive on a case it can't actually reason about, per the task's explicit
guidance and the same posture PR #4951 already established for `warn` severity.

Severity is `warn`, matching PR #4951's rule and the pre-existing hex-color design-token rule in
the same files — same "pre-existing violations, tighten later" posture, and neither
`rider-app-test` nor `driver-app-test` in `.github/workflows/ci.yml` currently runs `eslint` at
all (only `tsc --noEmit` + `jest`), so this is advisory in this repo's CI regardless of severity,
same as the SPACING/FONT rule before it.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to lint tooling.** Only `rider-app/eslint.config.js`,
  `driver-app/eslint.config.js`, and the two new `eslint-rules/min-touch-target.js` support files
  are added/modified; no application code changed. A custom ESLint rule is static analysis only —
  it does not run at build or runtime, so it cannot change app behavior, only developer-facing
  lint output.
- **Other consumers of these two `eslint.config.js` files:** the existing `no-restricted-syntax`
  blocks (`error.message` at `error` severity, and the hex-color + SPACING/FONT rule at `warn`)
  live in their own separate config object with their own `files` glob key, and this change adds
  a **new** config object (new `plugins`/`rules` keys, not `no-restricted-syntax`) rather than
  editing either existing block — so the array-merge footgun PR #4951 hit and documented
  (`no-restricted-syntax`'s array not merging across two config objects matching the same files)
  does not apply here: `plugins`/`rules` for a *different* rule name in a *new* block do not
  collide with or replace the earlier blocks' `rules.no-restricted-syntax`. Verified by re-running
  `eslint` after the change and confirming the pre-existing hex-color/SPACING/FONT/`error.message`
  warning and error counts are unchanged (see §9).
- **CI impact:** none — `warn` severity does not fail `eslint`'s exit code, and confirmed (same as
  PR #4951's finding) that no CI workflow in this repo runs `eslint` for either app at all today.
- **No other importers/consumers:** `eslint.config.js` in each app is not imported by anything
  else in the repo; the new `eslint-rules/min-touch-target.js` files are only required from their
  own app's `eslint.config.js`. No cross-file or cross-app blast radius.

## 5. User-experience effect

None. This changes only what developers see in their editor/lint output; it has no rider, driver,
corporate-admin, or internal-admin facing effect, visible mid-session or otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/eslint-rules/min-touch-target.js` | New file: custom ESLint rule resolving `style`+`hitSlop` to an effective touch-target size | Purpose-built MIN_TOUCH check (44pt) the SPACING/FONT PR explicitly deferred |
| `driver-app/eslint-rules/min-touch-target.js` | Same file, mirrored | Same reason |
| `rider-app/eslint.config.js` | Added a `require` of the new rule and a new config block registering it as `spinr/min-touch-target` (warn) over `app/**/*.{ts,tsx}`, `store/**/*.{ts,tsx}`, `components/**/*.{ts,tsx}` | Wire the new rule in, same file scope as the existing SPACING/FONT rule |
| `driver-app/eslint.config.js` | Same change, mirrored | Same reason |
| `docs/change-log/2026-09-04-min-touch-target-lint-rule.md` | New file (this log) | Mandatory Change Impact & Risk Log for a live-tested-surface-adjacent change per `CLAUDE.md` |

## 7. Before / after

```js
// Before (rider-app/eslint.config.js and driver-app/eslint.config.js) — no MIN_TOUCH check exists.
```

```js
// After — new config block added after the existing SPACING/FONT block, own plugin/rule:
{
  files: ['app/**/*.{ts,tsx}', 'store/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}'],
  plugins: {
    spinr: { rules: { 'min-touch-target': minTouchTarget } },
  },
  rules: {
    'spinr/min-touch-target': 'warn',
  },
},
```

## 8. Rollback plan

Revert the four changed/added files (`git revert`) or delete the new config block plus the
`eslint-rules/min-touch-target.js` files — a plain `git revert` **is** a sufficient rollback: this
PR writes no data, applies no migration, and touches no live state.

## 9. Verification performed

- [x] Automated tests run (full `jest` suite, both apps, real production `yarn install` first —
      not a dev-server or partial run):
  - driver-app: 127/127 test suites, 1438/1438 tests passed (42.04s)
  - rider-app: 143/143 test suites, 1963/1963 tests passed (44.38s)
  - Confirms the ESLint config change and new rule files don't affect the Jest toolchain (Jest
    does not load `eslint.config.js`, but the full suite was run anyway per CLAUDE.md's
    "no assumption" rule).
- [x] `npx tsc --noEmit` run in both apps: **clean, 0 errors** in both (rider-app 19.17s,
      driver-app 17.65s).
- [x] `npx eslint app components` run in both apps, before/after via `git stash` isolating just
      the two `eslint.config.js` diffs (the new `eslint-rules/*.js` files are untracked and stay
      present either way, but the rule is inert unless `eslint.config.js` requires and registers
      it):
  - driver-app baseline (stashed, rule absent): 8 errors (pre-existing `react/no-unescaped-entities`,
    unrelated to this change), 2063 warnings. After (rule restored): same 8 errors, 2064
    warnings — **exactly +1**, matching the one genuine finding below. No other rule's count moved.
  - rider-app baseline: 0 errors, 2644 warnings. After: identical, 0 errors, 2644 warnings — the
    new rule fires 0 times against rider-app's real `app`/`components` files (see the
    `createStyles(colors) =>` factory-pattern limitation in §"What was NOT verified" below); no
    other rule's count moved either.
- [x] Ran the new rule against a synthetic fixture (9 cases: true positives — plain small style,
      inline-object small style; true negatives — hitSlop-compensated, already-≥44pt, array-merge
      compensated; skip cases — padding-only/no explicit size, dynamic conditional array entry,
      non-literal `hitSlop`) in both apps and confirmed every case resolves exactly as designed —
      2 warnings fired (the 2 true positives), the 3 true-negative/skip-adjacent cases and the 2
      pure-skip cases correctly silent.
- [x] Ran against real code and manually confirmed the flagged hit is genuine: driver-app's
      `components/ScreenHeader.tsx` — a `TouchableOpacity` `back` button (40x40, no `hitSlop`,
      styled via `StyleSheet.create`) used by every screen that renders `ScreenHeader` (shared
      component). Confirmed **no false positives**: the only other hit-adjacent candidates found
      by grep (`aiAvatar`/`locationPin` in rider-app's `ai-assistant.tsx`, 26x26/22x22) are plain
      `<View>` decorative icon containers, not `Touchable*`/`Pressable` — correctly not flagged.
- [x] Blast-radius grep performed: confirmed neither `eslint.config.js` has any other importer in
      the repo; confirmed the new config block (new `plugins`/`rules` keys for a new rule name)
      does not share an array with, and does not affect, the pre-existing `no-restricted-syntax`
      blocks (`error.message` at `error`, hex-color + SPACING/FONT at `warn`) in the same files —
      verified empirically via the stash-isolated before/after counts above, not just by reading
      the config structure.
- [x] Reviewed against relevant CLAUDE.md convention: no state-machine/money/RLS/PIPEDA surface
      touched; this is a pure dev-tooling/lint change, same category as PR #4951.
- [ ] Feature-flagged if user-visible and non-trivial — n/a, no user-visible surface exists for a
      lint rule.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation needed).
- [x] Blast radius is stated, not assumed: isolated to two `eslint.config.js` files and two new
      support files; no other importers; verified interaction with the pre-existing
      `no-restricted-syntax` rules in the same files rather than assuming independence.
- [x] No silent behavior change to an already-shipped flow — this is new lint enforcement only;
      no application code changed.

## What was NOT verified

- **Real-world coverage is narrower than "every undersized touchable" — by design, and confirmed
  empirically.** The rule requires a *directly* top-level `const styles = StyleSheet.create({...})`
  to resolve a `styles.foo` reference; it does not follow the `const createStyles = (colors) =>
  StyleSheet.create({...})` theme-factory pattern used for theme-aware styles (`const styles =
  createStyles(colors)` inside the component). That pattern appears in ~4 of ~10 style-defining
  files in each app's `app`/`components` dirs (grepped both apps). This is why rider-app shows 0
  new warnings today despite real undersized touchables likely existing in those factory-styled
  files — not a rule bug (confirmed by isolating the exact pattern in a standalone fixture, where
  it correctly resolves and flags a plain top-level `StyleSheet.create` but silently skips the
  factory-wrapped one, per the rule's own documented "unresolvable → skip" contract). Extending
  resolution into arrow-function-wrapped `StyleSheet.create` calls was judged out of scope for
  this PR (adds real resolution complexity for what's still a `warn`-only, non-blocking rule);
  flagging as a known follow-up if broader coverage is wanted later.
- The one real violation this rule surfaces today (driver-app's `ScreenHeader` back button) was
  **not fixed** — this PR is enforcement-only, matching PR #4951's explicit precedent of not
  touching call sites in the same change that adds the rule.
- No real production build (`expo export` / EAS build) was run for either app — this PR changes
  only dev-tooling lint config plus two new files that are never imported by application code
  (only by `eslint.config.js`, which participates in neither Metro nor EAS's build graph), so a
  production build was judged out of scope; `tsc --noEmit` and the full `jest` suite were run
  instead as the closest available real checks, matching PR #4951's precedent for the same class
  of change.
- Neither rider-app nor driver-app has any visual-regression tooling (per CLAUDE.md), but that is
  moot here since no rendered output changes — this is a lint-only diff.
- The rule was verified via direct `node_modules/.bin/eslint` invocation, not via a CI workflow —
  confirmed (grepping `.github/workflows/ci.yml`) that neither `rider-app-test` nor
  `driver-app-test` currently runs `eslint` at all, so this rule (like PR #4951's) is advisory in
  this repo's CI today regardless of severity; not something this PR changes or was asked to fix.
