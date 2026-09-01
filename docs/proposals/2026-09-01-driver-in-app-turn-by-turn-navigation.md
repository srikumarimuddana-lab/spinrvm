# First-Party In-App Turn-by-Turn Navigation — Scoped Proposal

**Date:** 2026-09-01
**Status:** Proposal — no code changed by this document.
**Trigger:** User question, "did you audit and see are there any more improvements that can be done to match and exceed the ride share giants?" — asked at the end of a session spent fixing the driver-app map/vehicle-tracking stack (course-up camera, marker-icon smoothing, route-line traveled-erasure). This is the follow-up on the one gap flagged as a real, multi-week build rather than something to fold into that work.

---

## 1. Executive summary

| | |
|---|---|
| **The gap** | Driver-app has no in-app turn-by-turn navigation. `ActiveRidePanel.tsx` deep-links out to Google Maps / Waze / Apple Maps (`Linking.openURL`) for actual driving guidance — the driver leaves the earnings/ride screen entirely. |
| **What competitors ship** | Uber Driver, Lyft Driver, and Bolt Driver all ship first-party in-app voice-guided navigation with turn arrows, lane guidance, and speed-limit display, built on their own routing stacks. Drivers never leave the app. |
| **What we already have that helps** | A working route-drawing pipeline (`RouteLine`, `shared/constants/routeMapStyle.ts`), a course-up follow camera with speed-adaptive zoom (this session), a smooth vehicle marker with route-snapping (`CarMarker`), and Google Directions integration already wired for polyline + ETA. None of this is navigation, but it's the map-rendering foundation navigation would sit on top of. |
| **What's missing that this proposal is actually about** | Turn-by-turn *maneuver* data (the backend explicitly requests `steps=false` on every Directions/OSRM call today — see §2.2), a voice-instruction engine, live re-route-on-deviation, and a nav-specific UI overlay (turn arrow, distance-to-turn, lane guidance). |
| **Recommended path** | **Build on Google's own routing data** (already the fare/route source of truth) rather than adopting a separate navigation SDK (Mapbox Navigation, HERE, etc.) — see §4 for why. Ship in 3 phases so value lands incrementally instead of one large release. |
| **Rough effort** | Phase 1 (voice-guided turn list, no new SDK): **2–3 weeks**, one mobile engineer. Phase 2 (live re-route + lane guidance): **3–4 weeks** more. Phase 3 (offline resilience, CarPlay/Android Auto nav surface): **open-ended**, scope separately. |
| **Biggest risk** | Cost. Directions API billing steps up meaningfully when requesting `steps=true` + more frequent re-route calls during active navigation (see §5). This needs a real budget conversation before Phase 1 starts, not after. |

---

## 2. Current state (verified in code)

### 2.1 What driver-app does today for navigation

`driver-app/components/dashboard/ActiveRidePanel.tsx` (~line 376–411):

