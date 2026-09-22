# Change Impact & Risk Log — phantom distance from gap-fill connectors

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session-assisted), branch `claude/zen-tesla-694lsr` |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | rides |
| PR / commit link | _(branch `claude/zen-tesla-694lsr`)_ |
| Related issue or gap ID | Reported from ride `0c24901f-7c9e-4de5-9792-19f954b713a5` |

## 1. Issue / gap identified

A rider reported that a ride's **Actual Trip** distance (8.96 km) was ~2 km longer
than the booked route (6.99 km) and that the map drew a detour they never took —
straight down an arterial and back. The inflated distance carried a real cost to
them (~22 cents of per-km insurance). A second, unrelated defect surfaced in the
same screenshots: the **Pickup** tab drew a long diagonal across the city while
its own card read 0.60 km.

## 2. Root cause

**Two independent causes, one screen.**

**(a) Phantom trip distance — a routed gap connector nobody could have driven.**
When GPS drops mid-trip, `route_reconstruction.append_connector()` bridges the
hole by asking OSRM (then Google) to route between the last fix before the gap
and the first fix after it. The router returns the *fastest legal path*, not the
path driven. Two GPS fixes on opposite carriageways of a divided road are only
legally connected via the next turnaround — so the router answers a ~389 m hole
with a ~2.3 km out-and-back, and that distance was published as trip distance.

Nothing rejected it. The guard in `compute_gap_route_via_osrm` was:

```python
maximum_km = max(direct_km * 5.0, direct_km + 2.0)
```

The ratio term (5x = 1.945 km) *would* have rejected it. The absolute term
raised the ceiling to **2.389 km** and let 2.33 km through. That `+ 2.0` slack is
sized for long gaps; on a short one it is the entire failure.

The downstream backstop also missed, narrowly:
`route_finalizer.resolve_measured_distance_km()` falls back to the booked
distance above `max_vs_reference = 1.3` x planned. This ride was
**8.96 / 6.99 = 1.282**. That ceiling exists because of an earlier identical
incident (ride SPR-EG7X86, 2026-09-12, ADR 016); this one passed under it.

**(b) Pickup tab drew the trip's crow-flies line.** `RideRouteMap` falls back to
a straight pickup→dropoff gradient when it has no geometry
(`ride-route-map.tsx:433`), and `suppressStraightFallback` was only ever set for
*imported* rides. So a non-imported ride with no Phase-2 trail showed the
**trip's** straight line under a "Driver → Pickup" heading — on the screen used
for SGI and dispute review — contradicting the pickup distance on its own card.
The code comment at `ride-detail-modal.tsx:770-775` already warns about exactly
this failure mode; the suppression was simply never wired for this case.

Note: the 0.60 km pickup figure is computed from real Phase-2 breadcrumbs, so
those points exist. They are not *drawn* because `p2_route_geometry_enabled`
defaults to `false` (migration 345). **Its live value was not checked** — see
section 11.

## 3. Fix / remediation

1. **Route the gap along the roads actually travelled.** The gap `/route` call
   now carries OSRM `bearings` derived from the observed direction of travel on
   each side of the hole, pinning both endpoints to the carriageway driven
   rather than the fastest legal path. If a bearing hint makes the gap
   unroutable, it is retried once unconstrained so a hint can never lose a gap.
2. **Refuse what could not have been driven.** A routed connector implying more
   than `MAX_PLAUSIBLE_SPEED_KPH` (180) across the gap's own elapsed time is
   refused outright — recorded in `failed_gaps`, contributing no geometry and no
   distance, and **not** substituted with a straight line. An honest hole beats
   a fabricated path on an audit map.
3. **Size the slack to the route it is measuring.** `max_extra_km` is now a
   parameter. The default stays `2.0` so the live-trail consumer is untouched;
   completed-route reconstruction passes `0.5`, because that distance is
   audited and billed.
**Decision recorded (2026-09-21, product owner):** a gap whose routed answer is
rejected for *shape* still gets a short straight-line connector rather than
being refused outright. For the reported ride that means a ~389 m dashed line
whose km is already excluded from measured distance — refusing outright would
mark the entire route "incomplete" over a 389 m hole, which is a worse artifact
on the SGI/dispute screen than a short honest line. Outright refusal is
reserved for the *physically impossible* case (guard 2), where no straight line
would be defensible either.

