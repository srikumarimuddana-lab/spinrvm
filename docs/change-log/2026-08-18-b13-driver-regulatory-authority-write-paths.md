# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md B13 |

## 1. Issue / gap identified

B13's own text described a completed backfill (migration 265, 2026-07-28)
and a still-pending guard-tightening in `sgi_forms.py`'s
`_out_of_scope_drivers()`. Before doing that tightening, checked the real
production `drivers` table (read-only) to confirm it was still safe —
and found it wasn't: **212 drivers now exist (was 209 at the 2026-07-28
backfill), and 7 of the new ones have NULL `regulatory_authority` again.**
Tightening the guard as originally scoped would have immediately blocked
these 7 real Saskatchewan drivers from SGI forms — the exact regression
the guard's own code comment warns against.

## 2. Root cause

Three separate driver-row-creation write paths never set
`regulatory_authority`/`regulatory_region` at all:
- `backend/routes/drivers/profile.py`'s `PUT /me` auto-create branch (a
  new driver's first profile write)
- `backend/routes/drivers/profile.py`'s `POST /register` new-row branch
  (the primary become-driver flow — the one that actually produced the 7
  NULL rows found live, confirmed via `created_at`/`service_area_id`
  matching real Saskatoon/Regina signups)
- `backend/documents.py`'s `link_driver_document` auto-create branch
  (uploading a document before completing vehicle-info)

