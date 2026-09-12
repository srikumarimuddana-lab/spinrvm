# 2026-09-12 — route finalizer: implausibility ceiling on the published measured distance (ADR 016 Phase 1)

Surface: backend (`utils/route_finalizer.py`) — completed-ride distance stats, display only. Live-tested (rides).
Incident: `SPR-EG7X86` (Android, 2026-09-12 01:52–02:07 UTC): all 564 GPS points present, worst gap 35 s, and the ride row still says **14.862 km** for a trip the booking put at 9.21 km and the spike-filtered GPS sum at 9.17 km.

| Field | Entry |
|---|---|
| **Issue/gap identified** | The finalizer published 14.862 km (`actual_distance_km`, `distance_km`, `phase_distances.trip_in_progress`) for a 9.2 km trip; the driver-facing trip distance, the daily rollup and the SGI Period-3 audit row all carry it. |
| **Root cause** | `resolve_measured_distance_km` had a floor for impossibly SHORT results (the 2.6-km-for-1.8-km incident) and nothing for impossibly LONG ones. OSRM map matching over a dense two-stream trail (foreground + background producers) returned 11.216 km, and gap reconstruction added 3.893 km of routed "connectors" for 99 s of gaps (`ride_routes.route_quality`: `inferred_gap_count: 6`, `inferred_distance_ratio: 0.258`) — 15.1 km, accepted as `observed` because coverage was 0.997. The inline settlement-time path had already computed a sane 10.147 km (`rides.route_quality.actual_distance_km_road_snapped`); the finalizer's later write overrode it. |
| **Fix/remediation** | `resolve_measured_distance_km` gains `gps_km` (the spike-filtered straight-segment sum, `distances.actual_distance_km_haversine`) and `max_vs_reference` (default 1.3; settings row key `route_distance_max_vs_reference_ratio`, read like the two existing knobs). When a booked distance exists and the candidate exceeds `max_vs_reference × max(planned, gps_km)`, the booked distance is published with basis `planned_capped` (audit trigger `implausible_distance_cap`) and a WARNING log carries the numbers. Anchored on the booking: the GPS chord sum is a lower bound (a hole contributes one chord), so it only ever *raises* the ceiling — a real detour lengthens the GPS sum too and passes. |
| **Risk & impact on existing functionality** | Blast radius: `resolve_measured_distance_km` has one caller, `_recompute_ride_distance_stats` (finalizer). Readers of what it writes: `rides.distance_km` (display under fare-lock; the dormant billing input when fare-lock is off — unchanged by this PR, and a cap can only lower it), `actual_distance_km`, `phase_distances` → `utils/driver_activity.py` (daily report), `utils/period_distance_audit.py` / `driver_period_distances` (SGI revision rows), `ride_route_analyzer` (copies `distance_basis`). New basis string `planned_capped` flows into `ride_metrics.phases.trip_in_progress.distance_basis` and `ride_distance_recomputes.trigger` — both free text. Fare never touched (`total_fare`, snapshots untouched; tests pin it). Rides with no booked distance (`planned_distance_km` 0/absent) are exempt from the ceiling — the existing floor/straight-share rules still apply. |
| **User experience effect** | Drivers: trip distance on the ride card / daily report for an over-matched ride shows the booked distance instead of an inflated one. Riders: receipts already label the quoted distance (Phase 0); the ride-details / ride-completed route-quality line reads "Distance from booking · GPS route implausible" for a capped ride instead of "Route reconstructed · 74% GPS observed · 26% inferred". Admin: same label in the ride detail modal; `distance_basis = planned_capped` in route quality. Not visible mid-session (finalizer runs after completion). New copy ships with the OTA for the apps; the admin change is in `shared/`, rebuilt with the dashboard. |
| **Files modified** | `backend/utils/route_finalizer.py` — ceiling in the resolver, knob read + WARNING at the call site, trigger map entry. `backend/tests/test_measured_distance_resolver.py` — 6 new cases (EG7X86 numbers, real detour, larger reference, configurable ratio, no-booking exemption, YNYA93 locked-phone ride). `shared/utils/routeSegments.ts` — `routeQualityLabel` gains a `planned_capped` branch ("Distance from booking · GPS route implausible") ahead of the observed/inferred-ratio copy, which otherwise describes the *discarded* reconstruction (money-audit finding); consumers: admin `ride-detail-modal.tsx`, rider-app `ride-details.tsx` / `ride-completed.tsx`. `shared/utils/__tests__/routeSegments.test.ts` — +1. |
| **Before/after snippet** | See below. |
| **Rollback plan** | Settings row: `UPDATE settings SET route_distance_max_vs_reference_ratio = 1000 WHERE id = 'app_settings'` disables the ceiling without a deploy (a column may need adding first — the read is `dict.get` with default, so the absence of the column is the default 1.3). Code: `git revert` + Fly deploy. Already-capped rides keep `planned_capped`; a re-finalization (`ride_routes` requeue) recomputes. |
| **Verification performed** | `pytest tests/test_measured_distance_resolver.py tests/test_e2e_route_tail_recovery.py tests/test_route_finalizer*.py` — **43 passed**; `ruff check` + `ruff format --check` clean. `shared/utils/__tests__/routeSegments.test.ts` via rider-app jest — 6 passed; `tsc --noEmit` clean for rider-app and admin-dashboard. `spinr-money-auditor` on the branch: safe to merge, no money path reachable (traced `recalculate_fare_for_distance`'s single settlement-time caller, the fare-lock-gated `distance_km` write, earnings/T4A/corporate/incentive readers); its one warning (the label) fixed here. Dry run against the three real rides' numbers (from `ride_routes.route_quality` / `rides.route_quality`): EG7X86 15.109 → capped to 9.21; YNYA93 9.981 vs planned 10.08 → kept, `reconstructed`; T9NYPB (12.24 vs planned 6.89 / GPS 8.98) → would cap to 6.89. |
| **What was NOT verified** | The new label copy was not screenshotted — rider-app has no visual-regression tooling, and the admin ride-detail modal is not one of the six seeded Playwright pages. Not run against a live finalizer queue; the numbers are the stored reconstruction outputs, not a re-execution of OSRM. The settlement-time inline band (1/3×–3× haversine in `utils/trip_distance.py`) is unchanged — ADR 016 Phase 0's "demote inline promotion" item still waits on product sign-off #1. The 1.3 ratio is a judgement call from three rides; a settings knob exists so it can be tuned without a deploy. |

## Before / after

```python
# before — nothing above the floor
if straight_line_km and candidate < min_vs_straight * float(straight_line_km):
    return planned, "planned_estimated"
if candidate > 0 and straight_share <= max_straight_share:
    return candidate, "observed" if coverage >= min_coverage else "reconstructed"

# after
if straight_line_km and candidate < min_vs_straight * float(straight_line_km):
    return planned, "planned_estimated"
planned = float(planned_km or 0)
if planned > 0 and candidate > max_vs_reference * max(planned, float(gps_km or 0)):
    return round(planned, 3), "planned_capped"
...
```

Worked example (SPR-EG7X86): observed 11.216 + routed 3.893 = 15.109 > 1.3 × max(9.21, 9.17) = 11.97 → publish 9.21, basis `planned_capped`.
