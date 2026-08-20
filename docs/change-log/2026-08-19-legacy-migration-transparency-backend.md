# Change Impact & Risk Log — Legacy-migration transparency (backend)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (backend agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers, admin, rides, safety (insurance-period export) |
| PR / commit link | local worktree branch `worktree-agent-adab23e7699c4899c` — not pushed |
| Related issue or gap ID | `docs/audit/2026-08-19-legacy-migration-data-quality-audit.md` — "Not fixed — regulatory" BLOCKER, and "Not fixed — migration data-integrity" (2 findings) + rider `legacy_import_metadata` exposure check |

This entry covers four related, same-session fixes, tracked as five commits:

1. Regulatory BLOCKER: `is_reconstructed` insurance-period rows invisible to the SGI/regulator export.
2. `sin_collected_at` misrepresents provenance for legacy-backfilled SINs (two read paths: admin driver-detail, T4A filer handoff; plus a correctness fix inside the importer's own metadata stamp, found during implementation — see §2 below).
3. Legacy rides' estimated `duration_minutes` has no per-row marker (importer fix only; historical backfill explicitly deferred).
4. Additional check: rider list endpoint didn't expose `legacy_import_metadata` the way the driver list already does.

## 1. Issue / gap identified

1. `scripts/compliance_export.py` (the SGI-subpoena-response tool) never referenced `driver_insurance_periods.is_reconstructed` (migration 332), so a regulator request today would receive reconstructed legacy-import insurance-period rows with no marker distinguishing them from a contemporaneously-logged row.
2. A SIN backfilled from the legacy `banks.csv` import gets the exact same `sin_collected_at` timestamp self-entry produces. Two read paths (admin driver-detail, T4A filer-handoff export) display it at face value with no way to tell provenance apart.
3. When a legacy booking import has no `start_ride_at`, the importer estimates `duration_minutes` from distance and logs a warning at import time, but the committed value is indistinguishable from a real measured duration.
4. The rider-equivalent of the driver list's already-exposed `legacy_import_metadata` was missing from the admin Users list projection.

## 2. Root cause

1. `compliance_export.py`'s embedded-select column list and `redact_row()` were written before migration 332 added `is_reconstructed`, and nothing updated them afterward.
2. `apply_legacy_sin_dob_import` writes `sin_collected_at = now_iso` on any SIN backfill, same as `routes/drivers/profile.py`'s self-entry path — no separate marker existed to read back.
   **Found and fixed during implementation, not scoped in the original ask**: `apply_legacy_sin_dob_import` was *also* stamping `legacy_import_metadata[LEGACY_BANK_SIN_DOB_SOURCE]` unconditionally whenever it wrote *either* SIN or date_of_birth — including a DOB-only backfill for a driver whose SIN was already self-entered. The task's own suggested derivation ("legacy_import if the metadata key is present") would have silently mislabeled that self-entered SIN as legacy-imported. Fixed by adding `sin_written`/`dob_written` booleans to the marker itself (see §3). Confirmed safe to change: per the audit doc, `apply_legacy_sin_dob_import` has never run with `--apply` in production, so no live driver row carries the old marker shape yet.
3. The estimation branch (`duration_minutes = max(1, int(distance_km / FALLBACK_SPEED_KMH * 60) + 5)`) writes straight into the `rides` row with no accompanying flag; only a log warning at import time records that it happened.
4. `_USER_LIST_COLUMNS` in `routes/admin/users.py` was an explicit, restrictive projection (built to keep `profile_image` out of bulk reads) that was never revisited when `legacy_import_metadata` was added elsewhere; the driver-list equivalent (`admin_get_drivers`) has no restrictive projection at all, so it was never blocked there.

## 3. Fix / remediation

1. `scripts/compliance_export.py`: added `is_reconstructed` to the embedded-select columns, `redact_row()`'s output, and `FIELDNAMES` (CSV header). Defaults to `False` if the key is ever absent from a row (defensive; the column is `NOT NULL DEFAULT false` so this should not occur in practice).
2. Added `services.driver_import_service.sin_source(driver) -> "legacy_import" | "self_entry" | None`, a pure derived-field function reading `legacy_import_metadata` + `sin_collected_at`. Wired into:
   - `admin_get_driver_live_stats` (admin driver-detail slideout) — new `sin_source` response key.
   - `_t4a_filer_handoff_rows` (T4A filer-handoff export) — new `sin_source` CSV/export column; added `legacy_import_metadata` to that function's `drivers` column projection so the data is available to derive from.
   - `apply_legacy_sin_dob_import`'s marker write now records `sin_written`/`dob_written` booleans so `sin_source()` can tell a genuine SIN backfill apart from a DOB-only one.
3. `booking_import_service.py`: stamps `legacy_import_metadata.duration_estimated = true/false` on every imported ride, set from the same `started_at` truthiness check that already gates the estimation branch. Importer-code-path fix only — the next re-import (including the Oct 30 final cutover) gets this marker automatically.
4. Added `legacy_import_metadata` to `_USER_LIST_COLUMNS` in `routes/admin/users.py` (the rider list projection). The single-rider detail endpoint (`admin_get_user_details`) already exposed it via `get_user_by_id`'s `select("*")` — no change needed there.

## 4. Risk & impact on existing functionality

**§1 (`compliance_export.py`)** — Isolated. Grepped every caller/reader: only `backend/tests/test_compliance_export_script.py` imports `redact_row`/`run_export`; the script is invoked standalone via CLI, not imported by any route or service. No other consumer of this module exists.

**§2 (`sin_source`)** — Additive field on two existing API/export responses; no existing field's meaning or value changed (`sin_collected_at` itself is untouched everywhere). Blast radius grepped:
- `sin_source()` itself is a brand-new function with no prior callers.
- The `apply_legacy_sin_dob_import` marker-shape change (`sin_written`/`dob_written` added to the JSONB value under `legacy_import_metadata[LEGACY_BANK_SIN_DOB_SOURCE]`) is read only by the new `sin_source()` and by `test_legacy_sin_dob_import_service.py`'s own assertions on `marker["batch"]` — no other reader inspects that inner dict's shape (confirmed via grep for `LEGACY_BANK_SIN_DOB_SOURCE`, 2 files total: the service and its test). Since this importer has never run `--apply` in production, there is no live data whose marker shape this change is incompatible with.
- `admin_get_driver_live_stats` — grepped for other callers of this function/route; it's the admin driver-detail slideout's own endpoint (`GET /api/admin/drivers/{id}/live-stats`), no other backend caller. Adding a response key does not affect any existing key or status code.
- `_t4a_filer_handoff_rows` / `get_t4a_filer_handoff` — grepped; only called from its own route handler and its test file. Adding a `drivers` column to the query projection (`legacy_import_metadata`) and a new CSV/export column is additive; existing columns/values are unchanged. One existing test (`test_t4a_filer_handoff_never_includes_sin`) enforces an allowlist of SIN-named columns as a PII leak guard — updated to include `sin_source` (a provenance label, never any part of the actual SIN, so the guard's intent still holds) and left the guard mechanism itself unchanged, so it still fails the test if an actual SIN-carrying column is ever added.

**§3 (`duration_estimated`)** — Grepped every backend reader of `rides.legacy_import_metadata`: `services/fare_service.py`, `services/legacy_payout_correction_service.py`, `services/legacy_gst_backfill_service.py`, `utils/legacy_rides.py`, `routes/admin/users.py`, `routes/admin/rides.py`, `routes/rides/rating.py`, `routes/rides/payments.py`, `routes/rides/receipts.py`. All either check truthiness/presence of the dict as a whole, or read one specific, differently-named key — none assume a closed key set. The one dict-equality check, `utils/legacy_rides.EXCLUDE_LEGACY_RIDES = {"legacy_import_metadata": {"$eq": {}}}`, is unaffected: that filter's `== {}` comparison was never true for an imported row regardless of this change, since the dict already carries `batch`/`source`/`old_booking_id`/etc. for every imported ride. Isolated to the importer's write path; does not touch any already-committed ride row (explicitly out of scope — see §"What was NOT verified / deferred" below).

**§4 (rider list `legacy_import_metadata`)** — `_USER_LIST_COLUMNS` has two callers: `admin_get_users` (the list endpoint, returns the projected rows directly — this is where the new field becomes visible) and `admin_export_users` (builds its CSV row from an explicit field-by-field dict, does not spread the row — confirmed by reading the function body — so the export output is unaffected by this change). No other caller of `_USER_LIST_COLUMNS` exists.

**Cross-cutting**: none of these four fixes touch ride state transitions, wallet/allowance deltas, dispatch, or Stripe flows. No background loop (`backend/core/lifespan.py`) reads or writes any of the touched fields.

## 5. User-experience effect

- **Internal admin only.** No rider, driver, or corporate-admin-facing change.
- The admin driver-detail slideout and the T4A filer-handoff export gain a new field/column (`sin_source`); the admin Users list gains a new field (`legacy_import_metadata`) that the admin-dashboard frontend does not yet render (per the audit, this is a known, separately-tracked frontend gap — out of this session's scope, which is `.claude/context` scoped to backend-only).
- Not visible mid-session to anyone actively using the rider/driver apps — these are all admin-only, pull-based read paths (nothing pushed via WebSocket).
- No copy/notification change.
- The SGI compliance export (`scripts/compliance_export.py`) is a CLI tool run on-demand by an admin/ops person responding to a regulator request, not a live in-app surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `scripts/compliance_export.py` | Added `is_reconstructed` to columns, `redact_row()` output, `FIELDNAMES` | Finding #1 — regulator export must distinguish reconstructed insurance-period rows |
| `backend/tests/test_compliance_export_script.py` | Added `is_reconstructed` cases (true/default-false), CSV header assertion | Test coverage for #1 |
| `backend/services/driver_import_service.py` | Added `sin_source()`; added `sin_written`/`dob_written` to the `LEGACY_BANK_SIN_DOB_SOURCE` marker dict | Finding #2 — derive SIN provenance without changing `sin_collected_at`'s meaning; fix a mislabeling trap found while implementing the derivation |
| `backend/tests/test_legacy_sin_dob_import_service.py` | Added `sin_written`/`dob_written` assertions; added a DOB-only-backfill regression test; added `sin_source()` unit tests including the self-entry-mislabeled-as-legacy regression | Test coverage for #2 |
| `backend/routes/admin/drivers.py` | Wired `sin_source(drv)` into `admin_get_driver_live_stats`'s response | Finding #2 — admin driver-detail read path |
| `backend/tests/test_admin_drivers_coverage.py` | Added 3 tests: self_entry, legacy_import, None cases for `sin_source` in the live-stats response | Test coverage for #2 |
| `backend/routes/admin/compliance.py` | Added `legacy_import_metadata` to the T4A drivers-column projection; added `sin_source` to the exported row + `fieldnames` + subtitle copy | Finding #2 — T4A filer-handoff export |
| `backend/tests/test_compliance_reports_http.py` | Updated the SIN-column PII-leak-guard allowlist to include `sin_source`; added a legacy-provenance-labeling test | Test coverage for #2, keeps the existing leak guard intact |
| `backend/services/booking_import_service.py` | Stamp `legacy_import_metadata.duration_estimated` (true/false) on every imported ride | Finding #3 — importer-code-path fix |
| `backend/tests/test_booking_import_service.py` | Added assertions on `duration_estimated` for both the measured and estimated-duration cases | Test coverage for #3 |
| `backend/routes/admin/users.py` | Added `legacy_import_metadata` to `_USER_LIST_COLUMNS` | Rider-side exposure check — match the driver list's existing exposure |
| `backend/tests/test_admin_users_management.py` | Added a test asserting the rider list endpoint projects and returns `legacy_import_metadata` | Test coverage for the rider-exposure fix |

## 7. Before / after

**`scripts/compliance_export.py` — `redact_row()`** (additive field, not a behavior change to existing fields):
```python
# Before
return {
    "ride_id": period_row.get("ride_id"),
    ...
    "total_fare_cad": _money(ride.get("total_fare")),
}

# After
return {
    "ride_id": period_row.get("ride_id"),
    ...
    "total_fare_cad": _money(ride.get("total_fare")),
    "is_reconstructed": bool(period_row.get("is_reconstructed", False)),
}
```

**`driver_import_service.apply_legacy_sin_dob_import` — marker write** (behavior-changing: the stamped JSONB shape gains two keys; no production data exists in the old shape yet, see §4):
```python
# Before
meta[LEGACY_BANK_SIN_DOB_SOURCE] = {"batch": batch, "imported_at": now_iso}

# After
meta[LEGACY_BANK_SIN_DOB_SOURCE] = {
    "batch": batch,
    "imported_at": now_iso,
    "sin_written": bool(plain_sin),
    "dob_written": bool(upd.get("date_of_birth")),
}
```

**`booking_import_service.py` — ride metadata** (additive field on every future import; does not touch already-committed rows):
```python
# Before
"legacy_import_metadata": {
    "batch": batch, "source": IMPORT_SOURCE, ..., "old_payout_gst_amount": float(payout_gst_amount),
},

# After
"legacy_import_metadata": {
    "batch": batch, "source": IMPORT_SOURCE, ..., "old_payout_gst_amount": float(payout_gst_amount),
    "duration_estimated": not bool(started_at),
},
```

## 8. Rollback plan

All four fixes are pure-additive code changes (new response fields, new export columns, new JSONB keys) with no migration and no mutation of existing data. Rollback is a plain `git revert` of the relevant commit(s) — no feature flag needed because nothing user-visible (rider/driver/corporate) is touched, no live data is written differently in a way that requires a data-level fix, and no already-applied row needs correcting (the booking-importer change only affects rows imported *after* this deploys; the SIN-backfill marker-shape change only affects a backfill that has never run in production).

## 9. Verification performed

- [x] Automated tests run (unit, `pytest -q --no-cov` on each touched test file individually, plus grouped runs):
  - `test_compliance_export_script.py` — 13 tests pass (ran earlier in this session, in the background)
  - `test_legacy_sin_dob_import_service.py` — 22 tests pass
  - `test_admin_drivers_coverage.py`, `test_compliance_reports.py`, `test_compliance_reports_http.py`, `test_driver_sin_collection.py`, `test_admin_tax_id_import.py` — 318 tests pass (grouped run)
  - `test_booking_import_service.py` — 40 tests pass; also ran `test_admin_booking_import.py`, `test_booking_import_rides_numeric_no_float_cast.py`, `test_legacy_tax_basis.py`, `test_payouts_amount_no_float_cast.py` — 49 tests pass
  - `test_admin_users_management.py`, `test_admin_users_search.py`, `test_admin_business_logic.py`, `test_admin_rbac.py`, `test_admin_routes_auth.py` — 93 tests pass
  - No test run against a real backend service — this is a Python/pytest-only repo pass, **not** an `npm run build` case (no `admin-dashboard`/`rider-app`/`driver-app` files were touched).
- [x] Blast-radius grep performed for every touched read/write path (listed in full in §4 above): `redact_row`/`run_export` callers, `LEGACY_BANK_SIN_DOB_SOURCE` readers, `rides.legacy_import_metadata` readers, `_USER_LIST_COLUMNS` callers.
- [x] Reviewed against relevant CLAUDE.md conventions: additive-over-destructive (no migration, no column repurposing), PIPEDA (no PII added to any log/Sentry payload — `sin_source` is a three-value enum, never the SIN; `is_reconstructed` is a boolean), do-not-silently-swallow-errors (no error handling touched).
- [x] `ruff check` run on every touched `.py` file — clean except 4 pre-existing `B904` findings in `routes/admin/drivers.py` at line numbers far from any edit in this session, confirmed pre-existing via `git stash` + re-run before any of this session's commits landed.
- [ ] Feature-flagged: not applicable — internal-admin-only, additive, no live-tested rider/driver/corporate flow touched.

## 10. What was NOT verified / deferred

- **No live Supabase access this session** — all verification is against `mock_supabase_client`/local fakes, per this repo's existing test-tier convention. Nothing was checked against a real database or staging environment.
- **No `npm run build`** — this session touched backend Python only; the admin-dashboard frontend does not yet render the newly-exposed `sin_source`/`legacy_import_metadata` fields (a known, separately-tracked frontend gap per the audit doc, explicitly out of this session's scope).
- **Historical `duration_minutes` backfill is explicitly deferred, not done.** Finding #3's fix only covers the importer code path going forward (so a future re-import, e.g. the Oct 30 cutover, gets the marker automatically). The ~182 already-imported legacy rides whose `duration_minutes` was estimated do **not** get `legacy_import_metadata.duration_estimated` retroactively in this session — per the task's explicit instruction, this was treated as a separate, larger decision (touches already-live ride data) requiring its own sign-off, not silently done as a side effect here. **Flagged as a follow-up**, mirroring the shape of `backfill_legacy_driver_sin_dob.py` (dry-run-default CLI). Not written this session: the task's instructions on this point were internally in tension (one line said "do NOT write a script... in this session," a later optional-stretch line said "you may... write one, but it must default to dry-run and never actually apply") — resolved conservatively toward the more specific "do NOT" instruction rather than the softer stretch-goal phrasing, per this repo's "escalate rather than silently ship" convention when instructions are ambiguous. The SIN/DOB backfill script's plan/apply separation, batch-scoped idempotency key, and write-time `.is_(col, "null")` guard pattern would transfer directly to a `duration_minutes` backfill script if/when this is explicitly commissioned.
- **The consent-basis BLOCKER for imported users** (the other, more serious "Not fixed — regulatory" finding in the audit doc) is out of scope for this session — it is a legal decision, not an engineering one, and was not touched.
- **No visual/snapshot regression tooling exists in this repo** for the admin-dashboard frontend that would eventually render these new fields — not applicable to this session's backend-only diff, but noted per CLAUDE.md's standing-gap convention.
