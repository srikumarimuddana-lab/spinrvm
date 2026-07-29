# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Direct follow-up to the original report-template professionalism complaint |

## 1. Issue / gap identified

The GST/PST Remittance report's subtitle crammed a date range and three dollar totals onto one line — `"2026-06-29 to 2026-07-29 — Total GST $17.91, Total PST $0.00, Total HST $0.00"` — reported as reading unprofessionally. The logo-size and timestamp-formatting fixes shipped earlier this session addressed the other two complaints in the same report but not this one.

## 2. Root cause

`report_branding.new_branded_pdf/new_branded_workbook/new_branded_document` all accepted `subtitle` as a single string only, so every caller building a multi-part subtitle had to concatenate it into one line — `routes/admin/compliance.py`'s GST/PST endpoint did exactly that.

## 3. Fix / remediation

Widened `subtitle` on all three branded-document constructors to accept `str | list[str]` (backward compatible — a plain string still works everywhere else). Each line renders on its own row; the PDF's divider-rule position and content-start y now compute from the subtitle line count instead of a fixed offset. GST/PST's subtitle is now `["2026-06-29 to 2026-07-29", "GST: $17.91    PST: $0.00    HST: $0.00"]`. The truncation warning (rare, high-value) is appended to the totals line rather than added as a 3rd line, since `new_branded_workbook` only renders the first 2 lines — this keeps it visible across every format, not just PDF/Word.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to subtitle rendering.** Grepped every caller of the three constructors — GST/PST remittance, insurance-period audit, Knight Archer, DSAR lookup, and the new T4A filer handoff — all pass a plain string except GST/PST (now updated); none of those callers needed any change since `str` input still works identically to before.
- CSV format never used subtitle at all — unaffected.
- No schema, no API contract change — this is presentation-layer only.

## 5. User-experience effect

- **Internal admin only.** The GST/PST Remittance report (all 4 formats: PDF/Excel/Word/CSV — CSV unaffected) now shows a clean two-line header instead of one crowded sentence.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/report_branding.py` | `subtitle` accepts `str \| list[str]` on all 3 branded-document constructors | Core of the fix |
| `backend/routes/admin/compliance.py` | GST/PST subtitle split into 2 lines; `_render_tabular_report`'s type hint widened | Apply the fix |
| `backend/tests/test_report_branding.py` | 2 new tests (multiline renders, header block grows) | Coverage |
| `backend/tests/test_compliance_reports_http.py` | 1 new test asserting the route passes a 2-line list | Coverage |

## 7. Before / after

```python
# Before
subtitle = (
    f"{start_date.date().isoformat()} to {end_date.date().isoformat()} — "
    f"Total GST ${gst_total:.2f}, Total PST ${pst_total:.2f}, Total HST ${hst_total:.2f}"
)

# After
totals_line = f"GST: ${gst_total:.2f}    PST: ${pst_total:.2f}    HST: ${hst_total:.2f}"
subtitle = [f"{start_date.date().isoformat()} to {end_date.date().isoformat()}", totals_line]
```

## 8. Rollback plan

Plain `git revert` — pure presentation code, no data, no schema, no flag.

## 9. Verification performed

- [x] `pytest backend/tests/test_report_branding.py backend/tests/test_compliance_reports_http.py backend/tests/test_sgi_form_filler.py backend/tests/test_stripe_kyc_sync.py backend/tests/test_sgi_template_versions.py` — 86/86 passing.
- [x] `ruff check` clean.
- [ ] Not visually verified in a rendered PDF/Excel/Word file (no viewer in this session) — reasoned about and unit-tested at the layout-call level (divider-y math, line count), not screenshotted.

## 10. What was NOT verified / deferred

- The other two reports (insurance-period audit, Knight Archer) already had reasonably clean single-line subtitles and were left unchanged — only GST/PST had the crowding complaint.