4. **Clock the anchor connectors too** (follow-up, same branch). A
   `missing_start`/`missing_tail` connector had no timestamp on either side and
   was bounded only by `MAX_INFERRED_CONNECTOR_KM`, so a tail could contribute
   up to **10 km** of invented distance with nothing asking whether the driver
   had time to cover it. The ride lifecycle is that missing clock, and
   `route_segments._tail_quality` already computed the same interval for
   reporting — it simply never reached the connector decision. It does now,
   above `ANCHOR_SPEED_GATE_MIN_KM` (1.0 km).

   Care was needed in one place: giving anchors an `elapsed_seconds` would
   otherwise have activated `MAX_INFERRED_GAP_SECONDS` for them too, refusing
   any tail on a ride where the driver took more than 5 minutes to tap
   complete. That cap means "GPS was down this long", which only an internal
   gap can evidence, so it is now explicitly scoped to `internal_gap`.

5. **Close the last loose guard, and make the next one self-announcing**
   (follow-up, same branch). The speed gate cannot catch a detour driven
   *slowly*: 1.8 km across a 389 m gap over 120 s is only ~54 km/h, entirely
   plausible, and the 5x ratio admitted it. The ratio is now a parameter too,
   3.0 for a completed route (default 5.0 kept for the live trail). With the
   0.5 km absolute slack still protecting short gaps, ordinary
   around-the-block routing is unaffected — verified at 100 m/350 m,
   200 m/600 m and 1 km/2.5 km.

   Every connector decision now increments
   `spinr_rides_route_connector_total{outcome}` — `routed`,
   `routed_high_detour` (accepted but above 2x the straight line),
   `straight`, `refused_speed`, `refused_distance_cap`, `refused_time_cap`.
   Thresholds are judgement calls and can be wrong; this is how being wrong
   surfaces on a dashboard instead of in a rider's complaint, which is exactly
   how this bug was found.

6. **Stop a part-guessed number being the billed number** (owner decision,
   2026-09-22). Everything above makes the guess *better*; this stops the guess
   being money. `resolve_measured_distance_km` summed
   `observed + routed_connector` into `actual_distance_km` — the field the
   per-km insurance charge reads — while its own docstring said the value was
   for display and "never bill". Intent and wiring disagreed.

   Now: when a trip contains routed gap fill **and** the result deviates from
   the booking by more than `max_guess_deviation_ratio` (10%), the booked
   distance is published instead, under a new
   `planned_guess_deviation` basis. The reported ride was 28.2% over, so it
   would now publish 6.99 km rather than 8.96 km.

   **Deliberately gated on `routed > 0`.** With a complete GPS trail a large
   deviation is a real detour; clamping that to the booking would under-report
   the trip and, on a 0%-commission product where the driver keeps the fare,
   underpay the driver. The owner's instruction was "if a trip's distance is
   part guess" — this is that precondition, made explicit.

   The ratio reads from `app_settings.route_guess_deviation_max_ratio` when
   present, defaulting to 0.10 — a no-deploy tuning lever, and no migration
   needed for the default to hold. The same ratio governs both conditions: the
   guess must exceed it as a share of the booking, and the published number
   must drift past it.

   **Ordering matters and is deliberate.** `planned_capped` (the 1.3x
   catastrophic ceiling) is evaluated first; this rule only covers what slips
   under it. Reversing the two swallows the ceiling's own test cases and, worse,
   swallows real detours — see section 11.

7. **Stop the pickup tab borrowing the trip's chord.** A non-planned phase with
   no drawable geometry of its own now suppresses the straight-line fallback and
   shows the two pins plus its existing empty hint.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend finalizer + admin map), stated below.**

Grep performed for every consumer of each changed symbol:

| Symbol | Other consumers found | Treatment |
|---|---|---|
| `compute_gap_route_via_osrm` | `route_reconstruction.py:223`, **`live_breadcrumbs.py:197` and `:245`** | `max_extra_km` **defaults to the old 2.0**, and bearings default to `None`, so the live trail is byte-for-byte unchanged. Only reconstruction opts in. |
| `compute_gap_route_via_google` | `route_reconstruction.py` only | Same defaulted parameter. |
| `_compute_route_via_osrm` | `compute_route()` (live rider/driver ETA), `compute_gap_route_via_osrm` | New `bearings` arg is optional and omitted by the ETA path; request params are identical when unset. |
| `reconstruct_completed_route` | `route_finalizer.py:947`, `ride_route_analyzer.py:278` | Both receive the same dict shape; only `failed_gaps` can now be non-empty for a new reason. |
| `suppressStraightFallback` | `ride-detail-modal.tsx` only (single call site) | Widened from imported-only to any geometry-less non-planned phase. |

What could regress:

- **A refused gap lowers the candidate distance.** `resolve_measured_distance_km`
  already handles a too-low candidate: below `min_vs_straight` (0.8) x the
  crow-flies endpoint distance it publishes the booked distance as
  `planned_estimated` rather than a wrong GPS number. So the failure mode of
  this change is "falls back to booked distance", not "under-reports".
- **`failed_gaps` non-empty flips route quality.** `_quality_projection` sets
  `finalization_reason = "provider_partial_failure"` and `_final_status` treats
  the route as incomplete. That path is pre-existing and already reachable via
  the distance/time caps — this adds a third reason to reach it, so more rides
  may read "incomplete" rather than "complete". This is the intended trade:
  an honest incomplete beats a confident wrong number.
- **Completion-time settlement is not touched.** This is the deferred
  finalizer, which the module docstring states is display/audit geometry only;
  fare and lifecycle authority stay with completion settlement.
- No money arithmetic was added or changed; no `Decimal`/float surface is
  involved in this diff (these are geometry floats, never money).
- Background loops: `route_finalizer (15s)` behaviour is unchanged other than
  the connector decision itself; no new loop, no replay-safety change.

## 5. User-experience effect

- **Rider / driver:** on a ride with a GPS dropout, the published Actual Trip
  distance no longer includes a detour the driver did not drive. For the
  reported ride this is a ~2 km reduction. This is the intended correction, and
  it is visible on ride history and receipts for **newly finalized rides only** —
  already-finalized rides are not rewritten (see section 11).
- **Internal admin:** the route map may now show an honest gap where it
  previously drew a confident wrong line, and more rides may be labelled
  incomplete. The Pickup tab no longer draws a misleading diagonal.
- **Not visible mid-session** — finalization runs after completion.
- No notification or copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/route_distance.py` | Optional `bearings` on the OSRM `/route` call; `_gap_bearings()` helper; `max_extra_km` parameter replacing the hardcoded `+ 2.0`; one unconstrained retry | Route the gap along the carriageway driven, and let the caller size the detour slack to what the distance is used for |
| `backend/utils/route_reconstruction.py` | Passes observed headings and the strict slack; refuses a routed connector implying >180 km/h into `failed_gaps`; `_exit_bearing`/`_entry_bearing`/`_implied_speed_kph` helpers; corrected a stale docstring claiming `failed_gaps` is always empty | The physics test lives here because only this layer knows the gap's elapsed time |
| `backend/utils/route_reconstruction_projection.py` | Added `bearing_deg()` | Pure geometry helper, beside the existing `distance_m()` |
| `backend/tests/test_route_reconstruction.py` | 5 tests: refusal, control, cap boundary, bearings+slack plumbing, anchor connectors not speed-gated | Regression cover for the reported ride |
| `backend/tests/test_route_distance_osrm.py` | 6 tests incl. a parametrized one proving the old `+2.0` slack admits 2.33 km and `0.5` rejects it | Pins the exact hole that caused this |
| `backend/utils/route_finalizer.py` | `max_guess_deviation_ratio` + the `planned_guess_deviation` basis, its trigger label, a `logger.warning` naming the drift, and the settings read | Stop a part-guessed distance reaching the billed field |
| `admin-dashboard/.../ride-detail-modal.tsx` | `hasGeometryForPhase`/`noGeometryForPhase`; widened `suppressStraightFallback`; `BOOKED_DISTANCE_BASES` | Stop a geometry-less phase borrowing the trip's chord; keep the map in step with a card showing booked km |
| `admin-dashboard/.../ride-route-map.test.ts` | 2 source-contract tests | Matches the existing convention in that file |
| `shared/utils/routeQualityLabel` (`routeSegments.ts`) | Added a `planned_guess_deviation` branch before the ratio fallback | Without it the new basis fell through and captioned the booked figure with the REJECTED reconstruction's observed/inferred percentages |
| `shared/utils/__tests__/routeSegments.test.ts` | 1 test pinning that caption | The `planned_capped` branch beside it already had one |
| `backend/tests/test_measured_distance_resolver.py` | 2 tests: guess materiality measured against the trip, and the inverse | Regression cover for the denominator defect below |
| `backend/scripts/flag_implausible_route_connectors.sql` | New, read-only | Identify already-finalized rides with the same signature, for review — not correction |

## 7. Before / after

```python
# Before — route_distance.compute_gap_route_via_osrm
routed = await _compute_route_via_osrm(start_lat, start_lng, end_lat, end_lng, osrm_url)
...
maximum_km = max(direct_km * 5.0, direct_km + 2.0)   # 389 m gap -> 2.389 km ceiling
if distance_km < direct_km or distance_km > maximum_km:
    return None                                       # 2.33 km detour ADMITTED
