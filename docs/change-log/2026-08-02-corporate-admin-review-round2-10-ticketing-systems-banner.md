# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "two parallel, non-integrated ticketing systems" |

## 1. Issue / gap identified

Two separate, simultaneously-live ticketing systems exist: an in-house
system (`support/_tabs/tickets.tsx`, backed by `getTickets`/
`replyToTicket`/`closeTicket`/`createTicket`) embedded as a tab on the
"Support & Issues" page, and the Help Desk (Zoho) integration
(`support-tickets/tickets/page.tsx`, backed by `getDeskTickets`/
`createDeskTicket`/`searchDeskTickets`), linked as its own top-level
sidebar item with a dedicated Trends analytics sub-page.

## 2. Root cause

**Verified before deciding the fix's shape** (unlike the Disputes/FAQs
pair, this one does NOT reduce to the same shallow case): the two ticket
systems call entirely different API functions against what are almost
certainly different backing tables — this is a genuine architectural
duplication (two separate ticket stores), not just two UIs over one
dataset. Full consolidation would mean picking a winner and migrating
real ticket data/history — a real project, out of scope for a light-touch
pass.

## 3. Fix / remediation

Per the same product decision as Disputes/FAQs (light-touch, no data
migration, full consolidation deferred): added a banner on the in-house
tickets tab pointing admins toward the Help Desk (Zoho) integration.
Worded deliberately more hedged than the Disputes/FAQs banners ("Spinr's
primary ticketing system is now the Help Desk (Zoho) integration" rather
than "manage in full over there") since these are NOT the same data —
an admin with real open tickets in the in-house system isn't being told
their data moved, only that the actively-developed system going forward
is the Zoho integration. Evidence for that direction: the Zoho system has
its own dedicated backend module (`routes/admin/support_tickets.py`), a
Trends analytics sub-page, and documented ongoing test-coverage
investment (`ACTION_ITEMS.md`); the in-house tab has none of that.

## 4. Risk & impact on existing functionality

- **Blast radius: one file, one banner, no logic changed.** Every
  existing state variable, handler, and API call in the tab is untouched.
- Grepped every consumer of the tickets tab component: only
  `support/page.tsx`'s tab switch, unchanged in this commit.
- No change to the Help Desk (Zoho) pages or the in-house ticket data —
  this is purely a wayfinding banner.
- Explicitly did **not** claim data equivalence or deprecate/hide the
  in-house tab's functionality — an admin who has been using it can keep
  using it exactly as before; the banner adds awareness, nothing else.

## 5. User-experience effect

**Internal admin-facing only.** An admin using the in-house tickets tab
now sees a pointer toward the Help Desk (Zoho) integration as the primary
system going forward. No existing functionality removed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` | New banner linking to `/dashboard/support-tickets/tickets` | Point toward the actively-developed system without falsely implying shared data |

## 7. Rollback plan

`git revert` the commit. No migration, no data written — purely additive
JSX in one file.

## 8. Verification performed

- [x] Confirmed via code read that the two ticket systems use genuinely
      different API functions/backends (not the same shallow case as
      Disputes/FAQs) before deciding how to word the banner.
- [x] Bracket-balance check on the touched file (no TS/JS toolchain run,
      per this round's instruction) — balanced.
- [x] Confirmed the sidebar links to `/dashboard/support-tickets/tickets`
      as its own top-level "Help Desk" nav item.
- [x] Blast-radius grep performed: only `support/page.tsx` renders this
      tab component; unchanged.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to a working flow — every existing
      handler/state/API call is byte-for-byte unchanged; the banner's
      wording was deliberately chosen not to imply a data merge that
      doesn't exist

## What was NOT verified

Did not run `eslint`/`tsc --noEmit`/`vitest` or a production build — per
this round's explicit instruction, deferred to a single pass at the end.
Did not confirm with certainty that the in-house ticket system is truly
deprecated (no explicit deprecation notice found in `ACTION_ITEMS.md` or
code comments) — the banner's wording was deliberately hedged for exactly
this reason, pointing toward the more actively-developed system without
asserting the other is dead. Full consolidation (a real data migration
project) is explicitly out of scope and not attempted here.
