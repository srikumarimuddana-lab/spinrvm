# Change Impact & Risk Log: admin tables announce sort state; search inputs get accessible names

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | UX program W1.2 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | clean-sheet F-51; `docs/audit/2026-09-25-ux-scorecard-world-class-minimal.md` gap 5 |

## 1. Issue / gap identified

- **Sort state:** sortable admin tables showed their current sort only with a chevron icon, so screen readers couldn't tell which column was sorted or in which direction.
- **Search inputs:** 15 admin search inputs were identified only by placeholder text, which is not a reliable accessible name. The earlier audit counted "14 of 23"; a whole-element scan found 15 of 25.

## 2. Root cause

`SortableHead` never set `aria-sort`. Search inputs were written with a placeholder and a search icon but no label.

## 3. Fix / remediation

- **Sort state:** `SortableHead` sets `aria-sort` on its header cell. The active column gets `"ascending"` or `"descending"`; the others get `"none"`.
- **Search inputs:**
  - 15 inputs get an `aria-label` naming what they search, for example "Search rides" or "Search audit logs".
  - The company-portal address field uses its existing visible label text ("Pickup" or "Drop-off") through `aria-label={label}`, because its `<label>` wasn't associated with the input.

**Not in this change:** sticky table headers. The admin tables sit inside horizontal-scroll containers, and those stop `position: sticky` from following the page scroll. That needs its own table-layout change and has been moved in the plan.

## 4. Risk & impact on existing functionality

- **Blast radius:** `SortableHead` is shared by about 39 admin tables, and the labels touch 15 files.
- **Nothing visible changes:** every edit adds an ARIA attribute. No class, style or markup structure changed.
- **Visual baselines unaffected:** the 6 baselined pages (`login`, `dashboard-home`, `-drivers`, `-monitoring`, `-settings`, `-rides`) include the rides list, whose search input gained an `aria-label`. That attribute has no visual effect.
- **Tests:** tests that find inputs by placeholder are unaffected. The accessible name only changes for queries by role and name, and the full admin suite passes.

## 5. User-experience effect

- **Admins using a screen reader:** sortable column headers now announce their sort state, and search fields announce what they search.
- **Everyone else:** no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/ui/sortable-table.tsx` | `aria-sort` on the header cell | Announce the sort state |
| `admin-dashboard/src/components/ui/sortable-table.test.tsx` | New: 4 tests | none / ascending / descending, plus that the header still sorts |
| 15 files with search inputs[^files] | `aria-label` on the search input | Accessible name |

[^files]: `data-transfer/EntitySearchTable.tsx`, `support-tickets/tickets/page.tsx`, `audit-logs/page.tsx`, `safety/page.tsx`, `drivers/_components/driver-rides-tab.tsx`, `promotions/page.tsx`, `corporate-accounts/page.tsx`, `rides/_components/ride-list.tsx`, `support/_tabs/{lost-and-found,complaints,tickets,flags}.tsx`, `faqs/page.tsx`, `cloud-messaging/page.tsx`, `company-portal/[id]/book/page.tsx`.

## 7. Before / after

```tsx
// Before
<TableHead className={...}>
<Input placeholder="Search..." ... />
```

```tsx
// After
<TableHead aria-sort={ariaSort} className={...}>
<Input aria-label="Search flags" placeholder="Search..." ... />
```

## 8. Rollback plan

**No feature flag.** The change is ARIA-only with no visual or behavioural effect.

**Rollback:** `git revert`; the admin dashboard redeploys through Vercel.

## 9. Verification performed

- [x] **New tests:** 4 in `sortable-table.test.tsx`. Three fail without the change and all four pass with it.
- [x] **Full admin suite:** 85 files, 755 tests pass.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Production build:** `npm run build` (`next build`) succeeds.
- [x] **Lint:** ESLint on the touched files matches before exactly: 0 errors and 46 warnings, all pre-existing.
- [ ] **Accessibility review:** `spinr-accessibility-reviewer` is running. The outcome will be recorded before merge.

## 10. What was NOT verified

- **No screen-reader run:** no real screen reader (NVDA, VoiceOver) was used. The announcements were reasoned about from the ARIA spec.
- **Visual baselines:** the visual-regression job on the 6 baselined pages is expected to pass unchanged, because no visible markup changed. CI will confirm.
