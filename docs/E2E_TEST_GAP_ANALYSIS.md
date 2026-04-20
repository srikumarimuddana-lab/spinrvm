# E2E Test Gap Analysis — Spinr Ride-Share

**Date:** 2026-04-20
**Branch:** `claude/plan-e2e-testing-SK3bX`
**Scope:** backend, rider-app, driver-app, shared/

This document enumerates the user stories and test scenarios for the
Spinr ride-share platform, maps them to existing and newly added tests,
and flags the remaining gaps in priority order.

---

## 1. What was added in this branch

### Driver-app E2E (new — none existed)
- `driver-app/playwright.config.ts` — mirrors rider-app config, port 3003
- `driver-app/e2e/fixtures.ts` — auth seeding, backend mocks, WebSocket stub
- `driver-app/e2e/smoke.spec.ts` — boot + auth-routing
- `driver-app/e2e/ride-offer.spec.ts` — ride-state rendering, earnings poll, active-ride poll
- `driver-app/e2e/online-toggle.spec.ts` — config fetch, unhandled rejections, suspended-driver state
- `driver-app/package.json` — added `@playwright/test`, `test:e2e` script, `web`/`build:web` scripts

### Backend full-lifecycle E2E
- `backend/tests/test_e2e_ride_lifecycle.py` — happy-path lifecycle (accept → arrive → start → complete), concurrency (double-accept), HTTP smoke (all ride routes mounted)

### Cross-app integration
- `tests/test_cross_app_ride_lifecycle.py` — rider-visible status after driver acts, WebSocket channel contract, status-enum parity between rider and driver views, cancel-during-accepted driver notification, full rider/driver endpoint mount smoke

---

## 2. User stories & scenario coverage matrix

Legend: `X` covered, `~` partial, `—` not covered.

### 2.1 Rider user stories

| # | User story | Unit | Integration | E2E | Gap |
|---|---|---|---|---|---|
| R1 | As a rider, I can sign up / log in with phone OTP | X | ~ | X | native E2E missing |
| R2 | As a rider, I can see fare estimates before booking | X | X | X | surge-applied estimate not E2E'd |
| R3 | As a rider, I can request a ride with pickup + dropoff | X | X | X | scheduled-ride path not E2E'd |
| R4 | As a rider, I see the driver's ETA and car moving on a map | X | ~ | ~ | live location interpolation not tested |
| R5 | As a rider, I get OTP to hand to the driver at pickup | X | X | — | OTP entry flow not in E2E |
| R6 | As a rider, I can cancel before / after match (with correct fee) | X | X | — | fee-calc E2E missing |
| R7 | As a rider, I can chat with the driver mid-trip | X | ~ | — | chat E2E missing |
| R8 | As a rider, I can pay with card / wallet / cash | X | X | — | wallet-top-up E2E missing |
| R9 | As a rider, I can apply a promo code | X | X | — | promo E2E missing |
| R10 | As a rider, I can rate and tip the driver post-trip | X | X | X | double-submit guard not asserted |
| R11 | As a rider, I can add stops mid-trip | X | X | — | multi-stop E2E missing |
| R12 | As a rider, I can share my ride with a contact | X | X | — | share-link E2E missing |
| R13 | As a rider, I can trigger an SOS / safety call | X | ~ | — | SOS E2E missing |
| R14 | As a rider, my ride survives app restart | ✅ | ✅ | — | `rideStore.restart.test.ts` — 8 scenarios |
| R15 | As a rider on a corporate account, charges split correctly | X | X | — | corporate E2E only backend |
| R16 | As a rider, I earn loyalty points | X | X | — | loyalty E2E missing |

### 2.2 Driver user stories

