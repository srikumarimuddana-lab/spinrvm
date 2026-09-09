# Driver Heatmap Mobile and Android Auto Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. Do not deploy or merge automatically when executing a later implementation request.

**Goal:** Deliver the shared soft demand layer and useful map labels on iOS, Android and Android Auto, with reliable data ownership and no navigation regression.
**Architecture:** A session-owned controller fetches v3 metadata once and exposes a read-only snapshot to phone and car views. Both render the same secured transparent tiles. Platform adapters supply camera, safe-area, theme and lifecycle context.
**Tech Stack:** Existing Expo 57, React Native 0.86.3, react-native-maps 1.27.2, Auto Play 0.5.13, TypeScript/Jest and native EAS builds.
**Spec:** [Driver heatmap review and design](../specs/2026-09-08-driver-heatmap-review-design.md).

## Global constraints

- Keep Apple Maps/iOS and Google Maps/Android. Do not activate CarPlay or change provider/pods as a shortcut.
- Preserve earnings, Go online/offline confirmation, driver availability, ride offers, routing, navigation handoff, insurance transitions and map attribution.
- No fabricated wait ranges, dollar bonuses or busiest-by-rank labels. Initial `wait_minutes` is null.
- Shared v3 expiry 180 seconds, refresh 90 seconds ±10%, effective range 30–120; at most one metadata request in flight and one scheduled timer per runtime/session.
- Phone labels at most 5; Auto at most 3, noninteractive; stale/error/unauthorized/off-session means no heat or actionable labels.
- ≤3 files / approximately 200 lines per implementation commit. Add required change-impact/verification disclosure to each implementation PR.
- Native device evidence is required. Mocked MapView/Heatmap/UrlTile tests cannot prove rendering.

## M0 — Prove the native drawing path before building tile infrastructure (P0)

Files: create `driver-app/components/dashboard/DemandTileProbe.tsx`, `driver-app/__tests__/components/DemandTileProbe.test.tsx`; keep probe disconnected from production routes. Hook it into a development-only screen in a separate commit if required.

- [ ] Create a synthetic 2×2 XYZ fixture served from a development HTTPS endpoint: transparent corners, contiguous warm gradient across edges and an obvious geographic anchor. No private rides or map-provider imagery. Confirm the pinned maps import is `UrlTile`; do not confuse documentation's URLTile heading with the export.
- [ ] Probe the intended props:

```tsx
<UrlTile urlTemplate={fixtureUrl} tileSize={256} minimumZ={10}
  maximumNativeZ={16} maximumZ={20} shouldReplaceMapContent={false}
  opacity={1} zIndex={0} />
```

- [ ] Exercise iOS Apple Maps and Android Google Maps on native builds: pan, zoom 10/13/16/18, rotate, follow camera, day/night, overlap car/route/airport overlays, transition to active ride, then unmount/remount. Confirm fixture URLs work without arbitrary header support.
- [ ] Mount the same probe in Android Auto's existing VirtualDisplay through `carSurface.tsx`, in a separate development-only commit. Test both DHU and a physical head unit; verify stable-area changes and phone screen off. Do not count a phone screenshot as Auto verification.
- [ ] Capture reported iOS black rings on the actual installed build/OTA if reproducible. Compare with a probe using `strokeColor="transparent"` and `strokeWidth={0}`; treat any border improvement as containment only. Record build/runtime versions in evidence, not sensitive API payloads.
- [ ] Commit capability evidence under `docs/reviews/2026-09-08-driver-heatmap-native-capability.md`. The required result is correct native composition on all three targets. If any fails, stop its v3 renderer rollout and investigate; keep flags off rather than claiming parity.

## M1 — One authoritative session context and controller (P0)

First commit: modify `driver-app/store/driverStore.ts` and add `driver-app/__tests__/store/demandContext.test.ts` only if the needed canonical fields are absent. Inspect actual state before adding duplicates.

- [ ] Define reconciled heatmap context: driver/session generation, server service-area ID, authenticated/eligible/App Check readiness, isOnline, rideState. Accept server-forced offline and active ride changes immediately. Auth/driver absence has precedence over cached UI state. Clear context on logout/account change; do not let a late old-session refresh restore it.
- [ ] Test server-forced offline versus stale cached online, session switch, area reassignment and ride offer arrival. Commit the canonical context boundary without enabling new rendering.

