# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/a29-tax-audit-trail` |
| Related issue or gap ID | ACTION_ITEMS.md A29, sub-finding "No audit trail on the tax-rate admin endpoints themselves" |

## 1. Issue / gap identified

Two separate admin endpoints let an admin change a service area's GST/PST/HST
rate or enabled flags — `PUT /api/v1/areas/{area_id}/tax`
(`backend/features.py`'s `pricing_router`) and
`PUT /api/admin/areas/{area_id}/tax` (`backend/routes/admin/service_areas.py`)
— and neither wrote an `audit_logs` row nor required a justification string,
unlike the analogous surge-cap override path in
`admin_update_service_area` (same file), which requires a written
justification and records a `surge_override_above_cap` audit row for any
multiplier above `SURGE_CAP`.

## 2. Root cause

The two tax endpoints were built as plain CRUD-style admin mutations (get →
merge non-null fields → write) before the surge-cap audit pattern was
introduced elsewhere in the codebase; the audit requirement was never
retrofitted onto them. This was compounded by the actual 2026-08-11 PST
enablement event (ACTION_ITEMS.md A27), where the real rate change was applied
via direct Supabase access, bypassing both endpoints entirely and leaving no
in-band audit trail (mitigated after the fact with 4 retroactive `audit_logs`
rows). That incident didn't create this gap, but it's why the *next* tax-rate
change made through the normal admin UI needed this fix before it ships.

## 3. Fix / remediation

Both endpoints now require a non-blank `justification: str` field on their
request body (Pydantic-required, so a missing field 422s before the handler
runs; a present-but-blank/whitespace-only string 400s inside the handler) and
write a `tax_rate_changed` row to `audit_logs` via the existing
`log_admin_action(admin, action, resource, resource_id, details)` helper
(`backend/utils/audit_logger.py`) on every successful change — capturing
`admin["id"]`/`admin["role"]` (via `log_admin_action`'s own `actor_id`/
`actor_role` fields), `area_id`, the pre-change rate/enabled values ("old"),
the post-change values ("new"), and the justification string. This is the
exact same helper, action-naming convention, and required-justification shape
already used by `admin_update_service_area`'s `surge_override_above_cap` path
— no new audit-write mechanism was introduced.

`backend/routes/admin/service_areas.py`'s `admin_update_area_tax` previously
had no `admin: dict = Depends(get_admin_user)` parameter at all (the route was
still auth-gated at the router-mount level in `routes/admin/__init__.py`, just
without access to the caller's identity inside the handler) — added so the
handler can pass the actor into `log_admin_action`.

**Incidental fix, directly caused by this restructuring (not a separate
change):** `admin_update_area_tax` previously fetched the post-update row
only, and returned `area.get(k)` for each tax field with no null-check — if
`area_id` didn't exist, `area` was `None` and this raised an unhandled
`AttributeError` (an unclean 500) instead of a clean error. Capturing the
*pre-change* row to build `old_values` for the audit log means that row is
now fetched up front regardless, so a `None`-guard there (and the equivalent
guard already present on the post-update fetch, mirroring the sibling
`pricing_router` endpoint's existing 404 check) was essentially free and is
included here rather than left as a newly-reintroduced landmine. This is the
one place this PR changes error-path behavior beyond adding the audit trail
itself; covered by a new `test_update_area_tax_area_not_found_is_404_without_audit` test.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** Both endpoints are pure read-modify-write mutators
of `service_areas`' six tax columns (`gst_enabled`, `gst_rate`, `pst_enabled`,
`pst_rate`, `hst_enabled`, `hst_rate`). Grepped for every other caller/reader:

- **`backend/features.py`'s `update_area_tax`** — no other backend module
  imports or calls this function directly (`grep -rn "update_area_tax\b"
  backend --include=*.py`: only its own definition and the new test file).
  Its route is mounted once, on `pricing_router` → `v1_api_router` →
  `/api/v1/areas/{area_id}/tax`. No admin-dashboard client wrapper targets the
  `/api/v1/...` (non-`/admin`-prefixed) path — grepped
  `admin-dashboard/src` for `/areas/${...}/tax` and found only the
  `/api/admin/...` variant below.
- **`backend/routes/admin/service_areas.py`'s `admin_update_area_tax`** — no
  other backend module calls this function directly. Its route is mounted
  twice (`admin_router` included both under `v1_api_router` with `/api/v1`
  and directly with `/api`), giving `/api/v1/admin/areas/{id}/tax` and
  `/api/admin/areas/{id}/tax`. The admin-dashboard has a client wrapper for
  the latter (`admin-dashboard/src/lib/api/content-area.ts`'s
  `updateAreaTax(areaId, data)`, re-exported from `lib/api.ts`) **but it is
  not called from any page or component** — grepped
  `admin-dashboard/src` for `updateAreaTax\b` and the only two hits are the
  definition and its re-export; no UI currently drives this endpoint. This
  means the new required-`justification` field cannot break any live
  in-app flow today, but it does mean the admin-dashboard UI will need a
  justification input added before this endpoint is wired up to a screen —
  noted as a follow-up, not part of this change.
- **`AreaTaxRequest` / `UpdateTaxConfigRequest`** (the two Pydantic request
  models) — grepped for every other construction site; only the two route
  handlers and this PR's own tests instantiate them.
- **`log_admin_action` / `audit_logs` table** — the helper is already used
  throughout `routes/admin/service_areas.py` (`service_area_created`,
  `surge_override_above_cap`, `service_area_deleted`, and others) and across
  many other admin routes repo-wide. This change adds one more call site
  (`tax_rate_changed`) with its own distinct `action` string; it does not
  modify `log_admin_action` itself, the `audit_logs` schema, or any other
  caller's behavior. `log_admin_action` already swallows its own write
  failures (logs `logger.error(...)`, returns `None`) rather than raising —
  unchanged, so a transient `audit_logs` write failure still cannot roll back
  or block the underlying tax-rate write, consistent with every other
  `log_admin_action` call site in the codebase.
- **Fare calculation / rider receipts** — untouched. Neither endpoint's
  read/write shape for `gst_enabled`/`gst_rate`/`pst_enabled`/`pst_rate`/
  `hst_enabled`/`hst_rate` changed; `services/fare_service.py` and the
  receipt renderer read `service_areas` rows the same way as before.

No ride state, wallet/Stripe, or background-loop code paths are touched by
this change.

## 5. User-experience effect

Admin-only, additive, no rider/driver/corporate-facing effect. For an admin
calling either endpoint: a request that previously succeeded with no
`justification` field will now fail — a request body missing the field gets a
422 from FastAPI's request validation, and a request with a
present-but-blank/whitespace `justification` gets a 400 with an explanatory
`detail` message — the same posture the surge-cap override endpoint has had
in this codebase already. Since (per the blast-radius grep above) no
admin-dashboard UI currently calls either endpoint, this is not a mid-session
break for any existing screen; it only affects a hypothetical direct API
caller (e.g. a script, Postman, or a future UI) that isn't in this repo today.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | Added `log_admin_action` to the dual try/except imports; added a required `justification: str` field to `UpdateTaxConfigRequest`; `update_area_tax` now takes `admin: dict = Depends(get_admin_user)`, validates a non-blank justification (400 if blank), captures old/new tax values, and writes a `tax_rate_changed` `audit_logs` row via `log_admin_action` before returning | A29: audit trail + required justification on the `pricing_router` tax endpoint, mirroring the surge-cap override pattern |
| `backend/routes/admin/service_areas.py` | Added a required `justification: str` field to `AreaTaxRequest`; `admin_update_area_tax` now takes `admin: dict = Depends(get_admin_user)`, validates a non-blank justification (400 if blank), fetches the pre-change row and 404s cleanly if the area doesn't exist (previously an unguarded `AttributeError`/500 — see section 4), excludes `justification` from the DB update payload (it is not a `service_areas` column), captures old/new tax values, and writes a `tax_rate_changed` `audit_logs` row via the already-imported `log_admin_action` before returning | A29: same fix on the `routes/admin/service_areas.py` tax endpoint |
| `backend/tests/test_admin_service_areas_coverage.py` | Updated `TestAreaTax`'s two existing `admin_update_area_tax` tests to pass `justification=` and `admin=_ADMIN`, and to assert `log_admin_action` is called with old/new/justification; added three new tests for missing-field (422 via Pydantic), blank-justification (400, no write, no audit), and area-not-found (404, no audit) | Keep existing coverage passing under the new required field; cover the new validation paths |
| `backend/tests/test_features_area_tax_audit.py` (new) | New test file covering `update_area_tax` in `backend/features.py`: audit row written with old/new/justification, empty-field payload still audits, missing-justification 422, blank-justification 400 with no write, area-not-found 404 with no audit | This endpoint had zero prior test coverage for the tax-write path; A29 requires tests for the new audit behavior |
| `docs/change-log/2026-08-12-a29-tax-endpoint-audit-trail.md` (this file) | New Change Impact & Risk Log entry | Mandatory per `CLAUDE.md` for any change to a live-tested/regulatory-adjacent admin surface |

## 7. Before / after

`backend/features.py` — `update_area_tax`:

```python
# before
@pricing_router.put("/areas/{area_id}/tax")
async def update_area_tax(area_id: str, req: UpdateTaxConfigRequest):
    """Update tax configuration for a service area."""
    update_data: Dict[str, Any] = {}
    for field in ["gst_enabled", "gst_rate", "pst_enabled", "pst_rate", "hst_enabled", "hst_rate"]:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    if update_data:
        await db_supabase.update_one("service_areas", {"id": area_id}, update_data)

    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    return {...}
```

```python
# after
@pricing_router.put("/areas/{area_id}/tax")
async def update_area_tax(area_id: str, req: UpdateTaxConfigRequest, admin: dict = Depends(get_admin_user)):
    """Update tax configuration for a service area. ... (A29 docstring)"""
    justification = (req.justification or "").strip()
    if not justification:
        raise HTTPException(status_code=400, detail="Tax rate changes require a written justification ...")

    existing = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not existing:
        raise HTTPException(status_code=404, detail="Service area not found")
    old_values = {f: existing.get(f) for f in _TAX_FIELDS}

    update_data: Dict[str, Any] = {}
    for field in _TAX_FIELDS:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val
    if update_data:
        await db_supabase.update_one("service_areas", {"id": area_id}, update_data)

    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    new_values = {f: area.get(f) for f in _TAX_FIELDS}

    await log_admin_action(
        admin, "tax_rate_changed", "service_areas", area_id,
        {"old": old_values, "new": new_values, "justification": justification},
    )
    return {...}
```

`backend/routes/admin/service_areas.py`'s `admin_update_area_tax` follows the
identical before/after shape (required justification → 400 on blank →
old/new capture → `update_one` with `justification` excluded from the payload
→ `log_admin_action("tax_rate_changed", ...)`).

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change, no data written to a new
column — `audit_logs` already exists and is already written to by many other
call sites; this only adds one more `action` string (`tax_rate_changed`) to
that existing table. Reverting the commit removes the `justification`
requirement and the audit write and restores prior endpoint behavior exactly;
no live data (rates already stored on `service_areas`, or already-written
`audit_logs` rows) needs cleanup either way. If a hotfix is needed faster than
a revert-deploy, no feature flag is required — the endpoints were not in use
by any UI (see blast-radius section), so there is no in-flight caller to
protect.

## 9. Verification performed

- Ran `pytest tests/test_features_area_tax_audit.py tests/test_admin_service_areas_coverage.py -q --no-cov` via the repo's `/tmp/spinr-venv` interpreter: **41 passed, 0 failed**.
- Ran `pytest tests/test_features.py tests/test_calculate_all_fees_tax.py -q --no-cov` (broader features.py regression check): **27 passed, 0 failed**.
- Ran `ruff check` on both modified route files and both test files: all checks passed.
- Grepped the full backend tree and `admin-dashboard/src` for every caller of both endpoint functions, both request models, and the admin-dashboard client wrapper (`updateAreaTax`) — findings listed in section 4.
- **This is NOT a real Supabase run.** All tests patch `backend.features.db_supabase.*` / `backend.routes.admin.service_areas.db_supabase.*` and `log_admin_action` directly (function-level unit tests calling the route handlers as plain async functions, not through a live FastAPI `TestClient`/HTTP layer, and not against a real or throwaway Supabase schema).

## What was NOT verified

- **No real Supabase integration test.** Never exercised against an actual
  `audit_logs` table or `service_areas` row — only against `AsyncMock`-backed
  `db_supabase.get_rows`/`update_one`, so a real-schema mismatch (e.g. an
  `audit_logs` column type that doesn't accept the `details` shape used here)
  would not be caught by this test run. The `details` JSON shape used
  (`{"old": {...}, "new": {...}, "justification": "..."}`) matches the shape
  already used successfully by the neighboring `surge_override_above_cap`
  call in the same file, which is some indirect evidence but not a
  substitute for a live check.
- **No HTTP-level test** (FastAPI `TestClient` / real request) confirming the
  422-on-missing-field behavior end-to-end — verified indirectly by asserting
  `pydantic.ValidationError` is raised when constructing the model without
  `justification`, which is the mechanism FastAPI's request layer relies on
  to produce the 422, but the actual HTTP response code was not observed.
- **No production build was run** — this is a backend-only Python change; no
  `admin-dashboard`/`rider-app`/`driver-app` files were modified, so `npm run
  build` is not applicable here.
- **No staging/live-Supabase manual repro.** Not tested against a running
  backend instance or Postman/curl call against a deployed environment.
- **Admin-dashboard UI was not updated** to send a `justification` field —
  out of scope per this task's "backend only" constraint. Confirmed via grep
  that no current UI calls either endpoint, so this is a documented follow-up
  rather than a live gap; whoever wires up a tax-editing screen against
  either endpoint next will need to add a justification input at that time.