| # | User story | Unit | Integration | E2E | Gap |
|---|---|---|---|---|---|
| D1 | As a driver, I complete onboarding (profile → vehicle → docs → verify) | X | X | ~ | E2E now covers "verified" path only |
| D2 | As a driver, I can go online and receive offers | X | X | ~ | WS event push not asserted in E2E |
| D3 | As a driver, I can accept / decline a ride within countdown | X | X | ~ | countdown-timeout E2E missing |
| D4 | As a driver, I navigate to pickup using in-app map | X | ~ | — | navigation handoff E2E missing |
| D5 | As a driver, I verify the rider's OTP before starting | X | X | — | OTP verify E2E missing |
| D6 | As a driver, I start and complete the trip | X | X | X | covered by new ride-offer spec |
| D7 | As a driver, my earnings update after each trip | X | X | X | covered by new earnings poll test |
| D8 | As a driver, I can cash out / schedule payouts | X | X | — | payout E2E missing |
| D9 | As a driver, I see a banner when docs expire / onboarding blocks | X | X | ~ | only suspended state E2E'd |
| D10 | As a driver, I complete quests for bonuses | X | X | — | quest E2E missing |
| D11 | As a driver, I can chat with the rider | X | ~ | — | chat E2E missing |
| D12 | As a driver, my active trip survives app restart | ✅ | ✅ | — | `driverStore.restart.test.ts` — 8 scenarios |
| D13 | As a driver, I get my T4A at year-end | X | X | — | T4A E2E skipped (low frequency) |
| D14 | As a driver, I can rate the rider | X | X | — | rider-rating E2E missing |
| D15 | As a driver, I can go offline and WS closes cleanly | X | ~ | — | socket-close E2E missing |

### 2.3 Cross-app / concurrency scenarios

| # | Scenario | Covered? | Where |
|---|---|---|---|
| C1 | Driver accepts → rider UI flips within <1s via WS (not 15s poll) | X | `test_cross_app_ride_lifecycle::test_driver_accept_updates_status_seen_by_rider` |
| C2 | Two drivers racing same offer → exactly one wins | X | `test_e2e_ride_lifecycle::test_two_drivers_accepting_same_ride_one_wins` |
| C3 | Rider cancels post-accept → driver notified via WS | X | `test_cross_app_ride_lifecycle::test_cancel_by_rider_during_driver_accepted_frees_driver` |
| C4 | Driver cancels post-accept → rider notified | — | **Gap: add to cross-app** |
| C5 | Status enums match between rider + driver views | X | `test_status_enums_match_between_rider_and_driver_views` |
| C6 | Rider + driver endpoint surface both mounted | X | `TestCrossAppHTTPContract` |
| C7 | Surge multiplier applied consistently across estimate + fare + payout | — | **Gap: add test** |
| C8 | WebSocket reconnect after network drop preserves ride state | ✅ | `useRiderSocket.ts:onopen` + `useDriverDashboard.ts:onmessage` + `test_p1_ws_reconnect.py` |
| C9 | Backend clock drift: server timestamps authoritative over client | — | **Gap: add test** |
| C10 | Rider offline during pickup — status transitions deferred, not dropped | — | **Gap: add test** |

### 2.4 Edge cases & failure modes

| # | Scenario | Covered? | Priority |
|---|---|---|---|
| E1 | No drivers nearby → rider sees "no drivers available" after timeout | — | P1 |
| E2 | All nearby drivers decline → expand radius / fail | — | P1 |
| E3 | Stripe charge fails at completion → retry + fallback | ~ backend only | P1 |
| E4 | Payment 3DS challenge interrupts completion | — | P2 |
| E5 | Driver goes offline mid-trip | — | P1 |
| E6 | Driver's phone loses GPS mid-trip | — | P2 |
| E7 | Rider updates dropoff after trip started | — | P2 |
| E8 | Duplicate ride request (double-tap confirm) | — | P1 |
| E9 | Rider creates ride while another is active | X backend | — |
| E10 | Driver accepts a ride that was just cancelled (race) | X backend | — |
| E11 | Expired auth token mid-trip → refresh without losing state | ~ | P1 |
| E12 | WebSocket auth message rejected | X | — |
| E13 | Backend rolling deploy during active trip (WS drop) | — | P2 |
| E14 | Timezone / DST boundary in scheduled ride | — | P2 |
| E15 | Abusive rider: pickup outside service area | — | P2 |
| E16 | Surge boundary: multiplier changes between estimate and create | — | P1 |