Second commit: create `driver-app/services/demandHeatmapController.ts`, `driver-app/__tests__/services/demandHeatmapController.test.ts`.
Interfaces: `subscribeDemand(listener): unsubscribe`, `acquireDemandSurface('phone'|'car'): release`, `getDemandSnapshot()`, `updateDemandContext(context)`; immutable snapshots follow the spec plus local status and received monotonic time. Refcounts measure consumers, not number of pollers.

- [ ] With fake timers and deferred requests, test no fetch without auth/eligibility, online idle starts once, adding car does not add a timer, releasing phone leaves connected car active, releasing last consumer stops, and status changes never trigger extra immediate requests. Layer selection changes presentation or requests once explicitly, never replays the polling effect.
- [ ] Implement scheduling after request settlement, not on status changes. Use AbortController plus a generation check; every completion/scheduled callback must match current session/area/layer generation. Old finally/then callbacks cannot restart timers.

```ts
const requestGeneration = generation;
const result = await fetchSnapshot({ signal: abortController.signal });
if (requestGeneration !== generation || !canFetch()) return;
publishValidated(result);
scheduleAfterSettlement(requestGeneration);
```

- [ ] Define private controller helpers `canFetch(): boolean`, `publishValidated(result): void` and `scheduleAfterSettlement(expectedGeneration): void`; all read the same canonical context. At most one request survives per context. Cancellation is not a network error; 401/403 clears demand immediately; transient failures back off 90/180/300 seconds with jitter and never extend snapshot expiry. Error telemetry is sanitized.
- [ ] Phone fetches only while visible/foreground. Connected car may fetch with phone backgrounded, but only while its session is active and App Check/auth is ready. No permanent poller in FCM background handlers. Repeated connect events are idempotent.
- [ ] Run `cd driver-app && yarn test --runInBand demandHeatmapController demandContext`; commit `fix(heatmap): own polling at the driver session boundary`.

## M2 — Connect existing phone/car lifecycles (P0)

Files: modify `driver-app/hooks/useDemandHeatmap.ts`, `driver-app/hooks/demandHeatmapShared.ts`, `driver-app/__tests__/hooks/demandHeatmapShared.test.ts`.

- [ ] Make the hook an adapter to the controller; retain existing return fields for v1/v2 callers during rollout. Derive cells from layer without refetch-on-render. Preserve the 30–600 legacy clamp and feature-disabled state without a tight retry loop.
- [ ] Replace phone-publisher dependence with controller subscription. Test subscriber mount ordering, zero-consumer reset, layer changes, release/reacquire, and two simultaneously mounted phone views. The shared module can remain a compatibility facade; it must not own another timer.
- [ ] Run `cd driver-app && yarn test --runInBand useDemandHeatmap demandHeatmapShared`; commit the adapter change.
- [ ] In a separate commit modify `driver-app/lib/androidAuto/carSession.ts` and `driver-app/lib/androidAuto/__tests__/carSession.test.ts`: acquire demand after existing auth/App Check readiness; release on disconnect; acquire later when readiness recovers; consume server-corrected context. Test car cold launch without mounting dashboard, phone screen off, no-session map-only behavior, reconnect and forced-offline.
- [ ] Reconcile phone context at `driver-app/hooks/useDriverDashboard.ts` in another ≤3-file commit with its targeted tests if it currently owns independent isOnline state. Do not select the last writer between stale phone/car state. Run related driver-session tests and commit before moving on.

## M3 — Freshness, payload validation and safe fallback (P0)

Files: create `driver-app/services/demandSnapshotValidation.ts`, `driver-app/__tests__/services/demandSnapshotValidation.test.ts`; integrate controller in a separate commit.
Interface: `validateDemandSnapshot(input, expectedContext, receivedAt, serverDate) -> DemandSnapshot | null`.

