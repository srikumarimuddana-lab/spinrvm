# Change Impact & Risk Log — public ride-tracking page: smooth car, brand tokens, arrived / ended states

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (agent session) |
| Surface(s) | admin-dashboard (public `/track/[rideId]` page only) |
| Domain (Sentry tag) | rides |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x` (this PR). Builds on W4.1 (#5888, merged), whose `marker-interpolation` util it reuses unchanged. |
| Related issue or gap ID | UX enhancement program W4.2 (`.claude/plans/2026-09-25-ux-enhancement-program.md`); #2816 (token migration) |

`/track/[rideId]` is the **public, unauthenticated** page a rider shares with a contact (`track.spinr.ca/{token}` rewrites to it). Real riders and their contacts use it during live app testing.

## 1. Issue / gap identified

- The driver's car jumped from fix to fix on every 5 s poll. Its heading turned on its own 600 ms CSS tween, separate from the position.
- The page used 38 raw Tailwind palette classes (behind 33 `eslint-disable` comments) and hex status colours, instead of the design tokens.
- On `driver_arrived` the sheet kept showing "N min away". That ETA is to the **drop-off**, so it means nothing while the driver waits at pickup.
- On `completed` / `cancelled` the map went empty but kept the old route line. Nothing on the map said the trip was over.

## 2. Root cause

- **Motion:** the page's `upsertMarker` set `marker.position = pos` directly on each poll. `applyCarRotation` put a CSS `transition` on the car image, so heading and position animated on two clocks.
- **Tokens:** the page is fixed-light on purpose, but the root layout's `ThemeProvider defaultTheme="dark"` puts `.dark` on `<html>` for nearly every visitor. `globals.css` then points `--background`, `--card`, … at the dark palette. Semantic classes would have turned the page dark, so the #2816 migration skipped it (2026-07-29 and 2026-08-21 change logs).
- **Ended state:** for terminal statuses, `track_shared_ride` (`backend/routes/rides/sharing.py`) returns only `status`, `message` and the two addresses. The page removed the pins and the car (no coordinates) but never cleared `routePolylinesRef`. A late OSRM response could also still draw a route.

## 3. Fix / remediation

**Car motion (commit `01f2409`)**
- The existing car is no longer moved by `upsertMarker`. `moveCar(to)` glides it with the W4.1 util (`src/lib/map/marker-interpolation.ts`), which was reused unchanged.
- Position **and** heading go through `interpolateMarker` on one `requestAnimationFrame` loop over `MARKER_ANIMATION_MS` (1 s). A new fix mid-glide retargets from the pose drawn at that moment.
- The util's snap rules apply unchanged. The car is drawn straight at the fix on:
  - first placement,
  - a jump over 500 m,
  - a stale feed (over 30 s),
  - `prefers-reduced-motion`, read live on each update.
- The glide is dropped when the driver is released (`driver: null`) and on unmount.
- How the heading is *derived* is unchanged (route snap → travel direction → hold). Only how it is *drawn* changed. The CSS transition is gone, and the map-rotation correction (`heading_changed`) re-applies the pose currently drawn.

**Brand tokens (commit `d4e76ec`)**
- New `light-tokens.ts` re-declares, on the page root, the light `:root` value of the 11 tokens the page uses. This is the same scoped mechanism `.theme-v2` uses: Tailwind's `@theme inline` compiles `bg-card` to `var(--card)`, as confirmed in the production build's CSS.
- The markup now uses `bg-card`, `bg-background`, `bg-muted`, `text-foreground`, `text-muted-foreground`, `border-border`, and `bg-success` / `bg-info` / `bg-warning` / `bg-destructive` for the status dot. The page still renders light.
- A test fails if a pinned value drifts from `globals.css :root`.
- The route legend dots use the shared `ROUTE_PIN_COLORS` that the map pins already use.
- The car SVG illustration keeps its own fills. It is a drawing, not UI chrome.

**Arrived / ended (commits `92529b3`, `062d656`)**
- **`driver_arrived`:** the sheet headline reads "The driver has arrived". The drop-off ETA is removed from the sheet and the pill. The car, driver name and plate stay as they were.
  - The first version said "Your driver has arrived". `062d656` changed it at the user's request, because people viewing a shared link are usually the rider's contacts, not the rider.
  - No other text on the page addresses the viewer as the rider. The other status labels ("Finding driver", "Driver on the way", "Driver arrived", …) are already neutral.
- **`completed` / `cancelled`** (`ENDED_STATUSES`, mirroring `RideStatus.terminal_statuses()`):
  - The route line is cleared, and any in-flight OSRM request is voided (`routeFetchSeqRef++`).
  - The car stays off the map even if a payload ever carried a driver.
  - An overlay on the map says "Trip ended" / "Trip cancelled" and "Live location is no longer shared.".
  - The `completed` label changes from "Trip complete" to "Trip ended".
- A visually hidden `role="status"` region announces each status change to screen readers (WCAG 4.1.3).
- **Unchanged:** the invalid/expired-link state ("Tracking unavailable" / "Tracking link is invalid or has expired.").

**Statuses used, and where they were confirmed**
- `driver_arrived`, `completed` and `cancelled` are values of `backend/models/ride_status.py` `RideStatus`.
- `track_shared_ride` returns `ride.status` verbatim for live rides and the terminal payload for `terminal_statuses()` (`{COMPLETED, CANCELLED}`).
- No status the backend does not return is used.

**Alternatives considered**
- *Position through the util, heading left on the CSS transition (smaller diff).* Rejected: on a big jump or under Reduce Motion the position would snap while the heading still tweened, and the task asks for the util's bearing lerp.
- *Make `/track` theme-aware, following `<html>`.* Rejected: with `defaultTheme="dark"` every first-time visitor would get a black page under a light Google map. That reverses a recorded decision on a live public page. It is raised as an open question below instead.
- *Hide the map entirely when the trip ends.* Rejected: it unmounts the Maps container mid-session and needs a layout rework. The overlay keeps the layout, and there is nothing live left on the map to show.

## 4. Risk & impact on existing functionality

- **Blast radius: single page.**
  - `page.tsx` is a route with no importers.
  - `light-tokens.ts` and `track-test-harness.ts` are imported only by this page and its tests.
  - The W4.1 util, `@spinr/shared` (`routeMapStyle`, `vehicleTracking`), `globals.css`, `middleware.ts`, `auth-initializer.tsx` and the backend endpoint are **read, not changed**.
  - Other importers of the util: `dashboard/monitoring/monitoring-map.tsx` only, which is unaffected.
  - The page is not in `docs/known-forks.md`.
  - rider-app and driver-app do not call `/rides/track`.
- **Heading logic:** the three places that used to call `applyCarRotation()` (poll, OSRM snap, map rotation) now go through `moveCar` / the drawn pose. A regression here would show as a car facing the wrong way. It is covered by the heading-glide and map-rotation tests.
- **Driver swap without an intermediate `driver: null` poll:** the payload carries no driver ID (by design, for privacy). If a new driver's first fix is under 500 m from the old one's, the car glides 1 s between them instead of jumping. The heading logic already had this limitation.
- **Performance:** while gliding, one marker position write and one transform write per frame, for one marker, for about 1 s of every 5 s. Hidden tabs pause rAF, and the first frame after the tab returns lands on the target.
- **Late OSRM response after unmount:** it can start one last 1 s glide on a detached map object. This is harmless, and the same class of pre-existing after-unmount writes the route drawing already had.
- **No interaction** with the ride state machine, dispatch, money, insurance periods or any backend write. This change is display-only.
- **PIPEDA / privacy:**
  - No new fields are read or shown. The page still shows only what it showed before: driver name, photo when approved, rating, vehicle, plate, addresses, ride code and ETA.
  - No logging or telemetry was added: no `console.*`, no Sentry calls.
  - Coordinates are used only to draw the map, as before, and are never rendered as text.
  - The live region contains only status copy.

## 5. User-experience effect

- **Who sees it:** anyone opening a shared tracking link, usually a rider's contact and sometimes the rider. Internal admins are not affected.
- **Mid-session:** yes. After deploy, the next page load shows the smooth car. A contact watching a trip that ends sees the ended overlay on the next poll.
- **Visible differences:**
  - The car slides between fixes, and its heading turns during the slide.
  - The in-progress status dot is info blue instead of violet.
  - Card borders are one gray step darker (`gray-100` → `--border` `#e5e7eb`).
  - The "Pickup" / "Drop-off" labels and the footer went from `gray-400` (2.5:1, below AA) to `--muted-foreground` (4.8:1).
  - The map placeholder is `--muted` instead of `gray-200`.
