# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-06 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-09-05-vehicle-type-icon-color.md`, which fixed the same gap in rider-app/driver-app and flagged this admin-dashboard instance as a weaker match, intentionally deferred |

## 1. Issue / gap identified

`admin-dashboard/src/app/dashboard/vehicle-types/page.tsx`'s `VEHICLE_ICON_MAP` gives each vehicle type (`car-compact`/`car-sport`/`bus`/`bus-outline`) a distinct lucide icon shape, but two rendered instances used the dashboard's default muted foreground color for all four types:
- The card-grid fallback icon (shown when a type has no uploaded illustration) — `text-muted-foreground/30`.
- The Add/Edit dialog's icon picker, where all 4 options are shown side by side — no color class at all (default text color).

The picker in particular is a genuine "compare 4 options" list, the exact pattern already fixed for rider-app/ride-options.tsx and driver-app/vehicle-info.tsx.

## 2. Root cause

Not applicable (enhancement, not a defect) — same shape as the mobile-app fixes: the icon map only ever supplied a glyph component, with no color dimension.

## 3. Fix / remediation

Added two Tailwind-class color maps (following this dashboard's own established categorical-color convention — see `driver-timeline.tsx`'s `EVENT_CONFIG` for the precedent, including its `#2816`-tracked `eslint-disable` block for the "raw Tailwind color utility" lint rule, which the new maps here also need and now carry):

| Icon key | Picker color (`VEHICLE_ICON_COLORS`) | Card-fallback color (`VEHICLE_ICON_MUTED_COLORS`) |
|---|---|---|
| `car-compact` | `text-blue-600` | `text-blue-300` |
| `car-sport` | `text-amber-600` | `text-amber-300` |
| `bus` | `text-emerald-600` | `text-emerald-300` |
| `bus-outline` | `text-violet-600` | `text-violet-300` |
| unrecognized/missing | `text-gray-500` | `text-muted-foreground/30` (unchanged default) |

Hues match the same icon keys' colors already shipped in `rider-app/ride-options.tsx` and `driver-app/vehicle-info.tsx`, so a vehicle type reads as the same color across admin, rider, and driver.

**Deliberately left unchanged:** the small inline type-icon badge next to the capacity/marker metadata on each card (`vt.icon` rendered at `TypeIcon` near line 369) — it sits in a row alongside two other icons (`Users`, the map-marker preview) that are already neutral/uncolored, and giving only this one icon a per-type color would make it visually inconsistent with its own row rather than clearer.

**A real Tailwind pitfall caught and avoided:** the first draft tried to reuse `VEHICLE_ICON_COLORS` for the muted fallback by interpolating an opacity modifier at runtime (`` `${vehicleIconColor(icon)}/40` ``). Tailwind's scanner only generates CSS for a class string that appears **verbatim** somewhere in scanned source — a template-concatenated `"text-blue-600/40"` never matches anything the string `"text-blue-600"` alone would generate, so the class would silently do nothing at runtime (no build error, no lint error — just an inert class). Caught by reasoning through the build pipeline, not by observing the failure at runtime. Fixed by using a fully-literal second map (`VEHICLE_ICON_MUTED_COLORS`) instead, with each class spelled out as a complete string.

## 4. Risk & impact on existing functionality

- **Blast radius: confirmed isolated.** Grepped `admin-dashboard/` for `VEHICLE_ICON_MAP`/`VEHICLE_ICON_COLORS`/`VEHICLE_ICON_MUTED_COLORS` — all local to this one file, no other reader.
- Purely decorative — does not touch vehicle-type CRUD, the icon-value stored on save, illustration/marker upload, or any API call.
- No backend, schema, or API change.
- **Also surfaced, not fixed:** `vehicleIconLabel()` Title-Cases the icon key and only strips a trailing `-outline`, so `"bus"` and `"bus-outline"` both label as `"Bus"` in the picker — a pre-existing, unrelated ambiguity noticed while writing this fix's tests (had to pick two other icon keys to get unambiguous button text). Not fixed here per the surgical-changes rule; flagging it rather than scope-creeping a label fix into a color-only change.

## 5. User-experience effect