- [ ] Reject wrong schema/area/session generation, nonfinite/out-of-range coordinates, malformed PNG URL origin, arbitrary host URLs, invalid zoom/size, malformed/future timestamps, invalid bands and unbounded zones. Only accept the configured Spinr API origin; never follow a server-provided external tile origin without a reviewed allowlist.
- [ ] Use source `generated_at`/`expires_at`, response Date and monotonic elapsed time. Token lifetime cannot exceed snapshot lifetime. Expiry independently clears tiles and labels even if no requests resolve. A successful download of an old snapshot does not renew it.
- [ ] Add deferred-request regression: begin online request, set offline/active ride/logout, resolve request, advance timers; assert no cells, tokens, labels or further timer from the old generation. Add token expiry during pan and simultaneous phone/car expiry.
- [ ] For legacy metadata without expiry, cap age by receipt-time fallback and disclose reduced freshness precision; never carry that fallback into v3. Missing/malformed v3 freshness hides demand.
- [ ] Run validation/controller tests; commit `fix(heatmap): expire all demand surfaces consistently`.

## M4 — Shared soft overlay and geographically anchored labels (P1)

Files: create `driver-app/components/dashboard/DemandOverlay.tsx`, `driver-app/components/dashboard/DemandZoneLabels.tsx`, `driver-app/__tests__/components/DemandOverlay.test.tsx`. Keep pure collision/selection helpers with their own test in a subsequent ≤3-file commit.

- [ ] Render one UrlTile overlay keyed by immutable snapshot/layer/palette identity; update handle before expiry. Use fixture-tested props from M0. Set no `tileCachePath`; do not enable offline tile mode. Native SDKs may still cache: unmount expired layers and use new snapshot identity, not cache headers alone, to prevent stale repaint.
- [ ] Overlay component requires status ready, enabled flag, valid context and unexpired snapshot. Error/stale/idle/active ride remove tiles and labels together. A failed raster path must not mask route, marker or ride actions. Keep the legacy renderer only behind the legacy flag during migration; never fall back to unsuppressed raw points.
- [ ] Labels use map markers anchored to server coordinates. Select deterministically by band, then stable ID, with at least 600 m peak separation and screen-space rectangle collision checks. Exclude phone controls/sheets/car stable-area occlusions; keep at most 5 phone/3 car labels. No rank-derived “high”. Wait labels remain absent when null.
- [ ] Use one palette definition scoped to demand rather than changing shared app colors globally. Test valid hex/RGBA values, immutable snapshot props, invalid payload exclusion, deterministic culling, overlapping labels and screen-edge kernel support. Phone marker labels may be accessible; Auto markers are noninteractive.
- [ ] Run `cd driver-app && yarn test --runInBand DemandOverlay DemandZoneLabels`; capture matching fixture screenshots on all targets. Commit renderer before dashboard integration.

## M5 — Integrate a cleaner phone dashboard (P1)

Files: modify `driver-app/app/driver/(tabs)/index.tsx`, `driver-app/components/dashboard/DemandLegend.tsx`, `driver-app/components/dashboard/HotspotChips.tsx` in ≤200-line commits; add focused tests in separate ≤3-file commits.

- [ ] Mount v3 DemandOverlay before foreground marker/route controls according to M0's verified z-order. Gate old HeatmapCells off when v3 is active. Check all callers with `rg -n 'HeatmapCells|DemandLegend|HotspotChips|useDemandHeatmap' driver-app`; do not change exported contracts blindly.
- [ ] Replace always-visible ranked chip row with geographic labels and one demand toggle. Keep history/upcoming selectors and forecast in the demand sheet. Preserve the existing car asset and earnings totals; do not change trip status semantics while rearranging controls.
- [ ] Integrate the existing online/offline control into a compact status area in a separate commit with `MapControls.tsx` and its existing test if layout requires it. Test permissions, go-online failure/confirmation, active offer, pending navigation handoff, and safe areas at small screen sizes.
- [ ] Add new translation keys to `driver-app/i18n/en.json`, `fr.json`, `es.json` as one 3-file commit. Copy: `Recent activity`, `Higher activity`, `Demand unavailable`, `Demand updating`, `Not enough recent activity to show areas`, `Estimated wait for a request`. Verify text scaling and screen-reader labels.
- [ ] Optional authenticated offline preview ships behind its own false-default flag. It may fetch only when phone visible; eligibility remains server checked. Test that preview never calls go-online, changes availability/insurance periods, or enables extra GPS collection.
- [ ] Run relevant dashboard/control/hook tests and native iOS/Android screenshots. The UI must not show raw errors, routes obscured by heat, or false waits/bonuses. Commit with explicit before/after screenshots in the implementation PR.

