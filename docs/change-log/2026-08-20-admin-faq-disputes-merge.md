# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Findings A (completion) and B, `Admin IA Audit` — user-approved full merge |

## 1. Issue / gap identified

FAQs and Disputes each had two independently hand-rolled implementations
over the same API: a standalone page (`/dashboard/faqs`, `/dashboard/disputes`)
and a separate "condensed" tab component inside Support & Issues
(`support/_tabs/faqs.tsx`, `support/_tabs/disputes.tsx`). Both pairs were
previously left alone in this session (see
`docs/change-log/2026-08-20-admin-support-faq-orphan-and-nav-depth.md`)
because they're covered by a documented "light-touch fix per product
decision: point here, don't merge/remove" comment. That decision was
escalated back to the user, who approved the full merge — see this
session's conversation. Proceeding on explicit approval, not a unilateral
re-decision.

## 2. Root cause

Two UIs for one dataset, built independently over time, each maintained
separately (e.g. C23's Chargebacks sub-feature was added only to the
standalone Disputes page, not the Support tab — the exact kind of drift
duplication causes).

## 3. Fix / remediation

- `support/_tabs/faqs.tsx` and `support/_tabs/disputes.tsx` are now thin
  wrappers importing and rendering the standalone pages' own default
  exports (`FaqsPage` from `../../faqs/page`, `DisputesPage` from
  `../../disputes/page`) — the exact pattern `records/page.tsx` already
  established for Data Transfer/Compliance/Bulk Operations/Export
  Approvals. One implementation each, reused as-is; the "Open in full"
  pointer banners are gone because there's no longer a "condensed" view to
  point away from.
- `next.config.ts`: added `/dashboard/faqs → /dashboard/support?tab=faqs`
  and `/dashboard/disputes → /dashboard/support?tab=disputes` redirects
  (307, `permanent: false`), matching the 4 existing Records redirects
  exactly — nothing bookmarked, linked from an old audit-log entry, or
  hardcoded in a support runbook 404s.
- `sidebar.tsx`: removed the two now-redundant top-level entries
  ("Disputes & Refunds", "FAQs") and added them as nav children under
  Support & Issues (alongside the other 5 sub-views), in the same tab
  order used by `support/page.tsx`'s `TAB_ORDER`.