- **Copy changes:**
  - "The driver has arrived" (new sheet headline; the screen-reader announcement uses the same string).
  - "Trip ended" (was "Trip complete").
  - "Live location is no longer shared." (new).
  - "Trip cancelled" is unchanged.
  - The user chose the arrival wording (see Decisions below). The other strings have not had a separate copy review.
- **Screen readers:** status changes are now announced politely.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/track/[rideId]/page.tsx` | Car moved via `moveCar` (util glide, rAF, cleanup); rotation takes the drawn bearing; semantic token classes with the light scope on both roots; `ENDED_STATUSES`; arrived headline; ended overlay, route clear and OSRM void; `role="status"` region | W4.2 items 1–3 |
| `admin-dashboard/src/app/track/[rideId]/light-tokens.ts` | New: the 11 pinned light token values | Keeps `/track` fixed-light while using tokens |
| `admin-dashboard/src/app/track/[rideId]/track-test-harness.ts` | New: fake `google.maps`, URL-routed `fetch`, manual rAF, poll/flush helpers | Shared by the three render test files |
| `admin-dashboard/src/app/track/[rideId]/track-marker-motion.render.test.tsx` | New: 8 tests | Exact first placement, glide midpoint/landing, heading glide, map-rotation correction, 500 m snap, Reduce Motion, driver released, unmount |
| `admin-dashboard/src/app/track/[rideId]/track-brand-tokens.render.test.tsx` | New: 3 tests | Pinned values equal `globals.css :root`; live, loading and invalid-link states sit in the light scope with no raw palette classes |
| `admin-dashboard/src/app/track/[rideId]/track-ride-states.render.test.tsx` | New: 6 tests | Arrived copy with no ETA; completed clears car, glide and route; cancelled; late OSRM draws nothing; link opened after the end; expired link keeps "Tracking unavailable" |
| `docs/change-log/2026-09-26-track-page-smooth-marker.md` | This entry | CLAUDE.md Change Impact Log requirement |

## 7. Before / after

```tsx
// Before — every poll teleported the car, and rotation tweened on its own
upsertMarker(driverMarkerRef, d?.lat, d?.lng, carSvg, 52, true, 2); // sets .position
...
lastDriverPosRef.current = here;
applyCarRotation();            // img.style.transition = 'transform 600ms ease-out'
```

```tsx
// After — create/remove only; an existing car glides via the W4.1 util
if (!driverMarkerRef.current || d?.lat == null || d?.lng == null) {
  upsertMarker(driverMarkerRef, d?.lat, d?.lng, carSvg, 52, true, 2);
}
...
lastDriverPosRef.current = here;
moveCar({ lat: here.latitude, lng: here.longitude, bearing: carBearingRef.current });
```

```tsx
// Before — hardcoded light palette behind lint suppressions
// eslint-disable-next-line no-restricted-syntax -- ... fixed-light ... (#2816)
<div className="min-h-screen bg-gray-50 flex flex-col">
```

```tsx
// After — semantic classes, light values pinned on the page root
<div className="min-h-screen bg-background text-foreground flex flex-col" style={TRACK_LIGHT_TOKENS}>
```

```tsx
// Before — sheet on driver_arrived: "7 min away" (an ETA to the drop-off)
{ride.eta_minutes != null && isActive ? (<… {ride.eta_minutes} … min away …>) : (<… {statusCfg.label} …>)}
```

```tsx
// After
<p className="sr-only" role="status">{headline}</p>
{isArrived ? (<div …>{headline}</div>)   // "The driver has arrived"
 : ride.eta_minutes != null && isActive ? (<… min away …>) : (<… {statusCfg.label} …>)}
