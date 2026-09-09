# Driver heatmap review and redesign specification

Date: 2026-09-08. Repository: `srikumarimuddana-lab/spinrvm`.
Reviewed main commit: `64ebc7e8eb8fd3f448b1fb3f5721443927f2772b`.
Status: proposed design; documentation-only PR. No app, API, production configuration, or database changes.

## Outcome

Replace the driver heatmap's ring/square appearance with a soft, continuous demand overlay and readable, geographically anchored labels. Keep Apple Maps on iOS and Google Maps on Android; support Android Auto independently of the phone dashboard. Fix data freshness and lifecycle defects before expanding visibility. Estimated waits and extra earnings must come from validated data, not from heat intensity.

The supplied Spinr screenshot shows two concentric red circles with heavy black outlines, a car marker, substantial native map detail, and several floating controls. The reference shows a continuous warm overlay, short map-anchored wait ranges, a separate incentive badge, and a compact status area. The reference is a design target, not evidence that Spinr can calculate those numbers today.

## Evidence and boundaries

Static review covered the driver dashboard, heatmap hook/shared publisher, phone renderer/legend/hotspots, Android Auto surface/session/registration, app configuration/manifests, backend heatmap endpoint/configuration/H3 helper, relevant tests, and earlier heatmap plans/change logs. Source links below are pinned to the reviewed commit. No production data or configuration was queried. No simulator, native build, DHU, car hardware, or app runtime was exercised.

