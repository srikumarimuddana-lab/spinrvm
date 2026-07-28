# Module: Compliance & Tax Reporting

**Status:** Structured lifecycle audit complete (2026-07-28) — not yet swept by
the full 22-dimension audit-framework process used for `admin-panel.md`/
`backend-api.md`. This file registers the module so a future systematic sweep
includes it automatically instead of missing it by omission (gap G9 in the
lifecycle audit below).
**Root folder:** `backend/routes/admin/compliance.py`,
`backend/utils/report_branding.py`, `backend/migrations/263_compliance_export_events.sql`,
`admin-dashboard/src/app/dashboard/compliance/page.tsx`
**Prior audit:** `reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md`
(SDLC-lifecycle audit — coverage snapshot, gap register G1–G10, phase-by-phase
assessment against CLAUDE.md's own bars, not the 22-dimension framework below)
**Related:** `docs/threat-model/admin-panel.md` (AI-3 — this module's exports
are in scope of that open finding, see G2), `docs/adr/008-report-branding-fixed-vs-branded.md`

---

## What This Module Does

Regulatory/tax exports for admins with the `compliance` module grant:
GST/PST/HST remittance summary and driver insurance-period regulatory audit
trail, in four formats (PDF/CSV/XLSX/DOCX), Spinr-branded (never a fixed
regulator layout — see the ADR). Every export is logged to
`compliance_export_events` (append-only, migration 263) for a future
privacy/regulatory audit trail. Distinct from the fixed-format SGI
D00032/D00033 forms (`routes/admin/sgi_forms.py`), which are not covered by
this module.

---

## Applicable Dimensions (subset relevant to this module; full mapping
deferred to the eventual formal sweep)

| # | Dimension | Priority | Notes |
|---|---|---|---|
| 02 | Authentication | Required | Standard admin JWT + `require_module("compliance")`; see G1 (fixed 2026-07-28 — module was previously ungrantable to non-super-admins). |
| 04 | Input validation | Required | `date_range`/`format` query params are FastAPI `Query(pattern=...)`-constrained; `driver_id` passed straight through to a filtered Supabase query. |
| 09 | Test coverage | Required | 61% at audit time, **95%** after G3 fix (HTTP-level `TestClient` tests added). Coverage floor per CLAUDE.md is 70% for admin routes. |
| 11 | Security headers / rate limiting | Required | No rate limit at audit time (G6) — fixed: `10/minute` via the shared `default_limiter`, matching `routes/admin/rides.py`'s comparable report/export-weight endpoints. |
| 12 | Compliance (PIPEDA) | Required | Exports contain driver names + insurance-period timestamps — real PII, no dual-approval gate on large exports (G2 — scoped into the existing open threat-model finding AI-3, not yet implemented). |
| 17 | Observability | Required | No Sentry/metrics instrumentation at audit time (G5) — fixed: domain-tagged Sentry capture + `spinr_admin_compliance_export_total{report_type,outcome}` counter. |
| 21 | Threat model / STRIDE | Required | Covered indirectly via `docs/threat-model/admin-panel.md`'s AI-3 (mass export → PII leak) — this module's two endpoints are explicitly named in scope there as of the G2 fix. No module-specific STRIDE pass done yet. |

Dimensions not yet assessed for this module (left for the eventual formal
sweep, not claimed as "pass" or "fail" here): 03 (encryption/secrets — N/A,
no new secrets), 14 (performance — `_ROW_LIMIT = 10000` cap exists but no
load test), 18 (DR/BCP), 19 (fraud), 20 (financial reconciliation — this
module *reads* `tax_breakdown` for reporting, does not write money), 22
(third-party risk — reuses existing `fpdf2`/`openpyxl`/`python-docx`, already
in `requirements.txt`).

---

## Lifecycle Audit Gap Register (2026-07-28) — status

See `reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md`
for full detail, reasoning, and rejected alternatives per gap.

| Gap | Summary | Status |
|---|---|---|
| G1 | Module ungrantable to non-super-admins (missing from `ALL_MODULES`) | **Fixed** (PR #2681) |
| G2 | No dual-approval gate on large exports (extends open AI-3) | **Scoped into AI-3** (PR #2682, docs-only — implementation tracked under AI-3 itself, not this module) |
| G3 | Route handlers untested at HTTP level (61%, below 70% bar) | **Fixed** (PR #2684) — 95% |
| G4 | No E2E test for `/dashboard/compliance` | **Fixed** — `admin-dashboard/e2e/compliance.spec.ts` |
| G5 | No Sentry/metrics instrumentation | **Fixed** (PR #2688) |
| G6 | No rate limiting | **Fixed** (PR #2688) |
| G7 | No ADR for branded/fixed_format design split | **Fixed** — `docs/adr/008-report-branding-fixed-vs-branded.md` |
| G8 | `compliance_export_events`' claimed 7-year retention has no enforcement | Tracked — `ACTION_ITEMS.md` D9 (P3, purge job not yet built; migration comment's claim is currently aspirational) |
| G9 | No audit-framework module scope file | **Fixed** — this file |
| G10 | Rollback command verified locally, never against real staging | **Accepted as sufficient, not re-verified** — `ACTION_ITEMS.md` D10 (table holds zero real rows in staging; re-running a destructive `DROP TABLE` against shared staging purely to prove the command isn't worth the risk) |

---

## Known Report Types (see `REPORT_FORMAT_REGISTRY` in `report_branding.py`)

`gst_pst_remittance`, `insurance_period_audit` — both `branded`, both live in
this module. `sgi_driver_details`/`sgi_vehicle_details` are `fixed_format`
and belong to `routes/admin/sgi_forms.py` instead — never confuse the two;
see ADR-008.
