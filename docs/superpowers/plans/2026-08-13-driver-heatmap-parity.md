# Driver Heatmap Parity — Competitive Analysis & Phased Product Plan

- **Date:** 2026-08-13
- **Status:** Proposed (planning only — no code changes in this PR)
- **Paired design spec:** `docs/superpowers/specs/2026-08-13-driver-heatmap-parity-design.md`
- **Supersedes:** `plans/heatmap_implementation_plan.md` (pre-corporate-era draft; proposed tables that now exist and a Leaflet stack the admin dashboard never adopted — admin uses MapLibre)
- **Related backlog:** ACTION_ITEMS `D4` (driver heatmap v1 — shipped, closed), `C11`/ADR-010 (KPI metrics aggregation — open, gates the cross-area admin work in this plan)

## 1. The ask

"How do Uber Driver and Lyft Driver show heatmaps (including on Android Auto), what are we missing, and what is the plan for the driver app and the admin dashboard per service area?"

Headline answer: **Spinr already ships a v1 driver heatmap and a working Android Auto integration** — this is not greenfield. But v1 is a static 7-day pickup scatter with a privacy defect, no live-demand signal, no iOS rendering, no earnings context, and no car-screen presence. Uber and Lyft both treat the heatmap as an *earnings-guidance product*, not a data layer. This plan closes that delta in four phases plus a parallel admin track.

## 2. What Uber and Lyft ship today (verified 2026-08-13)

### Uber Driver

