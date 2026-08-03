# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — invoicing — PDF generator test slice |

## 1. Issue / gap identified

The PDF generator added in round2-21 had no test coverage — including
the one real crash risk its own design calls out (`pdf_safe` on
user-controlled company/member text) and the row-cap disclosure
invariant borrowed from `driver_statement_pdf.py`.

## 2. Root cause

Test-writing was its own decomposed subtask, following this round's
per-item pattern.

## 3. Fix / remediation

New `backend/tests/test_corporate_statement_pdf.py`, mirroring
`test_driver_statement_pdf.py`'s exact pattern (`%PDF` prefix assertion +
`pypdf` text extraction for content assertions, since fpdf2 compresses
content streams). 9 tests:

- Basic bytes/size sanity, and an empty `({}, {})` input doesn't raise.
- The "this is a record, not a bill" disclosure text is present (the
  specific wording chosen in round2-21 to avoid implying the PDF itself
  triggers a charge).
- GST/PST tax-by-type breakdown renders.
- **Both** row caps (line items at 40, members at 20) disclose their
  overflow count exactly like `driver_statement_pdf.py`'s payout cap —
  never a silent truncation.
- No false-positive overflow note when everything fits.
- A company legal name containing an em dash, curly quotes, and smart
  quotes does **not** raise `FPDFUnicodeEncodingException` — the specific
  failure mode `report_branding.pdf_safe`'s docstring warns about,
  directly exercised rather than assumed handled.
- Empty line-items/by-member renders the documented placeholder text
  instead of a blank/broken section.

## 4. Risk & impact on existing functionality

- **Blast radius: one new test file. No production code touched.**
- Reused, not duplicated, the exact test pattern from the sibling driver-
  statement PDF test file — no new test infrastructure.

## 5. User-experience effect

None — test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_corporate_statement_pdf.py` | New file: 9 tests | Cover the round2-21 generator, especially its one real crash risk (non-Latin-1 text) |

## 7. Rollback plan

`git revert` the commit. Test-only, no data or runtime behavior involved.

## 8. Verification performed

- [x] `ast.parse` syntax check — clean.
- [x] Manually traced the special-character test against
      `report_branding.pdf_safe`'s documented behavior (em dash → `-`,
      curly quotes → normalized/replaced) to confirm the assertion is
      meaningful, not just "doesn't crash by luck."
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      deferred to the single end-of-round pass, which is also the first
      point this session will confirm the generator actually produces a
      well-formed PDF (fpdf2 was never executed until then).

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — test-only
- [x] No behavior change to a working flow — purely additive tests

## What was NOT verified

Did not run these tests — this is the first point in this round's
Stripe-free PDF slice where actual execution (not just `ast.parse`) would
catch a real bug (e.g. an fpdf2 API mismatch, a cell-width overflow). That
risk is explicitly carried forward to the end-of-round pass rather than
hidden. Route wiring (the actual download endpoints) is the next commit.
