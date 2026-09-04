# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin, rides |
| PR / commit link | (this branch — `claude/saskatoon-city-monthly-report-7xwtco`) |
| Related issue or gap ID | Explicit user request this session; echoes the "no municipal vehicle-for-hire volume reporting" gap flagged in `docs/change-log/2026-07-30-insurance-billing-airport-trips-reports.md` §10 |

## 1. Issue / gap identified

Spinr already has a monthly report for SGI (the insurance side — "SGI Insurance Billing" / "Knight Archer Insurance Billing" tabs). There was no equivalent report for the City of Saskatoon's own municipal reporting (the requester's "CITY part"): a per-trip log covering completed rides and rider/driver cancellations that happened after a driver had accepted, with a Trip_Status column, filterable by date. Follow-up request in the same session: also include the assigned driver's license number per trip.

## 2. Root cause

Not a bug — new reporting capability, explicitly requested. This is the first of two City-of-Saskatoon reports the requester asked for; the second was not scoped in this session.

## 3. Fix / remediation

1. **`GET /api/admin/compliance/saskatoon-city-trip-log`** (`backend/routes/admin/compliance.py`) — every ride in the Saskatoon service area *requested* in the selected window that either completed, or was cancelled by the rider or driver after `driver_accepted_at` was already set. Emits exactly the six columns the requester specified: `Request_Timestamp`, `Accept_Timestamp`, `Begin_Timestamp`, `End_Timestamp`, `Passenger_Wait_Time (Mins)`, `Trip_Status` (`Completed` / `Cancelled by Rider` / `Cancelled by Driver`). Follows the established Compliance-report pattern exactly: Spinr-branded output via the shared `_render_tabular_report` (PDF/CSV/Excel/Word), `compliance_export_events` audit logging, the dual-approval export gate, and Sentry-tagged failure capture.
2. Saskatoon is resolved by name (`service_areas.name ILIKE '%Saskatoon%'`) rather than a hardcoded id, and the lookup **raises** if nothing matches rather than silently reporting on every service area.
3. Three domain decisions were ambiguous enough to not guess silently (per `CLAUDE.md`'s "surface assumptions, don't silently resolve them") — confirmed with the requester via `AskUserQuestion` before implementing:
   - **Passenger wait time** = `ride_started_at − ride_requested_at`.
   - **Cancelled rows** leave `Begin_Timestamp`/`End_Timestamp`/wait-time blank (trip never started).
   - **Trip_Status** for a cancellation names who cancelled ("Cancelled by Rider" / "Cancelled by Driver").
   One further assumption was *not* put to the requester (judged low-risk/easily-corrected, flagged instead via code comment + in-app `Hint`, the same convention `_airport_trips_rows` uses for its own column-set assumption): the report is dated by `ride_requested_at`, not `ride_completed_at`/`cancelled_at`, so one date filter applies uniformly to both completed and cancelled rows.
   "Cancelled after acceptance, rider or driver" is `driver_accepted_at IS NOT NULL AND cancelled_by IN ('rider','driver')` — excludes cancellations before any driver accepted (searching / offer-pending) and excludes system/admin/corporate-suspension cancellations outright, per the requester's literal scope ("rider or driver cancels").
4. Admin-dashboard: new "Saskatoon City" tab on the Compliance page, following the existing card/select/download layout. Defaults to CSV (not PDF, unlike the other tabs) since the City's spec is a literal column-header list, most likely consumed as a spreadsheet. Excluded from the page-level Service Area filter (like T4A) since it's always Saskatoon-only server-side; the filter's own hint text was updated to say so, since it previously claimed T4A was the *only* exception.
5. **Follow-up (same session): `Driver_License_Number` column added**, appended after `Trip_Status` so the six City-specified columns keep their original order. Every retained row (completed, or cancelled-after-acceptance) always has a `driver_id`, so the lookup is always meaningful. Driver ids from the *kept* rows are batched (`$in`, chunks of 200 — same convention `_airport_trips_rows`/`_driver_roster_rows` use) against `drivers`, and `license_number` is decrypted with the same `_decrypt_driver_pii` vault round-trip `_driver_roster_rows` already uses elsewhere in this file — not a new PII-exposure pattern, the same one already shipped for the Driver Roster and SGI-form exports, gated the same way (super_admin only).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated — one new read-only endpoint, no writes, no schema change.** Grepped for other readers/writers of `rides.driver_accepted_at`, `.ride_started_at`, `.cancelled_by`, `.service_area_id` in the report path: this is a new read-only aggregation over existing columns already written by `routes/rides/matching.py`, `routes/rides/cancellation.py`, and `routes/drivers/ride_flow.py`/`ride_cancel.py` — nothing here writes to `rides` or any other table.
- Two lines of pre-existing UI copy (the page-level Service Area filter's hint text and its surrounding comment) were edited to stay accurate now that a second tab (this one) also bypasses that filter — a correctness fix forced by the new tab, not a scope-driven rewrite.
- No interaction with the ride state machine, background loops, or money/wallet deltas — this report only reads already-terminal `completed`/`cancelled` rides.
- **PIPEDA note on the follow-up `Driver_License_Number` column:** a driver's license number is on the "never in logs/Sentry/analytics" list in `CLAUDE.md`'s Compliance section, but that rule targets logging/telemetry, not a super_admin-gated regulatory export — `_driver_roster_rows` (same file) and the SGI D00032/D00033 forms already export the same decrypted field to a regulator/insurer. This reuses that exact precedent and access gate rather than establishing a new one. Grepped `drivers.license_number` for other readers: only `_driver_roster_rows` and the SGI form filler read it elsewhere, both via the same `_decrypt_driver_pii` helper — no divergence introduced.

## 5. User-experience effect

- **Internal (super_admin) only.** One new tab on the Compliance page, gated the same way every other tab on that page already is (`_require_super_admin` server-side, `useRequireSuperAdmin` client-side). No rider/driver/corporate-admin-facing change, and nothing visible mid-session to anyone using the rider/driver apps.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/compliance.py` | +191 lines, then +54/-21 in the license-number follow-up: `_resolve_saskatoon_service_area_ids`, `_report_minutes_between`, `_saskatoon_city_trip_log_rows` (now also batches+decrypts driver license numbers for kept rows), `GET /saskatoon-city-trip-log` | Core of the new report |
| `backend/tests/test_compliance_reports.py` | +192 lines, then +82/-4 in the follow-up: 12 tests total, covering inclusion/exclusion rules, area scoping, the missing-area error, the wait-time helper, and the license-number lookup/decrypt/batching wiring | Coverage |
| `admin-dashboard/src/lib/api/data-transfer.ts` | +17 lines: `downloadSaskatoonCityTripLog` | Frontend client |
| `admin-dashboard/src/lib/api.ts` | +1 line: re-export | Frontend client |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | +96/-13 lines, then +7/-1 in the follow-up: new tab, state, handler, updated Service Area filter hint text, Hint copy mentioning the license-number column | UI |

## 7. Before / after

```text
# Before: no City-of-Saskatoon trip-log report existed. The only cancellation
# attribution reported anywhere in Compliance was cancellation_type inside
# the raw rides table — never surfaced as a rider-vs-driver-after-acceptance
# distinction in any generated report.
```

```python
# After — the inclusion/exclusion rule (backend/routes/admin/compliance.py):
elif status == "cancelled":
    cancelled_by = r.get("cancelled_by")
    if not r.get("driver_accepted_at") or cancelled_by not in ("rider", "driver"):
        # Cancelled before any driver accepted, or cancelled by someone
        # other than the rider/driver — out of scope for this report.
        continue
    trip_status = f"Cancelled by {cancelled_by.capitalize()}"
```

## 8. Rollback plan

Plain `git revert` of the commits on this branch — one new read-only endpoint and one new UI tab (plus the license-number column added to both in a same-day follow-up), no data written, no migration, no flag involved. Nothing to unwind on the data side since nothing was ever written.

## 9. Verification performed

- [x] `ruff check` and `ruff format --check` clean on both changed Python files.
- [x] `python3 -c "import ast; ast.parse(...)"` syntax-checked both changed Python files.
- [x] Manual line-by-line trace of `_saskatoon_city_trip_log_rows` against each of the 12 test cases (completed timeline math, blank cancelled-row fields, rider/driver/system/admin cancellation attribution, Saskatoon-only scoping, the missing-area `RuntimeError`, and — added in the follow-up — the driver-license batching/decrypt wiring and the "excluded rows never trigger a drivers lookup" property), including tracing `AsyncMock(side_effect=lambda d: d)`'s behavior as a passthrough for the patched `_decrypt_driver_pii`.
- [x] Manual review of the frontend diff against the working `airport-trips` tab it mirrors — tag structure, state wiring, and the API client's URL/param shape all matched line-for-line except the intentional removal of the service-area param.
- [ ] **`pytest` was NOT run.** This sandboxed session's outbound network policy blocks `pypi.org`/`files.pythonhosted.org` (confirmed via direct `curl` → 403 "Host not in allowlist"), so `pip install -r backend/requirements.txt` could not complete and the backend test suite could not execute here, for either the original report or this follow-up. The 12 tests in `test_compliance_reports.py` are written and lint-clean but **unexecuted** — run `pytest backend/tests/test_compliance_reports.py -k Saskatoon` before treating this as verified.
- [ ] **No production build was run.** `admin-dashboard/node_modules` isn't installed in this session and `npm install` would hit the same network restriction, so neither `tsc`/`eslint` nor `npm run build` were run. Per `CLAUDE.md` this means the frontend change is **not** verified to the bar this repo requires for an `admin-dashboard` change — a dev server or `tsc --noEmit` wasn't even attempted, let alone a real build.
- [ ] Not visually verified in a browser.
- [ ] Not run against real production ride/service-area data.

## 10. What was NOT verified / deferred

- **The exact column set and date-field definition are NOT confirmed against the City of Saskatoon's actual published reporting spec.** `ACTION_ITEMS.md` G2 (open as of 2026-08-21) already flags that even Saskatoon's bylaw number/fee schedule aren't confirmed directly with the City — this report's field semantics (wait-time formula, request-date framing, blank-vs-cancellation-time for cancelled rows) were confirmed with the requester, not against a City document. Both the code comments and the in-app `Hint` say this explicitly, matching how `_airport_trips_rows` flags its own unconfirmed-authority-spec assumption.
- **The backend test suite could not be executed in this session** (see §9) — this is the most material open item; run it before merge.
- **No production/dev build of `admin-dashboard` was run** (see §9).
- The second City-of-Saskatoon report the requester mentioned ("two reports to be generated") was not scoped or built — only the trip log was requested to start with ("can you start with this").
