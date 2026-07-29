# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | live-user follow-up on Compliance/SGI report templates |

## 1. Issue / gap identified

Two more reported issues on the branded Compliance/report-generation output: (1) the logo embedded in every branded report/Excel/Word document was an explicitly-labeled placeholder recreation, not Spinr's real brand mark, and rendered visibly smaller than the report title; (2) date/time values in the insurance-period audit report (`started_at`/`ended_at`) and the Knight Archer driver-onboarding report (`onboarded_at`) rendered as raw, unformatted ISO 8601 strings with no space between date and time.

## 2. Root cause

1. `backend/static/branding/spinr_logo.png`'s own README explicitly documented it as "a clean recreation ... replace with an official design-team asset when one exists." `new_branded_pdf()`/`new_branded_workbook()`/`new_branded_document()` all rendered it at a fixed size (11mm PDF / 40px Excel / 0.35in Word) chosen when the placeholder was added, without adjusting for the title text's visual weight.
2. `routes/admin/compliance.py`'s `_insurance_period_rows()` and the Knight Archer row-builder passed `p.get("started_at")`/`d.get("created_at")` etc. straight through from the DB into the report row dict with no formatting — the raw column value (e.g. `2026-07-29T14:32:10.123456+00:00`) went directly into the PDF/Excel/Word/CSV cell.

## 3. Fix / remediation

1. Replaced `backend/static/branding/spinr_logo.png` with the real Spinr wordmark already used live in `driver-app`/`rider-app` (`assets/images/spinr-logo.png`), upscaled 2x with Lanczos resampling for print resolution. Increased the rendered logo size across all three branded formats (PDF 11mm→14mm, Excel 40px→48px, Word 0.35in→0.42in) so it sits level with the title's visual weight instead of reading smaller.
2. Added `report_branding.format_report_timestamp(value, empty="")` — parses an ISO 8601 timestamp and renders it as `YYYY-MM-DD HH:MM UTC` (falls back to the raw string if unparseable, rather than hiding a real data problem behind a blank cell). Wired into both report builders' `started_at`/`ended_at`/`onboarded_at` fields.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `format_report_timestamp` is a new function with no other callers to break. The logo/sizing constants in `report_branding.py` are read by every branded report (GST/PST remittance, insurance-period audit, Knight Archer, DSAR lookup) — grepped `REPORT_FORMAT_REGISTRY` to confirm these are the only 4 "branded" report types; all 4 get the same visual improvement, none regress (SGI D00032/D00033 are `fixed_format` and never call these functions).
- No ride, dispatch, payment, or corporate-billing code touched. No schema change.
- The logo swap is an asset replacement, not a code-path change — `has_logo_asset()`'s fallback (title-only header if the file is ever missing) is untouched.

## 5. User-experience effect

- **Internal admin only.** Every branded report an admin downloads or emails now shows the real Spinr logo (previously a placeholder) at a size that matches the title, and shows human-readable timestamps instead of raw ISO strings.
- Not visible to riders/drivers/corporate admins.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/static/branding/spinr_logo.png` | Replaced placeholder with the real brand logo (from driver-app/rider-app assets), upscaled 2x | Real logo, not a recreation |
| `backend/static/branding/README.md` | Updated to describe the real asset and its source | Keep docs accurate |
| `backend/utils/report_branding.py` | Larger logo rendering (PDF/Excel/Word); new `format_report_timestamp()` helper | Logo sizing; timestamp formatting |
| `backend/routes/admin/compliance.py` | `started_at`/`ended_at`/`onboarded_at` now go through `format_report_timestamp()` | Fix crammed date/time display |
| `backend/tests/test_report_branding.py` | 4 new tests for `format_report_timestamp` | Coverage for the fix |

## 7. Before / after

```python
# Before
"onboarded_at": d.get("created_at") or "",
# → "2026-07-29T14:32:10.123456+00:00" rendered raw in the report cell

# After
"onboarded_at": report_branding.format_report_timestamp(d.get("created_at")),
# → "2026-07-29 14:32 UTC"
```

## 8. Rollback plan

Plain `git revert` — pure code + a static asset swap, no data or schema touched, no flag involved. If the new logo asset is somehow wrong, reverting restores the placeholder file (still functional, just not the real mark).

## 9. Verification performed

- [x] Automated tests: `pytest backend/tests/test_report_branding.py` (30 passed, 4 new), `test_compliance_reports_http.py` (53 passed, unaffected), `test_sgi_form_filler.py` (13 passed, unaffected — confirms the logo-asset change doesn't touch the SGI `fixed_format` path).
- [x] Manually verified the real logo image (visually inspected — bullseye mark + "spinr" wordmark, matches the live mobile apps) and confirmed `format_report_timestamp` against several real-shaped ISO inputs (with microseconds, with `Z` suffix, `None`, and a malformed string).
- [x] `ruff check` clean on both touched backend files.
- [ ] Not visually verified in a rendered PDF/Excel/Word file in this session (no PDF viewer in this environment) — the fpdf2/openpyxl/python-docx calls were reasoned about and unit-tested at the data/field level, not screenshotted. Flagging per CLAUDE.md's "no visual regression tooling" gap.

## 10. What was NOT verified / deferred

- Whether the upscaled logo (768×312, from a 384×156 source) is print-sharp enough at 14mm — 2x nearest-available upscale with Lanczos is a reasonable approximation, not a substitute for a native high-resolution export from the original design file if one exists.
- The remaining asks from this round (sidebar/module IA, report-template storage strategy, T4A/CRA electronic-filing research) are research-and-recommend items, intentionally not code changes in this batch — covered in the accompanying chat response, not this log.
