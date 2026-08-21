# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Regression in Finding G (nav-depth pass, PR #4304), reported by the user with a screenshot |

## 1. Issue / gap identified

Clicking a Support & Issues sidebar child (Complaints, Lost & Found, Flags,
FAQs, Legal, etc.) highlighted the correct item in the sidebar but did not
change the tab content shown on the page — the previously-active tab stayed
visible. Screenshot: sidebar shows "Complaints" highlighted, page content
still shows the "Legal" tab.

## 2. Root cause

`support/page.tsx`'s `activeTab` was `useState<TabSlug>(initialTab)`,
seeded from `searchParams.get("tab")` **once, at mount**. The sidebar's
Support & Issues children are all links to the same route
(`/dashboard/support?tab=<slug>`) with different query strings. Next.js App
Router reuses the existing page component instance for same-route
client-side navigations rather than remounting it, so `searchParams`
updates and `sidebar.tsx`'s `isActiveHref()` (which reads `searchParams`
live on every render) correctly re-highlights the new child — but
`activeTab`'s `useState` initializer never re-runs, so the `Tabs` component
kept rendering whichever tab was active on first page load. First
introduced in the Finding G nav-depth work (PR #4304); not caught then
because that PR's manual testing was reasoned about from the code, not
screenshotted (no visual-regression tooling in this repo, stated explicitly
in that PR) — this exact click-through interaction needed a live click to
surface.

## 3. Fix / remediation

Removed the `useState` entirely. `activeTab` is now derived directly from
`searchParams.get("tab")` on every render — no seed-once state to go
stale. `onTabChange` (fired when a user clicks a `TabsTrigger` inside the
page itself) still calls `router.replace()`, which updates the URL and
triggers a re-render with the new `searchParams`, so the derived
`activeTab` picks it up automatically. Removes the bug class rather than
patching it with an effect.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `support/page.tsx`.** No prop/type signature
  changed; `TAB_META`, `TAB_ORDER`, `isValidTab`, and every `_tabs/*.tsx`
  component are untouched.
- **Known related risk, not fixed here:** `records/page.tsx` has the
  identical `useState(initialTab)` pattern. It isn't currently exploitable
  — nothing external links into Records with a different `?tab=` value
  (its one sidebar entry has no query-param children) — but the same bug
  would appear if that ever changes. Flagged, not fixed, since it isn't
  broken today and fixing unrequested code is out of scope for this PR.
- In-page tab clicks (the `TabsTrigger` buttons themselves, not sidebar
  children) already worked correctly before this fix and are unaffected —
  verified the click handler path is unchanged.

## 5. User-experience effect

- **Internal admin only.** Before this fix, any admin using the sidebar to
  jump between Support & Issues sub-views after the page was already
  loaded got a confusing, silently-wrong screen (correct highlight, wrong
  content) — exactly the kind of "no silent behavior change to a
  live-tested flow" case CLAUDE.md's release gates call out. This fix
  makes the page behave as Finding G intended: sidebar clicks now
  correctly switch the visible tab.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support/page.tsx` | `activeTab` derived from `searchParams` each render instead of `useState` seeded once at mount | Fix stale-tab bug on sidebar navigation |

## 7. Before / after

```
# Before
const requestedTab = searchParams.get("tab");
const initialTab = isValidTab(requestedTab) ? requestedTab : "tickets";
const [activeTab, setActiveTab] = useState<TabSlug>(initialTab);

const onTabChange = (value: string) => {
    if (!isValidTab(value)) return;
    setActiveTab(value);
    router.replace(`/dashboard/support?tab=${value}`, { scroll: false });
};

# After
const requestedTab = searchParams.get("tab");
const activeTab: TabSlug = isValidTab(requestedTab) ? requestedTab : "tickets";

const onTabChange = (value: string) => {
    if (!isValidTab(value)) return;
    router.replace(`/dashboard/support?tab=${value}`, { scroll: false });
};
```

## 8. Rollback plan

`git-revert-safe` — single-file, no data/API/schema change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded, `/dashboard/support` compiled clean.
- [x] `npx vitest run` — 339/339 passed, including `pages.smoke.test.tsx`'s `/dashboard/support` render check.
- [x] Root-caused against the actual reported screenshot (sidebar highlight vs. stale tab content) rather than guessing — traced through `sidebar.tsx`'s live `searchParams` read vs. the page's seed-once `useState` to confirm the exact mechanism before writing the fix.
- [ ] **Not manually click-tested in a real browser** — this sandbox has no way to interact with a live page (Playwright's pinned browser build mismatch, noted in prior change-logs). The fix is a well-understood React pattern (derive-from-props/searchParams instead of state-seeded-once) and the build/type-check confirm no regressions, but the actual click-through wasn't re-verified live. Stated explicitly rather than assumed fixed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — including the related-but-unfixed `records/page.tsx` risk.
- [x] No silent behavior change — this fixes an unintended one; UX effect section states what was broken and what's fixed.
