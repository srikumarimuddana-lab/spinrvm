# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, safety |
| PR / commit link | (this branch) |
| Related issue or gap ID | Explicit user request this session |

## 1. Issue / gap identified

Two new reporting gaps identified this session: (1) Spinr bills its commercial TNC insurer on a usage basis (cents per kilometre driven while on cover) but had no report aggregating per-driver insured kilometres for reconciling that invoice; (2) no report existed for airport-originated/destined trips, needed for airport ground-transportation program reporting.

## 2. Root cause

Not a bug — new reporting capability, both explicitly requested.

## 3. Fix / remediation

1. **`GET /api/admin/compliance/insurance-usage-billing`** — sums `rides.distance_km` for every ride where the driver was in Insurance Period 2 (en route) or 3 (passenger aboard) during the requested window, joining `driver_insurance_periods` to `rides` via `ride_id`. Deduplicates a ride that has BOTH a Period 2 and Period 3 row (the normal lifecycle for every completed ride) so its distance is counted once, not twice. Takes `rate_cents_per_km` as a required query param with **no built-in default** — the actual contracted rate is a business detail this code has no way to know, and guessing one would silently misstate every invoice; the report shows the km × rate math inline so a wrong rate is obvious before the report is sent.
2. **`GET /api/admin/compliance/airport-trips`** — completed rides where "airport" (case-insensitive) appears in the pickup or dropoff address, with trip type (Pickup/Dropoff/Both), distance, driver name, and service area (city). Matched by text search rather than a dedicated flag — rides don't reference the existing curated `venues` table by ID, so address matching is what's actually queryable today. Column set follows the general convention most North American airport TNC ground-transportation programs use; explicitly documented (in both code comments and the UI hint) as NOT a confirmed published spec from a specific Saskatchewan airport authority, since none was available to verify field requirements against.
3. Both follow the exact established Compliance-report pattern: Spinr-branded output (all 4 formats), audit logging, the dual-approval export gate, Sentry-tagged failure capture, and registration in `report_branding.REPORT_FORMAT_REGISTRY`.
4. Admin-dashboard: two new tabs on the Compliance page, following the same card/select/download layout as the existing reports.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated — two new read-only endpoints, no writes.** Grepped for other callers of `driver_insurance_periods`/`rides.distance_km` in the report path: none conflict, this is a new aggregation over existing data.
- No schema, no API contract change to anything existing.
- The insurance billing report's per-ride dedup logic is the one piece of real aggregation complexity — explicitly regression-tested (`test_insurance_usage_billing_counts_ride_once_across_periods`) against exactly the double-count failure mode it exists to prevent.

## 5. User-experience effect

- **Internal admin only.** Two new tabs on the Compliance page. No rider/driver/corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/compliance.py` | 2 new row-builders + 2 new endpoints | Core of the fix |
| `backend/utils/report_branding.py` | 2 new `REPORT_FORMAT_REGISTRY` entries | Consistency |
| `backend/tests/test_compliance_reports_http.py` | 10 new tests | Coverage |
| `admin-dashboard/src/lib/api.ts` | 2 new download functions | Frontend client |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | 2 new tabs | UI |

## 7. Before / after

```python
# Before: no report aggregated insured km per driver, or airport trips at all.

# After — insurance billing (the ride-double-count guard is the key logic):
seen = seen_ride_per_driver.setdefault(driver_id, set())
if ride_id in seen:
    continue  # already counted this ride's km for this driver
seen.add(ride_id)
km_by_driver[driver_id] += distance_by_ride.get(ride_id, Decimal("0"))
```

## 8. Rollback plan

Plain `git revert` — two new read-only endpoints, no data written, no flag involved.

## 9. Verification performed

- [x] `pytest backend/tests/test_compliance_reports_http.py backend/tests/test_report_branding.py` — 72/72 passing, including the double-count regression test and an airport-address-filter test confirming non-airport rides are excluded.
- [x] `ruff check` clean.
- [x] Real production build (`npm run build`) for `admin-dashboard` — succeeded, both new tabs compile.
- [ ] Not visually verified in a browser (no browser available this session).
- [ ] Not run against real production ride/insurance-period data — mocked in tests per this repo's convention.

## 10. What was NOT verified / deferred

- **The airport-trips report's exact column set is NOT confirmed against any specific airport authority's published requirements** — both the code comments and the in-app hint say this explicitly. Before submitting this report to an actual airport ground-transportation program, confirm the required fields with that authority directly.
- The insurance usage billing report has no default/remembered rate — an admin must re-enter it every time, which is intentional (see §3) but is friction worth revisiting if the rate rarely changes (e.g., store it as a `settings` value with an override option) — not built here to avoid a second, unrequested feature.
- No municipal vehicle-for-hire volume reporting was built — flagged in the prior research response as depending on specific municipal bylaw terms not determinable from the codebase.
