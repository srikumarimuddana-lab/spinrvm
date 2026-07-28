# Module: Data Transfer

**Status:** Structured lifecycle audit complete (2026-07-28) — not yet
swept by the full 22-dimension audit-framework process used for
`admin-panel.md`/`backend-api.md`. This file registers the module so a
future systematic sweep includes it automatically instead of missing it
by omission (gap H7 in the lifecycle audit below).
**Root folder:** `backend/routes/admin/data_transfer_{export,import,jobs,search}.py`,
`backend/services/data_transfer/*`, `backend/migrations/262_data_transfer_export_jobs.sql`,
`admin-dashboard/src/app/dashboard/data-transfer/`,
`admin-dashboard/src/app/dashboard/bulk-operations/`
**Prior audit:** `reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md`
(SDLC-lifecycle audit — coverage snapshot, gap register H1–H9,
phase-by-phase assessment against CLAUDE.md's own bars)
**Related:** `docs/threat-model/admin-panel.md` (AI-3 — the export
endpoint is in scope of that open finding, see H2),
`docs/adr/009-data-transfer-background-export-and-unredacted-scope.md`

---

## What This Module Does

Admin bulk entity import/export (full-fidelity ZIP bundles of
users/drivers — profile, documents, ride history, insurance-period
audit trail), unified search/select across users+drivers, SGI regulator
compliance PDF form-fill, and export-job history/download-link
regeneration. RBAC key: `bulk_operations`. Distinct from the legacy
`/dashboard/bulk-operations` page (CSV-driven tools, hard-coded
`role === "super_admin"`, does not rely on the module grant), and from
the Compliance & Tax Reporting module's tabular reports (which has its
own `compliance` module key and never touches document files or
full-fidelity PII).

---

## Applicable Dimensions (subset relevant to this module; full mapping
deferred to the eventual formal sweep)

| # | Dimension | Priority | Notes |
|---|---|---|---|
| 02 | Authentication | Required | Standard admin JWT + `require_module("bulk_operations")`; see H1 (fixed 2026-07-28 — module was previously ungrantable to non-super-admins, PR #2700). |
| 04 | Input validation | Required | Entity-count cap (`MAX_ENTITIES_PER_EXPORT = 100`), ZIP size cap (`MAX_ZIP_BYTES = 200_000_000`), `entity_type` pattern-constrained. |
| 09 | Test coverage | Required | 37-69% at audit time (zero test files for any of the 4 route handlers), fixed to 96-100% (see H4). Coverage floor per CLAUDE.md is 70% for admin routes. |
| 11 | Security headers / rate limiting | Required | Only the export route was rate-limited at audit time (H3) — import/jobs/search had none, and `import/commit` is an unthrottled bulk-write path. Fixed: all four routes now rate-limited. |
| 12 | Compliance (PIPEDA) | Required | The export endpoint is deliberately unredacted, full-fidelity PII + document bytes (see ADR-009) — no dual-approval gate on large exports (H2 — scoped into the existing open threat-model finding AI-3, not yet implemented). |
| 17 | Observability | Required | **Already covered at audit time** — `observability.py` (Sentry + Prometheus, `spinr_data_transfer_*_total` counters) shipped early in the module's original build and is actually called from the route layer, ahead of where the Compliance module started. |
| 20 | Financial reconciliation | Required | N/A — this module moves entity/document data, not money. |
| 21 | Threat model / STRIDE | Required | Covered indirectly via `docs/threat-model/admin-panel.md`'s AI-3 (mass export → PII leak) — the export endpoint is the most literal match to AI-3's own attack-tree wording (`AAT-1`: `"Export all" endpoint`); explicitly named in AI-3's scope as of the H2 fix. No module-specific STRIDE pass done yet. |

Dimensions not yet assessed for this module (left for the eventual
formal sweep, not claimed as "pass" or "fail" here): 03 (encryption/secrets
— reuses existing `_vault_encrypt`, no new secrets), 14 (performance —
backgrounding + entity/byte caps already address the obvious risk, no
formal load test), 18 (DR/BCP), 19 (fraud), 22 (third-party risk — reuses
existing Supabase Storage, `openpyxl`/`pypdf`, already in
`requirements.txt`).

---

## Lifecycle Audit Gap Register (2026-07-28) — status

See `reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md`
for full detail, reasoning, and comparison against the Compliance
module's own audit.

| Gap | Summary | Status |
|---|---|---|
| H1 | `bulk_operations` missing from `ALL_MODULES` (ungrantable) | **Fixed** (PR #2700) |
| H2 | Export endpoint not scoped into open AI-3 finding | **Scoped into AI-3** (this PR, docs-only) |
| H3 | Only export route rate-limited; import/jobs/search had none | **Fixed** (PR #2706) |
| H4 | Zero test files for any of the 4 admin routes | **Fixed** (PR #2714) |
| H5 | No E2E coverage for any Data Transfer/Bulk Operations surface | **Fixed** — see `admin-dashboard/e2e/` |
| H6 | No ADR for background-job model / unredacted-export decision | **Fixed** — `docs/adr/009-data-transfer-background-export-and-unredacted-scope.md` |
| H7 | No audit-framework module scope file | **Fixed** — this file |
| H8 | Migration 262 rollback re-verification against real staging | Status unknown, not confirmed either way — flagged in the audit, not independently tracked further here |
| H9 | Two dashboard pages share one RBAC module key with different access models | **Fixed** — cross-reference comment added to `sidebar.tsx` |

---

## Known Report/Job Types

`data_transfer_export_jobs` (migration 262) tracks admin-initiated
export batches — purged hourly by `utils/data_export_purge.py` past
`expires_at`, unlike the Compliance module's `compliance_export_events`
(which claims a 7-year retention with no enforcement yet, tracked as
that module's own gap G8/`ACTION_ITEMS.md` D9). Format dispatch (`zip`,
`csv`, `json`, `excel`) lives in `data_transfer_export.py`'s
`_FORMAT_BUILDERS` table, mirroring the Compliance module's
`REPORT_FORMAT_REGISTRY` pattern for "one lookup table, not
per-call-site branching" — see ADR-008 for the reasoning behind that
shape in the sibling module.