| Capability | Detail |
|---|---|
| Grid | H3 hexagonal cells (Uber's own open-sourced grid system); display hexes shaded light orange → dark red |
| Earnings overlay | Per-zone **dollar amounts** (surge earnings additive), not just color intensity |
| Sticky surge | Driving through zones locks the **highest** surge amount for the next trip, even after leaving the zone |
| Refresh | ~10-minute cadence |
| **Predictive** | The guidance heatmap is **forecast-driven** (deep probabilistic models predicting above-average earning areas), not just a live count — Uber explicitly moved off "where demand was" to "where demand will be" |
| Android Auto | View + accept trips, manage ride queue, turn-by-turn on the head unit, **and the demand heatmap renders on the car screen** |
| CarPlay / Tesla | Supported |

### Lyft Driver

| Capability | Detail |
|---|---|
| Heat shading | Pink busy-area shading for general demand |
| Bonus Zones | Pink/purple zones with an **exact dollar bonus** printed on the zone; purple = smaller bonus, pink = larger |
| Lock mechanic | Drive into the zone → bonus guaranteed on the next completed ride; longer in a pink zone → bigger bonus |
| Forfeit rules | Guarantee lost on going offline, cancelling, missing a request, or entering Destination Mode |
| Refresh | Real-time; zones expire as demand shifts |
| Android Auto | Supported — Lyft Maps on the in-car display with directions and ride details |

Sources: [Uber — How Surge Works](https://www.uber.com/us/en/drive/driver-app/how-surge-works/) · [Uber Blog — Enhancing Uber's Guidance Heatmap with Deep Probabilistic Models](https://www.uber.com/en-CA/blog/enhancing-ubers-guidance-heatmap-with-deep-probabilistic-models/) · [Uber — Summer 2024 Driver app updates (Android Auto)](https://www.uber.com/us/en/drive/product-updates/summer-2024/) · [autoevolution — Uber rolls out Android Auto for drivers](https://www.autoevolution.com/news/uber-finally-starts-rolling-out-android-auto-support-for-drivers-230461.html) · [Lyft Help — Personal Power Zones](https://help.lyft.com/hc/en-us/articles/115012926807-Power-Zones-for-drivers) · [Lyft Driver Blog — Bonus Zones](https://www.lyft.com/hub/posts/bonus-zones) · [Google Play — Lyft Driver](https://play.google.com/store/apps/details?id=com.lyft.android.driver&hl=en_US)

### The Android Auto reality check

Both competitors ship Android Auto for drivers, and Uber renders its heatmap on the head unit. Android Auto's car-app framework is template-driven and non-interactive on the map surface (interaction goes through buttons/alerts), so a heatmap there is **display-only ambience** — which is exactly the distraction-safe way to do it. Spinr's own Android Auto layer already draws a live branded map on the car screen; adding the heatmap layer to it is a JS-level change, not a platform project (§7, HM-30).

## 3. What Spinr ships today (code-grounded inventory)

**Driver app (phone):**
- v1 demand heatmap: `GET /drivers/demand-heatmap` → raw ride pickup points from the **last 7 days** (any status, cap 2 000, weight always 1), scoped to the driver's service area, gated on `service_areas.show_demand_heatmap` (`backend/routes/drivers/profile.py:317-363`, migration `81_service_areas_missing_columns.sql`).
- Rendered via react-native-maps' native `Heatmap` (radius 35, teal→gold→orange→red gradient) — **Google-provider only**, so it renders on Android and silently not at all on iOS, which uses Apple Maps (`driver-app/app/driver/(tabs)/index.tsx:3,40,772-782`).
- Fetched **once**, on entering the idle state — no refresh interval, so it goes stale over a shift (`index.tsx:278-307`).
- Surge reaches drivers as a top-bar multiplier pill (2-min poll of `/service-areas`, `index.tsx:178-203`, `DriverTopBar.tsx`), on the offer card (`RideOfferPanel.tsx`), and in past-ride fare breakdowns — but **never on the map**.
- Per-area incentives exist end-to-end (admin CRUD → shown on offers, activity, ride detail, and the Android Auto trip card) but have **no geographic form** — nothing tells a driver *where* to be to earn one.
- Android Auto is implemented (`@iternio/react-native-auto-play`; `driver-app/lib/androidAuto/` — live car map, route, trip card, offer alert with surge badge, nav handoff) but is **unproven on hardware** (validated at JS level only, per `carSurface.tsx` header) and draws no heatmap. iOS CarPlay is dormant pending an Apple entitlement (`docs/carplay-android-auto.md`).

**Backend:**
- Surge engine computes demand (active rides, 10-min window) and supply (available drivers, Redis-presence-filtered, point-in-polygon) **per service area** every 2 min, and already appends `{multiplier, demand_count, supply_count, ratio, source}` history to `surge_pricing` (`backend/utils/surge_engine.py`) — an underused per-area time series.
- Demand forecasting exists (`backend/utils/demand_forecast.py`, exposed admin-only via `/admin/demand-forecast`).
- Driver breadcrumbs land in `driver_location_history` (90-day retention) and latest position on `drivers.lat/lng`.

**Admin dashboard (MapLibre + recharts + shadcn):**
- `/dashboard/heatmap` — historical pickup/dropoff heatmap with service-area filter (RBAC module `heatmap` exists).
- `/dashboard/monitoring` — live map of online/on-ride drivers + active rides over WebSocket with drift-poll fallback, filterable by area.
- `/dashboard/service-areas` — 8-tab per-area editor: surge controls (justification required > 2.5×), polygon editor, airport sub-zones, dispatch cascade, incentives, fees, docs, passes.
- `/dashboard/forecast` — per-area demand forecast page.
- **Missing:** any cross-area comparison (match rate / volume / utilization by area), any live demand-vs-supply layer, any unmet-demand view. The KPI table in CLAUDE.md is still unmeasured (C11; ADR-010 accepted, unimplemented).

## 4. Gap matrix

| Capability | Uber | Lyft | Spinr today | Plan |
|---|---|---|---|---|
| Demand heatmap on driver map | ✅ hex cells | ✅ shading | ⚠️ Android only; static 7-day scatter | P0 fix, P1 live |
| Live (not just historical) demand | ✅ | ✅ | ❌ | P1 |
| Predictive "will be busy" layer | ✅ | ➖ | ❌ (backend exists, admin-only) | P2 |
| Refresh cadence | ~10 min | real-time | ❌ once per idle-entry | P0 |
| iOS rendering | ✅ | ✅ | ❌ (Apple Maps no-op) | P0 |
| Surge/earnings shown *on the map* | ✅ $ per hex + sticky | ✅ $ per zone + lock | ❌ top-bar pill only | P1 (surge), P2 ($ zones) |
| Geographic bonus/incentive zones | ✅ (quests/surge) | ✅ signature feature | ⚠️ incentives exist, no geography | P2 (business decision) |
| Airport queue visibility for drivers | ✅ | ✅ | ⚠️ admin airport sub-zones only | P2 |
| Scheduled-demand preview | ➖ | ➖ | ❌ (Spinr has scheduled rides — differentiator) | P1 |
| Heatmap on Android Auto | ✅ | ➖ (nav/ride info) | ❌ (car map exists, no layer) | P3 |
| Android Auto at all | ✅ | ✅ | ⚠️ built, unproven on hardware | P3 validation |
| CarPlay | ✅ | ✅ | ❌ dormant (entitlement) | P3 |
| Legend / explainability | ✅ | ✅ | ❌ none, color-only, no i18n | P0 |
| Privacy-safe aggregation | ✅ (cells) | ✅ (zones) | ❌ **raw pickup coordinates sent to drivers** | **P0 — see below** |
| Admin: live demand vs supply per area | n/a | n/a | ❌ | Admin track |
| Admin: per-area trend analytics | n/a | n/a | ⚠️ data captured (`surge_pricing`), never charted | Admin track |
| Admin: cross-area KPI comparison | n/a | n/a | ❌ (C11 open) | Admin track |

## 5. What we're missing — ranked

1. **P0 privacy defect (fix before anything else):** `GET /drivers/demand-heatmap` returns up to 2 000 **exact rider pickup coordinates** to any driver in the area. The native heatmap blurs them on screen, but the API payload itself carries precise lat/lngs — individual home addresses are inferable by anyone with a driver token. This contradicts the PIPEDA data-minimization posture (CLAUDE.md forbids exact pickup addresses even in *logs*). Remediation: aggregate server-side to ~300–500 m cells with a k-anonymity floor (suppress cells below k pickups), keeping the existing response shape so shipped app versions keep working. Detail in the design spec §1.1.
2. **No live signal:** a week-old scatter tells a driver where demand *was*, not where riders are searching *now*. The surge engine already counts live demand per area every 2 min — none of it reaches drivers spatially.
3. **iOS drivers get nothing:** the Google-only `Heatmap` component silently no-ops on Apple Maps. Cross-platform cell rendering (polygons) fixes this and unlocks labels for later phases.
4. **Staleness:** no refresh while idle; a driver online for 3 hours navigates by a snapshot from clock-in.
5. **No earnings context on the map:** both competitors put dollars/multipliers *in the zones*. Spinr surge is per-service-area (city-wide), so the honest MVP is surge-tier shading of the area polygon + the multiplier chip on the map; sub-area surge is a separate product/regulatory decision (§10).
6. **Incentives are blind:** the per-area incentive system (admin-configurable, driver-visible on offers) has no map presence — the entire Lyft Bonus Zone mechanic is "incentives + geography + preview," and we already own two of the three pieces.
7. **Sparse-market cold start (Saskatchewan-specific):** Regina/Saskatoon live demand at 2 pm may be 3 searching rides — a live-only heatmap would look broken. The fix is the **blended layer**: hour-of-week baseline (last 4 weeks) + live boost + scheduled-rides-soon, clearly labeled "usually busy now" vs "busy right now." Uber solves this with forecasting; blending is our cheaper first step and reuses `demand_forecast`.
8. **Accessibility/explainability:** color-only encoding, a rainbow-ish teal→red gradient (deuteranopia-hostile and off-brand), no legend, no i18n strings, no empty/error states.
9. **Car screens:** Android Auto built but never hardware-validated; no heatmap layer on it; CarPlay blocked on entitlement paperwork.
10. **Admin flying blind per area:** ops can toggle `show_demand_heatmap` but can't see what drivers see, can't see live demand vs supply, can't see unmet demand (searching → auto-cancelled = lost revenue with GPS attached), and can't compare areas.

## 6. Product principles (Spinr guardrails applied)

- **Informational, never directive.** Drivers are independent contractors: the heatmap suggests, never mandates. No penalties, scores, or compliance tracking tied to positioning; no Lyft-style forfeit mechanics that punish missing a request (control-of-work smell). Copy rules in spec §2.4.
- **Not surge-first.** Surge display respects the 2.5× auto cap and existing visibility rules. The map may show the *current* multiplier; it never forecasts or hypes surge ("surge likely later" is banned copy).
- **No earnings promises.** "High demand" ≠ "you will earn $X." Dollar figures appear only for *guaranteed* incentives (P2) that finance has budgeted, never as estimates.
- **Privacy by aggregation.** Drivers see cells, never points. k-anonymity floor configurable via `app_settings`. Raw coordinates stay server-side (and out of logs, as always).
- **Additive & flagged.** Every phase ships dark behind `app_settings` flags plus the existing per-area `show_demand_heatmap` toggle — rollback is a flag flip, no redeploy (matches the live-testing release gates).

## 7. Phased roadmap

Sizes: S ≤ 2 days, M ≤ 1 week, L = needs its own decomposition. Each ticket = one PR = one Change Impact entry where behavior changes.

### Phase 0 — Fix what already ships (1 sprint, no new product surface)

| ID | Ticket | Surface | Size |
|---|---|---|---|
| HM-01 | Server-side cell aggregation + k-anonymity floor for `/drivers/demand-heatmap`, response shape unchanged (points become cell centroids, weight = count) | backend | M |
| HM-02 | Recency weighting (day-decay) + exclude rider-cancelled noise from the aggregate | backend | S |
| HM-03 | Refresh heatmap every 90 s while idle+online; pause on offer/active ride/backgrounded | driver-app | S |
| HM-04 | Brand sequential ramp (validated, see spec §2.3) + legend + i18n strings + loading/empty/error states | driver-app | M |
| HM-05 | Cross-platform cell rendering (polygons) replacing Google-only `Heatmap` → iOS parity | driver-app | M |
| HM-06 | Redis cache (60 s/area) + `spinr_drivers_heatmap_*` metrics on the endpoint | backend | S |

### Phase 1 — Live + blended demand, surge on the map (1–2 sprints)

| ID | Ticket | Surface | Size |
|---|---|---|---|
| HM-10 | v2 payload: cells carry `{live, baseline, scheduled}` components (live = searching last 10 min; baseline = 4-week hour-of-week; scheduled = next-2-h scheduled pickups, aggregated) behind `driver_heatmap_v2_enabled` | backend | M |
| HM-11 | Surge-tier shading of the service-area polygon + multiplier chip on the map (reuses existing 2-min poll; no new endpoint) | driver-app | S |
| HM-12 | Layer UI: "Busy now" / "Usually busy at this hour" / "Scheduled pickups soon" with legend states | driver-app | M |
| HM-13 | Admin-tunable config in `app_settings`: k floor, cell size, blend weights, refresh seconds | backend + admin | S |

### Phase 2 — Earnings-forward parity (business decisions required first — §10)

| ID | Ticket | Surface | Size |
|---|---|---|---|
| HM-20 | Geographic incentive zones: polygon/cell targeting on the existing incentives model + zone display with **guaranteed** dollar amount + preview before going | full-stack | L |
| HM-21 | Airport sub-zone status for drivers (builds on existing `parent_service_area_id` sub-areas) | full-stack | M |
| HM-22 | Hotspot chips: top-3 busiest cells as tappable chips with drive-time ("Downtown — high demand — 12 min"), informational copy only | driver-app | M |
| HM-23 | Predictive layer: surface `demand_forecast` (today admin-only) as the driver's "next few hours" strip | backend + driver-app | M |

### Phase 3 — Car screens

| ID | Ticket | Surface | Size |
|---|---|---|---|
| HM-30 | Heatmap cells on the Android Auto car surface (display-only, idle state only — distraction-safe like Uber's) | driver-app (JS) | S–M |
| HM-31 | Hardware validation pass for the whole Android Auto surface (DHU + real head unit) — currently "unproven on hardware" | QA | M |
| HM-32 | CarPlay: file Apple entitlement request now (weeks of lead time); parity work after grant | driver-app | blocked |

### Admin track (parallel; per-service-area, as requested)

| ID | Ticket | Page | Size |
|---|---|---|---|
| AD-01 | Live demand/supply overlay on `/dashboard/monitoring`: searching-ride cells + available-driver count per area (reuses surge-engine queries) | monitoring | M |
| AD-02 | Per-area trends: chart the existing `surge_pricing` history (demand, supply, ratio, multiplier over time) on the service-area detail — the data is already captured every 2 min and never visualized | service-areas | M |
| AD-03 | Unmet-demand map: pickups of searching→auto-cancelled rides as cells, per area + hour filter ("where do we lose rides") | heatmap | M |
| AD-04 | Cross-area comparison table: match rate, volume, utilization, surge-active share, unmet demand — **depends on C11/ADR-010 metrics work; sequence together** | new or dashboard | M–L |
| AD-05 | Heatmap ops config UI (exposes HM-13 knobs; shows drivers-see-what preview) | service-areas | S |

## 8. Business case & success metrics

Driver-side tooling is a recruitment/retention lever: Spinr's pitch is 0 % commission — matching Uber/Lyft's *tools* while beating their *economics* is the story. Operationally, better positioning attacks two KPIs that are currently below the measurement bar at all (C11):

- **Driver utilization ≥ 55 %** (primary): pilot-area lift is the headline success metric.
- **Match rate ≥ 85 %**: more drivers pre-positioned near demand → fewer searching timeouts.
- Supporting: time-to-first-offer after going online; unmet-demand rate per area (AD-03 makes it visible).
- **Guardrails:** driver cancellation ≤ 3 % (no surge-chasing mid-offer); rider cancellation ≤ 8 %; no PII exposure (HM-01 closes the current one); zero "app told me where to go" support tickets classified as control-of-work complaints.
- Feature health: heatmap fetch success rate, % of online drivers with heatmap visible, endpoint P95 (< 150 ms cached).

Cost: no new vendors, no Google Maps API spend (own data, own tiles on admin), Redis cache marginal. The main spend is engineering time (≈ 2 sprints to P1) and, for P2 bonus zones, real incentive dollars — which is why P2 is gated on a business decision, not engineering.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Privacy: current endpoint leaks exact pickups | HM-01 first; k-floor default 3; cells only; audit that no raw coords appear in new logs |
| Contractor classification: heatmap read as work direction | Informational-only copy review (legal) on P1/P2 strings; no penalties/forfeits; document in `regulatory-sk.md` context |
| Sparse data makes live view look broken | Blended baseline layer is the default view; live-only never ships alone |
| Surge perception / "surge-first" drift | Show current multiplier only, cap-clamped; no surge forecasts; `spinr-surge-auditor` on every surge-adjacent PR |
| Battery/data drain from polling | 90 s cadence, idle-only, backgrounded pause; measured in HM-03 verification |
| Android Auto still unproven on hardware | HM-31 before any AA feature work builds on it |
| Two map stacks (RN Maps mobile / MapLibre admin) drift visually | Shared cell-geometry + color tokens defined once in the spec; both consume the same API |
| Admin cross-area table blocked by C11 | AD-04 explicitly sequenced with ADR-010 implementation, not before |

## 10. Decisions needed (product/business, not engineering)

1. **Geographic incentive zones (HM-20):** spend real money on Lyft-style guaranteed zone bonuses? Needs finance sign-off (budget caps exist on the incentives model), fraud review (`spinr-fraud-auditor`), and classification review of the copy. Recommend scoping only after P1 data shows where incentives would actually move supply.
2. **iOS rendering approach:** recommended = cross-platform cell polygons (HM-05, no new SDK). Alternative = adopt Google Maps provider on iOS (new SDK dependency + API key + visual switch for every other map in the app). Decide before HM-05.
3. **k-anonymity floor default (k = 3) and cell size (~400 m):** privacy posture sign-off.
4. **Sub-area surge:** per-zone multipliers (true Uber parity) change rider-facing pricing granularity — regulatory + fare-transparency review before it enters any roadmap. Explicitly out of scope here.
5. **CarPlay entitlement:** file now (lead time) even though the work is P3?
6. **AD-04 sequencing:** fold into the C11/ADR-010 implementation or keep separate?
