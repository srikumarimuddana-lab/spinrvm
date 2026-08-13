# Change Impact & Risk Log — Per-Service-Area Heatmap Tuning

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers, admin |
| Migration | `312_service_areas_heatmap_config.sql` |
| Follows | `2026-08-13-heatmap-review-remediation.md` (global kill switch + clamps) |

## 1. Issue / gap identified

Every heatmap tuning value was either hardcoded in the endpoint or globally
settable. Six of them — the aggregation windows — were plain literals with no
way to change them at all, and the five knobs added by migration 311 applied
platform-wide.

## 2. Root cause

The values were tuned for a single mid-size market during initial build and
never parameterised. That is genuinely the wrong granularity for the windows:

- A dense downtown core wants a shorter "busy now" window and smaller cells
  than a sparse rural area covering the same ride volume.
- A **low-volume region needs a longer baseline window** to clear the same
  k-anonymity floor a busy region clears in a day. With one global window the
  only ways to give a quiet market visible cells were to lower the privacy
  floor everywhere or leave that market with an empty heatmap. The first is not
  an acceptable trade for a PIPEDA control; the second makes the feature
  useless exactly where driver guidance matters most.

## 3. Fix / remediation

`backend/utils/heatmap_config.py` owns a single spec (`HEATMAP_SPEC`) of every
knob — type, bounds, default, and global-settings key. Resolution is
**area override → global `app_settings` → code default**, with an
unconditional clamp at the end.

Eleven keys are now per-area overridable. Six were previously hardcoded:

| Key | Was | Now |
|---|---|---|
| `live_window_days` | literal `7` | per-area, 1–30 |
| `now_window_minutes` | literal `10` | per-area, 5–120 |
| `baseline_window_days` | literal `28` | per-area, 7–90 |
| `scheduled_lookahead_hours` | literal `2` | per-area, 1–24 |
| `forecast_hours_ahead` | literal `6` | per-area, 1–24 |
| `forecast_lookback_days` | literal `28` | per-area, 7–90 |
| `k_floor`, `cell_lat_deg`, `cell_lng_deg`, `decay_half_life_days`, `refresh_seconds` | global only (migration 311) | global **and** per-area |

Design decisions worth recording:

- **Sparse storage, not a snapshot.** `heatmap_config` holds only the keys an
  area explicitly overrides. An area that inherits must keep tracking the
  global when the global moves; storing a full snapshot would silently freeze
  it at whatever the value happened to be the day someone opened the form.
- **Cache key embeds a config fingerprint.** A cached payload is only valid for
  the config that built it. Without this, retuning an area — or tightening its
  k-anonymity floor — would keep serving cells built under the old settings
  until the TTL lapsed.
- **API rejects; read site clamps.** The admin API 422s on unknown keys and
  out-of-range values rather than storing and silently clamping: an operator
  who types `k_floor: 0` must be told, not handed a `1` that looks like it
  saved what they asked for. The read site clamps *anyway*, because direct SQL,
  migrations, and bulk scripts never pass through the API.
- **Bounds served, not restated.** The admin form renders limits from the same
  spec via `GET /service-areas/{id}/heatmap-config`, so the form and the
  validator cannot drift and a new knob needs no frontend change.

## 4. Risk & impact on existing functionality

**Blast radius (grep-verified):**

- `backend/utils/heatmap_config.py` — **new module.** Importers:
  `routes/drivers/profile.py` (the driver endpoint) and
  `routes/admin/service_areas.py` (validation + the new read endpoint). No
  other consumers.
- `backend/routes/drivers/profile.py::get_demand_heatmap` — the only behaviour
  change is *where* the numbers come from. With no overrides and no globals set,
  every resolved value equals the previous literal, so an untouched install is
  byte-identical. Verified by `test_no_sources_yields_code_defaults`.
- `backend/routes/admin/service_areas.py::ServiceAreaUpdateRequest` — **shared
  by every service-area edit in the admin dashboard.** The change is purely
  additive (one new optional field plus its validator); no existing field's
  type, bounds, or behaviour is altered. A payload that omits `heatmap_config`
  behaves exactly as before.
- `service_areas` table — one additive column. Every existing reader
  (`SELECT *` consumers included) is unaffected; the column simply appears with
  `{}`.
- `admin-dashboard/src/lib/api/pricing.ts` — additive export.
  `updateServiceArea` itself is unchanged.

**What could regress:**

- The cache-key change means **every heatmap cache entry misses once** on
  deploy (the key gains a fingerprint segment). Expected and self-correcting
  within one refresh cycle; the effect is one extra rebuild per area.
- An operator setting an aggressive override (e.g. a 1-day baseline window in a
  quiet area) can make that area's heatmap sparse or empty. That is the
  feature working as intended — but it is a real way to degrade a market's
  experience, which is why the form shows the platform value alongside every
  override and the privacy-floor help text warns against trading the floor for
  coverage.
- The new `service_areas_heatmap_config_is_object` CHECK rejects a non-object
  written directly to the column. Default `{}` satisfies it; no existing row
  can fail on apply.

**Explicitly unaffected:** ride state machine, dispatch, offer timeout,
insurance periods, money/wallet/Stripe, WebSocket contract, background loops,
surge pricing. The heatmap endpoint remains read-only on `rides`/`drivers`/
`service_areas` and imports nothing from dispatch.

## 5. User-experience effect

**Drivers:** none until an operator sets an override. Any change then takes
effect on the next refresh for drivers in that area only — visible mid-session
as different cell sizes, a different set of visible cells, or a changed poll
interval. Nothing changes for drivers in areas left on inherit.

