# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (surge/pricing) |
| PR / commit link | commit `1b3f584` (local worktree, not pushed) |
| Related issue or gap ID | Ranked blocker #21, `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` (row 197, and finding #15 at row 131) |

## 1. Issue / gap identified

`routes/admin/service_areas.py`'s dedicated admin manual surge-override
endpoint, `PUT /api/admin/service-areas/{area_id}/surge`
(`admin_update_surge_pricing`, lines ~908-937), wrote `surge_enabled`,
`surge_active`, and `surge_multiplier` to the `service_areas` row but never
stamped `surge_source`. Per CLAUDE.md's surge convention ("Surge engine runs
every 2 minutes; updates only service areas where `surge_source == 'auto'`"),
that stamp is the only thing that tells the automated surge engine to leave
a row alone.

## 2. Root cause

`backend/utils/surge_engine.py` reads `area.get("surge_source", "auto")`
(line 291) and skips an area only when that value is exactly `"manual"`
(line 292). Because `admin_update_surge_pricing`'s write payload never set
`surge_source`, any override made through this specific route left the
column at whatever it was before (frequently unset/`None`, which the engine
treats identically to `"auto"`). The auto engine's next 2-minute pass would
then read demand/supply for the area and overwrite the admin's override —
silently, with no error, defeating the purpose of a manual override.

The sibling endpoint, `PUT /api/admin/service-areas/{area_id}`
(`admin_update_service_area`, the generic area-update route), does not have
this bug: the admin-dashboard's `GeneralTabForm` (in
`admin-dashboard/src/app/dashboard/service-areas/page.tsx`, line 702)
explicitly sends `updates.surge_source = "manual"` in its request body, and
`admin_update_service_area` passes `surge_source` through its field
allow-list (line 713) verbatim. That is the endpoint the live UI actually
calls for surge edits.

