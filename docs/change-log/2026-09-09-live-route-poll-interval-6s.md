# Change Impact & Risk Log — Live-route polyline/ETA refresh shortened from 20s to 6s

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app (phone screen + Android Auto surface) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` |
| Related issue or gap ID | User-reported "turn by turn navigation latency"; user explicitly chose the 5-8s option (via `AskUserQuestion`) after this session's investigation identified the 20s poll as the primary structural cause |

## 1. Issue / gap identified

Driver-app has no in-app turn-by-turn instruction UI — it deep-links to Waze/Google Maps/Apple Maps for actual maneuver-by-maneuver guidance (`ActiveRidePanel.tsx`). What Spinr renders itself is a road-matched route line + ETA, fetched from `/rides/{id}/live-route` and refreshed only every 20 seconds, on both the phone screen and the Android Auto fallback poller. A driver perceives this line/ETA lagging behind their actual position through turns and after route changes as "navigation latency."

## 2. Root cause

`GET /rides/{id}/live-route`'s own 20-second poll cadence was simply too coarse for something a driver experiences as live navigation — confirmed by a dedicated investigation this session (file:line evidence: `driver-app/app/driver/(tabs)/index.tsx`'s `setInterval(fetchLiveRoute, 20000)`, matched by `driver-app/lib/androidAuto/useCarLiveRoute.ts`'s `POLL_MS = 20_000`, whose own comment states it must match the phone's cadence). A separate, lower-confidence factor (the OSRM→Google Directions fallback chain's own sequential timeouts, each ~2s, additive on OSRM failure) was also flagged but is **not** addressed by this change — see "What was NOT verified."

## 3. Fix / remediation

Shortened both `POLL_MS` (`useCarLiveRoute.ts`) and the phone screen's matching `setInterval` (`index.tsx`) from 20,000ms to 6,000ms — the middle of the 5-8s range the user picked. `LIVE_ROUTE_MAX_AGE_MS` (`= POLL_MS * 3`) scales down proportionally (60s → 18s), preserving the same "ride out ~3 missed polls before treating the line as stale" safety margin rather than an absolute time value.

Verified this is safe to do without a backend cost blow-up before implementing:
- `backend/routes/rides/tracking.py`'s `get_live_route` has no per-request rate limiter of its own.
- Its routing provider chain (`utils.route_distance.compute_route`) tries **self-hosted OSRM first** — infra load, not metered spend — and only falls back to Google Directions (metered) when OSRM is unconfigured or failing.
- The Google Directions fallback is already protected independently of poll frequency by `utils/maps_budget.py`'s daily-spend circuit breaker (Redis-tracked per-SKU call counts; opens and returns 503 instead of forwarding to Google once `MAPS_DAILY_BUDGET_USD` is exceeded).

So the steady-state cost of this change is additional OSRM (self-hosted) request volume, not additional Google Maps billing exposure; the existing circuit breaker already caps the one path that would cost money.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two `setInterval` cadences.** Grepped every consumer of `POLL_MS`/`LIVE_ROUTE_MAX_AGE_MS` — both are local to `useCarLiveRoute.ts`, consumed only by its own two effects and `isLiveRouteUsable`'s max-age check; no test hardcodes either constant's numeric value (`liveRouteShared.test.ts` defines its own independent local `MAX_AGE` for a different, generic test, not tied to this constant).
- The phone-screen and car-surface pollers already defer to each other via `hasLiveRoutePublisher()`/`registerLiveRoutePublisher()` (only one is ever actually hitting the backend at a time) — unaffected by this change, both sides were simply sped up together to keep matching.
- No change to the routing provider chain itself, the budget circuit breaker, or any endpoint's auth/ownership checks.
- 3.33x more `/rides/{id}/live-route` requests per active navigating/in-progress ride (one poller, not two, per the dedupe above) — self-hosted OSRM absorbs this as infra load. Worth watching OSRM CPU/latency after this ships, but not a blocking risk given it's self-hosted capacity, not a third-party bill.

## 5. User-experience effect

- **Driver-facing**: the route line and ETA shown on both the phone screen and the Android Auto surface during `navigating_to_pickup`/`arrived_at_pickup`/`trip_in_progress` should visibly catch up to reality faster — a turn or a detour should stop showing the stale line for as long as before.
- Not applicable to riders, corporate admins, or internal admins — this endpoint and both call sites are driver-app only.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | `setInterval(fetchLiveRoute, 20000)` → `6000` | User-approved fix for reported navigation latency |
| `driver-app/lib/androidAuto/useCarLiveRoute.ts` | `POLL_MS = 20_000` → `6_000` | Must stay matched to the phone's cadence per this file's own existing comment |

## 7. Before / after

```ts
// Before (both files' own constant/literal)
POLL_MS = 20_000  // driver-app/lib/androidAuto/useCarLiveRoute.ts
setInterval(fetchLiveRoute, 20000)  // driver-app/app/driver/(tabs)/index.tsx
```

```ts
// After
POLL_MS = 6_000
setInterval(fetchLiveRoute, 6000)
```

## 8. Rollback plan

No migration, no live data. Two numeric constants in two files — `git revert` is a complete rollback to the 20s cadence. No feature flag: this is a polling-frequency tune, not a new capability, and there's no existing `app_settings` row for this cadence to piggyback on. If OSRM load becomes a real problem post-ship, the fastest lever is reverting this commit (or hand-editing both constants back up) rather than anything requiring a migration.

## 9. Verification performed

- [x] `yarn jest` (full driver-app suite) — 132 suites / 1497 tests passed, no test asserts the old numeric cadence
- [x] `npx tsc --noEmit` — clean
- [x] Blast-radius grep for both constants, confirmed isolated
- [x] Confirmed via code reading (not assumption) that the Google Directions fallback is already budget-capped independent of poll frequency, and that OSRM is the self-hosted primary path — this was the deciding factor for judging the change safe to ship without a backend-side rate limit change alongside it
- [ ] **`npm run build` / EAS production build NOT run** — OTA-eligible JS-only change, no native deps touched
- [ ] Not load-tested against the self-hosted OSRM instance — the 3.33x request-volume increase is reasoned from the code (self-hosted, no metered cost) rather than measured against real OSRM capacity/latency headroom; worth a look at OSRM's own CPU/latency dashboards after this ships to confirm no unexpected pressure

## What was NOT verified

- **The OSRM→Google Directions sequential-fallback timeout stacking** (a separate, lower-confidence finding from this session's investigation: on OSRM failure, the fallback to Google Directions adds its own ~2s timeout on top, worst case ~4s within a single poll cycle) is **not** addressed by this change — it would compound with a faster poll cadence during an OSRM outage specifically (more frequent slow polls, not fewer). A follow-up to make that fallback concurrent rather than sequential was flagged but not implemented here, since it's a distinct code change or from what the user was asked to approve.
- Not confirmed on a real device that 6s materially changes the *perceived* smoothness versus 20s — this is a direct, mechanical fix for the identified cause (a coarse poll interval) but, like the marker-rotation fix shipped earlier this session, has not been visually verified on hardware.
