# Change Impact & Risk Log — Heatmap Pre-Deploy Review Remediation

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend, driver-app, admin-dashboard |
| Domain (Sentry tag) | drivers, admin, dispatch-adjacent (read-only) |
| Related | `docs/reviews/2026-08-13-heatmap-predeploy-review.md` (15-reviewer audit) |

Fixes every blocker and HIGH finding from the pre-deploy review of the driver
heatmap feature set (P0–P3 + admin track AD-01/02/03/05).

## 1. Issue / gap identified

The review found the admin heatmap config surface was a silent no-op end to
end, a k-anonymity hole in the v2 baseline layer, a migration numbering
collision with `origin/main`, manual surge overrides leaving no audit trail, a
dead forecast chart, self-inflicted test/typecheck regressions, and a set of
ops screens whose labels overstated what the underlying numbers meant.

## 2. Root cause

Four recurring causes, not eight unrelated bugs:

1. **Reusing an endpoint URL was mistaken for reusing its contract.** AD-05
   posted seven new keys to an existing settings endpoint whose request model
   is `extra="ignore"`, so they were dropped at validation with a 200 returned.
2. **Guards applied to derived values instead of raw ones.** The baseline
   k-anonymity floor tested a *normalised* score, which a single ride still
   clears.
3. **Speculative fallbacks (`a ?? b ?? 0`) against an unverified response
   shape**, which converted a loud contract mismatch into a silent all-zero
   chart.
4. **Attestations written from memory rather than re-run.** `tsc`/tests were
   verified before the commit that broke them, and the change log recorded the
   earlier result.

## 3. Fix / remediation

| Ref | Fix |
|---|---|
| B1 | Migration 311 adds the 7 settings columns + DB CHECK bounds; fields declared on `SettingsUpdateRequest` with matching bounds; 5 numerics added to `AppSettings` |
| B2 | Baseline k-floor now suppresses on **raw** counts before normalising |
| B3 | Migration renamed 307 → 310 (`origin/main` had merged its own 307/308/309); `origin/main` merged into the branch |
| B4 | `admin_update_service_area` appends a `surge_pricing` row on any surge change; both surge endpoints share one append-only helper |
| B5 | Forecast reads `predicted_rides`/`hour`/`day_name`; speculative fallbacks removed |
| B6 | Both API submodule mocks rebuilt from `importOriginal`; 5 duplicate lucide keys deleted |
| B7 | `Switch` + `htmlFor`/`id` + `aria-describedby` across the config tab; forecast values in `sr-only` text |
| B8 | Change log corrected in place with strikethroughs |
| H1 | Poll interval clamped at three layers: DB CHECK, API schema, runtime read site + client |
| H2 | Redis cache read guarded — degrades to rebuild, not 500 |
| H3/H4 | "Unmet gap/unfulfilled requests" → "demand pressure"; ratio bands no longer carry a `×` suffix; 0.8 tier split restored |
| H5 | Forecast query filtered/sorted in SQL; `get_surge_status` cached 30s; admin poll gated behind a toggle; heat-map props memoised |
| H6/H7/H12 | `idle` status ends the offline shimmer; i18n wired with fr/es parity + English fallback |
| H9/H10/H11 | Load-failure guard blocks editing; error + staleness states replace silent catches; truncation disclosed |
| — | **Global kill switch** `driver_heatmap_enabled` (user request), checked before the per-area toggle and before any cache read |

## 4. Risk & impact on existing functionality

**Blast radius, named explicitly:**

- `backend/routes/drivers/profile.py::get_demand_heatmap` — the driver-facing
  endpoint. Only caller is the driver app's `useDemandHeatmap`. Read-only on
  `rides`/`drivers`/`service_areas`; no writes, no dispatch imports (grep-verified).
- `backend/routes/admin/settings.py::SettingsUpdateRequest` — **shared by the
  whole admin Settings page.** Change is purely additive (8 new optional
  fields); no existing field's type or bounds altered. `extra="ignore"` is
  retained, so unrelated payload keys behave exactly as before.
- `backend/utils/surge_engine.py::get_surge_status` — read-only reporting
  function. Callers: admin surge-status route, monitoring page, heatmap page.
  **Not** used by `surge_recalculation_loop`, so the caching cannot affect
  actual pricing. `use_cache=False` escape hatch provided.
- `backend/utils/demand_forecast.py::_get_historical_hourly_demand` — callers
  are the admin forecast endpoint and the driver v2 payload. Return shape
  unchanged; only the query that populates it changed.
