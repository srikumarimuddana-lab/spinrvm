# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Findings A (partial) and G, `Admin IA Audit` (this session's IA/duplication audit) |

## 1. Issue / gap identified

Two separate gaps on the Support & Issues page (`/dashboard/support`):

- **Finding A (partial):** a third, undocumented FAQ CRUD implementation
  (`FaqsList` nested inside `support/_tabs/tickets.tsx`'s "Tickets" sub-tab)
  duplicated `getFaqs`/`createFaq`/`updateFaq`/`deleteFaq` calls with no
  permission checks, no audience labels, and no `updated_at` column — a
  plain orphaned duplicate with no product-decision cover (unlike the
  documented "FaqsTab" and standalone `/dashboard/faqs` pair, left
  untouched — see §2).
- **Finding G:** Support & Issues' 7 sub-views had no nav representation
  (compare Help Desk's 2 children) and no URL sync — a tab couldn't be
  bookmarked, deep-linked, or highlighted in the sidebar.

## 2. Root cause

**Finding A:** three FAQ UIs were built independently over time; two of the
three (the standalone page and `FaqsTab`) are covered by an explicit,
documented product decision (`support/_tabs/faqs.tsx:110-116`, and the
identical note on `support/_tabs/disputes.tsx:92-97` for Disputes) to
**not** merge them — "light-touch fix per product decision: point here,
don't merge/remove." The third (`FaqsList` in `tickets.tsx`) predates or was
missed by that review; nothing documents a reason to keep it. **This PR
does not touch the two documented screens** — the full-merge question for
FAQs/Disputes was escalated back to the product owner rather than
re-decided here (see the audit artifact's Finding A/B write-up).

**Finding G:** the page's tab strip was a plain `useState` + button row
predating the URL-synced `Tabs`/`useSearchParams` pattern `records/page.tsx`
later established; nobody retrofitted it, and no sidebar children were ever
added for it.

## 3. Fix / remediation

- Removed `FaqsList` and its containing sub-tab switcher from
  `support/_tabs/tickets.tsx`; `TicketsTab` now renders `TicketsList`
  directly (`export default function TicketsTab() { return <TicketsList />; }`).
  Dropped now-unused imports (`getFaqs`/`createFaq`/`updateFaq`/`deleteFaq`,
  `HelpCircle`).
- Converted `support/page.tsx` to the same `Tabs`/`TabsList`/`TabsTrigger`/
  `TabsContent` + `useSearchParams`/`router.replace` pattern as
  `records/page.tsx` — tabs are now real Radix tabs (`role="tab"`,
  `role="tablist"`) and the active tab syncs to `?tab=<slug>` in the URL.
- Added real sidebar nav children under **Support & Issues** for the 5
  sub-views with no other nav entry: Support Tickets, Complaints, Lost &
  Found, Flags, Legal — each linking to `/dashboard/support?tab=<slug>`.
  **Disputes and FAQs deliberately excluded** — both already have their own
  top-level nav entries and are covered by the untouched product decision
  above; giving them a second nav path here would add duplication, not
  remove it.
- Extended `sidebar.tsx`'s active-route check (previously duplicated 3×
  inline as `pathname === href || pathname.startsWith(href)`) into one
  shared `isActiveHref()` helper that also handles `?tab=` query-param
  children by comparing `useSearchParams().get("tab")` — existing plain-path
  children (Drivers' 5, Help Desk's 2) go through the same unchanged
  `pathname`-only branch, so their behavior is byte-for-byte identical.
  `Sidebar` is now wrapped in `Suspense` (required by `useSearchParams` in
  the App Router); `SidebarInner` holds the actual component.
- Updated `e2e/support.spec.ts`'s 6 tab-switch tests: `getByRole('button', ...)`
  → `getByRole('tab', ...)` (Radix Tabs' real ARIA role) and removed the
  now-obsolete DOM-scoping workaround that existed only to disambiguate
  against the nested FAQ sub-tab this PR deletes.

## 4. Risk & impact on existing functionality

- **Blast radius: `sidebar.tsx`'s active-check change is shared by every nav
  item in the app** (6 groups, ~30 items, several with existing plain-path
  children). Verified the new `isActiveHref()` helper's no-query branch is
  textually identical to the 3 inline checks it replaces — existing
  Drivers/Help Desk/every other child and parent behave exactly as before;
  only hrefs containing `?` take the new branch, and only Support & Issues'
  new children use that shape today.
- `support/page.tsx`'s tab content itself (`TicketsTab`, `DisputesTab`,
  `ComplaintsTab`, `LostAndFoundTab`, `FlagsTab`, `FaqsTab`,
  `LegalDocumentsTab`) is unchanged — same `next/dynamic` lazy imports, same
  components, same `useRequireModule("support")` page-level gate. Only the
  tab-strip chrome around them changed.
- Grepped for other consumers of the deleted `FaqsList` component and its
  sub-tab state — it was a local, unexported function only ever rendered by
  `TicketsTab` in the same file; no other importer.
- `e2e/a11y-baseline.json`'s `/dashboard/support` entry (1 known violation)
  is a `toBeLessThanOrEqual` ceiling in `crawl-audit.spec.ts`, not an exact
  match — Radix Tabs' real `tablist`/`tab` roles should meet-or-improve on
  the prior custom button row, so no update needed there.

## 5. User-experience effect

- **Internal admin only.**
- The removed nested FAQ sub-tab (inside "Support Tickets") disappears —
  any admin who was using it now uses the page's own top-level "FAQs" tab
  instead (same data, same API, already one click away on the same page).
- Support & Issues' tabs are now bookmarkable/deep-linkable
  (`/dashboard/support?tab=complaints`) and 5 of its 7 sub-views gained a
  sidebar entry — a **visible, additive** navigation change for any admin
  with the `support` module. Not a mid-session disruption: nothing here
  fires while a tab is actively in use, only changes how it's reached.
- Disputes/FAQs' documented "point to the full page" banners are unchanged
  — the light-touch decision from the prior review still stands.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` | Removed `FaqsList` + its sub-tab switcher; `TicketsTab` renders `TicketsList` directly; dropped unused imports | Remove the one FAQ duplicate with no product-decision cover |
| `admin-dashboard/src/app/dashboard/support/page.tsx` | Rewrote as Radix `Tabs` + `useSearchParams`/`router.replace`, matching `records/page.tsx` | URL-synced, deep-linkable, real tablist semantics |
| `admin-dashboard/src/components/sidebar.tsx` | Added 5 nav children under Support & Issues; added shared `isActiveHref()` helper handling `?tab=`; wrapped `Sidebar` in `Suspense` | Real nav depth for sub-views with no other entry point |
| `admin-dashboard/e2e/support.spec.ts` | 6 tab-switch tests: `role: 'button'` → `role: 'tab'`; removed obsolete DOM-scoping workaround | Match the new Radix Tabs DOM; the collision it worked around no longer exists |

## 7. Before / after

```
# Before (support/_tabs/tickets.tsx)
export default function TicketsTab() {
    const [sub, setSub] = useState<"tickets" | "faqs">("tickets");
    return (
        <div className="space-y-4">
            <div className="flex gap-1 border-b -mt-1">{/* Tickets | FAQs sub-tab buttons */}</div>
            {sub === "tickets" ? <TicketsList /> : <FaqsList />}
        </div>
    );
}

# After
export default function TicketsTab() {
    return <TicketsList />;
}
```

```
# Before (sidebar.tsx, 3 places)
const active = pathname === item.href ||
    (item.href !== "/dashboard" && pathname.startsWith(item.href));

# After (one shared helper, same behavior for plain paths)
const isActiveHref = (href: string): boolean => {
    const [path, query] = href.split("?");
    if (!query) {
        return pathname === href || (href !== "/dashboard" && pathname.startsWith(href));
    }
    if (pathname !== path) return false;
    const wantTab = new URLSearchParams(query).get("tab");
    return wantTab != null && searchParams.get("tab") === wantTab;
};
```

## 8. Rollback plan

`git-revert-safe` — pure frontend nav/UI change, no migration, no data, no
API contract change. Worst case of a bad revert-forward is a broken tab
link or a missing sidebar entry, not data loss. No feature flag: this repo's
`app_settings` flag mechanism targets user-visible product behavior changes
with real business risk (per CLAUDE.md gate 3); a nav/IA cleanup with a
one-command revert doesn't warrant one.

## 9. Verification performed

- [x] Real production build (`npm run build`, not dev server, not `tsc`
      alone) — succeeded, all 73 routes including `/dashboard/support`
      built clean.
- [x] `npx vitest run` — 339/339 passed (includes
      `pages.smoke.test.tsx`'s `/dashboard/support` render-without-crashing
      check).
- [x] Blast-radius grep performed on `sidebar.tsx`'s active-check change
      (every nav item in the app) and on the deleted `FaqsList` component
      (no other importer).
- [x] Reviewed against this session's IA audit and the pre-existing
      documented FAQ/Disputes product decision — did not touch the parts
      that decision covers.
- [ ] **E2E (`e2e/support.spec.ts`) not executed** — attempted via the
      pre-installed Chromium, but this sandbox's Playwright browser build
      (`chromium_headless_shell-1194`) doesn't match the version this
      project's `@playwright/test` pins (`-1234`), a pre-existing
      environment mismatch unrelated to this change. The spec was updated
      by inspection (`role: 'button'` → `role: 'tab'` matches Radix Tabs'
      documented ARIA output) and reviewed, not run — stated explicitly
      rather than assumed passing. Should be verified in CI, which has the
      correct pinned browser.
- [x] Not feature-flagged — justified above (#8).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data touched).
- [x] Blast radius is stated, not assumed — sidebar active-check change and
      deleted-component grep both listed above.
- [x] No silent behavior change — UX effect section filled in; the one
      unverified item (e2e) is stated, not silently assumed clean.
