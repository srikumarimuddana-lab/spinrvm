# Change Impact & Risk Log — resolve the undocumented #2563EB blue (#4640 Finding 3)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Surface(s) | rider-app, driver-app (design consistency) |
| Related issue | #4640 (round-2 swarm audit) Finding 3 |

## Issue/gap identified

`#2563EB` (Tailwind blue-600) was hardcoded at 4 sites across both apps — a third, undocumented blue
distinct from the theme's real info token (`colors.info`: `#3B82F6` light / `#0A84FF` dark) — never
adapting between themes.

## Root cause

No token existed for this color when these 4 call sites were written; each one hardcoded the literal
independently.

## Fix/remediation — split treatment, not a uniform find-and-replace

The 4 sites are not equivalent, so a blind swap to `colors.info` everywhere would have traded one
inconsistency for a real regression risk:

1. **`driver-app/app/driver/(tabs)/profile.tsx`, `rider-app/app/(tabs)/account.tsx`** ("Help Center" menu-row
   icon) — these sit in the normal themed surface. Switched to `colors.info` (icon) and `${colors.info}1A`
   (background tint), matching the existing tint-background convention already used elsewhere in both apps
   (`colors.primary}1A`, see `driver-app/settings.tsx`, `payout.tsx`, `profile-setup.tsx`).
2. **`driver-app/app/driver/ride-detail.tsx`** (the "Imported from the previous app" map-overlay pill,
   2 sites: icon + text color) — **left hardcoded, but named and documented**. This pill's background
   (`rgba(255,255,255,0.95)`) is itself deliberately theme-independent (stays near-white so it's legible
   over the map regardless of theme), so swapping its text/icon to `colors.info` would introduce dark
   mode's `#0A84FF` — tuned for a dark surface — against a background that never goes dark. That's a real
   contrast-regression risk, not a fix. Extracted to a named module constant
   (`ROUTE_STATUS_PILL_ICON_COLOR`) with a comment explaining why it's intentionally fixed, turning
   "undocumented and inconsistent" into "documented and intentional" without gambling on a visual
   regression this repo has no tooling to catch (B38, no committed visual-regression baselines).

## Risk & impact on existing functionality

- **Blast radius: isolated to 3 files, 4 call sites**, all cosmetic (icon/text color only — no layout,
  no logic, no test-relevant behavior). `git diff | grep -viE "color|Color"` confirms no non-color line
  changed.
- The sibling "Lost & Found" orange icon (`#F97316`, also hardcoded, immediately adjacent to the Help
  Center icon in both menu rows) was **not** touched — it wasn't named in this finding, and changing only
  one of two sibling icons to theme-adapt is itself worth a follow-up, not bundled into this fix.

## User experience effect

Visible only in dark mode: the Help Center icon on 2 screens now renders in the theme's dark-mode info
blue (`#0A84FF`) instead of the fixed `#2563EB`. Not screenshotted (no active visual-regression tooling
for either app, per CLAUDE.md's standing note) — reasoned about via the existing, already-verified
`colors.info`/`}1A` tint pattern used elsewhere in both apps.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/profile.tsx` | Help Center icon/background → `colors.info`/`${colors.info}1A` | Theme-adapt, matches surrounding themed surface |
| `rider-app/app/(tabs)/account.tsx` | Help Center `MenuRow` icon/background → `colors.info`/`${colors.info}1A` | Same |
| `driver-app/app/driver/ride-detail.tsx` | Extracted `#2563EB` to a documented `ROUTE_STATUS_PILL_ICON_COLOR` constant, applied at both pill sites | Document the deliberate theme-independent exception instead of leaving it unexplained |

## Rollback plan

`git-revert-safe` — pure styling change, no data/schema/logic.

## Verification performed

- `tsc --noEmit` clean on both apps.
- Full jest suites: driver-app 1936/1936, rider-app 1937/1937 (both baselines, unaffected by this change).
- Targeted screen tests re-run: `driverProfileScreen.test.tsx`, `rideDetailBackButton.test.tsx`,
  `driverRideDetailScreen.test.tsx`, `accountScreen.test.tsx` — all pass.

## What was NOT verified

- No live device/simulator screenshot in dark mode for either theme-adapted site — no visual-regression
  tooling exists for either app (standing gap, B38). The `colors.info`/`}1A` pattern itself is already
  proven elsewhere in both apps, which is what this change relies on rather than a fresh visual check.
- The sibling orange "Lost & Found" icon's own theme-adaptation was not assessed as part of this change.
