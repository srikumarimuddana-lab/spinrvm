# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard (company portal) |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — self-serve wallet funding — company-portal UI slice (final) |

## 1. Issue / gap identified

Final slice: the round2-18 endpoint exists but no company-portal screen
can drive it — a company admin would have to call the API directly.

## 2. Root cause

Never built — see round2-18 for full background.

## 3. Fix / remediation

- Added a "Top up" button on the existing company-portal billing page's
  Wallet balance `Metric` card (`company-portal/[id]/billing/page.tsx`),
  opening a `Dialog` with a single amount input ($100–$10,000, matching
  the backend's bounds) and a "Top up" button that calls the new
  `selfServeWalletTopup` client function.
- **Deliberately scoped to charging the saved default card only** — this
  slice does not add a "manage payment methods" flow or Stripe Elements
  card-entry UI, so no client-supplied `payment_method_id` is sent; the
  backend (round2-18) already falls back to `get_default_payment_method`
  in that case. A company with no card on file gets the backend's clear
  422 message surfaced via toast, not a broken flow — adding a new-card
  entry UI is out of scope and would need its own product/security
  review (raw card collection, PCI scope) before being built.
- On success, shows a toast explaining the balance updates once the
  webhook-driven payment completes (async, not synchronous) rather than
  implying an instant balance bump — accurately describes the same
  `payment_intent.succeeded` webhook flow every other topup path in this
  codebase already goes through.
- `Metric` (the shared stat-card component already used by every metric
  on this page) gained an optional `action` slot so the wallet-balance
  card alone can render the button — every other `Metric` call site on
  this page is unchanged (the prop is optional).
- New `selfServeWalletTopup` client function + `SelfServeTopUpResult`
  type in `lib/companyApi.ts`, following the file's existing
  `companyRequest<T>(...)` pattern exactly (same session binding as every
  other company-portal call — NOT the staff-admin `lib/api.ts` client).

## 4. Risk & impact on existing functionality

- **Blast radius: one new dialog + one new optional prop on a shared
  component, in two files.** Grepped every other call site of `Metric`
  on this page (4 other cards) — none pass `action`, so `{action}`
  renders `undefined` (nothing) for them; visually and functionally
  unchanged.
- Grepped `companyApi.ts` for any existing consumer of the new function
  name: none — first use.
- Bracket-balance check (no TS/JS toolchain run, per this round's
  instruction) on both touched files — clean.
- This UI depends entirely on the round2-18 endpoint's own safeguards
  (amount cap, active-company + own-membership checks, Stripe
  off-session confirm) — no new validation logic duplicated or invented
  client-side beyond the same $100/$10,000 bounds shown as `min`/`max` on
  the input (a UX hint only; the backend is the actual enforcement).

## 5. User-experience effect

**Corporate-admin (company-side) facing.** A company admin viewing their
billing page can now top up their own wallet from a saved card, instead
of contacting Spinr support. No existing metric card, table, or CSV
export on this page changes behavior. If a company has no card on file,
the attempt fails with a clear message rather than a silent no-op.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx` | New Top-up dialog, `Metric`'s new optional `action` slot, new imports (`Dialog*`, `Input`, `useToast`, `Plus`) | Company-portal UI for the round2-18 endpoint |
| `admin-dashboard/src/lib/companyApi.ts` | New `selfServeWalletTopup` function + `SelfServeTopUpResult` type | Typed client for the round2-18 endpoint |

## 7. Rollback plan

`git revert` the commit. Purely additive UI — no data written by this
commit itself (real Stripe charges only happen if a company admin
actually clicks through the dialog against the already-live round2-18
endpoint, which predates and is independent of this UI).

## 8. Verification performed

- [x] Bracket-balance check on both touched files (no TS/JS toolchain
      run, per this round's instruction) — balanced.
- [x] Confirmed every other `Metric` call site on this page still
      compiles logically unchanged (new prop is optional, unused
      elsewhere).
- [x] Confirmed the amount bounds shown in the UI ($100–$10,000) match
      the round2-18 backend's `Field(ge=100, le=10000)` exactly.
- [x] Confirmed `selfServeWalletTopup`'s request shape (`POST`, body
      `{amount}`) matches `SelfServeTopUpRequest` on the backend.
- [x] Did **not** run `npm run build`, `tsc --noEmit`, `eslint`, or start
      the dev server — per this round's explicit instruction. Per
      CLAUDE.md's own gate, a real production build (not just a syntax
      check) is required before merge for any `admin-dashboard` change;
      this is deferred to the end-of-round pass, same standing gap noted
      in round2-17 for the sibling admin-dashboard UI slice of item #63.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — confirmed via grep for other
      `Metric` consumers and other `companyApi.ts` exports
- [x] No silent behavior change to a working flow — every existing
      metric card, table, and CSV export is byte-for-byte unchanged

## What was NOT verified

Did not run `npm run build` or any TypeScript/lint toolchain, and did not
click through this dialog in a browser — no visual/functional
confirmation exists yet that the amount validation, toast messaging, or
dialog open/close states behave correctly against a real backend
response. This closes out the self-serve wallet-funding feature build
(round2-18 through round2-20, 3 commits). Combined with round2-17
(item #63's admin-dashboard UI), there are now two `admin-dashboard`
files from this round that have never been compiled — both should be the
first things checked in the end-of-round `npm run build` pass.
