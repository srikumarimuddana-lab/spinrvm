# Change Impact & Risk Log — Error-vs-empty states, dark mode, responsive

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | Operational Analytics review, findings P2-error, P3-dark, P3-responsive |

## 1. Issue / gap identified

1. **A failed request rendered as an empty period.** Each fetch used
   `.catch(() => null)`, and the charts then showed "No data for selected
   period" / "No cancellation data". On a page whose job is spotting bad
   news, "0 cancellations" reads as good news when the truth was "the
   backend is down". One global `fetchError` banner sat above four charts
   that each contradicted it.
2. **Dark mode was broken on this page.** The dashboard runs `next-themes`
   with `attribute="class"` and a `.dark` block in `globals.css`;
   `earnings/page.tsx` carries 30 `dark:` variants. This page had **zero**,
   with hardcoded `bg-red-50 text-red-700`, `text-green-600`, `text-red-600`.
3. **The acceptance summary row was `grid grid-cols-3` with no breakpoint** —
   three cramped cards on mobile, while the KPI row above correctly used
   `grid-cols-2 md:grid-cols-4`.
4. **The pie chart had no legend** (every other chart on the page does) and
   labelled every slice, which collides once several small reasons appear.

## 2. Root cause

(1) `.catch(() => null)` collapses two distinct outcomes — "request failed"
and "server returned nothing" — into one value, after which the render layer
cannot tell them apart. (2)–(4) are omissions from before the dashboard grew
a theme system and before the page grew this many charts.

## 3. Fix / remediation

- Replaced the single `fetchError` flag with per-section state
  (`overviewError`, `cancelError`, `driverError`); the global banner is now
  derived from them, so it keeps working unchanged.
- Added a `SectionError` component rendered *in place of* each chart whose
  request failed, worded to make the distinction explicit: "it isn't zero,
  it's unknown." Empty states were reworded to state the fact positively
  ("No cancellations in this period").
- The driver table's empty row now renders in red with an error message when
  the request failed, rather than muted grey.
- KPI cards are hidden only when data is genuinely absent, never as a way of
  hiding a failure.
- 13 `dark:` variants added across the banner, KPI values, and the
  low-performer card.
- `REASON_COLORS` became `reasonColors(isDark)`, drawing from the same
  validated categorical palette as the rest of Analytics — the four real
  reasons take palette slots, the three residual buckets are greys (the
  "Other" treatment rather than inventing three more hues), and dark mode
  uses the palette's selected deeper steps.
- Summary row → `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`.
- Pie gained a `Legend`, dropped `labelLine`, and now labels only slices ≥8%;
  the reason table beneath carries exact values.

## 4. Risk & impact on existing functionality

**Blast radius: one file plus one import.** Only
`admin-dashboard/src/app/dashboard/analytics/page.tsx` changed, plus an
import of the existing `chart-palette.ts`. No shared component modified, no
backend change, no API contract change, no migration.

The `fetchError` name is retained as a derived value, so the existing global
banner and its retry button behave exactly as before — the change is purely
additive underneath it.

`useTheme()` from `next-themes` is newly called on this page. The provider
already wraps the app (`layout.tsx`), and `resolvedTheme` is `undefined` on
the first server render; `reasonColors(undefined === "dark")` evaluates to
the light map, which is the correct pre-hydration default and matches what
the page rendered before.

**Risk accepted:** the reason-colour change alters the pie/table colours
slightly in light mode (`no_drivers_available` and `other`/`scheduled`
greys shift by a step). Cosmetic, and it brings the page onto the one
validated palette rather than a second hardcoded set.

## 5. User-experience effect

**Internal admin only.** Nothing rider-, driver-, or corporate-facing.

Admins will see: a genuine error message where a chart previously claimed
zero; a page that works in dark mode; a summary row that stacks on mobile;
and a legible pie with a legend. The most consequential is the first — an
operator can no longer read a backend outage as a quiet day.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | Per-section error state + `SectionError`; 13 `dark:` variants; theme-aware reason colours; responsive summary grid; pie legend + selective labels | Stop a failure reading as zero; make the page work in dark mode and on mobile |

## 7. Before / after

```tsx
// Before — a 503 and an empty period render identically
{overview?.daily_chart && overview.daily_chart.length > 0 ? (
  <BarChart … />
) : (
  <p>No data for selected period</p>   // ← also shown when the request failed
)}
```

```tsx
// After — "we don't know" and "there were none" are different answers
{overviewError ? (
  <SectionError onRetry={fetchCore} />          // "it isn't zero, it's unknown"
) : overview?.daily_chart?.length > 0 ? (
  <BarChart … />
) : (
  <p>No data for selected period</p>
)}
```

## 8. Rollback plan

`git revert` is sufficient and complete. Frontend-only, single file, no
migration, no schema change, no write path, no persisted state, no live data.

No feature flag: the change is internal-admin-only and read-only, and
flagging it would mean keeping the misleading empty state selectable.

## 9. Verification performed

- [x] **Real production build run** — `npm run build`, exit 0, full route table emitted. `tsc --noEmit` exit 0 after each step.
- [x] `npm run lint` — **0 errors** (335 warnings, all the pre-existing repo-wide `react-hooks` class; the count rose from 327 because this branch added files following the same established data-fetching-in-effect pattern used by every other page here).
- [x] Temporal-dead-zone check — confirmed `REASON_COLORS` (line 138) is declared before `pieData` (line 219) reads it.
- [x] Verified the page went from **0 to 13** `dark:` variants.
- [x] Blast-radius grep — no shared component touched; `chart-palette.ts` is imported, not modified.

## 10. What was NOT verified

- **Nothing was rendered, in either theme.** This is a change *about* how the page looks in failure and in dark mode, verified only by type-check and build. That is a weak form of evidence for this particular diff — weaker than for the backend work — and a manual pass in both themes is required before merge.
- **The error states were never triggered.** `SectionError` has not been seen; no request was made to fail. The wiring is verified by reading the ternaries, not by observing a 503.
- **No visual/snapshot regression tooling exists for admin-dashboard** (standing gap, re-confirmed).
- **Responsive behaviour was reasoned from Tailwind breakpoints, not measured** at real viewport sizes.
- The pie's 8% label threshold was chosen as a reasonable cutoff, not tuned against real cancellation distributions — with many mid-sized reasons labels could still crowd.
- No accessibility audit beyond keeping identity off colour-alone (legend added, error text in words). Contrast of the new dark variants against the dark surface was not measured with a checker.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] The visible change (errors no longer reading as zero) documented in §5
- [ ] **Open gate: manual render pass in light AND dark, including a forced failure, before merge** (see §10)
