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

The single "Insurance Usage-Based Billing" report aggregated insured km per driver only, required the admin to type in a rate every time, and didn't show which trip/phase the km came from — no way for an insurer to audit down to a trip. Two insurers need separate reports at two different fixed rates (SGI $0.11/km, Knight Archer $0.011/km). The "Insurance-Period Regulatory Audit" report's content (driver, phase, timestamps) became fully redundant with the new per-trip billing reports. "Knight Archer Driver Onboarding" named a generic driver-roster export after one specific consumer. Several date-ranged Compliance reports used a rolling-N-days shorthand (today/7d/30d/90d/1y) instead of an explicit From/To range defaulting to the current calendar month.

## 2. Root cause

Not a bug — a product rework, explicitly requested and confirmed via two clarifying questions (rate units: SGI $0.11/km / Knight Archer $0.011/km, not fractions of a cent; and confirmation to retire the audit report rather than keep or hide it).

## 3. Fix / remediation

**Backend (`backend/routes/admin/compliance.py`):**
1. New `_insurance_billing_detail_rows(start, end, rate_per_km)` reads `driver_period_distances` (migration 249 — GPS-measured driven distance *per insurance period*), not `rides.distance_km`. One row per (driver, ride, phase) — each phase shows its own real leg distance. This is more correct than the retired report's approach, which summed the *same* `rides.distance_km` into both Period 2 and Period 3 and had to de-duplicate per ride to avoid double-billing; per-phase GPS distance has no such ambiguity.
2. Two new endpoints share that builder: `GET /compliance/insurance-billing-sgi` (rate fixed at `Decimal("0.11")`) and `GET /compliance/insurance-billing-knight-archer` (rate fixed at `Decimal("0.011")`) — no rate query param, so a typo can no longer misstate an invoice.
3. `GET /compliance/insurance-period-audit` and its row-builder removed entirely (retired per user confirmation — "Retire it — remove the tab").
4. `GET /compliance/knight-archer-driver-onboarding` renamed to `GET /compliance/driver-roster` (`_driver_roster_rows`, `report_type: "driver_roster"`) — same query/columns, generic name.
5. New `_resolve_date_window(date_from, date_to)` — explicit `YYYY-MM-DD` query params, defaulting to the 1st of the current month through today when omitted (either side independently). Applied to GST/PST Remittance, Airport Trips, and both new billing endpoints, replacing the old `date_range` shorthand. `_parse_date_range` (now unused) removed.
6. `report_branding.REPORT_FORMAT_REGISTRY` updated: `insurance_period_audit` removed, `insurance_usage_billing` replaced by `insurance_billing_sgi` / `insurance_billing_knight_archer`, `driver_roster` added (was missing from the registry even under its old name — a pre-existing gap, now closed).

**Frontend (`admin-dashboard/src/app/dashboard/compliance/page.tsx`, `src/lib/api/data-transfer.ts`, `src/lib/api.ts`):**
1. Tabs: "Insurance Usage Billing" (rate input) → "SGI Insurance Billing" + "Knight Archer Insurance Billing" (no rate input, fixed server-side). "Insurance-Period Audit" tab removed. "Knight Archer Driver Onboarding" → "Driver Roster".
2. New shared `DateRangeFields` component (native `<input type="date">` From/To) replaces the rolling-range `<Select>` on GST/PST, Airport Trips, and both billing tabs. `monthToDateDefaults()` seeds each pair of From/To state to the current calendar month, matching the backend default.
3. `lib/api/data-transfer.ts`: `downloadGstPstRemittance`/`downloadAirportTrips`/new billing downloaders take `(format, dateFrom?, dateTo?)` instead of a `dateRange` string; `downloadInsurancePeriodAudit`/`downloadKnightArcherDriverOnboarding`/`downloadInsuranceUsageBilling` removed; `downloadDriverRoster`/`downloadInsuranceBillingSgi`/`downloadInsuranceBillingKnightArcher` added. Same pattern for the `email*` functions.

**Records module (Data Transfer / Search & Select) — deliberately NOT changed.** Its Search & Select tab already has From/To date fields (`EntitySearchTable.tsx`), satisfying "add from and to date filter." I did **not** default those to the current month: that tool selects entities for cross-environment migration/export, where "all users regardless of onboarding date" is normal and defaulting to month-to-date would silently hide most of the userbase from a migration admin. Flagging this explicitly — if the month-default was intended there too, say so and I'll add it.

## 4. Risk & impact on existing functionality

