# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety (GPS route-reconstruction / insurance-period audit trail) |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | Items #2 and #3 of the stage 7/8 (insurance-period / fare-billing) GPS-to-billing audit, 2026-09-02 |

## 1. Issue / gap identified

`backend/utils/route_reconstruction.py`'s gap-fill (`append_connector`) always bridged a gap between two pieces of observed GPS evidence — with a routed line if OSRM/Google succeeded, otherwise a straight-line chord — with no check on how far apart the two sides were (item #3) and no check on how much time had actually passed between them (item #2, only relevant to an *internal* gap between two observed segments, where both sides carry a real device timestamp). A 5-second signal drop and a 30-minute tunnel/dead-zone outage were filled identically, and a routed/straight connector could span any distance, however implausible, as a "believable" substitute for missing GPS.

## 2. Root cause

The module's own docstring said it plainly: "the system never produces `failed_gaps` or unverified endpoints" — the 4-tier fill (OSRM → Google → haversine) was designed to *always* succeed, with no plausibility ceiling on the gap it was being asked to bridge. `MAX_INFERRED_CONNECTORS` (an existing constant) caps the *count* of routed/network attempts before falling back to straight-line, but neither it nor anything else capped a single connector's *distance*, and no code path compared elapsed time between the GPS fixes on either side of an internal gap.

## 3. Fix / remediation