**Blast-radius / liveness finding:** grepped the admin-dashboard for every
caller of the dedicated `/surge` endpoint. `admin-dashboard/src/lib/api/
pricing.ts` exports `updateSurge()` wrapping this exact route, and it is
re-exported from `lib/api.ts`, but **no `.tsx`/`.ts` file in the app calls
`updateSurge`** — confirmed via `grep -rn "updateSurge\b" admin-dashboard/src`
returning only the definition and the barrel re-export. So this was
correctly characterized by the audit (and by CLAUDE.md's task framing) as a
**latent bug, not an active production incident**: the route is live and
directly callable via the API (e.g. curl, Postman, a future frontend wire-up,
or any other integration), so it must still be fixed, but no real admin
session was silently losing surge overrides through it today.

## 3. Fix / remediation

Added `"surge_source": "manual"` to both branches of `area_update` in
`admin_update_surge_pricing` (the `is_active=True` and `is_active=False`
paths), matching the exact string value (`"manual"`) that
`admin_update_service_area`'s live-wired path already writes. No new value
was invented.

Also checked whether this endpoint needs the same "`surge_multiplier` > 2.5
requires justification" gate CLAUDE.md documents and that
`admin_update_service_area` enforces (lines 639-667 of the same file, gated
on `area.surge_justification`). It does not need a code change:
`SurgePricingRequest.multiplier` is already `Field(ge=1.0, le=2.5)` (line
304-306) — Pydantic itself 422s any value above 2.5 before the handler body
ever runs, so there is no reachable code path here that could apply an
above-cap multiplier without justification. This is stricter than the
sibling endpoint (which allows up to 10.0 with a written-justification
gate), not weaker, so it already satisfies the spirit of the rule; changing
the Pydantic bound to 10.0 + adding a justification gate would be a larger,
unrequested behavior change to a route that is currently unreachable from
any known caller, so it was left as-is. Documented this finding in a new
test (`test_surge_multiplier_hard_capped_at_2_5_by_pydantic`) so it isn't
silently relied upon.

No error-swallowing issue was found on this write: the
`db_supabase.update_one("service_areas", ...)` call for the authoritative
row is not wrapped in a `try/except` — any DB failure already propagates as
an unhandled exception (FastAPI turns that into a 500 with the underlying
traceback surfaced in logs), consistent with CLAUDE.md's "don't silently
swallow DB errors" rule. No change was needed there.

## 4. Risk & impact on existing functionality

- **Who else reads `surge_source`:** grepped all non-test backend code for
  `surge_source`. Readers/writers, by file:
  - `backend/utils/surge_engine.py` — the auto engine. Reads it to decide
    whether to touch a `service_areas` row (line 291-292); also echoes it
    back verbatim as `"source"` in `get_surge_status()`'s per-area payload
    (line 445), which is what `GET /api/admin/surge/status` returns to the
    dashboard.
  - `backend/features.py` (line 443) — the "reset area to auto" endpoint
    (`PUT /api/v1/service-areas/{id}/surge/auto`, called by the
    `resetSurgeToAuto` button in the admin dashboard's `GeneralTabForm`)
    writes `surge_source: "auto"` explicitly. Unaffected by this change.
  - `backend/routes/admin/service_areas.py` — `admin_update_service_area`
    (pass-through field), `_record_manual_surge_history` (reads it only to
    default the `surge_pricing` audit row's `source` column when the caller
    didn't specify one — already defaulted to `"manual"` before this fix,
    unchanged), and now this endpoint.
  - `backend/schemas.py` (comment only, no runtime read) and
    `backend/utils/kyb_reverification.py` (comment only) — no behavior.
- **Could this regress a flow that currently works?** The only behavioral
  change is that `PUT /service-areas/{id}/surge` now marks the area
  `surge_source="manual"` instead of leaving it unset. Concretely:
  - **Before this fix:** calling this route left the area indistinguishable
    from an auto-managed area to `surge_engine.py`; the auto engine would
    overwrite the multiplier on its very next 2-minute run. That silent
    overwrite **is the bug**, not intended behavior — so removing it is the
    fix, not a regression.
  - **After this fix:** an area touched by this route now behaves exactly
    like one touched by the live-wired `admin_update_service_area` path —
    the auto engine leaves it alone until an admin explicitly resets it via
    `resetSurgeToAuto` (`surge_source: "auto"`). This is the documented,
    intended behavior of a "manual override," not a new one.
  - Because **no live caller reaches this route today** (confirmed above),
    there is no currently-running admin session or scheduled job whose
    observed behavior changes as a result of this deploy. The auto engine's
    behavior toward areas that already have `surge_source` set via the
    generic endpoint is completely unchanged — this fix only affects rows
    written through this one specific, currently-unreached route.
- **Interaction with the 16 background loops:** yes — the surge engine loop
  (`backend/core/lifespan.py`) is the one directly affected, in the sense
  described above (it will no longer touch areas set via this endpoint).
  That is the intended fix, not a new risk; no other background loop reads
  `surge_source`.
- **Blast radius:** isolated to `backend/routes/admin/service_areas.py`'s
  `admin_update_surge_pricing` function and its own tests. No frontend,
  migration, or other route file was touched.

## 5. User-experience effect

None today, for the reason stated above (the route has no live caller). If
a future frontend change or external integration starts calling
`PUT /api/admin/service-areas/{id}/surge`, the user-visible effect *of this
fix* is that an admin's manual override through that route will actually
stick (matching the already-correct behavior of the surge edit form on
`/dashboard/service-areas`), instead of silently reverting to auto-computed
pricing within ~2 minutes with no error or indication to the operator. That
is a bug fix, not a new UX change requiring flagging.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/service_areas.py` | Added `"surge_source": "manual"` to both branches of `area_update` in `admin_update_surge_pricing` | Stop the auto surge engine from silently overwriting manual overrides written through this route (ranked blocker #21) |
| `backend/tests/test_admin_service_areas_coverage.py` | Extended the two existing `TestUpdateSurgePricing` tests to assert `surge_source == "manual"`; added `test_surge_source_stamped_matches_sibling_endpoint` and `test_surge_multiplier_hard_capped_at_2_5_by_pydantic` | Regression coverage for the fix + documents the already-sufficient 2.5 cap so it isn't re-investigated as a gap later |

## 7. Before / after

```python
# Before
if surge.is_active:
    area_update: Dict[str, Any] = {
        "surge_enabled": True,
        "surge_active": surge.multiplier > 1.0,
        "surge_multiplier": surge.multiplier,
    }
else:
    area_update = {
        "surge_enabled": False,
        "surge_active": False,
        "surge_multiplier": 1.0,
    }
```

```python
# After
if surge.is_active:
    area_update: Dict[str, Any] = {
        "surge_enabled": True,
        "surge_active": surge.multiplier > 1.0,
        "surge_multiplier": surge.multiplier,
        "surge_source": "manual",
    }
else:
    area_update = {
        "surge_enabled": False,
        "surge_active": False,
        "surge_multiplier": 1.0,
        "surge_source": "manual",
    }
```

## 8. Rollback plan

Purely additive field on an existing write to an existing column
(`service_areas.surge_source`, already used by the live-wired sibling
endpoint) — no migration involved. To roll back:
- `git revert` the commit — safe here specifically because, per the blast-
  radius finding above, this route has no live caller, so there is no
  in-flight admin session or live-data state that depends on the new
  behavior; reverting only restores the pre-fix (buggy) write, it does not
  need to undo any Stripe charge, wallet delta, or ride-state row.
- If the route somehow becomes live before a revert is possible, an operator
  can also just re-run the auto-reset endpoint (`PUT /api/v1/service-areas/
  {id}/surge/auto`, `resetSurgeToAuto`) on any affected area to force
  `surge_source` back to `"auto"` immediately, without any deploy.

## 9. Verification performed

- [x] Automated tests run — unit only. `/tmp/spinr-venv/bin/pytest
      backend/tests/test_admin_service_areas_coverage.py -q` →
      **64 passed, 1 warning in 155.65s**, including the 2 modified tests and
      2 new tests added by this change.
- [x] `ruff check backend/routes/admin/service_areas.py
      backend/tests/test_admin_service_areas_coverage.py` → **All checks
      passed.**
- [ ] Manual repro steps followed in staging — **not done**. No staging
      environment was exercised; this is a backend-only unit-test-verified
      change.
- [x] Blast-radius grep performed — `grep -rn "surge_source"
      backend --include=*.py` (excluding tests/`__pycache__`) and
      `grep -rn "updateSurge\b" admin-dashboard/src`; results and every
      reader/writer found are listed in section 4 above.
- [x] Reviewed against relevant CLAUDE.md convention — surge pricing rules
      section, "Admin manual override accepts 1.0–10.0 but any value > 2.5
      requires documented justification" (checked: not applicable to this
      endpoint, see section 3), and "Do not silently swallow errors" (checked:
      the write is unguarded, errors already propagate).
- [ ] Feature-flagged — **not applicable/not done**. This is a one-line
      additive fix to a route with zero live callers (confirmed above); per
      CLAUDE.md's own gate #3, flagging is for "user-visible and non-trivial"
      changes to shared components used by 3+ pages — this affects no
      currently-reachable UI at all, so gating it further was judged
      unnecessary rather than skipped by default.
- Backend-only Python change — no `admin-dashboard`/`rider-app`/`driver-app`
  build was applicable or run (this task did not touch any frontend file).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; no live-data
      dependency because the route has no current caller)
- [x] Blast radius is stated, not assumed — every reader/writer of
      `surge_source` in the codebase enumerated above by file and line
- [x] No silent behavior change to an already-shipped flow: confirmed via
      grep that no shipped UI flow calls this route today, so this fix
      changes no observed behavior for any real user — the "before/after"
      is entirely within a currently-dead code path, made ready-correct for
      whenever it becomes live
