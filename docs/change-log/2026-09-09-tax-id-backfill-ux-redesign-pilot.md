# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/tax-id-backfill-ux-pilot`) |
| Related issue or gap ID | Follow-up to the Legacy Tax-ID Backfill CSV-format confusion reported this session (30 real validation errors, operator unable to tell what the tool did or how to build its input file) |

## 1. Issue / gap identified

The Legacy Tax-ID (SIN + GST/HST BN) Backfill admin tool had three usability gaps, surfaced when an operator's real CSV was rejected and they couldn't tell why, what the tool was for, or how to build the file it wanted:

1. No plain-language explanation on the screen of what the tool does, why it exists, which files it needs, or its value — only a one-line technical description.
2. No way to build its required `phone,sin,gst_bn` CSV from the raw MongoDB export inside the admin portal — an operator had to build it out-of-band (e.g. by pasting real SIN/GST data into a chat session), which is a PIPEDA exposure this tool's own docstring says it's designed to avoid.
3. Validation errors were shown as a raw technical table (`row_ref` / `field` / short backend message) with no explanation of what caused them or what to do — an operator had to bring the error export elsewhere to get it interpreted.

This is a pilot: only this one tool (of ~19 migration tools on the Bulk Import page) is being redesigned, to validate the approach before considering the rest.

## 2. Root cause