- **Blast radius: `compliance.py`'s date-ranged endpoints are grepped for every consumer.** Only the Compliance page (`admin-dashboard/src/app/dashboard/compliance/page.tsx`) calls any of these — no other admin page, mobile app, or backend module references `/compliance/insurance-*`, `/compliance/knight-archer-*`, `/compliance/gst-pst-remittance`, or `/compliance/airport-trips`. Confirmed via repo-wide grep before and after.
- **Breaking API change, accepted as low-risk:** `date_range=<shorthand>` query param no longer works on GST/PST, Airport Trips, or the billing endpoints (422 if a caller still sends it, since it's simply ignored/unrecognized — no crash). Internal admin-only surface, no external API consumers, same PR updates the only frontend caller. Not additive/flagged because there's no mid-session user experience to protect here (an admin picking a report's date range isn't "mid-flow" the way a rider mid-ride is).
- **`driver_period_distances` dependency:** the two new billing reports now depend on this table being populated (`utils/period_distance_audit.py`, called from ride settlement) rather than `rides.distance_km` + `driver_insurance_periods`. If GPS-measured phase distance was ever skipped for a ride (audit-write failures are best-effort per that module's own docstring), that ride's km won't appear in either billing report. This is a real behavior difference from the retired report (which always had *some* number, from `rides.distance_km`, even if double-counted) — flagging as the main thing to watch after this ships: if billing totals look unexpectedly low for a given month, check `driver_period_distances` coverage before assuming a code bug.
- **Removed report retention:** `insurance_period_audit`'s specific report/endpoint is gone; anyone who bookmarked or scripted against that exact URL gets a 404. No migration path was built (the user confirmed removal, and this is a read-only reporting endpoint — no stored state to migrate).

## 5. User-experience effect

**Internal admin only, Compliance module.** Two renamed/restructured tabs, one tab removed, three tabs' date controls changed from a dropdown to two date pickers with a new default range (current month instead of "last 30 days"). No rider/driver/corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/compliance.py` | New `_insurance_billing_detail_rows` + 2 endpoints; removed audit report; renamed KA onboarding → driver roster; new `_resolve_date_window` applied to 4 endpoints | Core of the rework |
| `backend/utils/report_branding.py` | Registry entries updated to match | Consistency |
| `backend/tests/test_compliance_reports.py` | `TestInsurancePeriodRows` → `TestInsuranceBillingDetailRows` (new fixture, new assertions) | Coverage for new logic |
| `backend/tests/test_compliance_reports_http.py` | Removed audit-report tests, renamed KA tests, added SGI/KA billing tests, added date-window tests | Coverage |
| `backend/tests/test_compliance_rate_limit.py` | Renamed rate-limit test class/route | Matches renamed endpoint |
| `backend/tests/test_report_branding.py` | Updated registry assertions | Matches new registry |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | Tab restructure, `DateRangeFields` component, state/handler rewrite | UI |
| `admin-dashboard/src/lib/api/data-transfer.ts` | New/renamed download+email functions, `dateWindowParams` helper | Frontend client |
| `admin-dashboard/src/lib/api.ts` | Updated re-export list | Barrel file |

## 7. Before / after

```python
# Before: aggregate per driver, admin-supplied rate, ride distance double-counted across phases
async def _insurance_usage_billing_rows(start_date, end_date, rate_cents_per_km):
    periods = await db_supabase.get_rows("driver_insurance_periods", {...})
    ...  # de-dupe by (driver_id, ride_id) to avoid double-billing rides.distance_km

# After: one row per (driver, ride, phase), each phase's own GPS-measured distance, fixed rate
_SGI_RATE_PER_KM = Decimal("0.11")
async def _insurance_billing_detail_rows(start_date, end_date, rate_per_km):
    distances = await db_supabase.get_rows("driver_period_distances", {"period": {"$in": [2, 3]}, ...})
    for d in distances:
        km = _d(d.get("distance_km"))
        amount = (km * rate_per_km).quantize(Decimal("0.01"))
        rows.append({"driver_name": ..., "trip_date": ..., "phase": ..., "phase_km": f"{km:.3f}", ...})
```

## 8. Rollback plan

`git revert` both backend commits and the frontend commit — no data written, no migration, no flag involved; the removed `insurance-period-audit` endpoint/tab would come back as-is since nothing was deleted from the database.

## 9. Verification performed

- [x] `pytest backend/tests/test_compliance_reports.py backend/tests/test_compliance_reports_http.py backend/tests/test_compliance_rate_limit.py backend/tests/test_report_branding.py` — 87/87 passing.
- [x] `ruff check` on all touched backend files — clean.
- [x] Real production build (`npm run build`) for `admin-dashboard` — succeeded, including `/dashboard/compliance`.
- [x] `npx tsc --noEmit` — no errors in any touched file (pre-existing, unrelated errors in `__tests__/route-segments.test.ts` and `companyApi.test.ts` confirmed present before this change).
- [x] `npx eslint` on the 3 touched frontend files — clean except one pre-existing unused-arg warning in `data-transfer.ts` (confirmed present before this change, not introduced here).
- [ ] Not visually verified in a browser (no browser available this session).
- [ ] Not run against real production `driver_period_distances` data — mocked in tests per this repo's convention. Given the risk noted in §4 (dependency on that table's coverage), worth a spot-check against real data before the first real monthly SGI/Knight Archer invoice is sent.

## 10. What was NOT verified / deferred

- **Real coverage of `driver_period_distances`** for the months these reports will actually be run against — not checked from this environment. If a chunk of trips are missing phase-distance rows (e.g. from a period before migration 249 was live, or from any past audit-write failures), the new reports will under-report km relative to what the retired report would have shown for the same window. Worth a one-time reconciliation check before the first real invoice.
- **Records module's Search & Select tab was deliberately left with an unbounded (no-default) date filter** rather than defaulting to month-to-date — see §3's note. Confirm this is the intended scope before considering the "add from/to date filter to all reports in records and compliance" ask fully closed.
- Airport Trips report's exact column set is still not confirmed against any specific airport authority's published spec (flagged in the earlier Airport Trips change log; unchanged by this work).
