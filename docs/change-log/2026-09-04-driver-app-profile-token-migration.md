# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | See PR description |
| Related issue or gap ID | `/design Spinr Apps` audit follow-up — opportunistic migration onto the driver-app hex-color / spacing-font lint rules added in `docs/change-log/2026-09-04-spacing-font-touch-target-lint-rule.md` |

## 1. Issue / gap identified

driver-app's `eslint.config.js` carries two warn-level `no-restricted-syntax` rules (hex-color
literals, and `padding`/`margin`/`fontSize` numeric literals) that are enforcement-only —
~423 hex and ~1,634 spacing/font pre-existing violations were left unmigrated on purpose, per
the audit's "lint enforcement + opportunistic migration, not a redesign" recommendation. This
is the first opportunistic migration round against that backlog.

## 2. Root cause

No prior round of driver-app token migration had happened yet (unlike admin-dashboard's
`#2816` batches). `app/driver/(tabs)/profile.tsx` (the driver Profile tab) was the single
highest-warning file in the app (186 of 2,063 total warnings: 73 hex + 113 spacing/font) —
a natural, bounded first target.

## 3. Fix / remediation

Two commits, both scoped to `app/driver/(tabs)/profile.tsx`:

1. **Hex-color migration** — 41 of 73 raw hex-color literals replaced with `useTheme()`
   semantic tokens (`colors.success`/`warning`/`danger`/`gold`/`orange`/`border`/
   `surfaceLight`/`textDim`). Only literals with a genuine semantic match were migrated;
   see §4 for what was deliberately left and why.
2. **Spacing/font migration** — 50 of 113 raw `padding`/`margin`/`fontSize` literals
   replaced with `SPACING`/`FONT` constants from `shared/utils/responsive.ts` (new import
   added). Scope was deliberately limited to literals whose exact numeric value already
   equals a `SPACING` (4/8/16/24/32/48) or `FONT` (11/13/15/16/22/26) scale value — a pure
   token substitution with **zero pixel-level change**. Values that don't exactly match the
   scale (e.g. `paddingVertical: 14`, `fontSize: 18`, `marginTop: 2`) were left as
   pre-existing hardcoded literals rather than rounded onto the nearest token, since driver-app
   has no visual-regression tooling to verify a pixel shift is safe (see §9).

Net effect on this file: 186 → 95 warnings (49% reduction). App-wide (`app`, `components`,
`store`): hex 425 → 384 (−41), spacing/font 1,638 → 1,588 (−50), total 2,063 → 1,972 (−91).
8 pre-existing `react/no-unescaped-entities` errors in this file are untouched (unrelated to
this task, not touched by either commit).

### What was migrated vs. deliberately left (hex colors)

Migrated (semantic match to an existing theme token):
- Document/driver verification status colors (pending → `colors.warning`, rejected/expired →
  `colors.danger`, valid/approved → `colors.success`) — these drive the same status badge
  logic used elsewhere in the app, so using the real semantic tokens keeps them consistent
  with how status is shown throughout, not just visually similar.
- Sign-out (destructive) actions and the "Application Rejected" banner → `colors.danger`.
- Star rating icon → `colors.gold` (exact hex value already matches the token in both themes).
- Lost & Found icon → `colors.orange` (exact semantic token already exists for this purpose).
- Chevron-forward row-affordance icons, the disabled save-button background, and `TextInput`
  placeholder text → `colors.textDim` / `colors.border`. These were genuine **dark-mode
  contrast bugs**, not just lint nits: a hardcoded light gray (`#D1D5DB`, `#B0B7C0`) against
  a dark-mode surface has materially lower contrast than the theme's own dim/border tokens,
  which are tuned per-theme.
- The Edit-Profile/Licence modal's `LinearGradient` second stop and a document-status icon
  fallback background → `colors.surfaceLight`. Also a real dark-mode bug: the previous
  hardcoded `#F8F9FA`/`#F9FAFB` (near-white) would render as a bright patch against a dark
  modal background in dark mode.

Deliberately left hardcoded (forcing a token here would be semantically wrong, not just
inconvenient):
- `#fff` (18 occurrences) — decorative text/icons on surfaces that are the *same* fixed brand
  color in both light and dark theme (the header gradient, colored status banners/badges, the
  primary-color save button). `ThemeColors` has no "white"/"onPrimary" token, and there isn't
  one because these surfaces don't derive from `background`/`surface` — mapping them to any
  existing token would be wrong in one theme or the other.
- `#000` (5 occurrences) — `shadowColor` for elevation shadows. No shadow token exists in
  `ThemeColors`, and iOS-style elevation shadows are conventionally black regardless of theme.
- Decorative per-row accent icon colors (`#38BDF8` email/year, `#8B5CF6` quests, `#6366F1`
  license plate) — this screen deliberately gives each info/menu row a distinct bright icon
  color for visual scannability (an arbitrary categorical palette, not a semantic
  success/warning/danger/info signal); the theme has no matching purple/indigo token, and
  forcing e.g. purple onto `colors.info` (blue) would be visibly wrong.
- `#991B1B` — a deliberately *darker* shade of red used only for the rejection-box body text
  (for readability against the lighter red icon/title above it). `ThemeColors` has a single
  `danger` shade, not a darker text-safe variant, so there is no accurate token for this.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to a single file.** `app/driver/(tabs)/profile.tsx` is an
  Expo-Router file-route, rendered only by the router (Profile tab); grepped `app`,
  `components`, `store` for any import of this path — none found (only a comment reference in
  `app/_layout.tsx`). No shared component, hook, or utility was touched.
