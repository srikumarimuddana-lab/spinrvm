# Phases 3+4 — admin Distance UI, insurer P1 rows, customer pickup-leg surfaces

**Date:** 2026-08-19
**Surface:** backend admin/compliance/receipts + admin-dashboard + rider-app + driver-app (live-tested), 9 commits
**Trigger:** tracking-overhaul roadmap Phases 3 (Distance Travelled/Logs admin UI) and 4 (insurer + customer surfaces). Owner decisions locked: P1 informational-only in insurer billing; customer surfaces dark behind `rider_show_pickup_leg_enabled`.

## Issue/gap identified
The per-phase distance data (Phases 0–2) had no product surface: admins could not see per-day driver distances or drill into a day; insurers had no view of contingent (Period-1) exposure; riders/drivers could never see the pickup approach even where it would explain a receipt.

## Fix/remediation (by commit)
1. `e327e53` — `routes/admin/driver_distance.py`: `distance-travelled` (per-Regina-day km+seconds per phase; closed days from `driver_daily_stats`, in-progress day computed live; json/csv/pdf/xlsx via the shared branded renderer; 92-day cap) and `distance-logs` (per-day insurance-period spans joined with `driver_period_distances_current`, ride codes, clip-to-day). Mounted under `require_module("drivers")`.
2. `67c1b8f`+`781df56` — admin dashboard: API client + a new **Distance** tab in the driver Sheet (30-day pageable table, expandable per-day logs with phase chips and revision-source hover, CSV/XLSX/PDF export via authenticated blob fetch).
3. `4780aef` — compliance: `include_period_1` on the SGI/Knight Archer billing endpoints appends "Period 1 — contingent, not billed" rows (rate "—", amount "Not billed") after the billed rows; billed totals provably untouched (flag-off byte-identical, pinned). Shared `_driver_billing_identity` extraction.
4. `05cf2f8` — `get_ride(include_route=True)` honors `rider_show_pickup_leg_enabled` (default off; settings outage → trip-only contract).
5. `3cbe1f4` — shared `routeSegments` normalizer carries `phase` (validated 3-value union).
6. `962b634`+`7cebc3b` — rider ride-details/ride-completed and driver ride-detail render the pickup leg as a dashed grey layer UNDER the untouched RouteLine trip gradient; the "no raw Polyline" contract tests now sanction exactly this one use.
7. `4c55024` — email receipt: flag-gated "Driver's approach to pickup: X km — not charged" beside the route map, outside the fare table.

## Risk & impact on existing functionality
- **RouteLine (shared, 3+ pages)**: deliberately NOT modified. The dashed layer is per-screen and additive; with the flag off the server sends trip-only segments, so all three screens render byte-identically to before (zero P2 sections → zero dashed polylines).
- **`toGeoJsonMultiLineString`/`toReactNativeSegments` consumers** (share-track page, admin maps): phase is additive metadata; geometry arrays unchanged.
- **Insurance billing money path**: P1 section is a separate helper — no parameter combination routes P1 km into `grand_total_km`/`total billed` (pinned by `test_p1_km_never_reach_the_billed_totals`); the extraction of `_driver_billing_identity` is pure code motion (billed-rows tests unchanged and green).
- **Receipt fare math**: untouched under both flag states (pinned); the context line contains no dollar figure.
- **Admin route namespace**: new paths `distance-travelled`/`distance-logs` under `/drivers/{id}/` verified against existing `daily-activity`/`stats`/`rides` (route-shadowing guard suite green).
- **Manual reviewer passes** (Codex absent per repo status): `spinr-migration-reviewer` (Phase 2 migrations — blocker fixed), `spinr-money-auditor`, `spinr-admin-rbac-reviewer`, `spinr-insurance-period-auditor` run on this batch; findings addressed before push.

## User experience effect
- **Internal admin (visible immediately on deploy)**: new Distance tab in the driver panel; insurance billing exports gain an opt-in query param (default output unchanged).
- **Rider/driver (dark until flag flip)**: nothing changes while `rider_show_pickup_leg_enabled` is off. When flipped: detail maps gain a dashed grey approach line; new receipts gain one informational sentence. Mid-session visibility: detail screens fetch per view, so the change appears on next open — no mid-ride surprise.

## Rollback plan
Customer surfaces: flip `rider_show_pickup_leg_enabled` off (admin Settings, no redeploy) — server stops sending P2 segments and the receipt line; clients render as before with no client update. Insurer rows: per-request opt-in (`include_period_1`) — simply don't pass it. Admin Distance tab: UI-only; revert commit or leave (reads are module-gated). No data mutation anywhere in these phases.

## Verification performed
- Backend: full fast suite green before push (12k+ tests); new suites `test_admin_driver_distance.py` (6), `TestPeriod1InformationalRows` (3), `TestGetRidePickupLegFlag` (3), `TestPickupLegContextLine` (4); receipt/email/compliance suites (157) green.
- Admin dashboard: `tsc --noEmit` clean, vitest 223/223, **real `next build` run and green** (per the release-gate rule).
- Rider + driver apps: `tsc --noEmit` clean; full jest suites run before push; route-contract tests updated to sanction exactly one raw Polyline per screen.
- Mobile: **no EAS/production mobile build was run** (unavailable in this environment) — jest + tsc only, stated explicitly.

## What was NOT verified
- No visual regression tooling exists for any surface (standing gap) — the dashed-line rendering and Distance tab layout were reasoned about, not screenshotted; MapLibre/react-native-maps rendering needs a staging device pass before the flag flips.
- PDF/XLSX branded exports exercised through the shared renderer's existing tests, not opened by a human.
- Live Supabase untested; `driver_period_distances_current` joins exercised via mocks.
