# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Finding C, `Admin IA Audit` (this session's IA/duplication audit) |

## 1. Issue / gap identified

Two unrelated ticketing systems both surfaced under the plain label "Tickets"
one nav group apart in the admin sidebar — Support & Issues' in-house ticket
tab (`getTickets`/`replyToTicket`) and Help Desk's Zoho Desk integration
(`getDeskTickets`) — with no qualifier distinguishing which is which from the
nav alone.

## 2. Root cause

Two separately-built ticketing surfaces were each named generically
("Tickets") at the time they were built, before the second one existed. A
prior review (see comment at `support/_tabs/tickets.tsx:124-132`) already
identified these as "two parallel, non-integrated ticketing systems" with
genuinely separate backends — full consolidation was correctly scoped out as
a real migration — and added an in-page banner pointing to Zoho as primary,
but the nav-level label collision itself was never fixed.

## 3. Fix / remediation

Renamed the labels only — no data, routing, or component logic touched:

- Sidebar child nav item under **Help Desk**: "Tickets" → **"Zoho Tickets"**.
- **Support & Issues** page's internal tab: "Tickets" → **"Support Tickets"**.
- Zoho ticket list page's `<h1>` heading: "Tickets" → **"Zoho Tickets"** (kept
  in sync with its own renamed nav entry).
- The nested sub-tab's `aria-label` inside the Support Tickets tab (which
  referenced the tab's old name) updated to match: "(within Tickets)" →
  "(within Support Tickets)".

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to display text.** No `id`/route/prop values
  changed — `TABS` still uses `id: "tickets"`, the Zoho route is still
  `/dashboard/support-tickets/tickets`, and no API call, filter, or stored
  preference keys off the label string.
- Grepped the repo for `"Tickets"` / `'Tickets'` / `>Tickets<` outside the 4
  edited files — no test file or other component asserts on this literal
  label text, so no other consumer is affected.
- The nested sub-tab component inside `_tabs/tickets.tsx` (its own internal
  "Tickets"/"FAQs" sub-tab strip) was deliberately left as-is — it's scoped
  inside a parent tab now labeled "Support Tickets", so the surrounding
  context already disambiguates it; renaming it too would have been a
  non-required change to a component already flagged as a separate finding
  (FAQ triplication) in the same audit.

## 5. User-experience effect

- **Internal admin only**, no rider/driver/corporate-admin-facing change.
- Visible immediately to any admin viewing the sidebar or the Support &
  Issues tab strip; not something an admin would have mid-action when it
  changes (it's a nav label, not in-flight state), so no mid-session
  disruption risk.
- No new confirmation, validation, or notification copy — pure label text.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/sidebar.tsx` | Help Desk child label `"Tickets"` → `"Zoho Tickets"` | Disambiguate from the in-house tickets tab |
| `admin-dashboard/src/app/dashboard/support/page.tsx` | Tab label `"Tickets"` → `"Support Tickets"` | Disambiguate from Zoho Help Desk |
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx` | `<h1>` heading `"Tickets"` → `"Zoho Tickets"` | Keep page heading in sync with its renamed nav entry |
| `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` | `aria-label` `"(within Tickets)"` → `"(within Support Tickets)"` | Keep the a11y label in sync with the renamed parent tab |

## 7. Before / after

```
# Before (sidebar.tsx)
{ href: "/dashboard/support-tickets/tickets", label: "Tickets", icon: Inbox, module: "support_tickets" },

# After
{ href: "/dashboard/support-tickets/tickets", label: "Zoho Tickets", icon: Inbox, module: "support_tickets" },
```

```
# Before (support/page.tsx)
{ id: "tickets", label: "Tickets", icon: LifeBuoy },

# After
{ id: "tickets", label: "Support Tickets", icon: LifeBuoy },
```

## 8. Rollback plan

Pure label-text revert — `git revert` is a fully sufficient rollback here
(no migration, no data, no ride/wallet/insurance state touched). No feature
flag needed for the same reason: worst case of a bad rename is momentary
admin confusion, not a functional regression, and revert is a single
no-deploy-risk commit.

## 9. Verification performed

- [x] Automated tests run: `npx vitest run` — 339/339 passed, no test
      references the old label text.
- [x] Real production build: `npm run build` (not dev server, not `tsc`
      alone) — succeeded, all routes including `/dashboard/support` and
      `/dashboard/support-tickets/tickets` built clean.
- [x] Blast-radius grep performed: searched `admin-dashboard` for
      `"Tickets"` / `'Tickets'` / `>Tickets<` literals outside the edited
      files — no other consumer found.
- [x] Reviewed against this session's IA audit (Finding C) and the prior
      documented product decision in `support/_tabs/tickets.tsx` (light-touch
      naming fix, no backend consolidation — consistent with that decision).
- [x] Not feature-flagged — justified above (#8): pure display text, fully
      and cheaply revertible.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data touched).
- [x] Blast radius is stated, not assumed (grep performed, isolated to 4 files).
- [x] No silent behavior change — labels only, no routing/logic/data change;
      UX effect section filled in above.