| Area | Current implementation and evidence |
|---|---|
| Versions | `driver-app/package.json`: Expo `~57.0.20`, React Native `0.86.3`, maps `1.27.2`, Auto Play `0.5.13`. Use manifest versions, not the older SDK description in AGENTS.md. |
| iOS phone | Apple Maps deliberately configured; no Google Maps iOS key/pod. [app.config.ts, lines 48–54](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/app.config.ts#L48-L54). |
| Android phone | Native Google `Heatmap`, radius 45 pixels, opacity .75, maximum 200 cells. iOS instead draws two circles per cell, maximum 60 cells. [HeatmapCells.tsx](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/components/dashboard/HeatmapCells.tsx#L62-L164). |
| Android Auto | Separate rectangle renderer, maximum 80 cells, hardcoded dark ramp, no viewport culling before the cap. [carSurface.tsx](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/lib/androidAuto/carSurface.tsx#L312-L319), [polygon rendering](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/lib/androidAuto/carSurface.tsx#L513-L524). |
| CarPlay | Dormant, requires entitlement and scene wiring. iOS phone support does not mean CarPlay support. [app.config.ts](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/app.config.ts#L379-L383). CarPlay activation is outside this plan. |
| Data | Authenticated `GET /drivers/demand-heatmap`, area derived from the current user's driver record. v1 decayed history; gated v2 live/baseline/scheduled components, area surge, hourly forecast. [profile.py](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/backend/routes/drivers/profile.py#L366-L720). |
| Cache | Redis payload TTL 60 seconds, keyed by service area, v1/v2, effective configuration fingerprint. Global and area switches are evaluated before cache reads. Defaults: client poll 90 seconds, clamped 30–600 seconds, ±10% jitter. |

## Findings, ordered by delivery priority

P0 below means prerequisite to the redesign rollout, not a claim of a confirmed production incident.

| ID / priority | Finding | Consequence / required change |
|---|---|---|
| HM26-01 / P0 | `HEATMAP_SPEC` still accepts `k_floor=1`; the per-area JSON overrides take precedence. Global admin model also accepts 1. Migration 397 raises the global settings CHECK to 3, but does not clamp per-area JSON at this resolver. [resolver](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/backend/utils/heatmap_config.py#L67), [migration](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/backend/migrations/397_h3_dispatch_geo_settings.sql). | A below-policy override can emit a single ride's coarse cell. Enforce minimum 3 at read time and every write boundary. This is Spinr's documented privacy floor, not a claim that k=3 guarantees legal compliance or anonymization. Current production values are unknown. |
| HM26-02 / P0 | Poll effect depends on `status` and invokes an immediate fetch. Requests have no cancellation/generation guard. Cleanup clears a timer but cannot stop a late response or its `.then(scheduleNext)`. [useDemandHeatmap.ts](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/hooks/useDemandHeatmap.ts). | A response after going offline/accepting a ride can repopulate cleared cells; status changes cause extra fetches and can defeat intended retry pacing. Use one lifecycle owner, generation checks, cancellation and one timer scheduled after settlement. |
| HM26-03 / P0 | Server `generated_at` is declared but unused. Freshness uses local fetch completion. Errors retain cells; phone renders whenever `cells.length > 0`, independently of `heatmapVisible/status`. [dashboard lines 1206–1213](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/app/driver/%28tabs%29/index.tsx#L1206-L1213). | Old heat can remain while its warning/legend disappears. Apply server expiry, clear actionable labels on error/stale, and gate every renderer consistently. |
| HM26-04 / P0 | Auto subscribes to the phone publisher; no publisher means empty data. Its standalone `startCarSession()` hydrates auth/ride/config but never starts heatmap fetching. The phone poller skips background polls. [shared publisher](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/hooks/demandHeatmapShared.ts), [car session](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/lib/androidAuto/carSession.ts#L324-L404). | Cold-start or phone-background Auto cannot dependably show current demand. Move ownership to a session service with phone and car subscribers; retain App Check/auth readiness guards. |
| HM26-05 / P1 | iOS fallback is explicitly two concentric circles. Both pass `strokeWidth={0}` but omit `strokeColor`. | The bullseye geometry is confirmed. The heavy black border's native cause is unconfirmed; reproduce against the reported binary/OTA before attributing it to a library bug. Explicit transparent strokes are only a candidate containment fix, not the redesign. |
| HM26-06 / P1 | iOS intensity is divided by the maximum among visible cells; Auto uses its own maximum/ramp. Android relies on native density normalization. [HeatmapCells.tsx](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/components/dashboard/HeatmapCells.tsx#L37-L123). | Panning can change what the same color means. A lone weak cell becomes darkest. Use one server-defined scale and renderer on every platform; never normalize by the viewport maximum. |
| HM26-07 / P1 | Blend uses `max(live, baseline, scheduled)` although baseline is 0–1 and others are counts. “Live” includes assigned/accepted/arrived/in-progress rides. | Mixed units and fulfilled requests cannot represent current opportunity or predicted wait. Default to explicitly labelled recent request activity; keep history and upcoming demand separate. |
| HM26-08 / P1 | Top-three hotspots are ranked cells; first is always labelled high. Chips live in a bottom row and only pan the camera. [HotspotChips.tsx](https://github.com/srikumarimuddana-lab/spinrvm/blob/64ebc7e8eb8fd3f448b1fb3f5721443927f2772b/driver-app/components/dashboard/HotspotChips.tsx). | No geographic labels, confidence threshold, spatial separation, or real wait predictions. Select distinct zones, anchor labels on-map, and do not equate rank with high demand. |
| HM26-09 / P1 | Center-only phone viewport clipping excludes kernels crossing the edge; Auto caps globally before filtering. | Hotspots pop at screen edges and offscreen cells can consume the car budget. Use viewport plus kernel halo; cap work deterministically after spatial filtering. |
| HM26-10 / P1 | Backend fetches up to 5,000 rows separately for history/live/baseline/scheduled. There is no same-key rebuild lock in the endpoint. | High volume can truncate the history used for a baseline, and cache misses across replicas multiply scans. Aggregate/page completely, surface incomplete data, and single-flight cache rebuilds across replicas. |
| HM26-11 / P1 | Configurable cell sizes are emitted only with v2, although v1 aggregation also uses them. | An older/v1 response can be drawn at the wrong grid size. Include additive geometry metadata for v1; new renderer must consume explicit centers/geometry without re-snapping. |
| HM26-12 / P1 | Native map components are mocked in renderer tests; the test ramp uses invalid short values such as `#a`. | These tests verify JS filtering, not black outlines, gradients, composition, or hardware performance. Require screenshots from actual native builds on all three surfaces. |
| HM26-13 / P2 | H3 helper exists, but the inspected driver endpoint does not call it or branch on `heatmap_h3_enabled`. September 1 change log claims driver H3 behavior absent from this snapshot. | Reconcile source/doc drift. Do not assume driver v2 is already H3 or reuse hex payloads as rectangular centers. H3 migration is not required for the visual redesign. |

Preserve the existing strengths: area-derived authorization, server-side cell aggregation, per-component suppression, cache fingerprinting, refresh bounds/jitter, area/global switches, phone viewport tracking, and car rendering disabled during active rides. Do not claim the whole authorization stack was penetration-tested.

## Competitive reference, verified 2026-09-08

Uber publicly describes warm demand colors and wait estimates, with surge shown separately in purple; this closely matches the supplied reference. Availability differs by market. [Uber driver heatmap](https://www.uber.com/us/en/drive/how-much-drivers-make/).

Lyft documents busy zones and demand planning, and its current Flash Turbo documentation separates time-sensitive bonus zones from ordinary ride activity. These are useful product patterns; this review does not infer either company's proprietary prediction algorithm. [Lyft driver app](https://www.lyft.com/hub/posts/explore-the-lyft-driver-app), [Flash Turbo](https://help.lyft.com/hc/en-us/articles/6198177189-Flash-Turbo).

## Architecture decision

| Option | Benefit | Cost / risk | Decision |
|---|---|---|---|
| Keep providers; shared transparent raster demand tiles | Identical smooth heat field, same scale, bounded native overlays, preserves existing navigation | New secured tile path, server raster/cache work; must prove native/Auto composition | Recommended, after a native capability spike |
| Move iOS to Google and use native Heatmap everywhere | True gradient on both phones | Existing intentional pod/provider choice must change; native build, licensing/key and gesture regressions; Auto still needs proof | Do not make this the prerequisite |
| Replace maps with another vector SDK | Flexible layers and labels | Largest rewrite; camera/routes/markers/Auto integration all affected | Out of scope for this request |

The maps 1.27.2 API explicitly supports Heatmap only on Google Maps. Its tile API supports native iOS/Android overlays without replacing the base map. This establishes an API option, not proof it composites on Spinr's Auto VirtualDisplay. [Heatmap API](https://github.com/react-native-maps/react-native-maps/blob/v1.27.2/docs/heatmap.md), [tile API](https://github.com/react-native-maps/react-native-maps/blob/v1.27.2/docs/tiles.md).

## Product and visual specification

- Soft amber → orange → Spinr red gradient; alpha starts at zero and caps at 0.32. No black strokes, concentric Circle primitives, or square grid outlines. An isolated valid zone may be rounded, but must have a continuous fade without rings.
- Use transparent 256 px PNG XYZ tiles at native zooms 10–16. Generate with a kernel halo and crop to avoid tile seams. Keep heat geography stable while zooming. Low-zoom views below 10 hide detail; above 16 scale the same native tiles.
- Preserve road labels, car marker, route/airport overlays, attribution, camera follow and gesture behavior. Do not repaint or cache Apple/Google base-map imagery on the backend. Only Spinr's aggregated overlay is rasterized.
- Start with map-anchored `Recent activity` labels; `Higher activity` requires the server's published absolute band threshold. Maximum 5 phone / 3 car labels after collision avoidance and safe-area exclusion. Never display example numbers as live information.
- Use the actual server centroids. Smooth only already-suppressed aggregates; never interpolate individual pickups or expose rider/driver identifiers or exact trip coordinates.
- Phone layout: compact earnings pill at top, one demand toggle, recenter control, and compact bottom online/offline status. Put layer/history details inside the demand sheet. Maintain a clear accessible Go online / Go offline action; preserve confirmations and insurance/state transitions.
- Default data layer is `recent`; history is explicitly `usual`; upcoming is `scheduled`. No mixed-unit blend. Offline demand preview may be added only for authenticated eligible drivers with the new flag, while the screen is visible; it must never set the driver online/available or start extra background GPS.
- Empty is `Not enough recent activity to show areas`; network failure is `Demand unavailable`; expired data is `Demand updating`. Neither empty nor privacy-suppressed means guaranteed absence of riders. Existing rides/navigation remain usable.
- Text contrast at least 4.5:1; text plus icon makes meaning available without color; phone touch targets at least 44 pt iOS / 48 dp Android; respect text scaling and reduced motion. Translate keys across en/fr/es.
- Auto is a separate surface: maximum 3 noninteractive map labels, no phone bottom sheet, use host-approved template actions and available/stable map area. Night colors follow car ambient state. Active offer/ride, safety UI and navigation take precedence over demand.
- Car capability/quality tests follow [Android map surface guidance](https://developer.android.com/training/cars/apps/library/draw-maps), [car app quality](https://developer.android.com/docs/quality-guidelines/car-app-quality), and [DHU testing](https://developer.android.com/training/cars/testing). No claim of Play approval is made here.

## Proposed additive v3 contract

Keep the current endpoint and v1/v2 keys for installed clients. Opt-in clients request `schema=3`; authorize the current driver and derive area server-side before accessing cache. Existing legacy semantics must not be silently repurposed.

```ts
type DemandBand = 'low' | 'medium' | 'high';
type DemandLayer = 'recent' | 'usual' | 'scheduled';
type DemandZone = {
  id: string; lat: number; lng: number; band: DemandBand;
  wait_minutes: { min: number; max: number } | null;
};
type DemandV3 = {
  schema_version: 3; enabled: boolean; area_id: string;
  snapshot_id: string; generated_at: string; expires_at: string;
  refresh_seconds: number; layer: DemandLayer; scale_version: string;
  availability: 'ready' | 'insufficient' | 'unavailable';
  zones: DemandZone[];
  tiles: { url_template: string; token_expires_at: string;
    tile_size: 256; min_zoom: 10; max_native_zoom: 16 } | null;
  surge: { active: boolean; multiplier: number } | null;
};
```

Use `wait_minutes=null` in the initial release. `generated_at` identifies source computation, not cache retrieval. Default polling remains 90 seconds ±10%, bounds 30–600. Proposed v3 expiry is 180 seconds; clamp effective v3 refresh to at most 120 seconds so configured legacy intervals cannot exceed freshness. Disable/error responses have no usable tiles or zones. Server and device clocks must be accounted for using response Date/monotonic elapsed time; future/malformed timestamps fail closed for demand.

Tiles: authenticated metadata issues an opaque random session handle lasting at most 180 seconds, scoped to driver/session, authorized area, immutable snapshot, layer, palette and zoom limits. Native tile GET uses that handle because UrlTile does not document arbitrary auth headers; never put an access/refresh JWT in the URL. Resolve/revalidate the handle and current switches before returning even cached bytes. A shared authorized area raster cache must not contain personalized earnings or credentials. Invalid, expired, revoked or cross-area handles return 401/403; rate/budget limits return 429. Logs and telemetry redact the handle everywhere, including proxies. No public CDN or persistent device tile cache in v1 of this redesign.

Snapshot cache key includes area, schema, geometry/scale/policy versions, config fingerprint and source window; binary tile keys add snapshot/layer/palette/z/x/y. Snapshot TTL 60 seconds; retain immutable source/tile data at most 180 seconds for issued handles. Use cross-replica single-flight with bounded lock lifetime and ownership-safe release. Render CPU work off the async request loop with bounded concurrency. Cache failures must be observable and bounded, not trigger unconstrained full-history rebuilds.

## Data rules and later wait estimates

Initial recent layer counts distinct valid ride requests in the configured recent window; cancellation/retry duplicates and fulfilled requests are not interpreted as unserved demand. History and scheduled bookings remain separate. Publish thresholds per area/window with a version; never label the maximum visible point as high automatically. Use a privacy threshold of at least 3 distinct contributing riders per emitted cell/component in v3, and enough independent observations for labels; suppress before smoothing. Preserve legacy minimum ride-count suppression while strengthening v3.

True opportunity/wait work is a later, separately gated phase: use eligible fresh available supply, vehicle class, demand arrival rate, queue/airport constraints, and observed idle-to-next-offer durations. Exclude assignment/active-trip time from idle exposure and include censored no-offer intervals to avoid optimistic bias. Audit existing event coverage before collecting anything new; store coarse cells and durations rather than unnecessary raw location trails.

Do not convert request counts, a rider pickup ETA, normalized history, or a surge multiplier into a driver wait estimate. Release numeric ranges only after time-held-out validation by service area and vehicle type, at least 100 independent recent intervals per evaluated cohort, and at least 80% empirical interval coverage on that holdout. These are proposed product gates, not measured accuracy. Otherwise keep null. Display wording `Estimated wait for a request`, never guarantee a ride.

Keep existing fare/surge policy unchanged. No `+$5.25` badges until a real server-authoritative driver incentive exists with eligibility, currency and expiry. A current area multiplier can be labelled separately as a multiplier; never turn it into a fabricated dollar bonus.

## Delivery and success gates

Implementation steps: [backend plan](../plans/2026-09-08-driver-heatmap-backend.md), then [mobile and Auto plan](../plans/2026-09-08-driver-heatmap-mobile.md).

1. P0 containment and lifecycle/freshness: protect existing clients; demonstrate no late-response repaint after offline/logout/ride acceptance.
2. Native tile capability spike on iOS, Android and Auto before committing to production tile infrastructure. If Auto composition fails, keep its redesigned demand layer off and resolve the native blocker; do not claim parity or silently migrate providers.
3. Ship v3 dark; pilot internal drivers in Regina/Saskatoon, including sparse suburbs, same captured fixture across platforms, day/night and poor connectivity.
4. Release shared shaded zones first. Wait/incentive work is not a dependency for a useful visual improvement.

Proposed budgets: warmed metadata p95 under 300 ms; cached tile p95 under 200 ms; bounded cold raster generation under 1 s; no more than one metadata request in flight per JS runtime/session; native map frame time p95 at most 33 ms on the chosen lower-end test phone/head unit; added battery drain no more than 5 percentage points over a matched one-hour baseline. Record measured results rather than asserting these budgets are already met.

Feature flag `driver_heatmap_v3_enabled` defaults false with internal driver/area targeting. Keep per-platform rendering flags so a native problem can be isolated. Test flag-off behavior and token invalidation before rollout. Auth failure, cross-area response, surviving expired heat, native crash, or ride-offer regression blocks release immediately. After seven days of internal canary meeting gates, expand one service area at a time. No rollout dates or performance results are asserted by this planning PR.
