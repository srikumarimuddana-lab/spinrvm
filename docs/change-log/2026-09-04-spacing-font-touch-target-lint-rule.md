# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | rider-app / driver-app |
| Domain (Sentry tag) | n/a (lint-only, dev-tooling change; no runtime code path touched) |
| PR / commit link | See PR description |
| Related issue or gap ID | `/design Spinr Apps` audit finding: "Adopt SPACING/FONT/MIN_TOUCH from shared/utils/responsive.ts" |

## 1. Issue / gap identified

`shared/utils/responsive.ts` exports a well-built `SPACING`/`FONT` token scale (plus dynamic
font scaling, breakpoints, tablet-aware sheet snaps) that is used in almost nothing — screens
and components hardcode raw numeric `padding`/`margin`/`fontSize` literals instead, so a future
spacing/typography pass has no single source of truth to change.

## 2. Root cause

No enforcement mechanism ever existed to nudge new code toward the token scale; the utility was
added but nothing in CI or lint flags a hardcoded literal at the property sites where a token
should be used, so the habit of hardcoding numbers propagated unchecked.

## 3. Fix / remediation

Added a second `no-restricted-syntax` selector pair (warn-level) to both `rider-app/eslint.config.js`
and `driver-app/eslint.config.js`, directly analogous to the existing hex-color design-token rule
in the same files. The new rule flags a bare numeric literal used as the value of any
`padding*`/`margin*`/`fontSize` object property inside `app/**/*.tsx`, `store/**/*.tsx`, and
`components/**/*.tsx`, and points the developer at `SPACING`/`FONT` from `shared/utils/responsive.ts`.
Two selector variants are needed: one for a plain positive `Literal` (`paddingTop: 8`) and one for
a negative number, which ESLint's parser represents as a `UnaryExpression` wrapping a `Literal`,
not a `Literal` itself (`marginLeft: -8`).

This is enforcement-only. No existing call site was touched or migrated — per the audit's own
recommendation ("lint enforcement + opportunistic migration, not a redesign"), the hundreds of
pre-existing violations are left as-is and are expected to be cleaned up file-by-file as those
files are touched for other reasons, the same posture already established for the hex-color rule.

**Why `warn`, not `error`:** an `error` severity would fail `eslint` (and any CI job that runs it
non-permissively) on essentially every screen file in both apps immediately — 1918 warnings in
rider-app and 1634 in driver-app today (see counts below). That would block unrelated PRs from
merging over pre-existing code the PR author didn't write and isn't in scope to fix. `warn`
matches the exact precedent set by the hex-color rule in the same files (same severity, same
"tighten later once cleaned up" comment).

**Selector-scoping rationale:** the selector is restricted to property keys matching
`/^(padding|margin|fontSize)/` rather than flagging all numeric literals, because plain numbers
are legitimately used for many things unrelated to spacing tokens (array indices, `opacity`,
`zIndex`, animation durations, `width`/`height`, `borderRadius`, etc.). A repo-wide numeric-literal
rule would have produced an unusable amount of noise; scoping to the specific property-name family
keeps every hit a real spacing/font hardcode. Verified (grep across both apps' `app/`, `components/`,
`store/`) that no property name matching that prefix regex exists in the codebase other than the
genuine RN style properties (`padding`, `paddingTop/Bottom/Left/Right/Horizontal/Vertical`,
`margin` + its same six directional/axis variants, `fontSize`) — no false-positive property names
like `marginError` or `paddingDays` were found.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to lint tooling.** Only `rider-app/eslint.config.js` and
  `driver-app/eslint.config.js` are modified; no application code changed. `no-restricted-syntax`
  is a static-analysis rule — it does not run at build or runtime, so it cannot change app
  behavior, only developer-facing lint output.
