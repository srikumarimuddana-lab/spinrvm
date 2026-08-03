# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "automated KYB re-verification" — admin dashboard UI slice (final) |

## 1. Issue / gap identified

Final slice: the round2-32 endpoint exists but no admin-dashboard screen
shows it.

## 2. Root cause

Never built.

## 3. Fix / remediation

- New `getKybReverificationDue` API client function + `KybReverificationCompany`/
  `KybReverificationDue` types in `lib/api/corporate.ts`, re-exported from
  `lib/api.ts`.
- New summary card on `dashboard/corporate-accounts/page.tsx`, placed
  directly after the existing wallet-risk-portfolio card (item #56) and
  built from the **exact same structural template**: a colored banner
  card (sky, not amber — visually distinct from the wallet-risk warning
  color so the two "flagged" categories aren't confused with each other),
  a count + threshold summary line, clickable chips linking to each
  flagged company's detail page (capped at 12 visible with a "+N more"
  overflow note, same pattern as the wallet-risk card), each showing the
  company name and its last KYB review date.
- The card is purely informational — clicking through leads an admin to
  the existing company detail page, where the existing KYB review flow
  (already built, `kyb-review` endpoint) is unchanged and is how an admin
  would actually act on the reminder. This commit adds no new "approve/
  reject" action anywhere.

## 4. Risk & impact on existing functionality

- **Blast radius: one new card + one new state/effect pair, in three
  files.** Grepped every other consumer of the wallet-risk card's JSX
  pattern and state variables (`flaggedWallets`/`totalWallets`/
  `riskLoading`): none collide with the new `kybDue`/
  `kybThresholdMonths`/`kybLoading` names.
- The existing wallet-risk card, search/filter bar, account table, and
  every dialog (create/edit/delete) on this page are unchanged —
  confirmed by diff, the new card was inserted between two existing
  blocks without altering either.
- Bracket-balance check (no TS/JS toolchain run, per this round's
  instruction) on all three touched files — clean.
- No new mutating action was added — this is a read-only summary linking
  to the existing (unmodified) KYB review flow.

## 5. User-experience effect

**Internal admin-facing only.** A Spinr admin viewing the corporate
accounts list now sees a card flagging companies whose KYB approval is
stale, with a direct link to each company's detail page. No existing
screen, filter, or action changes behavior. No company/rider/driver-
facing surface is touched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/page.tsx` | New summary card + state/effect, mirroring the existing wallet-risk card | Surface the round2-32 endpoint |
| `admin-dashboard/src/lib/api/corporate.ts` | New types + `getKybReverificationDue` function | Typed client for the round2-32 endpoint |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new function + 2 types | Match the established barrel-export convention |

## 7. Rollback plan

`git revert` the commit. Purely additive, read-only UI — no data written
by this commit.

## 8. Verification performed

- [x] Bracket-balance check on all three touched files (no TS/JS
      toolchain run, per this round's instruction) — balanced.
- [x] Confirmed via diff that the existing wallet-risk card, search bar,
      table, and every dialog on this page are byte-for-byte unchanged.
- [x] Confirmed the new state/prop names don't collide with any existing
      variable on this page.
- [x] Confirmed the card links to the existing (unmodified) company
      detail page, where the existing KYB review action already lives —
      no new mutating endpoint or action was added in this commit.
- [x] Did **not** run `npm run build`, `tsc --noEmit`, `eslint`, or start
      the dev server — per this round's explicit instruction. This is
      the fifth `admin-dashboard`/company-portal file this round that has
      never been compiled (alongside round2-17, round2-20, round2-25,
      round2-29) — all five should be checked together in the
      end-of-round `npm run build` pass, the first thing that pass should
      do per CLAUDE.md's requirement that a real production build runs
      before any `admin-dashboard` change merges.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — confirmed via diff
- [x] No silent behavior change to a working flow — every existing
      control on this page is unchanged; the new card is purely additive
      and read-only

## What was NOT verified

Did not run `npm run build` or click through this card in a browser — no
visual/functional confirmation exists yet. This closes out the automated-
KYB-re-verification feature build (round2-30 through round2-33, 4
commits) and, with it, **all 5 of the "ask me one-by-one" business-
decision items** (#63 pricing, #64 self-serve funding, #65 invoicing,
#66 section budgets, #67 KYB re-verification) plus items #61-62 (QA
follow-ups from earlier in this round). Per the standing governing
instruction for this entire session, the single end-of-round `pytest` +
`npm run build` pass across everything committed this round is now the
next and final step — no further sequential fixes remain in the
originally-scoped "15 remaining findings" list.