- `backend/routes/admin/service_areas.py::admin_update_service_area` — adds an
  append-only insert after the existing update. Failure is caught and logged,
  so the operator's surge change cannot fail because of it.
- `admin-dashboard/src/lib/demand-bands.ts` — **new shared module**, consumed
  by `monitoring-map.tsx` and `heatmap/page.tsx` only (both new consumers).
- `driver-app/i18n/index.ts::translate` — **used by every driver-app screen.**
  Change is a fallback added only on the miss path: a key that previously
  returned itself now returns the English string. No existing successful
  lookup changes.
- `driver-app/hooks/useDemandHeatmap.ts` — consumed by the driver home screen
  and the Android Auto surface. Grep-verified: no other importers.

**What could regress:**

- The new `settings_heatmap_bounds_chk` CHECK would reject an out-of-range
  value written directly to the DB. Defaults satisfy it; a pre-existing row
  with no heatmap columns gets defaults, so it cannot fail on apply.
- Areas absent from the surge-status response now render neutral grey rather
  than purple on the monitoring map. Deliberate — see §5.
- `get_surge_status` can return data up to 30s old. Accepted: the underlying
  numbers are recomputed every 120s.

**Explicitly unaffected:** ride state machine, dispatch, offer timeout,
insurance periods, money/wallet/Stripe paths, WebSocket contract, background
loops. Confirmed by the full backend suite plus the review's dispatch and
money audits.

## 5. User-experience effect

**Drivers (mid-session visible):**
- Offline or heatmap-disabled: the permanent loading shimmer is gone — nothing renders.
- French/Spanish drivers: heatmap UI is translated instead of showing English or a raw key string.
- Persistent failures now hide the overlay instead of pinning an "unavailable" pill for the shift.
- Quiet nights keep the layer selector/forecast instead of dropping to v1.
- A surge-disabled area no longer shows a surge chip drivers can't earn on.

**Internal admins (mid-session visible):**
- Live demand on the heatmap page is now opt-in per session (default off).
- "Unmet Gap / unfulfilled requests" renamed to "Demand pressure" with an
  explicit "not stranded riders" caption — the old label overstated a healthy
  busy market several-fold and invited unnecessary surge overrides.
- Ratio legends drop the `×` suffix; a new "Building (0.8–1.2) → 1.50× fare"
  band appears where "Balanced" previously hid two live surge tiers.
- Areas with no reported data are grey with a legend entry, distinct from oversupply.
- Failed polls now show an error + "data as of HH:MM" instead of silently stale colours.
- The config tab is relabelled "Driver Heatmap (All Areas)" with an amber
  scope warning; its settings genuinely save now.