- **Other consumers of `no-restricted-syntax` in these files:** the existing `error.message`
  rule (`error` severity) and the hex-color design-token rule (`warn` severity) both already use
  `no-restricted-syntax` in the same two files. **Important finding from verification:** ESLint
  flat config does not merge an array-valued rule option (like `no-restricted-syntax`'s selector
  array) across two config objects that both match the same files — the later-declared object's
  array fully *replaces* the earlier one for that rule, it does not append. Adding the new
  selectors as their own separate config block (as first drafted) silently disabled the hex-color
  rule entirely (verified: hex-color warnings dropped from 729 → 0 in rider-app when the two
  blocks coexisted). **Fixed** by merging the hex-color selector and the two new spacing/font
  selectors into a single `no-restricted-syntax` array within one config object, with a code
  comment explaining why, so a future third selector added the "obvious" way (a new block) won't
  repeat the same silent-disable mistake. Re-verified after the fix: hex-color warnings are back
  to their full pre-existing count (729 rider-app / 423 driver-app) alongside the new rule firing.
- **CI impact:** none — `warn` severity does not fail `eslint`'s exit code, and no CI workflow in
  this repo was found to run `eslint` with `--max-warnings 0` for either app (both apps already
  carry hundreds of `warn`-level hex-color hits today with no red CI as a result).