**Internal admins:** the "Driver Heatmap (All Areas)" tab now opens with a
per-area section above the platform-wide one. Each row shows the effective
value, an Override checkbox, the platform value it inherits or diverges from,
and the permitted range. The header states how many values this area overrides.

**Riders:** no change. No rider-facing code touched.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/312_service_areas_heatmap_config.sql` | New: `heatmap_config` JSONB + is-object CHECK | Storage |
| `backend/utils/heatmap_config.py` | New: spec, resolver, fingerprint, override introspection | Resolution chain |
| `backend/routes/drivers/profile.py` | Resolve config; six hardcoded windows replaced; fingerprint in cache key | Per-area application |
| `backend/routes/admin/service_areas.py` | `heatmap_config` field + strict validator; persisted-field allow-list; new `GET .../heatmap-config` | Admin API |
| `admin-dashboard/src/lib/api/pricing.ts` | `getAreaHeatmapConfig` + types | Client |
| `admin-dashboard/src/lib/api.ts` | Barrel re-exports | Client |
| `admin-dashboard/.../service-areas/page.tsx` | `AreaHeatmapOverrides` component + labels; rendered above the global panel | Admin UI |
| `backend/tests/test_heatmap_config_resolution.py` | New: 44 cases | Coverage |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | 5 endpoint tests | Coverage |
| `backend/tests/test_admin_service_areas_coverage.py` | 13 API/validation tests | Coverage |
| `admin-dashboard/src/__tests__/dashboard/area-heatmap-overrides.test.tsx` | New: 11 interaction tests | Coverage |

## 7. Before / after

```python
# Before — literals, identical for every market
cutoff_7d  = (now - timedelta(days=7)).isoformat()
cutoff_10m = (now - timedelta(minutes=10)).isoformat()
cutoff_28d = (now - timedelta(days=28)).isoformat()
cutoff_2h  = (now + timedelta(hours=2)).isoformat()
raw_fc = await _forecast_demand(area_id=area_id, hours_ahead=6, lookback_days=28)
```

```python
# After — resolved per area, clamped regardless of source
hm_cfg = resolve_heatmap_config(service_area, app_settings)
cutoff_live      = (now - timedelta(days=hm_cfg["live_window_days"])).isoformat()
cutoff_now       = (now - timedelta(minutes=hm_cfg["now_window_minutes"])).isoformat()
cutoff_baseline  = (now - timedelta(days=hm_cfg["baseline_window_days"])).isoformat()
cutoff_scheduled = (now + timedelta(hours=hm_cfg["scheduled_lookahead_hours"])).isoformat()
raw_fc = await _forecast_demand(
    area_id=area_id,
    hours_ahead=hm_cfg["forecast_hours_ahead"],
    lookback_days=hm_cfg["forecast_lookback_days"],
)
```

## 8. Rollback plan

1. **Single area, no deploy:** clear its overrides in the admin form — it
   returns to the platform values immediately.
2. **All areas, no deploy:** `UPDATE service_areas SET heatmap_config = '{}';`
   Every area falls back to global settings, which is the pre-migration
   behaviour exactly.
3. **Whole feature, no deploy:** the `driver_heatmap_enabled` kill switch from
   the previous batch still applies and is checked first.
4. **Code:** revert the commit. The column becomes inert (nothing reads it);
   no data cleanup needed.
5. **Data:** drop the column per the migration header. Additive with a
   constant default, so no backfill either direction.

## 9. Verification performed

- [x] **Backend: full suite 11,296 passed, 8 skipped, 1 xfailed, 0 failed** (+67).
- [x] **Admin: 213 passed** (+11); `tsc --noEmit` clean; `npm run build` succeeds.
- [x] Defaults assertion pins that an install with no overrides and no globals
      resolves to the exact previous literals — the no-op-on-deploy guarantee.
- [x] Endpoint tests assert a per-area window **actually reaches the rides
      query** (measuring the cutoff it receives), not merely that it resolves.
- [x] Cache-key test asserts two configs produce different keys, so a tuning
      change cannot serve a stale payload.
- [x] Hostile-input tests: `k_floor: 0`, `refresh_seconds: 1`, `cell_lat: 0`,
      `"abc"`, NaN, malformed/non-object column — all clamp or fall through
      without raising, on a path that runs once per driver poll.
- [x] The admin form suite is a real mount-and-interact test, not a stubbed
      smoke entry, and was **verified to fail** when the save payload is built
      wrong (sending effective values instead of the sparse override set).
- [x] Blast-radius greps recorded in §4, including every importer of the new
      module and of the shared `ServiceAreaUpdateRequest`.

## 10. What was NOT verified

- **Migrations 311 and 312 have not been applied to any database.** Both are
  review-only. Apply in a normal deploy window; 312's CHECK cannot fail on
  existing rows (the column is new with default `{}`).
- **No staging or device run.** The admin form was not exercised in a browser
  and the driver-visible effect of a changed window was not observed on a
  handset — both are code-reasoned and unit-tested only.
- **No visual regression tooling exists for admin-dashboard**, so the new
  section's layout and contrast were reasoned about, not screenshotted.
  Standing gap.
- **No load test of the per-area cache-key change.** It causes one extra rebuild
  per area on deploy by construction; this was not measured.
- **No guidance yet on what values suit which market.** The form exposes the
  knobs and their safe ranges but does not recommend settings; choosing a
  baseline window for a low-volume area is currently an operator judgement
  call with no in-product help beyond the range and the privacy warning.
