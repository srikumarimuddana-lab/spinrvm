# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

#2816 found 91 admin-dashboard files use raw, hardcoded Tailwind color utilities (`text-gray-900`, `bg-red-500`, etc.) instead of the semantic theme tokens established in `globals.css` since Phase 0 (#2786). It named `/dashboard/staff` as the worst confirmed offender: the page heading (`text-gray-900`) measured **1.12:1 contrast** against the dark theme's page background — effectively invisible, since `text-gray-900` is a light-mode-only near-black with no dark-mode awareness at all. The page also used a completely off-brand red (`bg-red-500`/`hover:bg-red-600`, `#fb2c36`) for its primary CTA buttons, unrelated to either the old Tailwind default or the Spinr brand token (`--primary`, `#d32f2f`) from #2786.

## 2. Root cause

Pre-dates the theme-token system entirely — this page was written against raw Tailwind defaults and never migrated when `globals.css` gained semantic tokens in Phase 0.

## 3. Fix / remediation

Ported `/dashboard/staff` (the issue's own suggested "prove out the approach" first fix) onto the existing semantic tokens:

- Neutral grays (`text-gray-900/800/700/600/500/400/300`, `bg-gray-100/50`, `border-gray-200/100`, `bg-white`) → `text-foreground`/`text-muted-foreground`, `bg-muted`, `border-border`, `bg-card` respectively.
- Off-brand red CTAs (`bg-red-500`/`hover:bg-red-600`, `border-red-500`, `accent-red-500`, `focus:ring-red-200`) → `bg-primary`/`hover:bg-primary/90`, `border-primary`, `accent-primary`, `focus:ring-ring/40` — the actual Spinr brand token.

**Deliberately left untouched**, per #2816's own guidance to distinguish "hardcoded but fine" from "hardcoded and broken" rather than blind-replacing: the `ROLE_COLORS` badge constant (`bg-red-100 text-red-700` etc. — categorical role coloring, needs its own per-pair verification like Phase 2's badge work), the module-chip "selected" pastel background (`bg-red-50 border-red-200`), status icon colors (`text-yellow-500`, `text-green-500`, `text-orange-500`, `text-red-400`), and the two `AlertDialogAction` destructive-confirm buttons (`bg-red-600 hover:bg-red-700`/`bg-orange-600 hover:bg-orange-700`) — the latter matches an identical pattern already used elsewhere in the app (e.g. `support/_tabs/lost-and-found.tsx`), so fixing it here alone would be inconsistent with the rest of the app rather than a real improvement; it's part of #2816's wider 91-file scope, not this file alone.

### A real bug caught by live verification, not by lint/build alone

Two of my token substitutions **introduced a new WCAG failure** that only a live axe run against real content caught (mirroring Phase 2's own methodology of not trusting token math in isolation):

- `text-primary` on a `bg-primary/10` selected-chip background computed to **4.28:1** in light mode (below the 4.5:1 AA threshold for normal text) — verified by hand-deriving the WCAG relative-luminance formula for the blended color, not assumed. Fixed by using `text-foreground` (already a proven-safe pairing against `bg-card`/`bg-muted`) instead of relying on colored text over a light tint.
- `text-muted-foreground` on `bg-muted` measured **4.39:1** in light mode (again just under 4.5:1) across three elements I introduced (the "Cancel" button, the avatar-initials circle, and 21 unselected module-checkbox chips) — this only reproduces with **real staff data + the form actually open**, which the CI a11y gate's empty-mocked crawl never exercises. Caught via a temporary local Playwright+axe diagnostic (mocked real staff data, opened the form, ran `color-contrast` in both themes), fixed by using `text-foreground` for these specific non-interactive/already-selected contexts instead.

### A second, unrelated finding surfaced along the way

The same live check also surfaced that `sidebar.tsx`'s nav links (`text-sidebar-foreground/60`/`/50`) fail `color-contrast` in **light mode** — a pre-existing issue, not something this diff touches or caused (confirmed: none of the failing nodes are in `staff/page.tsx` after my fixes; all are in the shared sidebar). Phase 2 (#2817) only verified the dark-mode sidebar fix. Filed as a new backlog issue, #2846, rather than expanding this PR's scope to a shared component.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to `/dashboard/staff` — no shared component touched, no prop/behavior change, purely `className` swaps on one page.
- No interaction, data flow, or API call changed — verified by reading the full diff: every edit is a class-string substitution, nothing else.
- The two existing test suites for this page (`e2e/staff.spec.ts` interaction tests, and `e2e/crawl-audit.spec.ts`'s baseline for `/dashboard/staff`, currently 2 violations) are unaffected in their assertions (they check text content and element visibility, not exact class names).

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/staff`, super-admin-only route). The page's dark-mode rendering changes from largely-invisible (1.12:1 heading) to fully legible; the primary CTA color and focus rings switch from an off-brand red to the actual Spinr brand red used everywhere else in the app.
- Not visible mid-session to riders/drivers/corporate admins.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | Replaced hardcoded neutral-gray and off-brand-red Tailwind classes with semantic theme tokens (`text-foreground`, `text-muted-foreground`, `bg-muted`, `bg-card`, `border-border`, `bg-primary`/`text-primary-foreground`, `border-primary`, `accent-primary`, `focus:ring-ring/40`) | Fixes the 1.12:1 dark-mode contrast failure and off-brand red CTA named in #2816; role/status badge colors deliberately deferred |

## 7. Before / after

```
# Before
<h1 className="text-2xl font-bold text-gray-900">Staff Management</h1>
<button className="... bg-red-500 text-white ... hover:bg-red-600 ...">
  Add Staff
</button>
```

```
# After
<h1 className="text-2xl font-bold text-foreground">Staff Management</h1>
<button className="... bg-primary text-primary-foreground ... hover:bg-primary/90 ...">
  Add Staff
</button>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/migration touched, no other file depends on this page's specific classes.

## 9. Verification performed

- [x] `npm run build` — clean, all 34 routes compile.
- [x] `npm run lint` — 0 new warnings from the changed file (the one pre-existing warning at `staff/page.tsx:86` — `loadStaff` used-before-declared — predates this diff and is untouched by it).
- [x] **Live axe verification against real content, in both themes** — the actual methodology that matters here: mocked real staff data (not empty), opened the create-staff form, selected "Custom" to render the module-checkbox grid, ran `@axe-core/playwright`'s `color-contrast` rule against a clean production build (`npm run build` + `npm run start`, no dev-server cache) in both dark (app default) and light theme.
  - Dark theme: 0 violations, both before and after my fixes.
  - Light theme: caught 2 real regressions my own edits introduced (see section 3), fixed both, then re-verified — 0 remaining violations attributable to this file; the only remaining light-mode violations are in the pre-existing, unrelated `sidebar.tsx` (filed as #2846).
- [x] Blast-radius grep: confirmed no other file imports or extends `staff/page.tsx`'s markup/classes.

## What was NOT verified

- Not tested with a real logged-in super-admin session against a live Supabase-backed backend — verified via mocked staff data through the app's own existing `e2e/staff.spec.ts` mock-setup pattern (`setupAdminMocks`), not a live API.
- Did not extend the token migration to the deliberately-deferred elements (`ROLE_COLORS`, status icons, destructive-confirm dialogs) — these remain part of #2816's open 91-file scope, not silently dropped.
- No screenshot/visual diff captured (no visual-regression baselines exist yet for admin-dashboard, #2809) — relied on the live axe computed-contrast check instead, which is a stronger empirical signal than visual inspection for this specific class of bug (contrast ratios), though it wouldn't catch a purely aesthetic/layout regression.