```

## 8. Rollback plan

- **Per viewer, no deploy:** OS/browser Reduce Motion puts the car straight at each fix, which is what the old page did (the old rotation tween was already cut to 0.01 ms under Reduce Motion by the global CSS rule).
- **Whole page:**
  - There is no feature flag. The plan lists W4.2 as "Gate: none", and `/track` has no `app_settings`-flag plumbing: it is public and loads no settings.
  - Rollback is on Vercel: promote the previous admin-dashboard deployment (instant, no rebuild), or redeploy with the three commits reverted.
- Nothing is written to live data (no ride state, no money, no insurance rows), so no data remediation is needed.

## 9. Verification performed

- [x] **New render tests fail on the old page and pass on the new one.** Each file was run against the page as it was before its commit:
  - motion: 4/8 failed on the original page (glide, heading glide, driver released, unmount). The other 4 (first placement, >500 m snap, Reduce Motion, map rotation) are regression guards and pass on both.
  - tokens: 2/3 failed (live and loading/invalid states not in the light scope). The parity test passes on both by design.
  - states: 5/6 failed (arrived copy, completed, cancelled, late OSRM, opened-after-end). The expired-link test is a regression guard.
  - wording (`062d656`): the arrived test failed against the "Your driver" page (1/6) and passes now. It checks that the visible headline and the `role="status"` announcement are exactly "The driver has arrived", and that "your driver" appears nowhere on the page.
  - All 17 pass on the final page.
- [x] **Full suite:** `npx vitest run` passed, 94 files / 822 tests (re-run after `062d656`, same result).
- [x] **Re-verified on the PR branch**, rebuilt from `main` after #5888–#5891:
  - `npx vitest run`: 102 files, 858 tests passed;
  - `npx tsc --noEmit`: exit 0;
  - ESLint on the 6 changed files: 0 errors, and the same 2 pre-existing warnings on `page.tsx`;
  - `npm run build`: 80/80 pages, with `ƒ /track/[rideId]` listed.
- [x] **Typecheck:** `npx tsc --noEmit` is clean (exit 0), including after `062d656`.
- [x] **ESLint on changed files:** 0 errors. `page.tsx` has the same 2 warnings it had before (an unused `no-explicit-any` directive and `<img>` LCP). The new files have 0. The 33 `no-restricted-syntax` suppressions are gone, and the rule reports 0 on the page.
- [x] **Production build:** a real `npm run build` (Turbopack) passed: compiled successfully and generated 80/80 static pages, with `ƒ /track/[rideId]` listed. Re-run after `062d656` with the same result.
  - The worktree's `node_modules` was a real copy of the main checkout's, not a symlink, because Turbopack rejects a symlink that points outside its root.
  - No config was changed.
  - The built CSS was checked: `.bg-card{background-color:var(--card)}` and `.bg-success{…var(--success)}`. This confirms the scoped override mechanism.
- [x] **Code review:** a `/code-review` (medium) pass on the three code commits found no correctness issues.
- [x] **PIPEDA review:** the coordinator ran the privacy review of `wip/w4-2`. Verdict: **clean**. The branch adds no new personal data, and nothing is retained after the trip ends. The reviewer raised one pre-existing, backend-side follow-up about ended-trip addresses; it is recorded under "Pre-existing issues" below.
- [x] **Blast-radius grep:**
  - importers of `marker-interpolation` and of the page;
  - `/track` and `rides/track` references across `admin-dashboard/src`, rider-app and driver-app;
  - `docs/known-forks.md` (not listed);
  - backend `track_shared_ride` and `RideStatus`, for the statuses used.
- [x] **Visual regression:** `/track` is **not** one of the 6 seeded Playwright baselines (`login`, `dashboard-home`, `dashboard-rides`, `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`), so no baseline is affected. It also means there is no automated visual check of this page at all.
- [ ] Feature flag: none (plan: "Gate: none"). The reasons are in §8.

## What was NOT verified

- **No real browser run.**
  - Neither the glide nor the ended overlay has been seen on screen.
  - All behaviour is proven in jsdom against a fake `google.maps` (a recording stand-in for `Map`, `AdvancedMarkerElement` and `Polyline`) and a manual rAF queue. Real Google Maps marker behaviour, such as how `.position` writes interact with `panTo`, was reasoned about, not observed.
  - No screenshots were taken. The colour and contrast notes in §5 come from the token values, not from rendered pixels, and no axe run was done.
- **Not tested against the live backend** or a real share token. Payloads were shaped from `sharing.py` by reading it.
- **"Renders light under `.dark`" is shown only indirectly:** jsdom does not compute CSS custom properties from the stylesheet. The tests prove the scope is on the root and that its values match `:root`, and the production CSS proves the classes read those variables. Nobody looked at a dark-mode browser.
- **The PIPEDA review covered this branch's diff only.** Its verdict is recorded in §9. Backend retention of addresses was out of scope and is listed as a follow-up below.
- **Thresholds:** the util's 1 s / 500 m / 30 s thresholds were designed for a ~4 s ping. This page polls every 5 s and was not tuned separately.

## Decisions (user, 2026-09-26)

- **Theme:** `/track` stays fixed-light, as built. It does not follow the visitor's theme.
- **Copy:** the arrival headline is "The driver has arrived", not "Your driver has arrived", because viewers are usually the rider's contacts. Done in `062d656`.

## Pre-existing issues noticed, not changed (out of scope)

- The ETA is always driver → **drop-off**, including while the driver is heading to pickup, where it is labelled "min away".
- One failed poll (for example a network blip) flips the whole page to "Tracking unavailable" and unmounts the map container. When the next poll succeeds, `mapRef` still points at the old, detached map, so the map stays blank.
- A `scheduled` status falls back to the "Finding driver" label.
- With no Maps key, visitors see a developer-facing message ("set `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` in Vercel").
- Polling continues every 5 s after the trip ends.
- **Privacy follow-up from the PIPEDA review (backend, not changed here):**
  - After a trip ends, the public endpoint's terminal branch (`track_shared_ride` in `backend/routes/rides/sharing.py`) still returns the full pickup and drop-off street addresses, and this page shows them.
  - The share token stays valid for 24 h from its creation whatever the ride status. A link that has been forwarded can therefore show exact addresses for up to 24 h after the trip.
  - Legacy tokens with no creation timestamp already expire once the ride ends. Timestamped tokens do not.
  - Proposed fix, either:
    - return only city/area-level addresses for ended rides, or
    - shorten the token's life once the trip ends.
  - Either option is a backend change to a live public endpoint and needs its own Change Impact Log.
  - Logged as `ACTION_ITEMS.md` **B44** (#5889), with P1 proposed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (Reduce Motion per viewer; promote the previous Vercel deployment)
- [x] Blast radius is stated, not assumed (single public page; shared util and backend read-only)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)