- **Distance cap (item #3, applies to every connector reason — `missing_start`, `internal_gap`, `missing_tail`):** added `MAX_INFERRED_CONNECTOR_KM = 10.0`, reusing the same plausibility magnitude as the existing `gps_filtering.py` constant `MAX_UNTIMED_HOP_KM` (its "max believable single hop without timing info"). A connector whose gap exceeds this is refused outright — no routed attempt, no straight-line fallback.
- **Time cap (item #2, `internal_gap` only):** added `MAX_INFERRED_GAP_SECONDS = 300` (5 minutes — same magnitude as `route_finalizer.py`'s existing `BACKSTOP_GRACE_SECONDS`). `route_reconstruction_projection.py`'s `project_observed_sections` now attaches each projected section's *whole-segment* (not per-chunk) first/last device-capture timestamp; when two consecutive projected sections come from different observed GPS segments, `route_reconstruction.py` computes the real elapsed time between them and refuses the connector if it exceeds the cap. `missing_start`/`missing_tail` anchors (booked pickup/dropoff coordinates, or a completion fix) have no comparable "before" GPS timestamp, so only the distance cap applies to them.
- A refused connector is recorded in `failed_gaps` (a real, non-empty list now) instead of being silently bridged. This reuses machinery that already existed and was already fully wired but dead: `route_finalizer.py`'s `_quality_projection`/`_final_status` already turn a non-empty `failed_gaps` into `incomplete_reason: "osrm_reconstruction_failed"` and `processing_status: "incomplete"`, and the retry loop (`retryable_reconstruction`) already retries a few times before settling on `reconstruction_status: "failed"`. No new downstream logic was needed — this was previously an unreachable branch.
- Internal-only timestamp fields added to projected sections (`segment_start_captured_at`/`segment_end_captured_at`) are stripped before a section reaches `reconstruct_completed_route`'s public `segments` output, so they never leak into the persisted `route_segments`/`road_matched_segments` JSON or the client-facing route rendering.

## 4. Risk & impact on existing functionality

- **Blast radius:** `reconstruct_completed_route` has 2 callers — `route_finalizer.py` (the production Period-3 route-finalization pipeline) and `ride_route_analyzer.py`'s `_reconstruct_route` (a read-only, offline diagnostic evidence-analysis tool, no DB writes of its own). `project_observed_sections` has 1 caller: `reconstruct_completed_route` itself. No other module imports either function.
- **Fare/billing is not directly at risk.** `route_finalizer.py`'s `resolve_measured_distance_km` — the function that decides what distance gets billed — already independently excludes straight-line connector distance from the billed figure entirely, and already falls back to the planned/booked distance whenever straight-line share is too high or coverage is too low. This existing safety net is unchanged by this fix; a refused (failed) gap is a strict subset of what that function was already treating as untrustworthy (a refused gap simply never gets *any* geometry, routed or straight, instead of getting a straight line that would have been excluded from billing anyway).
- **What changes in practice:** a ride whose GPS evidence has (a) a routed/straight-line gap wider than 10 km, or (b) an internal GPS outage longer than 5 minutes, now has that specific route drawn with a visible, honest break instead of a fabricated line across it, and its route finalization is marked `incomplete` (retried a few times, per the existing retry loop, then settled as `reconstruction_status: "failed"`). This is a narrow, boundary-condition-only behavior change — the two-segment, ~100-second-gap fixture already covered by the pre-existing test suite is unaffected (100s < 300s cap, ~400m gap < 10km cap), confirmed by the full existing suite passing unmodified.
- **`driver_insurance_periods`** itself is not written by this code path — this only affects the GPS route-*geometry* reconstruction used for the regulatory route/distance audit trail and the rider/driver-facing route rendering, not the append-only period-transition table.

## 5. User-experience effect

- **Rider- and driver-facing, but only in the rare boundary case.** A completed-ride route view that previously drew a straight or routed line across a >5-minute GPS outage or a >10km gap will now show that segment of the route as a visible break instead. For the overwhelming majority of rides (no gap this large), there is no visible change at all. Not visible mid-session — this only affects the *post-trip* finalized route view, not anything rendered during an active ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/route_reconstruction.py` | Added `MAX_INFERRED_CONNECTOR_KM`/`MAX_INFERRED_GAP_SECONDS`; `append_connector` now refuses an implausible gap into `failed_gaps` instead of bridging it; internal-gap call site computes real elapsed time between segments; added `_strip_internal_projection_fields` so the new timestamp fields never leak into public output; updated module docstring | Items #2 and #3 |
| `backend/utils/route_reconstruction_projection.py` | `project_observed_sections` now attaches each section's whole-segment `segment_start_captured_at`/`segment_end_captured_at` | Supplies the real timestamps the time cap needs |
| `backend/utils/route_finalizer.py` | Updated a now-stale comment claiming `retryable_reconstruction` was dead code | Doc accuracy only — no logic change |
| `backend/tests/test_route_reconstruction.py` | 2 new regression tests (distance cap, time cap) + an assertion that internal timestamp fields never appear in public output | Regression coverage |
| `backend/tests/test_route_reconstruction_projection.py` | Updated the exact-equality fixture for the 2 new fields; added a fallback-branch coverage test | Regression coverage |

## 7. Before / after

```python
# Before
async def append_connector(start, end, reason):
    gap_distance = distance_m(start, end)
    if gap_distance <= CONTINUITY_TOLERANCE_M:
        return
    connector_attempts += 1
    if connector_attempts > MAX_INFERRED_CONNECTORS:
        # always straight-line fills, however far apart
        ...
```
```python
# After
async def append_connector(start, end, reason, elapsed_seconds=None):
    gap_distance = distance_m(start, end)
    if gap_distance <= CONTINUITY_TOLERANCE_M:
        return
    if gap_distance / 1000.0 > MAX_INFERRED_CONNECTOR_KM:
        failed_gaps.append(f"{reason}_exceeds_distance_cap")
        return
    if elapsed_seconds is not None and elapsed_seconds > MAX_INFERRED_GAP_SECONDS:
        failed_gaps.append(f"{reason}_exceeds_time_cap")
        return
    connector_attempts += 1
    ...
```

## 8. Rollback plan

`git revert` is sufficient — this is application-code logic only, no schema/migration/config involved. No feature flag exists or is needed: the effect is a strictly narrower "believable gap" definition, and the fallback for a refused gap (`incomplete`, then retried, then `reconstruction_status: "failed"`) is existing, already-tested machinery, not new state. If the caps prove too aggressive against real traffic, the two constants (`MAX_INFERRED_CONNECTOR_KM`, `MAX_INFERRED_GAP_SECONDS`) can be tuned or reverted independently without touching the rest of the change.

## 9. Verification performed

- [x] Automated tests: `pytest backend/tests/test_route_reconstruction.py backend/tests/test_route_reconstruction_projection.py backend/tests/test_route_finalizer.py backend/tests/test_route_finalizer_loop.py backend/tests/test_route_finalizer_recompute.py backend/tests/test_e2e_route_tail_recovery.py backend/tests/test_period1_distance_finalizer.py backend/tests/test_period1_distance_finalizer_coverage.py --no-cov` — **51 passed** (49 pre-existing + 2 new dedicated regression tests, plus updates to 2 pre-existing tests for the new internal fields), including the full existing `test_route_reconstruction.py` suite unmodified in its assertions (confirming the ~100s/~400m fixture stays under both new caps).
- [x] `ruff check` on all 5 changed files — clean, 0 errors.
- [x] Blast-radius grep performed — confirmed 2 callers of `reconstruct_completed_route` (`route_finalizer.py` production path, `ride_route_analyzer.py` read-only diagnostic path), 1 caller of `project_observed_sections`.
- [x] Reviewed against CLAUDE.md's "do not silently swallow errors" / insurance-period conventions — a refused gap is now loud (recorded in `failed_gaps`, surfaces as `incomplete_reason` on the ride's route quality) rather than silently fabricated.
- [ ] Not feature-flagged — not applicable; this narrows an existing gap-fill boundary condition rather than introducing new user-visible functionality, and the fallback path it now reaches is pre-existing, already-tested machinery.

## 10. What was NOT verified

- No live/on-device confirmation against a real GPS trace with a >5-minute outage or a >10km gap — verified via unit tests with synthetic fixtures only.
- Did not verify against `ride_route_analyzer.py`'s own test suite in detail beyond confirming it shares the same `reconstruct_completed_route` call and therefore the same behavior — it is a read-only offline diagnostic tool, not a write path, so the risk there is display-only.
- Did not tune `MAX_INFERRED_CONNECTOR_KM`/`MAX_INFERRED_GAP_SECONDS` against real production gap-distribution data — both values were chosen to match the magnitude of an existing, already-reviewed constant elsewhere in the codebase (`MAX_UNTIMED_HOP_KM`, `BACKSTOP_GRACE_SECONDS`) rather than derived from a fresh analysis; flagged as a follow-up to revisit once real data is available.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`; the two constants can also be tuned independently).
- [x] Blast radius is stated, not assumed (2 + 1 callers, both grepped and confirmed).
- [x] No silent behavior change to an already-shipped rider/driver-facing flow without the UX field filled in — the only visible effect (a visible route break instead of a fabricated line, in the rare boundary case) is described in section 5.
