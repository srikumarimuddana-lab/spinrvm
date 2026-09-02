# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-01 |
| Author | engineering |
| Surface(s) | backend / admin-dashboard / driver-app (payload shape unchanged) |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (uncommitted) |
| Related issue or gap ID | H3 dispatch + heatmap; admin-visible failover |

## 1. Issue / gap identified

Live matching scans `drivers` with a lat/lng bounding box. That will not scale to multi-city, and the admin heat map currently ships up to 5,000 exact pickup/dropoff points. There was no operator-visible signal when a geo lookup failed.

## 2. Root cause

Candidate lookup was never abstracted behind a provider. Redis presence already exists, but there is no cell index, and PostGIS `location` is stale. Heatmaps were built as raw coordinate lists.

## 3. Fix / remediation

Dark-shipped H3 Redis live-location index + PostGIS `location_geog` candidate RPC + H3 heatmap aggregation, all behind `dispatch_geo_provider` (default `legacy`) and `heatmap_h3_enabled` (default false). Failover to PostGIS then legacy is logged, metric'd, Sentry-tagged `domain=dispatch` (deduped to once/minute per reason), pushed to admin WebSocket, stored as `last_served` for the live-map banner (10s poll, not WS-only), and shown on Redis & Infra.

Review remediations (2026-09-01, Codex defect-first):

- Atomic Lua/Python H3 upserts with stale-`source_ts` reject; cell membership is a per-member ZSET (score = last-seen unix ts) so idle IDs expire without a SET TTL race.
- Unhealthy flag is sticky (no TTL); incomplete rebuilds do not mark the index ready.
- GPS/presence writes no-op unless `dispatch_geo_provider` is `h3`/`shadow` (`spinr:h3:writes_enabled`); go-offline always removes.
- Activation requires exact `maxmemory-policy=noeviction` and a known memory percent under 60% (unknown policy/memory block; unlimited `maxmemory=0` is allowed). Settings/area writes reject `h3`/`shadow` until Redis infra is OK; `h3` also needs a complete index. Chicken-egg: Shadow → Rebuild → H3.
- H3 ID sets are eligibility-filtered then haversine-ranked before the 500 cap; query auto-drops resolution when the disk exceeds 400 cells.
- PostGIS RPC cap 5000 with optional WAV/vehicle_type predicates in SQL.
- Shadow compare is `spawn()`'d off the hail path and compares in-radius IDs only; unready still emits `shadow_skipped`.
- Failover logs+metrics first, then the fallback query, then Redis/WS/Sentry.
- Inherit-global persists SQL NULL via `model_fields_set` (`""` and explicit `null`).
- Admin heatmap fail-closed aggregates (k=3) when settings cannot be read — never ships exact GPS on that path.
- Driver heatmap cache key includes H3 on/off; v2 hex `cells` still ship when H3 is on (rectangular grid metadata omitted).
- Rebuild is `require_super_admin`, returns `{ran, skipped, audit_ok}`; UI does not toast success on skip/audit failure.
- Redis card is red only for `status_summary` or `last_served.failed_over`; live map shows a poll-error banner.
- `last_served` is per service area so one area's failover does not hide another's recovery.

## 4. Risk & impact on existing functionality

Blast radius:

- `routes/rides/matching.py` — candidate fetch only; ranking, claims, offers, insurance period 2 unchanged. Default still `get_rows` + box filter.
- Location writes (`driver_repo.update_driver_location`, REST marker, go-online, presence, tombstone) — extra Redis SET after a successful Postgres GPS write. Fail-open for GPS; fail-closed for H3 readiness.
- `utils/driver_presence.py` — go-offline removes the driver from H3; presence pings do not refresh H3 TTLs.
- Admin heatmap / driver demand heatmap — payload shape unchanged until `heatmap_h3_enabled`.
- Shared Redis — new `spinr:h3:*` keys. H3 is **not** activated while `maxmemory-policy` is `allkeys-lru`.

Consumers of `get_rows("drivers")` on the matching path: matching.py (live), DispatchService.find_candidate_drivers (tests only, untouched).

## 5. User experience effect

- Riders/drivers: none while flags stay default.
- Internal admin: persistent red banner on Live Monitoring when matching has failed over or H3 cannot serve; activity-feed WS events; Dispatch geo card on Redis & Infra with configured vs last-served provider; Settings + per-area geo provider; heatmap subtitle when H3 aggregation is on.

## 6. Files modified

| file path | what changed | why |
|---|---|---|
| `backend/utils/h3_cells.py` | H3 helpers | cell math |
| `backend/utils/h3_location_index.py` | Redis index + health | live location |
| `backend/utils/h3_index_reconciler.py` | 2 min rebuild loop | heal holes |
| `backend/services/dispatch_candidates.py` | providers + failover | matching |
| `backend/services/h3_heatmap.py` | k-anonymous hexes | PIPEDA |
| `backend/migrations/397_*.sql` | flags + k_floor ≥ 3 | dark ship |
| `backend/migrations/398_*.sql` | PostGIS RPC | failover |
| `backend/routes/rides/matching.py` | provider fetch | live path |
| `admin-dashboard/.../monitoring/redis/page.tsx` | failover card | ops visibility |

## 7. Before / after snippet

Before: matching always `get_rows` + `$and` geo box.

After: `fetch_dispatch_candidates(...)` with `dispatch_geo_provider=legacy` calling that same `get_rows`. H3 empty-and-ready returns `[]` (no drivers). H3 unready failovers to PostGIS then legacy and emits `dispatch_geo_event`.

## 8. Rollback plan

Set `settings.dispatch_geo_provider = 'legacy'` and `heatmap_h3_enabled = false` (admin Settings, no deploy). GPS writes stop populating Redis once the write flag refreshes. Feature-flag rollback; no Stripe/wallet/ride-state mutation. Do not drop `location_geog`. Redis keys expire at 90s (unhealthy flag is sticky until a complete rebuild or a provider rollback plus rebuild skip).

## 9. Verification performed

- Unit tests (2026-09-01, targeted, coverage gate off): `test_h3_cells`, `test_h3_location_index`, `test_dispatch_candidates`, `test_h3_heatmap`, `test_h3_index_reconciler`, `test_heatmap_config_resolution`, inherit-null (empty string + explicit null), settings reject `h3` on in-process Redis, admin heatmap settings-failure aggregates, driver v2 hex cells, lifespan watchdog spawn count. **100 + 44 + related matching assertions passed.**
- Production build: not run (`npm run build` / full backend image). Targeted pytest only.

## 10. What was NOT verified

- No staging Redis/Postgres (ACTION_ITEMS E1).
- No Locust load test (E2).
- No live `noeviction` + memory-percent check against production Redis.
- Admin visual regression still has no seeded baselines (B38).
- Driver-app heatmap was not opened in Expo; payload keys are unchanged on the default flag.
- PostGIS RPC not EXPLAIN'd against production.