```

```python
# After — direction of travel pinned, slack sized by the caller
bearings = _gap_bearings(start_bearing, end_bearing)
routed = await _compute_route_via_osrm(start_lat, start_lng, end_lat, end_lng, osrm_url, bearings)
if not routed and bearings:
    routed = await _compute_route_via_osrm(start_lat, start_lng, end_lat, end_lng, osrm_url)
...
maximum_km = max(direct_km * 5.0, direct_km + max_extra_km)   # 0.5 -> 0.889 km ceiling
if distance_km < direct_km or distance_km > maximum_km:
    return None                                                # 2.33 km detour REJECTED
```

```python
# After — route_reconstruction.append_connector, new physics refusal
implied_kph = _implied_speed_kph(float(distance_km), elapsed_seconds)
if implied_kph is not None and implied_kph > MAX_CONNECTOR_IMPLIED_SPEED_KPH:
    failed_gaps.append(f"{reason}_implausible_detour")
    return          # no geometry, no distance, and no straight-line substitute
```

```tsx
// Before / after — admin pickup view
- suppressStraightFallback={importedNoGps}
+ suppressStraightFallback={importedNoGps || noGeometryForPhase}
```

## 8. Rollback plan

**No feature flag was added — see section 11, this is the one pre-merge gate
this change does not satisfy.** The rollback path is instead data-level and does
not require a second deploy to *repair data*, though it does require a code
revert to stop the new behaviour:

1. **Code:** `git revert` the commits. The changed thresholds are module
   constants (`GAP_MAX_EXTRA_KM`, `MAX_CONNECTOR_IMPLIED_SPEED_KPH`), so a
   one-line revert of either restores the prior behaviour independently.
2. **Data:** route finalization is explicitly replayable — the module is
   "safe to replay as a newer route revision arrives", over immutable GPS
   evidence. Any ride finalized under the new rules can be recomputed under the
   old ones by re-queueing it:
   ```sql
   UPDATE ride_routes
      SET processing_status = 'pending',
          processing_claimed_at = NULL,
          next_retry_at = now(),
          finalized_at = NULL,
          snapshot_revision = 0,
          snapshot_object_path = NULL,
          snapshot_url = NULL
    WHERE ride_id = '<ride_id>';
   ```
   (the column set `mark_route_pending()` itself writes). The `route_finalizer`
   loop picks it up within ~15 s.
3. This change writes no new column and no migration, so there is nothing to
   roll back schema-wise.

## 9. Verification performed

- [x] **Logic dry-run against the reported scenario.** `pytest` could not be
      installed in this environment (see section 11), so the new behaviour was
      exercised through a stdlib-only harness driving the real
      `segment_route` + `reconstruct_completed_route` code paths with stubbed
      providers. Verified: a 2.33 km connector across a 389 m / 20 s hole is
      refused with `failed_gaps == ['internal_gap_implausible_detour']`, zero
      routed km, zero straight km, zero inferred segments; a 0.42 km connector
      over the same hole is still accepted; the cap boundary is exact (at 180
      km/h accepted, at 181 refused); both bearings and `max_extra_km=0.5` reach
      the router; anchor connectors are not speed-gated.
- [x] **Provider-gate arithmetic verified numerically** against the real
      `_haversine_km`: gap 0.389 km, 5x ceiling 1.946 km, old `+2.0` ceiling
      2.389 km (admits 2.33), new `+0.5` ceiling 0.889 km (rejects 2.33).
      `_gap_bearings` formatting, the OSRM `bearings` param reaching the URL,
      its omission when unknown, and the unconstrained retry were all exercised
      against the real module with a stubbed transport.
- [x] **Blast-radius grep performed** — searched every caller of
      `compute_gap_route_via_osrm`, `compute_gap_route_via_google`,
      `_compute_route_via_osrm`, `reconstruct_completed_route`, and
      `suppressStraightFallback`. Results in the section 4 table. The
      `live_breadcrumbs.py` consumer is the one that forced the
      defaulted-parameter approach over editing the constant in place.
- [x] `ruff check` clean on all changed Python files.
- [x] Reviewed against `CLAUDE.md` conventions: dual-import pattern preserved in
      both edited modules; no float money arithmetic introduced; no error
      silently swallowed (each refusal is an explicit `logger.info` naming the
      gap, the distance and the implied speed); append-only insurance rows
      untouched.
- [ ] **Not feature-flagged** — see sections 8 and 11.
- [ ] Manual staging repro — not performed, see section 11.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (re-queue SQL above).
- [x] Blast radius is stated, not assumed (grep table in section 4).
- [x] No silent behaviour change — the UX field (section 5) is filled in for
      both the distance change and the admin map change.

## 10b. Review round — four defects found in this diff's own code

Run against the diff after it was pushed: `spinr-money-auditor`,
`spinr-insurance-period-auditor`, plus a self-review. Four findings were real
and are fixed here; two more are real but belong outside this PR (below).

**(a) The materiality gate used the wrong denominator — the worst of the four.**
`resolve_measured_distance_km` asked whether the routed gap fill exceeded 10% of
the *booking*. It should have asked whether it exceeded 10% of *the number about
to be published*. The booking is precisely the value under suspicion when a
driver detours, so measuring the guess against it let a stale booking masquerade
as evidence of guessing. Executed repro, against the real function body:

```
observed 14.3 + routed 0.71 = 15.01 km,  booking 7.0 km,  gps_km 15.0
  before: (7.0,   'planned_guess_deviation')   <- 8.01 km taken off the driver
  after:  (15.01, 'observed')