### 2.5 Security / abuse scenarios

| # | Scenario | Covered? | Priority |
|---|---|---|---|
| S1 | Rider tries to accept own ride (role escalation) | ✅ | P1 — fixed: `accept_ride` guard + pinned |
| S2 | Driver tries to complete a ride not assigned to them | X | — |
| S3 | JWT with tampered role claim → 401 | ✅ | P1 — role from DB, pinned in `test_p1_security.py` |
| S4 | Rate-limit on OTP send | X | — |
| S5 | SQL/NoSQL injection in ride.notes or address | ~ | P1 |
| S6 | PII (phone, card tail) leaks in logs or API responses | X | — |
| S7 | Rider views another rider's ride by guessing ride_id | X | — |
| S8 | Driver views another driver's earnings | ✅ | P1 — scoped by `current_user.id`, pinned in `test_p1_security.py` |
| S9 | CORS / CSRF on web-export endpoints | — | P1 |

---

## 3. Coverage summary by component

| Component | Unit | Integration | E2E (this branch) | Overall |
|---|---|---|---|---|
| Backend routes | ~40% (Supabase baseline ~6%) | High | Happy-path + concurrency | Good |
| Backend WS | High (auth + ack) | Medium | Channel contract | Good |
| Rider-app store | Moderate | — | Smoke + lifecycle | Adequate |
| Rider-app UI | Low | — | 3 specs | Weak |
| Driver-app store | Moderate | — | New smoke + lifecycle | Adequate |
| Driver-app UI | Low | — | 3 new specs | Weak |
| Shared types | High | — | — | Good |
| Cross-app contracts | — | New | Endpoint mount + status parity | **New baseline** |
| Native (iOS/Android) | Unit only | — | — | **No E2E at all** |

---

## 4. Prioritized gap closure plan

### P0 — Ship-blockers

**Status (2026-04-20):** All 5 investigated; tests added in
`backend/tests/test_p0_ship_blockers.py`.
Implementation work remaining: P0-4 surge-lock, P0-5 Stripe card charge.

| # | Item | Impl | Test | Notes |
|---|---|---|---|---|
| P0-1 | Driver-cancel-post-accept notifies rider | ✅ exists (`drivers.py:2115`) | ✅ pinned | Handler-level WS + push both asserted |
| P0-2 | No-drivers-available 5-min timeout | ✅ exists (`rides.py::ride_search_timeout`) | ✅ pinned | Function extracted to module scope so it's directly testable |
| P0-3 | Duplicate ride request guard | ✅ exists (active-ride + `Idempotency-Key`) | ✅ pinned | Handler + route-level both covered |
| P0-4 | Surge boundary (estimate → create) | ✅ **closed** — signed `estimate_token` locks surge | ✅ full coverage | Token: HMAC-SHA256, 5-min TTL, bound to rider + route + vehicle_type; `backend/utils/estimate_token.py` |
| P0-5 | Payment failure at complete | ⚠️ **partial** — card path is a stub (`rides.py:1088`) | ✅ wallet pinned + xfail documents card gap | Needs Stripe `PaymentIntent.confirm` + decline handling |

### P1 — Critical before scale
6. ✅ **WebSocket reconnect with state preservation** — C8 — closed: `useRiderSocket.ts` calls `fetchRide` in `onopen`; `useDriverDashboard.ts` calls `fetchActiveRide` on first auth-confirmed message after reconnect; `backend/tests/test_p1_ws_reconnect.py` pins ConnectionManager round-trip + HTTP recovery; `rider-app/hooks/__tests__/useRiderSocket.reconnect.test.ts` pins client-side reconnect behavior
7. ✅ **Mid-trip restart restore** (rider and driver) — R14, D12 — closed: `rider-app/store/__tests__/rideStore.restart.test.ts` pins `hydrateActiveRide()` (8 scenarios: active, terminal, stale, offline, no-override); `driver-app/store/__tests__/driverStore.restart.test.ts` pins `hydrateDriverRideState()` (8 scenarios: navigating, arrived, in-progress, terminal states, corrupt JSON, no-override)
8. ✅ **Role-claim tampering guard** — S3, S8 — closed: S1 gap fixed (`accept_ride` in `drivers.py` now rejects `ride.rider_id == current_user.id` → 403); S3 pinned — `get_current_user` always uses DB role, never JWT claim; S8 pinned — `get_driver_earnings` uses `current_user.id`, no caller-supplied driver_id; `backend/tests/test_p1_security.py`
9. **Multi-stop E2E** — R11
10. **Driver offline mid-trip** — E5
11. **Token refresh mid-trip** — E11
12. **CORS on web exports** — S9

