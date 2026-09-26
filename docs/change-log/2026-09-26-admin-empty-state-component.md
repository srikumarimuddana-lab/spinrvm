# Change Impact & Risk Log — admin shared EmptyState component (W5.3, part b)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (agent session) |
| Surface(s) | admin-dashboard (internal staff only) |
| Domain (Sentry tag) | admin |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x`: the EmptyState component plus two commits moving four pages onto it |
| Related issue or gap ID | UX enhancement program W5.3 (`.claude/plans/2026-09-25-ux-enhancement-program.md`), "Shared `EmptyState` component". Part a (pollers) shipped in #5902. |

## 1. Issue / gap identified

A grep finds about 40 admin files with a hand-written "No … found/yet" message: inline texts, table rows and page-level blocks. The page-level blocks use different icon sizes, icon opacities, padding and heading tags. The same kind of screen looks slightly different from page to page, and each new list copies whichever version its author found first.

## 2. Root cause

There was no shared component, so each page copied markup from a neighbour.

## 3. Fix / remediation

- **New `components/empty-state.tsx`:** `EmptyState({ icon, title, description?, headingLevel? })`.
  - It renders the pattern the pages already shared most often: a 48 px lucide icon at `text-muted-foreground/30`, a `text-lg font-semibold` heading, a muted hint and `py-16`.
  - The title is an `h2` by default. `headingLevel={3}` keeps a page's outline where the list sits under an `h2`, or where a page already used an `h3`.
  - The icon stays decorative; lucide marks it `aria-hidden`.
  - An action slot is deliberately left out until a page that needs one adopts it.
- **Four pages move to it:**

| Page | Result |
|---|---|
| Audit logs | Identical DOM; keeps its filter-aware hint |
| Staff | Identical DOM |
| Promotions | Same text and icon; vertical padding `py-12` → `py-16` (16 px more above and below). This is the one visible change |
| Cloud messaging, scheduled tab | Identical DOM; keeps its `h3` |

**Alternatives considered (gate 10):**
- *Adopt it everywhere in one PR, with `size`/`variant`/`action` props.* Rejected for now. It would change the look of dozens of screens at once, including the Rides and Drivers visual baselines, whose e2e mocks render empty lists and so need a human re-capture. It would also add props before a page needs them.
- *Leave the pages hand-rolled and document the pattern.* Rejected: documentation doesn't stop the next copy drifting.

## 4. Risk & impact on existing functionality

- **Blast radius: admin-dashboard only.** The component is new, and its only importers are the four pages above.
- **Not on a visual baseline.** None of the four pages is among the six in `e2e/visual-regression.spec.ts` (login, home, drivers, monitoring, settings, rides).
- **Behaviour:** nothing interactive changes. It is markup only, with no data, state or network change.
- **Heading outline (pre-existing, unchanged):** on cloud messaging the card title above the list is a styled `div` (`CardTitle`), not a heading. The page's outline therefore goes `h1` → `h3`. That skip is on `main` already and is kept as is; see §10.

## 5. User-experience effect

- **Who sees it:** internal admins.
- The only visible difference is 16 px more space around the promotions empty state. Audit logs, staff and cloud messaging look exactly as before.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/empty-state.tsx` (new) | The component | Shared empty-state block |
| `admin-dashboard/src/components/__tests__/empty-state.test.tsx` (new) | 4 cases | Coverage, including DOM equality with the old block |
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | Uses EmptyState | W5.3 |
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | Uses EmptyState | W5.3 |
| `admin-dashboard/src/app/dashboard/promotions/page.tsx` | Uses EmptyState (`py-12` → `py-16`) | W5.3 |
| `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx` | Uses EmptyState (`headingLevel={3}`) | W5.3 |
| `docs/change-log/2026-09-26-admin-empty-state-component.md` (new) | This log | CLAUDE.md change-impact rule |
| `.claude/plans/2026-09-25-ux-enhancement-program.md` | Progress line | Program tracking |

## 7. Before / after

Before (`staff/page.tsx`):

```tsx
<div className="text-center py-16">
  <Users className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
  <h2 className="text-lg font-semibold">No staff members yet</h2>
  <p className="text-muted-foreground mt-1">Add your first team member to share admin access</p>
</div>
```

After:

```tsx
<EmptyState
  icon={Users}
  title="No staff members yet"
  description="Add your first team member to share admin access"
/>
```

## 8. Rollback plan

No feature flag: the plan lists W5.3 as "Gate: none", and three of the four pages render identically. Revert the commits and redeploy admin-dashboard, or promote the previous Vercel deployment. Nothing is written to live data.

## 9. Verification performed

- **Component tests:** 4 cases:
  - `h2` by default;
  - `h3` with `headingLevel={3}`;
  - the icon is `aria-hidden`;
  - the component's `innerHTML` equals staff's old hand-rolled block.
- **Type check:** `npx tsc --noEmit` exit 0.
- **ESLint:** 0 findings on the component and its test. On the four pages, the same findings as on `main`.
- **Full admin suite:** `npx vitest run`: 109 files and 927 tests passed (the 923 of W5.3a plus these 4). The admin tree was then confirmed identical to `main` after #5902.
- **Production build:** `npm run build` exit 0, 80/80 static pages.
- **Design-consistency review** (`spinr-design-consistency-reviewer`): no blockers, verdict on-brand. It found:
  - token-only styling, consistent with Quiet Console;
  - byte-identical DOM for audit logs and staff;
  - the promotions padding as the only visual change;
  - none of the four pages on a visual baseline.

  Its one warning is the pre-existing cloud-messaging heading skip (§4, §10).

## 10. What was NOT verified, and follow-ups

- **No screenshot** of the promotions padding change. It was reasoned from the class change, and the page has no visual baseline.
- **No screen-reader pass.** The heading semantics were checked by role queries in jsdom.
- **Follow-up, pre-existing:** cloud messaging's outline skips from `h1` to `h3` because `CardTitle` is a `div`. Making the card title a real heading, or the empty state an `h2`, is a separate, deliberate change.
- **Next adopters (part c)**, from the review. None of them is a straight swap:
  - **Service areas:** closest match, but `font-bold` and a full-opacity icon. The inconsistency needs reconciling while adopting.
  - **Vehicle types:** has an "Add" button, so it needs the action slot added then.
  - **Driver notes and driver timeline:** a smaller compact variant, which would need a size option.
  - **Rides list and drivers table:** on visual baselines, so adopting there needs a baseline re-capture `[H]`.
