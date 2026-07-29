# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (spinr platform session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (added on push) |
| Related issue or gap ID | Epic #2785 Phase 2 (continuation of PR #2817) |

## 1. Issue / gap identified

Continuing Phase 2's "accent scale for admin-specific density needs (tables, badges, charts)" charter: investigated the app's two status/priority "badge" color helpers (`statusColor()` in `lib/utils.ts`, shared across the app; `P_COLORS` in `support/_tabs/tickets.tsx`, page-local) for the same class of contrast bug already found and fixed in the sidebar (PR #2817).

## 2. Root cause and methodology

**Important correction to an initial assumption**: #2803 hypothesized `statusColor()` (used by ~10 different status values) was a likely broken shared source. This was **empirically tested, not assumed** — mocked real ticket data with all 10 status values through the actual `support/_tabs/tickets.tsx` page (the crawl-audit's empty-mocked routes never render real badge content, so this was previously entirely untested) and ran axe against the real rendered output, in both themes (dark via `next-themes`' actual default, `defaultTheme="dark"` in `app/layout.tsx`; light via a `localStorage` override since forcing light requires it).

**Result of empirical testing**:
- `statusColor()`: 8 of 10 status values passed cleanly in both themes on first check. 2 failed in **light mode only** (previously invisible, since light mode was never exercised with real badge content by any existing test): `searching`/`open` (yellow-700, 4.46:1 — just under WCAG AA's 4.5:1) and `completed` (green-700, 4.32:1). Both already had correct dark-mode overrides (`dark:text-yellow-400`/`dark:text-green-400`, confirmed passing).
- `P_COLORS` (page-local, only consumer is this one file): had **no dark-mode overrides at all** — a single shade used in both themes, unlike its sibling `statusColor()`. 3 of 4 priority values failed in **both** themes: `low` (zinc-600, dark 2.27:1), `medium` (blue-600, dark 3.25:1 / light 4.39:1), `urgent` (red-600, dark 3.68:1 / light 3.82:1). Only `high` (amber-600) passed as-is, and only in dark mode (light mode: 2.85:1, badly failing).

## 3. Fix / remediation

- `statusColor()`: darkened light-mode text for `searching`/`open` (yellow-700→yellow-800) and `completed` (green-700→green-800). Dark-mode values (`-400` shades) were already correct and left untouched.
- `P_COLORS`: added dark-mode overrides to all 4 entries, reusing already-empirically-proven shades from the sibling `statusColor()` wherever the color family matched exactly (`medium`→`blue-700 dark:blue-400`, `urgent`→`red-700 dark:red-400` — both byte-identical to `statusColor()`'s `driver_assigned`/`cancelled` entries, which were already confirmed passing in both themes). For `low` (zinc) and `high` (amber), where no exact sibling entry existed: kept `low`'s light shade as-is (already confirmed passing) and added `dark:text-zinc-400`; derived a new light shade for `high` (amber-800, hand-verified at 6.32:1 against its own tint background) while explicitly preserving the already-good dark value (`dark:text-amber-600`, unchanged) rather than guessing a new dark shade.

## 4. Risk & impact on existing functionality

- **Blast radius**: `statusColor()` is a shared helper — grepped for all consumers and found only `support/_tabs/tickets.tsx` actually calls the *function* (a broader keyword grep for "status"/"Color" matched 20 files, but those use their own independent, unrelated status-coloring logic, not this shared helper — confirming this fix's real blast radius is exactly the one file, same as `P_COLORS`).
- Pure text-color-value changes within the same hue family (e.g., yellow-700→yellow-800 is a shade darker, not a different color) — no layout, structural, or interaction change.
- **Verified with real re-renders after the fix**, not just re-applying the same math: reran both diagnostic passes (dark, then light, via the actual UI with real mocked ticket data across all 10 statuses × 4 priorities) — **zero color-contrast violations in either theme**, confirmed by axe, not assumed from the token values alone.
- Existing unit tests (`__tests__/lib/utils.test.ts`, `statusColor` describe block) assert loose hue-family substrings (`.toContain('yellow')`, etc.), not exact shade numbers — all 17 tests still pass unchanged.
- `npm run lint` clean (one pre-existing, unrelated warning in a different tab/function of the same file, not touched by this change). `npm run build` clean.

## 5. User-experience effect

- **Internal-admin-facing only.** Support-ticket status badges (all pages) and support-ticket priority badges (`/dashboard/support`, tickets tab only) are slightly darker/more legible text, in whichever theme was previously failing. No layout or interaction change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/utils.ts` | `statusColor()`: `searching`/`open` yellow-700→yellow-800, `completed` green-700→green-800 (light-mode text only; dark unchanged) | Empirically-confirmed light-mode contrast failures (4.46:1, 4.32:1), both now ≥6:1 |
| `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` | `P_COLORS`: added dark-mode overrides to all 4 entries; darkened `medium` (blue-600→700), `urgent` (red-600→700), `high` (amber-600→800, light only) | No dark-mode variants existed at all; 3 of 4 failed in both themes when actually rendered |

## 7. Before / after

```ts
// Before (lib/utils.ts)
searching: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",   // light: 4.46:1 — fails AA
completed: "bg-green-500/15 text-green-700 dark:text-green-400",     // light: 4.32:1 — fails AA

// After
searching: "bg-yellow-500/15 text-yellow-800 dark:text-yellow-400",  // light: 6.19:1
completed: "bg-green-500/15 text-green-800 dark:text-green-400",     // light: 6.25:1
```

```ts
// Before (tickets.tsx) — no dark: variants at all
const P_COLORS = { low: "bg-zinc-500/15 text-zinc-600", medium: "bg-blue-500/15 text-blue-600",
                    high: "bg-amber-500/15 text-amber-600", urgent: "bg-red-500/15 text-red-600" };
// dark: low 2.27:1, medium 3.25:1, urgent 3.68:1 — all fail AA
// light: medium 4.39:1, high 2.85:1, urgent 3.82:1 — all fail AA

// After
const P_COLORS = { low: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
                    medium: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
                    high: "bg-amber-500/15 text-amber-800 dark:text-amber-600",
                    urgent: "bg-red-500/15 text-red-700 dark:text-red-400" };
// re-verified via axe, both themes: zero violations
```

## 8. Rollback plan

`git-revert-safe` — pure className string changes, no data/migration/Stripe state.

## 9. Verification performed

- [x] Empirically diagnosed with real rendered data (not hand-math-only, not assumed from #2803's hypothesis) — mocked all 10 status values + all 4 priority values through the real `support` page, both themes.
- [x] Re-verified after the fix, both themes, same method: **zero color-contrast violations** in either dark or light mode (previously: 2 statusColor failures in light, 3 P_COLORS failures in dark, 3 P_COLORS failures in light).
- [x] Grepped for other consumers of both `statusColor()` and `P_COLORS` — confirmed blast radius is exactly the 2 files changed.
- [x] Ran existing unit tests (`utils.test.ts`) — 17/17 pass unchanged (loose hue-family assertions, not exact-shade).
- [x] `npm run lint` clean (1 pre-existing unrelated warning), `npm run build` clean.
- [ ] Feature-flagged: not applicable — same low-risk reasoning as the sidebar fix (#2817): pure color-value change, no structural change.

## What was NOT verified

- Did not audit the other ~19 files matched by the broader "status/Color" keyword grep — those use independent, page-local status-coloring logic unrelated to `statusColor()`/`P_COLORS`, and are part of the much larger #2816 (91-file hardcoded-color) finding, not this fix.
- Did not re-run the full 41-route `a11y-baseline.json` suite — these badges never render in the crawl-audit's empty-mocked routes (confirmed: no baseline entry for `/dashboard/support` changed), so this fix has no effect on that baseline file and none was updated. The improvement here is real but currently invisible to the automated a11y gate — worth noting as a testing-coverage gap (the gate only catches what actually renders with the mocked data it's given).
- Did not screenshot before/after — no visual-regression baselines exist yet (#2809 still pending).
