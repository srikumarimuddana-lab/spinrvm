# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Found in the same codebase-wide audit that produced the emergency-contact and vehicle-type icon-color fixes |

## 1. Issue / gap identified

`driver-app/app/driver/faq.tsx` groups FAQs into category sections (Onboarding/Payments/Documents/Technical/general) and gives each section header a distinct icon via `CATEGORY_ICONS`, but every header icon rendered with the same hardcoded `colors.primary`. All category sections on the (single, scrollable) Help Center screen read as visually identical except for icon shape.

## 2. Root cause

Not applicable (enhancement, not a defect) — same shape as the prior fixes: `CATEGORY_ICONS` only ever supplied a glyph name, with no color dimension.

## 3. Fix / remediation

Added a parallel `CATEGORY_COLORS` map (plus `DEFAULT_CATEGORY_COLOR` for the `general`/unrecognized fallback) next to the existing `CATEGORY_ICONS`:

| Category | Icon | Color |
|---|---|---|
| Onboarding | rocket | `#8B5CF6` (purple) |
| Payments | wallet | `#10B981` (green) |
| Documents | document-text | `#3B82F6` (blue) |
| Technical | settings | `#6B7280` (gray) |
| general / unrecognized | help-circle | `#F59E0B` (amber) |

The section header's icon and its background circle now use the category's color instead of the flat `colors.primary` tint.

## 4. Risk & impact on existing functionality

- **Blast radius: confirmed isolated.** Grepped `driver-app/` for `CATEGORY_ICONS`/`CATEGORY_COLORS` — two other files (`lost-and-found.tsx`, `report-safety.tsx`) declare their own same-named local consts, but these are separate lexical scopes with no shared import; `faq.tsx`'s copy has no other reader.
- Purely decorative — does not touch FAQ fetching, search/filter logic, category grouping/sort order, or the accordion expand/collapse behavior.
- No backend, schema, or API change.

## 5. User-experience effect

- **Driver-facing.** Visible the next time a driver opens the Help Center: each category section header now has a color specific to that category instead of every header sharing the same red brand tint.
- **Visible mid-session?** The Help Center screen re-renders its full FAQ list on each mount (no long-lived open session to worry about); this is a one-time visual change on next load.
- No copy or functional change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/faq.tsx` | Added `CATEGORY_COLORS`/`DEFAULT_CATEGORY_COLOR`; the category section header's icon and background circle now use the category's color instead of `colors.primary` | Give each FAQ category a distinguishing accent color, matching the icon-shape distinction that already existed |
| `driver-app/__tests__/app/faqScreen.test.tsx` | Added 2 tests: two categories render distinct, non-brand-color header icons; an unrecognized category falls back to the default accent color | Regression coverage |
| `docs/change-log/2026-09-05-driver-app-faq-category-color.md` | New file (this log) | Required for a change to a `drivers`-domain surface per CLAUDE.md's live-testing gate |

## 7. Before / after

```tsx
// Before
<View style={styles.categoryIconBox}>
  <Ionicons name={(CATEGORY_ICONS[category] ?? 'help-circle') as any} size={16} color={colors.primary} />
</View>
```

```tsx
// After
<View style={[styles.categoryIconBox, { backgroundColor: (CATEGORY_COLORS[category] ?? DEFAULT_CATEGORY_COLOR) + '15' }]}>
  <Ionicons name={(CATEGORY_ICONS[category] ?? 'help-circle') as any} size={16} color={CATEGORY_COLORS[category] ?? DEFAULT_CATEGORY_COLOR} />
</View>
```

## 8. Rollback plan

Pure frontend, additive-only visual change — no migration, no feature flag, no data change. Revert is a plain `git revert` of this commit; the screen returns to a single shared brand-color tint for every category header.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean, no errors.
- [x] `npx eslint` — 0 errors; new warnings are the same pre-existing "no hardcoded hex colors" style rule already present elsewhere in this file.
- [x] Added 2 new tests, confirmed both icon names used (`rocket`, `wallet`, `help-circle`) don't collide with any other hardcoded icon in this file (learned to check this the hard way during the vehicle-type-color fix earlier this session — see `docs/change-log/2026-09-05-vehicle-type-icon-color.md` §9). Full test file: 11/11 passed.
- [x] Ran the entire driver-app jest suite: 130 suites / 1478 tests, all passed.
- [x] Blast-radius grep: confirmed `CATEGORY_ICONS`/`CATEGORY_COLORS` in this file has no other readers; two other files share the const name but are separate, unrelated local scopes.

### What was NOT verified

- Not run on a real device/simulator — no Expo runtime available in this sandboxed session.
- driver-app has no automated visual-regression tooling at all (per CLAUDE.md) — reasoned about via the jest color/icon assertions and code review, not screenshotted.
- Color-contrast/accessibility of the accent colors was reasoned about (same palette style as the already-accepted emergency-contact and vehicle-type fixes) but not measured with a contrast-ratio tool.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data path).
- [x] Blast radius is stated, not assumed (isolated to one file; confirmed via grep).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — UX effect (decorative-only, not mid-session, no functional change) is stated in §5.