```tsx
const googleWebUrl = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
// ...
await openWithFallback(`waze://?ll=${lat},${lng}&navigate=yes`);
// ...
Linking.openURL(defaultUrl).catch(() => Linking.openURL(googleWebUrl));
```

The driver taps "Navigate," the OS opens Google Maps/Waze/Apple Maps as a *separate app*, and Spinr's own screen (earnings counter, ride status, cancel/complete buttons, SOS) is no longer visible. This is a real product cost: every navigation moment is a moment the driver isn't looking at ride state, and a moment Spinr has zero visibility into (no route-deviation detection while the driver is in a different app, no "arrived" auto-detection tied to nav completion).

### 2.2 What's already half-built and reusable

- **Route geometry**: `MapViewDirections` (`react-native-maps-directions`) already fetches Google Directions polylines for both legs (driver→pickup, pickup→dropoff) and feeds them into `RouteLine`.
- **Route-deviation detection**: `index.tsx`'s `OFF_ROUTE_M` / `offRouteStreakRef` logic already flags when the driver strays >60m from the planned route for 3 consecutive fixes — this is the trigger a re-route feature would hook into, already built.
- **Course-up follow camera + speed-adaptive zoom** (this session): the camera already frames the road ahead the way a nav app does. A turn-by-turn overlay would sit on top of this camera, not replace it.
- **Backend explicitly suppresses turn data today**: `backend/utils/route_distance.py:211` and `:536`, `backend/utils/maps_eta.py:107` all pass `steps=false` (or OSRM's equivalent) on every Directions/routing call. This is deliberate — today the app only needs the polyline, distance, and duration, not per-turn instructions, so requesting `steps=true` and paying for the extra payload/cost would be pure waste. **This means Phase 1 requires a backend change**, not just a frontend one: flip `steps=true`, parse Google's `legs[].steps[]` (each with `html_instructions`, `maneuver`, `distance`, `duration`, and its own polyline), and forward a cleaned-up version to the client.

### 2.3 What Uber/Lyft/Bolt actually do (general industry knowledge, not verified against their code)

All three built or bought a real navigation engine rather than shipping raw Directions-API turn lists:
- **Uber** built its own routing engine after years on Google/HERE, largely for cost control at their volume and to embed proprietary signals (historical driver GPS traces) into ETAs.
- **Lyft** and most smaller players **still license or integrate a third-party navigation SDK** (historically Mapbox Navigation SDK, sometimes HERE) rather than building routing from scratch — this is the realistic comparison point for Spinr's scale, not Uber's.
- The common UI pattern across all of them: a turn-arrow banner at the top of screen, distance-to-next-turn, a voice prompt at fixed distance thresholds (e.g., "in 500m, turn right," "turn right now"), and automatic re-route on deviation — not full lane-level guidance in the first version any of them shipped.

---

## 3. What Phase 1 actually needs to do

A driver taps "Navigate" and, without leaving the app:
1. Sees a turn-arrow banner (e.g., "↱ Turn right onto Albert St — 400m") above the existing map.
2. Hears a voice prompt at two distance thresholds per turn (an approach warning + the turn itself) — reuse whatever TTS mechanism already exists for ride-offer sounds (`useRideOfferSound.ts` shows the app already does audio cues; check if `expo-speech` or similar is already a dependency before adding one).
3. Sees the current step's distance-remaining count down live, using the same GPS fix cadence already driving the map.
4. Gets a "recalculating" banner + one fresh Directions call when `offRouteStreakRef` (already built) trips.

This is deliberately **not** lane guidance, not speed-limit display, not offline tile caching — those are Phase 2+ per the effort table above.

---

## 4. Architecture options

### Option A — Build turn-by-turn on top of Google Directions data (recommended for Phase 1)

Request `steps=true` on the existing Directions calls, parse the step list into a simple `{instruction, maneuver, distanceMeters, polyline}[]`, track "which step is the driver on" by matching GPS progress against each step's own short polyline (the exact same `snapToRoute()` technique `CarMarker` and the new `RouteLine` traveled-erasure already use — see this session's work), and drive a voice/banner UI off that.

**Pros:** No new SDK, no new billing relationship, no native module to integrate — pure application code on data the app is already 90% fetching. Ships fastest. Directly reuses this session's route-snapping work.
**Cons:** Google's `html_instructions` are raw HTML strings meant for a webpage ("Turn <b>right</b> onto <b>Albert St</b>"), not app-ready — need a small parser/sanitizer. No lane-guidance data. Re-route-on-deviation means a live Directions API call mid-drive, which is the real cost driver (see §5).

### Option B — Integrate a dedicated navigation SDK (Mapbox Navigation SDK is the realistic choice)

Swap (or run alongside) `react-native-maps` with Mapbox's navigation stack, which ships built-in voice guidance, lane info, speed limits, and offline routing out of the box.

**Pros:** Far more capability out of the box — this is the actual feature parity path with Uber/Lyft's polish level, not just their headline feature.
**Cons:** A genuinely large migration — Mapbox's navigation SDK is not a drop-in alongside `react-native-maps`; it typically means running Mapbox's own map renderer for the navigation surface (possibly a second map engine alongside the existing Google-based `react-native-maps` used everywhere else), a new native module with its own iOS/Android build config, a new billing relationship (Mapbox's navigation SDK is priced separately from its maps), and a real UI/UX redesign of the driving screen around Mapbox's own conventions. This is not a "Phase 1" scope — it's closer to "redo the driver map" and should be its own decision with its own budget line, not bundled into a turn-by-turn ask.

### Option C — OSRM-based DIY routing (self-hosted)

The codebase already has an OSRM fallback path (`route_distance.py:536`) for when Google Directions is unavailable. OSRM can return `steps=true` too, and it's already self-hosted infrastructure (no incremental per-call billing).

**Pros:** No new per-call cost for the turn-by-turn data itself once OSRM already returns steps.
**Cons:** OSRM's step/maneuver quality is noticeably rougher than Google's (worse for local road names, no live-traffic-aware rerouting) — was chosen here as a *fallback*, not a primary source, for a reason. Building the primary experience on the fallback path inverts that reasoning and risks a worse first impression than what drivers currently get by leaving the app to Google Maps.

**Recommendation:** Option A for Phase 1. Revisit Option B only if driver feedback after Phase 1 specifically calls out missing lane guidance/offline support as a retention blocker — don't pre-build capability nobody's asked for yet, consistent with this codebase's "simplicity first" convention.

---

## 5. Cost — the risk that needs a real answer before Phase 1 starts

- `steps=true` increases Google Directions response payload and, depending on Google's current pricing tier structure, can change which SKU a call bills against. **This session has no live current Google Maps Platform pricing data to compute a real cost delta** — same caveat this repo's own `ACTION_ITEMS.md` already states for Uber fare-positioning: don't treat any number here as current without checking Google's live pricing page.
- The bigger cost driver is **re-route frequency during active navigation**: if a driver goes off-route often (or the deviation threshold is too tight), each recalculation is a fresh billed Directions call, on top of whatever the existing `_PRICING_ROUTE_WAIT_S` fare-estimate call already costs per ride. This needs its own rate-limit/debounce design (e.g., minimum N seconds between re-route calls) before Phase 1 ships, not as an afterthought.
- Concrete next step: get current Google Maps Platform Directions/Routes API pricing (ideally with the `steps` parameter's actual billing impact, if it has one) before committing to a Phase 1 timeline, so the effort estimate in §1 can be paired with a real dollar estimate.

---

## 6. Open questions for you / the team

1. **Budget conversation first or code first?** Given §5, I'd recommend getting a real Google Maps Platform cost estimate before Phase 1 starts, not after.
2. **Does "match the giants" mean Uber's bar (they built their own engine) or Lyft's bar (SDK-integrated)?** This proposal assumes Lyft's bar is the realistic target given Spinr's current scale — worth confirming explicitly rather than assuming.
3. **Voice**: does the app have an existing TTS mechanism to reuse, or does this need a new dependency (e.g. `expo-speech`)? Not verified in this pass — first thing to check when Phase 1 actually starts.
4. **Should Phase 1 ship behind a feature flag** (this codebase's `app_settings` pattern) so it can be dark-launched to a subset of drivers first? Given the SLA/cost risk in §5, this is very likely yes — flagging it here rather than deciding unilaterally.