Only the bulk CSV-import path (`driver_import_service.py`) ever called the
resolver that computes these two fields. A fourth write path,
`backend/routes/drivers/location.py`'s admin/internal `POST /drivers`, has
the same gap even though no evidence exists it's been used to create a
NULL row in practice (it's admin-gated, likely rarely if ever called) —
fixed anyway since it's the same bug class and a one-line fix.

## 3. Fix / remediation

- New shared helper `_resolve_regulatory_defaults(service_area_id)` in
  `backend/routes/drivers/_shared.py`. Resolves the service area (if an id
  was supplied) and delegates to `driver_import_service.
  regulatory_authority_defaults` — the same resolver the bulk-import path
  already uses, so all write paths now agree on one piece of logic. Falls
  back to an explicit `("SGI", "SK")` only when that resolver still can't
  determine a region (no `service_area_id`, or a stale one) — correct
  today since 0 of 212 production drivers are outside Saskatchewan.
  **Caught by the migration reviewer's WARNING**: this fallback would
  mis-tag a real future non-SK driver if that province's own
  `service_areas` rows are still unpopulated at signup — accepted for now
  (single-market product), flagged as a follow-up needed before a second
  province launches (see §9 below).
- Wired into all four driver-creation write paths listed above. In every
  case, `regulatory_authority`/`regulatory_region` are placed into the
  insert dict *before* the `**updates`/`**payload` spread — and neither
  field is in any of the four endpoints' own client-writable allow-lists,
  so a client can never override the computed value.
- New migration `333_drivers_regulatory_authority_backfill_round2.sql`
  backfills the 7 specific rows the bug already produced (scoped by
  explicit `id IN (...)`, matching migration 265's precedent exactly — see
  that file for the safety reasoning). Does **not** touch the
  `sgi_forms.py` guard — tightening it is intentionally left for a later,
  separate step once this backfill is confirmed applied in production.

## 4. Risk & impact on existing functionality

- **Blast radius, fully enumerated**: grepped every `insert_one("drivers"`
  / `.table("drivers").insert(` call in the backend. Four found and fixed
  (the three above + `location.py`'s admin create). One more,
  `services/data_transfer/entity_import_service.py`'s cross-environment
  bundle-import commit, was **deliberately left untouched** — it copies an
  existing driver profile's own fields verbatim from the source bundle,
  including `regulatory_authority` if the source already has it; forcing
  a default there would be wrong for a genuine future cross-province
  import, unlike the blank-signup case these four paths handle. The bulk
  CSV `driver_import_service.py` path was already correct before this
  change (unmodified).
- **No override risk**: confirmed for each of the four fixed endpoints
  that `regulatory_authority`/`regulatory_region` aren't in the
  client-writable field allow-list, so the computed value always wins.
- **No performance risk**: one extra `get_rows("service_areas", ...)`
  call only when `service_area_id` is present, on a cold driver-creation
  path (not a hot loop, not dispatch-adjacent).
- **Migration 333**: reviewed by `spinr-migration-reviewer` —
  **SAFE TO APPLY**, no blockers. Scoped by explicit id (same pattern as
  265), idempotent (`AND regulatory_authority IS NULL` — no-op if a human
  already hand-fixed one of the 7 via admin action first), documented
  rollback that doesn't newly block any driver (guard still tolerates
  NULL as of this migration).

## 5. User-experience effect

None today — this only populates two internal fields that gate SGI-form
generation (an admin/back-office action), not anything a rider or driver
sees. It prevents a *future* UX regression: without this fix, any driver
signing up after this point would have kept silently accumulating the
same NULL gap, and the guard could never be safely tightened.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/_shared.py` | New `_resolve_regulatory_defaults()` helper | Single source of truth for all write paths |
| `backend/routes/drivers/profile.py` | Both driver auto-create branches now set `regulatory_authority`/`regulatory_region` | Close the two busiest write paths |
| `backend/documents.py` | Document-upload auto-create branch now sets the two fields | Third live write path with the same gap |
| `backend/routes/drivers/location.py` | Admin `POST /drivers` now sets the two fields | Same bug class, for completeness |
| `backend/migrations/333_drivers_regulatory_authority_backfill_round2.sql` | New — backfills the 7 rows the bug already produced | Restore production to a consistent state |
| `backend/tests/test_drivers_extended.py` | 8 new tests (auto-create, register, create_driver, resolver unit tests) | Coverage for every fixed path |
| `backend/tests/test_documents.py` | 1 new test | Coverage for the `documents.py` path |

## 7. Before / after

```python
# Before (routes/drivers/profile.py, PUT /me auto-create)
new_driver = {
    "id": str(uuid.uuid4()),
    ...
    "created_at": datetime.now(timezone.utc).isoformat(),
    **updates,
}
```

```python
# After
_reg_authority, _reg_region = await _shared._resolve_regulatory_defaults(updates.get("service_area_id"))
new_driver = {
    "id": str(uuid.uuid4()),
    ...
    "created_at": datetime.now(timezone.utc).isoformat(),
    "regulatory_authority": _reg_authority,
    "regulatory_region": _reg_region,
    **updates,
}
```

## 8. Rollback plan

- **Code**: `git revert` is safe — the four fixed endpoints simply stop
  setting two extra fields; no other code reads them synchronously in a
  way that would break if they went missing again (the `sgi_forms.py`
  guard already tolerates NULL, unchanged by this PR).
- **Migration 333**: documented in the migration's own header comment —
  `UPDATE ... SET regulatory_authority = NULL, regulatory_region = NULL
  WHERE id IN (<7 ids>)` — restores the exact pre-migration grandfathered
  state; safe because the guard still tolerates NULL.

## 9. Verification performed

- [x] Live, read-only production query (`soavhtdhefowwvforzwb`) confirming
  the 7 NULL rows, their `created_at`/`service_area_id`, and that all
  resolve to real Saskatchewan service areas (Saskatoon/Regina) — same
  verification rigor migration 265 used
- [x] `spinr-migration-reviewer` review: SAFE TO APPLY, no blockers. One
  WARNING (single-market fallback needs revisiting before a second
  province launches) — not a blocker for this PR, noted for a future
  ACTION_ITEMS entry
- [x] 350 tests pass across every touched/adjacent test file (drivers,
  documents, driver_import_service, dual-import-parity)
- [x] New tests specifically prove: (a) each of the 4 fixed paths sets
  both fields, (b) the resolver's fallback behavior for no-service-area,
  explicit-authority, name-match-only, and unresolvable-id cases, (c) the
  existing-driver/duplicate-phone paths are unaffected (resolver not
  invoked)
- [x] Caught and fixed a real bug in my own first draft of the fallback
  logic during test-writing: `if not authority or not region: authority,
  region = authority or "SGI", region or "SK"` only overrode the
  already-falsy field, leaving a wrongly-computed non-empty `authority`
  value (`"Provincial / municipal authority"`) in place when region alone
  was unresolved — fixed to override both fields together when region is
  unresolved, since an unresolved region means the authority computed
  alongside it is unreliable too
- [x] `ruff check` / `ruff format --check` clean on every touched file

## 10. What was NOT verified

- **Migration 333 has not been applied to production in this session** —
  only prepared and reviewed. It follows this repo's normal manual-apply
  process (`python -m backend.scripts.run_migrations` against
  `DATABASE_URL`), same as migration 265 before it — not run via Supabase
  MCP directly, consistent with this session's read-only MCP usage
  elsewhere.
- **`sgi_forms.py`'s guard tightening is explicitly NOT part of this
  PR** — it's still gated on confirming migration 333 has actually been
  applied to production; doing it in the same PR would risk the guard
  landing before the backfill runs, live-blocking real drivers again.
- **The single-market fallback's edge case (a real second-province driver
  with an unresolvable service area)** is accepted-as-correct for today's
  product but not stress-tested against real non-SK data, since none
  exists yet.