- **Internal-admin-facing.** Visible the next time an admin opens Vehicle Types: the Add/Edit dialog's 4 icon-picker options now have distinct colors, and a vehicle type's card-grid fallback icon (when no illustration is uploaded) tints to that type's color instead of a flat muted gray.
- **Visible mid-session?** Only the next time the page or dialog is opened — not a live update to an already-open screen.
- No copy or functional change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx` | Added `VEHICLE_ICON_COLORS`/`vehicleIconColor()` (picker) and `VEHICLE_ICON_MUTED_COLORS`/`vehicleIconMutedColor()` (card fallback), each wrapped in a `#2816`-style `eslint-disable-next-line no-restricted-syntax` block; applied to the picker's `<Icon>` and the card grid's `<FallbackIcon>` | Give each vehicle type a distinguishing accent color, matching the icon-shape distinction that already existed, and matching the mobile-app fix's palette for cross-surface consistency |
| `admin-dashboard/src/__tests__/dashboard/vehicle-types-icon-color.test.tsx` | New file: 3 tests — card-grid fallback icon color per type, neutral fallback for an unrecognized icon, and distinct picker-option colors in the Add dialog | Regression coverage |
| `docs/change-log/2026-09-06-admin-vehicle-types-icon-color.md` | New file (this log) | Required for a behavior change to an `admin` surface per CLAUDE.md's live-testing gate |

## 7. Before / after

```tsx
// Before — card-grid fallback icon
const FallbackIcon = VEHICLE_ICON_MAP[vt.icon] || Car;
return <FallbackIcon className="h-16 w-16 text-muted-foreground/30" />;

// Before — Add/Edit dialog icon picker
<Icon className="h-6 w-6" />
```

```tsx
// After — card-grid fallback icon
const FallbackIcon = VEHICLE_ICON_MAP[vt.icon] || Car;
return <FallbackIcon className={`h-16 w-16 ${vehicleIconMutedColor(vt.icon)}`} />;

// After — Add/Edit dialog icon picker
<Icon className={`h-6 w-6 ${vehicleIconColor(value)}`} />
```

## 8. Rollback plan

Pure frontend, additive-only visual change — no migration, no feature flag, no data change. Revert is a plain `git revert` of this commit; both render sites return to their prior neutral/muted coloring.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean, no errors.
- [x] `npx eslint` on both changed files — 0 errors. The "raw Tailwind color utility" (`#2816`) warnings the new maps would otherwise trigger are suppressed with the same `eslint-disable`/`eslint-enable` block pattern already used by `driver-timeline.tsx`'s `EVENT_CONFIG`, with a one-line reason as that rule requires.
- [x] Added 3 new tests in a new file, following this repo's existing pattern for testing a real page component (`area-heatmap-overrides.test.tsx`): mocked `@/lib/api`'s `getVehicleTypes`, dynamically imported the page component (a static top-level import would be hoisted by the ES module spec ahead of the mock-factory's own top-level `const`, hitting a temporal-dead-zone reference error — caught this while writing the test, not guessed at). All 3 pass.
- [x] Ran the **entire admin-dashboard vitest suite**: 62 suites / 580 tests, all passed.
- [x] Ran a **real production build** (`npm run build`, not just `tsc --noEmit`) — exit code 0, `/dashboard/vehicle-types` compiled cleanly alongside every other route. (First attempt hit a pre-existing, unrelated sandbox disk-space ceiling on the Turbopack build cache — cleared the stale cache directory and reran clean; not a code issue.)
- [x] Blast-radius grep: confirmed the new maps and `VEHICLE_ICON_MAP` have no other readers outside this file.

### What was NOT verified

- **No visual-regression coverage exists for this page.** Per `CLAUDE.md`, admin-dashboard's Playwright visual-regression suite (`e2e/visual-regression.spec.ts`) is real and merge-blocking, but only for its 6 seeded pages (`login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`, `dashboard-rides`) — `dashboard-vehicle-types` is not one of them. This change is reasoned about via the vitest DOM-class assertions and code review, not screenshotted, and no baseline needs re-capturing since none exists.
- Not manually clicked through in a real browser session — verified via the automated test suite and a clean production build only.
- Color-contrast/accessibility of the accent colors was reasoned about (same hue family already accepted for `driver-timeline.tsx`'s `EVENT_CONFIG`, at Tailwind's standard 600/300 shade weights) but not measured with a contrast-ratio tool.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data path).
- [x] Blast radius is stated, not assumed (isolated to one file; confirmed via grep).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — UX effect (admin-facing, decorative-only, not mid-session, no functional change) is stated in §5.
- [x] Visual-regression coverage gap stated explicitly per §9, not silently assumed covered.
