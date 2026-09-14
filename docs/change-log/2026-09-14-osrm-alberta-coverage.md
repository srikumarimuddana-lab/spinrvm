# Change Impact & Risk Log — Alberta coverage on the OSRM graph

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code session (requested by repo owner) |
| Surface(s) | backend (indirectly — OSRM is the map-matching provider `backend/utils/route_distance.py` calls) |
| Domain (Sentry tag) | rides |
| PR / commit link | this commit |
| Related issue or gap ID | none — owner request; `deploy/osrm/README.md` §1 "Adding a second province" |

## 1. Issue / gap identified

The self-hosted OSRM graph covers Saskatchewan only. A GPS trace that crosses
into Alberta has no road network to match against there, so the Alberta portion
of the trip is not road-matched.

## 2. Root cause

Not a defect — a deliberate scope choice. `deploy/osrm/Dockerfile` builds from
the Geofabrik Saskatchewan extract, and one OSRM process serves exactly one
graph. `EXTRA_REGION_URLS` has existed for this since the file was written; it
was simply never set.

## 3. Fix / remediation

No code change. `EXTRA_REGION_URLS` is now set to the Geofabrik Alberta extract
as a **build-time** variable on the Railway `osrm-backend` service, so the
existing `osmium merge` path in the `fetch` stage produces one SK+AB graph.

This commit is the documentation half: it records that the variable is set in
production, and corrects a genuinely misleading instruction in the README (see
§7) that cost a cycle on the sibling tile service.

## 4. Risk & impact on existing functionality

- **Who reads this**: `backend/utils/route_distance.py` (OSRM preferred →
  Google Roads → haversine) and `backend/utils/route_validation.py`
  (`_validate_via_osrm`). Both resolve the URL from `app_settings.osrm_url` or
  `settings.OSRM_URL`. Note the `settings` table has **no** `osrm_url` column,
  so the documented DB-override path is dead and the env var is the only source.
- **What changes**: `actual_distance_km` and `ride_metrics` for trips whose
  trace enters Alberta. Those trips are currently either partially matched
  (OSRM returns `code:"Ok"` with non-contiguous `matchings`, and
  `_compute_via_osrm` sums only what matched) or fall through to Google Roads
  or haversine. After this, they match end to end.
- **What does NOT change**: **what anyone is charged.** Verified live against
  production on 2026-09-14: `fare_lock_enabled = true`,
  `fare_distance_basis = 'road'`. `routes/drivers/ride_complete.py`'s
  `_fare_lock` branch writes `distance_km` only and skips
  `recalculate_fare_for_distance`. The rider's pre-booking quote is priced on
  Google Directions in `routes/rides/_shared.py` and never touches OSRM.
  **If `fare_lock_enabled` is ever turned off, this becomes a fare change** and
  must be re-assessed before that flip.
- **Trips wholly inside Saskatchewan are unaffected** — the SK road data is
  identical in the merged graph.
- **Known interaction**: `utils/trip_distance.py`'s 1/3×–3× sanity gate
  compares road distance against a haversine baseline that *drops* segments
  over 5 km / 300 s / 150 km/h rather than interpolating. Long rural
  cross-border stretches are exactly where those gaps occur, so a correct,
  newly-complete OSRM distance can land above `3×` a deflated baseline and be
  discarded — silently keeping the old, lower number. This is pre-existing
  behaviour that this change makes *more* likely to be hit, not a new bug.
- **Blast radius**: single-surface (backend distance measurement). No ride
  state machine, dispatch, wallet or insurance-period code is touched. The tile
  server is a separate service and is unaffected.

## 5. User-experience effect

- **Rider / driver**: no visible change while `fare_lock_enabled = true` —
  receipts and fares are unchanged.
- **Internal admin**: `actual_distance_km` on the ride detail view and the SGI
  dispute map will read differently (generally longer, and more accurate) for
  cross-border trips.
- **Mid-session visibility**: none. Distance is computed at trip end; a rider
  mid-ride or driver online sees nothing change under them.
- No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `deploy/osrm/README.md` | Records that Alberta is enabled in production; corrects "redeploy" → a fresh build is required | The variable is build-time only; a Railway *redeploy* reuses the prior build snapshot and silently keeps the SK-only graph |
| `docs/change-log/2026-09-14-osrm-alberta-coverage.md` | This entry | Required for a change affecting recorded trip distance |

No application code changed.

## 7. Before / after

The README previously said:

```
On Railway, set `EXTRA_REGION_URLS` as a **build-time** variable on the OSRM
service and redeploy.
```

"Redeploy" is wrong and actively misleading. Railway's redeploy reuses the
previous deployment's **existing build**, so the new build arg never reaches
`docker build` and the service comes back up on the same SK-only graph — with a
green deploy and no error anywhere. This exact trap cost a cycle on the tile
service earlier the same day. A build-arg change needs a *fresh build*, which on
this service means a commit matching its `/deploy/osrm/**` watch pattern.

## 8. Rollback plan

Unset `EXTRA_REGION_URLS` on the Railway `osrm-backend` service and trigger a
fresh build. No code revert and no deploy of this repository is required —
which is the reason Alberta is environment config rather than a changed
Dockerfile default.

No data-level remediation is needed: this writes nothing. `actual_distance_km`
rows already written are unaffected, and rows written while it is live remain
valid measurements. Fares were never repriced (`fare_lock_enabled = true`), so
there is no money to unwind.

## 9. Verification performed

- [x] Blast-radius grep performed — searched for `OSRM_URL` / `osrm_url`
      consumers (`utils/route_distance.py`, `utils/route_validation.py`,
      `scripts/backfill_imported_ride_routes.py`, `core/config.py`) and for
      `map-spinr` (not hardcoded anywhere in the repo). Confirmed via the
      `settings` table schema that no `osrm_url` DB override column exists.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — checked the live
      `fare_lock_enabled` / `fare_distance_basis` values in production rather
      than trusting the README's 2026-09-13 note.
- [ ] Automated tests run — none exist for graph coverage; this is a data-build
      change, not a code change.
- [ ] Manual repro steps followed in staging — **not done, no staging OSRM.**
- [ ] Feature-flagged — n/a; the environment variable *is* the flag.

**What was NOT verified:** the merged build had not completed at the time of
writing. Specifically unproven: that `osrm-extract` survives the larger merged
extract on Railway's builder (peak RAM scales with extract size — OOM is the
most likely failure and presents as a build death with no clear error), and
that Alberta is actually loaded afterwards. The latter must be checked with
`EXPECT_ALBERTA=1 deploy/osrm/smoke-test.sh`, **not** a `code:"Ok"` check: an
SK-only graph answers `Ok` for Edmonton by snapping ~230 km to the provincial
border, so only the snap *distance* distinguishes a real merge from a silent
fallback.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — the admin-visible `actual_distance_km` change is stated in §5
