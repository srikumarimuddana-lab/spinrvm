# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard (company portal) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — invoicing — company-portal UI slice (final) |

## 1. Issue / gap identified

Final slice: the round2-23 company-portal endpoint exists but no button
downloads it — a company admin would have to hit the API directly.

## 2. Root cause

Never built.

## 3. Fix / remediation

- New `fetchCompanyStatementPdfBlob(companyId, month)` in
  `lib/companyApi.ts`, mirroring `lib/api/corporate.ts::fetchKybDocumentBlob`'s
  exact minimal pattern (raw `fetch` with a Bearer token from the
  relevant auth store, `.blob()` return, no JSON parsing) — a binary
  response bypasses `companyRequest`'s JSON-only contract, same as the
  existing KYB-document precedent.
- New "Invoice PDF" button on the billing page, next to the existing CSV
  export button, calling the new function and triggering a browser
  download via the same `URL.createObjectURL` + anchor-click pattern
  `downloadCSV` (already on this page) uses — no new download mechanism
  invented.
- Errors surface via the same `toast` pattern the round2-20 top-up dialog
  established on this same page (consistent, not a third pattern).

## 4. Risk & impact on existing functionality

- **Blast radius: one new function + one new button, in two files.**
  Grepped every other call site of `downloadCSV`/the CSV button: none —
  the new PDF button is additive, sitting beside it, not replacing it.
- Bracket-balance check (no TS/JS toolchain run, per this round's
  instruction) on both touched files — clean.
- No existing state, handler, or metric card on this page is touched —
  confirmed by diff: only new lines were added (new import, new state
  variable, new handler function, new button).

## 5. User-experience effect

**Corporate-admin (company-side) facing.** A company admin viewing their
billing page can now download a formatted PDF invoice for the selected
month, alongside the existing CSV export. No existing button, table, or
metric on this page changes behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/company-portal/[id]/billing/page.tsx` | New `downloadPDF` handler + "Invoice PDF" button + `downloadingPdf` state | Drive the round2-23 company-portal endpoint |
| `admin-dashboard/src/lib/companyApi.ts` | New `fetchCompanyStatementPdfBlob` function | Typed blob-fetch client for the round2-23 endpoint |

## 7. Rollback plan

`git revert` the commit. Purely additive UI — no data written by this
commit; a PDF download is a read-only action against already-existing
data.

## 8. Verification performed

- [x] Bracket-balance check on both touched files (no TS/JS toolchain
      run, per this round's instruction) — balanced.
- [x] Confirmed the blob-fetch pattern matches the existing
      `fetchKybDocumentBlob` precedent (auth-store token, raw `fetch`,
      `.blob()`, no `companyRequest` JSON parsing) rather than inventing
      a new binary-download convention.
- [x] Confirmed the endpoint path
      (`/api/company/{id}/billing/statements/{month}/pdf`) matches the
      round2-23 route's registered path exactly, including the
      `encodeURIComponent(month)` needed since `month` is a user-visible
      `YYYY-MM` string used directly in a URL path segment.
- [x] Did **not** run `npm run build`, `tsc --noEmit`, `eslint`, or start
      the dev server — per this round's explicit instruction. This is the
      third `admin-dashboard`/company-portal file this round that has
      never been compiled (alongside round2-17 and round2-20) — all three
      should be the first things checked in the end-of-round build pass,
      per CLAUDE.md's requirement that a real production build (not just
      `tsc --noEmit`) runs before any `admin-dashboard` change merges.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — confirmed via diff that only
      new lines were added
- [x] No silent behavior change to a working flow — every existing
      button, handler, and metric card on this page is unchanged

## What was NOT verified

Did not run `npm run build` or click through this button in a browser —
no visual/functional confirmation exists yet that the download actually
triggers correctly, that the auth header resolves against a real
company-portal session, or that the resulting file opens as a valid PDF
in a real browser. This closes out the invoicing feature build (round2-21
through round2-25, 5 commits, following round2-12 through round2-20 for
items #63 and #64) — the end-of-round `npm run build` pass for
`admin-dashboard` now has three UI slices from this round it has never
compiled, and should be treated as the single highest-priority item in
that pass before anything else.
