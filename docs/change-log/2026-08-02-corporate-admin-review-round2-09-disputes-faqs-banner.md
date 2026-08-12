# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "Disputes and FAQs each exist as two live, unreconciled screens" |

## 1. Issue / gap identified

Both Disputes and FAQs exist as two separate, simultaneously-live admin
screens each: a dedicated standalone page (`dashboard/disputes`,
`dashboard/faqs` — linked from the sidebar) and a smaller, condensed tab
embedded inside the Support page (`support/_tabs/disputes.tsx`,
`support/_tabs/faqs.tsx`). An admin landing on the Support page's tab has
no indication a fuller, dedicated screen exists elsewhere.

## 2. Root cause

Confirmed via code read (not just the report's claim): both pairs call
the exact same API functions (`getDisputes`/`resolveDispute`,
`getFaqs`/`createFaq`/`updateFaq`/`deleteFaq`) — there is no data-model
split, only a UI-level duplication where the Support page's tabs are
smaller/older versions that were never reconciled with the later,
fuller dedicated pages the sidebar actually links to.

## 3. Fix / remediation

Per explicit product decision (light-touch, no data migration, full
consolidation deferred): added an `Alert` banner at the top of both
`support/_tabs/disputes.tsx` and `support/_tabs/faqs.tsx` linking to the
corresponding dedicated page ("This is a condensed view. Manage
[disputes/FAQs] in full on the dedicated page → Open [page]"). Both tab
views remain fully functional — nothing removed, no redirect, no data
touched — this only adds a wayfinding cue toward the canonical screen.

## 4. Risk & impact on existing functionality

- **Blast radius: two files, one banner each, no logic changed.** Every
  existing state variable, handler, and API call in both tab components
  is untouched — confirmed by only inserting new JSX before the existing
  return content, not modifying any existing line.
- Grepped every consumer of `DisputesTab`/`FaqsTab`: only
  `dashboard/support/page.tsx`'s tab switch, unchanged in this commit.
- No change to the dedicated standalone pages (`dashboard/disputes`,
  `dashboard/faqs`) — they were already the intended primary screens (the
  ones the sidebar links to) and needed no fix.

## 5. User-experience effect

**Internal admin-facing only.** An admin using the Support page's
Disputes or FAQs tab now sees a clear pointer to the fuller dedicated
screen. No existing functionality removed or degraded — the tabs
continue to work exactly as before for an admin who prefers the
condensed in-context view.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support/_tabs/disputes.tsx` | New banner linking to `/dashboard/disputes` | Point toward the canonical screen |
| `admin-dashboard/src/app/dashboard/support/_tabs/faqs.tsx` | New banner linking to `/dashboard/faqs` | Point toward the canonical screen |

## 7. Before / after

```tsx
// Before
return (
    <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
            ...

// After
return (
    <div className="space-y-4">
        <Alert>
            <ExternalLink className="h-4 w-4" />
            <AlertDescription className="flex items-center justify-between gap-3">
                <span>This is a condensed view. Manage disputes in full on the dedicated page.</span>
                <Link href="/dashboard/disputes" ...>Open Disputes & Refunds</Link>
            </AlertDescription>
        </Alert>
        <div className="flex flex-wrap items-center justify-between gap-3">
            ...
```

## 8. Rollback plan

`git revert` the commit. No migration, no data written — purely additive
JSX in two files.

## 9. Verification performed

- [x] Confirmed both tab/page pairs share the same underlying API
      functions (no data-model divergence) before deciding the fix's
      shape, rather than assuming the report's wording literally.
- [x] Bracket-balance check on both touched `.tsx` files (no TS/JS
      toolchain run, per this round's instruction) — balanced.
- [x] Confirmed the sidebar links to the dedicated pages (`grep` on
      `sidebar.tsx`), establishing which screen is canonical before
      choosing the link target.
- [x] Blast-radius grep performed (see §4): only `support/page.tsx`
      renders these tab components; unchanged.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — confirmed via grep, not guessed
- [x] No silent behavior change to a working flow — every existing
      handler/state/API call in both tabs is byte-for-byte unchanged;
      only new banner JSX was inserted

## What was NOT verified

Did not run `eslint`/`tsc --noEmit`/`vitest` or a production build — per
this round's explicit instruction, deferred to a single pass at the end.
Did not manually click through either tab in a browser — reasoned
through the existing `Alert`/`Link` component usage already proven
working elsewhere in the codebase, rather than screenshotted; no
visual-regression tooling exists in this repo for this surface (a
standing, previously-flagged gap). Full consolidation (picking one screen,
migrating any tab-only behavior, removing the other) was explicitly
deferred per product decision and is not attempted here.
