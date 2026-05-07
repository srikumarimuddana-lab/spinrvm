# Spinr TODO List

> **Last audited**: 2026-05-07 — entries verified against current codebase.
> Items marked **DONE** are implemented; the audit cross-referenced
> `backend/routes/`, `backend/tests/test_p*_*.py`, and the rider/driver app source.

## Open Items (Genuine Feature Gaps)

### Low Priority
- [ ] **Driver App: Earnings export (CSV/PDF)** — Tax-prep convenience feature
  - File: `driver-app/app/driver/earnings.tsx`
  - Action: Add export buttons (CSV first, PDF later)

- [ ] **Driver App: Dark mode** — Light theme only
  - Files: `driver-app/`, `rider-app/`
  - Action: Implement theme system (Expo `useColorScheme` + design tokens)

- [ ] **API: No `/v1/` versioning prefix** — Future-proofing
  - File: `backend/server.py`
  - Action: Mount routers under `/v1/` prefix; add deprecation strategy doc

---

## Completed (Pinned by Tests)

The following items from earlier TODOs are now **fully implemented and test-pinned**:

### Backend Infrastructure
- [x] **WebSocket reconnection with state preservation** (P1-6) → `backend/tests/test_p1_ws_reconnect.py`
- [x] **JWT secret enforced ≥ 32 chars at startup** → `backend/core/middleware.py`
- [x] **server.py modularized** (171 lines, all routes split into `backend/routes/`)
- [x] **CORS hardened to specific origins** → `backend/tests/test_p1_cors.py` (8 scenarios)
- [x] **Rate limits on rides, cancel, promo endpoints** → P3 hardening
- [x] **Duplicate-ride guard (409 on race)** (P0-3) → `backend/tests/test_e8_duplicate_ride.py`
- [x] **Surge bait-and-switch guard (signed estimate_token)** (P0-4) → `backend/tests/test_e16_surge_boundary.py`

### Driver App
- [x] **Push notifications (FCM/APNs)** (P3-19) → `backend/tests/test_p3_push_notifications.py`
- [x] **WebSocket reconnect** → `useDriverDashboard.ts` + `driverStore.reconnect.test.ts`
- [x] **Location batched via `/drivers/location-batch`** (P3-20) → `backend/tests/test_p3_background_location.py`
- [x] **Background location permission flow** → `goOnlinePermission.test.ts` + Maestro `08_background_location.yaml`
- [x] **15s offer timeout configurable** → reads `configuredCountdownSeconds` from backend settings
- [x] **Tip collection UI** → `driver-app/components/dashboard/TripCompletedPanel.tsx:270`
- [x] **Race-condition handling (double-accept)** → 409 guard in `drivers.py` + `test_e2e_ride_lifecycle.py`
- [x] **Geofence arrival verification** → 100m `ARRIVAL_RADIUS_KM` in `backend/routes/drivers.py:1836` + driver-side check in `index.tsx:607`
- [x] **External navigation override (Google/Waze)** → `driver-app/app/driver/settings.tsx:79` exposes `navApp` selector

### Rider App
- [x] **Multi-stop mid-trip** (P1-9) → `backend/tests/test_p1_multi_stop.py` (11 cases, fare recalc included)
- [x] **Scheduled rides + DST** (P2-17) → `backend/tests/test_p2_scheduled_rides.py`
- [x] **Promo / wallet / loyalty** (P2-15) → `backend/tests/test_p2_promo_wallet_loyalty.py` (22 cases)
- [x] **Chat E2E** (P2-13) → `backend/tests/test_p2_chat.py`
- [x] **SOS button** (P2-14) → `backend/tests/test_p2_sos.py`
- [x] **Mid-trip restart restore** (P1-7) → `rideStore.restart.test.ts`

### Admin Dashboard
- [x] **Authentication + session refresh** → `admin-dashboard/src/lib/api.ts` + `authStore.ts` + `test_p1_security.py`

### Token / Auth
- [x] **Token refresh mid-trip** (P1-11) → `backend/tests/test_p1_token_refresh.py`
- [x] **Role-claim tampering guard** (P1-8) → `backend/tests/test_p1_security.py`

---

## Intentional Design Decisions (Not Bugs)

- **4-digit OTP** — Kept for UX simplicity; mitigated by rate limiting + 5-min expiry. See `dependencies/__init__.py:38–41`.

---

## Gated / In-Flight

- [ ] **Stripe card payment at ride completion** (P0-5) — Implementation on branch `claude/p0-5-stripe-card-charge`. Phases A–D complete; **Phase E (manual Stripe-staging validation) pending**.
  - Runbook: `docs/scoping/P0-5_PHASE_E_RUNBOOK.md`
  - Merge guide: `docs/scoping/P0-5_MERGE_RESOLUTION_GUIDE.md`
  - Single conflict in `backend/routes/rides.py` (< 10 min to resolve once Phase E clears).

- [ ] **Real-device background-location continuity test** (P3-20) — Maestro flow scaffolded at `.maestro/driver/08_background_location.yaml`. Awaits CI simulator infrastructure or manual run per `docs/runbooks/MOBILE_SMOKE.md §6`.

---

## Notes

- All P0/P1/P2/P3 E2E gap-analysis items (`docs/E2E_TEST_GAP_ANALYSIS.md`) are **CLOSED** as of this audit.
- Open feature work above is enhancement-level, not ship-blocker.
- See `backend/tests/conftest.py::_STALE_TEST_CLASSES` (currently empty — kept for future test-suite repairs).
