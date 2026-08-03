# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "geofence policy is configurable in the UI and silently does nothing" |

## 1. Issue / gap identified

`services/corporate_policy_service.py`'s `evaluate_policy_for_ride`
permanently defers the geofence rule ("DEFERRED: always passes; awaiting
PostGIS infrastructure") — a company can set `allowed_geofence` on its
policy and it is silently never enforced.

**Finding premise correction**: the original report described this as a
UI control ("configurable in the UI"). Grepped the entire admin-dashboard
(`company-portal` and `dashboard`) for `allowed_geofence`/`geofence` —
zero references in any `.tsx` file outside the unrelated service-areas
map feature (a different "geofence" concept entirely). The
`company-portal/[id]/policy/page.tsx` form (the actual corporate policy
editor) has fields for active/max-fare/payment-source/time-windows only
— no geofence input exists today. So there is no UI control to hide or
label; `allowed_geofence` is reachable only by calling the API directly
(`PUT`/`PATCH /company/{id}/policy`), which does fully validate and
accept GeoJSON (`_validate_geofence`) and store it, with zero signal to
the caller that it won't be enforced.

## 2. Root cause

The PostGIS-dependent enforcement was deferred (documented clearly in
code comments in `corporate_policy_service.py`), but the API-level
accept-and-store path was never annotated to match — a caller reading
the policy back has no way to know, from the response alone, that a
geofence they set does nothing.

## 3. Fix / remediation

Since there is no UI control to hide/label, the equivalent fix at the
layer that actually exists (the API) is response-level transparency: new
`_annotate_geofence_enforcement()` helper stamps `geofence_enforced:
false` onto the policy dict whenever `allowed_geofence` is set, applied
to all three policy endpoints' responses (`GET`, `PUT`, `PATCH
/company/{id}/policy`). Omitted entirely when no geofence is set, so a
policy that never touches this feature sees no new field at all.

## 4. Risk & impact on existing functionality

- **Blast radius: one new pure helper function, three `return` statements
  in `routes/corporate_company.py`.** No change to `_validate_geofence`,
  `upsert_corporate_policy`, `get_corporate_policy`, or
  `corporate_policy_service.py`'s actual (deferred) enforcement logic.
- Grepped every consumer of the three policy endpoints: the rider Work
  Profile screen (read-only summary display) and the company-portal
  policy page (create/edit form) — neither references `allowed_geofence`
  today (see §1), so neither can be affected by a new field appearing
  only when that unused feature is set.
- Purely additive field, never removes or renames an existing key —
  every existing consumer reading `active`/`max_fare_per_ride`/
  `allowed_payment_source`/`allowed_time_windows` sees them completely
  unchanged.
- `_annotate_geofence_enforcement` never mutates its input in place
  (returns a new dict via `{**policy, ...}`) — verified with a dedicated
  test — so no risk of a shared/cached policy object being silently
  altered elsewhere.

## 5. User-experience effect

None today — no UI reads or displays `geofence_enforced`, since no UI
sets `allowed_geofence` in the first place. This closes the gap for any
direct API caller (a future UI, an integration, or an admin using the API
directly) rather than for an existing screen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company.py` | New `_annotate_geofence_enforcement()`; applied to `get_policy`, `replace_policy`, `patch_policy` (both its early-return and normal-return paths) | Stop silently accepting a geofence that will never be enforced |
| `backend/tests/test_corporate_company_gap_coverage.py` | 4 new tests: GET/PUT flag-when-set, GET no-flag-when-unset, and a unit-level test proving the helper's contract (including no-mutation) | Lock in the annotation behavior |

## 7. Before / after

```python
# Before
@router.get("/policy")
async def get_policy(company_id, guard=Depends(require_company_member)):
    return await get_corporate_policy(company_id) or {}

# After
@router.get("/policy")
async def get_policy(company_id, guard=Depends(require_company_member)):
    return _annotate_geofence_enforcement(await get_corporate_policy(company_id) or {})
```

## 8. Rollback plan

`git revert` the commit. No migration, no data written — the stored
policy row is completely unchanged; this only adds a derived field to
the HTTP response.

## 9. Verification performed

- [x] Grepped the entire admin-dashboard for `allowed_geofence`/`geofence`
      to confirm the finding's UI-control premise doesn't match the
      current codebase (see §1) before deciding the fix's shape.
- [x] 4 new tests: `geofence_enforced: false` appears on `GET`/`PUT`
      responses when a geofence is set, is absent when unset, and a
      unit-level test proving the helper never mutates its input and
      handles the empty-policy case correctly.
- [x] `python3 -c "import ast; ast.parse(...)"` on both touched files —
      clean.
- [x] Confirmed the existing `test_geofence_valid_passes` test (which
      mocks `upsert_corporate_policy` returning `{"id": "p1"}` — no
      `allowed_geofence` key) is unaffected: its only assertion is
      `status_code == 200`, and the annotation is a no-op when the
      *returned* row (not the request body) lacks the field.
- [x] Blast-radius grep performed (see §4): every consumer of the three
      touched endpoints.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — confirmed via grep that no
      current UI reads or writes `allowed_geofence`
- [x] No silent behavior change to a working flow — purely additive field,
      every existing response key unchanged, verified by the no-mutation
      test

## What was NOT verified

Did not run `pytest` for this individual fix — per this round's explicit
instruction, deferred to a single pass at the end. Did not extend this
same transparency treatment to `corporate_policy_service.py`'s response
shape at ride-booking time (the `skipped_rules` field it already
populates, per its own comment, already serves that purpose at
evaluation time — this fix's scope was specifically the policy
create/read endpoints, where the gap was that a caller reading back a
*stored* policy had no signal, not the evaluation path itself, which was
already self-documenting). Did not build a UI control for geofence at
all — out of scope; this fix corrects the specific "silently accepts and
never signals" gap for the API surface that exists today, not a new
feature.
