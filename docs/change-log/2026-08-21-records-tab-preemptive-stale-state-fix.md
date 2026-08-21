# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Flagged (not fixed) in `docs/change-log/2026-08-21-support-tab-sidebar-nav-sync-fix.md` §4 as a known-related, not-currently-exploitable risk |

## 1. Issue / gap identified

`records/page.tsx` has the identical `activeTab = useState(initialTab)`
seeded-once pattern that caused the real, user-reported bug on
`support/page.tsx` (sidebar navigation between two `?tab=` values on the
same route not updating the visible tab content). It was flagged but
explicitly not fixed in that earlier change-log because nothing in the app
currently links into Records with more than one `?tab=` value, so it
wasn't exploitable yet.

## 2. Root cause

Same as the `support/page.tsx` fix: `activeTab`'s `useState` initializer
runs once at mount from `searchParams.get("tab")`. Next.js App Router
reuses the existing page component instance for same-route client-side
navigations, so a future external link with a different `?tab=` value
would re-render with new `searchParams` but a stale `activeTab`.

## 3. Fix / remediation

Removed the `useState`; `activeTab` is now derived directly from
`searchParams.get("tab")` (falling back to the first tab the user can
view) on every render — the exact same pattern already applied and
verified on `support/page.tsx`. `useState` import removed as it's now
unused in this file (`useMemo` is still used).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `records/page.tsx`.** Grepped the file —
  `activeTab`/`setActiveTab` are used only at the three sites touched
  here (declaration, `onTabChange`, and the `Tabs value=` prop).
- **Not currently exploitable, confirmed again**: grepped the whole
  `admin-dashboard/src` tree for any other `dashboard/records?tab=` link
  — none exist outside `records/page.tsx` itself and a comment in
  `sidebar.tsx` noting exactly this (the sidebar's own Records entry has
  no query-param children). This PR is pre-emptive, closing the same bug
  class before it can ship broken, not fixing an active regression.
- In-page tab clicks (the `TabsTrigger` buttons) already worked correctly
  before this change (same reasoning as the `support/page.tsx` fix) and
  are unaffected.

## 5. User-experience effect

- **Internal admin only, super-admin-gated page.** No behavior change
  today (no live path exercises the buggy case). Prevents a future
  correct-highlight/wrong-content bug if a `?tab=`-carrying link into
  Records is ever added (e.g. a dashboard shortcut card), without needing
  to remember to re-derive this fix at that time.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/records/page.tsx` | `activeTab` derived from `searchParams` each render instead of `useState` seeded once at mount; removed now-unused `useState` import | Close the same stale-tab bug class already fixed on `support/page.tsx`, before it becomes exploitable |

## 7. Before / after

```tsx
# Before
const requestedTab = searchParams.get("tab");
const initialTab = isValidTab(requestedTab) && canView[requestedTab] ? requestedTab : visibleTabs[0];
const [activeTab, setActiveTab] = useState<TabSlug | undefined>(initialTab);

const onTabChange = (value: string) => {
    if (!isValidTab(value)) return;
    setActiveTab(value);
    router.replace(`/dashboard/records?tab=${value}`, { scroll: false });
};

# After
const requestedTab = searchParams.get("tab");
const activeTab: TabSlug | undefined =
    isValidTab(requestedTab) && canView[requestedTab] ? requestedTab : visibleTabs[0];

const onTabChange = (value: string) => {
    if (!isValidTab(value)) return;
    router.replace(`/dashboard/records?tab=${value}`, { scroll: false });
};
```

## 8. Rollback plan

`git-revert-safe` — single-file, no data/API/schema change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded, `/dashboard/records` compiled clean.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [ ] **Not manually click-tested in a real browser** — same sandbox limitation noted in the `support/page.tsx` fix (Playwright's pinned browser build mismatch). This change mirrors an already browser-repro-verified pattern from that fix, and there is no live path today that exercises the fixed case (see §4), so a live click-through would only re-confirm no regression on the always-worked in-page-click path — reasoned about via the unchanged `onTabChange`/`Tabs` wiring rather than screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — including the "not currently exploitable" reconfirmation via a fresh repo-wide grep.
- [x] No silent behavior change — this is a pre-emptive fix with no live-reachable behavior difference today; the UX-effect section says so explicitly.