## M6 — Integrate Android Auto as its own surface (P1)

Files: modify `driver-app/lib/androidAuto/carSurface.tsx`, create `driver-app/lib/androidAuto/__tests__/carDemandOverlay.test.tsx`; modify `register.ts` only in a separate ≤3-file commit if host actions need changing.

- [ ] Replace the 80-square rendering path with shared DemandOverlay, using car day/night palette, map viewport plus halo and host available/stable area. Remove duplicated ramp/rectangle math only after callers/tests no longer depend on it.
- [ ] Retain existing camera rotation, live route, driver marker, offer panel, earnings privacy, recenter/pan/zoom and navigation handoff. No demand modal or phone-specific controls. A MapTemplate refresh must not be triggered by every metadata poll or heading update.
- [ ] Test car-only startup, phone foreground/background transitions, multiple connects without disconnect, USB/wireless disconnect, App Check delay, expired/revoked auth, server-forced offline, ride offer arrival and completion, ambient light change, and viewport resizing. Confirm zero demand during active offer/ride.
- [ ] Run `cd driver-app && yarn test --runInBand carSession carDemandOverlay register carScreen carMapCamera`; then DHU and real hardware with the same screenshots/fixtures as phones. Mark JS-only tests as JS-only.
- [ ] Commit `feat(driver): render shared demand zones on Android Auto` with hardware evidence and the platform kill-switch drill.

## M7 — Native builds, canary and acceptance

- [ ] Run targeted Jest suites above and standard repository lint/type/CI gates. Existing `HeatmapCells` tests remain useful for legacy filtering but do not substitute for native visuals. Driver app has no active automated native visual regression gate in the reviewed conventions.
- [ ] From driver-app run `eas build --profile preview --platform ios` and `eas build --profile preview --platform android` for native QA. Test minified/store-distribution Auto via the existing `android-auto` EAS profile and the authorized internal track; DHU/debug builds alone are insufficient. Do not submit to stores or publish OTA as a side effect of writing this plan.
- [ ] Record sparse/dense/zero cells, day/night, large fonts, pan/zoom, tile boundaries, loss/recovery of network, stale metadata, expired tile handle, area/account switch and accepted/in-progress rides on each platform. Capture black-ring reproduction separately from redesign acceptance.
- [ ] Measure warmed metadata/tile latency, cold raster timing, request counts with phone+car, native frame times and one-hour added battery drain against the spec budgets. Test bad-network behavior without weakening expiry.
- [ ] Internal driver/Regina/Saskatoon canary for seven days after all gates pass, then staged area rollout. Flags remain false until real measurements exist. Immediate stop for unauthorized/cross-area output, stale heat, native crash, session sign-out or ride-offer regression.
- [ ] Numeric waits: separate model/evaluation PR, using idle-to-offer intervals, fresh eligible supply, vehicle type and airport rules. Until ≥100 independent held-out intervals per cohort and ≥80% interval coverage, preserve null. Bonus labels need a real funded incentive contract; neither feature is part of the initial visuals release.

## Rough effort, not a delivery promise

| Work | Planning estimate | Main uncertainty |
|---|---|---|
| Native capability and P0 privacy/lifecycle fixes | 3–5 engineering days | Physical Auto reproduction / old binary |
| v3 aggregates, tiles, sessions and cache controls | 4–7 days | Schema/index coverage and native tile request behavior |
| Phone/Auto UI integration, accessibility and native QA | 4–7 days | VirtualDisplay composition, device performance |
| Canary observation | 7 calendar days after gates | Real traffic and operational stability |

Wait-time estimation is excluded from these estimates; data collection/validation can take several additional weeks. Existing production settings and the current screenshot's binary version remain unverified, so this is an implementation sequence, not a promise that every screenshot symptom has already been reproduced.
