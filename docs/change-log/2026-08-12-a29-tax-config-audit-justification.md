# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code (session request) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | `ACTION_ITEMS.md` A29, first finding ("No audit trail on the tax-rate admin endpoints themselves") |

## 1. Issue / gap identified

A tax-rate change (GST/PST/HST) carries real regulatory/financial weight (every
rider's charge, CRA/SK remittance obligations) but had no written-justification
requirement and, on the two dedicated `/areas/{id}/tax` endpoints the audit
flagged, no audit-log entry at all.

## 2. Root cause

The audit's framing assumed the two dedicated tax endpoints
(`features.py`'s `pricing_router.put("/areas/{area_id}/tax")` and
`routes/admin/service_areas.py`'s `admin_update_area_tax`) were the live
tax-editing surface. Investigation found neither is actually reachable from
any frontend today — grepped every `.tsx` file across `admin-dashboard`,
`rider-app`, `driver-app`, `shared`: zero callers of either path. The
admin-dashboard's real tax editor is the service-areas page's inline
`FieldInput`, which calls `updateServiceArea` → `PUT /api/admin/
service-areas/{area_id}` (`admin_update_service_area`). That endpoint
**does** already write a generic `service_area_updated` audit-log entry
listing `updated_fields` whenever gst/pst/hst change through it — so the
live path was not actually silent, just not held to the same
written-justification discipline the surge-above-cap override already has.

## 3. Fix / remediation

- `admin_update_service_area` (`routes/admin/service_areas.py`, the live
  path): added a `tax_justification` field to `ServiceAreaUpdateRequest`.
  When any of `gst_enabled`/`gst_rate`/`pst_enabled`/`pst_rate`/
  `hst_enabled`/`hst_rate` is present in the request, a non-empty
  justification is now required (400 if missing) and a dedicated
  `tax_config_updated` audit-log entry is written (in addition to the
  existing generic `service_area_updated` entry), mirroring the exact
  `surge_override_above_cap` pattern already in this function.
- `admin-dashboard/src/app/dashboard/service-areas/page.tsx`:
  `handleFieldUpdate` now prompts (`window.prompt`, same convention as the
  existing corporate-wallet-adjustment "Reason (required)" prompt) for a
  justification before saving any of the six tax fields, and sends it as
  `tax_justification`. Non-tax fields are unaffected — no prompt, no new
  required param.
- The two dedicated-but-unreachable tax endpoints
  (`features.py::update_area_tax`, `routes/admin/service_areas.py::
  admin_update_area_tax`) were hardened with the same justification + audit
  requirement, for consistency and in case either gets wired up later — not
  because they carry live traffic today.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (admin-dashboard tax editing) plus two
  isolated, currently-dead backend endpoints.** Grepped for every other
  caller of `admin_update_service_area`, `update_area_tax`, and
  `admin_update_area_tax` before changing them — only the one live caller
  found (`admin-dashboard`'s service-areas page, via `updateServiceArea`).
  No rider-app/driver-app code, no background loop, no other admin-dashboard
  page touches any of the three.
- **What could regress:** any *other* field edit on the service-areas page
  that goes through the same `handleFieldUpdate`/`admin_update_service_area`
  path (name, airport fee, active toggle, etc.) — verified these are
  unaffected because the justification gate only triggers when a tax field
  is actually present in the request (`_tax_fields_touched` check), pinned
  by a new regression test (`test_non_tax_field_update_does_not_require_justification`).
- A tax-rate edit through the admin-dashboard UI now requires an extra
  prompt step before it saves — a **behavior change to a live-tested
  flow** (an admin who previously edited GST/PST/HST inline with no prompt
  will now be asked for a reason). This is the intended effect of the fix,
  not a side effect.

## 5. User-experience effect

- **internal admin only.** An admin editing a service area's GST/PST/HST
  rate via the inline field editor now sees a browser `prompt()` asking for
  a reason before the save goes through; cancelling or leaving it blank
  cancels the edit (no partial save). No rider/driver/corporate-admin-facing
  change — tax rates themselves are unchanged, only the admin write-path
  gained a justification requirement.
- Not mid-session-visible to any rider/driver (backend-admin-only surface).
- No notification/copy change beyond the new prompt text.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/service_areas.py` | Added `tax_justification` to `ServiceAreaUpdateRequest` and `AreaTaxRequest`; added justification-required + `tax_config_updated` audit-log block to `admin_update_service_area` (live path) and `admin_update_area_tax` (dead path, hardened for consistency) | Close A29's audit-trail finding on the actual live tax-editing path, plus the two endpoints the audit named |
| `backend/features.py` | Added `log_admin_action` import; added `tax_justification` to `UpdateTaxConfigRequest`; added justification-required + audit-log block to `update_area_tax` (dead path, hardened for consistency) | Same fix on the second dedicated-but-unreachable endpoint the audit named |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | `handleFieldUpdate` prompts for and sends `tax_justification` when a tax field is edited | Live UI must supply the now-required field or every tax edit would 400 |
| `backend/tests/test_admin_service_areas_coverage.py` | New tests: justification required/accepted for both `admin_update_service_area` (live) and `admin_update_area_tax` (dead); non-tax-field edit unaffected | Pin the new behavior and the "doesn't affect unrelated fields" invariant |
| `backend/tests/test_features.py` | New `TestAreaTaxJustification` class: justification required/accepted/empty-payload-skip for `update_area_tax` | Same coverage for the `features.py` endpoint |

## 7. Before / after

```python
# Before (routes/admin/service_areas.py, admin_update_service_area)
if update_data:
    await db_supabase.update_one("service_areas", {"id": area_id}, update_data)
    await log_admin_action(admin, "service_area_updated", "service_areas", area_id,
                            {"updated_fields": list(update_data.keys())})

# After — a tax field present now requires justification and gets its own
# audit entry before the generic update proceeds:
_tax_fields_touched = [f for f in _TAX_FIELDS if getattr(area, f) is not None]
if _tax_fields_touched:
    tax_justification = (area.tax_justification or "").strip()
    if not tax_justification:
        raise HTTPException(400, detail="Changing GST/PST/HST configuration requires "
                                         "a written justification (regulatory + financial risk).")
    await log_admin_action(admin, "tax_config_updated", "service_areas", area_id,
                            {"updated_fields": _tax_fields_touched, "justification": tax_justification})
# ... existing update_payload/service_area_updated logic unchanged below
```

```tsx
// Before (service-areas/page.tsx)
const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
  try {
    await updateServiceArea(areaId, { [field]: value });
    ...

// After
const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
  try {
    const payload: Record<string, any> = { [field]: value };
    if (TAX_FIELDS.has(field)) {
      const justification = window.prompt("Reason for this tax-configuration change (required):")?.trim();
      if (!justification) return;
      payload.tax_justification = justification;
    }
    await updateServiceArea(areaId, payload);
    ...
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no `app_settings` flag, no Stripe/wallet
state involved — the change only adds a request-time validation gate and an
audit-log write. Reverting the backend commit removes the justification
requirement (tax edits work exactly as before); reverting the frontend commit
removes the prompt. No data-level remediation needed since nothing written
by this change needs undoing (the `audit_logs` rows themselves are
append-only history, not state — leaving them after a revert is harmless).

## 9. Verification performed

- [x] Automated tests run (unit): `test_admin_service_areas_coverage.py`
  (37/37, up from 33 — 4 new: tax-justification-required/accepted on both
  `admin_update_service_area` and `admin_update_area_tax`, plus the
  non-tax-field-unaffected regression), `test_features.py` (27/27, up from
  24 — 3 new: `TestAreaTaxJustification`), `test_surge_reset_to_auto.py`
  (2/2), `test_calculate_all_fees_tax.py` (3/3) — all re-run together, 0
  failed.
- [ ] Manual repro steps followed in staging — not done, no staging
  environment available to this session (see `ACTION_ITEMS.md` E1).
- [x] Blast-radius grep performed: every `.tsx` caller of
  `updateServiceArea`/`updateAreaTax`/`getAreaTax` across
  `admin-dashboard`/`rider-app`/`driver-app`/`shared`, and every Python
  caller of `admin_update_service_area`/`update_area_tax`/
  `admin_update_area_tax` in `backend/routes/` and `backend/features.py` —
  confirms the stated single-live-caller blast radius.
- [x] Reviewed against relevant `CLAUDE.md` convention: mirrors the existing
  surge-above-cap justification pattern exactly (same request-shape,
  same 400-on-missing, same `log_admin_action` call shape).
- [ ] Feature-flagged — not applicable; this narrows an existing admin write
  path with a validation gate, not a new user-visible feature warranting a
  dark-launch flag.
- [x] **Real production build run**: `npm run build` in `admin-dashboard`
  — clean, full route manifest printed, `/dashboard/service-areas` built
  successfully. `npx tsc --noEmit` — 0 errors. `vitest run` — 160/160
  passed, 20/20 files (unchanged from before this fix — no existing test
  exercises this specific inline field-edit flow, so nothing needed
  updating there, but nothing regressed either).

## What was NOT verified

- No manual click-through of the actual `window.prompt()` UX in a running
  browser — verified by code read + `tsc`/build/existing-suite pass only,
  consistent with this repo's standing "no visual/snapshot regression
  tooling exists" gap for admin-dashboard UI changes.
- The two now-hardened-but-still-unreachable endpoints
  (`features.py::update_area_tax`, `routes/admin/service_areas.py::
  admin_update_area_tax`) were verified at the unit-test layer only (direct
  function calls with mocked deps) — not exercised via an actual HTTP
  request, since nothing in any frontend currently sends one.
- Whether `AreaTaxRequest`'s dedicated-endpoint version and
  `ServiceAreaUpdateRequest`'s live-endpoint version should eventually be
  consolidated (two near-duplicate tax field lists) — out of scope for this
  fix, which only closed the audit-trail gap the finding named.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
  remediation needed).
- [x] Blast radius is stated, not assumed (grepped, documented above).
- [x] No silent behavior change to an already-shipped flow without the UX
  field filled in — the new `window.prompt()` step is called out explicitly
  above as the intended, not incidental, effect.