- Both `shared/theme/index.ts` (`ThemeColors`) and `shared/utils/responsive.ts`
  (`SPACING`/`FONT`) were read-only in this change — no new tokens were added, no existing
  token's value was changed. Every other consumer of those two files is unaffected.
- No backend, ride-state, payment, or auth code path is touched — this is a driver-app UI
  styling change only.
- The two dark-mode contrast fixes (chevron/placeholder/border/gradient colors, §3) are a
  **visible improvement** in dark mode, not a regression — but they are still a rendered-output
  change and the corresponding pixels were not screenshotted (see §9).

## 5. User-experience effect

- **Driver-facing only** — the driver Profile tab (own profile, vehicle, documents, support
  menu, edit-profile and licence-entry modals).
- **Visible mid-session**: yes, if a driver has the Profile tab open (or opens it) after this
  ships, they will see it immediately — same screen, same layout, no new/removed elements.
  Exact-match spacing/font substitutions produce **zero pixel change** by construction. The
  hex-color substitutions are intended to be visually identical in light mode (all mapped
  values are the same semantic color family, e.g. green→green, red→red) and are a genuine
  visual *fix* in dark mode for the border/placeholder/chevron/gradient cases described above.
- No copy or notification text was changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/profile.tsx` | 41 hex-color literals → `useTheme()` tokens; added `SPACING`/`FONT` import; 50 exact-match padding/margin/fontSize literals → `SPACING`/`FONT` constants | Design-token migration (audit follow-up), see §3 |

## 7. Before / after

```tsx
// Before
const badgeColor =
    docStatus === 'pending' ? '#F59E0B' :
    docStatus === 'rejected' ? '#EF4444' :
    isExpired ? '#EF4444' :
    isExpiringSoon ? '#F59E0B' :
    (isValid || docStatus === 'approved') ? '#10B981' :
    '#EF4444'; // upload required
```

```tsx
// After
const badgeColor =
    docStatus === 'pending' ? colors.warning :
    docStatus === 'rejected' ? colors.danger :
    isExpired ? colors.danger :
    isExpiringSoon ? colors.warning :
    (isValid || docStatus === 'approved') ? colors.success :
    colors.danger; // upload required
```

```tsx
// Before
  card: {
    backgroundColor: colors.surface,
    borderRadius: 24,
    paddingHorizontal: 16,
    paddingVertical: 8,
```

```tsx
// After
  card: {
    backgroundColor: colors.surface,
    borderRadius: 24,
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.sm,
```

## 8. Rollback plan

This is a pure UI-styling change to one file with no data, migration, or config component —
`git revert` of the two commits (or either one independently) is a complete, safe rollback.
Neither commit depends on the other (hex and spacing/font are orthogonal literal families), so
either can be reverted alone without touching the other. No feature flag was used: the change
is not risky/behavioral enough to warrant one (no logic change, no new/removed UI elements,
same component tree), consistent with a pure token-substitution styling change.

## 9. Verification performed

- [x] `npx eslint app components store` before and after — confirmed the intended warning drop
      and no new errors (before: 8 errors / 425 hex / 1,638 spacing / 2,063 total; after: 8
      errors / 384 hex / 1,588 spacing / 1,972 total — errors unchanged, both counts dropped by
      exactly the number of literals migrated in each commit).
- [x] `npx tsc --noEmit` — clean, no errors, both before committing each commit.
- [x] Full `jest` suite — **127/127 suites, 1,438/1,438 tests passed**, including the dedicated
      `__tests__/app/driverProfileScreen.test.tsx` (23/23 tests) both after the hex-color
      commit and again after the spacing/font commit.
- [x] Blast-radius grep performed: `grep -rn "tabs)/profile" app components store` — only a
      comment reference in `app/_layout.tsx`; no real importer of this route file.
- [x] Reviewed against `CLAUDE.md` conventions: task decomposition (2 commits, 1 file, ≤200
      lines each — 81 and 99 lines respectively), surgical-changes (no unrelated reformatting;
      the 8 pre-existing `react/no-unescaped-entities` errors in this file were left untouched
      and are called out, not silently fixed).
- [ ] Feature-flagged: not applicable/justified — see §8.

## 10. What was NOT verified

- **No visual regression tooling exists for driver-app** (per `CLAUDE.md` §6: "rider-app and
  driver-app have none at all"). Every color and spacing change in this PR was reasoned about
  from theme token values and the file's existing usage patterns, not screenshotted or visually
  diffed in light or dark mode, on any device size, before or after. This applies to both the
  "zero pixel change" spacing substitutions (verified correct by construction — same literal
  value) and the hex-color substitutions (verified correct by *semantic* reasoning about theme
  intent, not by rendering the screen).
- Not manually exercised in a running Expo app/simulator (no device/simulator available in this
  environment) — verification is `tsc`/`eslint`/`jest` plus static code reading only.
- The 32 remaining hex-color and 63 remaining spacing/font warnings in this same file were
  deliberately left (see §3) and are not addressed by this PR — a future round can revisit them
  once/if visual-regression tooling exists for driver-app, since several of the "leave" cases
  (e.g. `#fff` on fixed-color surfaces) would need a new `ThemeColors` token (e.g. an
  "onPrimary"/white token) to migrate correctly, which is out of scope for a lint-cleanup PR.
- This is a single bounded round against a ~2,000-warning backlog; the rest of driver-app
  (`components/`, `store/`, and every other screen) is untouched and still fully at the
  pre-existing warning baseline.

## Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data/migration/flag involved)
- [x] Blast radius is stated, not assumed (isolated to one unimported route file)
- [x] No silent behavior change to an already-shipped flow — this is a pure styling/token
      substitution with the "what was NOT verified" boundary stated explicitly above