**Riders:** no change. No rider-facing code was touched.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/311_settings_heatmap_config.sql` | New: 7 columns + CHECK + kill switch | B1, kill switch |
| `backend/migrations/310_rides_area_created_idx.sql` | Renamed from 307 | B3 |
| `backend/routes/admin/settings.py` | 8 fields with bounds on `SettingsUpdateRequest` | B1, H1 |
| `backend/schemas.py` | 6 fields on `AppSettings` | B1 |
| `backend/routes/drivers/profile.py` | Kill switch, clamps, raw-count k-floor, guarded cache read, `surge_enabled` gate, scheduled lower bound, error logging | B2, H1, H2, mediums |
| `backend/routes/admin/service_areas.py` | Append-only surge history helper; both endpoints routed through it | B4 |
| `backend/utils/surge_engine.py` | 30s cache on `get_surge_status` | H5 |
| `backend/utils/demand_forecast.py` | SQL-side filters, `desc=True`, narrowed columns | H5 |
| `admin-dashboard/src/lib/demand-bands.ts` | New shared band module | H3, H4, colour drift |
| `admin-dashboard/src/lib/demand-forecast-transform.ts` | New forecast transform | B5 |
| `admin-dashboard/.../heatmap/page.tsx` | Gated poll, memoised props, honest labels, dormant state, legend, staleness | B5, H3, H5, H10 |
| `admin-dashboard/.../monitoring/{page,toolbar,monitoring-map,types}.tsx` | Shared bands, no-data colour, error/staleness, `aria-pressed` | H4, H10, H13 |
| `admin-dashboard/.../service-areas/page.tsx` | a11y, load guard, kill switch UI, allowlist copy, truncation notice, fetch cancellation | B7, H9, H11, H14 |
| `driver-app/hooks/useDemandHeatmap.ts` | `idle` status, clamp, `Array.isArray`, stop-when-disabled, hide-on-error | H1, H6, H12 |
| `driver-app/i18n/{index.ts,en,fr,es}.json` | English fallback + full fr/es parity | H7 |
| `driver-app/components/dashboard/*.tsx` | Strings routed through `t()` | H7 |
| Tests | New suites for settings round-trip, bands, forecast transform, driver hook; corrected 3 tests that pinned buggy behaviour | B6 + coverage |

## 7. Before / after

### B2 — baseline k-anonymity floor

```python
# Before — tests the NORMALISED score, which a single ride still clears
bl_max = max(baseline_grid.values()) if baseline_grid else 1
for k in baseline_grid:
    baseline_grid[k] = round(baseline_grid[k] / bl_max, 2)
...
bl_ok = bl_val > 0  # "baseline is already normalized, always passes if present"
```

```python
# After — suppress on RAW counts before normalising
baseline_raw = {k: v for k, v in baseline_grid.items() if v >= k_floor}
suppressed += len(baseline_grid) - len(baseline_raw)
bl_max = max(baseline_raw.values()) if baseline_raw else 1
baseline_grid = {k: round(v / bl_max, 2) for k, v in baseline_raw.items()}
...
bl_ok = key in baseline_grid  # presence IS the floor check
```

### H5 — forecast query

```python
# Before — oldest 10k rows platform-wide, SELECT *, filtered in Python
rides = await db.get_rows("rides", {"status": "completed"}, limit=10000, order="created_at")
```

```python
# After — window + area in SQL, newest first, two columns
filters = {"status": "completed", "created_at": {"$gte": start}}
if area_id:
    filters["service_area_id"] = area_id
rides = await db.get_rows("rides", filters, limit=10000, order="created_at",
                          desc=True, columns="created_at,service_area_id")
```

## 8. Rollback plan

1. **Feature-level, no deploy:** set `driver_heatmap_enabled = false` in
   Settings. Checked before the per-area toggle and before any cache read, so
   the heatmap disappears fleet-wide within one client refresh.
2. **Per-area:** `service_areas.show_demand_heatmap = false`.
3. **v2 only:** `driver_heatmap_v2_enabled = false` (drivers fall back to v1).
4. **Code:** revert the commits; nothing here is required by another feature.
5. **Data:** migration 311 is additive with defaults matching prior behaviour;
   rollback SQL is in the file header. The surge-history rows added by B4 are
   append-only audit data — leaving them in place after a revert is harmless
   and preferable to deleting audit history.

## 9. Verification performed

- [x] **Backend: full suite 11,229 passed, 8 skipped, 1 xfailed, 0 failed.**
- [x] **Driver-app: 397/397 passed; `tsc --noEmit` clean.**
- [x] **Admin: 202/202 passed (was 163 with 1 failing); `tsc --noEmit` clean; `npm run build` succeeds.**
- [x] New settings suite verified to fail 15/17 with the fix reverted.
- [x] New driver-hook suite verified to fail 9/16 with the fix reverted.
- [x] Locale key parity asserted programmatically (en/fr/es: 19 keys each, zero missing).
- [x] Blast-radius greps performed and recorded in §4, including every importer
      of the shared `translate()` and `useDemandHeatmap`.
- [x] Three pre-existing tests found to be pinning buggy behaviour were
      rewritten to assert the intended contract, not silently deleted:
      `test_v2_k_floor_per_component` (passing via the k-anonymity hole),
      `test_deactivate_surge_resets_multiplier_and_updates_existing_row`
      (pinning the PK-collision update), and the forecast area-filter test
      (mock ignored filters, so it could not distinguish the implementations).

## 10. What was NOT verified

- **Migration 311 has not been applied to any database.** Review-only. Apply in
  a normal deploy window; confirm the CHECK accepts the existing `settings` row
  (it will — all columns are new with in-range defaults).
- **No staging or device run.** No staging environment in this session; the
  driver-app changes were not exercised on a handset or an Android Auto head
  unit. Polygon rendering performance and the car surface remain code-reasoned.
- **No visual regression tooling exists for admin-dashboard or driver-app**, so
  every UI change here — including the contrast and colour-token work — was
  reasoned from code, not screenshotted. Standing gap.
- **No load test.** The polling fixes reduce request volume versus the reviewed
  state by construction (a gated poll and a 30s cache), but no measurement was
  taken.
- **Remaining open items, deliberately not fixed here:** the `heatmap` admin
  module still gates nothing server-side; AD-03 ships cards rather than the
  geographic map its ticket named; and the already-on-main findings (corporate
  payment-toggle override, migration 301's non-concurrent index, the
  advisory-only float gate) are filed separately — see the review document.