The tool was built to mirror the other single-CSV bulk importers on the page (`stripe_import.py`'s validate/commit contract) but, unlike some siblings (e.g. `Legacy SIN/DOB Backfill`), never got a raw-export upload path — so building the ready CSV was always a manual, out-of-band step. Explanatory copy and error-message UX were never a stated requirement when the tool shipped, since it was expected to be used infrequently by someone already familiar with the migration plan.

## 3. Fix / remediation

**Backend** (`backend/routes/admin/tax_id_import.py`):
- Added `POST /api/admin/tax-ids/import/prepare-validate` and `.../prepare-commit`, accepting raw `banks.csv` + `drivers.csv` directly. These reuse the already-tested join in `scripts/build_legacy_tax_id_csv.py:build_rows` and feed its output through the *same* `_build_plan`/validation logic the single-CSV path uses — validation behaves identically regardless of upload path.
- Extracted the single-CSV commit's write loop into `_apply_plan()` so both commit endpoints share one write path rather than duplicating it.
- Mirrors `legacy_sin_dob_backfill.py`'s two-file upload/validate/commit shape exactly, including its sha256-bound `validation_token` gate (same utility, `utils/driver_import_token.py`) — no new security primitive introduced.

**Frontend** (`admin-dashboard`):
- `LegacyTaxIdImport.tsx`: added a permanent "What this tool does, in plain terms" panel (what/why/which files/value); added a second upload tab ("Prepare from Mongo export") alongside the original ("I have the ready CSV"), wired to the new endpoints; each error/warning table row now shows a plain-language cause + fix beneath the raw backend message.
- New `lib/tax-id-error-help.ts`: a static lookup mapping every known backend validation message (from `tax_id_import.py` and `utils/sin.py`) to a plain-language explanation. Returns `null` (renders nothing extra) for an unmapped message rather than guessing.
- `lib/api/imports.ts` / `lib/api.ts`: new API client functions/types for the two new endpoints, mirroring `adminValidateSinDobBackfill`/`adminCommitSinDobBackfill`'s existing shape.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one tool.** Grepped every caller of the changed/added backend functions and the frontend component:
  - `_apply_plan` (new): called only by the two existing/new commit endpoints in the same file. No other module imports from `tax_id_import.py`.
  - `build_legacy_tax_id_csv.build_rows`: already had one caller (the CLI script's `main()`); this adds a second caller (the new endpoint). The function itself is unchanged — no risk to the CLI script's own behavior, confirmed by re-running `test_build_legacy_tax_id_csv.py` unchanged (9/9 pass).
  - `LegacyTaxIdImport.tsx`: rendered only from `bulk-operations/page.tsx`'s Phase 3 section; no other page imports it.
  - `lib/tax-id-error-help.ts`: new file, no other consumers.
- **Existing single-CSV validate/commit endpoints are behavior-preserving.** `commit_tax_id_import`'s only change is calling the extracted `_apply_plan()` instead of inline code — same logic, same order of operations. All 16 pre-existing tests in `test_admin_tax_id_import.py` pass unchanged (byte-identical assertions, no test modified).
- **What could regress:** none identified for the existing single-CSV flow. The new raw-export flow is net-new surface with its own dedicated tests (6 new backend tests, all passing) and does not alter any existing table schema, RLS policy, or write path beyond reusing the existing `_apply_plan` write logic.
- **Rate limiting**: the two new endpoints reuse the existing `tax_id_import_validate_limit`/`tax_id_import_commit_limit` SlowAPI decorators. SlowAPI keys by request path + user/IP, so this does not share a bucket with the existing single-CSV endpoints — each route gets its own independent limit.

## 5. User-experience effect

Internal-admin-facing only (super_admin role required); no rider/driver/corporate-admin visibility. Not visible mid-session to anyone outside the admin portal — this is a low-frequency, operator-run migration tool, not a live-tested consumer flow. The existing "I have the ready CSV" tab's behavior and copy are unchanged (still the default-selected tab), so an operator already familiar with the tool sees no disruption — the new tab and explainer panel are additive.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/tax_id_import.py` | Added `prepare-validate`/`prepare-commit` endpoints, raw-export read helpers, extracted `_apply_plan()` | Let an operator upload raw banks.csv+drivers.csv instead of building a CSV out-of-band |
| `backend/tests/test_admin_tax_id_import.py` | Added `TestPrepareFromLegacyExport` (6 tests) | Cover the new endpoints: join+validate, no-match 422, commit via shared `_apply_plan`, missing/mismatched token |
| `admin-dashboard/src/lib/api/imports.ts` | Added `adminPrepareValidateTaxIdFromLegacyExport`/`adminPrepareCommitTaxIdFromLegacyExport` + types | API client for the new endpoints |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new functions/types | Barrel file convention this repo already follows |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx` | Added explainer panel, two-tab upload UI, per-row error explanations | Address all three UX gaps in one pilot |
| `admin-dashboard/src/lib/tax-id-error-help.ts` (new) | Static message → plain-language explanation map | In-app error help so operators don't need to bring errors to Claude/support |
| `admin-dashboard/src/lib/__tests__/tax-id-error-help.test.ts` (new) | Unit tests for the mapping | Lock in exact-match and prefix-match behavior |

## 7. Before / after

```tsx
// Before — LegacyTaxIdImport.tsx (single upload path only)
<CardDescription>
    Fill SIN and GST/HST business number for drivers whose numbers were collected on
    the previous app, matched by phone. Bank account/routing numbers are never read
    from this CSV — only phone, sin, and gst_bn.
</CardDescription>
...
<Input id="tax-id-csv" type="file" ... />
```

```tsx
// After — plain-language panel + tabbed upload (single CSV, or raw export)
<WhatThisDoes />  {/* what / why / which files / value, always visible */}
<Tabs value={mode} onValueChange={...}>
  <TabsList>
    <TabsTrigger value="single">I have the ready CSV</TabsTrigger>
    <TabsTrigger value="legacy-export">Prepare from Mongo export</TabsTrigger>
  </TabsList>
  <TabsContent value="single">{/* original single-file input, unchanged */}</TabsContent>
  <TabsContent value="legacy-export">{/* banks.csv + drivers.csv inputs */}</TabsContent>
</Tabs>
```

```python
# Before — commit_tax_id_import's write loop, inline, not reusable
async def commit_tax_id_import(...):
    ...
    if plan["errors"]:
        return {**_report(plan, batch, len(rows)), "committed": False}
    # ~70 lines of write/stripe-push/audit logic inline here
    ...
```

```python
# After — extracted so the new raw-export commit endpoint can share it
async def commit_tax_id_import(...):
    ...
    if plan["errors"]:
        return {**_report(plan, batch, len(rows)), "committed": False}
    return await _apply_plan(plan, batch, admin)

async def commit_tax_id_import_from_legacy_export(...):
    ...
    if plan["errors"]:
        return {**_report(plan, batch, len(rows)), "committed": False}
    return await _apply_plan(plan, batch, admin)
```

## 8. Rollback plan

No feature flag, migration, or data dependency. `git-revert-safe`: the new endpoints and UI tab are purely additive (the original single-CSV path is unchanged and remains the default-selected tab), and no already-applied writes depend on this code existing — a revert simply removes the second upload option and the explainer/error-help text, returning to the pre-existing tool exactly as it was.

## 9. Verification performed

- [x] Automated tests run (unit): backend — `pytest tests/test_admin_tax_id_import.py tests/test_build_legacy_tax_id_csv.py tests/test_admin_legacy_sin_dob_backfill.py --no-cov -q` → 40 passed. Frontend — `npx vitest run` (full suite) → 593 passed (64 files), including the new `tax-id-error-help.test.ts` (5 tests).
- [x] **Real production build run**: `npm run build` (admin-dashboard) completed with no errors; `/dashboard/bulk-operations` compiled successfully. Not just a dev server or `tsc --noEmit` (though both were also run clean beforehand as a fast first check).
- [x] Blast-radius grep performed: confirmed `_apply_plan`, `build_rows`, `LegacyTaxIdImport.tsx`, and `tax-id-error-help.ts` have no other callers/importers beyond what's listed in section 4.
- [x] Reviewed against `CLAUDE.md` conventions: PIPEDA (no SIN/GST/phone value ever appears in a report, response, or the new error-help strings — all fixed, non-data-bearing text), the query-filter/DB-write conventions (no new DB write path — reuses existing `_apply_plan`), the dual-import pattern (new imports follow it).
- [ ] Manual repro in staging: not run — this environment has no staging deployment to click through; verification is unit/build-level only (see below).
- [ ] Feature-flagged: not applicable — internal admin tool, not a consumer-facing or shared-component surface with mid-session visibility.

## What was NOT verified

- Not exercised against a real Supabase dev environment or real Stripe — verified only against mocked `db_supabase`/`create_support_ticket`-style dependencies in unit tests, consistent with this tool's existing test suite (no integration tier exists for this route).
- Not manually clicked through in a running admin-dashboard instance — verification is `npm run build` + `vitest` + backend `pytest` only, no live browser session in this environment.
- Did not test the raw-export path against a real, full-size Mongo export (hundreds/thousands of rows) — only small synthetic fixtures. The 2 MB / 2,000-row per-file limits are copied from the already-shipped `legacy_sin_dob_backfill.py` sibling's own limits, not independently re-derived.
- Did not extend this redesign to any of the other ~18 migration tools on the Bulk Import page — this is explicitly scoped as a pilot on Tax-ID Backfill only, per the task.
- No screenshot/visual-regression check — `bulk-operations` is not one of the 6 pages `admin-dashboard`'s seeded Playwright visual-regression suite covers (`login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`, `dashboard-rides`), so this change has no automated visual coverage at all; reasoned about via the production build's successful compile and the component's own logic, not screenshotted.

## Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, purely additive change)
- [x] Blast radius is stated, not assumed (isolated to this one tool; confirmed via grep for every changed/added function and component)
- [x] No silent behavior change to an already-shipped flow (the original single-CSV tab is functionally unchanged; UX effect on it is limited to added-but-optional plain-language copy)
