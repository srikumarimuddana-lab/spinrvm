# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no formal invoice document exists for corporate billing" (business decision: downloadable PDF invoice per statement period) |

## 1. Issue / gap identified

A company only has a running wallet ledger and an on-screen month-to-date
statement/summary (`routes/corporate_company.py::billing_statement`,
already built) — no downloadable, formatted document exists for a
company's own records.

## 2. Root cause

Never built.

## 3. Fix / remediation

New `backend/utils/corporate_statement_pdf.py::generate_corporate_statement_pdf(company, statement) -> bytes`,
following `utils/driver_statement_pdf.py`'s exact template (found via
research before writing any code, rather than inventing a new PDF
pipeline) — same `report_branding.new_branded_pdf`/`render_branded_pdf_footer`
chrome, same `fpdf2` layout primitives (`section_heading`/`line_item`/
`h_rule` closures), same row-cap-with-"+N more"-disclosure pattern (here:
40 line items, top 20 members by spend), and the same `pdf_safe()` call
on every dynamic string (company name, member id, source type) to avoid
`FPDFUnicodeEncodingException` on em dashes/curly quotes in user-entered
text — a real crash mode `report_branding.pdf_safe`'s own docstring
documents.

**Presentation only — this module computes nothing and moves no money.**
It renders the exact same `summary`/`line_items` dict shape
`billing_statement` already produces (including the GST/PST breakdown
from item #57), just laid out as a document instead of JSON. The
document body explicitly states "This is a record of activity, not a
bill" to avoid implying it triggers a charge — funds were already debited
as each ride settled, or via the wallet-topup/subscription mechanisms
built earlier this round.

## 4. Risk & impact on existing functionality

- **Blast radius: one new file. No existing code touched.** Nothing
  calls this function yet (the route wiring is the next commit,
  round2-22).
- Grepped for every other consumer of `report_branding.new_branded_pdf`/
  `pdf_safe`: `driver_statement_pdf.py`, `receipt_pdf.py`,
  `subscription_invoice_pdf.py`, several compliance-report generators —
  all read-only consumers of shared helper functions, none modified.
- Money-arithmetic pre-commit hook passed — this module reads pre-
  computed Decimal-derived strings (already quantized by
  `corporate_company.py::_money_str`) and does no arithmetic of its own.

## 5. User-experience effect

None yet — the function exists but nothing renders or downloads it in
this commit.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/corporate_statement_pdf.py` | New file: `generate_corporate_statement_pdf` | Render a corporate billing statement as a downloadable PDF |

## 7. Rollback plan

`git revert` the commit. No data or runtime behavior involved — pure new
function, unreferenced elsewhere in this commit.

## 8. Verification performed

- [x] `ast.parse` syntax check — clean.
- [x] Compared every layout primitive and helper closure against
      `driver_statement_pdf.py` line-by-line before writing, rather than
      designing a new PDF layout style from scratch.
- [x] Confirmed `pdf_safe` is called on every string sourced from
      user-controlled data (company legal name, member id, ride source
      type) — the one real crash risk `report_branding.py`'s own
      docstring flags for this library.
- [x] Confirmed the input shape (`company` dict + `statement` dict with
      `month`/`line_items`/`summary`) matches exactly what
      `corporate_company.py::billing_statement` already returns, so the
      route wiring in the next commit needs no data transformation.
- [x] Did **not** run `pytest` for this file — per this round's explicit
      "don't run tests until everything is developed" instruction;
      dedicated tests (mirroring `test_driver_statement_pdf.py`'s
      `%PDF` prefix + `pypdf` text-extraction pattern) land in the very
      next commit (round2-22).

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`
- [x] Blast radius is stated, not assumed — nothing calls this yet
- [x] No behavior change to a working flow — new, uncalled code

## What was NOT verified

Did not run this function to confirm it actually produces a valid,
renderable PDF — no `pytest` execution this round. fpdf2's exact
behavior (font metrics, cell wrapping, page-break timing) is reasoned
from the already-proven `driver_statement_pdf.py` using the identical
primitives, not independently confirmed by running the generator.
Verification (byte-prefix + text-extraction assertions) is the explicit
subject of the very next commit.