- **No other importers/consumers:** `eslint.config.js` in each app is not imported by anything
  else in the repo (it's picked up by the ESLint CLI/IDE integration only), so there is no
  cross-file blast radius beyond the two files themselves.

## 5. User-experience effect

None. This changes only what developers see in their editor/lint output; it has no rider,
driver, corporate-admin, or internal-admin facing effect, visible mid-session or otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/eslint.config.js` | Merged a new `padding*`/`margin*`/`fontSize` numeric-literal `no-restricted-syntax` selector pair into the same rule array as the existing hex-color selector (previously two selector variants were staged as a separate config block, which was found to silently disable the hex-color rule and was corrected before commit) | Enforce `SPACING`/`FONT` token usage per the design audit finding |
| `driver-app/eslint.config.js` | Same change, mirrored | Same reason |
| `docs/change-log/2026-09-04-spacing-font-touch-target-lint-rule.md` | New file (this log) | Mandatory Change Impact & Risk Log for a live-tested-surface-adjacent change per `CLAUDE.md` |

## 7. Before / after

```js
// Before (rider-app/eslint.config.js and driver-app/eslint.config.js) — one block:
{
  files: ['app/**/*.{ts,tsx}', 'store/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}'],
  rules: {
    'no-restricted-syntax': [
      'warn',
      {
        selector: "Literal[value=/^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/]",
        message: 'Do not hardcode hex colors — call useTheme() ...',
      },
    ],
  },
},
```

```js
// After — same block, same files array, rules merged into one array:
{
  files: ['app/**/*.{ts,tsx}', 'store/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}'],
  rules: {
    'no-restricted-syntax': [
      'warn',
      {
        selector: "Literal[value=/^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/]",
        message: 'Do not hardcode hex colors — call useTheme() ...',
      },
      {
        selector:
          "Property[key.name=/^(padding|margin|fontSize)/][value.type='Literal'][value.raw=/^[0-9]+(\\.[0-9]+)?$/]",
        message: 'Do not hardcode padding/margin/fontSize values — use SPACING/FONT from shared/utils/responsive.ts.',
      },
      {
        selector:
          "Property[key.name=/^(padding|margin|fontSize)/][value.type='UnaryExpression'][value.operator='-'][value.argument.type='Literal'][value.argument.raw=/^[0-9]+(\\.[0-9]+)?$/]",
        message: 'Do not hardcode padding/margin/fontSize values — use SPACING/FONT from shared/utils/responsive.ts.',
      },
    ],
  },
},
```

## 8. Rollback plan

Revert the two `eslint.config.js` diffs (`git revert`) or delete the two new selector objects from
the `no-restricted-syntax` array — a plain `git revert` **is** a sufficient rollback here, unlike
the CLAUDE.md caveat for data-touching changes: this PR writes no data, applies no migration, and
touches no live state, so there is nothing to remediate beyond the code itself.

## 9. Verification performed

- [x] Automated tests run: full `jest` suite in both apps (rider-app: 140/140 test suites,
      1949/1949 tests passed, 64.17s; driver-app: 127/127 test suites, 1437/1437 tests passed,
      53.95s) — confirms the config change doesn't affect the Jest toolchain (Jest does not load
      `eslint.config.js`, but ran the full suite anyway per CLAUDE.md's "no assumption" rule.
- [x] `npx tsc --noEmit` run in both apps: **clean, 0 errors** in both.
- [x] `npx eslint app components store` run in both apps, before (branch stashed) and after:
  - rider-app baseline: 0 errors, 740 warnings (729 hex-color + others). After: 0 errors, 2658
    warnings (729 hex-color + 1918 new spacing/font rule + others) — **no new errors, only
    additive warnings**, hex-color count unchanged.
  - driver-app baseline: 8 errors (pre-existing `react/no-unescaped-entities`, unrelated to this
    change), 423 warnings. After: same 8 errors, 2057 warnings (423 hex-color + 1634 new
    spacing/font rule + others) — **error count identical before/after**.
- [x] Ran the new rule against known-offending real files in each app (`rider-app/app/lost-and-found.tsx`,
      `rider-app/app/pick-on-map.tsx` for the negative-margin `UnaryExpression` variant,
      `driver-app/app/vehicle-info.tsx`) and confirmed every flagged line is a genuine
      `padding*`/`margin*`/`fontSize` hardcode with a sensible message, no crash.
  - Confirmed **no false positives**: grepped both apps' `app/`, `components/`, `store/` dirs for
    every object-property name matching the `/^(padding|margin|fontSize)/` prefix and found only
    the genuine RN style property set (no `marginError`/`paddingDays`/etc.-style false hits).
- [x] Blast-radius grep performed: confirmed `eslint.config.js` in each app has no other
      importers in the repo; confirmed the only other rule sharing `no-restricted-syntax` in
      these files (`error.message`, `error` severity) is unaffected (0 warnings before and after,
      as expected — its selector array lives in a separate, still-distinct config block that
      wasn't touched).
  - **Found and fixed during verification**: staging the new selectors as a *separate* config
    block (mirroring the task's literal "second block" phrasing) silently zeroed out the
    hex-color rule's warnings due to ESLint flat config's non-merging array-rule semantics — see
    §4. Caught only by re-running the full lint and diffing warning counts per rule before/after,
    not by a smoke test on a single file.
- [x] Reviewed against relevant CLAUDE.md convention: no state-machine/money/RLS/PIPEDA surface
      touched; this is a pure dev-tooling/lint change.
- [ ] Feature-flagged if user-visible and non-trivial — n/a, no user-visible surface exists for a
      lint rule.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation needed).
- [x] Blast radius is stated, not assumed: isolated to two `eslint.config.js` files; no other
      importers; verified interaction with the pre-existing `no-restricted-syntax` rules in the
      same files rather than assuming independence.
- [x] No silent behavior change to an already-shipped flow — this is new lint enforcement only;
      no application code changed and nothing was migrated.

## What was NOT verified

- The hundreds of pre-existing violations were **not** migrated to `SPACING`/`FONT` — that is
  explicitly out of scope per the audit finding's own recommendation ("lint enforcement +
  opportunistic migration, not a redesign"). Real counts as of this commit: **1918 warnings in
  rider-app**, **1634 warnings in driver-app** (both apps' `app/`, `store/`, `components/` dirs,
  via `npx eslint app components store 2>&1 | grep -c 'Do not hardcode padding/margin/fontSize'`).
- No real production build (`expo export` / EAS build) was run for either app — this PR changes
  only dev-tooling lint config, not any file that participates in a Metro/EAS build, so a
  production build was judged out of scope; `tsc --noEmit` and the full `jest` suite were run
  instead as the closest available real checks. Flagging explicitly per CLAUDE.md rather than
  assuming build-equivalence.
- Neither rider-app nor driver-app has any visual-regression tooling (per CLAUDE.md), but that is
  moot here since no rendered output changes — this is a lint-only diff.
- The `MIN_TOUCH` (44pt minimum touch target) half of the original audit finding is **not**
  covered by this PR — a static ESLint selector cannot reliably determine a rendered element's
  effective touch-target size (it would require knowing the combined height/width/hitSlop of an
  arbitrary `TouchableOpacity`/`Pressable`, which isn't expressible as a simple AST pattern the
  way a numeric property literal is). Left out of scope rather than shipping a selector that
  can't actually catch real `MIN_TOUCH` violations; flagging for a follow-up if the team wants a
  narrower, purpose-built check for it (e.g. lint against `hitSlop`-less small fixed `width`/
  `height` on interactive components specifically).