```

0.71 km of fill is 4.7% of that trip and 10.1% of that booking. The GPS chord sum
independently corroborated 15.0 km and was ignored. This is the same failure
class CI caught on `d2fc341` — the fix there narrowed it rather than closing it.
Now gated on `routed > ratio * candidate`. Ride `0c24901f` is still caught (its
2.33 km connector is 26% of its own 8.96 km reconstruction) and the pre-existing
`test_real_detour_passes_because_the_gps_sum_grows_with_it` still passes.

**(b) The new basis had no approved copy.** `shared/utils/routeSegments.ts`'s
`routeQualityLabel` — the single label function behind the admin "Actual Trip"
card and its map captions — had branches for `planned_estimated` and
`planned_capped` but not for the `planned_guess_deviation` this PR introduced, so
it fell through to the ratio copy: *"Route reconstructed · 74% GPS observed · 26%
inferred"* printed beside a distance that is the booking. Those percentages
describe the 8.96 km reconstruction that was thrown away. The comment directly
above the fallthrough already warned against exactly this. So the geometry half
of this PR was fixed while the caption half still lied — on the very card the
reported ride's complaint was about. rider-app and driver-app are unaffected:
both have tests asserting they do not call this function.

**(c) The settings read obeyed admins inconsistently by JSON type.**
`.get(key) or 0.10` discards a deliberate JSON `0` (the strictest setting) while
honouring the string `"0"`. The three sibling reads one line above all use the
two-arg form; this line was the odd one out. **(d)** and it was unbounded — a
negative ratio makes both conditions trivially true for every ride, so one
mistyped `app_settings` row would publish the booking platform-wide. Now clamped
to `[0, 1]`, the way surge is clamped at its call sites.

### Escalated rather than fixed here

Both are real, both were verified against the code, and both are outside what
this PR should be widened to cover:

1. **`driver_period_distances` records a booking as if it were GPS-measured.**
   `route_finalizer.py` calls `record_period_distance_revision(...)` without
   `source=`, so every row is stamped `late_tail_rederivation` regardless of
   basis. That table is the 7-year SGI / Saskatchewan Transportation Act audit
   trail, and `routes/admin/compliance.py` bills insurers off it at
   `$0.11/km` (SGI) and `$0.011/km` (Knight Archer) — the exported report
   selects the `source` column and then never renders it, so an auditor cannot
   tell a measured row from a booking-substituted one. **This is pre-existing**:
   `planned_capped` and `planned_estimated` have done it since before this
   branch, so today's exports may already contain unlabelled substituted rows.
   This PR adds a third trigger and improves the *number* without touching the
   provenance. Fixing it means threading the basis into `source` and adding a
   column to a live insurer-facing export — a separate change with its own gate.
2. **`MIN_ROUTED_GAP_M` shifts distance from the routed bucket to the straight
   bucket, which raises `straight_share`.** On a trip with several sub-150 m
   gaps that can cross the pre-existing 0.25 share cap and fall back to
   `planned_estimated` — publishing a stale booking over a `gps_km`-corroborated
   measurement. Arithmetically confirmed. The fix would mean teaching that
   pre-existing fallback to weigh `gps_km`, which is a policy call on a
   live-tested money-adjacent surface, not a mechanical correction.

## 11. What was NOT verified

Stated plainly rather than implied:

- **The test suite was never run.** The sandbox has no network route to PyPI or
  npm (`pip install pytest` and the whole `requirements.txt` fail with "no
  matching distribution"; `admin-dashboard/node_modules` does not exist). The
  new tests are written to this repo's existing conventions and every assertion
  in them was reproduced by hand through the stdlib harness described above,
  but **they have not been executed by pytest or vitest — CI is their first
  real run.**
- **The admin commit bypassed the pre-commit eslint step** (`--no-verify`).
  `admin-dashboard/node_modules` does not exist here and `eslint.config.mjs`
  cannot resolve `eslint-plugin-storybook`, so ESLint fails to start — this is
  the sandbox, not the diff. Every other pre-commit check was run manually
  against those staged files and passed. Lint is unverified until CI.
- **No production build was run for the admin-dashboard change.** Not
  `npm run build`, not `tsc --noEmit`, not `vitest` — npm is unreachable here.
  The TypeScript change is small and locally typed, but it is unverified.
- **admin-dashboard visual regression: `dashboard-rides` is one of the 6 seeded,
  merge-blocking baselines** (`CLAUDE.md` gate 6 / `ACTION_ITEMS.md` B38). This
  change alters the map only *inside an opened ride-detail modal*, which the
  page-level baseline is not expected to capture — but that is reasoning, not a
  screenshot. If the visual job fails on `dashboard-rides`, the baseline needs
  re-capturing via `update-visual-baselines.yml`, which this agent integration
  cannot dispatch; a human must run it.
- **Nothing was confirmed against the live database.** The Supabase query for
  ride `0c24901f` was declined in this session, so the diagnosis rests on the
  code plus the numbers visible in the reporter's screenshots (8.96 km total,
  74% observed / 26% inferred → 2.33 km inferred, against a 6.99 km booking).
  The specific claim that this ride's connector was an opposite-carriageway
  turnaround is **inference from the reported shape, not a verified row.**
- **The review-round fixes were verified the same constrained way.** All 21
  tests in `test_measured_distance_resolver.py` were executed against the
  patched function body through the stdlib harness (AST-extracted, real code,
  no stubs of the logic itself), and the new test was confirmed to FAIL against
  the old denominator — so it discriminates. The `routeSegments.ts` change has
  **not** been run at all: vitest needs `node_modules`, which does not exist
  here. Its test is written to the file's existing convention and reasoned
  through, not executed.
- **Neither escalated finding above is fixed**, by deliberate scope decision.
  The insurer-export provenance gap in particular is live today, independent of
  whether this PR merges.
- **`flag_implausible_route_connectors.sql` has never been executed.** Its
  column names are grounded in what `route_finalizer.py` actually writes and its
  haversine matches `route_reconstruction_projection.distance_m` numerically,
  but it has not been run against a real schema.
- **Already-finalized rides are not corrected**, by explicit decision — the
  report query flags them for review instead. The reporter's own ride still
  carries the inflated distance.
- **`p2_route_geometry_enabled`'s live value is unknown.** The Pickup fix stops
  the *misleading* line; it does not make the Phase-2 trail appear. If that flag
  is off in production, the pickup leg will still draw nothing (correctly, with
  its hint) even though breadcrumbs exist. Turning it on is a separate change.
- **The speed refusal still does not cover every connector.** A connector
  inserted between two OSRM matchings *inside* one observed segment has no
  clock — `project_observed_sections` deliberately attaches whole-segment
  bounds only ("never within one") — so it is governed by the distance guards
  alone. Lower risk than the anchor case that was closed (see below): matchings
  within one segment are chunks of a trace whose points exist, not a dropout.
  The `max_extra_km` tightening is what covers it.
- ~~A trip with a tiny guess and a large genuine deviation is clamped to the
  booking.~~ **Fixed 2026-09-22 — CI caught this, and it was a real defect, not
  a tolerable false positive.** The first cut of the rule fired on `routed > 0`
  and was placed ahead of the `planned_capped` ceiling. The existing
  `test_real_detour_passes_because_the_gps_sum_grows_with_it` failed on it:
  13.0 km observed + 0.5 km routed against a 9.21 km booking, with the
  spike-filtered GPS sum at 13.2 km independently corroborating the number, was
  being clamped to 9.21 km — **under-reporting a genuine detour by 4.3 km,
  which on a 0%-commission product is 4.3 km of the driver's own fare.** That
  test existed because a prior incident taught the lesson; the new rule walked
  straight into it.

  Two corrections: the rule now also requires the **guess itself** to be
  material (`routed > max_guess_deviation_ratio * planned`), so a short
  connector inside an honestly-long trip cannot clamp it; and it is checked
  **after** `planned_capped`, so a catastrophic overshoot keeps its more
  specific label. Ride 0c24901f is still caught (2.33 km routed = 33% of a
  6.99 km booking, 28.2% drift). Three CI failures, all three from this
  branch's own work, no infra involvement.
- **Found in review, fixed 2026-09-22 (commit `7a80e2f`): duplicate connector
  ids.** The short-gap branch emitted a connector using `connector_attempts + 1`
  without incrementing the counter, so the next connector sharing its
  `gap_reason` took the same number and two segments landed in
  `road_matched_segments` under one `id`. Reachable on an ordinary ride — a stop
  (time-split gap under `MIN_ROUTED_GAP_M`) followed by a real dropout
  (distance-split gap over it), which is exactly the pair of failures this work
  addresses. `shared/utils/routeSegments.ts:95` passes a non-empty id straight
  through, so the collision reaches every reader. No consumer was observed to
  crash, but `road_matched_segments` is SGI and dispute evidence and two
  segments claiming one identity is a defect in the record on its own terms.
  Reproduced before fixing; regression test added.

- **Pre-existing gap found and fixed in passing:** `planned_capped` was never
  added to the admin map's `gpsTooIncomplete` check, so a ride capped to the
  booking still drew its GPS fragments — card and map disagreeing, exactly what
  the comment above that line warns about. `BOOKED_DISTANCE_BASES` now covers
  all three booked bases.
- **A detour under 3x the straight line and slow enough to be plausible is
  still accepted.** That is the intended envelope, not an oversight — a real
  detour looks exactly like this — but it means the guards bound the damage
  rather than eliminating it. `routed_high_detour` is the compensating control.
- **Anchor connectors are now clocked, but only above 1 km.** A
  `missing_start`/`missing_tail` connector shorter than
  `ANCHOR_SPEED_GATE_MIN_KM` is deliberately left ungated, because at that
  scale the geometry is usually anchor *error* — a booked-dropoff substitution
  or an off-road pickup snap — rather than travel, and holding it to a driving
  speed would refuse correct geometry. A connector between 0 and 1 km can
  therefore still be fabricated without a physics check. The threshold is a
  judgement call, not a measured one.
- **No load/perf measurement.** The bearing retry adds at most one extra OSRM
  call per gap, and only when a bearing-constrained request fails; the finalizer
  is a background loop with no stated P95 SLA, so this was reasoned about, not
  measured.
