# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-25 |
| Author | Claude Code (interactive session) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | #4557 |
| Related issue or gap ID | `docs/legacy-ride-history-presentation-plan.md` Items 2–3 |

## 1. Issue / gap identified

Legacy-imported rides (from the MongoDB migration batches) carry `legacy_import_metadata`,
but nothing in the rider-facing product surfaces this honestly. A rider viewing an imported
ride's detail screen today sees a planned route line with no indication it came from the
previous app rather than being GPS-tracked — admin-dashboard already shows this correctly
(an "Imported" badge + "no GPS was recorded" disclaimer), rider-app does not.

## 2. Root cause

`legacy_import_metadata` was added to `rides` for backend/admin traceability (migration 268),
and admin-dashboard's `ride-detail-modal.tsx` was built to read it directly, but rider-app's
`ride-details.tsx` was never updated to check for it — a gap identified this session while
researching the presentation plan doc, not a regression from a prior working state.

## 3. Fix / remediation

Two-part, dark-shipped end to end:

- **Backend**: adds `app_settings.legacy_ride_badge_enabled` (default `false`) and computes
  `show_legacy_badge` on `GET /rides/{ride_id}` — `true` only when the flag is on **and** the
  ride's `legacy_import_metadata` is non-empty.
- **Rider-app**: `ride-details.tsx` reads `ride.show_legacy_badge` (never
  `legacy_import_metadata` directly, so the backend flag stays the single source of truth) to
  show an "Imported" badge and the disclaimer "Imported from the previous app — no GPS was
  recorded for this ride", reusing admin-dashboard's existing wording verbatim.

## 4. Risk & impact on existing functionality

- `GET /rides/{ride_id}` (`backend/routes/rides/queries.py`) is read by rider-app, driver-app,
  and admin-dashboard. Adding one new, always-present boolean field is additive — no existing
  field is renamed, removed, or retyped. Blast radius: single endpoint at the backend layer,
  extending to one rider-app screen at the UI layer. Driver-app and admin-dashboard are
  unaffected — they simply receive one more field they don't yet read.
- The `_deps.get_app_settings()` call added is the same call already made four times earlier
  in this same function (`free_cancel_window_seconds`, `cancellation_fee_*`,
  `noshow_wait_seconds`, `ride_offer_timeout_seconds`) — same in-process-cached settings
  fetch, no new DB round-trip pattern, no interaction with any of the 18 background loops in
  `core/lifespan.py`.
- Rider-app: only `ride-details.tsx` (the past-ride detail/receipt screen, never shown
  mid-ride) touched. No change to `mapCoordinates`/`RouteLine` route-drawing logic —
  `planned_route_polyline` already rendered correctly for legacy rides before this change;
  this PR only adds labeling around it.
- Flag defaults `false`, so this change is a no-op in every environment until someone
  explicitly flips `legacy_ride_badge_enabled` — zero behavior change on merge.

## 5. User-experience effect

- Who sees a difference: riders, but only once the flag is explicitly turned on (default off
  — nobody sees a difference at merge time).
- Mid-session visible: N/A while the flag is off; once on, a rider opening a *past*
  (never active/in-progress) legacy-imported ride's detail screen sees the badge/disclaimer —
  this screen is never shown during a live ride.
- Copy: "Imported" badge label + the disclaimer sentence above, reused verbatim from
  admin-dashboard's existing internal-facing copy, not newly authored — **not** separately
  reviewed against the customer-centric tone standard this session, since it's now
  customer-facing for the first time (previously admin-only). Flagged under "What was NOT
  verified" below.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/queries.py` | `GET /{ride_id}` computes `show_legacy_badge` | Flag-gated derived field on the ride-detail response |
| `backend/schemas.py` | `AppSettings.legacy_ride_badge_enabled: bool = False` | Schema-side default, mirrors the DB migration |
| `backend/migrations/364_settings_add_legacy_ride_badge_enabled_flag.sql` | Adds `settings.legacy_ride_badge_enabled` column | Additive, defaulted `false` |
| `backend/tests/test_coverage_rides.py` | +3 test cases | Flag-off/flag-on/no-metadata coverage |
| `rider-app/app/ride-details.tsx` | Badge + disclaimer rendering | Honesty layer for legacy-imported rides |
| `rider-app/__tests__/rideDetailsScreen.test.tsx` | +2 test cases | Shown-when-flagged / hidden-without-flag coverage |

## 7. Before / after

```
# Before (backend/routes/rides/queries.py, end of get_ride)
        ride["total_earned"] = round(fare_only + tip + ride["incentive_amount"] + cancel_fee + tax, 2)

    return ride
```

```
# After
        ride["total_earned"] = round(fare_only + tip + ride["incentive_amount"] + cancel_fee + tax, 2)

    show_legacy_badge = False
    if ride.get("legacy_import_metadata"):
        try:
            settings = await _deps.get_app_settings()
            show_legacy_badge = bool(settings.get("legacy_ride_badge_enabled", False))
        except Exception:
            logger.error("Failed to fetch app settings for legacy ride badge flag", exc_info=True)
    ride["show_legacy_badge"] = show_legacy_badge

    return ride
```

## 8. Rollback plan

- **Immediate**: flip `app_settings.legacy_ride_badge_enabled` back to `false` — no redeploy,
  matches the `legacy_consent_notice_enabled` precedent (migration 356).
- **Full revert**: `git revert` is safe for both commits — this is a pure read/display
  feature, no data written anywhere. Migration rollback (already in the migration's own
  header): `ALTER TABLE public.settings DROP COLUMN IF EXISTS legacy_ride_badge_enabled`.

## 9. Verification performed

- [x] Automated tests run — backend: `pytest tests/test_coverage_rides.py` (177 passed) plus
      5 `AppSettings`-adjacent test files (48 passed); rider-app: `jest
      __tests__/rideDetailsScreen.test.tsx` (59 passed). `ruff check`/`ruff format --check`
      and `eslint`/`tsc --noEmit` all clean.
- [ ] Manual repro steps followed in staging — not run (no live Supabase/staging access this
      session)
- [x] Blast-radius grep performed — searched for every caller of `GET /rides/{ride_id}`
      (rider-app, driver-app, admin-dashboard) and for every other reader of
      `legacy_import_metadata` (`routes/rides/payments.py`, `rating.py`, admin-dashboard's
      `ride-detail-modal.tsx`) before adding the new field
- [x] Reviewed against relevant CLAUDE.md convention(s) — dark-ship/feature-flag pattern
      (mirrors `legacy_consent_notice_enabled` exactly), migration append-only/rollback
      convention, task-decomposition (≤3 files per logical change)
- [x] Feature-flagged (`legacy_ride_badge_enabled`, default `false`)

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip; default-`false` behavior verified by
      automated tests)
- [x] Blast radius is stated (one endpoint + one rider-app screen), not assumed
- [x] No silent behavior change to an already-shipped flow — flag defaults `false`, UX field
      above filled in explicitly

## What was NOT verified

- Not tested against a live Supabase instance, a real device, or a simulator — only mocked
  backend tests (pytest) and Jest component tests (React Test Renderer) this session.
- The badge/disclaimer copy was reused verbatim from admin-dashboard's existing
  internal-facing text, not independently reviewed against the customer-tone standard now
  that it is rider-facing for the first time.
- Driver-app's equivalent screen (Item 4 of `docs/legacy-ride-history-presentation-plan.md`)
  is not covered by this change — a driver viewing an imported ride still sees no
  badge/disclaimer until that follow-up ships, tracked separately.