- Updated 3 stale code comments describing the old "don't merge" state
  (`support/page.tsx` header, `sidebar.tsx`'s children-rationale comment,
  `faq-categories.ts`'s "two editors" note) so they describe the current
  architecture instead of a decision that no longer applies.

## 4. Risk & impact on existing functionality

- **Blast radius:** grepped the whole repo for every reference to
  `/dashboard/faqs` and `/dashboard/disputes` before changing anything.
  Real (non-build-artifact) hits: `sidebar.tsx` (updated), the two tab
  files' own "Open in full" `Link`s (removed along with the banners),
  `faq-categories.ts`'s comment (updated), and 4 e2e spec files
  (`disputes.spec.ts`, `support-tickets.spec.ts`, `ride-management.spec.ts`,
  `crawl-audit.spec.ts`) that `page.goto()` the old URLs directly — none
  assert on the literal post-navigation URL (checked), so the new 307
  redirect (which Playwright follows transparently, same as already proven
  for the 4 Records redirects) doesn't break them. No edits needed to
  those spec files.
- `disputes/page.tsx` and `faqs/page.tsx` themselves are **byte-for-byte
  unchanged** — same components, same API calls, same permission checks.
  `disputes/page.tsx`'s own `useRequireModule("support")` is now redundant
  when nested (the outer Support & Issues page already gates on the same
  module), but harmless — identical check, can't diverge, and
  `faqs/page.tsx` has no permission hook of its own to conflict with
  anything.
- `pages.smoke.test.tsx`'s existing `/dashboard/disputes` render-without-
  crashing test imports `disputes/page.tsx` directly (unaffected — that
  file didn't change) — still passes.
- `e2e/a11y-baseline.json`'s `/dashboard/disputes` (0) and `/dashboard/faqs`
  (0) entries are per-route ceilings keyed on the requested path, not the
  post-redirect one; since the component rendered there is unchanged, the
  violation count shouldn't move — not independently re-verified (see §9).

## 5. User-experience effect

- **Internal admin only, and explicitly approved before implementing.**
- Any admin using the "condensed" Support-tab FAQ/Dispute editor now sees
  the full-featured version instead (more columns, audience labels,
  Chargebacks sub-tab for Disputes) — a **strict superset**, not a feature
  removal.
- Old bookmarks/links to `/dashboard/faqs` or `/dashboard/disputes` keep
  working (redirect), just landing on the same content one level deeper in
  the URL.
- Sidebar: "Disputes & Refunds" and "FAQs" move from top-level items to
  children under "Support & Issues" — a nav-depth change, not a
  functionality change; both remain one click away, now grouped with their
  6 sibling sub-views instead of floating separately in the group.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support/_tabs/faqs.tsx` | Replaced 190-line duplicate implementation with a 3-line wrapper around `FaqsPage` | Full merge, approved |
| `admin-dashboard/src/app/dashboard/support/_tabs/disputes.tsx` | Replaced ~210-line duplicate implementation with a 3-line wrapper around `DisputesPage` | Full merge, approved |
| `admin-dashboard/next.config.ts` | +2 redirects (`/dashboard/faqs`, `/dashboard/disputes` → `/dashboard/support?tab=...`) | Keep old URLs alive, Records precedent |
| `admin-dashboard/src/components/sidebar.tsx` | Removed 2 top-level entries; added them as Support & Issues children; updated 2 stale comments | Complete the nav consolidation |
| `admin-dashboard/src/app/dashboard/support/page.tsx` | Updated header comment only (no logic change) | Comment no longer described current state |
| `admin-dashboard/src/lib/faq-categories.ts` | Updated header comment only (no logic change) | Same |

## 7. Before / after

```
# Before (support/_tabs/faqs.tsx) — 190 lines, own getFaqs/createFaq/... calls,
# own table/dialog JSX, "Open in full" banner pointing at /dashboard/faqs

# After
import FaqsPage from "../../faqs/page";
export default function FaqsTab() {
    return <FaqsPage />;
}
```

```
# Before (sidebar.tsx, Support group)
{ href: "/dashboard/disputes", label: "Disputes & Refunds", icon: Shield, module: "support" },
{ href: "/dashboard/faqs", label: "FAQs", icon: HelpCircle, module: "support" },

# After — moved into Support & Issues' children array:
{ href: "/dashboard/support?tab=disputes", label: "Disputes & Refunds", icon: HelpCircle, module: "support" },
...
{ href: "/dashboard/support?tab=faqs", label: "FAQs", icon: BookOpen, module: "support" },
```

## 8. Rollback plan

`git-revert-safe` — pure frontend consolidation, no migration, no data
touched, no API contract change. A revert restores the two standalone
implementations, the two top-level nav entries, and removes the 2
redirects in one commit.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded, all 73 routes
      built clean, including `/dashboard/support`, `/dashboard/faqs`, and
      `/dashboard/disputes`.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` — 0 errors; 331 pre-existing warnings, none new
      (compared against the pre-change baseline before this commit).
- [x] Blast-radius grep performed for every reference to the two old URLs
      across the whole repo (not just admin-dashboard) — listed in §4.
- [ ] **E2E not executed** — same pre-existing sandbox/Playwright browser
      version mismatch noted in the prior change-log
      (`chromium_headless_shell-1194` installed vs. `-1234` pinned).
      `disputes.spec.ts`, the FAQ section of `support-tickets.spec.ts`,
      `ride-management.spec.ts`, and `crawl-audit.spec.ts` were reviewed by
      inspection (no URL-literal assertions that a 307 redirect would
      break, confirmed the same redirect-follow behavior already proven
      for the 4 Records routes) but not run. Should be verified in CI.
- [ ] **a11y baseline not re-measured** — reasoned that `/dashboard/faqs`
      and `/dashboard/disputes`' axe violation counts shouldn't change
      (the rendered component is byte-identical to before), but this is
      inference, not a measurement — stated explicitly rather than assumed.
- [x] Not feature-flagged — justified above (#8); explicit user approval
      obtained before implementing per the escalation in the prior
      change-log.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change — UX effect section filled in; both
      unverified items (e2e, a11y re-measurement) stated, not silently
      assumed clean.