### P2 — Completeness
13. Chat E2E (rider + driver) — R7, D11
14. SOS E2E — R13
15. Promo / wallet / loyalty E2E — R8, R9, R16
16. Payout / T4A driver flows — D8, D13
17. Scheduled rides + DST — R3, E14

### P3 — Native (requires Detox or Maestro)
18. iOS + Android E2E for each app — currently all E2E is Expo-Web only
19. Native push-notification flows (FCM/APNs)
20. Background location for drivers (iOS policy changes)

---

## 5. Test execution runbook

### Fast (every commit)
```bash
# Backend unit tests
cd backend && pytest -m "not e2e and not slow"

# Rider-app unit
cd rider-app && yarn test

# Driver-app unit
cd driver-app && yarn test
```

### E2E (pre-merge / nightly)
```bash
# Backend lifecycle + cross-app
pytest -m e2e backend/tests/test_e2e_ride_lifecycle.py tests/test_cross_app_ride_lifecycle.py

# Rider-app web E2E
cd rider-app && yarn build:web && PLAYWRIGHT_START_SERVER=1 yarn test:e2e

# Driver-app web E2E
cd driver-app && yarn build:web && PLAYWRIGHT_START_SERVER=1 yarn test:e2e
```

### Full (release)
Run all of the above, plus:
- Admin-dashboard Playwright suite
- Backend `pytest` without `-m` filter (includes slow + payments integration)
- Manual smoke on iOS + Android (checklist in `docs/MOBILE_SMOKE.md` — **does not yet exist; P3 deliverable**)

---

## 6. Known limitations of the new E2E tests

1. **Web-only** — Playwright hits the Expo web export. Native-specific code paths (SecureStore encryption, native maps, native Firebase, Stripe Payment Sheet) are mocked out. A native failure will not be caught here.
2. **Backend mocked at HTTP layer** — no real Supabase, Redis, or Stripe. Integration with those services is only validated in backend tests that mock at the library layer, not end-to-end.
3. **WebSocket is stubbed** — the new driver-app fixture replaces `window.WebSocket` with a mock. This validates client-side subscription + event-handling logic, not actual WS framing or auth.
4. **Offers test asserts rendering, not interaction** — clicking accept/decline on real Expo Web with `react-native-maps` stubbed is brittle. Deeper assertions need dedicated testIDs added to the driver-app components (`ActiveRidePanel`, `DriverIdlePanel`, etc.). **Follow-up: add `testID` props, already done in `driver-app/app/login.tsx`.**
5. **No perf / load testing** — concurrency tests use `asyncio.gather` at N=2. Real-world scale (1 rider, 100 drivers bidding) is not exercised. Use `backend/tests/perf_baseline.py` as a starting point.

---

## 7. What "working end-to-end" means for this release

A release is considered E2E-ready when all of the following pass:

- [ ] All three component unit suites green
- [ ] `pytest -m e2e` passes (lifecycle + cross-app)
- [ ] Rider-app Playwright green on a fresh web export
- [ ] Driver-app Playwright green on a fresh web export
- [ ] Manual 2-device smoke: rider requests → driver (different device) accepts → completes → both apps show correct final state
- [ ] Manual rider-cancel and driver-cancel paths each tested once
- [ ] Payment completes end-to-end against Stripe test mode
- [ ] At least one P0 gap from §4 closed; others tracked in issues
